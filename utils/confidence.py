"""
Data Confidence Layer
─────────────────────
A single, deterministic 0-100 confidence score for the ledger's *data*
(not the user's behavior). Every downstream number — Money Pulse,
Recommendations, Insights — can read this to decide whether to present
precise values, ranges, or "Insufficient data".

Inputs (all read from the live DB, no caching):
  • months_active      — distinct YYYY-MM with ≥1 real (non-transfer) tx
  • coverage_gaps      — missing months between first and last imported
  • total_rows         — all transactions
  • flagged_share      — flagged / total
  • low_parse_share    — parse_confidence='low' / total
  • uncategorized_share — (NULL | '' | 'Misc') / total (debits only)
  • transfer_hygiene   — rows with direction∈{transfer,payment,cancelled}
                         but is_transfer=0 — stale-rerun detector

Safe contract:
  • Never raises.
  • Returns {score, level, reasons:[...], inputs:{...}} — reasons carry
    enough context for UI copy and future AI-grounded explanations.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from utils.database import get_connection


LEVEL_THRESHOLDS = [
    (85, "high"),
    (65, "medium"),
    (40, "low"),
    (0,  "insufficient"),
]


def _level(score: float) -> str:
    for threshold, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "insufficient"


def compute_data_confidence(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return {score, level, reasons, inputs}. Never raises."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        return _compute(conn)
    except Exception as e:  # defensive — confidence should never break the UI
        return {
            "score":   0,
            "level":   "insufficient",
            "reasons": [f"confidence check failed: {type(e).__name__}"],
            "inputs":  {},
        }
    finally:
        if close:
            conn.close()


def _compute(conn: sqlite3.Connection) -> dict:
    # Row totals
    totals = conn.execute("""
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN is_flagged=1 THEN 1 ELSE 0 END) AS flagged,
            SUM(CASE WHEN parse_confidence='low' THEN 1 ELSE 0 END) AS low_parse,
            SUM(CASE
                WHEN direction='debit'
                 AND (category IS NULL OR category='' OR category='Misc')
                THEN 1 ELSE 0 END) AS uncategorized_debits,
            SUM(CASE WHEN direction='debit' THEN 1 ELSE 0 END) AS debits_total,
            SUM(CASE
                WHEN direction IN ('transfer','payment','cancelled')
                 AND (is_transfer IS NULL OR is_transfer=0)
                THEN 1 ELSE 0 END) AS stale_transfer_flags
        FROM transactions
    """).fetchone()

    total_rows           = totals["total_rows"] or 0
    flagged              = totals["flagged"] or 0
    low_parse            = totals["low_parse"] or 0
    uncat_debits         = totals["uncategorized_debits"] or 0
    debits_total         = totals["debits_total"] or 0
    stale_transfer_flags = totals["stale_transfer_flags"] or 0

    # Coverage: distinct real-tx months
    month_rows = conn.execute("""
        SELECT DISTINCT strftime('%Y-%m', transaction_date) AS m
        FROM transactions
        WHERE direction NOT IN ('payment','cancelled')
          AND (is_transfer IS NULL OR is_transfer = 0)
        ORDER BY m
    """).fetchall()
    months = [r[0] for r in month_rows if r[0]]
    months_active = len(months)

    # Coverage gaps between first and last month
    coverage_gaps = 0
    if months_active >= 2:
        y0, m0 = int(months[0][:4]), int(months[0][5:7])
        y1, m1 = int(months[-1][:4]), int(months[-1][5:7])
        expected = 0
        y, m = y0, m0
        while (y, m) <= (y1, m1):
            expected += 1
            m += 1
            if m > 12:
                m = 1
                y += 1
        coverage_gaps = max(0, expected - months_active)

    # ── Scoring ────────────────────────────────────────────────────
    score = 100.0
    reasons: list[str] = []

    # 1. Months active — heaviest driver
    if months_active == 0:
        return {
            "score":   0,
            "level":   "insufficient",
            "reasons": ["No real transactions imported yet."],
            "inputs":  {
                "months_active": 0, "total_rows": total_rows,
            },
        }
    if months_active == 1:
        score -= 45
        reasons.append("Only 1 month imported — trends and stability need ≥2 months.")
    elif months_active == 2:
        score -= 25
        reasons.append("2 months imported — trends are directional, not statistical.")
    elif months_active < 6:
        score -= 10
        reasons.append(f"{months_active} months imported — add more for stronger signals.")

    # 2. Coverage gaps
    if coverage_gaps > 0:
        penalty = min(20, coverage_gaps * 7)
        score -= penalty
        reasons.append(f"{coverage_gaps} gap month(s) in timeline — import missing PDFs.")

    # 3. Flagged share
    if total_rows > 0 and flagged > 0:
        share = flagged / total_rows
        if share >= 0.05:
            penalty = min(15, share * 100 * 0.5)
            score -= penalty
            reasons.append(
                f"{flagged} flagged row(s) ({share*100:.0f}% of data) — review reduces noise."
            )

    # 4. Low parse confidence share
    if total_rows > 0 and low_parse > 0:
        share = low_parse / total_rows
        if share >= 0.03:
            penalty = min(10, share * 100 * 0.4)
            score -= penalty
            reasons.append(
                f"{low_parse} row(s) with low parse confidence ({share*100:.0f}%) — verify categories."
            )

    # 5. Uncategorized debit share (Misc / NULL / empty)
    if debits_total > 0 and uncat_debits > 0:
        share = uncat_debits / debits_total
        if share >= 0.05:
            penalty = min(10, share * 100 * 0.4)
            score -= penalty
            reasons.append(
                f"{uncat_debits} uncategorized debit(s) ({share*100:.0f}%) — use Review → Suggest."
            )

    # 6. Transfer hygiene — stale is_transfer=0 on transfer/payment/cancelled rows
    if stale_transfer_flags > 0:
        score -= min(10, stale_transfer_flags * 1.5)
        reasons.append(
            f"{stale_transfer_flags} row(s) with stale transfer flag — run Settings → Rules → Re-run Categorization."
        )

    score = max(0.0, min(100.0, score))
    return {
        "score":   round(score),
        "level":   _level(score),
        "reasons": reasons,
        "inputs":  {
            "months_active":          months_active,
            "coverage_gaps":          coverage_gaps,
            "total_rows":             total_rows,
            "flagged":                flagged,
            "low_parse":              low_parse,
            "uncategorized_debits":   uncat_debits,
            "debits_total":           debits_total,
            "stale_transfer_flags":   stale_transfer_flags,
        },
    }


def data_sufficient_for(feature: str, confidence: dict) -> tuple[bool, str]:
    """
    Gate feature output on data confidence.
    Returns (ok, human_reason). `reason` is empty when ok.

    feature: 'score_total' | 'savings_momentum' | 'income_stability' |
             'spending_control' | 'yoy' | 'drift'
    """
    m = (confidence.get("inputs") or {}).get("months_active", 0)
    rules = {
        "score_total":      (1,  "Need at least 1 month of data for a score."),
        "savings_momentum": (3,  "Needs 3+ months to compute momentum."),
        "income_stability": (2,  "Needs 2+ months for a stability measure."),
        "spending_control": (3,  "Needs 3+ months for a control measure."),
        "yoy":              (12, "Needs 2 calendar years for year-over-year."),
        "drift":            (4,  "Needs ~4 months to measure drift."),
    }
    need, msg = rules.get(feature, (0, ""))
    return (m >= need, "" if m >= need else msg)

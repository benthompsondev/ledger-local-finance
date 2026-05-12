"""
Momentum, streaks, and This Month's Mission — deterministic.

Design contract
───────────────
• Purely deterministic. No AI calls here. AI may wrap the mission line
  (see utils.ai_explainer.mission_framing), but the mission itself is
  computed from rules against imported data.
• Mature tone — no badges, no cheerleading. Streaks and missions exist to
  answer: "what am I trying to improve right now, and how do I know I'm
  improving?"
• Returns stable shapes so the UI can render without None-checks.

Public API
──────────
    compute_streaks(conn)  -> dict
    choose_mission(conn)   -> dict
    mission_progress(mission, conn) -> dict  (bundled into choose_mission)
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

from utils.database import get_connection


# ── Streaks ───────────────────────────────────────────────────────────────

def _months_span(first_month: str, last_month: str) -> list[str]:
    """Inclusive YYYY-MM month list between first and last."""
    y0, m0 = int(first_month[:4]), int(first_month[5:7])
    y1, m1 = int(last_month[:4]),  int(last_month[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def compute_streaks(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return deterministic streaks + trend signals for the Momentum card."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        return _compute_streaks_impl(conn)
    finally:
        if close:
            conn.close()


def _compute_streaks_impl(conn: sqlite3.Connection) -> dict:
    today = date.today()

    # 1. Days since last cash advance
    row = conn.execute("""
        SELECT MAX(transaction_date) AS d FROM transactions
        WHERE category='Cash Advance' AND direction='debit'
    """).fetchone()
    last_ca = row["d"] if row else None
    if last_ca:
        try:
            dt = date.fromisoformat(last_ca)
            days_since_ca = (today - dt).days
        except Exception:
            days_since_ca = None
    else:
        days_since_ca = None  # None = "never in the data"

    # 2. Consecutive most-recent months with net ≥ 0
    from utils.insights import monthly_aggregates
    aggs = monthly_aggregates(conn=conn)
    positive_streak = 0
    for a in reversed(aggs):
        if a["net"] >= 0:
            positive_streak += 1
        else:
            break

    # 3. Review queue trend — current flagged count (trend requires snapshots; skipped)
    flagged = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_flagged=1").fetchone()[0]

    # 4. Subscription monthly $ over last 90 days
    sub_row = conn.execute("""
        SELECT SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Subscriptions & Digital' AND direction='debit'
          AND transaction_date >= date('now', '-90 days')
    """).fetchone()
    sub_monthly = round((sub_row["total"] or 0) / 3.0, 2) if sub_row else 0.0

    # 5. Current-month controllable spend so far (for challenge progress)
    ym = today.strftime("%Y-%m")
    ctrl_row = conn.execute("""
        SELECT SUM(ABS(amount)) AS total
        FROM transactions
        WHERE strftime('%Y-%m', transaction_date) = ?
          AND direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In','Payment',
                               'Credit Card Payment','Cancelled','Housing / Mortgage',
                               'Fees / Interest','Cash Advance')
    """, (ym,)).fetchone()
    controllable_mtd = round((ctrl_row["total"] or 0), 2) if ctrl_row else 0.0

    # 6. Latest savings rate
    sr = aggs[-1]["savings_rate"] if aggs else 0.0
    latest_month = aggs[-1]["month"] if aggs else None
    latest_net   = aggs[-1]["net"]   if aggs else 0.0

    return {
        "days_since_cash_advance": days_since_ca,
        "positive_net_streak":     positive_streak,
        "flagged_count":           int(flagged or 0),
        "subscription_monthly":    sub_monthly,
        "controllable_mtd":        controllable_mtd,
        "latest_savings_rate":     sr,
        "latest_month":            latest_month,
        "latest_net":              latest_net,
        "current_month":           ym,
    }


# ── Mission rules ─────────────────────────────────────────────────────────

def choose_mission(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Picks THIS month's mission from rules, ordered by urgency.

    Each mission returns:
        {id, title, description, metric_label, target, current,
         progress_pct, win_condition, streaks}

    Progress numbers are deterministic and reflect only imported data.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        streaks = _compute_streaks_impl(conn)
        mission = _choose_mission_impl(conn, streaks)
        mission["streaks"] = streaks
        return mission
    finally:
        if close:
            conn.close()


def _choose_mission_impl(conn: sqlite3.Connection, s: dict) -> dict:
    """Priority-ordered mission selection."""
    # 1. Cash advance outstanding in last 30 days — highest priority,
    # BUT only when there is no plausible payment coverage from later
    # credit-card payments. Pass 35 Phase 3 fix: a cash advance whose
    # principal looks covered by later payments shouldn't be presented
    # as urgent "clear it" — Ledger sees transactions, not balances.
    row = conn.execute("""
        SELECT COUNT(*) AS cnt, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Cash Advance' AND direction='debit'
          AND transaction_date >= date('now','-30 days')
    """).fetchone()
    ca_cnt   = row["cnt"] or 0
    ca_total = round(row["total"] or 0, 2)
    if ca_cnt > 0:
        try:
            from utils.insights import cash_advance_status as _ca_status_fn
            _ca_status = _ca_status_fn(conn=conn) or {}
        except Exception:
            _ca_status = {}
        _verdict = _ca_status.get("verdict") or "outstanding"
        if _verdict in ("covered", "uncertain"):
            _title = "Verify the cash advance"
            _desc = (_ca_status.get("safe_action")
                     or (f"{ca_cnt} cash advance(s) totalling "
                         f"${ca_total:,.0f}. Verify on your card "
                         "statement; later payments may already cover it."))
            _win = ("Confirm the cash advance is settled and avoid new "
                    "ones in the next 30 days.")
        else:
            _title = "Clear the cash advance"
            _desc = (f"{ca_cnt} cash advance(s) totalling "
                     f"${ca_total:,.0f} in the last 30 days. "
                     "No credit-card payments seen after the advance. "
                     "Cash advances typically carry 22-30% APR.")
            _win = "No cash advance transactions in the next 30 days."
        return {
            "id":           "pay_cash_advance",
            "title":        _title,
            "description":  _desc,
            "metric_label": "Cash advance transactions",
            "target":       0.0,
            "current":      ca_total,
            "progress_pct": 0 if ca_total > 0 else 100,
            "win_condition": _win,
        }

    # 2. Review queue ≥ 10 flagged items
    if s["flagged_count"] >= 10:
        return {
            "id":           "clear_review",
            "title":        "Clear the review queue",
            "description":  (f"{s['flagged_count']} flagged rows. Every cleared item improves "
                             f"score accuracy and data confidence — most are bookkeeping, a few matter."),
            "metric_label": "Items remaining",
            "target":       0,
            "current":      s["flagged_count"],
            "progress_pct": 0,
            "win_condition": "Queue under 3 items by month end.",
        }

    # 3. Savings rate below 10%
    if s["latest_savings_rate"] is not None and 0 < s["latest_savings_rate"] < 10:
        return {
            "id":           "lift_savings",
            "title":        "Lift savings rate above 10%",
            "description":  (f"Latest month's savings rate is {s['latest_savings_rate']:.0f}%. "
                             f"Even 10% compounds faster than you'd guess — start with the biggest controllable category."),
            "metric_label": "Savings rate (latest month)",
            "target":       10.0,
            "current":      s["latest_savings_rate"],
            "progress_pct": int(min(100, (s["latest_savings_rate"] / 10.0) * 100)) if s["latest_savings_rate"] > 0 else 0,
            "win_condition": "Next imported month shows savings rate ≥ 10%.",
        }

    # 4. Subscription bill heavy
    if s["subscription_monthly"] >= 100:
        return {
            "id":           "audit_subscriptions",
            "title":        "Audit subscriptions",
            "description":  (f"~${s['subscription_monthly']:,.0f}/month in subscriptions "
                             f"over the last 90 days. Cancelling any dead weight is recurring savings."),
            "metric_label": "Monthly subscription spend",
            "target":       round(s["subscription_monthly"] * 0.80, 2),
            "current":      s["subscription_monthly"],
            "progress_pct": 0,
            "win_condition": "Cut 20% or more over the next two imported months.",
        }

    # 5. Default: hold the line
    current_sr = s["latest_savings_rate"] or 0
    return {
        "id":           "hold_savings",
        "title":        "Hold the savings rate at 20%+",
        "description":  (f"No urgent fires. Latest savings rate: {current_sr:.0f}%. "
                         f"Goal for this month is to keep it steady while imports catch up."),
        "metric_label": "Savings rate (latest month)",
        "target":       20.0,
        "current":      current_sr,
        "progress_pct": int(min(100, (current_sr / 20.0) * 100)) if current_sr > 0 else 0,
        "win_condition": "Next imported month stays at 20%+.",
    }


# ── Pass 10: Mission options (multi-mission engine) ──────────────────────

def mission_options(conn: Optional[sqlite3.Connection] = None, limit: int = 3) -> list[dict]:
    """
    Return up to `limit` mission candidates for this month, ranked by urgency.

    Each candidate has the same shape as choose_mission() plus:
      - difficulty ('easy'|'moderate'|'hard')
      - expected_impact (str)
      - next_action (str)

    This is how the Dashboard offers 2–3 choices instead of a single hard-coded pick.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        s = _compute_streaks_impl(conn)
        candidates = _mission_candidates(conn, s)
        # Attach shared streaks reference
        for c in candidates:
            c["streaks"] = s
        return candidates[:limit]
    finally:
        if close:
            conn.close()


def _mission_candidates(conn: sqlite3.Connection, s: dict) -> list[dict]:
    """Build every viable mission for the current state, ordered by urgency."""
    out: list[dict] = []

    # 1. Cash advance recovery
    row = conn.execute("""
        SELECT COUNT(*) AS cnt, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Cash Advance' AND direction='debit'
          AND transaction_date >= date('now','-30 days')
    """).fetchone()
    ca_cnt   = row["cnt"] or 0
    ca_total = round(row["total"] or 0, 2)
    if ca_cnt > 0:
        # Pass 35 Phase 3: payment-coverage-aware wording. Same logic as
        # _choose_mission_impl above — Ledger only sees transactions, so
        # never claim "pay it off" when later CC payments may already cover
        # the cash-advance principal.
        try:
            from utils.insights import cash_advance_status as _ca_status_fn
            _ca_status = _ca_status_fn(conn=conn) or {}
        except Exception:
            _ca_status = {}
        _verdict = _ca_status.get("verdict") or "outstanding"
        if _verdict in ("covered", "uncertain"):
            _title = "Verify the cash advance"
            _desc = (_ca_status.get("safe_action")
                     or (f"{ca_cnt} cash advance(s) totalling "
                         f"${ca_total:,.0f}. Verify on your card statement."))
            _diff = "easy"
            _impact = (
                f"~${ca_total * 0.05:,.0f}/yr in residual fee risk if "
                "the advance recurs."
            )
            _next = (
                "Confirm the cash advance is paid off on your card "
                "statement. Avoid using cash advances going forward."
            )
            _urank = 3
            _win = (
                "Confirm the cash advance is settled and avoid new ones "
                "in the next 30 days."
            )
        else:
            _title = "Clear the cash advance"
            _desc = (f"{ca_cnt} cash advance(s) totalling "
                     f"${ca_total:,.0f} in the last 30 days. "
                     "No credit-card payments seen after the advance.")
            _diff = "hard"
            _impact = (
                f"Avoids ~${ca_total * 0.25:,.0f}/yr in interest at "
                "25% APR."
            )
            _next = (
                "Pay off the cash advance this pay cycle. Then stop "
                "using credit card cash advances."
            )
            _urank = 1
            _win = "No cash advance transactions in the next 30 days."
        out.append({
            "id":              "pay_cash_advance",
            "title":           _title,
            "description":     _desc,
            "metric_label":    "Cash advance transactions",
            "target":          0.0,
            "current":         ca_total,
            "progress_pct":    0 if ca_total > 0 else 100,
            "win_condition":   _win,
            "difficulty":      _diff,
            "expected_impact": _impact,
            "next_action":     _next,
            "urgency_rank":    _urank,
        })

    # 2. Review queue
    if s["flagged_count"] >= 5:
        out.append({
            "id":              "clear_review",
            "title":           f"Clear the review queue ({s['flagged_count']} items)",
            "description":     (f"{s['flagged_count']} flagged rows. Every cleared item improves "
                                f"score accuracy and data confidence."),
            "metric_label":    "Items remaining",
            "target":          0,
            "current":         s["flagged_count"],
            "progress_pct":    0,
            "win_condition":   "Queue under 3 items by month end.",
            "difficulty":      "easy",
            "expected_impact": "Accurate monthly totals + stronger AI suggestions on future imports.",
            "next_action":     "Open Review → sort by High-impact → clear cash advance / NSF / large debit items first.",
            "urgency_rank":    2,
        })

    # 3. Savings rate lift
    sr = s["latest_savings_rate"]
    if sr is not None and 0 < sr < 10:
        out.append({
            "id":              "lift_savings",
            "title":           "Lift savings rate above 10%",
            "description":     (f"Latest month's savings rate is {sr:.0f}%. "
                                f"Even 10% compounds faster than you'd guess."),
            "metric_label":    "Savings rate (latest month)",
            "target":          10.0,
            "current":         sr,
            "progress_pct":    int(min(100, (sr / 10.0) * 100)) if sr > 0 else 0,
            "win_condition":   "Next imported month shows savings rate ≥ 10%.",
            "difficulty":      "moderate",
            "expected_impact": "A 5pp savings-rate lift on typical income compounds to thousands/yr.",
            "next_action":     "Open Spending → pick the biggest controllable category → set a 15% reduction.",
            "urgency_rank":    3,
        })

    # 4. Subscription audit
    if s["subscription_monthly"] >= 60:
        target = round(s["subscription_monthly"] * 0.80, 2)
        out.append({
            "id":              "audit_subscriptions",
            "title":           "Trim the subscription bill",
            "description":     (f"~${s['subscription_monthly']:,.0f}/mo in subscriptions over 90 days. "
                                f"Even a 20% cut is recurring savings."),
            "metric_label":    "Monthly subscription spend",
            "target":          target,
            "current":         s["subscription_monthly"],
            "progress_pct":    0,
            "win_condition":   f"Monthly subscription spend at or below ${target:,.0f}.",
            "difficulty":      "easy",
            "expected_impact": f"Cutting 20% = ~${round(s['subscription_monthly']*0.20*12,0):,.0f}/yr saved.",
            "next_action":     "Open Subscription Detective on the Dashboard → review cancellation candidates.",
            "urgency_rank":    4,
        })

    # 5. Controllable spend cap (always available)
    controllable_mtd = s.get("controllable_mtd", 0) or 0
    if controllable_mtd > 0:
        out.append({
            "id":              "cap_controllable",
            "title":           "Cap controllable spend this month",
            "description":     (f"Month-to-date controllable spend is ${controllable_mtd:,.0f}. "
                                f"Hold the line for the rest of the month."),
            "metric_label":    "Controllable spend MTD",
            "target":          round(controllable_mtd * 1.10, 2),  # allow 10% more to close out month
            "current":         controllable_mtd,
            "progress_pct":    100 if controllable_mtd == 0 else int(min(100, (controllable_mtd / max(controllable_mtd*1.10, 1)) * 100)),
            "win_condition":   "Month ends without a large controllable-spend spike.",
            "difficulty":      "moderate",
            "expected_impact": "Prevents end-of-month drift that kills savings rate.",
            "next_action":     "Open Spending → check the top 3 categories and any budget overruns.",
            "urgency_rank":    5,
        })

    # 6. Hold the line (fallback — always include if nothing urgent)
    current_sr = sr or 0
    if not out or current_sr >= 10:
        out.append({
            "id":              "hold_savings",
            "title":           "Hold the savings rate at 20%+",
            "description":     (f"No urgent fires. Latest savings rate: {current_sr:.0f}%. "
                                f"Goal this month: keep it steady."),
            "metric_label":    "Savings rate (latest month)",
            "target":          20.0,
            "current":         current_sr,
            "progress_pct":    int(min(100, (current_sr / 20.0) * 100)) if current_sr > 0 else 0,
            "win_condition":   "Next imported month stays at 20%+.",
            "difficulty":      "easy",
            "expected_impact": "Locks in the savings habit for compounding.",
            "next_action":     "Open Dashboard next week — keep an eye on any new subscriptions or fees.",
            "urgency_rank":    6,
        })

    # Sort by urgency_rank
    out.sort(key=lambda m: m.get("urgency_rank", 99))
    return out


# ── Pass 13: Money Progress / XP / Level (deterministic, mature tone) ────

# XP "buckets" — each is a deterministic measure of one financial habit. Caps
# are deliberate so a single dimension (e.g. a giant savings rate one month)
# can't dominate the level. Nothing here is shamey — losing momentum surfaces
# as "Recovery mode" not punishment, and missed dimensions just contribute 0
# rather than going negative.
_XP_CAPS = {
    "review_hygiene":      30,   # clearing flagged queue
    "no_cash_advance":     30,   # streak days
    "subscription_hold":   20,   # subscription burden vs income
    "savings_rate":        50,   # latest month savings rate ≥ target
    "controllable_cap":    20,   # MTD controllable spend held under cap
    "data_completeness":   20,   # months imported / months covered
    "positive_streak":     30,   # consecutive positive-net months
}
_XP_PER_LEVEL = 100   # XP to reach each next level


def _bucket_review_hygiene(s: dict) -> tuple[float, str]:
    """Reward a small flagged queue. 0 flagged → full XP. >=20 → 0 XP."""
    flagged = int(s.get("flagged_count") or 0)
    if flagged == 0:
        return _XP_CAPS["review_hygiene"], "Review queue clear — every cleared row tightens the score."
    # Linear taper: full at 0, zero at 20.
    pct = max(0.0, min(1.0, 1.0 - flagged / 20.0))
    return _XP_CAPS["review_hygiene"] * pct, f"{flagged} item(s) in review — clear them for full XP."


def _bucket_no_cash_advance(s: dict) -> tuple[float, str]:
    """Reward days since last cash advance. 90+ days → full XP. None → full XP."""
    days = s.get("days_since_cash_advance")
    cap = _XP_CAPS["no_cash_advance"]
    if days is None:
        return cap, "No cash advances on record — keep it that way."
    if days >= 90:
        return cap, f"{days} days since last cash advance — clean streak."
    pct = max(0.0, min(1.0, days / 90.0))
    return cap * pct, f"{days} days since last cash advance — 90+ for full XP."


def _bucket_subscription_hold(s: dict, latest_income: float) -> tuple[float, str]:
    """Reward keeping subscription bill below 5% of monthly income."""
    sub = float(s.get("subscription_monthly") or 0)
    cap = _XP_CAPS["subscription_hold"]
    if latest_income <= 0:
        return 0.0, "Need income data to score subscription discipline."
    ratio = sub / latest_income
    if ratio <= 0.03:
        return cap, f"~${sub:,.0f}/mo subs ≤ 3% of income — tight."
    if ratio >= 0.10:
        return 0.0, f"~${sub:,.0f}/mo subs ≥ 10% of income — Subscriptions / Reduce is the next move."
    # Smooth taper between 3% and 10%.
    pct = (0.10 - ratio) / 0.07
    return cap * pct, f"~${sub:,.0f}/mo subs ≈ {ratio*100:.0f}% of income."


def _bucket_savings_rate(s: dict) -> tuple[float, str]:
    """Latest savings rate. 25%+ → full XP. 0% → 0. Negative → 0 (handled in momentum)."""
    sr = float(s.get("latest_savings_rate") or 0)
    cap = _XP_CAPS["savings_rate"]
    if sr <= 0:
        return 0.0, f"Savings rate {sr:.0f}% — Recovery mode applies; XP returns when next month is positive."
    if sr >= 25:
        return cap, f"Savings rate {sr:.0f}% — full credit."
    pct = max(0.0, min(1.0, sr / 25.0))
    return cap * pct, f"Savings rate {sr:.0f}% — 25%+ for full XP."


def _bucket_controllable_cap(s: dict, latest_spending: float) -> tuple[float, str]:
    """Reward keeping MTD controllable spend below the latest-month total."""
    mtd = float(s.get("controllable_mtd") or 0)
    cap = _XP_CAPS["controllable_cap"]
    if latest_spending <= 0 or mtd <= 0:
        return cap, "No controllable spend tracked yet this month."
    # If MTD is well under last month's spend at this point, full XP.
    ratio = mtd / latest_spending
    if ratio <= 0.5:
        return cap, f"MTD controllable spend ~${mtd:,.0f} — well within last-month pace."
    if ratio >= 1.0:
        return 0.0, f"MTD controllable spend ~${mtd:,.0f} ≥ last full month — pressure on."
    pct = (1.0 - ratio) / 0.5
    return cap * pct, f"MTD controllable spend ~${mtd:,.0f}."


def _bucket_data_completeness(conn: sqlite3.Connection) -> tuple[float, str]:
    """Reward import coverage. Full XP when no gap_months in coverage."""
    from utils.insights import coverage_summary
    cov = coverage_summary(conn=conn)
    cap = _XP_CAPS["data_completeness"]
    total = cov["total_months"] or 0
    gaps = len(cov["gap_months"]) if cov.get("gap_months") else 0
    if total == 0:
        return 0.0, "No data yet — import statements to start earning XP."
    if gaps == 0:
        return cap, f"{total} month(s) imported, no gaps — full credit."
    pct = max(0.0, 1.0 - (gaps / max(total + gaps, 1)))
    return cap * pct, f"{total} month(s) imported with {gaps} gap(s)."


def _bucket_positive_streak(s: dict) -> tuple[float, str]:
    """Reward consecutive positive-net months. 6+ → full XP."""
    streak = int(s.get("positive_net_streak") or 0)
    cap = _XP_CAPS["positive_streak"]
    if streak == 0:
        return 0.0, "No positive-net streak yet — next surplus month starts the count."
    if streak >= 6:
        return cap, f"{streak} consecutive positive-net month(s) — full credit."
    pct = streak / 6.0
    return cap * pct, f"{streak} positive-net month(s) — 6+ for full XP."


def money_progress(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Deterministic XP / Level / Momentum scorecard for the Dashboard.

    Pure math from imported ledger state — no AI, no persistence, recomputes on
    every render. Exists to give the user a clear "play the month better"
    progression without childish badges or shame language.

    Schema:
        level             int   1-based, computed from total XP
        xp                float current XP within the active level (0..xp_to_next)
        xp_total          float lifetime XP across all dimensions (uncapped beyond level math)
        xp_to_next        int   _XP_PER_LEVEL constant
        progress_pct      int   0..100 toward next level
        momentum_label    str   Strong / Steady / Pressure / Recovery
        momentum_score    int   -50..+50 directional indicator
        wins              list  short bullet strings for the panel
        risks             list  short bullet strings for the panel
        breakdown         list  per-bucket {key, label, xp, cap, note}
        explanation       str   one-line summary
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    try:
        s = _compute_streaks_impl(conn)
        from utils.insights import monthly_aggregates
        aggs = monthly_aggregates(conn=conn)
        latest = aggs[-1] if aggs else {"income": 0, "spending": 0, "net": 0,
                                        "savings_rate": 0, "month": "—"}
        prev   = aggs[-2] if len(aggs) >= 2 else None

        breakdown_specs = [
            ("review_hygiene",     "Review hygiene",      _bucket_review_hygiene(s)),
            ("no_cash_advance",    "No cash advance",     _bucket_no_cash_advance(s)),
            ("subscription_hold",  "Subscription hold",   _bucket_subscription_hold(s, float(latest.get("income") or 0))),
            ("savings_rate",       "Savings rate",        _bucket_savings_rate(s)),
            ("controllable_cap",   "Controllable cap",    _bucket_controllable_cap(s, float(latest.get("spending") or 0))),
            ("data_completeness",  "Data completeness",   _bucket_data_completeness(conn)),
            ("positive_streak",    "Positive-net streak", _bucket_positive_streak(s)),
        ]

        breakdown: list[dict] = []
        xp_total = 0.0
        for key, label, (xp, note) in breakdown_specs:
            xp = max(0.0, min(float(xp), _XP_CAPS[key]))
            xp_total += xp
            breakdown.append({
                "key":   key,
                "label": label,
                "xp":    round(xp, 1),
                "cap":   _XP_CAPS[key],
                "note":  note,
            })

        # Level math: 100 XP per level, with a smooth carry-over.
        level = int(xp_total // _XP_PER_LEVEL) + 1
        xp_in_level = xp_total - (level - 1) * _XP_PER_LEVEL
        progress_pct = int(min(100, (xp_in_level / _XP_PER_LEVEL) * 100))

        # Momentum: directional measure based on month-over-month change in
        # savings rate + recent risk signals. Range -50..+50.
        momentum_score = 0
        if prev is not None:
            sr_delta = float(latest.get("savings_rate", 0)) - float(prev.get("savings_rate", 0))
            momentum_score += int(round(sr_delta * 2))   # 5pp swing = ±10
        if (s.get("days_since_cash_advance") or 999) < 30:
            momentum_score -= 15
        if int(s.get("flagged_count") or 0) >= 10:
            momentum_score -= 5
        if int(s.get("positive_net_streak") or 0) >= 3:
            momentum_score += 10
        # Negative-net latest month is "Recovery mode" — pressure but not shame.
        if float(latest.get("net", 0)) < 0:
            momentum_score -= 20
        momentum_score = max(-50, min(50, momentum_score))

        if momentum_score >= 20:
            momentum_label = "Strong"
        elif momentum_score >= 5:
            momentum_label = "Steady"
        elif momentum_score >= -15:
            momentum_label = "Pressure"
        else:
            momentum_label = "Recovery mode"

        # Wins / risks for the side panel — derived from breakdown notes plus
        # streak signals so the user sees what's earning XP and what isn't.
        wins: list[dict] = []
        risks: list[dict] = []
        for b in breakdown:
            if b["xp"] >= b["cap"] * 0.75:
                wins.append({"label": b["label"], "note": b["note"]})
            elif b["xp"] <= b["cap"] * 0.25:
                risks.append({"label": b["label"], "note": b["note"]})

        # Top up wins/risks with explicit streak signals.
        if (s.get("days_since_cash_advance") or 999) < 30:
            risks.append({"label": "Cash advance recent",
                          "note": "Cash advance in the last 30 days — high-interest pressure."})
        elif s.get("days_since_cash_advance") is not None and s["days_since_cash_advance"] >= 180:
            wins.append({"label": "Long clean streak",
                         "note": f"{s['days_since_cash_advance']} days without a cash advance."})

        if momentum_label == "Recovery mode":
            explanation = (
                f"Level {level} · Recovery mode this month. Latest net is negative; "
                f"XP from positive dimensions still counts — momentum returns when "
                f"the next month closes positive."
            )
        else:
            explanation = (
                f"Level {level} · {momentum_label}. {progress_pct}% to next level. "
                f"XP comes from review hygiene, savings rate, "
                f"subscription discipline, and import coverage."
            )

        return {
            "level":          level,
            "xp":             round(xp_in_level, 1),
            "xp_total":       round(xp_total, 1),
            "xp_to_next":     _XP_PER_LEVEL,
            "progress_pct":   progress_pct,
            "momentum_label": momentum_label,
            "momentum_score": int(momentum_score),
            "wins":           wins[:4],
            "risks":          risks[:4],
            "breakdown":      breakdown,
            "explanation":    explanation,
            "latest_month":   latest.get("month"),
        }
    finally:
        if close:
            conn.close()

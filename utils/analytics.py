"""
Analytics layer — cash flow, spending score, category summaries, trends.
All functions accept an optional sqlite3.Connection to avoid repeated opens.

Financial model (v8):
  INCOME  = all credits (direction='credit') with amount > 0, excluding CC payments + cancelled
            Includes: payroll (EFT), INTERAC e-Transfers IN, interest, any other deposits
            Excludes: direction='transfer' (savings pullbacks — internal only),
                      direction IN ('payment','cancelled')
  SPENDING = all debits (direction='debit'), excluding CC payments + cancelled
             Includes: MC purchases, mortgage, INTERAC e-Transfers OUT, fees, cash advances
             Excludes: direction IN ('payment','cancelled','transfer')
             Refund credits (direction='credit', amount<0) reduce spending total
  NEVER excluded: category='Transfer' — INTERAC e-Transfers to/from people are real cashflow
"""
from datetime import date, datetime, timedelta
from typing import Optional
import sqlite3

from utils.database import get_connection, get_transactions, get_monthly_summary, get_category_totals
from config.categories import NON_SPENDING_CATEGORIES, CASHFLOW_GROUPS


# ──────────────────────────────────────────────
# Date helpers
# ──────────────────────────────────────────────

def month_range(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def last_n_months(n: int = 3) -> tuple[str, str]:
    today = date.today()
    end = today.isoformat()
    start = (today - timedelta(days=30 * n)).isoformat()
    return start, end


# ──────────────────────────────────────────────
# Cash flow
# ──────────────────────────────────────────────

def monthly_cashflow(year: int, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """
    Returns list of {month, income, spending, net, tx_count} for each month in year.
    Excludes transfers, payments, and cancelled transactions.
    """
    rows = get_monthly_summary(year, conn=conn)
    result = []
    for r in rows:
        net = r["income"] - r["spending"]
        result.append({
            "month": r["month"],
            "income": round(r["income"], 2),
            "spending": round(r["spending"], 2),
            "net": round(net, 2),
            "tx_count": r["tx_count"],
        })
    return result


# ── Shared cashflow exclusion constants ───────────────────────────
# Only these are ALWAYS excluded (not real cashflow):
#   direction='payment'   → CC payment from chequing / MC "PAYMENT - THANK YOU"
#   direction='cancelled' → reversed/cancelled transactions
#   direction='transfer'  → savings account pullbacks (internal, not from people)
# NOTE: category='Transfer' (INTERAC e-Transfers) is NOT excluded — those are real cashflow
_ALWAYS_EXCLUDE_DIRECTIONS = ("payment", "cancelled", "transfer")
_ALWAYS_EXCLUDE_CATEGORIES = ("Credit Card Payment", "Cancelled")


def compute_cashflow(
    start_date: str,
    end_date: str,
    exclude_transfers: bool = False,
    account_type: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Single shared function used by Dashboard, Spending, and Income pages.

    INCOME  = direction='credit' AND amount > 0
              AND direction NOT IN ('payment','cancelled','transfer')
              AND category NOT IN ('Credit Card Payment','Cancelled')

    SPENDING = direction='debit'
               AND direction NOT IN ('payment','cancelled','transfer')
               AND category NOT IN ('Credit Card Payment','Cancelled')

    REFUND OFFSET = direction='credit' AND amount < 0  (MC refunds)
                    → subtracted from spending (not added to income)

    exclude_transfers (default False):
        When True, additionally exclude category IN ('Transfer','Transfer In','Transfer Out')
        from both income and spending.  Default OFF = INTERAC e-Transfers count as real cashflow.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    extra_cat_filter = ""
    if exclude_transfers:
        extra_cat_filter = " AND category NOT IN ('Transfer', 'Transfer In', 'Transfer Out')"

    acct_filter = ""
    if account_type and account_type != "all":
        acct_filter = " AND account_type = '" + account_type.replace("'", "''") + "'"

    row = conn.execute(f"""
        SELECT
            -- True income: credits with amount > 0 (not CC payments, not savings pullbacks)
            SUM(CASE
                WHEN direction = 'credit'
                 AND amount > 0
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled')
                 {extra_cat_filter}
                THEN amount ELSE 0
            END) AS income,

            -- True spending: positive-amount debits only (guards against malformed rows)
            -- category='Transfer' excluded: only self-transfers remain there (Benjamin->Benjamin)
            SUM(CASE
                WHEN direction = 'debit'
                 AND amount > 0
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled','Transfer')
                 {extra_cat_filter}
                THEN amount ELSE 0
            END) AS spending,

            -- Refund offset: negative-amount credits (MC refunds) reduce spending
            SUM(CASE
                WHEN direction = 'credit'
                 AND amount < 0
                THEN ABS(amount) ELSE 0
            END) AS refund_offset

        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          {acct_filter}
    """, (start_date, end_date)).fetchone()

    income        = row["income"]        or 0.0
    spending_raw  = row["spending"]      or 0.0
    refund_offset = row["refund_offset"] or 0.0
    spending      = max(0.0, spending_raw - refund_offset)

    result = {
        "income":         round(income, 2),
        "spending":       round(spending, 2),
        "spending_gross": round(spending_raw, 2),
        "refund_offset":  round(refund_offset, 2),
        "net":            round(income - spending, 2),
        "savings_rate":   round((income - spending) / income * 100, 1) if income > 0 else 0.0,
    }
    if close:
        conn.close()
    return result


def period_cashflow(start_date: str, end_date: str, conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Backwards-compatible wrapper — calls compute_cashflow() with default settings.
    All callers should migrate to compute_cashflow() directly.
    """
    return compute_cashflow(start_date, end_date, exclude_transfers=False, conn=conn)


# ──────────────────────────────────────────────
# Category breakdown
# ──────────────────────────────────────────────

def spending_by_category(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Returns [{category, total, tx_count, pct}] sorted by total descending.
    Only debits, no transfers.  account_type optional filter.
    """
    rows = get_category_totals(start_date, end_date, account_type=account_type, conn=conn)
    grand_total = sum(r["total"] for r in rows)
    result = []
    for r in rows:
        result.append({
            "category": r["category"],
            "total": round(r["total"], 2),
            "tx_count": r["tx_count"],
            "pct": round(r["total"] / grand_total * 100, 1) if grand_total > 0 else 0.0,
        })
    return result


def top_merchants(
    start_date: str,
    end_date: str,
    limit: int = 15,
    account_type: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    acct_clause = ""
    params: list = [start_date, end_date]
    if account_type and account_type != "all":
        acct_clause = " AND account_type = ?"
        params.append(account_type)
    params.append(limit)

    rows = conn.execute(f"""
        SELECT merchant, category, SUM(ABS(amount)) AS total, COUNT(*) AS visits
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'debit'
          AND is_transfer = 0
          AND category NOT IN ('Transfer', 'Transfer In', 'Transfer Out', 'Payment', 'Credit Card Payment', 'Savings', 'Cancelled')
          {acct_clause}
        GROUP BY merchant
        ORDER BY total DESC
        LIMIT ?
    """, params).fetchall()

    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


# ──────────────────────────────────────────────
# Recurring transactions
# ──────────────────────────────────────────────

def find_recurring(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """
    Identify merchants that appear in 3+ different calendar months.
    Returns [{merchant, category, avg_amount, months_seen, total}]
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    rows = conn.execute("""
        SELECT
            merchant,
            category,
            AVG(ABS(amount))  AS avg_amount,
            COUNT(DISTINCT strftime('%Y-%m', transaction_date)) AS months_seen,
            SUM(ABS(amount))  AS total,
            COUNT(*)          AS tx_count
        FROM transactions
        WHERE direction = 'debit'
          AND is_transfer = 0
          AND direction != 'cancelled'
        GROUP BY merchant
        HAVING months_seen >= 3
        ORDER BY avg_amount DESC
    """).fetchall()

    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


# ──────────────────────────────────────────────
# Monthly control score
# ──────────────────────────────────────────────

def compute_score(conn: Optional[sqlite3.Connection] = None, weights: Optional[dict] = None) -> dict:
    """
    0-100 monthly control score across 4 dimensions.

    Backwards-compatible return shape: keeps `total`, `savings_score`,
    `diversity_score`, `debt_score`, `consistency_score`, `weights`,
    `breakdown`, `savings_rate`.

    Adds (new, optional):
      • `data_confidence` — {score, level, reasons, inputs}
      • `dimensions`      — [{key, label, score, max, reason, sufficient}]
      • `sufficient`      — bool; False when months_active < 2
      • `prelim`          — True when data_confidence.level in {insufficient,low}
    """
    # Load weights from DB if not supplied. Current defaults
    # (40/30/15/15) apply only when the score_weights table is
    # genuinely empty — user-customized rows are preserved.
    if weights is None:
        try:
            from utils.database import get_score_weights
            w = get_score_weights(conn=conn)
            weights = w
        except Exception:
            weights = {"savings_weight": 40, "diversity_weight": 30,
                       "debt_weight": 15, "consistency_weight": 15}

    w_savings     = float(weights.get("savings_weight", 30))
    w_diversity   = float(weights.get("diversity_weight", 20))
    w_debt        = float(weights.get("debt_weight", 25))
    w_consistency = float(weights.get("consistency_weight", 25))

    # Data confidence is read-only; never raises.
    from utils.confidence import compute_data_confidence
    confidence = compute_data_confidence(conn=conn)
    months_active = (confidence.get("inputs") or {}).get("months_active", 0)

    # Prefer the latest complete statement month over a naive rolling 90-day
    # window. Partial current months stay visible in Transactions, but they do
    # not drive the score until the full statement period has been imported.
    from utils.insights import statement_coverage
    _cov = statement_coverage(conn=conn) or {}
    _complete_month = _cov.get("latest_complete_month") or ""
    _by_month = _cov.get("statement_coverage_by_month") or {}

    if _complete_month:
        _y, _m = int(_complete_month[:4]), int(_complete_month[5:7])
        start, end = month_range(_y, _m)
        score_period_label = f"month of {_complete_month}"
        score_window_label = (
            f"the complete statement month {_complete_month}"
        )
        _months_in_window = 1
    else:
        start, end = last_n_months(3)
        score_period_label = "last 90 days"
        score_window_label = "the last 90 days (no complete month available yet)"
        _months_in_window = 3

    cf = period_cashflow(start, end, conn=conn)
    cats = spending_by_category(start, end, conn=conn)
    monthly = monthly_cashflow(date.today().year, conn=conn)

    # ── Savings rate score ────────────────────────
    sr = cf["savings_rate"]
    if months_active >= 1 and cf["income"] > 0:
        savings_score = min(w_savings, max(0.0, (sr / 20) * w_savings))
        savings_reason = (
            f"Savings rate {sr:.0f}% over {score_period_label} "
            "(20% = full credit)."
        )
        savings_sufficient = True
    else:
        savings_score = 0.0
        savings_reason = (
            f"No income recorded in {score_period_label} — savings rate "
            "can't be computed yet."
        )
        savings_sufficient = False

    # ── Spending control ──────────────────────────
    # Measures what the user can actually influence. Fixed obligations
    # such as mortgage, utilities, insurance, transfers, CC payments, and
    # finance charges are excluded. The score blends two signals:
    #   1. concentration: is one controllable category dominating?
    #   2. trend: is controllable spending improving vs the prior complete month?
    _NON_CONTROLLABLE_CATS = {
        "Housing / Mortgage", "Rent", "Mortgage",
        "Utilities / Bills", "Insurance",
        "Transfer", "Transfer In", "Transfer Out",
        "Credit Card Payment", "Payment", "Cancelled",
        "Savings", "Investments", "Income",
        "Refund / Credit", "Rewards / Cashback",
        "Fees / Interest", "Cash Advance",
        "Reimbursement / Insurance Reimbursement",
    }
    _controllable_cats = [
        c for c in (cats or [])
        if c.get("category") and c["category"] not in _NON_CONTROLLABLE_CATS
    ]
    _total_controllable = sum(c["total"] for c in _controllable_cats) or 0.0
    if _controllable_cats and _total_controllable > 0:
        top_ctrl = max(_controllable_cats, key=lambda c: c["total"])
        top_pct = (top_ctrl["total"] / _total_controllable) * 100
        concentration_weight = w_diversity * 0.55
        trend_weight = w_diversity * 0.45
        concentration_score = max(
            0.0,
            concentration_weight
            - (max(0, top_pct - 25) / 35) * concentration_weight,
        )

        _complete_months = list(_cov.get("complete_months") or [])
        _prev_complete = ""
        if _complete_month and _complete_month in _complete_months:
            idx = _complete_months.index(_complete_month)
            if idx > 0:
                _prev_complete = _complete_months[idx - 1]
        if not _prev_complete and _complete_month:
            py, pm = int(_complete_month[:4]), int(_complete_month[5:7])
            if pm == 1:
                py, pm = py - 1, 12
            else:
                pm -= 1
            _prev_complete = f"{py:04d}-{pm:02d}"

        prev_total_controllable = 0.0
        trend_score = trend_weight * 0.5
        trend_reason = "No prior complete month available; trend is neutral."
        if _prev_complete:
            py, pm = int(_prev_complete[:4]), int(_prev_complete[5:7])
            p_start, p_end = month_range(py, pm)
            prev_cats = spending_by_category(p_start, p_end, conn=conn)
            prev_ctrl = [
                c for c in (prev_cats or [])
                if c.get("category")
                and c["category"] not in _NON_CONTROLLABLE_CATS
            ]
            prev_total_controllable = sum(c["total"] for c in prev_ctrl) or 0.0
            if prev_total_controllable > 0:
                change_pct = (
                    (_total_controllable - prev_total_controllable)
                    / prev_total_controllable
                ) * 100
                if change_pct <= -10:
                    trend_score = trend_weight
                elif change_pct <= 0:
                    trend_score = trend_weight * 0.85
                elif change_pct <= 10:
                    trend_score = trend_weight * 0.55
                elif change_pct <= 25:
                    trend_score = trend_weight * 0.25
                else:
                    trend_score = 0.0
                direction = "down" if change_pct < 0 else "up"
                trend_reason = (
                    f"Controllable spending is {direction} "
                    f"{abs(change_pct):.0f}% vs {_prev_complete} "
                    f"(${_total_controllable:,.0f} vs "
                    f"${prev_total_controllable:,.0f})."
                )

        diversity_score = max(0.0, min(w_diversity, concentration_score + trend_score))
        diversity_reason = (
            f"Top controllable category: {top_ctrl['category']}, "
            f"${top_ctrl['total']:,.0f}, {top_pct:.0f}% of controllable "
            f"spending across {score_period_label}. {trend_reason} "
            "Fixed obligations (Housing/Mortgage, Utilities/Bills, "
            "Insurance), transfers, card payments, and finance charges "
            "are excluded."
        )
        diversity_sufficient = True
    elif cats:
        # Spending exists but all of it falls in fixed categories.
        # Give full credit — there's nothing controllable to be
        # concentrated in.
        diversity_score = w_diversity
        diversity_reason = (
            f"No controllable consumption in {score_period_label} "
            "(only fixed obligations). Full credit by default."
        )
        diversity_sufficient = True
    else:
        diversity_score = w_diversity * 0.5
        diversity_reason = (
            f"No spending categories in {score_period_label}."
        )
        diversity_sufficient = False

    # ── Debt / fees score ─────────────────────────
    # This dimension is exact-statement only. Transaction-row guessing produced
    # false confidence when finance-charge rows disagreed with the Mastercard
    # summary. If no saved Mastercard summary overlaps the scoring window, the
    # dimension is neutral until Ledger can read the bank summary directly.
    close_c = False
    if conn is None:
        conn = get_connection()
        close_c = True

    from utils.database import get_statement_summaries_in_range

    debt_source: str = "missing_summary"
    _summary_rows: list[dict] = []
    interest_total = 0.0
    summary_interest_total = 0.0
    summary_fees_total = 0.0
    summary_cash_adv_total = 0.0
    _fc_rows: list[dict] = []
    debt_sufficient = False
    try:
        _summary_rows = get_statement_summaries_in_range(
            start, end, account_type="mastercard", conn=conn,
        ) or []
    except Exception:
        _summary_rows = []

    if _summary_rows:
        debt_source = "summary"
        debt_sufficient = True
        for s in _summary_rows:
            summary_interest_total += float(s.get("interest_charges") or 0)
            summary_fees_total     += float(s.get("fees") or 0)
            summary_cash_adv_total += float(s.get("cash_advances_total") or 0)
        interest_total = summary_interest_total + summary_fees_total

    monthly_interest_avg = (
        interest_total / max(1, _months_in_window)
    )
    if debt_sufficient:
        # $0 interest/fees = full credit; $50/mo or more = zero.
        debt_score = max(0, w_debt - (monthly_interest_avg / 50) * w_debt)
    else:
        debt_score = w_debt * 0.5

    if debt_source == "summary":
        _periods_bits: list[str] = []
        for s in _summary_rows[:3]:
            lbl = (s.get("statement_period_label") or "").strip()
            if not lbl:
                lbl = (
                    f"{s.get('statement_start_date','?')}.."
                    f"{s.get('statement_end_date','?')}"
                )
            _periods_bits.append(lbl)
        _periods = "; ".join(_periods_bits)
        debt_reason = (
            f"${summary_interest_total:,.2f} interest and "
            f"${summary_fees_total:,.2f} fees from Mastercard statement "
            f"summary ({_periods}). Cash-advance principal is not scored. "
            "Source: bank-provided statement summary."
        )
    else:
        debt_reason = (
            f"No Mastercard statement summary saved for {score_period_label}. "
            "Debt & fees is held at a neutral placeholder until Ledger can "
            "read the bank-provided Interest charges and Fees lines. "
            "Transaction rows are not used for this score."
        )

    # ── Consistency score ─────────────────────────
    # Fair small-window handling: if fewer than 2 months of data, report
    # "insufficient" rather than scoring 0 against the user.
    complete_months = list((_cov.get("complete_months") or []))
    recent_month_names = complete_months[-3:] if complete_months else []
    recent = [m for m in monthly if m["month"] in recent_month_names]
    if not recent:
        recent = [m for m in monthly if m["month"] >= start[:7]]
    if months_active >= 2 and recent:
        positive_months = sum(1 for m in recent if m["net"] >= 0)
        consistency_score = (positive_months / len(recent)) * w_consistency
        consistency_reason = (
            f"{positive_months}/{len(recent)} complete statement month(s) had net >= 0."
        )
        consistency_sufficient = True
    else:
        # Neutral — give half credit so a single bad month doesn't tank the total.
        consistency_score = w_consistency * 0.5
        consistency_reason = "Needs 2+ months of data; showing neutral placeholder."
        consistency_sufficient = False

    total = round(savings_score + diversity_score + debt_score + consistency_score)

    if close_c:
        conn.close()

    dimensions = [
        {"key": "savings",     "label": "Savings",
         "score": round(savings_score, 1),    "max": w_savings,
         "reason": savings_reason, "sufficient": savings_sufficient},
        {"key": "diversity",   "label": "Spending control",
         "score": round(diversity_score, 1),  "max": w_diversity,
         "reason": diversity_reason, "sufficient": diversity_sufficient},
        {"key": "debt",        "label": "Debt & fees",
         "score": round(debt_score, 1),       "max": w_debt,
         "reason": debt_reason, "sufficient": debt_sufficient},
        {"key": "consistency", "label": "Consistency",
         "score": round(consistency_score, 1), "max": w_consistency,
         "reason": consistency_reason, "sufficient": consistency_sufficient},
    ]

    prelim = confidence.get("level") in ("insufficient", "low")

    return {
        "total": min(100, total),
        "savings_score": round(savings_score, 1),
        "diversity_score": round(diversity_score, 1),
        "debt_score": round(debt_score, 1),
        "consistency_score": round(consistency_score, 1),
        "savings_rate": cf["savings_rate"],
        "weights": {"savings": w_savings, "diversity": w_diversity,
                    "debt": w_debt, "consistency": w_consistency},
        "breakdown": {
            "period": f"{start} -> {end}",
            "income": cf["income"],
            "spending": cf["spending"],
            "net": cf["net"],
        },
        # New — additive only, backwards-compatible
        "dimensions":      dimensions,
        "data_confidence": confidence,
        "sufficient":      months_active >= 2,
        "prelim":          prelim,
        # Expose which window drove the score so the Dashboard "Why this
        # score?" panel can render the right text.
        "score_period_label":  score_period_label,
        "score_window_label":  score_window_label,
        "analysis_month":       _complete_month,
        "statement_coverage": {
            "latest_complete_month": _complete_month,
            "latest_data_month":     _cov.get("latest_data_month", ""),
            "partial_months":        list(_cov.get("partial_months") or []),
            "incomplete_reason":     _cov.get("incomplete_reason", ""),
        },
        # Surface the exact finance-charge evidence behind the Debt & fees
        # dimension so the user can verify it against their PDFs.
        "finance_charges": {
            "source":    debt_source,   # 'summary' | 'missing_summary'
            "total":     round(interest_total, 2),
            "row_count": len(_fc_rows),
            "rows":      _fc_rows,
            "summary": [
                {
                    "period_label":      s.get("statement_period_label") or "",
                    "statement_start":   s.get("statement_start_date"),
                    "statement_end":     s.get("statement_end_date"),
                    "interest_charges":  round(float(s.get("interest_charges") or 0), 2),
                    "fees":              round(float(s.get("fees") or 0), 2),
                    "cash_advances_total": round(float(s.get("cash_advances_total") or 0), 2),
                    "new_balance":       round(float(s.get("new_balance") or 0), 2)
                                          if s.get("new_balance") is not None else None,
                }
                for s in _summary_rows
            ],
            "summary_totals": {
                "interest_charges":     round(summary_interest_total, 2),
                "fees":                 round(summary_fees_total, 2),
                "cash_advances_total":  round(summary_cash_adv_total, 2),
            },
        },
    }


def score_label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    elif score >= 65:
        return "Good"
    elif score >= 50:
        return "Fair"
    elif score >= 35:
        return "Needs Work"
    else:
        return "At Risk"


# ──────────────────────────────────────────────
# Month-over-month trend
# ──────────────────────────────────────────────

def spending_trend(category: str, months: int = 6, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Returns monthly totals for a specific category over the last N months."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    today = date.today()
    start = (today - timedelta(days=30 * months)).isoformat()

    rows = conn.execute("""
        SELECT strftime('%Y-%m', transaction_date) AS month, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category = ?
          AND transaction_date >= ?
          AND direction = 'debit'
          AND is_transfer = 0
        GROUP BY month
        ORDER BY month
    """, (category, start)).fetchall()

    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def net_worth_snapshot(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Returns latest investment total + estimated liquid balance."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    inv_row = conn.execute(
        "SELECT SUM(market_value) AS total FROM investments"
    ).fetchone()
    investments = inv_row["total"] or 0.0

    # Estimate liquid from last 90 days of net cashflow
    start, end = last_n_months(3)
    cf = period_cashflow(start, end, conn=conn)

    if close:
        conn.close()

    return {
        "investments": round(investments, 2),
        "net_cashflow_3m": cf["net"],
        "note": "Bank balances not automatically tracked — enter manually in Settings.",
    }


# ──────────────────────────────────────────────
# Income analytics
# ──────────────────────────────────────────────

def income_summary(start_date: str, end_date: str, account_type: Optional[str] = None,
                   exclude_etransfer_in: bool = False,
                   conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Returns total true income, source breakdown, and monthly trend
    for the given date range.

    True income (v8) = all credits with amount > 0:
      - direction = 'credit' AND amount > 0
      - Excludes direction IN ('payment','cancelled','transfer')
      - Excludes category IN ('Credit Card Payment','Cancelled')
      - Includes: payroll (EFT), INTERAC e-Transfers IN, interest, any deposit

    Pass 14 addition: `exclude_etransfer_in=True` also drops rows with
    category = 'Transfer In'. Useful when received e-Transfers are personal /
    pass-through movements rather than real income.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    _acct_filter = ""
    if account_type and account_type != "all":
        _acct_filter = f" AND account_type = '{account_type}'"
    _etxfer_filter = " AND category != 'Transfer In'" if exclude_etransfer_in else ""

    # Total + count
    row = conn.execute(f"""
        SELECT
            SUM(amount)        AS total,
            COUNT(*)           AS tx_count,
            COUNT(DISTINCT strftime('%Y-%m', transaction_date)) AS months_active
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'credit'
          AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category NOT IN ('Credit Card Payment','Cancelled')
          {_acct_filter}
          {_etxfer_filter}
    """, (start_date, end_date)).fetchone()

    total       = row["total"]        or 0.0
    tx_count    = row["tx_count"]     or 0
    months_active = row["months_active"] or 0

    # Monthly trend
    monthly = conn.execute(f"""
        SELECT
            strftime('%Y-%m', transaction_date) AS month,
            SUM(amount) AS total,
            COUNT(*) AS tx_count
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'credit'
          AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category NOT IN ('Credit Card Payment','Cancelled')
          {_acct_filter}
          {_etxfer_filter}
        GROUP BY month
        ORDER BY month
    """, (start_date, end_date)).fetchall()

    monthly_list = [dict(r) for r in monthly]

    # Stability: stddev proxy (max - min) / avg
    # With <2 months we cannot measure variance — report None so the UI can
    # render "Insufficient data" instead of a misleading 100%.
    monthly_totals = [m["total"] for m in monthly_list]
    if len(monthly_totals) >= 2:
        avg_m = sum(monthly_totals) / len(monthly_totals)
        variance = sum((x - avg_m) ** 2 for x in monthly_totals) / len(monthly_totals)
        stddev   = variance ** 0.5
        consistency_pct = max(0.0, round((1 - stddev / avg_m) * 100, 1)) if avg_m > 0 else 0.0
    else:
        consistency_pct = None  # not enough months to measure

    if close:
        conn.close()

    return {
        "total":            round(total, 2),
        "tx_count":         tx_count,
        "months_active":    months_active,
        "avg_monthly":      round(total / months_active, 2) if months_active > 0 else 0.0,
        "consistency_pct":  consistency_pct,
        "monthly_trend":    monthly_list,
    }


def income_by_source(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    exclude_etransfer_in: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Returns [{source, total, tx_count, pct, avg_amount}] sorted by total desc.
    Source = category (e.g. 'Income', 'Transfer In') then subcategory/merchant for detail.
    v8: includes all true income credits — payroll, e-Transfers IN, interest, etc.
    `exclude_etransfer_in=True` drops Transfer In rows (Pass 14).
    account_type: optional filter ('chequing', 'savings', 'mastercard', or None/all)
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    _acct_filter = ""
    if account_type and account_type != "all":
        _acct_filter = f" AND account_type = '{account_type.replace(chr(39), chr(39)*2)}'"
    _etxfer_filter = " AND category != 'Transfer In'" if exclude_etransfer_in else ""

    rows = conn.execute(f"""
        SELECT
            COALESCE(NULLIF(subcategory,''), NULLIF(merchant,''), raw_description) AS source,
            category,
            SUM(amount)   AS total,
            COUNT(*)      AS tx_count,
            AVG(amount)   AS avg_amount
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'credit'
          AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category NOT IN ('Credit Card Payment','Cancelled')
          {_acct_filter}
          {_etxfer_filter}
        GROUP BY source
        ORDER BY total DESC
    """, (start_date, end_date)).fetchall()

    grand = sum(r["total"] for r in rows) or 1.0
    result = []
    for r in rows:
        result.append({
            "source":     r["source"] or "Unknown",
            "category":   r["category"] or "Unknown",
            "total":      round(r["total"], 2),
            "tx_count":   r["tx_count"],
            "avg_amount": round(r["avg_amount"], 2),
            "pct":        round(r["total"] / grand * 100, 1),
        })

    if close:
        conn.close()
    return result


def income_monthly_by_source(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    exclude_etransfer_in: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Returns {source: [{month, total}]} for stacked area chart.
    v8: same broad income definition as income_summary / income_by_source.
    `exclude_etransfer_in=True` drops Transfer In rows (Pass 14).
    account_type: optional filter ('chequing', 'savings', 'mastercard', or None/all)
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    _acct_filter = ""
    if account_type and account_type != "all":
        _acct_filter = f" AND account_type = '{account_type.replace(chr(39), chr(39)*2)}'"
    _etxfer_filter = " AND category != 'Transfer In'" if exclude_etransfer_in else ""

    rows = conn.execute(f"""
        SELECT
            COALESCE(NULLIF(subcategory,''), NULLIF(merchant,''), raw_description) AS source,
            strftime('%Y-%m', transaction_date) AS month,
            SUM(amount) AS total
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'credit'
          AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category NOT IN ('Credit Card Payment','Cancelled')
          {_acct_filter}
          {_etxfer_filter}
        GROUP BY source, month
        ORDER BY source, month
    """, (start_date, end_date)).fetchall()

    result: dict[str, list[dict]] = {}
    for r in rows:
        src = r["source"] or "Unknown"
        result.setdefault(src, []).append({"month": r["month"], "total": round(r["total"], 2)})

    if close:
        conn.close()
    return result

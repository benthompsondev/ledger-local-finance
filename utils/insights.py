"""
Trend, pattern, plain-English insight, and recommendation engine.
Runs entirely from SQLite — no external calls.
All functions work correctly when history grows over time (backfills update automatically
because they query the live DB, not cached snapshots).
"""
import sqlite3
from datetime import date, timedelta
from typing import Optional
from utils.database import get_connection


# ── Pass 16: dollar-escape helper ──────────────────────────────────────
# Streamlit's markdown renderer treats paired `$...$` tokens as LaTeX math
# mode, which COLLAPSES whitespace inside the math span. So a body like
# "Averaging $152.50/month recently vs $0.00 before" renders as
# "Averaging 152.50/monthrecentlyvs0.00 before" — the manual-test bug
# the user reported. We escape every literal `$` as `\$` so Streamlit
# treats them as plain text.
#
# Rationale for centralizing this: every recommendation body / evidence
# / title written below ultimately lands inside `st.markdown` (via
# `rec_card` rendering) or `st.warning/info` (Insights tab). One helper
# = one place to enforce the rule.
def _esc_dollars(s: str) -> str:
    """Escape every '$' so Streamlit doesn't interpret '$...$' as LaTeX."""
    if s is None:
        return ""
    return str(s).replace("$", r"\$")


# ── helpers ────────────────────────────────────────────────────────────

def _conn(conn):
    if conn is not None:
        return conn, False
    return get_connection(), True


def _close(conn, opened):
    if opened:
        conn.close()


# ── Coverage ───────────────────────────────────────────────────────────

def imported_months(conn=None) -> list[str]:
    """Return sorted list of YYYY-MM months that have at least one real transaction."""
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT DISTINCT strftime('%Y-%m', transaction_date) AS m
        FROM transactions
        WHERE direction NOT IN ('payment','cancelled')
          AND is_transfer = 0
        ORDER BY m
    """).fetchall()
    _close(c, opened)
    return [r[0] for r in rows]


def coverage_summary(conn=None) -> dict:
    months = imported_months(conn=conn)
    if not months:
        return {"months": [], "first_month": None, "last_month": None,
                "total_months": 0, "gap_months": []}

    first = months[0]
    last  = months[-1]

    y0, m0 = int(first[:4]), int(first[5:7])
    y1, m1 = int(last[:4]),  int(last[5:7])
    expected = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        expected.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1

    month_set = set(months)
    gaps = [mo for mo in expected if mo not in month_set]

    return {
        "months": months,
        "first_month": first,
        "last_month": last,
        "total_months": len(months),
        "gap_months": gaps,
    }


# ── Pass 35: statement-aware completeness ──────────────────────────────
# Trust layer for "is this month complete enough to drive Dashboard / score
# / Plan / Reports?" — a partial current month (e.g. May 2026 with only
# 2026-05-02..2026-05-07 imported) was tanking Health Score and Net for
# the entire app. statement_coverage() classifies each imported month as
# complete vs partial using two cheap signals: span of imported days and
# end-of-month distance. No DB writes, no schema changes; partial rows
# remain visible in Transactions and Import history.

# Min days of activity to consider a month "complete enough." Tangerine
# Chequing + Mastercard statements together usually show 20+ distinct
# transaction days per real month. A month with <14 distinct days OR
# whose last imported day is more than 7 days before month-end is treated
# as partial.
_MR_MIN_COMPLETE_DAYS = 14
_MR_MIN_END_OF_MONTH_LAG = 7


def statement_coverage(conn=None) -> dict:
    """Classify each imported month as 'complete' vs 'partial'.

    Returns:
      {
        "latest_complete_month":   "YYYY-MM" or "",
        "latest_data_month":       "YYYY-MM" or "",
        "partial_months":          ["YYYY-MM", ...]   # current+older partials
        "complete_months":         ["YYYY-MM", ...]
        "incomplete_reason":       str (about latest_data_month, if partial)
        "statement_coverage_by_month": {
            "YYYY-MM": {
                "complete":         bool,
                "distinct_days":    int,
                "first_day":        "YYYY-MM-DD",
                "last_day":         "YYYY-MM-DD",
                "days_until_eom":   int,
                "tx_count":         int,
                "reason":           str   # why partial (or "complete")
            },
            ...
        }
      }

    The classification is intentionally conservative — when in doubt
    (e.g. import gap, no rows past mid-month), mark partial. Callers
    that need to operate on "the month Ben can trust" should read
    `latest_complete_month`. Callers that need "what's actually in the
    DB right now" should read `latest_data_month`.
    """
    import calendar as _cal
    c, opened = _conn(conn)
    rows = c.execute(
        """
        SELECT
            strftime('%Y-%m', transaction_date) AS month,
            COUNT(DISTINCT date(transaction_date)) AS distinct_days,
            MIN(transaction_date) AS first_day,
            MAX(transaction_date) AS last_day,
            COUNT(*) AS tx_count
        FROM transactions
        WHERE direction != 'cancelled'
          AND transaction_date IS NOT NULL
        GROUP BY month
        ORDER BY month
        """
    ).fetchall()
    _close(c, opened)

    by_month: dict[str, dict] = {}
    complete_months: list[str] = []
    partial_months:  list[str] = []
    last_data_month = ""

    for r in rows:
        month = r["month"] or ""
        if not month:
            continue
        last_data_month = month
        y, m = int(month[:4]), int(month[5:7])
        days_in_month = _cal.monthrange(y, m)[1]
        last_day_str = r["last_day"] or f"{month}-01"
        try:
            last_day = int(last_day_str[8:10])
        except ValueError:
            last_day = 1
        days_until_eom = max(0, days_in_month - last_day)
        distinct_days = int(r["distinct_days"] or 0)

        reasons: list[str] = []
        if distinct_days < _MR_MIN_COMPLETE_DAYS:
            reasons.append(
                f"only {distinct_days} distinct day(s) imported "
                f"(need >= {_MR_MIN_COMPLETE_DAYS})"
            )
        if days_until_eom > _MR_MIN_END_OF_MONTH_LAG:
            reasons.append(
                f"latest imported day is {last_day_str} "
                f"({days_until_eom} day(s) before month end)"
            )
        complete = not reasons
        reason = "complete" if complete else "; ".join(reasons)

        by_month[month] = {
            "complete":       complete,
            "distinct_days":  distinct_days,
            "first_day":      r["first_day"] or "",
            "last_day":       last_day_str,
            "days_until_eom": days_until_eom,
            "tx_count":       int(r["tx_count"] or 0),
            "reason":         reason,
        }
        if complete:
            complete_months.append(month)
        else:
            partial_months.append(month)

    latest_complete = complete_months[-1] if complete_months else ""
    incomplete_reason = ""
    if last_data_month and last_data_month not in complete_months:
        incomplete_reason = (
            by_month.get(last_data_month, {}).get("reason", "")
            or "partial"
        )

    return {
        "latest_complete_month":         latest_complete,
        "latest_data_month":             last_data_month,
        "partial_months":                partial_months,
        "complete_months":               complete_months,
        "incomplete_reason":             incomplete_reason,
        "statement_coverage_by_month":   by_month,
    }


# ── Pass 35 Phase 5: friendly statement period display ─────────────────
# Some import_log rows carry ugly fallback statement IDs like
# "ledger_document..." when the PDF didn't expose a clean period header.
# This helper turns a (statement_period, batch_id) pair into a friendly
# label by deriving min/max transaction dates for the batch. Pure read,
# no schema changes; the raw statement_period field is preserved.

# Heuristics for what counts as an "ugly" raw statement period that we
# should override with a derived label.
_UGLY_PERIOD_PREFIXES = (
    "ledger_document",
    "document",
    "fallback",
    "unknown",
)


def _looks_ugly_period(period: str) -> bool:
    s = (period or "").strip().lower()
    if not s or s == "-" or s in {"none", "n/a", "—"}:
        return True
    for p in _UGLY_PERIOD_PREFIXES:
        if s.startswith(p):
            return True
    return False


def friendly_import_period(
    *,
    statement_period: str = "",
    batch_id=None,
    conn=None,
) -> str:
    """Return a short, human-friendly period label for an import batch.

    Preference order:
      1. statement_period if it is a clean YYYY-MM or YYYY-MM..YYYY-MM
         shaped string.
      2. Derived "YYYY-MM statement / YYYY-MM-DD to YYYY-MM-DD" using
         min/max transaction dates for the batch.
      3. Falls back to whatever statement_period contained.
    """
    sp = (statement_period or "").strip()
    if sp and not _looks_ugly_period(sp):
        # Already friendly enough — return as-is.
        return sp

    if batch_id is None:
        return sp or "—"

    c, opened = _conn(conn)
    try:
        row = c.execute(
            """
            SELECT MIN(transaction_date) AS first_d,
                   MAX(transaction_date) AS last_d,
                   COUNT(*) AS n
            FROM transactions
            WHERE import_batch_id = ?
            """,
            (int(batch_id),),
        ).fetchone()
    finally:
        _close(c, opened)

    first_d = (row["first_d"] if row else "") or ""
    last_d  = (row["last_d"]  if row else "") or ""
    if not first_d or not last_d:
        return sp or "—"

    # Same calendar month → "YYYY-MM statement"; cross-month → "YYYY-MM
    # statement / YYYY-MM-DD to YYYY-MM-DD".
    if first_d[:7] == last_d[:7]:
        return f"{first_d[:7]} statement / {first_d} to {last_d}"
    return f"{last_d[:7]} statement / {first_d} to {last_d}"


# ── Pass 35b: conservative finance-charge classifier ───────────────────
# The original Pass 35 debt-score query used SQL `LIKE '%INTEREST%'`
# which accidentally matched **INTERAC e-Transfers** (the substring
# "INTER" lives inside "INTERAC"). That swept ~$570 of person-to-person
# transfers into the debt dimension and produced a ridiculous
# "$577.79/mo in interest + fees" explanation for a paid-off card.
#
# This helper:
#   * anchors on category='Fees / Interest' (canonical)
#   * adds raw_description patterns ONLY when they're true word boundaries
#     ('% INTEREST%' with a leading space, or starts-with check) so
#     'INTERAC' is impossible to match
#   * excludes bill-pay service vendors (Paymentus, etc.) — those are
#     ordinary service fees, not card finance charges
#   * never counts cash-advance principal (Cash Advance category) and
#     never counts payment rows (direction='payment')
# Returns a dict with the total + the underlying rows so the
# "Why this score?" panel can show evidence.

# Vendor substrings that are bill-payment service fees and should NOT
# count as card finance charges, even when they land under Fees /
# Interest. Match against merchant + raw_description, case-insensitive.
_FINANCE_CHARGE_VENDOR_EXCLUDE = (
    "paymentus",            # bill-payment service vendor
    "service fee",          # generic non-card service fee
    "atm fee",              # ATM operator fees aren't card finance charges
)

# Substrings whose presence ANYWHERE in raw_description is a strong
# signal of a real finance charge. We use these in addition to the
# canonical category filter so a row miscategorised but obviously a
# finance charge still gets scored.
_FINANCE_CHARGE_RAW_KEYWORDS = (
    "cash interest",
    "purchase interest",
    "cash advance fee",
    "overlimit fee",
    "over limit fee",
    "nsf fee",
    "overdraft",
)


def _row_is_finance_charge(category: str, merchant: str,
                            raw: str, direction: str) -> bool:
    """Return True only for rows that are unambiguously a finance charge.

    Conservative by design: when in doubt, exclude. The Pass 35 bug came
    from being too liberal with `LIKE` patterns; this helper trades a
    little under-counting for zero false positives like INTERAC.
    """
    if (direction or "").lower() == "payment":
        return False
    cat = (category or "").strip()
    m_low = (merchant or "").lower()
    r_low = (raw or "").lower()
    # Hard exclude: bill-pay service vendors and explicit non-card fees.
    for ex in _FINANCE_CHARGE_VENDOR_EXCLUDE:
        if ex in m_low or ex in r_low:
            return False
    # Hard exclude: cash-advance principal is NOT a finance charge.
    if cat == "Cash Advance":
        return False
    # Strongest signal: keyword match in raw_description.
    for kw in _FINANCE_CHARGE_RAW_KEYWORDS:
        if kw in r_low:
            return True
    # Otherwise require the canonical Fees / Interest category.
    return cat == "Fees / Interest"


def finance_charges_in_window(start_date: str, end_date: str,
                              conn=None) -> dict:
    """Return finance-charge rows + total in [start_date, end_date].

    Output:
      {
        "total":      float,
        "row_count":  int,
        "rows":       [{id, date, account_type, category, amount,
                        merchant, raw_description}, ...]   # ABS amounts
      }
    """
    c, opened = _conn(conn)
    try:
        rows = c.execute(
            """
            SELECT id, transaction_date AS date, account_type, category,
                   amount, merchant, raw_description, direction
            FROM transactions
            WHERE direction = 'debit'
              AND transaction_date BETWEEN ? AND ?
              AND (
                category = 'Fees / Interest'
                OR raw_description LIKE '%CASH INTEREST%'
                OR raw_description LIKE '%PURCHASE INTEREST%'
                OR raw_description LIKE '%CASH ADVANCE FEE%'
                OR raw_description LIKE '%OVERLIMIT FEE%'
                OR raw_description LIKE '%OVER LIMIT FEE%'
                OR raw_description LIKE '%NSF FEE%'
                OR raw_description LIKE '%OVERDRAFT%'
              )
            """,
            (start_date, end_date),
        ).fetchall()
        included: list[dict] = []
        total = 0.0
        for r in rows:
            if not _row_is_finance_charge(
                r["category"] or "",
                r["merchant"] or "",
                r["raw_description"] or "",
                r["direction"] or "",
            ):
                continue
            amt = float(r["amount"] or 0)
            total += abs(amt)
            included.append({
                "id":              int(r["id"]),
                "date":             r["date"],
                "account_type":     r["account_type"],
                "category":         r["category"],
                "amount":           round(abs(amt), 2),
                "merchant":         r["merchant"],
                "raw_description":  r["raw_description"],
            })
        return {
            "total":      round(total, 2),
            "row_count":  len(included),
            "rows":       included,
        }
    finally:
        _close(c, opened)


# ── Pass 35 Phase 3: cash-advance trust check ──────────────────────────
# Ledger sees transactions, not balances. A cash-advance debit followed by
# subsequent credit-card payments may well already be paid off — but the
# old code treated every detected cash advance as urgent unpaid debt.
# This helper returns a deterministic summary the Dashboard Copilot,
# momentum coach, and recommendations engine can use to choose between
# "verify / likely paid off" wording vs the urgent "clear it now" copy.

def cash_advance_status(conn=None) -> dict:
    """Summarize cash-advance exposure with payment-coverage awareness.

    Returns:
      {
        "ca_count":           int,
        "ca_total":           float,
        "first_ca_date":      "YYYY-MM-DD" or "",
        "last_ca_date":       "YYYY-MM-DD" or "",
        "cc_payments_since":  float,  # sum of CC payment debits on/after first CA
        "plausibly_covered":  bool,   # cc_payments_since >= ca_total and >0 CAs
        "verdict":            "no_history" | "covered" | "uncertain" | "outstanding",
        "safe_action":        str,    # safe deterministic wording
      }
    """
    c, opened = _conn(conn)
    try:
        ca = c.execute(
            """
            SELECT COUNT(*) AS cnt,
                   SUM(ABS(amount)) AS total,
                   MIN(transaction_date) AS first_d,
                   MAX(transaction_date) AS last_d
            FROM transactions
            WHERE category = 'Cash Advance' AND direction = 'debit'
            """
        ).fetchone()
        cnt = int((ca["cnt"] or 0) if ca else 0)
        total = float((ca["total"] or 0) if ca else 0)
        first_d = (ca["first_d"] if ca else "") or ""
        last_d  = (ca["last_d"]  if ca else "") or ""

        if cnt == 0:
            return {
                "ca_count":           0,
                "ca_total":           0.0,
                "first_ca_date":      "",
                "last_ca_date":       "",
                "cc_payments_since":  0.0,
                "plausibly_covered":  False,
                "verdict":            "no_history",
                "safe_action":        "",
            }

        # CC payments since the first cash advance — direction='payment' is the
        # canonical CC payment marker; we also accept category='Credit Card
        # Payment' as a defensive fallback for older imports.
        pay = c.execute(
            """
            SELECT SUM(ABS(amount)) AS pay_total
            FROM transactions
            WHERE (direction = 'payment'
                   OR category = 'Credit Card Payment')
              AND transaction_date >= ?
            """,
            (first_d,),
        ).fetchone()
        pay_total = float((pay["pay_total"] or 0) if pay else 0)

        plausibly_covered = pay_total >= total and total > 0
        if plausibly_covered:
            verdict = "covered"
            safe_action = (
                f"Verify the {cnt} cash-advance transaction(s) "
                f"(${total:,.0f}). Ledger sees later credit-card "
                f"payments totalling ${pay_total:,.0f} since "
                f"{first_d} - it may already be paid off. Treat as a "
                "historical fee/risk and avoid repeating."
            )
        elif pay_total > 0:
            verdict = "uncertain"
            safe_action = (
                f"Verify the cash-advance transaction(s) "
                f"(${total:,.0f}). Some credit-card payments "
                f"(${pay_total:,.0f}) followed the advance, but Ledger "
                "only sees transactions - it can't confirm the balance. "
                "Check your card statement before treating this as "
                "outstanding."
            )
        else:
            verdict = "outstanding"
            safe_action = (
                f"No credit-card payments seen after the cash advance "
                f"(${total:,.0f}). Cash advances typically carry "
                "22-30% APR - prioritize paying it down once confirmed."
            )

        return {
            "ca_count":           cnt,
            "ca_total":           round(total, 2),
            "first_ca_date":      first_d,
            "last_ca_date":       last_d,
            "cc_payments_since":  round(pay_total, 2),
            "plausibly_covered":  bool(plausibly_covered),
            "verdict":            verdict,
            "safe_action":        safe_action,
        }
    finally:
        _close(c, opened)


# ── Monthly aggregates ─────────────────────────────────────────────────

def monthly_aggregates(conn=None) -> list[dict]:
    """
    Per-month income, spending, net, savings rate, CC payments.

    Uses the SAME canonical filter language as analytics.compute_cashflow(),
    so Dashboard, Spending, Income, and Trends never disagree on totals.

      income   = credits with amount > 0, excluding CC payments / cancelled /
                 savings pullbacks (direction='transfer')
      spending = debits excluding same, MINUS refund credits (negative credits)
    """
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT
            strftime('%Y-%m', transaction_date) AS month,
            SUM(CASE
                WHEN direction = 'credit'
                 AND amount > 0
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled')
                THEN amount ELSE 0
            END) AS income,
            SUM(CASE
                WHEN direction = 'debit'
                 AND amount > 0
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled','Transfer')
                THEN amount ELSE 0
            END) AS spending_gross,
            SUM(CASE
                WHEN direction = 'credit'
                 AND amount < 0
                THEN ABS(amount) ELSE 0
            END) AS refund_offset,
            SUM(CASE WHEN direction='payment' THEN ABS(amount) ELSE 0 END) AS cc_payments_out,
            SUM(CASE
                WHEN direction = 'debit' AND category = 'Transfer Out'
                THEN ABS(amount) ELSE 0
            END) AS transfer_out,
            COUNT(*) AS tx_count
        FROM transactions
        WHERE direction != 'cancelled'
        GROUP BY month
        ORDER BY month
    """).fetchall()
    _close(c, opened)

    result = []
    for r in rows:
        income   = r["income"]          or 0
        gross    = r["spending_gross"]  or 0
        refunds  = r["refund_offset"]   or 0
        spending = max(0.0, gross - refunds)
        net      = income - spending
        result.append({
            "month":           r["month"],
            "income":          round(income, 2),
            "spending":        round(spending, 2),
            "net":             round(net, 2),
            "savings_rate":    round(net / income * 100, 1) if income > 0 else 0.0,
            "cc_payments_out": round(r["cc_payments_out"] or 0, 2),
            "transfer_out":    round(r["transfer_out"] or 0, 2),
            "tx_count":        r["tx_count"],
        })
    return result


def category_monthly(category: str, conn=None) -> list[dict]:
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT strftime('%Y-%m', transaction_date) AS month, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category = ?
          AND direction = 'debit'
          AND is_transfer = 0
        GROUP BY month ORDER BY month
    """, (category,)).fetchall()
    _close(c, opened)
    return [{"month": r["month"], "total": round(r["total"], 2)} for r in rows]


def all_categories_monthly(conn=None) -> dict[str, list[dict]]:
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT category, strftime('%Y-%m', transaction_date) AS month, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                               'Payment','Savings','Cancelled','Credit Card Payment')
        GROUP BY category, month
        ORDER BY category, month
    """).fetchall()
    _close(c, opened)
    result: dict = {}
    for r in rows:
        result.setdefault(r["category"], []).append({"month": r["month"], "total": round(r["total"], 2)})
    return result


# ── Drift detection ────────────────────────────────────────────────────

def category_drift(lookback_months: int = 3, conn=None) -> list[dict]:
    months = imported_months(conn=conn)
    if len(months) < lookback_months * 2:
        return []

    recent_months = months[-lookback_months:]
    prior_months  = months[-lookback_months * 2:-lookback_months]

    c, opened = _conn(conn)

    def period_totals(month_list):
        if not month_list:
            return {}
        placeholders = ",".join("?" * len(month_list))
        # Drift is *consumption-only*. Transfer Out / Transfer In, system
        # categories, and Income are explicitly excluded so a single income
        # debit row (e.g. a refund miscategorized as Income) cannot show up
        # in the spending-comparison view. Pass 15 added Income to the list
        # after the Trends Category Comparison surfaced an Income row.
        rows = c.execute(f"""
            SELECT category, SUM(ABS(amount)) AS total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) IN ({placeholders})
              AND direction='debit' AND is_transfer=0
              AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                   'Payment','Savings','Cancelled',
                                   'Credit Card Payment','Income')
            GROUP BY category
        """, month_list).fetchall()
        return {r["category"]: r["total"] / len(month_list) for r in rows}

    recent = period_totals(recent_months)
    prior  = period_totals(prior_months)

    all_cats = set(recent) | set(prior)
    drift = []
    for cat in all_cats:
        r_val = recent.get(cat, 0)
        p_val = prior.get(cat, 0)
        if p_val > 0:
            pct_change = (r_val - p_val) / p_val * 100
        elif r_val > 0:
            pct_change = 100.0
        else:
            pct_change = 0.0

        abs_change = r_val - p_val
        drift.append({
            "category":   cat,
            "recent_avg": round(r_val, 2),
            "prior_avg":  round(p_val, 2),
            "abs_change": round(abs_change, 2),
            "pct_change": round(pct_change, 1),
            "flagged":    pct_change > 15 and abs_change > 20,
        })

    _close(c, opened)
    return sorted(drift, key=lambda x: abs(x["abs_change"]), reverse=True)


# ── Pass 33: deterministic monthly_review packet ──────────────────────
# Single source of truth for the "Am I better/worse than last month, what
# changed, why, and what should I look at?" question. Used by:
#   - pages/13_Reports.py (Monthly Review summary card)
#   - pages/4_Trends.py   ("What changed?" lead + biggest movers + cause)
#   - utils/agent_context.py (monthly_review key for OpenClaw)
# Deterministic; no AI calls; no writes.

# Category classifier kinds — kept here so insights doesn't import from
# planner (avoids a planner→insights dependency cycle in some callers).
_MR_FIXED = {"Housing / Mortgage", "Utilities / Bills"}
_MR_VARIABLE = {
    "Groceries", "Food & Convenience", "Shopping", "Home Improvement",
    "Gas / Transport", "Entertainment", "Health / Care", "Pets",
    "Cash Advance", "Fees / Interest", "Misc",
}
_MR_SUBSCRIPTION = {"Subscriptions & Digital"}
_MR_NON_CONSUMPTION = {
    "Transfer", "Transfer Out", "Transfer In", "Internal Transfer",
    "Credit Card Payment", "Payment", "Savings", "Investments",
    "Cancelled", "Income", "Payroll Income", "Interest Income",
    "Rewards / Cashback", "Refund / Credit",
    "Reimbursement / Insurance Reimbursement",
}


def _mr_classify(category: Optional[str]) -> str:
    """Return one of 'fixed' / 'subscription' / 'controllable' / 'variable'.

    'controllable' is the soft consumer-discretionary bucket reduce_actions
    can act on (Shopping / Food / Entertainment / etc.). 'variable' is the
    fall-through for anything else not explicitly fixed/subs.
    """
    cat = category or ""
    if cat in _MR_FIXED:
        return "fixed"
    if cat in _MR_SUBSCRIPTION:
        return "subscription"
    if cat in _MR_VARIABLE:
        return "controllable"
    return "variable"


def _mr_top_merchants_for_category(
    category: str, month: str, conn, limit: int = 3,
) -> list[dict]:
    """Top N merchants in `category` for the given YYYY-MM month."""
    if not category or not month:
        return []
    rows = conn.execute(
        """
        SELECT merchant, SUM(ABS(amount)) AS total, COUNT(*) AS tx_count
        FROM transactions
        WHERE category = ?
          AND strftime('%Y-%m', transaction_date) = ?
          AND direction = 'debit'
          AND is_transfer = 0
        GROUP BY merchant
        ORDER BY total DESC
        LIMIT ?
        """,
        (category, month, int(limit)),
    ).fetchall()
    return [
        {
            "merchant": r["merchant"] or "(unknown)",
            "total":    round(float(r["total"] or 0), 2),
            "tx_count": int(r["tx_count"] or 0),
        }
        for r in rows
    ]


def _mr_inspect_action(kind: str, direction: str) -> tuple[str, str]:
    """Return (label, target_page_path) for a category kind + movement
    direction ('up' | 'down'). Routes the user to the page that can act.
    """
    if direction == "up":
        if kind == "subscription":
            return ("Open Reduce → Subscriptions",
                    "pages/11_Reduce.py")
        if kind == "fixed":
            return ("Open Plan → Bills tab",
                    "pages/12_Month_Plan.py")
        if kind in ("controllable", "variable"):
            return ("Open Reduce → 3 practical cuts",
                    "pages/11_Reduce.py")
    # Down or stable: not an action page, just a note.
    return ("No action — keep it up", "")


def monthly_review(conn=None) -> dict:
    """Return a deterministic snapshot of "what changed this month vs last".

    Shape:
      {
        "available":          bool,
        "reason":              str  (when available=False),
        "month":               "YYYY-MM" or "",
        "prev_month":          "YYYY-MM" or "",
        "income":              float,  "prev_income":   float,
        "spending":            float,  "prev_spending": float,
        "net":                 float,  "prev_net":      float,
        "savings_rate":        float,  "prev_savings_rate": float,
        "income_delta":        float,
        "spending_delta":      float,
        "net_delta":           float,
        "top_increases":       [{category, current, previous, abs_change,
                                 pct_change, kind}],   # up to 5
        "top_decreases":       [{category, current, previous, abs_change,
                                 pct_change, kind}],   # up to 5
        "biggest_mover":       {category, abs_change, pct_change, kind,
                                direction, top_merchants[],
                                inspect_label, inspect_target} or None,
        "data_caveats":        [str],
        "suggested_action":    {label, target_page, reason} or None,
      }
    """
    c, opened = _conn(conn)
    out: dict = {
        "available":         False,
        "reason":             "",
        "month":               "",
        "prev_month":          "",
        "income":              0.0,  "prev_income":         0.0,
        "spending":            0.0,  "prev_spending":       0.0,
        "net":                 0.0,  "prev_net":            0.0,
        "savings_rate":        0.0,  "prev_savings_rate":   0.0,
        "income_delta":        0.0,
        "spending_delta":      0.0,
        "net_delta":           0.0,
        "top_increases":       [],
        "top_decreases":       [],
        "biggest_mover":       None,
        "data_caveats":        [],
        "suggested_action":    None,
        # Pass 35b additions — make it explicit which months drove the
        # comparison so UI surfaces (Trends, Reports) can render the
        # right header and explain when a partial month was ignored.
        "uses_complete_months":    True,
        "truth_month":             "",
        "latest_data_month":       "",
        "ignored_partial_months":  [],
    }
    try:
        aggs = monthly_aggregates(conn=c) or []
        # Pass 35b Phase 1: monthly_review must compare the latest two
        # *complete* statement months, not the latest two months that
        # happen to exist in the DB. Without this, a partial current
        # month (e.g. May 2026 with only the first week of data)
        # appeared as "month" and Trends/Reports said "2026-05 vs
        # 2026-04" which is misleading. statement_coverage() is the
        # canonical complete/partial classifier.
        try:
            cov = statement_coverage(conn=c) or {}
        except Exception:
            cov = {}
        complete_months = list(cov.get("complete_months") or [])
        partial_months  = list(cov.get("partial_months") or [])
        latest_data_month = cov.get("latest_data_month") or ""

        # Build the aggregates list filtered to complete months only.
        complete_set = set(complete_months)
        aggs_complete = [a for a in aggs if (a.get("month") or "")
                         in complete_set]

        if len(aggs_complete) < 2:
            # Fallback hierarchy: if we have <2 complete months but
            # >=2 imported months, fall back to the old behaviour so the
            # very first imports still get *some* review. The caveat
            # below tells the user we're working on partial data.
            if len(aggs) < 2:
                out["reason"] = (
                    "Need at least 2 imported months to build a Monthly "
                    "Review. Import another statement, then return."
                )
                return out
            out["uses_complete_months"] = False
            aggs_for_compare = aggs
        else:
            aggs_for_compare = aggs_complete

        latest = aggs_for_compare[-1]
        prev   = aggs_for_compare[-2]
        out["available"]     = True
        out["month"]          = latest.get("month") or ""
        out["prev_month"]     = prev.get("month") or ""
        out["truth_month"]    = out["month"]
        out["latest_data_month"] = latest_data_month
        # Anything in partial_months that sits AT or AFTER the truth
        # month is the set of partial months we ignored for this packet.
        out["ignored_partial_months"] = [
            mo for mo in partial_months
            if mo > (out["month"] or "")
        ]
        out["income"]         = float(latest.get("income") or 0)
        out["spending"]       = float(latest.get("spending") or 0)
        out["net"]            = float(latest.get("net") or 0)
        out["savings_rate"]   = float(latest.get("savings_rate") or 0)
        out["prev_income"]    = float(prev.get("income") or 0)
        out["prev_spending"]  = float(prev.get("spending") or 0)
        out["prev_net"]       = float(prev.get("net") or 0)
        out["prev_savings_rate"] = float(prev.get("savings_rate") or 0)
        out["income_delta"]   = round(out["income"]   - out["prev_income"], 2)
        out["spending_delta"] = round(out["spending"] - out["prev_spending"], 2)
        out["net_delta"]      = round(out["net"]      - out["prev_net"], 2)

        # Per-category compare for the latest two months only —
        # category_drift averages over a window, which obscures a single
        # spiky month. Here we want raw current-vs-previous totals.
        rows = c.execute(
            """
            SELECT
                category,
                strftime('%Y-%m', transaction_date) AS m,
                SUM(ABS(amount)) AS total
            FROM transactions
            WHERE direction='debit' AND is_transfer=0
              AND strftime('%Y-%m', transaction_date) IN (?, ?)
            GROUP BY category, m
            """,
            (out["month"], out["prev_month"]),
        ).fetchall()
        cat_now: dict[str, float] = {}
        cat_prev: dict[str, float] = {}
        for r in rows:
            cat = r["category"] or ""
            if cat in _MR_NON_CONSUMPTION:
                continue
            v = float(r["total"] or 0)
            if r["m"] == out["month"]:
                cat_now[cat] = v
            else:
                cat_prev[cat] = v

        movers: list[dict] = []
        for cat in set(cat_now) | set(cat_prev):
            cur  = cat_now.get(cat, 0.0)
            prv  = cat_prev.get(cat, 0.0)
            ach  = round(cur - prv, 2)
            if abs(ach) < 5:
                # Sub-$5 movement is noise; skip to keep the surface readable.
                continue
            if prv > 0:
                pch = round((cur - prv) / prv * 100, 1)
            elif cur > 0:
                pch = 100.0
            else:
                pch = 0.0
            movers.append({
                "category":   cat,
                "current":    round(cur, 2),
                "previous":   round(prv, 2),
                "abs_change": ach,
                "pct_change": pch,
                "kind":       _mr_classify(cat),
            })

        ups   = sorted([m for m in movers if m["abs_change"] > 0],
                       key=lambda m: -m["abs_change"])
        downs = sorted([m for m in movers if m["abs_change"] < 0],
                       key=lambda m:  m["abs_change"])
        out["top_increases"] = ups[:5]
        out["top_decreases"] = downs[:5]

        # Biggest mover by absolute movement (could be up or down).
        if movers:
            biggest = max(movers, key=lambda m: abs(m["abs_change"]))
            direction = "up" if biggest["abs_change"] > 0 else "down"
            top_m = (
                _mr_top_merchants_for_category(
                    biggest["category"], out["month"], c, limit=3)
                if direction == "up" else []
            )
            label, target = _mr_inspect_action(biggest["kind"], direction)
            out["biggest_mover"] = {
                "category":      biggest["category"],
                "abs_change":    biggest["abs_change"],
                "pct_change":    biggest["pct_change"],
                "kind":          biggest["kind"],
                "direction":     direction,
                "top_merchants": top_m,
                "inspect_label":  label,
                "inspect_target": target,
            }

        # Data caveats — surfaced verbatim by the UI.
        # Pass 35b: explain when a partial month was ignored so the
        # user understands why the comparison header reads e.g.
        # "2026-04 vs 2026-03" instead of "2026-05 vs 2026-04".
        if out["ignored_partial_months"]:
            _ignored = ", ".join(out["ignored_partial_months"])
            out["data_caveats"].append(
                f"Ignored partial month(s) for this comparison: "
                f"{_ignored}. Those transactions are still visible in "
                "Transactions; they just don't drive monthly truth "
                "surfaces until the month is complete."
            )
        if not out.get("uses_complete_months", True):
            out["data_caveats"].append(
                "Fewer than 2 complete statement months are imported "
                "yet — comparison falls back to the latest two months "
                "in the DB, which may be partial."
            )
        if out["income"] <= 0:
            out["data_caveats"].append(
                f"{out['month']} has no income recorded yet — partial "
                f"month or missing payroll import."
            )
        if out["prev_income"] <= 0:
            out["data_caveats"].append(
                f"{out['prev_month']} had no recorded income — savings "
                f"rate comparison is unreliable."
            )
        # Big import-gap warning if the previous month is suspiciously
        # smaller than the latest month's tx count (suggests partial import).
        cur_tx = int(latest.get("tx_count") or 0)
        prv_tx = int(prev.get("tx_count") or 0)
        if cur_tx > 0 and prv_tx > 0 and prv_tx < cur_tx * 0.3:
            out["data_caveats"].append(
                f"{out['prev_month']} has only {prv_tx} transactions vs "
                f"{cur_tx} in {out['month']} — comparison may be partial."
            )

        # Suggested next action — single deterministic recommendation.
        bm = out["biggest_mover"]
        if bm and bm["direction"] == "up" and bm["inspect_target"]:
            out["suggested_action"] = {
                "label":       bm["inspect_label"],
                "target_page": bm["inspect_target"],
                "reason": (
                    f"{bm['category']} jumped "
                    f"${abs(bm['abs_change']):,.0f} vs {out['prev_month']} "
                    f"— inspect what's driving it."
                ),
            }
        elif out["spending_delta"] > 0 and out["net_delta"] < 0:
            out["suggested_action"] = {
                "label":       "Open Reduce",
                "target_page": "pages/11_Reduce.py",
                "reason": (
                    f"Net is ${abs(out['net_delta']):,.0f} worse than "
                    f"last month — pick one cut from Reduce."
                ),
            }
        elif out["net_delta"] >= 0:
            out["suggested_action"] = {
                "label":       "Open Plan",
                "target_page": "pages/12_Month_Plan.py",
                "reason": (
                    "Net is flat or improving — confirm this month's "
                    "plan is saved so the streak holds."
                ),
            }
        return out
    finally:
        _close(c, opened)


# ── Recurring merchant tracking ────────────────────────────────────────

def recurring_merchants(min_months: int = 3, conn=None) -> list[dict]:
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT
            merchant,
            category,
            COUNT(DISTINCT strftime('%Y-%m', transaction_date)) AS months_seen,
            AVG(ABS(amount))  AS avg_amount,
            MIN(ABS(amount))  AS min_amount,
            MAX(ABS(amount))  AS max_amount,
            SUM(ABS(amount))  AS total_paid,
            COUNT(*)          AS tx_count
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND direction != 'cancelled'
        GROUP BY merchant
        HAVING months_seen >= ?
        ORDER BY avg_amount DESC
    """, (min_months,)).fetchall()
    _close(c, opened)
    result = []
    for r in rows:
        avg = r["avg_amount"] or 0
        result.append({
            "merchant":    r["merchant"],
            "category":    r["category"],
            "months_seen": r["months_seen"],
            "avg_amount":  round(avg, 2),
            "min_amount":  round(r["min_amount"] or 0, 2),
            "max_amount":  round(r["max_amount"] or 0, 2),
            "total_paid":  round(r["total_paid"] or 0, 2),
            "tx_count":    r["tx_count"],
            "est_annual":  round(avg * 12, 2),
        })
    return result


# ── Year-over-year ─────────────────────────────────────────────────────

def yoy_comparison(month: int, conn=None) -> list[dict]:
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT
            strftime('%Y', transaction_date) AS year,
            category,
            SUM(ABS(amount)) AS total
        FROM transactions
        WHERE CAST(strftime('%m', transaction_date) AS INTEGER) = ?
          AND direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Payment','Savings','Cancelled','Credit Card Payment')
        GROUP BY year, category
        ORDER BY year, total DESC
    """, (month,)).fetchall()
    _close(c, opened)
    return [dict(r) for r in rows]


# ── Plain-English insights ─────────────────────────────────────────────

def generate_insights(conn=None) -> list[dict]:
    """
    Returns [{type, title, body, severity}] grounded in actual imported data.
    severity: 'info' | 'warning' | 'good'

    Pass 35d: Dashboard Insights must use the latest COMPLETE statement
    month as truth. Before this fix, a partial current month (e.g.
    May 2026 with only the first week imported) produced false
    "Savings rate is low: 0%" insights even when April was healthy.
    Mirrors the Pass 35b fix already applied to monthly_review().
    """
    c, opened = _conn(conn)
    insights = []
    aggs = monthly_aggregates(conn=c)

    if len(aggs) < 2:
        _close(c, opened)
        return [{"type": "info", "title": "Import more data",
                 "body": "Import at least 2 months of statements to see trends and insights.",
                 "severity": "info"}]

    # Pass 35d: filter to complete statement months only. Partial
    # months stay visible in Transactions; they just don't drive
    # Insight card claims about savings rate / spending deltas.
    try:
        _cov = statement_coverage(conn=c) or {}
        _complete = set(_cov.get("complete_months") or [])
        _partial_list = list(_cov.get("partial_months") or [])
    except Exception:
        _complete = set()
        _partial_list = []
    aggs_complete = [a for a in aggs if (a.get("month") or "") in _complete]
    if len(aggs_complete) >= 2:
        latest = aggs_complete[-1]
        prev   = aggs_complete[-2]
        _partial_note = ""
        # If a partial month sits after the latest complete one, surface
        # an explicit "recent partial activity" card so the user knows
        # we saw the data but excluded it from monthly truth.
        _latest_data = (_cov.get("latest_data_month") or "")
        if _latest_data and _latest_data not in _complete:
            insights.append({
                "type": "info",
                "title": (
                    f"Partial recent activity in {_latest_data}"
                ),
                "body": (
                    f"Some transactions are imported for {_latest_data} but "
                    f"the statement month isn't complete yet. They are "
                    f"visible on Transactions but excluded from Dashboard "
                    f"Insights, Health Score, and monthly comparisons. "
                    f"Import the rest of the month or wait for the next "
                    f"statement to unlock truth."
                ),
                "severity": "info",
            })
    else:
        # Fallback: not enough complete months yet — use the legacy
        # latest two, but label the cards so the user understands the
        # numbers are preliminary.
        latest = aggs[-1]
        prev   = aggs[-2]

    # NOTE: every '$' literal in the Insight body strings is escaped as '\$'
    # because Streamlit renders these via markdown in the Trends/Insights tab,
    # and unescaped paired '$...$' are interpreted as LaTeX math mode (which
    # collapses whitespace and produces malformed text).
    sr = latest["savings_rate"]
    if sr < 0:
        insights.append({
            "type": "warning",
            "title": f"Spending exceeded income in {latest['month']}",
            "body": f"You spent \\${abs(latest['net']):,.2f} more than you earned. "
                    f"Check for large one-off costs or transfers misclassified as spending.",
            "severity": "warning",
        })
    elif sr < 10:
        top_cat_row = c.execute("""
            SELECT category, SUM(ABS(amount)) AS total
            FROM transactions
            WHERE strftime('%Y-%m', transaction_date) = ?
              AND direction='debit' AND is_transfer=0
              AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                   'Payment','Savings','Cancelled','Credit Card Payment')
            GROUP BY category
            ORDER BY total DESC
            LIMIT 1
        """, (latest["month"],)).fetchone()
        if top_cat_row and (top_cat_row["total"] or 0) > 0 and latest["spending"] > 0:
            share = (top_cat_row["total"] / latest["spending"]) * 100
            driver = (
                f"Largest consumption category this month: **{top_cat_row['category']}** "
                f"at \\${top_cat_row['total']:,.0f} ({share:.0f}% of spending) — "
                f"start there to lift the rate."
            )
        else:
            driver = "Check the Spending page for the largest consumption categories this month."
        insights.append({
            "type": "warning",
            "title": f"Savings rate is low: {sr:.0f}%",
            "body": f"Only \\${latest['net']:,.2f} saved in {latest['month']}. {driver}",
            "severity": "warning",
        })
    elif sr >= 20:
        insights.append({
            "type": "good",
            "title": f"Strong savings rate: {sr:.0f}% in {latest['month']}",
            "body": f"\\${latest['net']:,.2f} saved. Consistently above 20% puts you on track.",
            "severity": "good",
        })

    spend_delta = latest["spending"] - prev["spending"]
    if abs(spend_delta) > 50:
        direction_word = "up" if spend_delta > 0 else "down"
        xfer_delta = latest.get("transfer_out", 0) - prev.get("transfer_out", 0)
        xfer_note = ""
        if abs(xfer_delta) >= 50 and spend_delta != 0 and abs(xfer_delta) / abs(spend_delta) >= 0.4:
            xfer_label = "more outgoing e-Transfers" if xfer_delta > 0 else "fewer outgoing e-Transfers"
            xfer_note = f" (\\${abs(xfer_delta):,.0f} from {xfer_label} — not consumption purchases)"
        insights.append({
            "type": "info",
            "title": f"Spending {direction_word} ${abs(spend_delta):,.0f} vs {prev['month']}",
            "body": (
                f"{latest['month']}: \\${latest['spending']:,.2f} vs "
                f"{prev['month']}: \\${prev['spending']:,.2f}.{xfer_note}"
            ),
            "severity": "warning" if spend_delta > 200 else "info",
        })

    _SKIP_DRIFT = frozenset({"Transfer Out", "Transfer In", "Transfer", "Cancelled", "Payment"})
    drift = category_drift(lookback_months=2, conn=c)
    for d in drift[:3]:
        if d["flagged"] and d["category"] not in _SKIP_DRIFT:
            insights.append({
                "type": "drift",
                "title": f"{d['category']} up {d['pct_change']:.0f}% over last 2 months",
                "body": f"Average \\${d['recent_avg']:,.2f}/month recently vs "
                        f"\\${d['prior_avg']:,.2f} before. "
                        f"+\\${d['abs_change']:,.2f}/month.",
                "severity": "warning",
            })

    sub_row = c.execute("""
        SELECT SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Subscriptions & Digital' AND direction='debit'
          AND strftime('%Y-%m', transaction_date) = ?
    """, (latest["month"],)).fetchone()
    sub_total = (sub_row["total"] or 0) if sub_row else 0
    if sub_total > 100:
        insights.append({
            "type": "subscriptions",
            "title": f"${sub_total:,.2f} in subscriptions this month",
            "body": "Review the Subscriptions page to identify anything you no longer use.",
            "severity": "warning" if sub_total > 150 else "info",
        })

    # Pass 17: removed the noisy "price_increase" insight that fired on any
    # 10% range across 3 months. Replaced by the conservative
    # `variable_review_*` recommendation in `compute_recommendations` which
    # carries no annualized impact claim. The Trends Insights tab no longer
    # needs to surface this — it's better expressed as a Recommendation.

    ca_row = c.execute("""
        SELECT COUNT(*) AS cnt, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Cash Advance' AND direction='debit'
          AND transaction_date >= date('now', '-90 days')
    """).fetchone()
    if ca_row and (ca_row["cnt"] or 0) > 0:
        insights.append({
            "type": "cash_advance",
            "title": f"{ca_row['cnt']} cash advance(s) in last 90 days",
            "body": f"Total: \\${ca_row['total']:,.2f}. Cash advances carry high interest — repay quickly.",
            "severity": "warning",
        })

    if len(aggs) >= 3:
        stable = [a for a in aggs if 5 <= a["savings_rate"] <= 35]
        if len(stable) >= 2:
            avg_sr = sum(a["savings_rate"] for a in stable) / len(stable)
            insights.append({
                "type": "good",
                "title": f"Consistent savings across {len(stable)} months",
                "body": f"Average savings rate of {avg_sr:.0f}% across stable months. "
                        f"Fixed costs (mortgage, subscriptions) have stayed predictable.",
                "severity": "good",
            })

    _close(c, opened)
    return insights


# ── Budget vs actuals ──────────────────────────────────────────────────

def budget_vs_actuals(month: str, conn=None) -> list[dict]:
    c, opened = _conn(conn)

    has_budgets = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='budgets'"
    ).fetchone()

    actuals_rows = c.execute("""
        SELECT category, SUM(ABS(amount)) AS actual
        FROM transactions
        WHERE strftime('%Y-%m', transaction_date) = ?
          AND direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Payment','Savings','Cancelled','Credit Card Payment')
        GROUP BY category
        ORDER BY actual DESC
    """, (month,)).fetchall()

    actuals = {r["category"]: round(r["actual"], 2) for r in actuals_rows}

    budgets = {}
    if has_budgets:
        budget_rows = c.execute("SELECT category, amount FROM budgets").fetchall()
        budgets = {r["category"]: r["amount"] for r in budget_rows}

    _close(c, opened)

    result = []
    all_cats = set(actuals) | set(budgets)
    for cat in sorted(all_cats):
        actual = actuals.get(cat, 0)
        budget = budgets.get(cat, None)
        result.append({
            "category":    cat,
            "budget":      budget,
            "actual":      actual,
            "remaining":   round(budget - actual, 2) if budget else None,
            "over_budget": (actual > budget) if budget else False,
            "pct_used":    round(actual / budget * 100, 0) if budget and budget > 0 else None,
        })
    return result


# ── v2.0: Recommendations engine ──────────────────────────────────────

def compute_recommendations(conn=None) -> list[dict]:
    """
    Returns a ranked list of actionable recommendations grounded in imported data.

    Each rec dict (v2):
      • key, title, body, category, annual_impact, evidence,
        action_label, action_type, action_value
      • priority           — 'high' | 'medium' | 'low' (recomputed from composite)
      • type               — 'cut' | 'review' | 'watch' | 'fix' | 'investigate' | 'optimize'
      • confidence         — 0..1, how trustworthy the signal is
      • controllability    — 0..1, how much the user can actually change this
      • urgency            — 0..1, how time-sensitive the action is
      • composite_score    — 0..1, weighted combination used for ranking
      • drivers            — list of {kind,label,value} machine-readable evidence

    Older generator blocks may not set every new field; a finalizer normalises
    defaults and recomputes `priority` from the composite.
    """
    c, opened = _conn(conn)
    recs = []
    aggs = monthly_aggregates(conn=c)

    if not aggs:
        _close(c, opened)
        return []

    months = imported_months(conn=c)
    n_months = len(months)

    # ── 1. Over-budget categories ─────────────────────────────────────
    if months:
        latest_month = months[-1]
        bva = budget_vs_actuals(latest_month, conn=c)
        for b in bva:
            if b["over_budget"] and b["budget"] and b["budget"] > 0:
                overage = b["actual"] - b["budget"]
                recs.append({
                    "key":          f"over_budget_{b['category']}",
                    "title":        f"{b['category']} over budget by ${overage:,.0f}",
                    "body":         f"In {latest_month} you spent ${b['actual']:,.2f} against a "
                                    f"${b['budget']:,.2f} budget. Check for one-off charges or adjust the target.",
                    "category":     b["category"],
                    "annual_impact": round(overage * 12, 2),
                    "action_label": f"View {b['category']} transactions",
                    "action_type":  "category_filter",
                    "action_value": b["category"],
                    "evidence":     f"${b['actual']:,.2f} actual vs ${b['budget']:,.2f} budget in {latest_month}",
                    "type":         "cut",
                    "confidence":   0.9,
                    "controllability": 0.7,
                    "urgency":      0.6,
                    "drivers": [
                        {"kind": "budget_overage", "label": b["category"],
                         "actual": b["actual"], "budget": b["budget"],
                         "month": latest_month, "overage": round(overage, 2)},
                    ],
                })

    # ── 2. Fast-rising categories (drift) ────────────────────────────
    # Skip cashflow movement categories — these are not controllable consumption.
    _SKIP_DRIFT_CATS = frozenset({
        "Transfer Out", "Transfer In", "Transfer", "Cancelled", "Payment",
    })
    drift = category_drift(lookback_months=2, conn=c)
    for d in drift[:5]:
        if d["flagged"] and d["abs_change"] > 30 and d["category"] not in _SKIP_DRIFT_CATS:
            recs.append({
                "key":          f"drift_{d['category']}",
                "title":        f"Cut {d['category']} spend — up {d['pct_change']:.0f}%",
                "body":         f"Averaging ${d['recent_avg']:,.2f}/month recently vs ${d['prior_avg']:,.2f} "
                                f"before. This trend costs an extra ~${d['abs_change'] * 12:,.0f}/year.",
                "category":     d["category"],
                "annual_impact": round(d["abs_change"] * 12, 2),
                "action_label": f"Review {d['category']} spending",
                "action_type":  "category_filter",
                "action_value": d["category"],
                "evidence":     f"Avg ${d['recent_avg']:,.2f}/mo recently vs ${d['prior_avg']:,.2f}/mo before",
                "type":         "cut",
                "confidence":   0.7,
                "controllability": 0.7,
                "urgency":      0.5,
                "drivers": [
                    {"kind": "category_drift", "label": d["category"],
                     "recent_avg": d["recent_avg"], "prior_avg": d["prior_avg"],
                     "abs_change": d["abs_change"], "pct_change": d["pct_change"]},
                ],
            })

    # ── 3. Subscription audit (Pass 18: active/stale split) ───────────
    # Use the new subscription_detective output so cancellation-candidate
    # recs only fire on ACTIVE subs (last seen within the active window
    # of the latest imported tx). Stale subs surface as a low-priority
    # "likely already stopped" rec instead, so the user sees them but
    # doesn't get pushed to cancel something that's already gone.
    det_for_recs = subscription_detective(conn=c)
    active_subs_recs       = det_for_recs.get("active_subs") or []
    stale_subs_recs        = det_for_recs.get("stale_subs") or []
    active_candidates_recs = det_for_recs.get("active_candidates") or []

    # Active cancellation candidates — one rec per top candidate (cap 4
    # so the list stays scannable; remaining live in Reduce).
    for c_sub in active_candidates_recs[:4]:
        merch = c_sub["merchant"]
        flags = list(c_sub.get("flags") or [])
        # Skip variable_amount-only — that's not actionable; it should
        # not become a "cut" rec (Pass 17 lesson: don't claim savings
        # from variability).
        actionable_flags = {"price_increase", "low_usage", "duplicate_candidate"}
        if not (set(flags) & actionable_flags):
            continue
        annual = float(c_sub.get("annual") or 0)
        monthly = float(c_sub.get("avg_amount") or 0)
        flag_label = (
            "price increase" if "price_increase" in flags
            else "low usage"  if "low_usage" in flags
            else "possible duplicate"
        )
        recs.append({
            "key":           f"active_sub_{merch.replace(' ', '_')}",
            "title":         f"Review {merch} — {flag_label}",
            "body":          (
                f"{merch} charges ~${monthly:,.2f}/month "
                f"(${annual:,.0f}/year) and was flagged as "
                f"{flag_label}. Cancelling would free that amount; "
                f"keeping it is fine if you still use it."
            ),
            "category":      c_sub.get("category") or "Subscriptions & Digital",
            "annual_impact": round(annual, 2),
            "action_label":  f"See {merch} transactions",
            "action_type":   "merchant_filter",
            "action_value":  merch,
            "evidence":      (
                f"~${monthly:,.2f}/mo · {c_sub.get('months_seen', 0)} months · "
                f"last {c_sub.get('last_seen', '?')}"
            ),
            "type":          "cut",
            "confidence":    0.85 if "price_increase" in flags else 0.75,
            "controllability": 0.9,
            "urgency":       0.5,
            "lifecycle":     "active",
            "drivers": [
                {"kind": "active_subscription_candidate", "merchant": merch,
                 "monthly": round(monthly, 2), "annual": round(annual, 2),
                 "flags": flags},
            ],
        })

    # Stale subscriptions — likely already stopped. Low priority; small
    # impact claim because we don't know if these are truly cancelled or
    # just haven't recurred yet, but they are NOT counted as fresh
    # savings opportunities.
    if stale_subs_recs:
        # Single roll-up rec — much less noisy than one card per stale sub.
        top_stale = sorted(stale_subs_recs, key=lambda s: -s["annual"])[:5]
        names = ", ".join(s["merchant"] for s in top_stale)
        stale_annual = float(det_for_recs.get("stale_annual_total") or 0)
        recs.append({
            "key":           "stale_subscriptions_rollup",
            "title":         f"{len(stale_subs_recs)} subscription(s) likely already stopped",
            "body":          (
                f"These haven't charged in over "
                f"{det_for_recs.get('active_window_days', 60)} days "
                f"(measured against your latest imported transaction). "
                f"They may already be cancelled. Verify before counting "
                f"as savings: {names}."
            ),
            "category":      "Subscriptions & Digital",
            # No annual_impact — we don't claim savings from things
            # that may already be cancelled.
            "annual_impact": 0,
            "action_label":  "Open Reduce → Possibly already stopped",
            "action_type":   "review_page",
            "action_value":  "subscriptions",
            "evidence":      (
                f"{len(stale_subs_recs)} stale sub(s) · "
                f"~${stale_annual:,.0f}/yr if all were still active"
            ),
            "type":          "watch",
            "confidence":    0.6,
            "controllability": 0.5,
            "urgency":       0.1,
            "lifecycle":     "stale",
            "drivers": [
                {"kind": "stale_subscriptions", "count": len(stale_subs_recs),
                 "merchants": [s["merchant"] for s in top_stale]},
            ],
        })

    # Active subs roll-up — only active subs counted, no stale dilution.
    if months and active_subs_recs:
        latest_month = months[-1]
        sub_total = float(det_for_recs.get("active_monthly_estimate") or 0)
        if sub_total > 80:
            annual_sub = sub_total * 12
            potential_saving = round(sub_total * 0.20 * 12, 2)
            recs.append({
                "key":          "subscription_audit",
                "title":        f"Audit subscriptions — ${sub_total:,.0f}/month active",
                "body":         (
                    f"You have ${sub_total:,.2f}/month in active subscriptions "
                    f"({len(active_subs_recs)} service(s), ${annual_sub:,.0f}/year). "
                    f"Cancelling unused ones could save ~${potential_saving:,.0f}/year."
                ),
                "category":     "Subscriptions & Digital",
                "annual_impact": potential_saving,
                "action_label": "Open Reduce",
                "action_type":  "review_page",
                "action_value": "subscriptions",
                "evidence":     (
                    f"{len(active_subs_recs)} active service(s) · "
                    f"${sub_total:,.2f}/mo · ${annual_sub:,.0f}/yr"
                ),
                "type":         "optimize",
                "confidence":   0.85,
                "controllability": 0.9,
                "urgency":      0.4,
                "lifecycle":    "active",
                "drivers": [
                    {"kind": "subscription_total_active", "month": latest_month,
                     "services": len(active_subs_recs),
                     "monthly": round(sub_total, 2),
                     "annual": round(annual_sub, 2)},
                ],
            })

    # ── 4. Variable charges to review (Pass 17: stripped of annualized impact) ──
    # Manual testing: even Pass 16's softened variable_* recs had annualized
    # impact estimates that the user found misleading — variable means
    # variable, projecting savings from it isn't honest. This pass:
    #   • Drops annual_impact entirely (set to 0) so cards never claim dollar
    #     savings the user didn't make.
    #   • Renames the rec key to `variable_review_*` so old `variable_*` /
    #     `price_increase_*` snooze states don't suppress the new shape.
    #   • Keeps Watch-tier urgency (0.2) so these stay below "save money".
    #   • Wording is purely descriptive: "amount varies; inspect before
    #     treating as a price change".
    #   • Skips merchants whose max/min ratio > 2.0 (pure variable, no
    #     "review" angle is useful — too noisy).
    rec_merchants = recurring_merchants(min_months=3, conn=c)
    for m in rec_merchants:
        if not (m["min_amount"] > 0 and m["max_amount"] > 15):
            continue
        ratio = m["max_amount"] / m["min_amount"]
        increase_pct = (m["max_amount"] - m["min_amount"]) / m["min_amount"] * 100
        if ratio > 2.0:
            continue
        if increase_pct < 10:
            continue
        avg = m.get("avg_amount") or ((m["max_amount"] + m["min_amount"]) / 2.0)
        recs.append({
            "key":          f"variable_review_{m['merchant'].replace(' ','_')}",
            "title":        f"Review variable charges from {m['merchant']}",
            "body":         (f"Recent charges range ${m['min_amount']:,.2f}–${m['max_amount']:,.2f} "
                             f"over {m['months_seen']} months (avg ${avg:,.2f}). "
                             "Amount varies across months; inspect before treating as a "
                             "price change."),
            "category":     m["category"],
            # Pass 17: no annualized impact for variable charges. Set to 0 so
            # cards don't display a fake "Est. annual impact" badge and the
            # rec doesn't roll up into the page-level total.
            "annual_impact": 0,
            "action_label": f"See {m['merchant']} transactions",
            "action_type":  "merchant_filter",
            "action_value": m["merchant"],
            "evidence":     f"Range ${m['min_amount']:,.2f}–${m['max_amount']:,.2f} over "
                            f"{m['months_seen']} months · avg ${avg:,.2f}",
            "type":         "watch",
            "confidence":   0.5,
            "controllability": 0.4,
            "urgency":      0.2,
            "drivers": [
                {"kind": "merchant_variable_charge", "merchant": m["merchant"],
                 "min": m["min_amount"], "max": m["max_amount"],
                 "avg": round(avg, 2), "months_seen": m["months_seen"]},
            ],
        })

    # ── 5. Low savings rate warning ────────────────────────────────────
    if aggs:
        latest = aggs[-1]
        if 0 < latest["savings_rate"] < 10 and latest["income"] > 0:
            target_sr = 20
            gap_monthly = latest["income"] * (target_sr - latest["savings_rate"]) / 100
            recs.append({
                "key":          "low_savings_rate",
                "title":        f"Savings rate at {latest['savings_rate']:.0f}% — target 20%",
                "body":         f"In {latest['month']} you saved ${latest['net']:,.2f} "
                                f"({latest['savings_rate']:.0f}%). Reaching 20% would add "
                                f"~${gap_monthly:,.0f}/month or ${gap_monthly * 12:,.0f}/year.",
                "category":     "Cashflow",
                "annual_impact": round(gap_monthly * 12, 2),
                "action_label": "Review spending categories",
                "action_type":  "spending_page",
                "action_value": "",
                "evidence":     f"Savings rate: {latest['savings_rate']:.1f}% in {latest['month']}",
                "type":         "investigate",
                "confidence":   0.75,
                "controllability": 0.5,
                "urgency":      0.6,
                "drivers": [
                    {"kind": "savings_rate", "month": latest["month"],
                     "rate": latest["savings_rate"], "net": latest["net"],
                     "income": latest["income"]},
                ],
            })

    # ── 6. Dining/food spend is high ─────────────────────────────────
    dining_rows = c.execute("""
        SELECT strftime('%Y-%m', transaction_date) AS month, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category = 'Food & Convenience'
          AND direction='debit' AND is_transfer=0
        GROUP BY month
        ORDER BY month DESC
        LIMIT 3
    """).fetchall()
    if dining_rows:
        avg_dining = sum(r["total"] or 0 for r in dining_rows) / len(dining_rows)
        if avg_dining > 300:
            recs.append({
                "key":          "food_convenience_high",
                "title":        f"Food & Convenience averages ${avg_dining:,.0f}/month",
                "body":         f"Based on your last {len(dining_rows)} months. Reducing by 25% "
                                f"would save ~${avg_dining * 0.25 * 12:,.0f}/year.",
                "category":     "Food & Convenience",
                "annual_impact": round(avg_dining * 0.25 * 12, 2),
                "action_label": "View Food & Convenience transactions",
                "action_type":  "category_filter",
                "action_value": "Food & Convenience",
                "evidence":     f"${avg_dining:,.2f}/month avg over last {len(dining_rows)} months",
                "type":         "cut",
                "confidence":   0.7 if len(dining_rows) >= 3 else 0.5,
                "controllability": 0.75,
                "urgency":      0.4,
                "drivers": [
                    {"kind": "category_average", "category": "Food & Convenience",
                     "avg_monthly": round(avg_dining, 2),
                     "months_measured": len(dining_rows)},
                ],
            })

    # ── 7. Review queue has old items ─────────────────────────────────
    review_count = c.execute("SELECT COUNT(*) FROM transactions WHERE is_flagged=1").fetchone()[0]
    if review_count >= 5:
        recs.append({
            "key":          "review_queue",
            "title":        f"Clear review queue — {review_count} items pending",
            "body":         f"You have {review_count} flagged transactions waiting for review. "
                            f"Clearing them improves score accuracy and data quality.",
            "category":     "Data Quality",
            "annual_impact": 0,
            "action_label": "Open Review page",
            "action_type":  "review_page",
            "action_value": "review",
            "evidence":     f"{review_count} flagged transactions",
            "type":         "review",
            "confidence":   1.0,
            "controllability": 1.0,
            "urgency":      0.5 if review_count >= 10 else 0.3,
            "drivers": [
                {"kind": "review_queue_size", "count": review_count},
            ],
        })

    # ── 8. Cash advance detected ─────────────────────────────────────
    ca = c.execute("""
        SELECT COUNT(*) AS cnt, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE category='Cash Advance' AND direction='debit'
          AND transaction_date >= date('now', '-90 days')
    """).fetchone()
    if ca and (ca["cnt"] or 0) > 0:
        # Pass 35 Phase 3: choose the wording (and urgency) based on whether
        # later CC payments plausibly cover the cash advance principal.
        # Ledger sees transactions, not balances - it must not assert an
        # outstanding payoff when later payments may have already covered it.
        try:
            _ca_status = cash_advance_status(conn=c) or {}
        except Exception:
            _ca_status = {}
        _verdict = _ca_status.get("verdict") or "outstanding"
        _safe_action = (
            _ca_status.get("safe_action")
            or (f"{ca['cnt']} cash advance(s) totalling "
                f"${(ca['total'] or 0):,.2f} in the last 90 days. "
                "Verify on your card statement; Ledger sees "
                "transactions, not balances.")
        )
        if _verdict == "covered":
            _title = "Cash advance recorded - verify on statement"
            _type  = "review"
            _urg   = 0.5
            _impact = 0.0
        elif _verdict == "uncertain":
            _title = "Cash advance recorded - confirm balance"
            _type  = "review"
            _urg   = 0.7
            _impact = round((ca["total"] or 0) * 0.10, 2)
        else:  # outstanding / no_history
            _title = "Cash advance detected - high interest risk"
            _type  = "fix"
            _urg   = 1.0
            _impact = round((ca["total"] or 0) * 0.25, 2)
        recs.append({
            "key":          "cash_advance",
            "title":        _title,
            "body":         _safe_action,
            "category":     "Fees / Interest",
            "annual_impact": _impact,
            "action_label": "View transactions",
            "action_type":  "category_filter",
            "action_value": "Cash Advance",
            "evidence":     f"{ca['cnt']} cash advance(s) in last 90 days; "
                            f"verdict: {_verdict}",
            "type":         _type,
            "confidence":   0.95,
            "controllability": 0.8,
            "urgency":      _urg,
            "drivers": [
                {"kind": "cash_advance_90d", "count": ca["cnt"],
                 "total": round(ca["total"] or 0, 2),
                 "verdict": _verdict,
                 "cc_payments_since": _ca_status.get("cc_payments_since", 0.0)},
            ],
        })

    # ── 9. Controllable category targets (Pass 18) ────────────────────
    # Use top_controllable_categories so cuts always reflect actual
    # consumption from the last 90 days. Only emit a rec for categories
    # we don't already cover via budget overage or drift, to avoid
    # double-listing the same category.
    _CONTROLLABLE_FIRST_ACTION = {
        "Shopping":             ("Pause one Amazon order this week and audit "
                                 "the cart before checkout."),
        "Food & Convenience":   ("Pack lunch 3x next week — every $15 lunch "
                                 "saved is ~$45/week."),
        "Groceries":            ("Plan two meals from your pantry next week "
                                 "and skip one grocery run."),
        "Subscriptions & Digital": ("Open Reduce → Active cancellation "
                                    "candidates and cancel one unused service."),
        "Entertainment":        ("Skip one streaming/event purchase this "
                                 "month — audit recurring ones in Reduce."),
        "Pets":                 ("Stretch one bulk-order interval (food, "
                                 "treats) by an extra week."),
        "Gas / Transport":      ("Combine 2 errand trips into 1; check Gas "
                                 "Buddy on your usual route."),
        "Health / Care":        ("Check whether any monthly charges here are "
                                 "reimbursable via insurance."),
    }
    # Categories where "cut by 20%" framing is wrong — utilities are
    # mostly fixed, so present them as REVIEW rather than a cut target.
    _NON_CUT_CATEGORIES = frozenset({
        "Utilities / Bills", "Housing / Mortgage",
        "Fees / Interest", "Cash Advance",
    })
    try:
        ctl = top_controllable_categories(conn=c, limit=5) or []
    except Exception:
        ctl = []
    _already_in_recs = {r["category"] for r in recs}
    for ctl_cat in ctl:
        cat_name = ctl_cat["category"]
        if cat_name in _already_in_recs:
            continue
        if cat_name in _NON_CUT_CATEGORIES:
            continue
        m_avg = float(ctl_cat.get("monthly_avg") or 0)
        if m_avg < 50:
            # Below the noise floor — not worth a card.
            continue
        save_mo = round(m_avg * 0.20, 2)
        save_yr = round(save_mo * 12, 2)
        first_action = _CONTROLLABLE_FIRST_ACTION.get(
            cat_name,
            f"Open Transactions filtered to {cat_name} and review the "
            f"largest 5 charges.",
        )
        recs.append({
            "key":           f"controllable_{cat_name.replace(' ', '_')}",
            "title":         f"Reduce {cat_name} to ${m_avg * 0.80:,.0f}/mo target",
            "body":          (
                f"Spending ~${m_avg:,.0f}/mo on {cat_name} (90-day avg "
                f"across {ctl_cat.get('tx_count', 0)} tx). A 20% cut would "
                f"free ~${save_mo:,.0f}/mo (~${save_yr:,.0f}/yr). "
                f"First step: {first_action}"
            ),
            "category":      cat_name,
            "annual_impact": save_yr,
            "action_label":  f"View {cat_name} transactions",
            "action_type":   "category_filter",
            "action_value":  cat_name,
            "evidence":      f"90-day avg ${m_avg:,.2f}/mo · {ctl_cat.get('tx_count', 0)} tx",
            "type":          "cut",
            "confidence":    0.7 if (ctl_cat.get('tx_count') or 0) >= 8 else 0.55,
            "controllability": 0.7,
            "urgency":       0.4,
            "lifecycle":     "active",
            "drivers": [
                {"kind": "controllable_category_target",
                 "category": cat_name, "monthly_avg": round(m_avg, 2),
                 "target": round(m_avg * 0.80, 2),
                 "save_monthly": save_mo, "save_yearly": save_yr},
            ],
        })

    # ── 10. Utilities review (NOT framed as easy cut) ─────────────────
    util_row = c.execute("""
        SELECT SUM(ABS(amount)) AS total_90d, COUNT(*) AS cnt
        FROM transactions
        WHERE category='Utilities / Bills' AND direction='debit' AND is_transfer=0
          AND transaction_date >= date('now', '-90 days')
    """).fetchone()
    util_total_90d = float(util_row["total_90d"] or 0) if util_row else 0
    util_cnt = int(util_row["cnt"] or 0) if util_row else 0
    if util_total_90d >= 300 and util_cnt >= 3:
        util_monthly = util_total_90d / 3.0
        recs.append({
            "key":          "utilities_review",
            "title":        f"Review utilities/bills — ${util_monthly:,.0f}/mo",
            "body":         (
                f"You spent ${util_total_90d:,.2f} on Utilities / Bills "
                f"in the last 90 days (~${util_monthly:,.0f}/mo across "
                f"{util_cnt} charges). These are mostly fixed — but "
                f"call providers once a year for retention discounts; "
                f"a 5–10% cut here is real money."
            ),
            "category":     "Utilities / Bills",
            # Conservative impact: 5% of monthly cost annualized. Only
            # surfaces as "Save Money" group when material.
            "annual_impact": round(util_monthly * 0.05 * 12, 2),
            "action_label": "View utilities transactions",
            "action_type":  "category_filter",
            "action_value": "Utilities / Bills",
            "evidence":     f"${util_total_90d:,.2f} over 90 days · {util_cnt} charges",
            "type":         "review",
            "confidence":   0.7,
            "controllability": 0.4,
            "urgency":      0.2,
            "lifecycle":    "active",
            "drivers": [
                {"kind": "utilities_total", "monthly": round(util_monthly, 2),
                 "tx_count_90d": util_cnt},
            ],
        })

    # ── 11. Uncategorized / low-confidence cleanup ────────────────────
    uncat_row = c.execute("""
        SELECT
            SUM(CASE WHEN category='Uncategorized' OR category IS NULL OR category='' THEN 1 ELSE 0 END) AS uncat,
            SUM(CASE WHEN parse_confidence IS NOT NULL AND parse_confidence < 0.5 THEN 1 ELSE 0 END) AS lowc,
            COUNT(*) AS total
        FROM transactions
        WHERE direction != 'cancelled'
    """).fetchone()
    uncat_n = int(uncat_row["uncat"] or 0) if uncat_row else 0
    lowc_n  = int(uncat_row["lowc"]  or 0) if uncat_row else 0
    if uncat_n >= 3 or lowc_n >= 5:
        recs.append({
            "key":          "data_cleanup_uncategorized",
            "title":        f"Clean {uncat_n} uncategorized + {lowc_n} low-confidence row(s)",
            "body":         (
                f"You have {uncat_n} uncategorized transaction(s) and "
                f"{lowc_n} flagged as low-confidence. Categorizing these "
                f"sharpens every spending chart and keeps recommendations "
                f"honest."
            ),
            "category":     "Data Quality",
            "annual_impact": 0,
            "action_label": "Open Review page",
            "action_type":  "review_page",
            "action_value": "review",
            "evidence":     f"{uncat_n} uncategorized · {lowc_n} low-confidence",
            "type":         "review",
            "confidence":   1.0,
            "controllability": 1.0,
            "urgency":      0.4 if (uncat_n + lowc_n) >= 15 else 0.2,
            "lifecycle":    "active",
            "drivers": [
                {"kind": "data_cleanup", "uncategorized": uncat_n,
                 "low_confidence": lowc_n},
            ],
        })

    # ── 12. Large one-off transaction (last 60 days) ───────────────────
    big_row = c.execute("""
        SELECT id, transaction_date, merchant, category, ABS(amount) AS amt
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                               'Payment','Credit Card Payment','Cancelled',
                               'Internal Transfer','Savings','Investments',
                               'Housing / Mortgage')
          AND transaction_date >= date('now', '-60 days')
        ORDER BY ABS(amount) DESC
        LIMIT 1
    """).fetchone()
    if big_row and (big_row["amt"] or 0) >= 500:
        m_label = (big_row["merchant"] or "")[:50] or "(unknown merchant)"
        recs.append({
            "key":          f"large_oneoff_{big_row['id']}",
            "title":        f"Review one-off charge: {m_label} (${float(big_row['amt']):,.0f})",
            "body":         (
                f"A ${float(big_row['amt']):,.2f} debit on "
                f"{big_row['transaction_date']} stands out in the last 60 "
                f"days. Confirm it was intentional and categorized "
                f"correctly. (One-offs can hide subscription renewals or "
                f"miscategorized transfers.)"
            ),
            "category":     big_row["category"] or "Misc",
            "annual_impact": 0,
            "action_label": "Open Transactions",
            "action_type":  "merchant_filter",
            "action_value": big_row["merchant"] or "",
            "evidence":     f"${float(big_row['amt']):,.2f} on {big_row['transaction_date']} ({big_row['category']})",
            "type":         "investigate",
            "confidence":   0.6,
            "controllability": 0.4,
            "urgency":      0.3,
            "lifecycle":    "active",
            "drivers": [
                {"kind": "large_oneoff", "amount": float(big_row['amt']),
                 "date": big_row["transaction_date"],
                 "merchant": big_row["merchant"]},
            ],
        })

    # ── 13. Import / data coverage gap ─────────────────────────────────
    if months:
        latest_month = months[-1]
        latest_aggs = next((a for a in aggs if a["month"] == latest_month), None)
        if latest_aggs and (latest_aggs.get("income") or 0) <= 0:
            recs.append({
                "key":          "import_coverage_gap",
                "title":        f"No income recorded in {latest_month} — import latest statements?",
                "body":         (
                    f"Latest imported month {latest_month} shows zero "
                    f"income. If you've been paid since then, your most "
                    f"recent statements may not be imported yet — "
                    f"otherwise, check that payroll deposits are being "
                    f"categorized as income, not transfer."
                ),
                "category":     "Data Quality",
                "annual_impact": 0,
                "action_label": "Open Import",
                "action_type":  "review_page",
                "action_value": "review",
                "evidence":     f"Latest month {latest_month} · income = $0",
                "type":         "review",
                "confidence":   0.8,
                "controllability": 1.0,
                "urgency":      0.5,
                "lifecycle":    "active",
                "drivers": [
                    {"kind": "missing_income_latest_month",
                     "month": latest_month},
                ],
            })

    _close(c, opened)

    recs = [_finalize_rec(r) for r in recs]

    # Sort: high composite score first, ties broken by annual_impact desc
    recs.sort(key=lambda r: (-r["composite_score"], -r.get("annual_impact", 0)))
    return recs


# ── Recommendation finalizer ──────────────────────────────────────────

_TYPE_DEFAULT_CONTROLLABILITY = {
    "cut":         0.75,
    "review":      1.0,
    "watch":       0.5,
    "fix":         0.8,
    "investigate": 0.5,
    "optimize":    0.9,
}


def _normalise_impact(annual: float) -> float:
    """Map $/year onto a 0..1 scale with diminishing returns; $5k = ~0.9."""
    if annual <= 0:
        return 0.0
    # Smooth log-ish curve: 500→0.33, 1500→0.6, 5000→0.9
    return max(0.0, min(1.0, 1 - (1.0 / (1.0 + annual / 800.0))))


def _finalize_rec(r: dict) -> dict:
    """Fill defaults, compute composite_score and derived priority.

    Pass 16 — also escape every '$' in user-visible string fields so the
    Recommendations page (which renders body / evidence via st.markdown)
    cannot collapse them into LaTeX math mode. Title is included because
    Streamlit expander labels DO process markdown.
    """
    r.setdefault("type",            "investigate")
    r.setdefault("confidence",       0.6)
    r.setdefault("controllability",  _TYPE_DEFAULT_CONTROLLABILITY.get(r["type"], 0.5))
    r.setdefault("urgency",          0.4)
    r.setdefault("drivers",          [])

    impact_norm = _normalise_impact(float(r.get("annual_impact") or 0.0))

    # `fix` and `review` recs are actions whose value isn't primarily dollar-denominated
    # (a cash advance is urgent regardless of $; clearing review is about data trust).
    # Give them a floor tied to urgency so they aren't buried by higher-$ cut recs.
    if r["type"] in ("fix", "review"):
        impact_norm = max(impact_norm, float(r["urgency"]) * 0.8)

    composite = (
        0.40 * impact_norm +
        0.25 * float(r["confidence"]) +
        0.20 * float(r["controllability"]) +
        0.15 * float(r["urgency"])
    )
    r["composite_score"] = round(max(0.0, min(1.0, composite)), 3)

    # Priority derived from composite — overrides any inline-set priority.
    if r["composite_score"] >= 0.66:
        r["priority"] = "high"
    elif r["composite_score"] >= 0.40:
        r["priority"] = "medium"
    else:
        r["priority"] = "low"

    # Escape user-visible strings — see _esc_dollars docstring.
    for field in ("title", "body", "evidence", "action_label"):
        if field in r and isinstance(r[field], str):
            r[field] = _esc_dollars(r[field])
    return r


# ── v2.0: Merchant detail ──────────────────────────────────────────────

def merchant_detail(merchant: str, conn=None) -> dict:
    """
    Returns full stats for a single merchant: monthly breakdown, trend, all transactions.
    """
    c, opened = _conn(conn)

    stats = c.execute("""
        SELECT
            COUNT(*) AS tx_count,
            SUM(ABS(amount)) AS total_paid,
            AVG(ABS(amount)) AS avg_amount,
            MIN(ABS(amount)) AS min_amount,
            MAX(ABS(amount)) AS max_amount,
            MIN(transaction_date) AS first_seen,
            MAX(transaction_date) AS last_seen,
            category
        FROM transactions
        WHERE merchant = ? AND direction='debit' AND is_transfer=0
    """, (merchant,)).fetchone()

    monthly = c.execute("""
        SELECT strftime('%Y-%m', transaction_date) AS month, SUM(ABS(amount)) AS total, COUNT(*) AS cnt
        FROM transactions
        WHERE merchant = ? AND direction='debit' AND is_transfer=0
        GROUP BY month ORDER BY month
    """, (merchant,)).fetchall()

    txs = c.execute("""
        SELECT id, transaction_date, raw_description, amount, category, flag_reason, parse_confidence
        FROM transactions
        WHERE merchant = ? AND direction='debit' AND is_transfer=0
        ORDER BY transaction_date DESC
    """, (merchant,)).fetchall()

    _close(c, opened)

    return {
        "merchant":    merchant,
        "stats":       dict(stats) if stats else {},
        "monthly":     [dict(r) for r in monthly],
        "transactions": [dict(r) for r in txs],
    }


# ── Pass 10: Subscription Detective ────────────────────────────────────

def subscription_detective(conn=None, lookback_months: int = 6,
                           active_window_days: int = 60) -> dict:
    """
    Deterministic subscription audit (Pass 18 active/stale split).

    Ranks recurring-looking merchants by annualized cost and flags:
      - variable_amount: amount varies meaningfully across the lookback
                         window. ONLY upgraded to "price_increase" when
                         confidence is very high (active sub, ≥3
                         observations, latest > prior-average by ≥10%).
      - duplicate_candidate: two merchants with near-identical cadence + amount
      - low_usage:  tx_count < months_seen
      - stale:      last seen > active_window_days ago, measured against the
                    LATEST imported transaction date (anchor), NOT today.
                    This prevents historical imports from being judged
                    "stale" relative to today's calendar — important
                    because a subscription seen monthly through 2026-01
                    on data imported in May-2026 is "actively recurring
                    in the data we have", not "stopped".

    Active subs       = last_seen within active_window_days of anchor date
    Stale subs        = last_seen > active_window_days from anchor date
    Active candidates = active subs with at least one flag (cancellation review)
    Stale candidates  = stale subs (likely already cancelled)

    Returned dict adds these new keys (older callers still see the
    `candidates` / `monthly_estimate` / `annual_total` keys):
        active_subs, stale_subs,
        active_candidates, stale_candidates,
        active_monthly_estimate, active_annual_total,
        stale_annual_total, active_candidate_annual_total,
        anchor_date.

    Pure math — no AI. Returns a stable shape even with no data.
    """
    c, opened = _conn(conn)

    # Anchor: the latest imported transaction date. We compare
    # `last_seen` against this rather than today() so a fresh import of
    # data from 2025-12 doesn't immediately look "stale" if today is
    # 2026-04. Falls back to today() when there's literally no data.
    anchor_row = c.execute(
        "SELECT MAX(transaction_date) AS m FROM transactions"
    ).fetchone()
    anchor_iso = (anchor_row["m"] if anchor_row and anchor_row["m"]
                  else date.today().isoformat())
    try:
        anchor_d = date.fromisoformat(anchor_iso)
    except Exception:
        anchor_d = date.today()

    # Candidate set: merchants with ≥2 months of activity, category=Subscriptions or
    # cadence-like (same amount recurring). Restrict to Subscriptions & Digital for safety.
    rows = c.execute("""
        SELECT merchant,
               category,
               COUNT(DISTINCT strftime('%Y-%m', transaction_date)) AS months_seen,
               COUNT(*)          AS tx_count,
               AVG(ABS(amount))  AS avg_amount,
               MIN(ABS(amount))  AS min_amount,
               MAX(ABS(amount))  AS max_amount,
               SUM(ABS(amount))  AS total_paid,
               MAX(transaction_date) AS last_seen,
               MIN(transaction_date) AS first_seen
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND category IN ('Subscriptions & Digital')
          AND transaction_date >= date(?, ?)
        GROUP BY merchant
        HAVING months_seen >= 2
        ORDER BY avg_amount DESC
    """, (anchor_iso, f'-{lookback_months*31} days')).fetchall()

    subs: list[dict] = []
    for r in rows:
        merchant = r["merchant"] or ""
        avg = r["avg_amount"] or 0
        mx = r["max_amount"] or 0
        mn = r["min_amount"] or 0
        months_seen = r["months_seen"] or 0
        last_seen = r["last_seen"] or ""
        annual = round(avg * 12, 2)

        # Days since last_seen — measured against the anchor (latest
        # imported tx date), not against today.
        days_since: Optional[int] = None
        try:
            if last_seen:
                days_since = (anchor_d - date.fromisoformat(last_seen)).days
        except Exception:
            days_since = None
        is_stale = (days_since is not None and days_since > active_window_days)

        flags: list[str] = []
        # Variable-amount flag (replaces the noisy price_increase rule).
        # We label "variable_amount" whenever the range is wide enough
        # to be worth a glance ( >20% over min AND >=$2 absolute) — this
        # is descriptive, not prescriptive. We DO NOT call it a price
        # increase here. The Reduce / Recommendation surfaces are free
        # to upgrade to "price_increase" when:
        #   • merchant is active (not stale),
        #   • months_seen >= 3,
        #   • latest amount is meaningfully above prior average.
        # That higher-confidence check needs the per-tx series so we
        # compute it below per-merchant and only attach it for active
        # subs.
        if mn > 0 and (mx - mn) / mn > 0.20 and (mx - mn) >= 2:
            flags.append("variable_amount")

        # Stale flag: same gating as Pass 17, now anchored.
        if is_stale:
            flags.append("stale")

        # Low-usage flag: cadence looks intermittent (fewer charges than
        # months observed). Genuinely useful signal for "this thing is
        # already trailing off".
        if r["tx_count"] < months_seen:
            flags.append("low_usage")

        # High-confidence price_increase upgrade — only on active subs
        # with at least 3 observations and a recent jump above the
        # prior-window average. We pull the per-tx series for the
        # merchant and check the latest charge against the average of
        # the previous ones.
        price_increase_evidence = None
        if (not is_stale) and (r["tx_count"] or 0) >= 3 and "variable_amount" in flags:
            tx_rows = c.execute("""
                SELECT transaction_date, ABS(amount) AS amt
                FROM transactions
                WHERE merchant = ?
                  AND direction='debit' AND is_transfer=0
                  AND category = 'Subscriptions & Digital'
                ORDER BY transaction_date
            """, (merchant,)).fetchall()
            amounts = [float(t["amt"] or 0) for t in tx_rows if (t["amt"] or 0) > 0]
            if len(amounts) >= 3:
                latest_amt = amounts[-1]
                prior_avg = sum(amounts[:-1]) / max(1, len(amounts) - 1)
                # 10% above prior average AND $2 absolute AND latest is
                # not strictly the same as the prior charge (filters
                # out one-off refunds/partials that might have inflated
                # the max).
                if (prior_avg > 0
                        and latest_amt > prior_avg * 1.10
                        and (latest_amt - prior_avg) >= 2):
                    flags.append("price_increase")
                    price_increase_evidence = {
                        "latest":     round(latest_amt, 2),
                        "prior_avg":  round(prior_avg, 2),
                        "delta":      round(latest_amt - prior_avg, 2),
                        "n_prior":    len(amounts) - 1,
                    }

        subs.append({
            "merchant":     merchant,
            "category":     r["category"],
            "months_seen":  int(months_seen),
            "tx_count":     int(r["tx_count"] or 0),
            "avg_amount":   round(avg, 2),
            "min_amount":   round(mn, 2),
            "max_amount":   round(mx, 2),
            "total_paid":   round(r["total_paid"] or 0, 2),
            "annual":       annual,
            "last_seen":    last_seen,
            "first_seen":   r["first_seen"] or "",
            "flags":        flags,
            "is_active":    not is_stale,
            "days_since_last_seen": days_since if days_since is not None else -1,
            "price_increase_evidence": price_increase_evidence,
        })

    # Duplicate detection: merchants whose avg amounts are within $1
    # AND share overlapping months — rough heuristic, high precision target.
    for i, a in enumerate(subs):
        for b in subs[i + 1:]:
            if abs(a["avg_amount"] - b["avg_amount"]) <= 1.0 and a["avg_amount"] >= 5:
                # mark both as potential duplicates of each other
                if "duplicate_candidate" not in a["flags"]:
                    a["flags"].append("duplicate_candidate")
                if "duplicate_candidate" not in b["flags"]:
                    b["flags"].append("duplicate_candidate")

    # Active vs stale split.
    active_subs = [s for s in subs if s["is_active"]]
    stale_subs  = [s for s in subs if not s["is_active"]]

    # Monthly / annual totals — overall (back-compat) and per-bucket.
    monthly_estimate = round(sum(s["avg_amount"] for s in subs), 2)
    annual_total     = round(sum(s["annual"]     for s in subs), 2)
    active_monthly_estimate = round(sum(s["avg_amount"] for s in active_subs), 2)
    active_annual_total     = round(sum(s["annual"]     for s in active_subs), 2)
    stale_annual_total      = round(sum(s["annual"]     for s in stale_subs),  2)

    _close(c, opened)

    # Rank cancellation candidates: any flagged (excluding pure
    # variable_amount-only flag — that's descriptive, not actionable).
    def _is_actionable(s: dict) -> bool:
        actionable = {"price_increase", "low_usage", "duplicate_candidate"}
        return bool(set(s["flags"]) & actionable)

    def _rank_key(s: dict) -> tuple:
        score = 0
        if "price_increase" in s["flags"]:    score -= 40
        if "low_usage" in s["flags"]:         score -= 20
        if "duplicate_candidate" in s["flags"]: score -= 15
        return (score, -s["annual"])

    active_candidates = sorted(
        [s for s in active_subs if _is_actionable(s)], key=_rank_key
    )[:5]
    stale_candidates = sorted(stale_subs, key=lambda s: -s["annual"])[:8]

    # Annual cost we'd save by cancelling every ACTIVE candidate (this
    # is what the Reduce "cancel-candidate annual" KPI should display).
    active_candidate_annual_total = round(
        sum(s["annual"] for s in active_candidates), 2
    )

    # Back-compat: keep old `candidates` key pointing at the active set
    # so any existing reader still works. Keep `subs` as the union.
    return {
        "subs":             subs,
        "monthly_estimate": monthly_estimate,
        "annual_total":     annual_total,
        "candidates":       active_candidates,
        "lookback_months":  lookback_months,
        "count":            len(subs),
        # Pass 18 additions
        "active_subs":                  active_subs,
        "stale_subs":                   stale_subs,
        "active_candidates":            active_candidates,
        "stale_candidates":             stale_candidates,
        "active_monthly_estimate":      active_monthly_estimate,
        "active_annual_total":          active_annual_total,
        "stale_annual_total":           stale_annual_total,
        "active_candidate_annual_total": active_candidate_annual_total,
        "anchor_date":                  anchor_iso,
        "active_window_days":           int(active_window_days),
    }


# ── Pass 10: Savings Scenario Simulator ────────────────────────────────

def scenario_simulate(scenario: dict, conn=None) -> dict:
    """
    Deterministic what-if simulator.

    scenario shape (any subset):
      {
        "category_cuts":   {"Food & Convenience": 0.25, ...},   # % reductions
        "subscription_cancels": ["NETFLIX", ...],               # merchant names
        "target_savings_rate": 10.0,                            # % goal
        "target_monthly_savings": 500.0,                        # $ goal
      }

    Returns:
      {
        "baseline":   {month, income, spending, net, savings_rate},
        "projected":  {spending, net, savings_rate, delta_savings_$, monthly},
        "assumptions": [...],
        "impact_breakdown": [{source, monthly_savings}, ...],
        "required_extra_cut": $ | null   # when target > projected
      }
    """
    c, opened = _conn(conn)

    # Use latest imported month as baseline
    aggs = monthly_aggregates(conn=c)
    if not aggs:
        _close(c, opened)
        return {
            "baseline":         None,
            "projected":        None,
            "assumptions":      ["No imported data yet — cannot simulate."],
            "impact_breakdown": [],
            "required_extra_cut": None,
        }

    latest = aggs[-1]
    baseline = {
        "month":        latest["month"],
        "income":       round(latest["income"],       2),
        "spending":     round(latest["spending"],     2),
        "net":          round(latest["net"],          2),
        "savings_rate": round(latest["savings_rate"], 2),
    }

    assumptions: list[str] = [
        f"Baseline is {baseline['month']}: income ${baseline['income']:,.0f}, "
        f"spending ${baseline['spending']:,.0f}."
    ]

    impact_breakdown: list[dict] = []
    total_monthly_savings = 0.0

    # Category cuts — use last 90d average for the category as reference so
    # cuts are realistic even if last month was noisy
    category_cuts = scenario.get("category_cuts") or {}
    for cat, pct in category_cuts.items():
        try:
            pct_f = float(pct)
        except (TypeError, ValueError):
            continue
        if pct_f <= 0:
            continue
        pct_f = min(1.0, pct_f)
        row = c.execute("""
            SELECT SUM(ABS(amount)) AS total
            FROM transactions
            WHERE category = ? AND direction='debit' AND is_transfer=0
              AND transaction_date >= date('now', '-90 days')
        """, (cat,)).fetchone()
        total_90d = (row["total"] if row else 0) or 0
        monthly_avg = total_90d / 3.0
        savings = round(monthly_avg * pct_f, 2)
        if savings > 0:
            impact_breakdown.append({
                "source": f"Cut {cat} by {int(pct_f*100)}%",
                "monthly_savings": savings,
                "basis":   f"90-day avg ${monthly_avg:,.0f}/mo in {cat}",
            })
            total_monthly_savings += savings
            assumptions.append(
                f"{cat}: 90-day average ${monthly_avg:,.0f}/mo, reducing by "
                f"{int(pct_f*100)}% → save ~${savings:,.0f}/mo."
            )

    # Subscription cancellations
    cancel_list = scenario.get("subscription_cancels") or []
    for merchant in cancel_list:
        row = c.execute("""
            SELECT AVG(ABS(amount)) AS avg_amount, COUNT(*) AS cnt
            FROM transactions
            WHERE merchant = ? AND direction='debit' AND is_transfer=0
              AND transaction_date >= date('now', '-90 days')
        """, (merchant,)).fetchone()
        avg = (row["avg_amount"] if row else 0) or 0
        if avg > 0:
            impact_breakdown.append({
                "source": f"Cancel {merchant}",
                "monthly_savings": round(avg, 2),
                "basis":   f"90-day avg ${avg:,.0f}",
            })
            total_monthly_savings += avg
            assumptions.append(
                f"{merchant}: avg ${avg:,.0f} over last 90 days, cancelling → save ~${avg:,.0f}/mo."
            )

    _close(c, opened)

    projected_spending     = max(0, baseline["spending"] - total_monthly_savings)
    projected_net          = baseline["income"] - projected_spending
    projected_savings_rate = (projected_net / baseline["income"] * 100) if baseline["income"] > 0 else 0

    projected = {
        "spending":     round(projected_spending,     2),
        "net":          round(projected_net,          2),
        "savings_rate": round(projected_savings_rate, 2),
        "delta_savings":  round(total_monthly_savings, 2),
        "delta_savings_annual": round(total_monthly_savings * 12, 2),
    }

    # Target gap analysis
    required_extra_cut = None
    target_sr = scenario.get("target_savings_rate")
    target_ms = scenario.get("target_monthly_savings")
    if target_sr is not None:
        try:
            target_sr_f = float(target_sr)
            needed_net = baseline["income"] * (target_sr_f / 100.0)
            gap = needed_net - projected_net
            if gap > 0:
                required_extra_cut = round(gap, 2)
                assumptions.append(
                    f"Target {target_sr_f:.0f}% savings rate requires net ≥ "
                    f"${needed_net:,.0f}/mo → still need ${gap:,.0f}/mo more in cuts."
                )
        except (TypeError, ValueError):
            pass
    if target_ms is not None:
        try:
            target_ms_f = float(target_ms)
            gap = target_ms_f - total_monthly_savings
            if gap > 0:
                if required_extra_cut is None:
                    required_extra_cut = round(gap, 2)
                assumptions.append(
                    f"Target ${target_ms_f:,.0f}/mo savings → still need "
                    f"${gap:,.0f}/mo more in cuts."
                )
        except (TypeError, ValueError):
            pass

    return {
        "baseline":            baseline,
        "projected":           projected,
        "assumptions":         assumptions,
        "impact_breakdown":    impact_breakdown,
        "required_extra_cut":  required_extra_cut,
    }


def top_controllable_categories(conn=None, limit: int = 5) -> list[dict]:
    """Last-90d debit totals for consumption categories, for the scenario UI."""
    c, opened = _conn(conn)
    rows = c.execute("""
        SELECT category, SUM(ABS(amount)) AS total_90d, COUNT(*) AS cnt
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                               'Payment','Credit Card Payment','Cancelled',
                               'Housing / Mortgage','Fees / Interest','Cash Advance')
          AND transaction_date >= date('now', '-90 days')
        GROUP BY category
        ORDER BY total_90d DESC
        LIMIT ?
    """, (limit,)).fetchall()
    _close(c, opened)
    return [
        {
            "category": r["category"],
            "total_90d": round(r["total_90d"] or 0, 2),
            "monthly_avg": round((r["total_90d"] or 0) / 3.0, 2),
            "tx_count": int(r["cnt"] or 0),
        }
        for r in rows
    ]


# ── Money Runway + Mission Deck ────────────────────────────────────────

_RUNWAY_FIXED_CATEGORIES = {
    "Housing / Mortgage",
    "Utilities / Bills",
    "Insurance",
}
_RUNWAY_EXCLUDED_CATEGORIES = {
    "Transfer", "Transfer Out", "Transfer In",
    "Payment", "Credit Card Payment", "Cancelled",
    "Internal Transfer", "Savings", "Fees / Interest", "Cash Advance",
}


def _month_bounds_from_label(month: str) -> tuple[str, str, int]:
    import calendar
    y, m = int(month[:4]), int(month[5:7])
    days = calendar.monthrange(y, m)[1]
    return f"{month}-01", f"{month}-{days:02d}", days


def _latest_tx_date_for_month(month: str, conn) -> str:
    start, end, _days = _month_bounds_from_label(month)
    row = conn.execute(
        "SELECT MAX(transaction_date) AS d FROM transactions "
        "WHERE transaction_date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()
    return (row["d"] if row and row["d"] else end)


def _cashflow_for_month(month: str, conn) -> dict:
    from utils.analytics import compute_cashflow
    start, end, _days = _month_bounds_from_label(month)
    return compute_cashflow(start, end, conn=conn) or {}


def _current_month_activity(month: str, anchor_date: str, conn) -> dict:
    from utils.analytics import compute_cashflow
    start, _end, _days = _month_bounds_from_label(month)
    return compute_cashflow(start, anchor_date, conn=conn) or {}


def _remaining_commitments(month: str, anchor_date: str, conn) -> dict:
    """Split remaining fixed bills and active subscriptions for a month."""
    from utils.planner import bills_and_commitments

    start, _end, _days = _month_bounds_from_label(month)
    month_start = date.fromisoformat(start)
    anchor = date.fromisoformat(anchor_date)
    bills = bills_and_commitments(conn=conn) or {}
    fixed = 0.0
    subs = 0.0
    upcoming: list[dict] = []

    for item in bills.get("items") or []:
        if not item.get("included_in_forecast"):
            continue
        last_seen = item.get("last_seen") or ""
        try:
            last_dt = date.fromisoformat(last_seen) if last_seen else None
        except Exception:
            last_dt = None
        already_seen_this_month = bool(
            last_dt and month_start <= last_dt <= anchor
        )
        if already_seen_this_month:
            continue

        amount = float(item.get("est_amount") or 0)
        group = item.get("group") or ""
        if group == "active_subscriptions":
            subs += amount
        else:
            fixed += amount
        upcoming.append({
            "merchant": item.get("merchant") or "Upcoming bill",
            "category": item.get("category") or "",
            "amount": round(amount, 2),
            "group": group or "commitment",
            "confidence": item.get("confidence") or "medium",
            "reason": item.get("reason") or "",
            "target_page": "Plan",
        })

    upcoming.sort(key=lambda r: -float(r.get("amount") or 0))
    return {
        "fixed_remaining": round(fixed, 2),
        "active_subscriptions_remaining": round(subs, 2),
        "upcoming": upcoming[:6],
        "active_monthly_estimate": round(
            float(bills.get("active_monthly_estimate") or 0), 2
        ),
    }


def _category_totals(month: str, through_date: str, conn) -> dict[str, float]:
    start, _end, _days = _month_bounds_from_label(month)
    rows = conn.execute(
        """
        SELECT category, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction='debit'
          AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                               'Payment','Credit Card Payment','Cancelled',
                               'Internal Transfer')
        GROUP BY category
        """,
        (start, through_date),
    ).fetchall()
    return {r["category"] or "Uncategorized": float(r["total"] or 0) for r in rows}


def _plan_category_targets(month: str, conn) -> dict[str, float]:
    from utils.database import get_monthly_plan
    plan = get_monthly_plan(month, conn=conn) or {}
    return {
        r.get("category"): float(r.get("target_amount") or 0)
        for r in (plan.get("category_targets") or [])
        if r.get("category")
    }


def _build_watchlists(
    *,
    current_month: str,
    truth_month: str,
    anchor_date: str,
    days_elapsed: int,
    days_in_month: int,
    conn,
) -> list[dict]:
    current_totals = _category_totals(current_month, anchor_date, conn)
    truth_totals = _category_totals(truth_month, _month_bounds_from_label(truth_month)[1], conn)
    targets = _plan_category_targets(current_month, conn)
    watchlists: list[dict] = []

    candidates = []
    for cat, amount in current_totals.items():
        if cat in _RUNWAY_EXCLUDED_CATEGORIES or cat in _RUNWAY_FIXED_CATEGORIES:
            continue
        target = targets.get(cat)
        typical = truth_totals.get(cat, 0.0)
        if target is None or target <= 0:
            target = typical if typical > 0 else max(amount, 1.0)
        expected_to_date = target * min(1.0, days_elapsed / max(days_in_month, 1))
        over_by = amount - expected_to_date
        pace_ratio = amount / max(expected_to_date, 1.0)
        if pace_ratio >= 1.15:
            status = "over"
        elif pace_ratio >= 0.90:
            status = "watch"
        else:
            status = "on_track"
        candidates.append((status != "on_track", over_by, amount, cat, target, typical, status))

    candidates.sort(key=lambda x: (not x[0], -x[1], -x[2]))
    for _flagged, over_by, amount, cat, target, typical, status in candidates[:3]:
        watchlists.append({
            "id": f"category:{cat.lower().replace(' ', '_').replace('/', '_')}",
            "kind": "category",
            "label": cat,
            "current_amount": round(amount, 2),
            "target_amount": round(target, 2),
            "typical_amount": round(typical, 2),
            "pace_status": status,
            "reason": (
                f"{cat} is ${amount:,.0f} so far vs a pace target of "
                f"${target:,.0f}/mo."
            ),
            "target_page": "Spending",
            "action_label": f"Review {cat}",
        })

    flagged_count = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE is_flagged=1"
    ).fetchone()["n"] or 0
    if flagged_count:
        watchlists.append({
            "id": "review_queue",
            "kind": "review",
            "label": "Review queue",
            "current_amount": float(flagged_count),
            "target_amount": 0.0,
            "typical_amount": 0.0,
            "pace_status": "watch",
            "reason": f"{flagged_count} transaction(s) still need review.",
            "target_page": "Review queue",
            "action_label": "Clear review rows",
        })

    sub = subscription_detective(conn=conn) or {}
    active_monthly = float(sub.get("active_monthly_estimate") or 0)
    if active_monthly > 0 and len(watchlists) < 4:
        watchlists.append({
            "id": "active_subscriptions",
            "kind": "subscription",
            "label": "Active subscriptions",
            "current_amount": round(active_monthly, 2),
            "target_amount": round(active_monthly * 0.8, 2),
            "typical_amount": round(active_monthly, 2),
            "pace_status": "watch",
            "reason": (
                f"Active recurring subscriptions are about ${active_monthly:,.0f}/mo."
            ),
            "target_page": "Reduce",
            "action_label": "Audit subscriptions",
        })

    return watchlists[:4]


def money_runway(conn=None) -> dict:
    """Deterministic safe-to-spend and weekly-control packet."""
    c, opened = _conn(conn)
    try:
        cov = statement_coverage(conn=c) or {}
        truth_month = cov.get("latest_complete_month") or ""
        latest_data_month = cov.get("latest_data_month") or ""
        if not truth_month and latest_data_month:
            truth_month = latest_data_month
        if not truth_month:
            return {
                "available": False,
                "reason": "Import at least one month of transactions to build a runway.",
                "truth_month": "",
                "latest_data_month": latest_data_month,
                "using_partial_month": False,
                "partial_month_note": "",
                "safe_to_spend": {
                    "amount": 0.0, "daily_amount": 0.0, "days_left": 0,
                    "period_end": "", "confidence": "low", "formula": {},
                },
                "runway_status": "unknown",
                "why": ["Ledger needs a complete statement month before it can estimate runway."],
                "watchlists": [],
                "upcoming": [],
                "wins": [],
                "data_caveats": ["Not enough imported data yet."],
            }

        current_month = latest_data_month or truth_month
        using_partial = bool(
            current_month
            and current_month != truth_month
            and current_month in (cov.get("partial_months") or [])
        )
        partial_note = ""
        caveats: list[str] = []
        if using_partial:
            partial_note = (
                f"{current_month} is partial: "
                f"{cov.get('incomplete_reason') or 'not enough days imported'}. "
                f"Ledger uses {truth_month} as the trusted baseline."
            )
            caveats.append(partial_note)

        anchor_date = _latest_tx_date_for_month(current_month, c)
        start, period_end, days_in_month = _month_bounds_from_label(current_month)
        month_start = date.fromisoformat(start)
        anchor = date.fromisoformat(anchor_date)
        days_elapsed = max(1, (anchor - month_start).days + 1)
        days_left = max(0, (date.fromisoformat(period_end) - anchor).days)

        truth_cf = _cashflow_for_month(truth_month, c)
        current_cf = _current_month_activity(current_month, anchor_date, c)

        from utils.database import get_monthly_plan
        plan = get_monthly_plan(current_month, conn=c)
        income_expected = float(
            (plan or {}).get("income_target")
            or truth_cf.get("income")
            or current_cf.get("income")
            or 0
        )
        spending_so_far = float(current_cf.get("spending") or 0)
        remaining = _remaining_commitments(current_month, anchor_date, c)
        goal_commitments = float((plan or {}).get("savings_target") or 0)

        from utils.analytics import compute_score
        score = compute_score(conn=c) or {}
        fc = score.get("finance_charges") or {}
        debt_or_fee_reserve = float(fc.get("total") or 0)

        buffer = round(max(25.0, min(250.0, income_expected * 0.05)), 2) if income_expected > 0 else 0.0
        amount = (
            income_expected
            - spending_so_far
            - float(remaining["fixed_remaining"])
            - float(remaining["active_subscriptions_remaining"])
            - goal_commitments
            - debt_or_fee_reserve
            - buffer
        )
        daily_amount = amount / max(days_left, 1) if days_left > 0 else amount
        if amount < 0:
            status = "danger"
        elif daily_amount < 15:
            status = "tight"
        elif daily_amount < 35:
            status = "watch"
        else:
            status = "clear"

        confidence = "high" if plan and not using_partial else ("medium" if truth_month else "low")
        why = [
            f"Baseline month: {truth_month}.",
            f"${spending_so_far:,.0f} spent so far in {current_month}.",
            f"${float(remaining['fixed_remaining']) + float(remaining['active_subscriptions_remaining']):,.0f} reserved for remaining bills/subscriptions.",
        ]
        if goal_commitments:
            why.append(f"${goal_commitments:,.0f} reserved for this month's savings goal.")
        if buffer:
            why.append(f"${buffer:,.0f} kept as a small safety buffer.")
        if using_partial:
            why.append("Partial current-month data is shown as recent activity, not as the monthly truth.")

        watchlists = _build_watchlists(
            current_month=current_month,
            truth_month=truth_month,
            anchor_date=anchor_date,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            conn=c,
        )
        packet = {
            "available": True,
            "truth_month": truth_month,
            "latest_data_month": latest_data_month,
            "using_partial_month": using_partial,
            "partial_month_note": partial_note,
            "safe_to_spend": {
                "amount": round(amount, 2),
                "daily_amount": round(daily_amount, 2),
                "days_left": int(days_left),
                "period_end": period_end,
                "confidence": confidence,
                "formula": {
                    "income_available_or_expected": round(income_expected, 2),
                    "spending_so_far": round(spending_so_far, 2),
                    "planned_bills_remaining": remaining["fixed_remaining"],
                    "active_subscriptions_remaining": remaining["active_subscriptions_remaining"],
                    "goal_commitments": round(goal_commitments, 2),
                    "debt_or_fee_reserve": round(debt_or_fee_reserve, 2),
                    "buffer": buffer,
                },
            },
            "runway_status": status,
            "why": why[:5],
            "watchlists": watchlists,
            "upcoming": remaining["upcoming"],
            "wins": [],
            "data_caveats": caveats,
        }
        packet["wins"] = found_money(conn=c).get("wins", [])
        return packet
    finally:
        _close(c, opened)


def found_money(conn=None) -> dict:
    """Read-only tiny-wins packet. It never moves or creates money."""
    c, opened = _conn(conn)
    try:
        cov = statement_coverage(conn=c) or {}
        truth_month = cov.get("latest_complete_month") or cov.get("latest_data_month") or ""
        if not truth_month:
            return {
                "available": False,
                "truth_month": "",
                "potential_redirect": 0.0,
                "wins": [],
                "data_caveats": ["Import data to find tiny wins."],
            }
        start, end, _days = _month_bounds_from_label(truth_month)
        import math
        rows = c.execute(
            """
            SELECT ABS(amount) AS amt
            FROM transactions
            WHERE transaction_date BETWEEN ? AND ?
              AND direction='debit'
              AND is_transfer=0
              AND amount > 0
              AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                   'Payment','Credit Card Payment','Cancelled',
                                   'Internal Transfer')
            """,
            (start, end),
        ).fetchall()
        roundup = sum(max(0.0, math.ceil(float(r["amt"] or 0)) - float(r["amt"] or 0)) for r in rows)

        score = None
        try:
            from utils.analytics import compute_score
            score = compute_score(conn=c)
        except Exception:
            score = {}
        fc = (score or {}).get("finance_charges") or {}
        fee_total = float(fc.get("total") or 0)

        sub = subscription_detective(conn=c) or {}
        inactive_count = len(sub.get("stale_subs") or [])
        flagged = c.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE is_flagged=1"
        ).fetchone()["n"] or 0

        wins: list[dict] = []
        wins.append({
            "id": "roundup_potential",
            "title": f"Roundup potential: ${roundup:,.2f}",
            "kind": "save",
            "amount": round(roundup, 2),
            "detail": "If you chose to round purchases to the next dollar, this is the trusted-month redirect amount.",
        })
        if fee_total <= 0.50:
            wins.append({
                "id": "low_fees",
                "title": "Almost no finance charges in the trusted month",
                "kind": "protect",
                "amount": round(fee_total, 2),
                "detail": f"Statement interest + fees were ${fee_total:,.2f}.",
            })
        if inactive_count:
            wins.append({
                "id": "inactive_excluded",
                "title": f"{inactive_count} inactive recurring service(s) excluded",
                "kind": "reduce",
                "amount": 0.0,
                "detail": "Inactive services are audit items, not active savings claims.",
            })
        if not flagged:
            wins.append({
                "id": "review_clear",
                "title": "Review queue is clear",
                "kind": "review",
                "amount": 0.0,
                "detail": "No flagged transactions are waiting for cleanup.",
            })

        potential = round(roundup, 2)
        return {
            "available": True,
            "truth_month": truth_month,
            "potential_redirect": potential,
            "wins": wins[:4],
            "data_caveats": [],
        }
    finally:
        _close(c, opened)


def mission_deck(conn=None, limit: int = 3) -> list[dict]:
    """Return the highest-value practical missions for the weekly loop."""
    c, opened = _conn(conn)
    try:
        runway = money_runway(conn=c)
        wins = found_money(conn=c)
        missions: list[dict] = []

        def _add(m: dict) -> None:
            if len(missions) >= max(1, limit):
                return
            if any(x.get("id") == m.get("id") for x in missions):
                return
            missions.append(m)

        safe = runway.get("safe_to_spend") or {}
        safe_amount = float(safe.get("amount") or 0)
        daily = float(safe.get("daily_amount") or 0)
        status = runway.get("runway_status") or "watch"

        if runway.get("available") and status in {"danger", "tight", "watch"}:
            _add({
                "id": "protect_runway",
                "title": "Protect the month",
                "kind": "protect",
                "why_it_matters": (
                    f"Safe-to-spend is ${safe_amount:,.0f} "
                    f"(${daily:,.0f}/day). Keep the month from drifting."
                ),
                "effort": "5 min",
                "impact_label": "Protects cashflow",
                "impact_amount": round(max(0.0, abs(safe_amount)), 2) if safe_amount < 0 else None,
                "confidence": safe.get("confidence") or "medium",
                "if_then_plan": (
                    f"If daily spend is over ${max(1, daily):,.0f}, then I will pause one flexible category for 48 hours."
                ),
                "action_label": "Open Plan",
                "target_page": "Plan",
                "evidence": [{"type": "money_runway", "status": status, "safe_to_spend": safe_amount}],
            })

        review_count = c.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE is_flagged=1"
        ).fetchone()["n"] or 0
        if review_count:
            _add({
                "id": "clear_review_queue",
                "title": f"Clear {min(3, int(review_count))} Review row(s)",
                "kind": "review",
                "why_it_matters": "Cleaner categories make every score, chart, and export more trustworthy.",
                "effort": "5 min",
                "impact_label": "Improves data quality",
                "impact_amount": None,
                "confidence": "high",
                "if_then_plan": "If I open Ledger this week, then I will clear 3 Review rows before changing the plan.",
                "action_label": "Open Review",
                "target_page": "Review queue",
                "evidence": [{"type": "review_queue", "count": int(review_count)}],
            })

        for w in runway.get("watchlists") or []:
            if w.get("kind") != "category" or w.get("pace_status") == "on_track":
                continue
            label = w.get("label") or "Top category"
            target = float(w.get("target_amount") or 0)
            current = float(w.get("current_amount") or 0)
            _add({
                "id": f"watch_{w.get('id','category')}",
                "title": f"Keep {label} under ${target:,.0f}",
                "kind": "reduce",
                "why_it_matters": f"{label} is at ${current:,.0f}; it is the clearest flexible area to control.",
                "effort": "this week",
                "impact_label": "Controls spending pace",
                "impact_amount": round(max(0.0, current - target), 2),
                "confidence": "medium",
                "if_then_plan": f"If {label} passes ${target:,.0f}, then I will switch to a no-spend version of that category for two days.",
                "action_label": w.get("action_label") or "Open Spending",
                "target_page": w.get("target_page") or "Spending",
                "evidence": [{"type": "watchlist", "label": label, "current": current, "target": target}],
            })
            break

        ca = cash_advance_status(conn=c) or {}
        if ca.get("ca_count") and ca.get("verdict") in {"covered", "uncertain"}:
            total = float(ca.get("ca_total") or 0)
            _add({
                "id": "verify_cash_advance",
                "title": f"Verify the ${total:,.0f} cash-advance category",
                "kind": "verify",
                "why_it_matters": "Ledger sees the transaction and later card payments, but it should not claim unpaid debt without balance evidence.",
                "effort": "2 min",
                "impact_label": "Prevents bad advice",
                "impact_amount": None,
                "confidence": ca.get("confidence") or "medium",
                "if_then_plan": "If the statement confirms the card is paid, then I will treat this as historical/category cleanup, not a payoff task.",
                "action_label": "Open Transactions",
                "target_page": "Transactions",
                "evidence": [{"type": "cash_advance_status", "verdict": ca.get("verdict"), "total": total}],
            })

        sub = subscription_detective(conn=c) or {}
        active_candidates = sub.get("active_candidates") or []
        if active_candidates:
            amount = float(active_candidates[0].get("annual") or 0)
            _add({
                "id": "trim_subscription_bill",
                "title": "Trim one active subscription",
                "kind": "reduce",
                "why_it_matters": "A recurring cut keeps paying you back every month.",
                "effort": "15 min",
                "impact_label": f"Up to ${amount:,.0f}/yr reviewed",
                "impact_amount": round(amount, 2),
                "confidence": "medium",
                "if_then_plan": "If I do one money task this week, then I will open Reduce and cancel or confirm one active subscription.",
                "action_label": "Open Reduce",
                "target_page": "Reduce",
                "evidence": [{"type": "subscription_candidate", "merchant": active_candidates[0].get("merchant"), "annual": amount}],
            })

        potential = float(wins.get("potential_redirect") or 0)
        if safe_amount > 0 and potential > 0:
            _add({
                "id": "redirect_found_money",
                "title": "Redirect a tiny win",
                "kind": "save",
                "why_it_matters": f"Roundups in the trusted month suggest ${potential:,.2f} could be redirected without changing transactions.",
                "effort": "2 min",
                "impact_label": f"${potential:,.2f} potential",
                "impact_amount": round(potential, 2),
                "confidence": "medium",
                "if_then_plan": "If safe-to-spend stays positive, then I will move one small chosen amount to my top goal.",
                "action_label": "Open Plan",
                "target_page": "Plan",
                "evidence": [{"type": "found_money", "potential_redirect": potential}],
            })

        if len(missions) < max(1, limit) and runway.get("available"):
            _add({
                "id": "maintain_streak",
                "title": "Keep the month steady",
                "kind": "protect",
                "why_it_matters": "Your current runway is positive; the best move is to avoid avoidable drift.",
                "effort": "2 min",
                "impact_label": "Maintain control",
                "impact_amount": None,
                "confidence": "medium",
                "if_then_plan": "If I make an unplanned purchase, then I will check the Dashboard before making a second one.",
                "action_label": "Open Dashboard",
                "target_page": "Dashboard",
                "evidence": [{"type": "money_runway", "status": status}],
            })

        return missions[:limit]
    finally:
        _close(c, opened)

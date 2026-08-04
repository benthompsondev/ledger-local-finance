"""
Weekly check-in packet — the deterministic data source for the Home page.

Everything here is computed from the local SQLite database. No AI, no
network calls. The Home page renders this packet top-to-bottom; the
Dashboard and Plan pages reuse safe_to_spend_summary() so the whole app
shows exactly one Safe to Spend number with one definition:

    safe_to_spend = current balances explicitly available for spending
                    − remaining fixed bills
                    − remaining active subscriptions
                    − current credit-card liabilities
                    − this month's savings target
                    − exact statement interest/fees reserve
                    − a small safety buffer

Planned income that has not arrived is reported separately and never enters
the Safe to Spend amount. Current balances already reflect income received and
spending completed, so adding either again would double count them.

(The underlying engine is utils.insights.money_runway; this module is
the single consumption point so pages can't drift apart again.)

Materiality: findings below config.constants.MATERIALITY_THRESHOLD are
never surfaced. Change the threshold there, not here.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from config.constants import (
    MATERIALITY_THRESHOLD, FRESH_CURRENT_DAYS, FRESH_STALE_DAYS,
)


def _conn(conn):
    from utils.database import get_connection
    if conn is not None:
        return conn, False
    return get_connection(), True


def _close(conn, opened):
    if opened:
        try:
            conn.close()
        except Exception:
            pass


def _today(today) -> date:
    if isinstance(today, date):
        return today
    if isinstance(today, str) and today:
        return date.fromisoformat(today)
    return date.today()


# ─────────────────────────────────────────────────────────────────────
# Canonical Safe to Spend
# ─────────────────────────────────────────────────────────────────────
def _median_monthly_income(conn) -> float:
    """Median monthly Money In across recent months — the plain "typical
    income" floor used when a strict reliable-income read is unavailable
    (e.g. sparse or not-yet-complete months). Median resists one low partial
    month, so it stays close to a 90-day average without over-counting a
    single big deposit."""
    from statistics import median
    from utils.insights import monthly_aggregates
    months = monthly_aggregates(conn=conn) or []
    incomes = [
        float(m.get("income") or 0) for m in months[-6:]
        if float(m.get("income") or 0) > 0
    ]
    # Need at least two months of income before quoting a "typical income"
    # floor; a single month is not a stable estimate.
    if len(incomes) < 2:
        return 0.0
    return round(float(median(incomes)), 2)


def flow_safe_to_spend(conn, today=None) -> Optional[dict]:
    """Flow-first Safe to Spend, derived entirely from transaction history.

    Two distinct numbers, because conflating them was the 1.4.1 defect:

      monthly_plan  typical income minus commitments, savings and buffer.
                    What a whole ordinary month has room for.
      remaining     the monthly plan minus flexible spending already done
                    this month. What is actually left.

    ``remaining`` is the headline. Needs no account balances and no saved
    plan; the cash-reconciled path refines it when balances exist. Returns
    None only when there is not enough income history to form any number.
    """
    from utils.analytics import typical_monthly_income
    from utils.database import get_applicable_plan, get_monthly_plan
    from utils.insights import _flexible_spending_so_far, statement_coverage
    from utils.planner import bills_and_commitments

    baseline = typical_monthly_income(conn=conn)
    income = float(baseline.get("amount") or 0)
    if income <= 0:
        return None

    bills = bills_and_commitments(conn=conn)
    commitment_items = [
        item for item in bills.get("items", [])
        if item.get("included_in_forecast")
    ]
    # The monthly share, so a bill arriving four times a year costs the plan
    # a quarter of it each month rather than all of it every month.
    commitments = round(sum(
        float(item.get("monthly_setaside") or item.get("est_amount") or 0)
        for item in commitment_items
    ), 2)

    coverage = statement_coverage(conn=conn)
    month = str(coverage.get("latest_data_month") or "")
    saved = (
        get_monthly_plan(month, conn=conn)
        or get_applicable_plan(month, conn=conn)
    ) if month else None
    if saved and float(saved.get("savings_target") or 0) > 0:
        savings = round(float(saved.get("savings_target") or 0), 2)
        buffer = round(float(saved.get("safety_buffer") or 0), 2)
    else:
        savings = round(income * 0.15, 2)  # pay-yourself-first default
        buffer = 0.0
    monthly_plan = round(max(0.0, income - commitments - savings - buffer), 2)

    # Flexible spending already done this month. Fixed commitments are
    # excluded so a bill is never subtracted twice.
    flexible_spent = 0.0
    if month:
        through = str(conn.execute(
            "SELECT MAX(transaction_date) FROM transactions "
            "WHERE transaction_date BETWEEN ? AND ?",
            (f"{month}-01", f"{month}-31"),
        ).fetchone()[0] or "")
        if today:
            through = min(through, str(today)) if through else str(today)
        if through:
            flexible_spent = _flexible_spending_so_far(
                f"{month}-01", through, commitment_items, conn,
            )
    remaining = round(max(0.0, monthly_plan - flexible_spent), 2)

    return {
        "income": round(income, 2),
        "income_months_used": int(baseline.get("months_used") or 0),
        "income_basis_label": str(baseline.get("basis_label") or ""),
        "income_confidence": str(baseline.get("confidence") or "low"),
        "commitments": commitments,
        "savings": savings,
        "buffer": buffer,
        "monthly_plan": monthly_plan,
        "flexible_spent": flexible_spent,
        "remaining": remaining,
        "month": month,
    }


def _stale_data_state(conn, today=None) -> Optional[dict]:
    """Refuse to call a finished month's leftovers "money you have now".

    Someone who imports in late July, with statements ending in June, was
    being shown June's remaining budget as though it were spendable today.
    June is history. The honest answer is to name the snapshot and ask for
    newer statements rather than quote a number for a month that is over.

    Returns a ready-to-use unavailable result, or None when the data is
    current enough to answer normally.
    """
    import calendar as _cal
    from datetime import date

    from utils.analysis_period import supported_latest_date

    supported = supported_latest_date(conn=conn)
    latest = supported.isoformat() if supported else ""
    if len(latest) < 7:
        return None
    if today is None:
        reference = date.today()
    elif isinstance(today, str):
        try:
            reference = date.fromisoformat(today[:10])
        except ValueError:
            return None
    else:
        reference = today

    latest_month = latest[:7]
    if latest_month >= reference.isoformat()[:7]:
        return None  # data reaches into the current month: nothing is stale

    # The data month is behind the calendar month. That is only a problem
    # once the grace period for statements arriving late has passed.
    year, month = int(latest_month[:4]), int(latest_month[5:7])
    month_end = date(year, month, _cal.monthrange(year, month)[1])
    if (reference - month_end).days <= _MR_STATEMENT_GRACE_DAYS:
        return None

    label = f"{_cal.month_name[month]} {year}"
    return {
        "available": False,
        "reason": (
            f"Your newest statements end in {label}, which is over. Import "
            f"more recent transactions for a current number."
        ),
        "month": latest_month,
        "amount": 0.0, "weekly_amount": 0.0, "daily_amount": None,
        "days_left": 0, "period_end": month_end.isoformat(),
        "period_ended": True,
        "status": "unknown", "confidence": "low",
        "reserved": {"basis": "stale", "balance_checked": False},
        "why": [
            f"The most recent transaction is dated {latest}.",
            f"{label} has ended, so its leftover budget is not money "
            f"available today.",
        ],
        "is_stale": True,
        "snapshot_label": label,
        "setup_screen": "add-data",
    }


# How long after a month ends before its figures stop being presented as
# current. Statements routinely arrive a week or two late, and nagging
# someone on the 3rd of the month would be wrong.
_MR_STATEMENT_GRACE_DAYS = 20


def _month_name(month: str) -> str:
    """"2026-08" as "August". Empty for anything unparseable."""
    import calendar as _calendar

    text = str(month or "")
    if len(text) < 7 or text[4] != "-":
        return ""
    try:
        return _calendar.month_name[int(text[5:7])]
    except (ValueError, IndexError):
        return ""


def signed_money(value: float, decimals: int = 0) -> str:
    """Money for a sentence, with the minus sign where people put it.

    `f"${net:,.0f}"` produces "$-68" for a negative net, which reads as a
    typo rather than as a loss. The sign belongs in front of the currency
    symbol, not between it and the digits.

        68   -> "$68"
       -68   -> "-$68"
         0   -> "$0"

    Negative zero is treated as zero: a month that landed exactly even should
    not be reported as "-$0" because of floating-point drift.
    """
    number = float(value or 0.0)
    rounded = round(number, decimals)
    if rounded == 0:
        return f"${0:,.{decimals}f}"
    sign = "-" if rounded < 0 else ""
    return f"{sign}${abs(rounded):,.{decimals}f}"


def weekly_allowance(amount: float, days_left: int) -> dict:
    """The next seven days' share of what is left, and the daily rate.

    One implementation for every Safe to Spend path. The flow fallback used
    to compute ``amount * 7 / 30.4`` — a fixed share of an average month,
    regardless of how much of this month remained. With $2,190 left and 26
    days to go it showed $504.28 against a true seven-day share of $589.62,
    and the same figure drives "stay within $X this week", so the advice was
    wrong in the cautious direction on a long month and the dangerous
    direction on a short one: with three days left it still suggested a
    week's worth of spending.

    A finished period has no rate at all. Dividing by zero days, or quoting
    a daily allowance for a month that has ended, is not a smaller number —
    it is a different kind of statement, and callers must show the
    period-ended state instead.
    """
    if days_left is None or int(days_left) <= 0:
        return {"daily_amount": None, "weekly_amount": None}
    days = int(days_left)
    remaining = float(amount or 0.0)
    daily = remaining / days
    # Seven days' worth, but never more than actually remains: with fewer
    # than seven days left, this week *is* the rest of the period.
    weekly = remaining if days <= 7 else daily * 7.0
    return {
        "daily_amount": round(daily, 2),
        "weekly_amount": round(weekly, 2),
    }


def safe_to_spend_summary(conn: Optional[sqlite3.Connection] = None,
                          today=None) -> dict:
    """The one Safe to Spend result every page must use.

    Returns:
      available        bool — False when there is not enough data
      reason           str  — plain-language reason when unavailable
      month            'YYYY-MM' the number applies to
      amount           float — total remaining for the period
      weekly_amount    float — amount for the next 7 days (== amount when
                       fewer than 7 days remain or the period has ended)
      daily_amount     float | None — None when the period has ended;
                       never derived from a zero-day division
      days_left        int
      period_end       'YYYY-MM-DD'
      period_ended     bool — True when 0 days remain; callers must show
                       a plain "period has ended" state, not a daily rate
      status           'clear' | 'watch' | 'tight' | 'danger'
      confidence       'high' | 'medium' | 'low'
      reserved         dict — what was subtracted (bills, subscriptions,
                       savings target, debt/fee reserve, buffer)
      is_stale         bool — the newest data describes a month that has
                       already ended, so there is no current number to give
      why              [str] — the engine's own explanation lines
    """
    from utils.insights import money_runway

    c, opened = _conn(conn)
    try:
        stale = _stale_data_state(c, today)
        if stale is not None:
            return stale
    except Exception:
        pass  # never let the freshness guard block the real answer
    try:
        runway = money_runway(conn=c, today=today) or {}
    finally:
        _close(c, opened)

    safe = runway.get("safe_to_spend") or {}
    if safe.get("period_ended"):
        # A closed month's remainder is useful history, but it is never money
        # available "now". Keep this rule in the canonical summary so Home,
        # Plan, Coach, and future callers cannot accidentally present an old
        # allowance as current spending permission.
        month = str(
            runway.get("latest_data_month")
            or runway.get("truth_month")
            or ""
        )
        try:
            year, month_number = (int(part) for part in month.split("-", 1))
            month_label = f"{calendar.month_name[month_number]} {year}"
        except (TypeError, ValueError):
            month_label = "the latest imported month"
        return {
            "available": False,
            "reason": (
                f"Your latest imported activity is from {month_label}, which "
                "has ended. Import current-month transactions before "
                "Northstar shows money available now."
            ),
            "month": month,
            "amount": 0.0,
            "weekly_amount": 0.0,
            "daily_amount": None,
            "days_left": 0,
            "period_end": safe.get("period_end") or "",
            "period_ended": True,
            "status": "unknown",
            "confidence": "low",
            "reserved": {
                "basis": "period_ended",
                "balance_checked": False,
            },
            "why": [
                f"{month_label} is a completed period, not a current budget.",
                "Import current-month activity to calculate a new amount.",
            ],
            "is_stale": True,
            "snapshot_label": month_label,
            "setup_screen": "add-data",
        }

    if not runway.get("available"):
        formula = safe.get("formula") or {}
        # Flow-first fallback: whenever the cash-reconciled path is blocked
        # (no balances entered, no saved plan, or a commitment-drift review),
        # still show the plain budget number (typical income − recurring −
        # savings) instead of a dead "Not ready". Balances and plan
        # confirmation stay optional refinements, never gates.
        flow = flow_safe_to_spend(c, today=today)
        if flow is not None:
            amount = flow["remaining"]
            buffer_note = (
                f" minus buffer ${flow['buffer']:,.0f}" if flow["buffer"] else ""
            )
            return {
                "available": True,
                "reason": "",
                "month": flow["month"] or runway.get("latest_data_month")
                or runway.get("truth_month") or "",
                "amount": amount,
                **weekly_allowance(amount, int(safe.get("days_left") or 0)),
                "days_left": int(safe.get("days_left") or 0),
                "period_end": safe.get("period_end") or "",
                "period_ended": bool(safe.get("period_ended")),
                "status": "clear" if amount > 0 else "tight",
                "confidence": "estimate",
                "reserved": {
                    "basis": "flow",
                    # Without account balances this is an estimate from the
                    # flow of money, not a checked cash position. Every
                    # surface must say so rather than implying certainty.
                    "balance_checked": False,
                    "reliable_income": flow["income"],
                    "income_months_used": flow["income_months_used"],
                    "income_basis_label": flow["income_basis_label"],
                    "recurring_commitments": flow["commitments"],
                    "savings_target": flow["savings"],
                    "buffer": flow["buffer"],
                    "monthly_plan": flow["monthly_plan"],
                    "flexible_spent": flow["flexible_spent"],
                    "flexible_allowance": flow["monthly_plan"],
                },
                "why": [
                    f"Typical income ${flow['income']:,.0f} minus recurring "
                    f"${flow['commitments']:,.0f} minus savings "
                    f"${flow['savings']:,.0f}{buffer_note} gives a monthly "
                    f"plan of ${flow['monthly_plan']:,.0f}.",
                    f"You have spent ${flow['flexible_spent']:,.0f} of it so "
                    f"far, leaving ${amount:,.0f}.",
                    f"Typical income: {flow['income_basis_label']}.",
                    "Add account balances to check this against real cash.",
                ],
                "basis": "flow",
                "is_stale": False,
                "using_partial_month": bool(runway.get("using_partial_month")),
                "partial_month_note": runway.get("partial_month_note") or "",
            }
        return {
            "available": False,
            "reason": runway.get("reason")
            or "Import at least one month of transactions first.",
            "month": "",
            "amount": 0.0, "weekly_amount": 0.0, "daily_amount": None,
            "days_left": int(safe.get("days_left") or 0),
            "period_end": safe.get("period_end") or "",
            "period_ended": bool(safe.get("period_ended")),
            "status": "unknown", "confidence": "low",
            "reserved": {
                "reconciliation": formula.get("reconciliation") or {},
                "plan_agreement": formula.get("plan_agreement") or {},
            },
            "why": runway.get("why") or [],
            "is_stale": False,
            "setup_screen": (
                "plan" if (formula.get("plan_agreement") or {}).get(
                    "needs_review"
                ) else "settings#planning-balances"
            ),
        }

    formula = safe.get("formula") or {}
    return {
        "available": True,
        "reason": "",
        "month": runway.get("latest_data_month")
        or runway.get("truth_month") or "",
        "amount": float(safe.get("amount") or 0.0),
        "weekly_amount": float(safe.get("weekly_amount")
                               if safe.get("weekly_amount") is not None
                               else (safe.get("amount") or 0.0)),
        "daily_amount": safe.get("daily_amount"),
        "days_left": int(safe.get("days_left") or 0),
        "period_end": safe.get("period_end") or "",
        "period_ended": bool(safe.get("period_ended")),
        "status": runway.get("runway_status") or "watch",
        "confidence": safe.get("confidence") or "medium",
        "is_stale": False,
        "reserved": {
            "balance_checked": True,
            "liquid_balance": float(formula.get("liquid_balance") or 0),
            "available_balance": float(
                formula.get("available_balance") or formula.get("liquid_balance") or 0
            ),
            "excluded_balance": float(formula.get("excluded_balance") or 0),
            "balance_accounts": formula.get("balance_accounts") or [],
            "stable_income_target": float(formula.get("stable_income_target") or 0),
            "income_expected": float(formula.get("stable_income_target") or 0),
            "stable_income_received": float(formula.get("stable_income_received") or 0),
            "stable_income_remaining": float(formula.get("stable_income_remaining") or 0),
            "future_income_forecast": float(
                formula.get("future_income_forecast") or 0
            ),
            "future_income_included": bool(
                formula.get("future_income_included", False)
            ),
            "spending_so_far": float(formula.get("spending_so_far") or 0),
            "flexible_allowance": float(
                formula.get("flexible_allowance") or 0),
            "flexible_spending_so_far": float(
                formula.get("flexible_spending_so_far") or 0),
            "flexible_remaining": float(
                formula.get("flexible_remaining") or 0),
            "cash_cushion_after_bills": float(
                formula.get("cash_cushion_after_bills") or 0),
            "bills_remaining": float(formula.get("planned_bills_remaining") or 0),
            "subscriptions_remaining": float(
                formula.get("active_subscriptions_remaining") or 0),
            "savings_target": float(formula.get("goal_commitments") or 0),
            "savings_contributed_this_month": float(
                formula.get("savings_contributed_this_month") or 0),
            "savings_remaining": float(
                formula.get("savings_remaining") or 0),
            "headline_savings_target": float(
                formula.get("headline_savings_target") or 0),
            "goal_allocations_total": float(
                formula.get("goal_allocations_total") or 0),
            "unallocated_savings": float(
                formula.get("unallocated_savings") or 0),
            "allocation_above_plan": float(
                formula.get("allocation_above_plan") or 0),
            "debt_or_fee_reserve": float(formula.get("debt_or_fee_reserve") or 0),
            "credit_card_liability": float(formula.get("credit_card_liability") or 0),
            "nonmonthly_reserves": float(
                formula.get("nonmonthly_reserves") or 0),
            "buffer": float(formula.get("buffer") or 0),
            "reconciliation": formula.get("reconciliation") or {},
            "plan_agreement": formula.get("plan_agreement") or {},
        },
        "why": runway.get("why") or [],
        "using_partial_month": bool(runway.get("using_partial_month")),
        "partial_month_note": runway.get("partial_month_note") or "",
    }


# ─────────────────────────────────────────────────────────────────────
# Data freshness
# ─────────────────────────────────────────────────────────────────────
def data_freshness(conn: Optional[sqlite3.Connection] = None,
                   today=None) -> dict:
    """Classify how current the imported data is.

    States: 'current' (≤ FRESH_CURRENT_DAYS days old),
    'getting_stale' (≤ FRESH_STALE_DAYS), 'stale', 'no_data'.
    `today` is injectable for tests.
    """
    t = _today(today)
    c, opened = _conn(conn)
    try:
        # Freshness answers "how current is my data", so it must read the
        # date the data genuinely reaches. A stray row dated years ahead
        # would otherwise report a stale import as perfectly up to date.
        from utils.analysis_period import supported_latest_date
        _supported = supported_latest_date(conn=c)
        latest = _supported.isoformat() if _supported else None

        account_rows = c.execute("""
            SELECT COALESCE(a.name, t.account_type) AS account_name,
                   COALESCE(a.type, t.account_type) AS account_type,
                   MAX(t.transaction_date) AS latest_date,
                   MIN(t.transaction_date) AS earliest_date,
                   COUNT(*) AS tx_count
            FROM transactions t
            LEFT JOIN accounts a ON a.id=t.account_ref
            WHERE t.direction != 'cancelled'
            GROUP BY COALESCE(a.id, t.account_type)
            ORDER BY account_name
        """).fetchall()

        from utils.insights import statement_coverage
        cov = statement_coverage(conn=c) or {}
    finally:
        _close(c, opened)

    if not latest:
        return {
            "state": "no_data",
            "latest_date": "",
            "days_since": None,
            "headline": "No data imported yet",
            "detail": "Import your first statement to get started.",
            "update_action": "Import statements",
            "incomplete_note": "",
            "accounts": [],
            "account_activity_note": "",
            "coverage_warning": "",
        }

    latest_d = date.fromisoformat(latest[:10])
    days_since = max(0, (t - latest_d).days)
    if days_since <= FRESH_CURRENT_DAYS:
        state = "current"
    elif days_since <= FRESH_STALE_DAYS:
        state = "getting_stale"
    else:
        state = "stale"

    pretty = latest_d.strftime("%B %d").replace(" 0", " ")
    headline = f"Updated through {pretty}"
    if days_since == 0:
        detail = "Updated today."
    elif days_since == 1:
        detail = "1 day ago."
    else:
        detail = f"No new data for {days_since} days."

    update_action = ""
    if state in ("getting_stale", "stale"):
        update_action = (
            "Import your latest statement before trusting this "
            "month's forecast."
        )

    incomplete_note = ""
    latest_data_month = cov.get("latest_data_month") or ""
    if latest_data_month and latest_data_month in (cov.get("partial_months") or []):
        incomplete_note = (
            f"{latest_data_month} is a partial month: "
            f"{cov.get('incomplete_reason') or 'not fully imported yet'}."
        )

    accounts = []
    latest_dates = []
    for account_row in account_rows:
        account_latest = date.fromisoformat(account_row["latest_date"][:10])
        account_days = max(0, (t - account_latest).days)
        latest_dates.append(account_latest)
        accounts.append({
            "account_name": account_row["account_name"],
            "account_type": account_row["account_type"],
            "latest_date": account_latest.isoformat(),
            "earliest_date": account_row["earliest_date"],
            "tx_count": int(account_row["tx_count"] or 0),
            "days_since": account_days,
            "partial_current_period": account_latest.strftime("%Y-%m") == t.strftime("%Y-%m"),
        })
    cutoff_gap = ((max(latest_dates) - min(latest_dates)).days
                  if len(latest_dates) > 1 else 0)
    account_activity_note = ""
    if cutoff_gap >= 7:
        account_activity_note = (
            "These are last-transaction dates, not statement end dates. "
            "A quiet account can legitimately show an earlier date."
        )

    return {
        "state": state,
        "latest_date": latest_d.isoformat(),
        "days_since": days_since,
        "headline": headline,
        "detail": detail,
        "update_action": update_action,
        "incomplete_note": incomplete_note,
        "accounts": accounts,
        "cutoff_gap_days": cutoff_gap,
        "account_activity_note": account_activity_note,
        # Kept in the response contract for older desktop builds. A last
        # transaction date cannot prove statement coverage, so it must never
        # degrade the forecast or replace a useful check-in.
        "coverage_warning": "",
    }


# ─────────────────────────────────────────────────────────────────────
# Monthly progress
# ─────────────────────────────────────────────────────────────────────
def monthly_progress(conn: Optional[sqlite3.Connection] = None,
                     today=None) -> dict:
    """Savings target vs projection for the current planning period.

    Definitions:
      savings_target   — the applicable plan's fixed dollar target
                         (the analysis month's own plan, else the most
                         recently saved plan; 0 if none exists)
      projected_net    — forecast_month's projected income − spending
      savings_gap      — projected_net − savings_target (negative = short)
      target_rate      — savings_target / expected income (when income > 0)
      projected_rate   — forecast_month's projected savings rate
      pace_vs_prev     — spending through day N this month vs spending
                         through day N of the previous month
      calendar_month   — today's real month (may be ahead of the data)
      has_current_plan — True when a plan exists for calendar_month;
                         this is what the Home action ladder gates on
    """
    from utils.planner import analysis_anchor, forecast_month
    from utils.database import get_applicable_plan, get_monthly_plan

    t = _today(today)
    calendar_month = t.strftime("%Y-%m")
    c, opened = _conn(conn)
    try:
        month = analysis_anchor(conn=c)
        plan = get_applicable_plan(month, conn=c)
        has_current_plan = get_monthly_plan(calendar_month, conn=c) is not None
        fc = forecast_month(plan_month=month, conn=c, today=t) or {}

        # Same-point-previous-month spending comparison.
        anchor_str = fc.get("anchor_date") or f"{month}-01"
        anchor = date.fromisoformat(anchor_str)
        prev_last = date(anchor.year, anchor.month, 1) - timedelta(days=1)
        prev_month = prev_last.strftime("%Y-%m")
        prev_cut_day = min(anchor.day, prev_last.day)
        prev_cut = date(prev_last.year, prev_last.month, prev_cut_day)

        def _spend_between(a: str, b: str) -> float:
            from utils.analytics import compute_cashflow
            return float(compute_cashflow(a, b, conn=c).get("spending") or 0)

        spend_now = _spend_between(f"{month}-01", anchor.isoformat())
        spend_prev = _spend_between(f"{prev_month}-01", prev_cut.isoformat())
    finally:
        _close(c, opened)

    income_expected = float((plan or {}).get("income_target") or 0)
    from utils.goals import goal_plan_summary
    goal_plan = goal_plan_summary(
        float((plan or {}).get("savings_target") or 0), conn=conn,
    )
    savings_target = float(goal_plan["effective_savings_target"])
    projected_net = float(fc.get("projected_net") or 0)
    has_plan = bool(plan)

    return {
        "month": month,
        "calendar_month": calendar_month,
        "has_plan": has_plan,
        "has_current_plan": has_current_plan,
        "plan_month": (plan or {}).get("month") or "",
        "plan_mode": (plan or {}).get("mode") or "",
        "savings_target": savings_target,
        "headline_savings_target": goal_plan["headline_savings_target"],
        "goal_allocations_total": goal_plan["goal_allocations_total"],
        "unallocated_savings": goal_plan["unallocated_savings"],
        "allocation_above_plan": goal_plan["allocation_above_plan"],
        "income_expected": income_expected,
        "target_rate": (savings_target / income_expected
                        if income_expected > 0 else None),
        "projected_net": projected_net,
        "projected_rate": float(fc.get("projected_savings_rate") or 0),
        "savings_gap": round(projected_net - savings_target, 2),
        "mtd_income": float(fc.get("mtd_income") or 0),
        "mtd_spending": float(fc.get("mtd_spending") or 0),
        "projected_spending": float(fc.get("projected_spending") or 0),
        "risk_level": fc.get("risk_level") or "insufficient_data",
        "days_elapsed": int(fc.get("days_elapsed") or 0),
        "days_in_month": int(fc.get("days_in_month") or 0),
        "pace_vs_prev": {
            "spend_now": round(spend_now, 2),
            "spend_prev_same_point": round(spend_prev, 2),
            "delta": round(spend_now - spend_prev, 2),
            "prev_month": prev_month,
            "through_day": anchor.day,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Meaningful changes (max 3, ≥ MATERIALITY_THRESHOLD)
# ─────────────────────────────────────────────────────────────────────
def _dominant_merchant(category: str, month: str) -> str:
    """Name the single merchant behind a category's month, or '' if none.

    Keeps "Mostly X" attributions honest: only returns a name when the top
    merchant is at least 60% of that category's spending for the month, so a
    broad category is never pinned on one merchant.
    """
    from utils.insights import _mr_top_merchants_for_category
    c, opened = _conn(None)
    try:
        tops = _mr_top_merchants_for_category(category, month, c, limit=20)
    finally:
        _close(c, opened)
    if not tops:
        return ""
    cat_total = sum(float(t.get("total") or 0) for t in tops)
    top = tops[0]
    total = float(top.get("total") or 0)
    if cat_total > 0 and total >= 0.6 * cat_total and top.get("merchant"):
        return str(top["merchant"])
    return ""


def meaningful_changes(conn: Optional[sqlite3.Connection] = None,
                       limit: int = 3) -> list[dict]:
    """Up to `limit` material findings, ranked by absolute dollar impact.

    Sources (all deterministic, all already computed elsewhere):
      * complete-month category movers (monthly_review)
      * statement fees / interest (exact-statement values only)
      * recurring price increases (subscription_detective flags)
      * improved savings (positive net delta)
    Every finding carries current value, comparison value, dollar delta,
    the comparison period, and a drill-down target.
    """
    from utils.insights import monthly_review, subscription_detective

    c, opened = _conn(conn)
    try:
        mr = monthly_review(conn=c) or {}
        det = subscription_detective(conn=c) or {}
    finally:
        _close(c, opened)

    findings: list[dict] = []
    # Behavioral findings require two genuinely complete months. When
    # monthly_review fell back to partial data (uses_complete_months False),
    # this month's incomplete totals get compared against a full prior month
    # and invent changes that never happened — e.g. every bill not yet paid
    # this month reads as a "decrease". A missing finding is safer than a
    # confident false one.
    if mr.get("available") and mr.get("uses_complete_months"):
        month, prev = mr.get("month") or "", mr.get("prev_month") or ""
        period = f"{month} vs {prev}"
        month_start = date.fromisoformat(f"{month}-01")
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        drill_start = month_start.isoformat()
        drill_end = (next_month - timedelta(days=1)).isoformat()

        for mv in (mr.get("top_increases") or []):
            # Fixed commitments (mortgage, utilities) are contractual, not
            # behavioral. Their month-to-month total can move purely from
            # shared-split attribution or posting timing — verified: an
            # unchanged $2,622.45 mortgage read as "up $262" only because one
            # month's row carried a different personal share. Never editorialize
            # that as a spending decision.
            if mv.get("kind") == "fixed":
                continue
            delta = abs(float(mv.get("abs_change") or 0))
            if delta < MATERIALITY_THRESHOLD:
                continue
            cat = mv.get("category") or "Uncategorized"
            current_val = float(mv.get("current") or 0)
            previous_val = float(mv.get("previous") or 0)
            # Both months are complete here, so a $0 prior month is a genuine
            # new expense, not missing coverage — surface it factually.
            if previous_val <= 0:
                why = f"New this month: ${current_val:,.0f} in {cat}."
            else:
                why = (
                    f"${current_val:,.0f} this month vs ${previous_val:,.0f} "
                    f"last month ({mv.get('pct_change') or 0:+.0f}%)."
                )
            lead = _dominant_merchant(cat, month)
            if lead:
                why += f" Mostly {lead}."
            findings.append({
                "id": f"mover_up_{cat}",
                "kind": "category_increase",
                "title": f"{cat} up ${delta:,.0f} vs {prev}",
                "current": current_val,
                "previous": previous_val,
                "delta": delta,
                "direction": "up",
                "period": period,
                "confidence": "high",
                "why_it_matters": why,
                "drill": {"category": cat, "cashflow_role": "spending",
                          "start_date": drill_start, "end_date": drill_end},
            })

        for mv in (mr.get("top_decreases") or []):
            if mv.get("kind") == "fixed":
                continue
            delta = abs(float(mv.get("abs_change") or 0))
            if delta < MATERIALITY_THRESHOLD:
                continue
            cat = mv.get("category") or "Uncategorized"
            current_val = float(mv.get("current") or 0)
            previous_val = float(mv.get("previous") or 0)
            findings.append({
                "id": f"mover_down_{cat}",
                "kind": "reduced_spending",
                "title": f"{cat} down ${delta:,.0f} vs {prev}",
                "current": current_val,
                "previous": previous_val,
                "delta": delta,
                "direction": "down",
                "period": period,
                "confidence": "high",
                # Factual only. A lower number is not automatically a win: it
                # may be timing, a skipped bill, or an incomplete category, so
                # state the numbers and let the user decide.
                "why_it_matters": (
                    f"${current_val:,.0f} this month vs ${previous_val:,.0f} "
                    f"last month ({mv.get('pct_change') or 0:+.0f}%)."
                ),
                "drill": {"category": cat, "cashflow_role": "spending",
                          "start_date": drill_start, "end_date": drill_end},
            })
            break  # one decrease finding is enough

        net_delta = float(mr.get("net_delta") or 0)
        if net_delta >= MATERIALITY_THRESHOLD:
            findings.append({
                "id": "improved_savings",
                "kind": "improved_savings",
                "title": f"Net ${net_delta:,.0f} better than {prev}",
                "current": float(mr.get("net") or 0),
                "previous": float(mr.get("prev_net") or 0),
                "delta": net_delta,
                "direction": "up",
                "period": period,
                "confidence": "high",
                "why_it_matters": (
                    f"You kept {signed_money(mr.get('net'))} in {month} "
                    f"vs {signed_money(mr.get('prev_net'))} in {prev}."
                ),
                "drill": {"cashflow_role": "net",
                          "start_date": drill_start, "end_date": drill_end},
            })

    # Recurring price increases from active subscription candidates.
    for cand in (det.get("active_candidates") or []):
        if "price_increase" not in (cand.get("flags") or []):
            continue
        annual = float(cand.get("annual") or 0)
        if annual < MATERIALITY_THRESHOLD:
            continue
        merchant = cand.get("merchant") or "Recurring service"
        findings.append({
            "id": f"price_up_{merchant}",
            "kind": "recurring_price_increase",
            "title": f"{merchant} price increased",
            "current": float(cand.get("avg_amount") or 0),
            "previous": None,
            "delta": annual,
            "direction": "up",
            "period": f"last {cand.get('months_seen') or '?'} months",
            "confidence": "medium",
            "why_it_matters": (
                f"~${cand.get('avg_amount') or 0:,.0f}/mo recurring — "
                f"${annual:,.0f}/yr if it stays."
            ),
            "drill": {"merchant": merchant, "cashflow_role": "spending"},
        })

    findings.sort(key=lambda f: -abs(float(f.get("delta") or 0)))
    return findings[: max(1, limit)]


# ─────────────────────────────────────────────────────────────────────
# Recommended actions (1 primary + ≤1 secondary)
# ─────────────────────────────────────────────────────────────────────
def recommended_actions(conn: Optional[sqlite3.Connection] = None,
                        today=None,
                        *,
                        freshness: Optional[dict] = None,
                        sts: Optional[dict] = None,
                        progress: Optional[dict] = None,
                        changes: Optional[list] = None) -> list[dict]:
    """Deterministic action ladder. First match wins the primary slot;
    the next distinct match becomes the secondary. Never more than two.

    Ladder:
      1. Data stale/missing        → import
      2. Plan period ended / none  → update the plan
      3. Material flagged rows     → review them
      4. Category running hot      → slow that category
      5. Otherwise                 → stay within this week's number
    """
    t = _today(today)
    c, opened = _conn(conn)
    try:
        if freshness is None:
            freshness = data_freshness(conn=c, today=t)
        if sts is None:
            sts = safe_to_spend_summary(conn=c, today=t)
        if progress is None:
            progress = monthly_progress(conn=c, today=t)
        if changes is None:
            changes = meaningful_changes(conn=c)

        row = c.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(ABS(amount)), 0) AS total
            FROM transactions
            WHERE is_flagged = 1 AND ABS(amount) >= ?
            """,
            (MATERIALITY_THRESHOLD,),
        ).fetchone()
        flagged_n = int(row["n"] or 0)
        flagged_total = float(row["total"] or 0)
    finally:
        _close(c, opened)

    actions: list[dict] = []

    def _add(a: dict) -> None:
        if len(actions) < 2 and all(x["id"] != a["id"] for x in actions):
            actions.append(a)

    if freshness.get("state") in ("stale", "no_data"):
        detail = (freshness.get("detail") or "").rstrip(".")
        _add({
            "id": "import_data",
            "title": ("Import your first statement"
                      if freshness["state"] == "no_data"
                      else "Import your latest statement"),
            "why": (detail + ". Forecasts and findings are only as "
                    "good as the data behind them."),
            "page": "pages/3_Import.py",
            "label": "Open Import",
        })

    # Plan actions gate on ONE question: does this calendar month have a
    # confirmed plan? Once the user confirms it (a one-click rollover on
    # the Plan page), this action disappears until next month.
    current_calendar_month = t.strftime("%Y-%m")
    if not progress.get("has_current_plan"):
        if progress.get("has_plan"):
            # A previous plan exists — confirming is a one-click rollover.
            _add({
                "id": "update_plan",
                "title": f"Confirm your {current_calendar_month} plan",
                "why": "Your last plan carries forward — one click on the "
                       "Plan page keeps Safe to Spend meaningful.",
                "page": "pages/12_Month_Plan.py",
                "label": "Open Plan",
            })
        else:
            _add({
                "id": "save_plan",
                "title": f"Confirm a plan for {current_calendar_month}",
                "why": "Safe to Spend gets sharper with a saved income and "
                       "savings target. Takes about a minute.",
                "page": "pages/12_Month_Plan.py",
                "label": "Open Plan",
            })

    if (not sts.get("available")
            and "balance" in str(sts.get("reason") or "").lower()):
        _add({
            "id": "add_balances",
            "title": "Add current account balances",
            "why": "Safe to Spend stays unavailable until Northstar knows the cash and card balances you have now.",
            "page": "Insights",
            "label": "Open Insights",
        })

    if flagged_n > 0:
        _add({
            "id": "review_flagged",
            "title": (f"Review {flagged_n} flagged transaction"
                      f"{'s' if flagged_n != 1 else ''} "
                      f"worth ${flagged_total:,.0f}"),
            "why": "These rows are big enough to move your numbers.",
            "page": "pages/8_Review.py",
            "label": "Open Review",
        })

    if progress.get("risk_level") == "danger" and sts.get("available"):
        gap = abs(float(progress.get("savings_gap") or 0))
        _add({
            "id": "slow_pace",
            "title": "Slow flexible spending for the rest of the month",
            "why": (f"Current pace is projected to miss the savings target "
                    f"by ${gap:,.0f}. The remaining Safe to Spend amount is "
                    "a cap, not a pace endorsement."),
            "page": "pages/12_Month_Plan.py",
            "label": "Open Plan",
        })

    hot = next((f for f in (changes or [])
                if f.get("kind") == "category_increase"), None)
    if hot:
        cat = (hot.get("drill") or {}).get("category") or "that category"
        period_ended = bool(sts.get("period_ended"))
        _add({
            "id": f"slow_{cat}",
            "title": (
                f"Review the increase in {cat}"
                if period_ended else
                f"Slow {cat} down for the rest of the month"
            ),
            "why": hot.get("why_it_matters") or "",
            "page": "pages/2_Transactions.py",
            "label": f"See {cat} transactions",
            "category": cat,
        })

    if not actions and sts.get("available"):
        amt = float(sts.get("amount") or 0)
        if amt < 0:
            # Never tell the user to "stay within" a negative number.
            _add({
                "id": "pause_flex",
                "title": ("Pause flexible spending — the plan is "
                          f"exceeded by ${abs(amt):,.0f}"),
                "why": "Cover fixed bills only until the next income "
                       "arrives or the plan is updated.",
                "page": "pages/12_Month_Plan.py",
                "label": "Open Plan",
            })
        else:
            wk = float(sts.get("weekly_amount") or 0)
            _add({
                "id": "stay_within",
                "title": (f"No urgent action — stay within ${wk:,.0f} "
                          f"this week"),
                "why": "Bills, savings, and a buffer are already reserved.",
                "page": "",
                "label": "",
            })

    return actions[:2]


# ─────────────────────────────────────────────────────────────────────
# Verdict + full packet
# ─────────────────────────────────────────────────────────────────────
def _verdict(freshness: dict, sts: dict, progress: dict,
             review: Optional[dict], calendar_month: str = "") -> dict:
    """One authoritative status + one plain-English sentence."""
    if freshness.get("state") == "no_data":
        return {
            "state": "no_data",
            "headline": "No data yet",
            "sentence": "Import your first statement and Ledger will take "
                        "it from there.",
        }
    if freshness.get("state") == "stale":
        if review and review.get("available"):
            net = float(review.get("net") or 0)
            month = str(review.get("month") or "the latest complete month")
            if net >= 0:
                headline = f"Your latest complete month kept ${net:,.0f}"
                result = f"${net:,.0f} more came in than went out"
            else:
                headline = (
                    f"Your latest complete month ran ${abs(net):,.0f} short"
                )
                result = f"${abs(net):,.0f} more went out than came in"
            return {
                "state": "stale_data",
                "headline": headline,
                "sentence": (
                    f"Based on {month}, {result}. "
                    f"{freshness.get('detail') or 'The data is out of date'} "
                    "Import your latest statement to refresh the forecast."
                ),
            }
        return {
            "state": "stale_data",
            "headline": "Data is too stale for a reliable read",
            "sentence": (freshness.get("detail") or "") +
                        " Import your latest statement to refresh the "
                        "forecast.",
        }
    # The data month can lag the real calendar (e.g. it's July but the
    # newest statement is June). From today's perspective that planning
    # period is over — say so instead of critiquing a finished month.
    data_month = sts.get("month") or progress.get("month") or ""
    if sts.get("period_ended") or (
        calendar_month and data_month and data_month < calendar_month
    ):
        # The newest statement describes a month that is over. That is a fact
        # about the old month, and this screen is for planning the one we are
        # in. Leading with "this planning period has ended" answered a
        # question nobody asked and left the current month unmentioned.
        closed = ""
        if review and review.get("available"):
            closed = (f" {_month_name(review['month'])} closed with "
                      f"{signed_money(review.get('net'))} kept.")
        current_label = _month_name(calendar_month) or "This month"
        if not (calendar_month and data_month and data_month < calendar_month):
            # The data is for the month we are in, and that month has simply
            # run out of days. Nothing newer is missing, so the old wording
            # is the right one: the period really has ended.
            return {
                "state": "period_ended",
                "headline": "This planning period has ended",
                "sentence": f"Start or update your next plan.{closed}",
            }
        if progress.get("has_current_plan"):
            return {
                "state": "awaiting_current_data",
                "headline": f"{current_label} plan is ready",
                "sentence": (f"No {current_label} transactions have been "
                             f"imported yet, so there is nothing to track "
                             f"against it.{closed}"),
            }
        return {
            "state": "awaiting_current_data",
            "headline": f"{current_label} plan ready to review",
            "sentence": (f"No {current_label} transactions have been "
                         f"imported yet.{closed}"),
        }
    if not sts.get("available"):
        return {
            "state": "insufficient",
            "headline": "Not enough data for a forecast yet",
            "sentence": sts.get("reason") or "Import more statements.",
        }
    amount = float(sts.get("amount") or 0)
    is_flow = (sts.get("reserved") or {}).get("basis") == "flow"
    if amount < 0:
        return {
            "state": "behind",
            "headline": "Over the current plan",
            "sentence": (f"You are projected to exceed the current plan "
                         f"by ${abs(amount):,.0f}. See what changed "
                         f"below before deciding what to pause."),
        }
    risk = progress.get("risk_level") or "insufficient_data"
    savings_gap = float(progress.get("savings_gap") or 0)
    if risk == "danger":
        return {
            "state": "behind",
            "headline": "Spending is running over your plan",
            "sentence": (
                (f"${amount:,.0f} is left for everyday spending this month, "
                 f"but recent pace projects a ${abs(savings_gap):,.0f} "
                 f"savings shortfall. Ease off or adjust the plan.")
                if is_flow else
                (f"${amount:,.0f} is the lower of flexible room and "
                 f"cash-backed room, but current pace projects a "
                 f"${abs(savings_gap):,.0f} savings shortfall. Slow "
                 f"flexible spending or update the plan.")
            ),
        }
    if sts.get("status") in ("tight", "watch") or risk == "watch":
        return {
            "state": "tight",
            "headline": "On plan, but the margin is thin",
            "sentence": (
                f"${amount:,.0f} is left for everyday spending this month "
                "after recurring costs, savings, and what you have spent."
                if is_flow else
                f"${amount:,.0f} is safe now after both the flexible-plan "
                "limit and cash-backed reservations are applied."
            ),
        }
    return {
        "state": "on_track",
        "headline": "You are on track this month",
        "sentence": (
            f"${amount:,.0f} is left for everyday spending this month, after "
            "recurring costs, savings, and what you have already spent."
            if is_flow else
            f"${amount:,.0f} is the lower of remaining flexible allowance "
            "and the cash cushion after bills."
        ),
    }


def weekly_checkin(conn: Optional[sqlite3.Connection] = None,
                   today=None) -> dict:
    """The complete deterministic packet for the Home page."""
    from utils.database import get_last_checkin
    from utils.goals import home_goal_summary
    from utils.insights import monthly_review

    t = _today(today)
    c, opened = _conn(conn)
    try:
        freshness = data_freshness(conn=c, today=t)
        sts = safe_to_spend_summary(conn=c, today=t)
        progress = monthly_progress(conn=c, today=t)
        changes = meaningful_changes(conn=c)
        goal = home_goal_summary(conn=c, today=t)
        review = monthly_review(conn=c) or {}
        actions = recommended_actions(
            conn=c, today=t, freshness=freshness, sts=sts,
            progress=progress, changes=changes,
        )
        last = get_last_checkin(conn=c)
    finally:
        _close(c, opened)

    return {
        "generated_for": t.isoformat(),
        "freshness": freshness,
        "safe_to_spend": sts,
        "progress": progress,
        "goal": goal,
        "changes": changes,
        "monthly_review": {
            "available": bool(review.get("available")),
            "month": review.get("month") or "",
            "prev_month": review.get("prev_month") or "",
            "net": float(review.get("net") or 0),
            "net_delta": float(review.get("net_delta") or 0),
            "spending_delta": float(review.get("spending_delta") or 0),
            "savings_rate": float(review.get("savings_rate") or 0),
        },
        "actions": actions,
        "verdict": _verdict(freshness, sts, progress, review,
                            calendar_month=t.strftime("%Y-%m")),
        "last_checkin": last,
        "materiality_threshold": MATERIALITY_THRESHOLD,
    }

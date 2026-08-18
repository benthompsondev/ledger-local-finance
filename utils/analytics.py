"""
Analytics layer — cash flow, spending score, category summaries, trends.
All functions accept an optional sqlite3.Connection to avoid repeated opens.

Financial model:
  ``amount`` preserves the signed account-relative movement from the source.
  ``transaction_type`` determines income, spending, and excluded plumbing.
  Refund-like credits are income and never rewrite category spending.
"""
from datetime import date, datetime, timedelta
from typing import Optional
import sqlite3
from statistics import median

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
        net = r.get("net", r["income"] - r["spending"])
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
# NOTE: Interac e-transfers are cash flow unless the canonical type records an
# explicit internal transfer; category labels never decide totals by themselves.
_ALWAYS_EXCLUDE_DIRECTIONS = ("payment", "cancelled", "transfer")
_ALWAYS_EXCLUDE_CATEGORIES = ("Credit Card Payment", "Cancelled")

REVIEWED_RECURRING_STATUSES = frozenset({
    "confirmed", "recurring", "excluded", "not_recurring",
})


def recurring_status_is_reviewed(status: object) -> bool:
    """Return whether a recurring decision is explicitly resolved."""
    return str(status or "").strip().lower() in REVIEWED_RECURRING_STATUSES


def compute_cashflow(
    start_date: str,
    end_date: str,
    exclude_transfers: bool = False,
    account_type: Optional[str] = None,
    share_view: str = "personal",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Single shared function used by Dashboard, Spending, and Income pages.

    The canonical transaction type determines whether a row is income,
    spending, refund income, or excluded financial plumbing. Stored amount
    signs and user-facing category labels do not independently determine flow.

    ``exclude_transfers`` remains for API compatibility. Canonical internal,
    investment, card-payment, and personal-transfer types are always excluded.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    params: list = [start_date, end_date]
    acct_filter = ""
    if account_type and account_type != "all":
        acct_filter = " AND account_type = ?"
        params.append(account_type)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + acct_filter, params,
    ).fetchall()]
    from utils.financial_semantics import cashflow_totals
    from utils.shared_expenses import apply_shared_view
    result = cashflow_totals(
        apply_shared_view(rows, conn, share_view), view=share_view
    )
    if close:
        conn.close()
    return result


def period_cashflow(start_date: str, end_date: str, conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Backwards-compatible wrapper — calls compute_cashflow() with default settings.
    All callers should migrate to compute_cashflow() directly.
    """
    return compute_cashflow(start_date, end_date, exclude_transfers=False, conn=conn)


def _employment_income_months(
    conn: sqlite3.Connection, *, exclude_month: str = "",
) -> list[dict]:
    """Aggregate payroll history without assuming legacy rows were migrated.

    Older Northstar databases can have a blank ``transaction_type`` even when
    the signed amount, description, category, and account type are intact. Use
    the shared semantic classifier for those rows so an upgraded profile gets
    the same payroll suggestion as a freshly imported profile.
    """
    from utils.financial_semantics import classify_transaction_type

    rows = [dict(row) for row in conn.execute(
        "SELECT transaction_date,merchant_normalized,merchant,raw_description,"
        "account_type,direction,category,category_source,transaction_type,amount "
        "FROM transactions WHERE amount > 0 ORDER BY transaction_date"
    ).fetchall()]
    grouped: dict[tuple[str, str], dict] = {}
    user_sources = {"user_edit", "user_bulk", "user_rule", "user"}
    for row in rows:
        month = str(row.get("transaction_date") or "")[:7]
        if not month or month == exclude_month:
            continue
        transaction_type = str(row.get("transaction_type") or "")
        if not transaction_type:
            transaction_type = classify_transaction_type(
                str(row.get("raw_description") or ""),
                str(row.get("account_type") or ""),
                str(row.get("direction") or ""),
                str(row.get("category") or ""),
            )
        if transaction_type != "employment_income":
            continue
        source_key = str(
            row.get("merchant_normalized") or row.get("merchant")
            or row.get("raw_description") or "PAYROLL"
        )
        key = (month, source_key)
        aggregate = grouped.setdefault(key, {
            "month": month,
            "source_normalized": source_key,
            "source": str(
                row.get("merchant") or row.get("raw_description")
                or "Payroll"
            ),
            "deposits": 0,
            "total": 0.0,
            "inferred_source": 0,
        })
        aggregate["deposits"] += 1
        aggregate["total"] += abs(float(row.get("amount") or 0))
        if str(row.get("category_source") or "") not in user_sources:
            aggregate["inferred_source"] = 1
    return sorted(grouped.values(), key=lambda row: str(row["month"]))


def stable_income_summary(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return a conservative monthly stable-income baseline.

    Only recurring employment-income sources from completed calendar months
    are considered. A saved plan is an intention, not evidence of income, and
    must never redefine this historical baseline. Users can explicitly confirm
    or exclude a recognized source in Settings.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    # The month in progress, which is excluded from "completed months".
    # Read from the supported anchor so one far-future row cannot make every
    # real month look historical.
    from utils.analysis_period import supported_latest_date
    latest = supported_latest_date(conn=conn)
    current_month = latest.strftime("%Y-%m") if latest else ""
    preferences = {
        r["source_normalized"]: r["status"]
        for r in conn.execute(
            "SELECT source_normalized,status FROM income_source_preferences"
        ).fetchall()
    }
    rows = _employment_income_months(conn, exclude_month=current_month)
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(str(row["source_normalized"]), []).append(row)
    recurring_counts = {
        source_key: len(source_rows)
        for source_key, source_rows in by_source.items()
        if len(source_rows) >= 2
    }
    max_months = max(recurring_counts.values(), default=0)
    automatic_stable = {
        source_key for source_key, count in recurring_counts.items()
        if count == max_months and count >= 3
    }
    eligible: dict[str, list[dict]] = {}
    for source_key, source_rows in by_source.items():
        preference = preferences.get(source_key, "")
        if preference == "excluded":
            continue
        inferred = any(int(row.get("inferred_source") or 0) for row in source_rows)
        if preference == "confirmed" or (
            source_key in automatic_stable and not inferred
        ):
            eligible[source_key] = source_rows

    month_totals: dict[str, float] = {}
    source_items = []
    for source_key, source_rows in eligible.items():
        values = [float(row["total"] or 0) for row in source_rows]
        for row in source_rows:
            month_totals[str(row["month"])] = (
                month_totals.get(str(row["month"]), 0.0)
                + float(row["total"] or 0)
            )
        source_items.append({
            "source": source_rows[0]["source"],
            "source_normalized": source_key,
            "monthly_amount": round(median(values), 2),
            "months_used": len(values),
            "status": preferences.get(source_key, "confirmed"),
        })
    totals = [value for _, value in sorted(month_totals.items()) if value > 0]
    confirmed = len(totals) >= 2 or bool(source_items and any(
        item["status"] == "confirmed" for item in source_items
    ))
    result = {
        "monthly_amount": round(median(totals), 2) if totals else 0.0,
        "basis": "payroll_history" if confirmed else "unavailable",
        "label": "Confirmed recurring payroll" if confirmed else "Not confirmed yet",
        "months_used": len(totals),
        "confirmed": confirmed,
        "sources": sorted(
            source_items, key=lambda item: -float(item["monthly_amount"])
        ),
    }
    if close:
        conn.close()
    return result


def reliable_income_summary(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Which income sources look reviewed and recurring, and their sizes.

    **This is review state, not the planning amount.** The monthly plan's
    income is ``typical_monthly_income``; so is Safe to Spend's. Only
    ``confirmed`` here is load-bearing, as the signal that income has been
    reviewed at all. Nothing may reuse ``monthly_amount`` as a planning
    figure — until 2.7.1 ``save_plan`` did, and because the two numbers are
    built differently, any gap of a cent rejected the save outright.

    This is intentionally broader than ``stable_income_summary``. Payroll is
    still reported as payroll, while recurring interest and recurring
    non-payroll deposits (including an incoming e-transfer that repeats
    reliably) may also support the plan. Refunds, reimbursements, card
    payments, investment movements, and one-off windfalls are never eligible.

    Automatic sources need at least three complete historical months with
    amounts within 35% of their median. A user-confirmed source can be used
    after two complete months.

    One property matters and is easy to misread as conservatism: each source
    contributes the median of the months **it actually appeared in**, not of
    the window. A source arriving in three months out of six therefore
    contributes its full monthly amount as though it arrived in all six, so
    this total runs *above* the average month for anyone with irregular
    deposits. `test_reliable_income_overstates_an_intermittent_source` pins
    that. It is the reason this figure must never price a month.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        from utils.financial_semantics import (
            classify_transaction_type, transaction_types_for_role,
        )

        from utils.analysis_period import supported_latest_date
        _anchor = supported_latest_date(conn=conn)
        current_month = _anchor.strftime("%Y-%m") if _anchor else ""
        preferences = {
            str(row["source_normalized"]): str(row["status"])
            for row in conn.execute(
                "SELECT source_normalized,status FROM income_source_preferences"
            ).fetchall()
        }
        stable_payroll = stable_income_summary(conn=conn)
        stable_payroll_keys = {
            str(source.get("source_normalized") or "")
            for source in stable_payroll.get("sources") or []
        }
        planning_types = set(transaction_types_for_role("planning_income"))
        rows = [dict(row) for row in conn.execute(
            "SELECT transaction_date,merchant_normalized,merchant,"
            "raw_description,account_type,direction,category,"
            "transaction_type,amount FROM transactions "
            "WHERE amount > 0 ORDER BY transaction_date"
        ).fetchall()]

        grouped: dict[str, dict] = {}
        from utils.insights import statement_coverage
        historical_months = [
            month for month in (
                statement_coverage(conn=conn).get("complete_months") or []
            )
            if month != current_month
        ][-6:]
        allowed_months = set(historical_months)
        for row in rows:
            month = str(row.get("transaction_date") or "")[:7]
            if month not in allowed_months:
                continue
            transaction_type = str(row.get("transaction_type") or "")
            if not transaction_type:
                transaction_type = classify_transaction_type(
                    str(row.get("raw_description") or ""),
                    str(row.get("account_type") or ""),
                    str(row.get("direction") or ""),
                    str(row.get("category") or ""),
                )
            if transaction_type not in planning_types:
                continue
            source_key = str(
                row.get("merchant_normalized") or row.get("merchant")
                or row.get("raw_description") or "INCOME"
            )
            source = grouped.setdefault(source_key, {
                "source_normalized": source_key,
                "source": str(
                    row.get("merchant") or row.get("raw_description")
                    or "Income"
                ),
                "transaction_type": transaction_type,
                "months": {},
            })
            source["months"][month] = (
                float(source["months"].get(month, 0.0))
                + abs(float(row.get("amount") or 0))
            )

        sources = []
        for source_key, source in grouped.items():
            preference = preferences.get(source_key, "")
            if preference == "excluded":
                continue
            values = [
                float(value) for _month, value
                in sorted(source["months"].items())
            ]
            if not values:
                continue
            midpoint = median(values)
            consistent = bool(
                midpoint > 0
                and max(abs(value - midpoint) / midpoint for value in values)
                <= 0.35
            )
            is_payroll = source["transaction_type"] == "employment_income"
            automatic = (
                source_key in stable_payroll_keys if is_payroll
                else len(values) >= 3 and consistent
            )
            explicitly_confirmed = preference == "confirmed"
            if not automatic and not (
                explicitly_confirmed and len(values) >= 2
            ):
                continue
            sources.append({
                "source": source["source"],
                "source_normalized": source_key,
                "monthly_amount": round(midpoint, 2),
                "months_used": len(values),
                "status": "confirmed" if explicitly_confirmed else "automatic",
                "kind": (
                    "Payroll" if is_payroll
                    else "Interest" if source["transaction_type"] == "interest_income"
                    else "Recurring money in"
                ),
            })

        amount = round(sum(
            float(source["monthly_amount"]) for source in sources
        ), 2)
        return {
            "monthly_amount": amount,
            "basis": "median_recurring_income" if sources else "unavailable",
            "label": (
                "Reliable monthly income floor"
                if sources else "Reliable income not confirmed yet"
            ),
            "months_used": max(
                (int(source["months_used"]) for source in sources), default=0
            ),
            "confirmed": bool(sources),
            "sources": sorted(
                sources, key=lambda source: -float(source["monthly_amount"])
            ),
        }
    finally:
        if close:
            conn.close()


TYPICAL_INCOME_MAX_MONTHS = 6


def _planning_income_deposits(conn) -> list[dict]:
    """Every deposit that counts as planning income, oldest first.

    One definition of "income worth planning around", shared by the
    monthly baseline and the scheduled-arrival outlook below, so the two can
    never disagree about what a payroll deposit is. Refunds, reimbursements,
    card payments and transfers are excluded by the ``planning_income`` role;
    recurring interest and incoming e-transfers are included, because for
    many people they are real income.

    A source the user marked "exclude from stable income" is dropped here,
    which is the only place that decision is applied. Through 2.8.0 the
    control wrote its row and nothing read it for planning:
    `reliable_income_summary` honoured it, but that function stopped pricing
    anything in 2.7.2, so the only visible effect of saying a deposit was not
    income was the button changing colour. Dropping the deposit here keeps one
    planning
    figure — the exclusion changes the input to `typical_monthly_income`, it
    does not add a second opinion about the amount — and keeps the baseline
    and the arrival outlook agreeing about what income is.

    Confirming a source deliberately does nothing here. Its deposits are in
    the data either way, and a whole-month average has no eligibility gate to
    open. Only exclusion removes money.
    """
    from utils.financial_semantics import (
        classify_transaction_type, transaction_types_for_role,
    )

    # Uppercased on both sides: `merchant_normalized` is already an uppercase
    # key, but a source recorded from a bare merchant or description is not,
    # and the preference row stores whatever key the screen showed.
    excluded = {
        str(row["source_normalized"] or "").strip().upper()
        for row in conn.execute(
            "SELECT source_normalized FROM income_source_preferences "
            "WHERE status='excluded'"
        ).fetchall()
    }
    planning_types = set(transaction_types_for_role("planning_income"))
    deposits: list[dict] = []
    rows = conn.execute(
        "SELECT transaction_date,raw_description,merchant,merchant_normalized,"
        "account_type,direction,category,transaction_type,amount "
        "FROM transactions WHERE amount > 0"
    ).fetchall()
    for row in rows:
        day = str(row["transaction_date"] or "")[:10]
        if not day:
            continue
        transaction_type = str(row["transaction_type"] or "")
        if not transaction_type:
            transaction_type = classify_transaction_type(
                str(row["raw_description"] or ""),
                str(row["account_type"] or ""),
                str(row["direction"] or ""),
                str(row["category"] or ""),
            )
        if transaction_type not in planning_types:
            continue
        source = str(
            row["merchant_normalized"] or row["merchant"]
            or row["raw_description"] or ""
        ).strip().upper() or "UNKNOWN"
        if source in excluded:
            continue
        deposits.append({
            "date": day,
            "source": source,
            "label": str(row["merchant"] or row["raw_description"] or source),
            "amount": abs(float(row["amount"] or 0)),
            "transaction_type": transaction_type,
        })
    deposits.sort(key=lambda item: item["date"])
    return deposits


def _planning_income_by_month(conn) -> dict[str, float]:
    """Planning income totalled per calendar month."""
    totals: dict[str, float] = {}
    for deposit in _planning_income_deposits(conn):
        month = deposit["date"][:7]
        totals[month] = totals.get(month, 0.0) + deposit["amount"]
    return totals


# How far past its usual date a deposit may drift before Plan stops counting
# on it. Payroll slips for weekends and bank holidays; five days covers that
# without pretending a deposit a fortnight late is merely running behind.
INCOME_GRACE_DAYS = 5

# A source has to have arrived this often before its rhythm is worth planning
# around. Two deposits is a coincidence, not a payday.
_MIN_INCOME_OCCURRENCES = 3


def _income_schedule(deposits: list[dict], before: date) -> list[dict]:
    """Sources paying on a recognisable rhythm, judged on history alone.

    Reuses ``recurring_cadence.classify_gap`` — the same gap classifier the
    bills side uses — rather than adding a second opinion about what counts
    as regular. Anything irregular is left out entirely: a one-off inheritance
    raises the monthly average, and treating that average as a payday means
    telling someone money is coming when nobody ever promised it.
    """
    from utils.recurring_cadence import classify_gap

    grouped: dict[str, list[dict]] = {}
    for deposit in deposits:
        if date.fromisoformat(deposit["date"]) >= before:
            continue
        grouped.setdefault(deposit["source"], []).append(deposit)

    schedule: list[dict] = []
    for source, rows in grouped.items():
        if len(rows) < _MIN_INCOME_OCCURRENCES:
            continue
        days = [date.fromisoformat(row["date"]) for row in rows]
        gaps = [
            (days[i] - days[i - 1]).days for i in range(1, len(days))
        ]
        gaps = [gap for gap in gaps if gap > 0]
        if not gaps:
            continue
        typical_gap = float(median(gaps))
        cadence, period_months = classify_gap(typical_gap)
        # Only rhythms that pay at least once a month can be placed inside a
        # single month with any confidence. A quarterly bonus is real income
        # but it is not a payday, so it never props up the outlook.
        if cadence == "irregular" or period_months > 1:
            continue
        deviations = [abs(gap - typical_gap) for gap in gaps]
        if median(deviations) > max(4.0, typical_gap * 0.35):
            continue
        amounts = [row["amount"] for row in rows[-6:]]
        schedule.append({
            "source": source,
            "label": rows[-1]["label"],
            "cadence": cadence,
            "typical_gap_days": round(typical_gap, 1),
            "amount": round(float(median(amounts)), 2),
            "last_seen": rows[-1]["date"],
            "day_of_month": int(median([day.day for day in days[-6:]])),
            "occurrences": len(rows),
        })
    schedule.sort(key=lambda row: -row["amount"])
    return schedule


def _expected_dates(entry: dict, month: str) -> list[date]:
    """When this source is expected to pay inside ``month``."""
    import calendar as _cal

    year, number = int(month[:4]), int(month[5:7])
    last_day = _cal.monthrange(year, number)[1]
    month_start, month_end = date(year, number, 1), date(year, number, last_day)

    if entry["cadence"] == "monthly":
        # A monthly salary keeps its day of the month; stepping forward by a
        # 30-day gap would drift a day earlier every month until it fell out
        # of the month entirely.
        return [date(year, number, min(entry["day_of_month"], last_day))]

    # Weekly, biweekly and twice-monthly rhythms have no fixed date, so walk
    # forward from the last known deposit at the observed interval.
    step = max(1, int(round(entry["typical_gap_days"])))
    when = date.fromisoformat(entry["last_seen"])
    dates: list[date] = []
    while when < month_start:
        when += timedelta(days=step)
    while when <= month_end:
        dates.append(when)
        when += timedelta(days=step)
    return dates


def expected_income_events(
    start: date,
    end: date,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Trustworthy paydays expected between two dates.

    Reuses the schedule the plan forecast already trusts: the same deposits,
    the same gap classifier, the same refusal to treat an irregular arrival
    as a payday. Nothing here predicts a new source of income; it only says
    when a rhythm that has been running is next due to run.

    The planning baseline can legitimately include repeating interest or
    other cash income without claiming it has a scheduled arrival. Radar has
    a narrower burden: employment income is eligible automatically, while any
    other source needs an explicit ``confirmed`` / "Use as income" decision.
    Excluded sources were already removed by ``_planning_income_deposits``.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        deposits = _planning_income_deposits(conn)
        preferences = {
            str(row["source_normalized"] or "").strip().upper():
                str(row["status"] or "")
            for row in conn.execute(
                "SELECT source_normalized,status "
                "FROM income_source_preferences"
            ).fetchall()
        }
    finally:
        if close:
            conn.close()

    deposits = [
        deposit for deposit in deposits
        if deposit.get("transaction_type") == "employment_income"
        or preferences.get(
            str(deposit["source"]).strip().upper()
        ) == "confirmed"
    ]
    schedule = _income_schedule(deposits, before=start)
    if not schedule:
        return []

    # The last few real deposits per source, so an expected payday can show
    # what its figure was drawn from rather than asserting an amount.
    recent: dict[str, list[dict]] = {}
    for deposit in deposits:
        recent.setdefault(deposit["source"], []).append(deposit)

    events: list[dict] = []
    for entry in schedule:
        history = [
            {"date": row["date"], "amount": round(float(row["amount"]), 2)}
            for row in recent.get(entry["source"], [])[-3:]
        ]
        for month in _months_spanned(start, end):
            for when in _expected_dates(entry, month):
                if start <= when <= end:
                    events.append({
                        "source": entry["source"],
                        "label": entry["label"],
                        "cadence": entry["cadence"],
                        "typical_gap_days": entry["typical_gap_days"],
                        "amount": entry["amount"],
                        "last_seen": entry["last_seen"],
                        "occurrences": entry["occurrences"],
                        "recent": history,
                        "expected_date": when.isoformat(),
                    })
    events.sort(key=lambda row: (row["expected_date"], -row["amount"]))
    return events


def _months_spanned(start: date, end: date) -> list[str]:
    """Every YYYY-MM the window touches, so a window can cross a year end."""
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return months


def scheduled_income_outlook(
    month: str,
    *,
    anchor: date,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """How much of this month's expected income has actually turned up.

    Plan used to treat the whole historical monthly baseline as still coming,
    however late in the month it was. A household whose second payroll never
    arrived was told it was on track on the 28th, because the missing $2,500
    was quietly counted as though it had been received. A budgeting app
    telling someone they have money they do not have is the worst failure it
    has available.

    Returns received, the part still credibly ahead, and the part that was
    due and did not arrive. Nothing here changes what Safe to Spend counts:
    that stays balances minus confirmed reservations.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        deposits = _planning_income_deposits(conn)
    finally:
        if close:
            conn.close()

    month_start = date(int(month[:4]), int(month[5:7]), 1)
    received = round(sum(
        deposit["amount"] for deposit in deposits
        if month_start <= date.fromisoformat(deposit["date"]) <= anchor
    ), 2)

    schedule = _income_schedule(deposits, before=month_start)
    if not schedule:
        return {
            "received": received,
            "scheduled_total": 0.0,
            "due_by_now": 0.0,
            "expected_remaining": 0.0,
            "missed": 0.0,
            "scheduled_detected": False,
            "sources": [],
            "note": "",
        }

    scheduled_total = 0.0
    due_by_now = 0.0
    overdue_labels: list[str] = []
    detail: list[dict] = []
    for entry in schedule:
        when_dates = _expected_dates(entry, month)
        scheduled_total += entry["amount"] * len(when_dates)
        past_due = [
            when for when in when_dates
            if when + timedelta(days=INCOME_GRACE_DAYS) <= anchor
        ]
        due_by_now += entry["amount"] * len(past_due)
        if past_due:
            overdue_labels.append(entry["label"])
        detail.append({
            "source": entry["label"],
            "cadence": entry["cadence"],
            "amount": entry["amount"],
            "expected_dates": [when.isoformat() for when in when_dates],
            "due_by_now": round(entry["amount"] * len(past_due), 2),
        })

    # Reconciled in total rather than deposit by deposit, so an employer whose
    # statement descriptor changes is not reported as a missed payday. What
    # matters is whether as much money arrived as the rhythm says it should
    # have by now.
    missed = max(0.0, round(due_by_now - received, 2))
    expected_remaining = max(
        0.0, round(scheduled_total - max(received, due_by_now), 2)
    )

    note = ""
    if missed > 0:
        who = overdue_labels[0] if overdue_labels else "Expected income"
        note = (
            f"${missed:,.0f} that usually arrives by now has not been "
            f"imported ({who}). It is not counted as income until it does."
        )
    elif expected_remaining > 0:
        note = (
            f"${expected_remaining:,.0f} of usual income is still expected "
            f"before month end. It is not available to spend yet."
        )

    return {
        "received": received,
        "scheduled_total": round(scheduled_total, 2),
        "due_by_now": round(due_by_now, 2),
        "expected_remaining": expected_remaining,
        "missed": missed,
        "scheduled_detected": True,
        "sources": detail,
        "note": note,
    }


def complete_month_income(
    conn: Optional[sqlite3.Connection] = None,
) -> list[tuple[str, float]]:
    """Planning income per complete month, oldest first.

    Only whole months count, so a half-imported month cannot drag the figure
    down. A complete month that happened to earn nothing is reported as zero
    rather than omitted: for seasonal, contract and gig income the quiet
    months are the whole point, and dropping them quotes a typical month that
    only describes the good ones.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        from utils.insights import statement_coverage

        complete = statement_coverage(conn=conn).get("complete_months") or []
        totals = _planning_income_by_month(conn)
        return [
            (month, round(float(totals.get(month, 0.0)), 2))
            for month in sorted(complete)
        ]
    finally:
        if close:
            conn.close()


def _partial_month_income(conn) -> list[tuple[str, float]]:
    """Fallback when no month is fully imported yet.

    Uses every month except the one still in progress, so a half-finished
    current month never sets the baseline. Worth less confidence than a
    complete-month read, but far better than refusing to show a number.
    """
    from utils.insights import statement_coverage

    totals = _planning_income_by_month(conn)
    in_progress = str(
        statement_coverage(conn=conn).get("latest_data_month") or ""
    )
    return [
        (month, round(total, 2))
        for month, total in sorted(totals.items())
        if month != in_progress
    ]


def typical_monthly_income(
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """The adaptive typical-income baseline behind the monthly plan.

    This reads whole months, unlike ``reliable_income_summary``, which sums a
    median per income source. That older shape counted a source arriving in
    three months out of six as if it arrived every month, and dropped
    automatically recognized payroll until the user confirmed it in Settings.
    Reading complete-month totals removes both problems.

    The window grows with the history available:

      1 complete month     the month itself, low confidence
      2 to 5 months        the average of all of them
      6 or more            the average of the six most recent

    The average of every complete month, including months that earned
    nothing. The current partial month is never included, and the months
    actually used are returned so a screen can show its work.

    A source the user excluded is gone from every month in the window, past
    ones included, because this is a rolling read of history rather than a
    stored figure. Saved plans keep the income they were saved with, so Last
    Plan Result still compares the intention that was actually made against
    the money that actually arrived.
    """
    import calendar as _cal

    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        months = complete_month_income(conn=conn)
        partial_basis = False
        if not months:
            months = _partial_month_income(conn)
            partial_basis = True
    finally:
        if close:
            conn.close()
    if not months:
        return {
            "amount": 0.0, "months_used": 0, "months": [], "first_month": "",
            "last_month": "", "confidence": "none",
            "basis_label": "Not enough months of history yet",
        }
    window = months[-TYPICAL_INCOME_MAX_MONTHS:]
    values = [total for _month, total in window]
    amount = round(sum(values) / len(values), 2)
    first_month, last_month = window[0][0], window[-1][0]

    def _name(month: str) -> str:
        return _cal.month_name[int(month[5:7])]

    count = len(window)
    noun = "partly imported month" if partial_basis else "complete month"
    if count == 1:
        confidence = "low"
        basis_label = f"Your only {noun}, {_name(first_month)}"
    else:
        confidence = (
            "low" if partial_basis else "medium" if count < 3 else "high"
        )
        basis_label = (
            f"Average of {count} {noun}s, "
            f"{_name(first_month)} to {_name(last_month)}"
        )
    return {
        "amount": amount,
        "months_used": count,
        # Exactly what was averaged, so a screen can show its work and a
        # reviewer can reconcile the figure without re-deriving it.
        "months": [
            {"month": month, "income": income} for month, income in window
        ],
        "first_month": first_month,
        "last_month": last_month,
        "confidence": confidence,
        "basis_label": basis_label,
    }


def income_confirmation_candidates(
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Stable inferred payroll patterns that need one explicit confirmation."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    preferences = {
        row["source_normalized"]: row["status"]
        for row in conn.execute(
            "SELECT source_normalized,status FROM income_source_preferences"
        ).fetchall()
    }
    from utils.analysis_period import supported_latest_date
    _anchor = supported_latest_date(conn=conn)
    rows = _employment_income_months(
        conn, exclude_month=_anchor.strftime("%Y-%m") if _anchor else "")
    for row in rows:
        row["monthly_total"] = row["total"]
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(str(row["source_normalized"]), []).append(row)
    candidates = []
    for source_key, source_rows in by_source.items():
        if preferences.get(source_key) in {"confirmed", "excluded"}:
            continue
        if len(source_rows) < 3 or not any(
            int(row.get("inferred_source") or 0) for row in source_rows
        ):
            continue
        totals = [float(row.get("monthly_total") or 0) for row in source_rows]
        midpoint = median(totals)
        if midpoint <= 0 or max(abs(total - midpoint) / midpoint for total in totals) > 0.35:
            continue
        deposits = sum(int(row.get("deposits") or 0) for row in source_rows)
        per_month = deposits / len(source_rows)
        candidates.append({
            "source_normalized": source_key,
            "source": source_rows[-1].get("source") or "Payroll deposit",
            "monthly_amount": round(midpoint, 2),
            "months_seen": len(source_rows),
            "deposits_seen": deposits,
            "cadence": "about every two weeks" if per_month >= 1.5 else "monthly",
            "reason": (
                f"{deposits} similar deposits across {len(source_rows)} months; "
                "confirm before Northstar uses this for planning."
            ),
        })
    if close:
        conn.close()
    return sorted(candidates, key=lambda row: -float(row["monthly_amount"]))


# ──────────────────────────────────────────────
# Category breakdown
# ──────────────────────────────────────────────

def spending_by_category(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    share_view: str = "personal",
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Returns [{category, total, tx_count, pct}] sorted by total descending.
    Only debits, no transfers.  account_type optional filter.
    """
    rows = get_category_totals(
        start_date, end_date, account_type=account_type,
        share_view=share_view, conn=conn,
    )
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
    share_view: str = "personal",
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
    from collections import defaultdict
    from utils.financial_semantics import cashflow_role
    from utils.shared_expenses import apply_shared_view
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + acct_clause, params,
    ).fetchall()]
    grouped: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"total": 0.0, "visits": 0}
    )
    for tx in apply_shared_view(rows, conn, share_view):
        if cashflow_role(tx) != "spending":
            continue
        key = (tx.get("merchant") or "Unknown", tx.get("category") or "Uncategorized")
        grouped[key]["total"] += abs(float(tx.get("_cashflow_amount") or 0))
        grouped[key]["visits"] += 1
    result = [
        {"merchant": merchant, "category": category,
         "total": round(values["total"], 2), "visits": values["visits"]}
        for (merchant, category), values in grouped.items()
    ]
    result.sort(key=lambda row: -row["total"])
    result = result[:limit]
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

    from collections import defaultdict
    from statistics import mean, median as series_median, pstdev
    from utils.financial_semantics import SPENDING_TYPES, cashflow_role

    grouped: dict[str, list[dict]] = defaultdict(list)
    preference_rows = conn.execute(
        "SELECT merchant_normalized,status,display_name,category "
        "FROM recurring_preferences"
    ).fetchall()
    preferences = {r["merchant_normalized"]: dict(r) for r in preference_rows}
    for row in conn.execute(
        "SELECT * FROM transactions ORDER BY transaction_date"
    ).fetchall():
        tx = dict(row)
        if cashflow_role(tx) != "spending":
            continue
        if tx.get("transaction_type") and tx["transaction_type"] not in SPENDING_TYPES:
            continue
        merchant = str(tx.get("merchant_normalized") or tx.get("merchant") or "").strip()
        if merchant:
            grouped[merchant].append(tx)
    result = []
    for merchant, items in grouped.items():
        preference_row = preferences.get(merchant, {})
        preference = preference_row.get("status", "")
        if preference == "not_recurring":
            continue
        months = {str(i.get("transaction_date") or "")[:7] for i in items}
        amounts = [abs(float(i.get("amount") or 0)) for i in items]
        strong_recurring_terms = (
            "SUBSCRIPTION", "HOSTING", "MORTGAGE", " INSURANCE",
            "RENT", "UTILITY", "UTILITIES", "HYDRO", "INTERNET",
            "MOBILE", "PHONE", "NETFLIX", "SPOTIFY", "DISNEY",
            "OPENAI", "ANTHROPIC", "ROGERS",
        )
        fixed_like = (items[0].get("category") in {
            "Housing / Mortgage", "Utilities / Bills", "Insurance",
            "Subscriptions & Digital", "Fitness",
        } or any(term in f" {merchant.upper()}" for term in strong_recurring_terms))
        if preference != "recurring":
            # Three observations remain the general rule. Two matching monthly
            # observations are enough only for an already-recognized bill or
            # subscription, where the category supplies useful prior evidence.
            early_fixed_candidate = (
                fixed_like and len(months) >= 2 and len(amounts) >= 2
            )
            if not early_fixed_candidate and (len(months) < 3 or len(amounts) < 3):
                continue
        avg = mean(amounts)
        stability = 1.0 if len(amounts) == 1 or avg == 0 else max(0.0, 1 - pstdev(amounts) / avg)
        dates = sorted(date.fromisoformat(str(i["transaction_date"])[:10]) for i in items)
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        cadence_days = series_median(gaps) if gaps else 30
        cadence = (
            "weekly" if 5 <= cadence_days <= 10
            else "biweekly" if 11 <= cadence_days <= 18
            else "monthly" if 20 <= cadence_days <= 40
            else "irregular"
        )
        # Automatic detection requires a plausible cadence and stable amount.
        # A user confirmation may intentionally override those heuristics.
        if preference != "recurring" and (
            cadence == "irregular" or stability < (0.55 if fixed_like else 0.75)
        ):
            continue
        if (preference != "recurring" and len(months) == 2
                and (cadence != "monthly" or stability < 0.85)):
            continue
        result.append({
            "merchant": (preference_row.get("display_name")
                         or items[0].get("merchant") or merchant.title()),
            "category": (preference_row.get("category")
                         or items[0].get("category") or "Uncategorized"),
            "avg_amount": round(avg, 2),
            "months_seen": len(months),
            "total": round(sum(amounts), 2),
            "tx_count": len(items),
            "confidence": round(stability, 2),
            "cadence": cadence,
            "last_seen": dates[-1].isoformat(),
            "expected_next": (
                (dates[-1] + timedelta(days=round(cadence_days))).isoformat()
                if cadence != "irregular" else None
            ),
            "recurring_status": "confirmed" if preference == "recurring" else "automatic",
            # Variable merchants remain available to the legacy watchlist,
            # but native Recurring Costs only shows bill-like rows or an
            # explicit user confirmation.
            "planning_relevant": preference == "recurring" or fixed_like,
            "merchant_normalized": merchant,
        })
    result.sort(key=lambda r: r["avg_amount"], reverse=True)
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

    Canonical income types are included, including refunds, reimbursements,
    and rewards credits. Transfers, payments, investments, and ambiguous
    deposits stay out.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    from collections import defaultdict
    from utils.financial_semantics import cashflow_role
    params: list = [start_date, end_date]
    clause = ""
    if account_type and account_type != "all":
        clause = " AND account_type=?"
        params.append(account_type)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + clause, params,
    ).fetchall()]
    income_rows = [r for r in rows if cashflow_role(r) == "income"]
    monthly_totals: dict[str, list[float]] = defaultdict(list)
    for row in income_rows:
        monthly_totals[str(row.get("transaction_date") or "")[:7]].append(
            abs(float(row.get("amount") or 0))
        )
    monthly_list = [
        {"month": month, "total": round(sum(values), 2),
         "tx_count": len(values)}
        for month, values in sorted(monthly_totals.items())
    ]
    total = sum(abs(float(r.get("amount") or 0)) for r in income_rows)
    tx_count = len(income_rows)
    months_active = len(monthly_list)

    # Stability: stddev proxy (max - min) / avg
    # With <2 months we cannot measure variance — report None so the UI can
    # render "Insufficient data" instead of a misleading 100%.
    trend_totals = [m["total"] for m in monthly_list]
    if len(trend_totals) >= 2:
        avg_m = sum(trend_totals) / len(trend_totals)
        variance = sum((x - avg_m) ** 2 for x in trend_totals) / len(trend_totals)
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
    Only canonical income rows are included, including refund-like credits.
    account_type: optional filter ('chequing', 'savings', 'mastercard', or None/all)
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    from collections import defaultdict
    from utils.financial_semantics import cashflow_role
    params: list = [start_date, end_date]
    clause = ""
    if account_type and account_type != "all":
        clause = " AND account_type=?"
        params.append(account_type)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + clause, params,
    ).fetchall() if cashflow_role(dict(r)) == "income"]
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        source = (row.get("merchant") or row.get("subcategory")
                  or row.get("raw_description") or "Unknown")
        source_key = str(
            row.get("merchant_normalized") or source
        )
        grouped[(
            source_key, source, row.get("category") or "Unknown"
        )].append(
            abs(float(row.get("amount") or 0))
        )
    preferences = {
        str(row["source_normalized"]): str(row["status"])
        for row in conn.execute(
            "SELECT source_normalized,status FROM income_source_preferences"
        ).fetchall()
    }
    grand = sum(sum(values) for values in grouped.values()) or 1.0
    result = []
    for (source_key, source, category), values in grouped.items():
        total = sum(values)
        result.append({
            "source": source,
            "source_normalized": source_key,
            "category": category,
            "total": round(total, 2), "tx_count": len(values),
            "avg_amount": round(total / len(values), 2),
            "pct": round(total / grand * 100, 1),
            "stable_status": preferences.get(source_key, "automatic"),
        })
    result.sort(key=lambda item: item["total"], reverse=True)

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
    Uses the same canonical genuine-income definition as income_summary.
    account_type: optional filter ('chequing', 'savings', 'mastercard', or None/all)
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    from collections import defaultdict
    from utils.financial_semantics import cashflow_role
    params: list = [start_date, end_date]
    clause = ""
    if account_type and account_type != "all":
        clause = " AND account_type=?"
        params.append(account_type)
    grouped: dict[tuple[str, str], float] = defaultdict(float)
    for raw in conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + clause, params,
    ).fetchall():
        row = dict(raw)
        if cashflow_role(row) != "income":
            continue
        source = (row.get("subcategory") or row.get("merchant")
                  or row.get("raw_description") or "Unknown")
        month = str(row.get("transaction_date") or "")[:7]
        grouped[(source, month)] += abs(float(row.get("amount") or 0))
    result: dict[str, list[dict]] = {}
    for (source, month), total in sorted(grouped.items()):
        result.setdefault(source, []).append(
            {"month": month, "total": round(total, 2)}
        )

    if close:
        conn.close()
    return result

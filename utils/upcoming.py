"""What SignalSpace expects to happen next, drawn only from what already has.

Every other reading in this app describes money that has moved. This one
describes money that has moved *repeatedly*, and says when the rhythm is next
due. That is a different kind of claim and it needs saying carefully.

**An expected event is an observation about the past stated as a date.**
"Hydro has arrived every 30 to 32 days for the last eight months, and the
last one was on the 24th" becomes "expected around the 24th". It is not a
prediction that the charge will occur, and nothing here may be presented as
one.

Three rules hold throughout, and the tests exist mostly to keep them.

**No balance, ever.** This module returns events and their sums. It never
returns, and must never be used to derive, a projected balance, a future
Safe to Spend, or a claim that someone will or will not have enough. The
engine already says elsewhere that telling someone they have money they do
not have is the worst failure it has available; telling them they will not
have it is the same failure wearing a calendar.

**Two clocks, kept apart.** The forward window is measured from the real
date, because a person asking what is coming means the actual calendar. All
evidence comes from imported statements, which usually stop earlier. Those
two dates are both reported, and the gap between them is disclosed rather
than smoothed over. A stale import must not be made to look current merely
because the wall clock moved.

**Cadence is not obligation.** A merchant belongs here only when the Plan
commitment classifier treats it as money owed. A regular restaurant visit is
still just a habit, even when the user correctly confirms that it repeats.
Income is equally narrow: automatic payday markers are payroll only, while a
different source needs an explicit "Use as income" decision.
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta
from typing import Any, Optional

from config.constants import FRESH_CURRENT_DAYS, FRESH_STALE_DAYS

# How far ahead the radar looks. Four weeks: long enough to cover the next
# payday and most monthly bills, short enough that the estimates are still
# drawn from a rhythm rather than extrapolated into fiction.
HORIZON_DAYS = 28

# Beyond this, a repeating charge is being projected further than its own
# evidence reasonably carries. A weekly bill fills a four-week window with
# at most four occurrences; anything claiming more than that is padding.
MAX_OCCURRENCES_PER_SOURCE = 4

# Data-age bands come from config.constants, so the radar and Home's
# freshness badge cannot drift into two vocabularies for one idea.
_CONFIDENCE_ORDER = ("low", "medium", "high")


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _as_date(value, fallback: Optional[date] = None) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return fallback


def _downgrade(confidence: str, steps: int = 1) -> str:
    """Move a confidence label down without inventing new ones."""
    try:
        index = _CONFIDENCE_ORDER.index(confidence)
    except ValueError:
        return "low"
    return _CONFIDENCE_ORDER[max(0, index - steps)]


def _add_months(anchor: date, months: int, day_of_month: int) -> date:
    """Step whole months, keeping the day and surviving short months."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(day_of_month, _cal.monthrange(year, month)[1]))


def _project(first: date, *, period_months: float, gap_days: float,
             last_seen: Optional[date], end: date) -> list[date]:
    """Every occurrence of a rhythm up to ``end``, oldest first.

    A bill on a whole-month rhythm keeps its **day of the month**, counted
    from the last charge rather than from a median gap. The cadence engine
    derives its next date by adding the typical gap, which is right for
    weekly and fortnightly rhythms and drifts for monthly ones: a bill that
    has landed on the 24th for eight months has a median gap of 30 or 31
    days, so gap arithmetic predicts the 23rd, then the 22nd, and walks out
    of the month entirely by the end of a year. Rent does not move.
    """
    if period_months and period_months >= 1 and last_seen is not None:
        # Counted from the last real charge, so this covers any cycle that
        # should have happened since as well as the ones still to come. The
        # cadence engine's own next date is deliberately not mixed in: it is
        # the same occurrence measured a different way, and including both
        # draws one bill twice a day apart.
        step = max(1, int(round(period_months)))
        dates, index = [], 1
        while len(dates) < MAX_OCCURRENCES_PER_SOURCE:
            when = _add_months(last_seen, step * index, last_seen.day)
            if when > end:
                break
            dates.append(when)
            index += 1
        return dates

    if first > end:
        return []
    dates = [first]
    step = max(1, int(round(gap_days or 0)))
    when = first
    while len(dates) < MAX_OCCURRENCES_PER_SOURCE:
        when = when + timedelta(days=step)
        if when > end:
            break
        dates.append(when)
    return dates


def _cadence_note(cadence: str, gap_days: float) -> str:
    """How often this happens, in words rather than a field name."""
    gap = int(round(gap_days or 0))
    if cadence == "monthly":
        return "usually once a month"
    if cadence == "quarterly":
        return "usually every three months"
    if cadence in {"annual", "yearly"}:
        return "usually once a year"
    if cadence == "weekly":
        return "usually every week"
    if cadence == "biweekly":
        return "usually every two weeks"
    if cadence == "semimonthly":
        return "usually twice a month"
    if gap:
        return f"usually about every {gap} days"
    return "repeats on a regular rhythm"


def _bill_items(c, *, today: date, window_end: date) -> list[dict]:
    """Trustworthy obligations whose next turn falls inside the window."""
    from utils.recurring_cadence import merchant_cadences
    from utils.planner import bills_and_commitments

    preferences = {
        str(row["merchant_normalized"] or ""): str(row["status"] or "")
        for row in c.execute(
            "SELECT merchant_normalized, status FROM recurring_preferences"
        ).fetchall()
    }
    commitments = bills_and_commitments(conn=c)
    automatic_obligations = {
        str(item.get("merchant_normalized") or "").upper(): item
        for item in (
            commitments.get("fixed_commitments", [])
            + commitments.get("active_subscriptions", [])
            + commitments.get("nonmonthly_commitments", [])
        )
        if item.get("included_in_forecast")
    }

    items: list[dict] = []
    for row in merchant_cadences(conn=c, today=today.isoformat()):
        key = str(row.get("merchant_normalized") or "")
        preference = preferences.get(key, "")
        # A cadence only says that spending repeated. The Plan classifier is
        # the canonical boundary between fixed obligations/subscriptions and
        # variable retail habits. The existing recurring preference confirms
        # repetition, not that money is owed, so it cannot bypass that line.
        if preference == "not_recurring":
            continue
        if key not in automatic_obligations:
            continue
        # Cancelled, paid off, or moved elsewhere. The cadence engine has
        # already decided it is no longer running; drawing it on a forward
        # timeline would be expecting a bill that stopped coming.
        if not row.get("is_active", True):
            continue
        expected = _as_date(row.get("expected_next"))
        if expected is None:
            continue

        confidence = str(row.get("confidence") or "low")
        amount = round(abs(float(row.get("est_amount") or 0)), 2)
        if amount <= 0:
            continue
        recent = [
            {"date": str(charge.get("date") or ""),
             "amount": round(float(charge.get("amount") or 0), 2)}
            for charge in (row.get("recent_charges") or [])
        ]

        for when in _project(
            expected,
            period_months=float(row.get("period_months") or 1),
            gap_days=float(row.get("typical_gap_days") or 0),
            last_seen=_as_date(row.get("last_seen")),
            end=window_end,
        ):
            # Radar answers what is likely coming next. Missed-bill detection
            # is a different product question, and old guesses must never
            # dominate this forward-looking surface or its totals.
            if when < today:
                continue
            commitment = automatic_obligations.get(key, {})
            items.append({
                "kind": "bill",
                "key": key or str(row.get("merchant") or ""),
                "label": str(
                    commitment.get("merchant") or row.get("merchant")
                    or key or "Recurring cost"
                ),
                "amount": amount,
                "expected_date": when.isoformat(),
                "days_away": (when - today).days,
                "cadence": str(row.get("cadence") or ""),
                "cadence_note": _cadence_note(
                    str(row.get("cadence") or ""),
                    float(row.get("typical_gap_days") or 0),
                ),
                "confidence": confidence,
                "last_seen": str(row.get("last_seen") or ""),
                "recent": recent,
                "drill": {
                    "merchant": str(row.get("merchant") or ""),
                    "start_date": "", "end_date": "",
                },
            })
    return items


def _income_items(c, *, today: date, window_end: date) -> list[dict]:
    """Paydays expected inside the window, from rhythms already running."""
    from utils.analytics import expected_income_events

    items: list[dict] = []
    for row in expected_income_events(today, window_end, conn=c):
        occurrences = int(row.get("occurrences") or 0)
        confidence = (
            "high" if occurrences >= 6
            else "medium" if occurrences >= 4
            else "low"
        )
        when = _as_date(row.get("expected_date"))
        if when is None:
            continue
        items.append({
            "kind": "income",
            "key": str(row.get("source") or ""),
            "label": str(row.get("label") or row.get("source") or "Income"),
            "amount": round(abs(float(row.get("amount") or 0)), 2),
            "expected_date": when.isoformat(),
            "days_away": (when - today).days,
            "cadence": str(row.get("cadence") or ""),
            "cadence_note": _cadence_note(
                str(row.get("cadence") or ""),
                float(row.get("typical_gap_days") or 0),
            ),
            "confidence": confidence,
            "last_seen": str(row.get("last_seen") or ""),
            "recent": [
                {"date": str(item.get("date") or ""),
                 "amount": round(float(item.get("amount") or 0), 2)}
                for item in (row.get("recent") or [])
            ],
            "drill": {
                "search": str(row.get("label") or ""),
                "start_date": "", "end_date": "",
            },
        })
    return items


def _money(value: float) -> str:
    return f"${abs(float(value)):,.0f}"


def upcoming_money(conn=None, today=None,
                   horizon_days: int = HORIZON_DAYS) -> dict[str, Any]:
    """Trustworthy bills and planning income expected in the next few weeks.

    ``today`` is injectable so the window is testable; it defaults to the
    real local date, because someone asking what is coming means the
    calendar on the wall rather than the last day their bank exported.
    """
    c, opened = _conn(conn)
    try:
        from utils.analysis_period import supported_latest_date

        anchor = _as_date(today, None) or date.today()
        window_end = anchor + timedelta(days=max(1, int(horizon_days)))
        data_through = supported_latest_date(conn=c)

        if data_through is None:
            return {
                "available": False,
                "reason": (
                    "Import a statement and SignalSpace can start recognising "
                    "what repeats."
                ),
                "window_start": anchor.isoformat(),
                "window_end": window_end.isoformat(),
                "data_through": "", "data_age_days": 0,
                "staleness": "no_data", "staleness_note": "",
                "items": [], "income": [], "bills": [],
                "expected_in_total": 0.0, "expected_out_total": 0.0,
                "income_count": 0, "bill_count": 0,
                "next_income": None, "out_before_next_income": None,
                "summary": "",
            }

        data_age = max(0, (anchor - data_through).days)
        if data_age <= FRESH_CURRENT_DAYS:
            staleness = "current"
        elif data_age <= FRESH_STALE_DAYS:
            staleness = "getting_stale"
        else:
            staleness = "stale"

        items = (
            _bill_items(c, today=anchor, window_end=window_end)
            + _income_items(c, today=anchor, window_end=window_end)
        )

        # Every rhythm here was measured up to the last imported day. Once
        # the import falls behind, the whole window is being described by
        # evidence that stops before it starts, so every estimate in it is
        # worth less — not just the ones near the end.
        if staleness == "stale":
            for item in items:
                item["confidence"] = _downgrade(item["confidence"])

        items.sort(key=lambda row: (row["expected_date"], row["kind"],
                                    -row["amount"]))
        income = [row for row in items if row["kind"] == "income"]
        bills = [row for row in items if row["kind"] == "bill"]

        expected_in = round(sum(row["amount"] for row in income), 2)
        expected_out = round(sum(row["amount"] for row in bills), 2)

        # The next payday is only worth naming if it is genuinely expected,
        # not merely arithmetically possible.
        next_income = next(
            (row for row in income
             if row["confidence"] != "low" and row["days_away"] >= 0),
            None,
        )
        out_before = None
        if next_income is not None:
            out_before = round(sum(
                row["amount"] for row in bills
                if row["expected_date"] <= next_income["expected_date"]
            ), 2)

        return {
            "available": bool(items),
            "reason": "" if items else (
                "SignalSpace does not have enough evidence for a trustworthy "
                "bill or payday in this window. Review merchant categories, "
                "recurring status, and income sources in Settings when they "
                "need correction."
            ),
            "window_start": anchor.isoformat(),
            "window_end": window_end.isoformat(),
            "horizon_days": int(horizon_days),
            "data_through": data_through.isoformat(),
            "data_age_days": data_age,
            "staleness": staleness,
            "staleness_note": _staleness_note(staleness, data_age),
            "items": items,
            "income": income,
            "bills": bills,
            "expected_in_total": expected_in,
            "expected_out_total": expected_out,
            "income_count": len(income),
            "bill_count": len(bills),
            "next_income": next_income,
            "out_before_next_income": out_before,
            "summary": _summary(expected_out, next_income, out_before,
                                horizon_days),
        }
    finally:
        if opened:
            c.close()


def _staleness_note(staleness: str, days: int) -> str:
    if staleness == "current":
        return ""
    if staleness == "getting_stale":
        return (
            f"Your statements stop {days} days ago, so anything expected "
            "since then has not been checked against them."
        )
    return (
        f"Your statements stop {days} days ago. Everything below is drawn "
        "from rhythms that were running up to then, and none of it has been "
        "checked against newer activity. Import a statement to firm it up."
    )


def _summary(expected_out: float, next_income: Optional[dict],
             out_before: Optional[float], horizon_days: int) -> str:
    """One sentence, and only what the data supports.

    Never "available", never a balance, never a shortfall. It reports a sum
    of expected outgoings and, when there is a payday worth naming, the
    stretch before it.
    """
    if expected_out <= 0:
        return ""
    if next_income is not None and out_before is not None:
        return (
            f"About {_money(out_before)} of bills are expected "
            f"before your next expected payday."
        )
    return (
        f"About {_money(expected_out)} of bills are expected in "
        f"the next {int(horizon_days)} days."
    )


__all__ = ["upcoming_money", "HORIZON_DAYS"]

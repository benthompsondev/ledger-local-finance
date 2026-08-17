"""
Last plan result — how a finished month compared with what was intended.

Design contract
───────────────
• One question, answered once: "did that month go roughly how I planned?"
  Not a score, not a verdict on the person, and never a claim that the plan
  caused the result. The plan is an intention that was recorded; the month is
  what the statements say happened.
• No new financial math. Money In, spending and kept all come from
  `insights.monthly_aggregates`, which is the same canonical personal-view
  aggregate Insights and Home read, so this card cannot quote a number the
  rest of the app disagrees with.
• Completed months only, and only a month that owns an exact plan row.
  `get_applicable_plan` deliberately falls back to the newest saved plan when
  a month has none — useful for planning, but fatal here, because it would
  compare a month against an intention formed for a different month.
• Contemporaneity has to be provable, not assumed. See below.

Proving the intention existed at the time
─────────────────────────────────────────
`monthly_plans` carries `created_at` and `updated_at`, and
`upsert_monthly_plan` preserves `created_at` across re-saves while refreshing
`updated_at`. That is enough to reject a plan that was written or edited
after the month was already over — which matters, because comparing a month
against a target set *after* seeing the result would manufacture a certainty
the data does not support.

Both stamps must therefore fall on or before the final moment of the month.

Two honest limitations, neither of which is worked around here:

  • `created_at` is written by SQLite's `datetime('now')`, which is UTC, while
    `upsert_monthly_plan` writes `updated_at` from local time. A plan saved in
    the last few hours of a month, in a timezone behind UTC, can therefore
    carry a `created_at` that has already rolled into the next month and be
    refused. That fails closed — it hides a card that could have been shown,
    rather than showing one that cannot be trusted — so it is left alone. A
    tolerance window would fix it only by admitting genuinely retroactive
    plans, which is the error that actually misleads.
  • A missing or unparseable stamp proves nothing, so it is refused too.

Public API
──────────
    last_plan_result(conn=None, today=None, share_view="personal")
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime
from typing import Any, Optional

from utils.database import get_connection

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
# Accepted shapes for a persisted timestamp. SQLite's own default writes the
# first; anything else is treated as unprovable rather than guessed at.
_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
               "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d")


def _conn(conn: Optional[sqlite3.Connection]) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    return get_connection(), True


def month_label(month: str) -> str:
    try:
        year, number = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        return month
    if not 1 <= number <= 12:
        return month
    return f"{_MONTHS[number - 1]} {year}"


def _month_end(month: str) -> Optional[datetime]:
    """The final instant of a month, or None when the month is unreadable."""
    try:
        year, number = int(month[:4]), int(month[5:7])
        last_day = calendar.monthrange(year, number)[1]
        return datetime(year, number, last_day, 23, 59, 59)
    except (TypeError, ValueError):
        return None


def parse_stamp(value: Any) -> Optional[datetime]:
    """Parse a persisted timestamp, or None when it proves nothing."""
    text = str(value or "").strip()
    if not text:
        return None
    for shape in _TS_FORMATS:
        try:
            return datetime.strptime(text, shape)
        except ValueError:
            continue
    return None


def intention_is_contemporaneous(plan: dict, month: str) -> bool:
    """Whether this row proves an intention that existed during the month.

    Requires both stamps: `created_at` shows the row was not invented after
    the fact, and `updated_at` shows its amounts were not edited once the
    result was already knowable.
    """
    end = _month_end(month)
    if end is None:
        return False
    created = parse_stamp(plan.get("created_at"))
    updated = parse_stamp(plan.get("updated_at"))
    if created is None or updated is None:
        return False
    return created <= end and updated <= end


def _coverage_note(detail: dict) -> str:
    """Plain language for why this month is trustworthy."""
    days = int(detail.get("distinct_days") or 0)
    last_day = str(detail.get("last_day") or "")
    if days and last_day:
        return (
            f"Complete statement coverage: {days} day"
            f"{'' if days == 1 else 's'} of activity through {last_day}."
        )
    if last_day:
        return f"Complete statement coverage through {last_day}."
    return "Complete statement coverage."


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def last_plan_result(conn: Optional[sqlite3.Connection] = None,
                     today=None,
                     share_view: str = "personal") -> dict[str, Any]:
    """The most recent finished month that can be compared with its own plan."""
    c, opened = _conn(conn)
    try:
        return _result(c, today, share_view)
    finally:
        if opened:
            c.close()


def _result(conn: sqlite3.Connection, today,
            share_view: str) -> dict[str, Any]:
    from utils.database import list_monthly_plans
    from utils.insights import monthly_aggregates, statement_coverage
    from utils.planner import plan_target_month

    # Never the applicable-plan fallback: only months that own a plan row.
    plans = {
        str(row.get("month") or ""): row
        for row in list_monthly_plans(conn=conn, limit=60)
    }
    if not plans:
        return _unavailable("No saved plan yet.")

    current_month = plan_target_month(today=today)
    aggregates = {row["month"]: row for row in monthly_aggregates(
        conn=conn, share_view=share_view,
    )}
    coverage = statement_coverage(conn=conn).get(
        "statement_coverage_by_month"
    ) or {}

    for month in sorted(plans, reverse=True):
        # A month still being lived has no result to report.
        if not month or month >= current_month:
            continue
        aggregate = aggregates.get(month)
        # Completeness is the engine's decision, read from the canonical
        # aggregate rather than re-derived here.
        if not aggregate or not aggregate.get("complete"):
            continue
        plan = plans[month]
        if not intention_is_contemporaneous(plan, month):
            continue

        intended = round(float(plan.get("savings_target") or 0), 2)
        actual = round(float(aggregate.get("net") or 0), 2)
        money_in = round(float(aggregate.get("income") or 0), 2)
        spending = round(float(aggregate.get("spending") or 0), 2)
        # Derived from the same rounded figures the screen shows, so the
        # comparison always reconciles with the two numbers above it.
        difference = round(actual - intended, 2)
        saved_on = parse_stamp(plan.get("updated_at"))
        return {
            "available": True,
            "reason": "",
            "month": month,
            "month_label": month_label(month),
            "intended_kept": intended,
            "actual_kept": actual,
            "difference": difference,
            "difference_abs": abs(difference),
            "met": difference >= 0,
            "money_in": money_in,
            "spending": spending,
            # Both are ordinary outcomes and must render without apology.
            "target_is_zero": intended == 0,
            "kept_is_negative": actual < 0,
            "coverage_note": _coverage_note(coverage.get(month) or {}),
            "planned_on": saved_on.strftime("%Y-%m-%d") if saved_on else "",
        }

    return _unavailable(
        "No finished month yet has both its own saved plan and complete "
        "statement coverage."
    )

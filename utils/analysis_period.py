"""One answer to "what date is this app looking at".

Every screen needs to know how far the imported data reaches, and until now
each one asked the database directly with ``MAX(transaction_date)``. That is
the wrong question. A single row typed with the wrong year — or dragged in
from a legacy database — is enough to make that maximum a date years away,
and because every window is measured back from it, all of the real activity
falls outside the view. One synthetic 2035 row moved the whole app from July
2026 to January 2035 and took visible spending from $416 to $42.

``utils/patterns.py`` already solved this for the pattern detectors: take the
newest date that has ordinary activity behind it, and pass over an isolated
outlier. That rule was only ever applied in one file. This module is that
same rule, promoted so Home, Insights, Plan and Coach share it, plus the
surrounding context those screens need to describe themselves honestly.

Three separate ideas that were previously conflated:

  data_through     the last date the imported data genuinely reaches
  analysis_month   the month a monthly view should describe
  state            whether that is current, recent or stale

They are usually the same story. When the data is months old they are not,
and saying "this month" about an old import is exactly the kind of quiet
inaccuracy the rest of the engine works hard to avoid.

Nothing here deletes or edits a transaction. A date that is passed over is
still stored, still visible in Transactions, and reported in
``ignored_dates`` so it can be reviewed.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Optional

# A date earns the anchor by having ordinary activity behind it on distinct
# days. Counting raw rows let three transactions carrying the same bad date
# support one another.
_ANCHOR_WINDOW_DAYS = 31
_ANCHOR_MIN_ACTIVE_DAYS = 3

# Past this, a monthly view that calls itself "this month" would be wrong.
_STALE_AFTER_DAYS = 35


# One engine request asks "how far does the data reach" from Home, Insights,
# Plan, Coach and patterns, and each answer re-scanned every transaction date.
# A profiled Insights request did that work 24 times.
#
# The memo below is keyed on the connection's write counter as well as its
# identity, so it invalidates itself the moment anything is inserted, updated
# or deleted on that connection. That matters for in-process callers such as
# the test suite, which seed rows between reads on one long-lived connection.
# The desktop engine additionally clears it at each request boundary.
#
# The identity is the connection *object*, never id(connection). CPython hands
# a freed object's id straight to the next allocation, so a closed
# connection's id could belong to a new connection on a different database
# within the same process. The write counter did not catch it: a reader that
# has written nothing reports 0, and so does a fresh empty database, so both
# components of the old key matched and the empty database answered with the
# populated one's dates. It surfaced as an order-dependent suite failure that
# passed in isolation, which is the worst way for a cache bug to present.
#
# Holding the object keeps one connection alive until the next lookup replaces
# it. That is bounded: the memo never holds more than one entry, and closing a
# connection releases its file handle regardless of who still references it.
_BOUNDS_MEMO: dict[tuple[Any, int, str], tuple] = {}


def _memo_key(c, reference: date) -> tuple[Any, int, str]:
    # sqlite3's total_changes counts every row written on this connection, so
    # it is a free write-generation stamp. Unknown connection types get -1,
    # which simply means "never reuse a cached answer".
    generation = getattr(c, "total_changes", -1)
    if not isinstance(generation, int):
        generation = -1
    return (c, generation, reference.isoformat())


def reset_supported_bounds_cache() -> None:
    """Drop memoized bounds. Call at a request boundary, or after writing rows."""
    _BOUNDS_MEMO.clear()


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _parse(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _activity_dates(c, reference: date) -> list[date]:
    """Distinct, non-cancelled transaction dates on or before ``reference``.

    Read every distinct date. The former 400-date limit could hide a legitimate
    supported cluster behind 400 sparse bad dates.
    """
    rows = c.execute(
        "SELECT transaction_date FROM transactions "
        "WHERE direction != 'cancelled' AND transaction_date <= ? "
        "GROUP BY transaction_date ORDER BY transaction_date",
        (reference.isoformat(),),
    ).fetchall()
    found: set[date] = set()
    for row in rows:
        parsed = _parse(row[0])
        if parsed is not None and parsed <= reference:
            found.add(parsed)
    return sorted(found)


def _supported_bounds(c, reference: date) -> tuple[
    Optional[date], Optional[date], str
]:
    """Return the plausible first/last activity dates and confidence.

    Three distinct active days inside a 31-day window establish ordinary
    statement activity. Tiny ledgers still work with one or two dates, but are
    explicitly low confidence. If a larger database is genuinely sparse, the
    available range is retained at low confidence rather than pretending it
    contains no data.

    Memoized per connection and reference date for the current request only.
    """
    memo_key = _memo_key(c, reference)
    if memo_key[1] < 0:
        return _compute_supported_bounds(c, reference)
    cached = _BOUNDS_MEMO.get(memo_key)
    if cached is not None:
        return cached
    result = _compute_supported_bounds(c, reference)
    # Only the current write generation is worth keeping; older entries can
    # never be hit again on this connection.
    _BOUNDS_MEMO.clear()
    _BOUNDS_MEMO[memo_key] = result
    return result


def _compute_supported_bounds(c, reference: date) -> tuple[
    Optional[date], Optional[date], str
]:
    dates = _activity_dates(c, reference)
    if not dates:
        return None, None, "none"
    if len(dates) <= 2:
        return dates[0], dates[-1], "low"

    first_supported: Optional[date] = None
    right = 0
    for left, candidate in enumerate(dates):
        if right < left:
            right = left
        window_end = candidate + timedelta(days=_ANCHOR_WINDOW_DAYS)
        while right + 1 < len(dates) and dates[right + 1] < window_end:
            right += 1
        if right - left + 1 >= _ANCHOR_MIN_ACTIVE_DAYS:
            first_supported = candidate
            break

    last_supported: Optional[date] = None
    left = 0
    for right, candidate in enumerate(dates):
        window_start = candidate - timedelta(days=_ANCHOR_WINDOW_DAYS)
        while left < right and dates[left] <= window_start:
            left += 1
        if right - left + 1 >= _ANCHOR_MIN_ACTIVE_DAYS:
            last_supported = candidate

    if first_supported is not None and last_supported is not None:
        return first_supported, last_supported, "high"
    return dates[0], dates[-1], "low"


def supported_date_bounds(conn=None, today=None) -> tuple[
    Optional[date], Optional[date]
]:
    """The plausible imported history range, capped at the reference date."""
    c, opened = _conn(conn)
    try:
        reference = _parse(today) or (
            today if isinstance(today, date) else None
        ) or date.today()
        first, last, _confidence = _supported_bounds(c, reference)
        return first, last
    finally:
        if opened:
            c.close()


def supported_latest_date(conn=None, today=None) -> Optional[date]:
    """The newest date the data genuinely reaches.

    Not ``MAX(transaction_date)``. Dates after the reference day are never
    eligible, and ordinary activity is established across distinct days so
    several rows carrying one bad date cannot support one another.
    """
    c, opened = _conn(conn)
    try:
        reference = _parse(today) or (
            today if isinstance(today, date) else None
        ) or date.today()
        _first, last, _confidence = _supported_bounds(c, reference)
        return last
    finally:
        if opened:
            c.close()


def ignored_future_dates(conn=None, today=None) -> list[dict]:
    """Dates past the supported anchor, with how many rows sit on each.

    These rows are still in the ledger and still findable. This exists so a
    screen can say "one transaction is dated 2035-01-01" rather than the app
    silently behaving as though the year were 2035.
    """
    c, opened = _conn(conn)
    try:
        reference = _parse(today) or (
            today if isinstance(today, date) else None
        ) or date.today()
        supported = supported_latest_date(conn=c, today=reference)
        boundary = supported or reference
        rows = c.execute(
            "SELECT transaction_date, COUNT(*) FROM transactions "
            "WHERE transaction_date > ? GROUP BY transaction_date "
            "ORDER BY transaction_date", (boundary.isoformat(),),
        ).fetchall()
        return [
            {"date": str(row[0])[:10], "transactions": int(row[1])}
            for row in rows if _parse(row[0])
        ]
    finally:
        if opened:
            c.close()


def analysis_context(conn=None, today=None) -> dict:
    """Everything a screen needs to say which period it is describing."""
    c, opened = _conn(conn)
    try:
        reference = _parse(today) or (today if isinstance(today, date) else None) \
            or date.today()
        first_supported, supported, confidence = _supported_bounds(c, reference)
        raw_row = c.execute(
            "SELECT MAX(transaction_date) FROM transactions"
        ).fetchone()
        raw_latest = _parse(raw_row[0] if raw_row else None)

        if supported is None:
            ignored = ignored_future_dates(conn=c, today=reference)
            if raw_latest is not None:
                rows = sum(item["transactions"] for item in ignored)
                return {
                    "has_data": True,
                    "data_through": "",
                    "data_from": "",
                    "raw_latest": raw_latest.isoformat(),
                    "ignored_dates": ignored,
                    "ignored_count": rows,
                    "analysis_month": reference.strftime("%Y-%m"),
                    "analysis_month_label": _month_label(reference),
                    "data_month": "",
                    "data_month_label": "",
                    "month_source": "calendar",
                    "days_behind": 0,
                    "confidence": "none",
                    "state": "unsupported",
                    "label": (
                        f"{rows} imported transaction"
                        f"{'' if rows == 1 else 's'} "
                        f"{'is' if rows == 1 else 'are'} dated after today. "
                        "The rows remain stored, but current calculations leave "
                        "them out until their dates are corrected."
                    ),
                }
            return {
                "has_data": False,
                "data_through": "",
                "data_from": "",
                "raw_latest": raw_latest.isoformat() if raw_latest else "",
                "ignored_dates": [],
                "ignored_count": 0,
                "analysis_month": reference.strftime("%Y-%m"),
                "analysis_month_label": _month_label(reference),
                "data_month": "",
                "data_month_label": "",
                "month_source": "calendar",
                "days_behind": 0,
                "confidence": "none",
                "state": "empty",
                "label": "No transactions have been imported yet.",
            }

        ignored = ignored_future_dates(conn=c, today=reference)
        days_behind = max(0, (reference - supported).days)
        # An import that stopped months ago must not be described as the
        # current month. Fall back to the calendar month and say so.
        stale = days_behind > _STALE_AFTER_DAYS
        month = (reference if stale else supported).strftime("%Y-%m")
        state = "stale" if stale else ("current" if days_behind <= 7 else "recent")

        return {
            "has_data": True,
            "data_through": supported.isoformat(),
            "data_from": (
                first_supported.isoformat() if first_supported else ""
            ),
            "raw_latest": raw_latest.isoformat() if raw_latest else "",
            "ignored_dates": ignored,
            "ignored_count": sum(item["transactions"] for item in ignored),
            "analysis_month": month,
            "analysis_month_label": _month_label(
                reference if stale else supported
            ),
            "data_month": supported.strftime("%Y-%m"),
            "data_month_label": _month_label(supported),
            "month_source": "calendar" if stale else "data",
            "days_behind": days_behind,
            "confidence": confidence,
            "state": state,
            "label": _label(supported, days_behind, stale, ignored),
        }
    finally:
        if opened:
            c.close()


def _label(supported: date, days_behind: int, stale: bool,
           ignored: list[dict]) -> str:
    through = supported.isoformat()
    if stale:
        text = (f"Data through {through}, {days_behind} days ago. Figures "
                f"describe that period, not today.")
    elif days_behind <= 7:
        text = f"Data through {through}."
    else:
        text = f"Data through {through}, {days_behind} days ago."
    if ignored:
        rows = sum(item["transactions"] for item in ignored)
        text += (f" {rows} transaction{'' if rows == 1 else 's'} dated after "
                 f"that ({ignored[0]['date']}"
                 f"{'' if len(ignored) == 1 else ' and later'}) "
                 f"{'is' if rows == 1 else 'are'} still stored but left out "
                 f"of the date range, because nothing around "
                 f"{'it' if rows == 1 else 'them'} supports that date.")
    return text


def _month_label(value: date) -> str:
    return f"{calendar.month_name[value.month]} {value.year}"


def analysis_month(conn=None, today=None) -> str:
    """The month a monthly view should describe, as ``YYYY-MM``."""
    return analysis_context(conn=conn, today=today)["analysis_month"]


def data_through_date(conn=None, today=None) -> Optional[date]:
    """The supported latest date, or ``None`` when there is no data."""
    return supported_latest_date(conn=conn, today=today)

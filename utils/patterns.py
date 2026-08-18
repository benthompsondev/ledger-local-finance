"""Patterns in spending that a month-by-month total cannot show.

Everything here is deterministic and computed from imported transactions. No
model is involved, and none should be: a claim like "you spend twice as much on
Fridays" is either true of the data or it is not.

Two rules hold throughout.

Every window is anchored to the **latest imported date**, never to the computer
clock. Someone who imports a statement three weeks late still gets answers
about the month their data actually covers, instead of an empty result for a
month they have not imported yet.

Every claim carries what it took to make it. A pattern drawn from two weeks is
labelled differently from one drawn from two years, because the second is worth
acting on and the first is a coincidence with a chart.
"""
from __future__ import annotations

import calendar
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from statistics import median, pstdev
from typing import Optional

# How much history each kind of claim needs before it is worth making.
MIN_DAYS_FOR_WEEKDAY = 56          # eight of each weekday
MIN_ACTIVE_DAYS_FOR_WEEKDAY = 21   # and spending on at least three of each
MIN_MONTHS_FOR_FREQUENCY = 3
MIN_MONTHS_FOR_RANGE = 3
MIN_MONTHS_FOR_YEAR_AGO = 13

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _latest_date(c) -> Optional[date]:
    """The newest date the data genuinely reaches.

    This rule started here and now lives in ``utils/analysis_period.py``, so
    that Home, Insights, Plan and Coach anchor to the same date these
    detectors do. Kept as a thin alias because this module reads better with
    a local name, and because a second copy of the rule is exactly what the
    move was meant to prevent.
    """
    from utils.analysis_period import supported_latest_date

    return supported_latest_date(conn=c)


def _fixed_commitment_keys(c) -> set[str]:
    """Merchants whose spending is already committed, so timing means nothing.

    The rent leaving on the first says nothing about how someone shops. Left
    in, it makes the first of the month the loudest day on every chart and
    buries the spending a person can actually act on.
    """
    from utils.categorizer import normalize_merchant
    from utils.planner import bills_and_commitments

    return {
        str(
            item.get("merchant_normalized")
            or normalize_merchant(str(item.get("merchant") or ""))
        )
        for item in bills_and_commitments(conn=c).get("items") or []
        if item.get("included_in_forecast")
    }


def _spending_rows(c, start: str, end: str,
                   share_view: str = "personal",
                   flexible_only: bool = True) -> list[dict]:
    """Purchases, bills and fees between two dates, canonical exclusions applied."""
    from utils.categorizer import normalize_merchant
    from utils.financial_semantics import cashflow_role
    from utils.shared_expenses import apply_shared_view

    excluded = _fixed_commitment_keys(c) if flexible_only else set()
    raw = c.execute(
        "SELECT * FROM transactions "
        "WHERE transaction_date >= ? AND transaction_date <= ?",
        (start, end),
    ).fetchall()
    rows = []
    for tx in apply_shared_view((dict(row) for row in raw), c, share_view):
        if cashflow_role(tx) != "spending":
            continue
        amount = abs(float(tx.get("_cashflow_amount") or 0))
        if amount <= 0:
            continue
        if excluded:
            key = str(
                tx.get("merchant_normalized")
                or normalize_merchant(str(
                    tx.get("merchant") or tx.get("raw_description") or ""
                ))
            )
            if key in excluded:
                continue
        rows.append({
            "id": tx.get("id"),
            "date": str(tx.get("transaction_date"))[:10],
            "amount": amount,
            "category": str(tx.get("category") or "Uncategorized"),
            "merchant": str(
                tx.get("merchant") or tx.get("raw_description") or ""
            ),
            "merchant_key": str(tx.get("merchant_normalized") or ""),
        })
    return rows


def spending_rows(start: str, end: str, conn=None,
                  share_view: str = "personal",
                  flexible_only: bool = True) -> list[dict]:
    """The canonical spending rows between two dates.

    Public form of the reader every detector in this module already uses, so
    that ``utils/month_trends.py`` can measure finished months against the
    same exclusions rather than growing a second copy of them. A second copy
    is how Insights and Home once disagreed about what counted as spending.
    """
    c, opened = _conn(conn)
    try:
        return _spending_rows(c, start, end, share_view, flexible_only)
    finally:
        if opened:
            c.close()


def _months_covered(rows: list[dict]) -> int:
    return len({row["date"][:7] for row in rows})


# ── the spending calendar ────────────────────────────────────────────────

def spending_calendar(months: int = 3, conn=None,
                      share_view: str = "personal",
                      flexible_only: bool = True) -> dict:
    """Daily spending totals, for a calendar heat map.

    A month total says you spent $2,400. A calendar says you spent it on nine
    days, three of which were weekends, and that the last week was quiet. That
    is the same money describing a habit instead of a sum.
    """
    c, opened = _conn(conn)
    try:
        latest = _latest_date(c)
        if latest is None:
            return {"available": False, "reason": "No transactions imported yet.",
                    "days": [], "weeks": []}
        first_of_month = latest.replace(day=1)
        start = first_of_month
        for _ in range(max(0, int(months) - 1)):
            start = (start - timedelta(days=1)).replace(day=1)
        rows = _spending_rows(c, start.isoformat(), latest.isoformat(),
                              share_view, flexible_only)
        by_day: dict[str, float] = defaultdict(float)
        count_by_day: dict[str, int] = defaultdict(int)
        for row in rows:
            by_day[row["date"]] += row["amount"]
            count_by_day[row["date"]] += 1

        days = []
        cursor = start
        while cursor <= latest:
            key = cursor.isoformat()
            days.append({
                "date": key,
                "amount": round(by_day.get(key, 0.0), 2),
                "count": count_by_day.get(key, 0),
                "weekday": cursor.weekday(),
                "month": key[:7],
            })
            cursor += timedelta(days=1)

        spent = [day["amount"] for day in days if day["amount"] > 0]
        quiet_days = sum(1 for day in days if day["amount"] == 0)
        busiest = max(days, key=lambda d: d["amount"]) if spent else None
        return {
            "available": bool(spent),
            "reason": "" if spent else "No spending in this window.",
            "start": start.isoformat(),
            "end": latest.isoformat(),
            "days": days,
            "total": round(sum(spent), 2),
            "spending_days": len(spent),
            "quiet_days": quiet_days,
            "busiest_day": busiest,
            "typical_spending_day": round(median(spent), 2) if spent else 0.0,
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()


# ── which days of the week cost the most ─────────────────────────────────

def day_of_week_profile(lookback_days: int = 180, conn=None,
                        share_view: str = "personal",
                        flexible_only: bool = True) -> dict:
    """Average spending per weekday, and whether the difference is real.

    Everyone believes they overspend at weekends. This checks.
    """
    c, opened = _conn(conn)
    try:
        latest = _latest_date(c)
        if latest is None:
            return {"available": False,
                    "reason": "No transactions imported yet.", "days": []}
        start = latest - timedelta(days=max(1, int(lookback_days)) - 1)
        rows = _spending_rows(c, start.isoformat(), latest.isoformat(),
                              share_view, flexible_only)
        covered_days = (latest - start).days + 1
        # A wide window is not the same as data in it. Sixty transactions on
        # one Wednesday span 180 days of calendar and say nothing whatever
        # about Wednesdays, so count the days that actually have spending.
        active_days = len({row["date"] for row in rows})
        if (
            covered_days < MIN_DAYS_FOR_WEEKDAY
            or active_days < MIN_ACTIVE_DAYS_FOR_WEEKDAY
            or not rows
        ):
            return {
                "available": False,
                "reason": (
                    f"Needs spending on at least "
                    f"{MIN_ACTIVE_DAYS_FOR_WEEKDAY} separate days to compare "
                    "weekdays fairly."
                ),
                "days": [],
            }

        totals: dict[int, float] = defaultdict(float)
        counts: dict[int, int] = defaultdict(int)
        occurrences: dict[int, set] = defaultdict(set)
        for row in rows:
            weekday = date.fromisoformat(row["date"]).weekday()
            totals[weekday] += row["amount"]
            counts[weekday] += 1
            occurrences[weekday].add(row["date"])

        # How many of each weekday the window actually contains, so a window
        # with five Fridays and four Mondays does not flatter Friday.
        weekday_counts: dict[int, int] = defaultdict(int)
        cursor = start
        while cursor <= latest:
            weekday_counts[cursor.weekday()] += 1
            cursor += timedelta(days=1)

        days = []
        for weekday in range(7):
            occurrences_count = max(1, weekday_counts[weekday])
            days.append({
                "weekday": weekday,
                "name": WEEKDAY_NAMES[weekday],
                "total": round(totals[weekday], 2),
                "average": round(totals[weekday] / occurrences_count, 2),
                "transactions": counts[weekday],
                "occurrences": weekday_counts[weekday],
            })

        averages = [day["average"] for day in days]
        overall = sum(averages) / 7 if averages else 0.0
        dearest = max(days, key=lambda d: d["average"])
        cheapest = min(days, key=lambda d: d["average"])
        spread = pstdev(averages) if len(set(averages)) > 1 else 0.0
        # Two bars to clear before this counts as a habit rather than noise.
        # A standard deviation alone is far too easy. With only seven values,
        # anything billed on a fixed day of the month lands unevenly across
        # weekdays and clears it: a quarterly fee on the 4th produces a
        # confident claim about Wednesdays that means nothing at all. So the
        # dearest day must also run half again above the rest of the week,
        # which a genuine weekend habit does comfortably and a billing
        # artifact does not.
        others = [
            day["average"] for day in days
            if day["weekday"] != dearest["weekday"]
        ]
        rest = median(others) if others else 0.0
        meaningful = bool(overall) and (
            (dearest["average"] - overall) > spread
            and rest > 0
            and dearest["average"] >= rest * 1.5
        )

        weekend = [day for day in days if day["weekday"] >= 5]
        weekday_rows = [day for day in days if day["weekday"] < 5]
        weekend_avg = sum(d["average"] for d in weekend) / 2 if weekend else 0.0
        weekday_avg = (
            sum(d["average"] for d in weekday_rows) / 5 if weekday_rows else 0.0
        )

        return {
            "available": True,
            "reason": "",
            "days": days,
            "overall_average": round(overall, 2),
            "dearest": dearest,
            "cheapest": cheapest,
            "meaningful": meaningful,
            "weekend_average": round(weekend_avg, 2),
            "weekday_average": round(weekday_avg, 2),
            "weekend_premium": round(weekend_avg - weekday_avg, 2),
            "lookback_days": covered_days,
            "start": start.isoformat(),
            "end": latest.isoformat(),
        }
    finally:
        if opened:
            c.close()


# ── paying more because you go more often ────────────────────────────────

def merchant_frequency_creep(conn=None, share_view: str = "personal",
                             min_visits: int = 4) -> list[dict]:
    """Merchants costing more because of more visits, not bigger baskets.

    This is the change people genuinely cannot see for themselves. A basket
    that grows is obvious on a receipt; two extra visits a month is invisible
    and costs the same money.
    """
    c, opened = _conn(conn)
    try:
        latest = _latest_date(c)
        if latest is None:
            return []
        # Two equal windows ending at the latest complete data.
        recent_start = (latest - timedelta(days=59))
        prior_start = (latest - timedelta(days=119))
        prior_end = recent_start - timedelta(days=1)

        recent = _spending_rows(c, recent_start.isoformat(),
                                latest.isoformat(), share_view, True)
        prior = _spending_rows(c, prior_start.isoformat(),
                               prior_end.isoformat(), share_view, True)
        if _months_covered(recent + prior) < MIN_MONTHS_FOR_FREQUENCY:
            return []

        def group(rows):
            out: dict[str, list[dict]] = defaultdict(list)
            for row in rows:
                key = row["merchant_key"] or row["merchant"].upper()
                if key:
                    out[key].append(row)
            return out

        recent_groups, prior_groups = group(recent), group(prior)
        findings = []
        for key, rows in recent_groups.items():
            before = prior_groups.get(key) or []
            if len(rows) < min_visits or len(before) < 2:
                continue
            now_total = sum(r["amount"] for r in rows)
            was_total = sum(r["amount"] for r in before)
            delta = now_total - was_total
            if delta < 25:
                continue
            now_visits, was_visits = len(rows), len(before)
            now_basket = now_total / now_visits
            was_basket = was_total / was_visits
            if now_visits <= was_visits:
                continue
            # Split the increase into "went more often" and "spent more each
            # time", so the finding can say which one it actually was.
            from_visits = (now_visits - was_visits) * was_basket
            from_basket = (now_basket - was_basket) * now_visits
            if from_visits <= abs(from_basket):
                continue
            findings.append({
                "merchant": rows[0]["merchant"],
                "merchant_key": key,
                "category": rows[0]["category"],
                "now_total": round(now_total, 2),
                "was_total": round(was_total, 2),
                "delta": round(delta, 2),
                "now_visits": now_visits,
                "was_visits": was_visits,
                "extra_visits": now_visits - was_visits,
                "now_basket": round(now_basket, 2),
                "was_basket": round(was_basket, 2),
                "explained_by_visits": round(from_visits, 2),
                "explained_by_basket": round(from_basket, 2),
                "recent_start": recent_start.isoformat(),
                "recent_end": latest.isoformat(),
                "prior_start": prior_start.isoformat(),
                "prior_end": prior_end.isoformat(),
                "transaction_ids": [r["id"] for r in rows if r["id"]],
            })
        findings.sort(key=lambda f: f["delta"], reverse=True)
        return findings
    finally:
        if opened:
            c.close()


# ── what a normal month looks like for each category ─────────────────────

def category_usual_range(conn=None, share_view: str = "personal",
                         lookback_months: int = 12) -> list[dict]:
    """The band a category normally falls in, and whether this month left it.

    A single month total answers "how much". A band answers "is that a lot for
    me", which is the question people are actually asking.
    """
    c, opened = _conn(conn)
    try:
        latest = _latest_date(c)
        if latest is None:
            return []
        start = latest.replace(day=1)
        for _ in range(max(1, int(lookback_months)) - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        # Fixed commitments stay in here: "is my rent usual" is a fair
        # question, unlike "which weekday is my rent on".
        rows = _spending_rows(c, start.isoformat(), latest.isoformat(),
                              share_view, flexible_only=False)
        current_month = latest.strftime("%Y-%m")

        by_category: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float))
        for row in rows:
            by_category[row["category"]][row["date"][:7]] += row["amount"]

        # The current month is partial, so compare it against the same slice of
        # earlier months rather than against their totals.
        day_cap = latest.day
        partial: dict[str, float] = defaultdict(float)
        for row in rows:
            if int(row["date"][8:10]) <= day_cap:
                partial[(row["category"], row["date"][:7])] = partial.get(
                    (row["category"], row["date"][:7]), 0.0
                ) + row["amount"]

        results = []
        for category, months in by_category.items():
            history = [
                partial.get((category, month), 0.0)
                for month in sorted(months)
                if month != current_month
            ]
            history = [value for value in history if value > 0]
            if len(history) < MIN_MONTHS_FOR_RANGE:
                continue
            typical = median(history)
            current = partial.get((category, current_month), 0.0)
            # Median absolute deviation rather than a standard deviation,
            # because one unusual month must not switch the detector off for a
            # year. A single $9,000 month widened a standard-deviation band
            # from $51-$69 to $0-$2,647, after which nothing in that category
            # could ever be worth mentioning again. MAD barely moves.
            deviations = [abs(value - typical) for value in history]
            spread = median(deviations) * 1.4826 if deviations else 0.0
            # And a floor, because a category that has been the same figure
            # every month has no spread at all, and a band of zero width would
            # never flag it even when it doubled.
            band = max(spread, typical * 0.15)
            low = max(0.0, typical - band)
            high = typical + band
            if current > high:
                state = "above"
            elif 0 < current < low:
                state = "below"
            else:
                state = "usual"
            results.append({
                "category": category,
                "current": round(current, 2),
                "typical": round(typical, 2),
                "low": round(low, 2),
                "high": round(high, 2),
                "delta": round(current - typical, 2),
                "state": state,
                "months_used": len(history),
                "through_day": day_cap,
                "month": current_month,
            })
        results.sort(key=lambda r: abs(r["delta"]), reverse=True)
        return results
    finally:
        if opened:
            c.close()


# ── the same month, a year ago ───────────────────────────────────────────

def same_month_last_year(conn=None, share_view: str = "personal") -> dict:
    """This month against the same month a year ago.

    The honest comparison for anything seasonal. Heating in February and
    holidays in July are not overspending, and a month-on-month chart calls
    them both a problem.
    """
    c, opened = _conn(conn)
    try:
        latest = _latest_date(c)
        if latest is None:
            return {"available": False, "reason": "No transactions imported yet."}
        from utils.analysis_period import supported_date_bounds

        earliest, _supported_end = supported_date_bounds(conn=c)
        if earliest is None:
            return {"available": False, "reason": "No supported transaction dates."}
        months_of_history = (
            (latest.year - earliest.year) * 12 + latest.month - earliest.month
        ) + 1
        if months_of_history < MIN_MONTHS_FOR_YEAR_AGO:
            return {
                "available": False,
                "reason": (
                    f"Needs {MIN_MONTHS_FOR_YEAR_AGO} months of history; "
                    f"there are {months_of_history}."
                ),
                "months_of_history": months_of_history,
            }

        month = latest.strftime("%Y-%m")
        try:
            year_ago_end = latest.replace(year=latest.year - 1)
        except ValueError:  # 29 February
            year_ago_end = latest.replace(year=latest.year - 1, day=28)
        year_ago_month = year_ago_end.strftime("%Y-%m")
        year_ago_start = year_ago_end.replace(day=1)

        # The whole picture, fixed costs included: rent rising year on year is
        # exactly the kind of thing this comparison exists to show.
        now_rows = _spending_rows(
            c, latest.replace(day=1).isoformat(), latest.isoformat(),
            share_view, flexible_only=False)
        then_rows = _spending_rows(
            c, year_ago_start.isoformat(), year_ago_end.isoformat(),
            share_view, flexible_only=False)
        if not then_rows:
            return {
                "available": False,
                "reason": f"Nothing imported for {year_ago_month}.",
                "months_of_history": months_of_history,
            }
        # Having *some* rows a year ago is not the same as having that month.
        # A thin early import produced "you are spending 187 per cent more",
        # which was really "last July is barely in the database". Both sides
        # need comparable activity or the comparison is arithmetic on noise.
        now_days = len({row["date"] for row in now_rows})
        then_days = len({row["date"] for row in then_rows})
        if then_days < max(5, now_days * 0.4):
            return {
                "available": False,
                "reason": (
                    f"{year_ago_month} has spending on only {then_days} day"
                    f"{'' if then_days == 1 else 's'}, against {now_days} this "
                    "month. There is not enough of that month imported to "
                    "compare fairly."
                ),
                "months_of_history": months_of_history,
            }

        def totals(rows):
            out: dict[str, float] = defaultdict(float)
            for row in rows:
                out[row["category"]] += row["amount"]
            return out

        now_by_cat, then_by_cat = totals(now_rows), totals(then_rows)
        categories = []
        for category in set(now_by_cat) | set(then_by_cat):
            now = round(now_by_cat.get(category, 0.0), 2)
            then = round(then_by_cat.get(category, 0.0), 2)
            categories.append({
                "category": category,
                "current": now,
                "year_ago": then,
                "delta": round(now - then, 2),
            })
        categories.sort(key=lambda row: abs(row["delta"]), reverse=True)
        now_total = round(sum(now_by_cat.values()), 2)
        then_total = round(sum(then_by_cat.values()), 2)
        return {
            "available": True,
            "reason": "",
            "month": month,
            "year_ago_month": year_ago_month,
            "through_day": latest.day,
            "current_total": now_total,
            "year_ago_total": then_total,
            "delta": round(now_total - then_total, 2),
            "percent": (
                round((now_total - then_total) / then_total * 100, 1)
                if then_total else 0.0
            ),
            "categories": categories[:8],
            "months_of_history": months_of_history,
        }
    finally:
        if opened:
            c.close()

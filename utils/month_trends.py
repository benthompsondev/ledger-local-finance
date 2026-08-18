"""What changed across *finished* months, and what it was made of.

``utils/patterns.py`` answers questions about the window ending at the latest
imported day, partial current month included. This module deliberately does
the opposite: it only ever reads months that ``statement_coverage`` has
already called complete.

That restriction is the whole point. "Dining is up" measured against a month
that is eleven days old is not a finding, it is an artefact of when someone
last exported a statement. Every claim here is drawn from months that are
finished and fully imported, so it survives the next import instead of being
rewritten by it.

Three questions, in the order they are worth asking:

1. Has a category been running consistently higher or lower, rather than
   spiking once? (``category_drift_across_months``)
2. Why was the last finished month better or worse than the one before it?
   (``month_difference``)
3. Where is money leaving in amounts too small to notice one at a time?
   (``small_spend_accumulation``)

Nothing here projects, recommends, or scores. Each function returns the
comparison it made, the months it used, and the rows behind it, so a caller
can show its working.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

# ── how much history each claim needs before it is worth making ──────────

# Four finished months is the floor for a drift claim: two on each side of
# the comparison. Three would mean a two-against-one test, where the "trend"
# is a single month wearing a plural.
MIN_COMPLETE_MONTHS_FOR_DRIFT = 4
MAX_DRIFT_WINDOW = 6

# Both bars, always. A percentage alone promotes rounding errors in small
# categories; a dollar figure alone promotes the largest category every time.
DRIFT_MIN_DOLLARS = 40.0
DRIFT_MIN_PERCENT = 20.0

# Two finished months, which is the least that can produce a difference.
MIN_COMPLETE_MONTHS_FOR_DIFFERENCE = 2
DIFFERENCE_MIN_DOLLARS = 150.0
DIFFERENCE_MIN_INCOME_SHARE = 0.05
# A category has to move by this much before it is named as a contributor.
CONTRIBUTOR_MIN_DOLLARS = 25.0
MAX_CONTRIBUTORS = 4
# Below this the sentence says "roughly unchanged" instead of a figure,
# because "$6 more income" is noise dressed as a cause.
INCOME_NOISE_FLOOR = 50.0

# A purchase at or under this is small. The figure is disclosed in the claim
# rather than hidden in the ranking, and it does nothing on its own: the
# finding still has to clear a share of that month's own spending, which is
# what stops this being a fixed opinion about coffee.
SMALL_PURCHASE_CEILING = 25.0
SMALL_MIN_COUNT = 8
SMALL_MIN_TOTAL = 75.0
SMALL_MIN_SHARE = 0.05
# Below this much flexible spending in a month, $25 is not a small purchase.
SMALL_MIN_MONTH_SPEND = 500.0
# The story is "lots of little ones", so the little ones have to be most of
# what happened in that category, not a few strays beside three big buys.
SMALL_MIN_COUNT_SHARE = 0.4
MAX_SMALL_MERCHANTS = 4

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _complete_months(c) -> list[str]:
    """Finished, fully imported months, oldest first."""
    from utils.insights import statement_coverage

    return list(statement_coverage(conn=c).get("complete_months") or [])


def _month_bounds(month: str) -> tuple[str, str]:
    import calendar as _cal

    year, number = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{_cal.monthrange(year, number)[1]:02d}"


def _label(month: str, *, with_year: bool = False) -> str:
    try:
        year, number = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        return str(month)
    name = MONTH_NAMES[number - 1]
    return f"{name} {year}" if with_year else name


def _span_label(months: list[str]) -> str:
    """"March to May", or "March" when a block is one month long."""
    if not months:
        return ""
    multi_year = len({m[:4] for m in months}) > 1
    if len(months) == 1:
        return _label(months[0], with_year=multi_year)
    return (f"{_label(months[0], with_year=multi_year)} to "
            f"{_label(months[-1], with_year=True)}")


def _adjacent(earlier: str, later: str) -> bool:
    ey, em = int(earlier[:4]), int(earlier[5:7])
    ly, lm = int(later[:4]), int(later[5:7])
    return (ly - ey) * 12 + (lm - em) == 1


def _rows(c, start: str, end: str, share_view: str,
          flexible_only: bool) -> list[dict]:
    from utils.patterns import spending_rows

    return spending_rows(start, end, conn=c, share_view=share_view,
                         flexible_only=flexible_only)


def _unavailable(reason: str, **extra) -> dict:
    return {"available": False, "reason": reason, **extra}


# ── 1. a category that has been running higher or lower ──────────────────

def category_drift_across_months(conn=None, share_view: str = "personal",
                                 window: int = MAX_DRIFT_WINDOW) -> dict:
    """Categories whose finished months have moved, and stayed moved.

    The test is deliberately not "is this month unusual". It splits the
    finished months into an earlier block and a recent one, compares the
    averages, and then requires **every** month in the recent block to sit on
    the correct side of the earlier average. One expensive month cannot
    satisfy that, which is the entire difference between a trend and a spike.

    Reviewed fixed commitments are left out. A mortgage that has not changed
    would otherwise dominate every comparison, and one that has changed is
    better described by the recurring-cost detector, which knows the billing
    cadence.
    """
    c, opened = _conn(conn)
    try:
        months = _complete_months(c)
        if len(months) < MIN_COMPLETE_MONTHS_FOR_DRIFT:
            return _unavailable(
                f"Needs {MIN_COMPLETE_MONTHS_FOR_DRIFT} finished months to "
                f"tell a trend from one costly month; there are {len(months)}.",
                complete_months=len(months), items=[],
            )
        used = months[-max(MIN_COMPLETE_MONTHS_FOR_DRIFT, int(window)):]
        split = len(used) // 2
        earlier, recent = used[:split], used[split:]

        totals: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float))
        for month in used:
            start, end = _month_bounds(month)
            for row in _rows(c, start, end, share_view, flexible_only=True):
                totals[row["category"]][month] += row["amount"]

        items = []
        for category, by_month in totals.items():
            monthly = [round(by_month.get(month, 0.0), 2) for month in used]
            # A category has to have been there the whole time. Spending that
            # started halfway through the window is a new thing, not a drift,
            # and averaging zeros into the baseline exaggerates it wildly.
            if any(value <= 0 for value in monthly):
                continue
            earlier_values = monthly[:split]
            recent_values = monthly[split:]
            earlier_avg = sum(earlier_values) / len(earlier_values)
            recent_avg = sum(recent_values) / len(recent_values)
            delta = recent_avg - earlier_avg
            if abs(delta) < DRIFT_MIN_DOLLARS:
                continue
            if earlier_avg <= 0:
                continue
            percent = delta / earlier_avg * 100
            if abs(percent) < DRIFT_MIN_PERCENT:
                continue
            # The consistency rule. Every recent month, not the average.
            if delta > 0 and min(recent_values) <= earlier_avg:
                continue
            if delta < 0 and max(recent_values) >= earlier_avg:
                continue
            items.append({
                "category": category,
                "direction": "higher" if delta > 0 else "lower",
                "earlier_average": round(earlier_avg, 2),
                "recent_average": round(recent_avg, 2),
                "delta_per_month": round(delta, 2),
                "delta_total": round(delta * len(recent_values), 2),
                "percent": round(percent, 1),
                "earlier_months": list(earlier),
                "recent_months": list(recent),
                "months": [
                    {"month": month, "total": value, "block":
                     "recent" if month in recent else "earlier"}
                    for month, value in zip(used, monthly)
                ],
                "months_used": len(used),
                "contiguous": all(
                    _adjacent(used[i], used[i + 1])
                    for i in range(len(used) - 1)
                ),
            })
        items.sort(key=lambda row: abs(row["delta_per_month"]), reverse=True)
        return {
            "available": bool(items),
            "reason": "" if items else (
                "No category has moved consistently enough across these "
                "finished months to be worth calling a trend."
            ),
            "items": items,
            "months_used": used,
            "earlier_months": earlier,
            "recent_months": recent,
            "complete_months": len(months),
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()


# ── 2. why the last finished month differed from the one before ──────────

def month_difference(conn=None, share_view: str = "personal") -> dict:
    """What made the last finished month better or worse than the one before.

    "You kept $600 more" is a fact people already half know. The useful part
    is the second sentence: whether that came from earning more or spending
    less, and which categories carried it.

    Fixed commitments stay in. An annual insurance bill landing in one month
    and not the other is one of the commonest honest explanations for a
    difference, and hiding it would leave the arithmetic not adding up.
    """
    c, opened = _conn(conn)
    try:
        from utils.financial_semantics import cashflow_totals
        from utils.shared_expenses import apply_shared_view

        months = _complete_months(c)
        if len(months) < MIN_COMPLETE_MONTHS_FOR_DIFFERENCE:
            return _unavailable(
                "Needs two finished months to compare; there "
                f"{'is' if len(months) == 1 else 'are'} {len(months)}.",
                complete_months=len(months),
            )
        prior_month, latest_month = months[-2], months[-1]

        def totals_for(month: str) -> dict:
            start, end = _month_bounds(month)
            raw = c.execute(
                "SELECT * FROM transactions "
                "WHERE transaction_date >= ? AND transaction_date <= ?",
                (start, end),
            ).fetchall()
            rows = list(apply_shared_view(
                (dict(row) for row in raw), c, share_view))
            return cashflow_totals(rows, view=share_view)

        latest, prior = totals_for(latest_month), totals_for(prior_month)
        kept_delta = round(latest["net"] - prior["net"], 2)
        income_delta = round(
            latest["income_for_net"] - prior["income_for_net"], 2)
        spending_delta = round(latest["spending"] - prior["spending"], 2)

        reference_income = max(
            latest["income_for_net"], prior["income_for_net"], 0.0)
        threshold = max(DIFFERENCE_MIN_DOLLARS,
                        reference_income * DIFFERENCE_MIN_INCOME_SHARE)
        if abs(kept_delta) < threshold:
            return _unavailable(
                f"{_label(latest_month)} and {_label(prior_month)} finished "
                f"within {threshold:,.0f} dollars of each other, which is too "
                "close to be worth explaining.",
                complete_months=len(months),
                latest_month=latest_month, prior_month=prior_month,
                kept_delta=kept_delta,
            )

        def by_category(month: str) -> dict[str, float]:
            start, end = _month_bounds(month)
            out: dict[str, float] = defaultdict(float)
            for row in _rows(c, start, end, share_view, flexible_only=False):
                out[row["category"]] += row["amount"]
            return out

        latest_cats, prior_cats = by_category(latest_month), by_category(prior_month)
        contributors = []
        for category in set(latest_cats) | set(prior_cats):
            now = round(latest_cats.get(category, 0.0), 2)
            was = round(prior_cats.get(category, 0.0), 2)
            delta = round(now - was, 2)
            if abs(delta) < CONTRIBUTOR_MIN_DOLLARS:
                continue
            contributors.append({
                "category": category,
                "latest": now,
                "prior": was,
                "delta": delta,
                # Spending down helps what was kept, so the sign flips.
                "helped": delta < 0,
            })
        contributors.sort(key=lambda row: abs(row["delta"]), reverse=True)
        named = contributors[:MAX_CONTRIBUTORS]
        # How much of the spending change the named categories actually
        # account for, so the card can be honest when they do not.
        named_effect = round(sum(row["delta"] for row in named), 2)

        return {
            "available": True,
            "reason": "",
            "latest_month": latest_month,
            "prior_month": prior_month,
            "latest_label": _label(latest_month),
            "prior_label": _label(prior_month),
            "adjacent": _adjacent(prior_month, latest_month),
            "direction": "better" if kept_delta > 0 else "worse",
            "kept_delta": kept_delta,
            "latest_kept": latest["net"],
            "prior_kept": prior["net"],
            "income_delta": income_delta,
            "latest_income": latest["income_for_net"],
            "prior_income": prior["income_for_net"],
            "income_moved": abs(income_delta) >= INCOME_NOISE_FLOOR,
            "spending_delta": spending_delta,
            "latest_spending": latest["spending"],
            "prior_spending": prior["spending"],
            "contributors": named,
            "contributor_count": len(contributors),
            "named_effect": named_effect,
            "complete_months": len(months),
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()


# ── 3. money leaving in amounts too small to notice ──────────────────────

def small_spend_accumulation(conn=None, share_view: str = "personal") -> dict:
    """A category where many small purchases added up to a real figure.

    Measured over the last finished month, so the total is a whole month's
    worth rather than however far into this one the statements happen to run.

    The ceiling is fixed and stated. What keeps this from being a fixed
    opinion about small purchases is the gate underneath it: the cluster has
    to be a real share of that month's own chosen spending, and most of the
    category's activity, before there is anything to say.
    """
    c, opened = _conn(conn)
    try:
        months = _complete_months(c)
        if not months:
            return _unavailable(
                "Needs one finished month before a monthly total means "
                "anything.", complete_months=0,
            )
        month = months[-1]
        start, end = _month_bounds(month)
        rows = _rows(c, start, end, share_view, flexible_only=True)
        month_total = round(sum(row["amount"] for row in rows), 2)
        if month_total < SMALL_MIN_MONTH_SPEND:
            return _unavailable(
                f"{_label(month)} had {month_total:,.2f} of everyday "
                "spending, too little for a $25 purchase to count as small.",
                month=month, month_total=month_total,
            )

        counts: dict[str, int] = defaultdict(int)
        small_rows: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            counts[row["category"]] += 1
            if row["amount"] <= SMALL_PURCHASE_CEILING:
                small_rows[row["category"]].append(row)

        share_floor = max(SMALL_MIN_TOTAL, month_total * SMALL_MIN_SHARE)
        items = []
        for category, matched in small_rows.items():
            count = len(matched)
            total = round(sum(row["amount"] for row in matched), 2)
            if count < SMALL_MIN_COUNT or total < share_floor:
                continue
            if count < counts[category] * SMALL_MIN_COUNT_SHARE:
                continue
            merchants: dict[str, dict] = {}
            for row in matched:
                key = row["merchant_key"] or row["merchant"].upper()
                entry = merchants.setdefault(
                    key, {"merchant": row["merchant"], "count": 0,
                          "total": 0.0})
                entry["count"] += 1
                entry["total"] += row["amount"]
            top = sorted(merchants.values(), key=lambda m: m["total"],
                         reverse=True)[:MAX_SMALL_MERCHANTS]
            for entry in top:
                entry["total"] = round(entry["total"], 2)
            items.append({
                "category": category,
                "count": count,
                "total": total,
                "average": round(total / count, 2),
                "largest": round(max(row["amount"] for row in matched), 2),
                "category_count": counts[category],
                "share_of_month": round(total / month_total * 100, 1),
                "merchants": top,
                "transaction_ids": [
                    row["id"] for row in matched if row["id"]
                ],
            })
        items.sort(key=lambda row: row["total"], reverse=True)
        return {
            "available": bool(items),
            "reason": "" if items else (
                f"No category in {_label(month)} had enough small purchases "
                "to add up to a figure worth showing."
            ),
            "items": items,
            "month": month,
            "month_label": _label(month),
            "month_start": start,
            "month_end": end,
            "month_total": month_total,
            "ceiling": SMALL_PURCHASE_CEILING,
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()


def latest_complete_month(conn=None) -> Optional[str]:
    """The last finished month, for callers that only need the anchor."""
    c, opened = _conn(conn)
    try:
        months = _complete_months(c)
        return months[-1] if months else None
    finally:
        if opened:
            c.close()


__all__ = [
    "category_drift_across_months",
    "month_difference",
    "small_spend_accumulation",
    "latest_complete_month",
]

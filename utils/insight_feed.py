"""What Northstar noticed, ranked, with the numbers already worked out.

The point of this module is a division of labour. **Northstar computes every
figure. A language model, if one is switched on at all, only writes prose about
figures it was handed.** The previous design asked a model to find insights in
a JSON dump of someone's finances, which meant it invented numbers, the
grounding check caught them, and the retry told it to stop using numbers. The
result was fluent, correct, and useless: paragraphs that carefully avoided
saying anything specific.

So a finding here arrives complete. It has a headline sentence with real
figures in it, the comparison it came from, what made it worth showing, and
which transactions back it up. The page can render it with no model at all.
That is the version that has to be good.
"""
from __future__ import annotations

from typing import Optional

# A finding has to clear both bars to be worth a person's attention: enough
# money to matter, and enough movement to be a change rather than noise.
MATERIAL_AMOUNT = 25.0
MATERIAL_PERCENT = 10.0

# At most this many, because a feed of fifteen findings is a spreadsheet.
MAX_CONCERNS = 3
MAX_POSITIVE = 1


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _money(value: float) -> str:
    return f"${abs(float(value)):,.2f}"


def _finding(kind: str, title: str, claim: str, **extra) -> dict:
    finding = {
        "id": extra.pop("id", kind),
        "kind": kind,
        "title": title,
        "claim": claim,
        "tone": extra.pop("tone", "watch"),
        "confidence": extra.pop("confidence", "medium"),
        "basis": extra.pop("basis", ""),
        "chart": extra.pop("chart", ""),
        "drill": extra.pop("drill", None),
        "transaction_ids": extra.pop("transaction_ids", []),
        "rank": float(extra.pop("rank", 0.0)),
    }
    finding.update(extra)
    return finding


# ── the detectors, each returning zero or one finding ────────────────────

def _weekend_finding(c, share_view: str) -> Optional[dict]:
    from utils.patterns import day_of_week_profile

    profile = day_of_week_profile(conn=c, share_view=share_view)
    if not profile.get("available") or not profile.get("meaningful"):
        return None
    premium = float(profile.get("weekend_premium") or 0)
    if premium < MATERIAL_AMOUNT / 2:
        return None
    weekend = float(profile["weekend_average"])
    weekday = float(profile["weekday_average"])
    if weekday <= 0:
        return None
    times = weekend / weekday
    dearest = profile["dearest"]
    return _finding(
        "weekday_pattern",
        f"{dearest['name']} is your most expensive day",
        f"You spend {_money(weekend)} on an average weekend day against "
        f"{_money(weekday)} on a weekday, which is {times:.1f} times as much. "
        f"{dearest['name']} is the highest at {_money(dearest['average'])}.",
        tone="watch",
        confidence="high" if profile["lookback_days"] >= 168 else "medium",
        basis="patterns.day_of_week_profile",
        chart="day_of_week",
        rank=premium * 4,
        weekend_average=weekend,
        weekday_average=weekday,
        premium=round(premium, 2),
        days=profile["days"],
    )


def _frequency_finding(c, share_view: str) -> Optional[dict]:
    from utils.patterns import merchant_frequency_creep

    rows = merchant_frequency_creep(conn=c, share_view=share_view)
    if not rows:
        return None
    top = rows[0]
    return _finding(
        "frequency_creep",
        f"{top['merchant']} costs more because you go more often",
        f"You visited {top['merchant']} {top['now_visits']} times in the last "
        f"two months against {top['was_visits']} in the two before, and spent "
        f"{_money(top['delta'])} more. The basket barely moved "
        f"({_money(top['was_basket'])} to {_money(top['now_basket'])}); "
        f"{_money(top['explained_by_visits'])} of the increase is simply "
        f"going more often.",
        tone="watch",
        confidence="high",
        basis="patterns.merchant_frequency_creep",
        chart="merchant_visits",
        drill={"page": "transactions", "merchant": top["merchant"],
               "start_date": top["prior_start"], "end_date": top["recent_end"]},
        transaction_ids=top["transaction_ids"][:40],
        rank=top["delta"] * 3,
        merchant=top["merchant"],
        detail=top,
    )


def _usual_range_finding(c, share_view: str) -> Optional[dict]:
    from utils.patterns import category_usual_range

    rows = [
        row for row in category_usual_range(conn=c, share_view=share_view)
        if row["state"] == "above" and row["delta"] >= MATERIAL_AMOUNT
    ]
    if not rows:
        return None
    top = rows[0]
    return _finding(
        "above_usual",
        f"{top['category']} is above its usual range",
        f"{top['category']} is at {_money(top['current'])} through day "
        f"{top['through_day']}, against a usual "
        f"{_money(top['low'])} to {_money(top['high'])} at this point in the "
        f"month. That is {_money(top['delta'])} above your typical "
        f"{_money(top['typical'])}, measured over {top['months_used']} months.",
        tone="watch",
        confidence="high" if top["months_used"] >= 6 else "medium",
        basis="patterns.category_usual_range",
        chart="category_range",
        drill={"page": "transactions", "category": top["category"]},
        rank=top["delta"] * 2,
        category=top["category"],
        detail=top,
    )


def _price_change_finding(c, share_view: str) -> Optional[dict]:
    from utils.recurring_cadence import merchant_cadences

    changes = []
    for item in merchant_cadences(conn=c):
        change = item.get("price_change")
        if not change:
            continue
        delta = float(change.get("delta") or 0)
        if delta <= 0:
            continue
        per_year = delta * float(item.get("times_per_year") or 12)
        changes.append((per_year, item, change))
    if not changes:
        return None
    changes.sort(reverse=True, key=lambda row: row[0])
    per_year, item, change = changes[0]
    return _finding(
        "price_rise",
        f"{item.get('merchant')} put its price up",
        f"{item.get('merchant')} went from "
        f"{_money(change.get('previous'))} to {_money(change.get('current'))} "
        f"around {change.get('changed_on')}. At "
        f"{item.get('cadence')} billing that is {_money(per_year)} more a year.",
        tone="watch",
        confidence="high",
        basis="recurring_cadence.merchant_cadences",
        chart="",
        drill={"page": "transactions", "merchant": item.get("merchant")},
        rank=per_year,
        merchant=item.get("merchant"),
        annual_impact=round(per_year, 2),
        detail={"cadence": item.get("cadence"), **change},
    )


def _year_ago_finding(c, share_view: str) -> Optional[dict]:
    from utils.patterns import same_month_last_year

    yoy = same_month_last_year(conn=c, share_view=share_view)
    if not yoy.get("available"):
        return None
    delta = float(yoy["delta"])
    if abs(delta) < MATERIAL_AMOUNT or abs(yoy["percent"]) < MATERIAL_PERCENT:
        return None
    direction = "more" if delta > 0 else "less"
    mover = yoy["categories"][0] if yoy["categories"] else None
    tail = ""
    if mover and abs(mover["delta"]) >= MATERIAL_AMOUNT:
        tail = (
            f" The biggest single difference is {mover['category']}, "
            f"{_money(mover['year_ago'])} then against "
            f"{_money(mover['current'])} now."
        )
    return _finding(
        "year_ago",
        f"Against {yoy['year_ago_month']}, you are spending {direction}",
        f"Through day {yoy['through_day']} you have spent "
        f"{_money(yoy['current_total'])}, against "
        f"{_money(yoy['year_ago_total'])} in the same stretch of "
        f"{yoy['year_ago_month']}. That is {_money(delta)} {direction}, "
        f"{abs(yoy['percent']):.1f} per cent.{tail}",
        tone="watch" if delta > 0 else "good",
        confidence="high",
        basis="patterns.same_month_last_year",
        chart="year_over_year",
        rank=abs(delta) * 1.5,
        detail=yoy,
    )


def _improvement_finding(c, share_view: str) -> Optional[dict]:
    """Something that genuinely got better. Only ever one, and only if true."""
    from utils.patterns import category_usual_range

    rows = [
        row for row in category_usual_range(conn=c, share_view=share_view)
        if row["state"] == "below" and abs(row["delta"]) >= MATERIAL_AMOUNT
        and row["months_used"] >= 4
    ]
    if not rows:
        return None
    top = rows[0]
    return _finding(
        "improved",
        f"{top['category']} is running below its usual range",
        f"{top['category']} is at {_money(top['current'])} through day "
        f"{top['through_day']}, {_money(abs(top['delta']))} below your typical "
        f"{_money(top['typical'])} for this point in the month.",
        tone="good",
        confidence="high" if top["months_used"] >= 6 else "medium",
        basis="patterns.category_usual_range",
        chart="category_range",
        drill={"page": "transactions", "category": top["category"]},
        rank=abs(top["delta"]),
        category=top["category"],
        detail=top,
    )


_CONCERN_DETECTORS = (
    _frequency_finding,
    _price_change_finding,
    _usual_range_finding,
    _weekend_finding,
    _year_ago_finding,
)


def insight_feed(conn=None, share_view: str = "personal") -> dict:
    """At most three things to look at, plus one that went well.

    Deliberately short. A feed that lists everything it can compute is a
    report, and people stop reading reports.
    """
    c, opened = _conn(conn)
    try:
        from utils.analysis_period import analysis_context
        from utils.insights import statement_coverage

        concerns: list[dict] = []
        for detector in _CONCERN_DETECTORS:
            try:
                found = detector(c, share_view)
            except Exception:  # noqa: BLE001
                # One detector failing must never take the feed down with it.
                found = None
            if found:
                concerns.append(found)

        # A category that is both above its usual range and driven by one
        # merchant would otherwise be reported twice under different names.
        seen_categories: set[str] = set()
        deduped: list[dict] = []
        for finding in sorted(concerns, key=lambda f: f["rank"], reverse=True):
            category = str(finding.get("category") or "")
            if category and category in seen_categories:
                finding["superseded_by"] = deduped[0]["id"] if deduped else ""
                continue
            if category:
                seen_categories.add(category)
            deduped.append(finding)

        positive = None
        try:
            positive = _improvement_finding(c, share_view)
        except Exception:  # noqa: BLE001
            positive = None

        coverage = statement_coverage(conn=c)
        complete = coverage.get("complete_months") or []
        gap = None
        if len(complete) < 2:
            gap = (
                "Two complete months are needed before Northstar can compare "
                "anything fairly. Import another statement."
            )

        return {
            "generated_from": "imported transactions only",
            "analysis": analysis_context(conn=c),
            "concerns": deduped[:MAX_CONCERNS],
            "positive": positive,
            "missing_data": gap,
            "complete_months": len(complete),
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()

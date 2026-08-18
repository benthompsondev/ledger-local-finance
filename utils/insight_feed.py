"""What SpendShape noticed, ranked, with the numbers already worked out.

The point of this module is a division of labour. **SpendShape computes every
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
# Everything that was true but lost the ranking, kept for the one surface
# that offers to show it. Not a second feed: one line each, behind a click.
MAX_ALSO_NOTICED = 4

# ── how findings are ordered ─────────────────────────────────────────────
#
# Every detector reports its impact as **dollars a month**, so a per-year
# subscription rise, a two-month merchant total and a month-to-date category
# delta are compared like with like. Before this, each detector multiplied
# its own raw figure by a hand-picked number, and a $4 monthly price rise
# scored above a $300 swing in what a month kept.
#
# The weight is the part that is not arithmetic: how well evidenced the claim
# is and how much a person can do about it. It is deliberately a narrow band,
# because a weight wide enough to reorder findings by a factor of two would be
# an opinion pretending to be a calculation.
#
# Two detectors measure a month that is still running, so their impact is the
# change *so far* rather than a month's worth. That is on purpose: scaling a
# six-day figure up to a monthly one would be a projection, and this ranking
# does not project. The effect is self-correcting — early in a month a partial
# finding scores low because little has happened, and by the end of the month
# it is measuring very nearly a whole one.
_WEIGHT = {
    "category_drift": 1.3,     # several finished months, consistent, actionable
    "price_rise": 1.3,         # exact, dated, and something you can cancel
    "kept_trend": 1.25,        # the broadest true statement about a run of months
    "frequency_creep": 1.2,    # a habit, with the transactions to prove it
    "month_difference": 1.1,   # explains a real swing, less directly actionable
    "small_spend": 1.0,
    "above_usual": 1.0,
    "improved": 1.0,
    "year_ago": 0.9,           # interesting, hard to act on
    "weekday_pattern": 0.7,    # behavioural colour rather than a decision
}
# Roughly how many weekend days a month holds, for turning a per-day weekend
# premium into the monthly figure the ranking compares.
_WEEKEND_DAYS_PER_MONTH = 8.7

# When two findings describe the same category, the one with more behind it
# wins, whatever the ranking says. "Shopping has run higher for three months"
# and "Shopping is above its usual range" are the same category twice, and the
# first is the better card even when a spike makes the second score higher:
# it contains the second's claim and adds the part a person cannot see. Only
# the winner is shown, so this decides which claim gets made, not how many.
_EVIDENCE_CLASS = {
    "category_drift": 2,      # several finished months, all pointing one way
    "kept_trend": 2,          # several finished months, and the same rule
    "frequency_creep": 1,     # two equal windows, with the visits counted
    "month_difference": 1,    # two finished months, fully imported
}


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _money(value: float) -> str:
    return f"${abs(float(value)):,.2f}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _finding(kind: str, title: str, claim: str, *, impact: float,
             **extra) -> dict:
    """One complete finding.

    ``impact`` is dollars a month. It is the only thing a detector has to
    normalise, and it is what makes the ordering comparable across detectors
    measuring completely different windows.
    """
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
        # The one number worth reading before the sentence. Rendered by the
        # frontend with its own money formatter; the engine never sends a
        # pre-formatted string, so there is one place currency is decided.
        "figure": extra.pop("figure", None),
        "figure_kind": extra.pop("figure_kind", "money"),
        "figure_caption": extra.pop("figure_caption", ""),
        # The rows behind the claim: what was compared, and what it came to.
        "evidence": extra.pop("evidence", []),
        # Sample size, timeframe, and what was left out of the comparison.
        "evidence_note": extra.pop("evidence_note", ""),
        "monthly_impact": round(float(impact), 2),
        "rank": round(abs(float(impact)) * _WEIGHT.get(kind, 1.0), 4),
    }
    finding.update(extra)
    # What this finding is *about*, which is what decides whether two cards
    # are the same news. Usually the category; the whole-month cards share
    # "kept", because "July kept less than June" and "you have kept less for
    # three months running" are one story told at two lengths.
    if not finding.get("subject"):
        category = str(finding.get("category") or "")
        finding["subject"] = f"category:{category}" if category else ""
    return finding


def _row(label: str, value: Optional[float], *, kind: str = "money",
         note: str = "", signed: bool = False) -> dict:
    """One evidence line. ``signed`` marks a change rather than a level, so
    the page shows a direction instead of an unlabelled amount."""
    return {"label": label, "value": value, "kind": kind, "note": note,
            "signed": signed}


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
        impact=premium * _WEEKEND_DAYS_PER_MONTH,
        tone="watch",
        confidence="high" if profile["lookback_days"] >= 168 else "medium",
        basis="patterns.day_of_week_profile",
        chart="day_of_week",
        figure=round(premium, 2),
        figure_caption="more on an average weekend day than a weekday",
        evidence=[
            _row("Average weekend day", round(weekend, 2)),
            _row("Average weekday", round(weekday, 2)),
            _row(dearest["name"], float(dearest["average"]),
                 note="dearest day of the week"),
        ],
        evidence_note=(
            f"{profile['lookback_days']} days to {_day(profile['end'])}. "
            "Reviewed fixed commitments are left out, so a bill falling on a "
            "Tuesday cannot make Tuesday look expensive."
        ),
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
        f"More trips to {top['merchant']}, not bigger ones",
        f"You visited {top['merchant']} {top['now_visits']} times in the last "
        f"two months against {top['was_visits']} in the two before, and spent "
        f"{_money(top['delta'])} more. The basket barely moved "
        f"({_money(top['was_basket'])} to {_money(top['now_basket'])}); "
        f"{_money(top['explained_by_visits'])} of the increase is simply "
        f"going more often.",
        # A two-month total, halved to the monthly figure the ranking uses.
        impact=top["delta"] / 2,
        tone="watch",
        confidence="high",
        basis="patterns.merchant_frequency_creep",
        chart="merchant_visits",
        drill={"page": "transactions", "merchant": top["merchant"],
               "start_date": top["prior_start"], "end_date": top["recent_end"]},
        transaction_ids=top["transaction_ids"][:40],
        figure=top["delta"],
        figure_caption=(
            f"more at {top['merchant']} over two months, on "
            f"{_plural(top['extra_visits'], 'extra visit')}"
        ),
        evidence=[
            _row("Visits, last two months", top["now_visits"], kind="count"),
            _row("Visits, two months before", top["was_visits"], kind="count"),
            _row("Average basket now", top["now_basket"]),
            _row("Average basket before", top["was_basket"]),
            _row("Explained by going more often", top["explained_by_visits"]),
        ],
        evidence_note=(
            f"{top['recent_start']} to {top['recent_end']} against "
            f"{top['prior_start']} to {top['prior_end']}, two windows of equal "
            "length."
        ),
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
        f"month. That is {_money(top['delta'])} more than usual by this "
        f"point, measured over {top['months_used']} months.",
        impact=top["delta"],
        tone="watch",
        confidence="high" if top["months_used"] >= 6 else "medium",
        basis="patterns.category_usual_range",
        chart="category_range",
        # Bounded to the days the claim is actually about. Without the dates
        # this opened the category's entire history, which is not the
        # evidence for "so far this month".
        drill=_month_to_date_drill(top),
        figure=top["delta"],
        figure_caption=(
            f"above your usual {_money(top['typical'])} by day "
            f"{top['through_day']}"
        ),
        evidence=[
            _row(f"{top['category']} so far", top["current"]),
            _row("Usual by this day", top["typical"],
                 note=f"{_money(top['low'])} to {_money(top['high'])} is normal"),
        ],
        evidence_note=(
            f"Compared against the same first {top['through_day']} days of "
            f"{_plural(top['months_used'], 'earlier month')}. This month is "
            "not finished, so it can still move."
        ),
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
        # `annual_impact` comes from the cadence detector, which knows how
        # often this merchant actually bills. Multiplying by twelve here was
        # wrong for anything quarterly or annual, and it was reading keys
        # (`previous`, `current`, `times_per_year`) that this producer has
        # never returned, so every call raised and the feed's own
        # never-take-the-page-down guard swallowed it. This card had never
        # once been shown.
        per_year = float(change.get("annual_impact") or delta * 12)
        changes.append((per_year, item, change))
    if not changes:
        return None
    changes.sort(reverse=True, key=lambda row: row[0])
    per_year, item, change = changes[0]
    merchant = str(item.get("merchant") or "")
    cadence = str(item.get("cadence") or "monthly")
    previous = float(change.get("previous_amount") or 0)
    current = float(change.get("current_amount") or 0)
    changed_on = str(change.get("changed_on") or "")
    return _finding(
        "price_rise",
        f"{merchant} costs more than it used to",
        f"{merchant} went from {_money(previous)} to {_money(current)} "
        f"around {_day(changed_on)}, and has stayed there since. On "
        f"{cadence} billing that is {_money(per_year)} a year at the new "
        f"amount.",
        impact=per_year / 12,
        tone="watch",
        confidence="high",
        basis="recurring_cadence.merchant_cadences",
        chart="",
        # Every charge, deliberately unbounded: the evidence for a price
        # change is the run of amounts before and after it.
        drill={"page": "transactions", "merchant": merchant},
        figure=round(float(change.get("delta") or 0), 2),
        figure_caption=f"more per charge since {_day(changed_on)}",
        evidence=[
            _row("Was", previous, note=f"before {_day(changed_on)}"),
            _row("Now", current, note="and since"),
            _row("A year at the new amount", round(per_year, 2)),
        ],
        evidence_note=(
            f"Billed {cadence}. The yearly figure is what the increase comes "
            "to if the new amount holds. It is not a forecast that it will."
        ),
        merchant=merchant,
        annual_impact=round(per_year, 2),
        detail={"cadence": cadence, **change},
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
    then = _month_name(yoy["year_ago_month"])
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
        f"You are spending {direction} than this time last year",
        f"Through day {yoy['through_day']} you have spent "
        f"{_money(yoy['current_total'])}, against "
        f"{_money(yoy['year_ago_total'])} in the same stretch of "
        f"{then}. That is {_money(delta)} {direction}, "
        f"{abs(yoy['percent']):.1f} per cent.{tail}",
        impact=abs(delta),
        tone="watch" if delta > 0 else "good",
        confidence="high",
        basis="patterns.same_month_last_year",
        chart="year_over_year",
        figure=round(delta, 2),
        figure_caption=f"{direction} than the same days of {then}",
        evidence=[
            _row(f"Through day {yoy['through_day']} now", yoy["current_total"]),
            _row(f"{then}, same days", yoy["year_ago_total"]),
        ] + [
            _row(row["category"], row["delta"],
                 note=f"{_money(row['year_ago'])} then, "
                      f"{_money(row['current'])} now")
            for row in yoy["categories"][:3]
            if abs(row["delta"]) >= MATERIAL_AMOUNT
        ],
        evidence_note=(
            f"{yoy['months_of_history']} months of history. Fixed commitments "
            "are included, because rent rising year on year is exactly what "
            "this comparison is for."
        ),
        detail=yoy,
    )


# ── finished-month detectors ─────────────────────────────────────────────

def _drift_finding(c, share_view: str) -> Optional[dict]:
    """A category that has moved and stayed moved, not one costly month."""
    from utils.month_trends import category_drift_across_months

    result = category_drift_across_months(conn=c, share_view=share_view)
    if not result.get("available"):
        return None
    rising = [row for row in result["items"] if row["direction"] == "higher"]
    if not rising:
        return None
    return _drift_card(rising[0], tone="watch")


def _sustained_win_finding(c, share_view: str) -> Optional[dict]:
    """The same test, the other way. A decrease is worth the same rigour."""
    from utils.month_trends import category_drift_across_months

    result = category_drift_across_months(conn=c, share_view=share_view)
    if not result.get("available"):
        return None
    falling = [row for row in result["items"] if row["direction"] == "lower"]
    if not falling:
        return None
    return _drift_card(falling[0], tone="good")


def _drift_card(top: dict, *, tone: str) -> dict:
    recent = len(top["recent_months"])
    recent_span = _span(top["recent_months"])
    earlier_span = _span(top["earlier_months"])
    higher = top["direction"] == "higher"
    verb = "run higher" if higher else "run lower"
    gap = "above" if higher else "below"
    return _finding(
        "category_drift",
        f"{top['category']} has {verb} for {_plural(recent, 'month')}",
        f"{top['category']} averaged {_money(top['recent_average'])} a month "
        f"across {recent_span}, against {_money(top['earlier_average'])} a "
        f"month across {earlier_span}. That is "
        f"{_money(top['delta_per_month'])} a month {'more' if higher else 'less'}"
        f", and {_money(top['delta_total'])} across the "
        f"{_plural(recent, 'month')}. Every one of those months came in "
        f"{gap} the earlier average, so this is a level that moved rather "
        f"than a single expensive month.",
        impact=abs(top["delta_per_month"]),
        id="category_drift_up" if higher else "category_drift_down",
        tone=tone,
        confidence="high" if top["months_used"] >= 6 else "medium",
        basis="month_trends.category_drift_across_months",
        chart="",
        drill={"page": "transactions", "category": top["category"],
               "start_date": f"{top['recent_months'][0]}-01",
               "end_date": _month_end(top["recent_months"][-1])},
        figure=top["delta_per_month"],
        figure_caption=f"a month {gap} {earlier_span}",
        evidence=[
            _row(_month_name(row["month"]), row["total"],
                 note="recent" if row["block"] == "recent" else "earlier")
            for row in top["months"]
        ],
        evidence_note=(
            f"{_plural(top['months_used'], 'finished month')}"
            f"{'' if top['contiguous'] else ', not consecutive because a month in between is not fully imported'}"
            ". Partial months and reviewed fixed commitments are left out."
        ),
        category=top["category"],
        detail=top,
    )


def _kept_trend_finding(c, share_view: str) -> Optional[dict]:
    """Not "last month was better" but "you are keeping more than you were"."""
    from utils.month_trends import INCOME_NOISE_FLOOR, kept_trend

    result = kept_trend(conn=c, share_view=share_view)
    if not result.get("available"):
        return None

    higher = result["direction"] == "higher"
    recent = len(result["recent_months"])
    recent_span = _span(result["recent_months"])
    earlier_span = _span(result["earlier_months"])
    gap = "above" if higher else "below"

    # Which side moved. Saying "you kept more" without saying whether that
    # was earning or spending leaves the useful half out.
    spending_delta = result["spending_delta"]
    if abs(spending_delta) < INCOME_NOISE_FLOOR:
        cause = "Spending stayed about the same"
    else:
        cause = (
            f"Spending is {_money(spending_delta)} a month "
            f"{'higher' if spending_delta > 0 else 'lower'}"
        )
    if result["income_moved"]:
        cause += (
            f" and money in is {_money(result['income_delta'])} a month "
            f"{'higher' if result['income_delta'] > 0 else 'lower'}"
        )
    else:
        cause += " and money in has barely moved"

    return _finding(
        "kept_trend",
        f"You have kept {'more' if higher else 'less'} for "
        f"{_plural(recent, 'month')} running",
        f"You kept {_money(result['recent_average'])} a month across "
        f"{recent_span}, against {_money(result['earlier_average'])} a month "
        f"across {earlier_span}. {cause}. Every one of those "
        f"{_plural(recent, 'month')} came in {gap} the earlier average, so "
        f"this is a level that moved rather than one good month.",
        impact=abs(result["delta_per_month"]),
        id="kept_trend_up" if higher else "kept_trend_down",
        subject="kept",
        tone="good" if higher else "watch",
        confidence="high" if result["months_used"] >= 6 else "medium",
        basis="month_trends.kept_trend",
        chart="",
        # No transaction filter can represent "what several months kept".
        # A button that quietly showed one month's rows would be answering a
        # different question than the one the card asked.
        drill=None,
        figure=result["delta_per_month"],
        figure_caption=f"a month {gap} {earlier_span}",
        evidence=[
            # Levels, not changes, so no sign. The two rows after them are
            # changes and do carry one.
            _row(_month_name(row["month"]), row["kept"],
                 note="recent" if row["block"] == "recent" else "earlier")
            for row in result["months"]
        ] + [
            _row("Money in, a month", result["income_delta"], signed=True,
                 note=f"{_money(result['earlier_income'])} to "
                      f"{_money(result['recent_income'])}"),
            _row("Spending, a month", result["spending_delta"], signed=True,
                 note=f"{_money(result['earlier_spending'])} to "
                      f"{_money(result['recent_spending'])}"),
        ],
        evidence_note=(
            f"{_plural(result['months_used'], 'finished month')}"
            f"{'' if result['contiguous'] else ', not consecutive because a month in between is not fully imported'}"
            ". Partial months are left out entirely."
        ),
        detail=result,
    )


def _month_difference_finding(c, share_view: str) -> Optional[dict]:
    """Not that the month was better, but what made it better."""
    from utils.month_trends import month_difference

    result = month_difference(conn=c, share_view=share_view)
    if not result.get("available"):
        return None

    better = result["kept_delta"] > 0
    latest, prior = result["latest_label"], result["prior_label"]
    spending_delta = result["spending_delta"]
    named = result["contributors"]

    if spending_delta < 0:
        spending_clause = f"Spending fell {_money(spending_delta)}"
    elif spending_delta > 0:
        spending_clause = f"Spending rose {_money(spending_delta)}"
    else:
        spending_clause = "Spending was identical"
    if named:
        listed = _join([row["category"] for row in named[:3]])
        spending_clause += (
            f", and {_money(result['named_effect'])} of that was {listed}"
        )
    spending_clause += "."

    if result["income_moved"]:
        way = "higher" if result["income_delta"] > 0 else "lower"
        income_clause = (
            f"Income was {_money(result['income_delta'])} {way}."
        )
    else:
        income_clause = "Income was roughly unchanged."

    not_adjacent = "" if result["adjacent"] else (
        f" {latest} and {prior} are the two most recent finished months; the "
        "months between them are not fully imported."
    )

    return _finding(
        "month_difference",
        f"{latest} kept {'more' if better else 'less'} than {prior}",
        f"You kept {_money(result['latest_kept'])} in {latest} against "
        f"{_money(result['prior_kept'])} in {prior}. {spending_clause} "
        f"{income_clause}{not_adjacent}",
        impact=abs(result["kept_delta"]),
        id="month_difference",
        tone="good" if better else "watch",
        confidence="high",
        basis="month_trends.month_difference",
        chart="",
        drill={"page": "transactions",
               "start_date": f"{result['latest_month']}-01",
               "end_date": _month_end(result["latest_month"])},
        figure=result["kept_delta"],
        figure_caption=(
            f"{'more' if better else 'less'} kept in {latest} than {prior}"
        ),
        evidence=[
            _row("Money in", result["income_delta"], signed=True,
                 note=f"{_money(result['prior_income'])} to "
                      f"{_money(result['latest_income'])}"),
            _row("Spending", result["spending_delta"], signed=True,
                 note=f"{_money(result['prior_spending'])} to "
                      f"{_money(result['latest_spending'])}"),
        ] + [
            _row(row["category"], row["delta"], signed=True,
                 note=f"{_money(row['prior'])} to {_money(row['latest'])}")
            for row in named
        ],
        evidence_note=(
            f"Two finished months, {prior} and {latest}, both fully imported. "
            f"{_plural(result['contributor_count'], 'category')} moved by $25 "
            f"or more"
            f"{f', and the largest {len(named)} are listed' if len(named) < result['contributor_count'] else ''}"
            ". Fixed commitments are included, so the figures add up."
        ),
        # When one category carries the whole difference, this card and a
        # card about that category are the same event told twice. Claiming
        # the category here lets the de-duplication see it. With several
        # contributors the claim is broader, and its subject is what the
        # month kept — which is what the multi-month kept card is about too.
        category=named[0]["category"] if len(named) == 1 else "",
        subject=f"category:{named[0]['category']}" if len(named) == 1 else "kept",
        detail=result,
    )


def _small_spend_finding(c, share_view: str) -> Optional[dict]:
    """Money leaving in amounts too small to notice one at a time."""
    from utils.month_trends import small_spend_accumulation

    result = small_spend_accumulation(conn=c, share_view=share_view)
    if not result.get("available"):
        return None
    top = result["items"][0]
    month = result["month_label"]
    return _finding(
        "small_spend",
        f"Small buys in {top['category']} added up",
        f"{_plural(top['count'], 'purchase')} of "
        f"{_money(result['ceiling'])} or less in {top['category']} came to "
        f"{_money(top['total'])} in {month}, {top['share_of_month']} per cent "
        f"of everything you chose to spend that month. The average was "
        f"{_money(top['average'])} and the largest {_money(top['largest'])}. "
        f"They were {top['count']} of the {top['category_count']} purchases "
        f"you made in that category.",
        impact=top["total"],
        id="small_spend",
        tone="watch",
        confidence="high",
        basis="month_trends.small_spend_accumulation",
        chart="",
        drill={"page": "transactions", "category": top["category"],
               "start_date": result["month_start"],
               "end_date": result["month_end"]},
        transaction_ids=top["transaction_ids"][:60],
        figure=top["total"],
        figure_caption=(
            f"across {_plural(top['count'], 'purchase')} of "
            f"{_money(result['ceiling'])} or less in {month}"
        ),
        evidence=[
            _row(row["merchant"], row["total"],
                 note=_plural(row["count"], "purchase"))
            for row in top["merchants"]
        ],
        evidence_note=(
            f"{month}, a finished month. Reviewed fixed commitments are left "
            f"out, so this is spending you chose. {_money(result['month_total'])}"
            " of that in total."
        ),
        category=top["category"],
        detail=top,
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
        impact=abs(top["delta"]),
        tone="good",
        confidence="high" if top["months_used"] >= 6 else "medium",
        basis="patterns.category_usual_range",
        chart="category_range",
        drill=_month_to_date_drill(top),
        figure=abs(top["delta"]),
        figure_caption=(
            f"below your usual {_money(top['typical'])} by day "
            f"{top['through_day']}"
        ),
        evidence=[
            _row(f"{top['category']} so far", top["current"]),
            _row("Usual by this day", top["typical"]),
        ],
        evidence_note=(
            f"Compared against the same first {top['through_day']} days of "
            f"{_plural(top['months_used'], 'earlier month')}. This month is "
            "not finished, so it can still move."
        ),
        category=top["category"],
        detail=top,
    )


# ── small helpers the cards share ────────────────────────────────────────

def _month_name(month: str) -> str:
    from utils.month_trends import _label

    return _label(month, with_year=True)


def _month_end(month: str) -> str:
    from utils.month_trends import _month_bounds

    return _month_bounds(month)[1]


def _span(months: list[str]) -> str:
    from utils.month_trends import _span_label

    return _span_label(months)


def _month_to_date_drill(top: dict) -> dict:
    """The days a "so far this month" claim is measured over, and no more."""
    month = str(top.get("month") or "")
    if not month:
        return {"page": "transactions", "category": top["category"]}
    return {
        "page": "transactions",
        "category": top["category"],
        "start_date": f"{month}-01",
        "end_date": f"{month}-{int(top['through_day']):02d}",
    }


def _day(value: str) -> str:
    """"2026-05-06" reads as "6 May 2026". A raw date is a database talking."""
    from utils.month_trends import MONTH_NAMES

    try:
        year, month, day = int(value[:4]), int(value[5:7]), int(value[8:10])
        return f"{day} {MONTH_NAMES[month - 1]} {year}"
    except (TypeError, ValueError, IndexError):
        return str(value)


def _join(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


_CONCERN_DETECTORS = (
    _kept_trend_finding,
    _month_difference_finding,
    _drift_finding,
    _frequency_finding,
    _price_change_finding,
    _small_spend_finding,
    _usual_range_finding,
    _weekend_finding,
    _year_ago_finding,
)

# Detectors that can only ever report good news, so they are not worth
# running for the concerns list. The two-directional ones are not here: they
# sit in the list above and are routed by what they actually found.
_POSITIVE_ONLY_DETECTORS = (
    _sustained_win_finding,
    _improvement_finding,
)


def _one_card_per_subject(findings: list[dict]) -> list[dict]:
    """One card per subject: the best-evidenced claim, then the biggest.

    A category that is both above its usual range and driven by one merchant
    would otherwise be reported twice under different names, and the two
    whole-month cards are the same news at two lengths. The loser is dropped
    rather than demoted, because "also noticed" is for true things that lost
    the ranking, not for the same finding wearing a different title.
    """
    best: dict[str, dict] = {}
    keep: list[dict] = []
    for finding in findings:
        subject = str(finding.get("subject") or "")
        if not subject:
            keep.append(finding)
            continue
        standing = best.get(subject)
        if standing is None:
            best[subject] = finding
            continue
        contest = (_EVIDENCE_CLASS.get(finding["kind"], 0), finding["rank"])
        held = (_EVIDENCE_CLASS.get(standing["kind"], 0), standing["rank"])
        winner, loser = (
            (finding, standing) if contest > held else (standing, finding)
        )
        loser["superseded_by"] = winner["id"]
        best[subject] = winner
    return sorted(keep + list(best.values()),
                  key=lambda f: f["rank"], reverse=True)


def insight_feed(conn=None, share_view: str = "personal") -> dict:
    """At most three things to look at, plus one that went well.

    Deliberately short. A feed that lists everything it can compute is a
    report, and people stop reading reports. What did not make the top three
    is returned separately in ``also_noticed``, for the one surface that
    offers to show it behind a click.
    """
    c, opened = _conn(conn)
    try:
        from utils.analysis_period import analysis_context
        from utils.insights import statement_coverage

        def run(detectors) -> list[dict]:
            out: list[dict] = []
            for detector in detectors:
                try:
                    found = detector(c, share_view)
                except Exception:  # noqa: BLE001
                    # One detector failing must never take the feed down.
                    found = None
                if found:
                    out.append(found)
            return out

        # A finding whose tone is "good" belongs on the other side of the
        # page. Two detectors report in both directions depending on what the
        # data did, so the routing is by what they found, not by which
        # function found it.
        found = run(_CONCERN_DETECTORS)
        concerns = [f for f in found if f["tone"] != "good"]
        already_good = [f for f in found if f["tone"] == "good"]

        # A category that is both above its usual range and driven by one
        # merchant would otherwise be reported twice under different names.
        # One card per category: the best-evidenced claim, and among equals
        # the one worth the most money a month.
        deduped = _one_card_per_subject(concerns)

        # Good news gets the same test as bad news. Only one is shown, and
        # "you have kept more for three months" should beat "groceries are
        # under their usual range six days in" even when the second is the
        # bigger number, because the first is the one that will still be
        # true tomorrow.
        positives = _one_card_per_subject(already_good + [
            f for f in run(_POSITIVE_ONLY_DETECTORS) if f["tone"] == "good"
        ])
        positives.sort(
            key=lambda f: (_EVIDENCE_CLASS.get(f["kind"], 0), f["rank"]),
            reverse=True,
        )
        shown = positives[:MAX_POSITIVE]
        positive = shown[0] if shown else None

        coverage = statement_coverage(conn=c)
        complete = coverage.get("complete_months") or []
        gap = None
        if len(complete) < 2:
            gap = (
                "Two complete months are needed before SpendShape can compare "
                "anything fairly. Import another statement."
            )

        return {
            "generated_from": "imported transactions only",
            "analysis": analysis_context(conn=c),
            "concerns": deduped[:MAX_CONCERNS],
            # Good news that lost the single positive slot lands here rather
            # than disappearing. Only one improvement is shown up front, but
            # a second true one should still be findable — otherwise a
            # detector can be working perfectly and never be seen.
            "also_noticed": (
                deduped[MAX_CONCERNS:] + positives[MAX_POSITIVE:]
            )[:MAX_ALSO_NOTICED],
            "positive": positive,
            "missing_data": gap,
            "complete_months": len(complete),
            "share_view": share_view,
        }
    finally:
        if opened:
            c.close()

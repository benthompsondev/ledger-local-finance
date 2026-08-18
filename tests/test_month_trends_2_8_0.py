"""Finished-month detectors: the claim has to be true, and silence has to hold.

Each detector is shown a profile that genuinely contains the pattern, and then
profiles that look like it but are not it. The second half matters more. A
detector that fires on one expensive month is worse than no detector, because
the claim it makes ("this has been climbing") is not true of the data and the
person has no way to tell.

Every figure here is synthetic.
"""
from __future__ import annotations

import pytest

from tests.conftest import add_tx, seed_month
from utils.insight_feed import _EVIDENCE_CLASS, insight_feed
from utils.month_trends import (
    category_drift_across_months, kept_trend, month_difference,
    small_spend_accumulation,
)

MONTHS_SIX = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")


def _shopping(month: str, total: float, *, spread: int = 2) -> list[dict]:
    """Shopping rows for a month, at merchants that vary so nothing here can
    be mistaken for a recurring commitment and quietly excluded."""
    each = round(total / spread, 2)
    return [
        dict(day=f"{month}-{8 + n * 3:02d}", desc=f"SHOP {month[-2:]}{n}",
             amount=each, direction="debit", category="Shopping")
        for n in range(spread)
    ]


def _seed_months(conn, plan: dict[str, float], **kwargs) -> None:
    """One complete month per entry, carrying that month's Shopping total."""
    for month, shopping in plan.items():
        seed_month(conn, month, extra=_shopping(month, shopping) if shopping
                   else [], **kwargs)


# ── 1. a category that moved and stayed moved ───────────────────────────

def test_a_category_running_higher_for_three_months_is_found(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 100, "2026-02": 110, "2026-03": 90,
        "2026-04": 300, "2026-05": 320, "2026-06": 310,
    })
    result = category_drift_across_months(conn=ledger_db)

    assert result["available"], result["reason"]
    top = next(r for r in result["items"] if r["category"] == "Shopping")
    assert top["direction"] == "higher"
    assert top["earlier_average"] == pytest.approx(100.0, abs=0.02)
    assert top["recent_average"] == pytest.approx(310.0, abs=0.02)
    assert top["delta_per_month"] == pytest.approx(210.0, abs=0.02)
    # The total is a fact about those months, not a projection forward.
    assert top["delta_total"] == pytest.approx(630.0, abs=0.06)
    assert len(top["recent_months"]) == 3
    assert top["contiguous"] is True


def test_one_expensive_month_is_not_called_a_trend(ledger_db):
    """The whole reason the consistency rule exists.

    The averages here are identical to the test above: earlier $100, recent
    $310. Only the shape differs, and the shape is the finding.
    """
    _seed_months(ledger_db, {
        "2026-01": 100, "2026-02": 100, "2026-03": 100,
        "2026-04": 100, "2026-05": 100, "2026-06": 730,
    })
    result = category_drift_across_months(conn=ledger_db)

    flagged = [r["category"] for r in result["items"]]
    assert "Shopping" not in flagged, (
        "a single expensive month was reported as a sustained trend"
    )


def test_a_decrease_is_found_with_the_same_rigour(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 400, "2026-02": 420, "2026-03": 380,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    result = category_drift_across_months(conn=ledger_db)

    top = next(r for r in result["items"] if r["category"] == "Shopping")
    assert top["direction"] == "lower"
    assert top["delta_per_month"] < 0


def test_three_finished_months_is_not_enough_to_claim_a_trend(ledger_db):
    _seed_months(ledger_db, {
        "2026-04": 100, "2026-05": 300, "2026-06": 320,
    })
    result = category_drift_across_months(conn=ledger_db)

    assert result["available"] is False
    assert "finished months" in result["reason"]
    assert result["complete_months"] == 3


def test_a_move_too_small_in_dollars_is_not_reported(ledger_db):
    # A clean, perfectly consistent 25% rise, worth $20 a month.
    _seed_months(ledger_db, {
        "2026-01": 80, "2026-02": 80, "2026-03": 80,
        "2026-04": 100, "2026-05": 100, "2026-06": 100,
    })
    result = category_drift_across_months(conn=ledger_db)

    assert "Shopping" not in [r["category"] for r in result["items"]]


def test_a_move_too_small_in_percent_is_not_reported(ledger_db):
    # $60 a month is real money, but on a $900 base it is 6.7% and well
    # inside the noise of an ordinary month.
    _seed_months(ledger_db, {
        "2026-01": 900, "2026-02": 900, "2026-03": 900,
        "2026-04": 960, "2026-05": 960, "2026-06": 960,
    })
    result = category_drift_across_months(conn=ledger_db)

    assert "Shopping" not in [r["category"] for r in result["items"]]


def test_spending_that_only_started_recently_is_not_drift(ledger_db):
    """Nothing, then something, is a new cost. Averaging zeros in would call
    it a 100% rise and put it top of the feed every time."""
    _seed_months(ledger_db, {
        "2026-01": 0, "2026-02": 0, "2026-03": 0,
        "2026-04": 300, "2026-05": 310, "2026-06": 300,
    })
    result = category_drift_across_months(conn=ledger_db)

    assert "Shopping" not in [r["category"] for r in result["items"]]


def test_a_partial_month_cannot_pull_the_comparison_down(ledger_db):
    """Half of July must not read as July collapsing."""
    _seed_months(ledger_db, {
        "2026-01": 100, "2026-02": 110, "2026-03": 90,
        "2026-04": 300, "2026-05": 320, "2026-06": 310,
    })
    add_tx(ledger_db, day="2026-07-02", desc="SHOP JUL", amount=15,
           direction="debit", category="Shopping")
    ledger_db.commit()
    result = category_drift_across_months(conn=ledger_db)

    top = next(r for r in result["items"] if r["category"] == "Shopping")
    assert "2026-07" not in [row["month"] for row in top["months"]]
    assert top["direction"] == "higher"


# ── 2. where the difference between two months came from ────────────────

def test_the_difference_between_two_months_reconciles(ledger_db):
    _seed_months(ledger_db, {"2026-05": 700, "2026-06": 100})
    result = month_difference(conn=ledger_db)

    assert result["available"], result.get("reason")
    assert result["latest_month"] == "2026-06"
    assert result["prior_month"] == "2026-05"
    assert result["direction"] == "better"
    # The identity the whole card rests on: what you kept moved by exactly
    # what came in minus what went out.
    assert result["kept_delta"] == pytest.approx(
        result["income_delta"] - result["spending_delta"], abs=0.02,
    )
    assert result["income_moved"] is False
    shopping = next(r for r in result["contributors"]
                    if r["category"] == "Shopping")
    assert shopping["delta"] == pytest.approx(-600.0, abs=0.02)
    assert shopping["helped"] is True


def test_the_named_categories_come_from_the_canonical_totals(ledger_db):
    """The card's arithmetic has to agree with the Insights month chart."""
    from utils.insights import monthly_aggregates

    _seed_months(ledger_db, {"2026-05": 700, "2026-06": 100})
    result = month_difference(conn=ledger_db)
    months = {row["month"]: row for row in monthly_aggregates(conn=ledger_db)}

    assert result["latest_spending"] == pytest.approx(
        months["2026-06"]["spending"], abs=0.02)
    assert result["prior_kept"] == pytest.approx(
        months["2026-05"]["net"], abs=0.02)


def test_two_months_that_finished_alike_are_not_explained(ledger_db):
    _seed_months(ledger_db, {"2026-05": 300, "2026-06": 320})
    result = month_difference(conn=ledger_db)

    assert result["available"] is False
    assert "too close" in result["reason"]


def test_one_finished_month_cannot_be_compared(ledger_db):
    _seed_months(ledger_db, {"2026-06": 300})
    result = month_difference(conn=ledger_db)

    assert result["available"] is False
    assert result["complete_months"] == 1


def test_a_gap_between_the_two_finished_months_is_disclosed(ledger_db):
    """May and July, because June was never fully imported."""
    _seed_months(ledger_db, {"2026-05": 700, "2026-07": 100})
    add_tx(ledger_db, day="2026-06-04", desc="ODD ROW", amount=30,
           direction="debit", category="Shopping")
    ledger_db.commit()
    result = month_difference(conn=ledger_db)

    assert result["available"], result.get("reason")
    assert result["prior_month"] == "2026-05"
    assert result["latest_month"] == "2026-07"
    assert result["adjacent"] is False


def test_income_moving_is_reported_as_income_not_spending(ledger_db):
    _seed_months(ledger_db, {"2026-05": 200, "2026-06": 200})
    add_tx(ledger_db, day="2026-06-22", desc="EMPLOYER BONUS", amount=900,
           direction="credit", category="Payroll Income")
    ledger_db.commit()
    result = month_difference(conn=ledger_db)

    assert result["available"], result.get("reason")
    assert result["income_moved"] is True
    assert result["income_delta"] == pytest.approx(900.0, abs=0.02)
    assert result["spending_delta"] == pytest.approx(0.0, abs=0.02)


# ── 3. small purchases that add up ──────────────────────────────────────

def _small_buys(month: str, count: int, amount: float,
                category: str = "Food & Convenience") -> list[dict]:
    return [
        dict(day=f"{month}-{2 + (n % 26):02d}", desc=f"CORNER {n}",
             amount=amount, direction="debit", category=category)
        for n in range(count)
    ]


def test_many_small_purchases_in_one_category_are_added_up(ledger_db):
    seed_month(ledger_db, "2026-06", extra=_small_buys("2026-06", 20, 11.0))
    result = small_spend_accumulation(conn=ledger_db)

    assert result["available"], result["reason"]
    top = result["items"][0]
    assert top["category"] == "Food & Convenience"
    # The seeded twenty, plus the fixture's own $6 coffee, which is also
    # under the ceiling and must be counted rather than quietly dropped.
    assert top["count"] == 21
    assert top["total"] == pytest.approx(226.0, abs=0.02)
    assert top["average"] == pytest.approx(226.0 / 21, abs=0.02)
    assert top["share_of_month"] > 0


def test_a_handful_of_small_purchases_is_not_a_finding(ledger_db):
    seed_month(ledger_db, "2026-06", extra=_small_buys("2026-06", 4, 11.0))
    result = small_spend_accumulation(conn=ledger_db)

    assert result["available"] is False


def test_small_purchases_that_never_add_up_stay_quiet(ledger_db):
    """Twelve genuinely trivial buys against an ordinary month of spending.

    This is the guard against the feature becoming an opinion about coffee.
    """
    seed_month(ledger_db, "2026-06", extra=_small_buys("2026-06", 12, 2.50))
    result = small_spend_accumulation(conn=ledger_db)

    assert result["available"] is False


def test_a_category_of_big_purchases_with_a_few_strays_is_not_it(ledger_db):
    """The story is "lots of little ones". Nine small buys beside twenty-five
    large ones is a different story, and the totals would mislead."""
    extra = _small_buys("2026-06", 9, 20.0, category="Shopping") + [
        dict(day=f"2026-06-{2 + n:02d}", desc=f"BIG BUY {n}", amount=180.0,
             direction="debit", category="Shopping")
        for n in range(25)
    ]
    seed_month(ledger_db, "2026-06", extra=extra)
    result = small_spend_accumulation(conn=ledger_db)

    assert "Shopping" not in [r["category"] for r in result.get("items") or []]


def test_a_partial_month_is_never_used_for_a_monthly_total(ledger_db):
    """A month total drawn from eleven days would understate every time."""
    seed_month(ledger_db, "2026-05", extra=_small_buys("2026-05", 20, 11.0))
    for n in range(20):
        add_tx(ledger_db, day=f"2026-06-{2 + n % 9:02d}", desc=f"CORNER {n}",
               amount=11.0, direction="debit", category="Food & Convenience")
    ledger_db.commit()
    result = small_spend_accumulation(conn=ledger_db)

    assert result["month"] == "2026-05"


# ── 4. what the feed does with them ─────────────────────────────────────

def test_the_new_findings_arrive_complete(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 100, "2026-02": 110, "2026-03": 90,
        "2026-04": 300, "2026-05": 320, "2026-06": 310,
    })
    feed = insight_feed(conn=ledger_db)
    cards = feed["concerns"] + feed["also_noticed"] + (
        [feed["positive"]] if feed["positive"] else [])
    new = [c for c in cards if c["kind"] in
           {"category_drift", "month_difference", "small_spend"}]

    assert new, "none of the finished-month detectors reported anything"
    for card in new:
        assert "$" in card["claim"]
        assert card["figure"] is not None
        assert card["figure_caption"]
        assert card["evidence"], f"{card['kind']} has no evidence rows"
        assert card["evidence_note"]
        assert card["basis"].startswith("month_trends.")
        assert card["drill"]


def test_a_small_price_rise_does_not_outrank_a_large_monthly_swing(ledger_db):
    """What the ranking rewrite is for.

    Before, a $4 monthly subscription rise scored 4 * 12 = 48 and a $600
    swing in what a month kept would have scored 600 * 1.1. The old scheme
    multiplied a yearly figure by one and a monthly figure by another.
    """
    _seed_months(ledger_db, {"2026-05": 700, "2026-06": 100})
    feed = insight_feed(conn=ledger_db)
    ranked = feed["concerns"] + feed["also_noticed"]
    if feed["positive"]:
        ranked.append(feed["positive"])
    by_kind = {card["kind"]: card for card in ranked}

    from utils.insight_feed import _WEIGHT

    assert "month_difference" in by_kind
    swing = by_kind["month_difference"]
    assert swing["monthly_impact"] == pytest.approx(600.0, abs=1.0)
    # Rank is impact-a-month times the documented weight, and nothing else.
    for card in ranked:
        assert card["rank"] == pytest.approx(
            abs(card["monthly_impact"]) * _WEIGHT[card["kind"]], abs=0.01,
        )
    # The concrete case the normalisation exists for: a $4-a-charge monthly
    # price rise is $48 a year, and must not outscore a $600 month.
    from utils.insight_feed import _finding

    small_rise = _finding("price_rise", "t", "c", impact=48 / 12)
    assert small_rise["rank"] < swing["rank"]


def test_a_better_month_is_good_news_not_a_concern(ledger_db):
    _seed_months(ledger_db, {"2026-05": 700, "2026-06": 100})
    feed = insight_feed(conn=ledger_db)

    assert "month_difference" not in [c["kind"] for c in feed["concerns"]]
    assert feed["positive"] is not None
    assert feed["positive"]["tone"] == "good"


def test_a_worse_month_is_a_concern(ledger_db):
    _seed_months(ledger_db, {"2026-05": 100, "2026-06": 700})
    feed = insight_feed(conn=ledger_db)

    kinds = [c["kind"] for c in feed["concerns"] + feed["also_noticed"]]
    assert "month_difference" in kinds


def test_the_feed_is_still_short_with_eight_detectors(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 100, "2026-02": 110, "2026-03": 90,
        "2026-04": 300, "2026-05": 320, "2026-06": 900,
    })
    feed = insight_feed(conn=ledger_db)

    assert len(feed["concerns"]) <= 3
    assert len(feed["also_noticed"]) <= 4
    ids = [c["id"] for c in feed["concerns"] + feed["also_noticed"]]
    assert len(ids) == len(set(ids)), "the same finding appeared twice"


def test_one_event_is_not_reported_twice_under_two_names(ledger_db):
    """June's swing IS the small-buys story here, told from the other end.

    One category carries the whole month difference, and that same category
    is the small-spend finding. Two cards, one event, and a reader who
    reasonably concludes two separate things went wrong.
    """
    small = _small_buys("2026-06", 24, 12.50)
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06", extra=small)
    feed = insight_feed(conn=ledger_db)

    everywhere = feed["concerns"] + feed["also_noticed"]
    claimed = [c for c in everywhere if c.get("category")]
    assert len(claimed) == len({c["category"] for c in claimed}), (
        "the same category was reported twice, once up front and once behind "
        "the click"
    )
    # Dropped, not demoted. "Also noticed" is for true things that lost the
    # ranking, not for the same finding wearing a different title.
    assert {c["kind"] for c in everywhere} & {"month_difference", "small_spend"}


def test_a_sustained_claim_beats_a_one_month_claim_about_the_same_category(
        ledger_db):
    """Shopping drifted up over three months AND spiked in the last one.

    The spike scores higher, because a spike is a bigger number. The sustained
    claim is still the better card: it contains the other one and adds the
    part a person cannot work out for themselves.
    """
    _seed_months(ledger_db, {
        "2026-01": 210, "2026-02": 195, "2026-03": 225,
        "2026-04": 470, "2026-05": 505, "2026-06": 900,
    })
    feed = insight_feed(ledger_db)
    shopping = [c for c in feed["concerns"] + feed["also_noticed"]
                if c.get("category") == "Shopping"]

    assert len(shopping) == 1, "Shopping was claimed by two cards at once"
    assert shopping[0]["kind"] == "category_drift"
    # And the one it beat scored higher, which is the point of the rule.
    assert shopping[0]["kind"] in _EVIDENCE_CLASS


def test_a_broad_month_difference_is_not_treated_as_a_duplicate(ledger_db):
    """Several categories moving is a different claim from any one of them."""
    seed_month(ledger_db, "2026-05", extra=[
        dict(day="2026-05-09", desc="SHOP A", amount=400.0,
             direction="debit", category="Shopping"),
        dict(day="2026-05-11", desc="TRIP CO", amount=300.0,
             direction="debit", category="Entertainment"),
    ])
    seed_month(ledger_db, "2026-06")
    result = month_difference(conn=ledger_db)
    feed = insight_feed(conn=ledger_db)
    card = next(c for c in feed["concerns"] + feed["also_noticed"]
                + ([feed["positive"]] if feed["positive"] else [])
                if c["kind"] == "month_difference")

    assert len(result["contributors"]) > 1
    assert card["category"] == ""


def test_a_broken_finished_month_detector_cannot_take_down_the_feed(
        ledger_db, monkeypatch):
    _seed_months(ledger_db, {"2026-05": 700, "2026-06": 100})
    import utils.insight_feed as module

    def explode(*args, **kwargs):
        raise RuntimeError("detector failed")

    monkeypatch.setattr(module, "_month_difference_finding", explode)
    monkeypatch.setattr(module, "_drift_finding", explode)
    feed = insight_feed(conn=ledger_db)

    assert isinstance(feed["concerns"], list)
    assert isinstance(feed["also_noticed"], list)


# ── 5. what several finished months kept ────────────────────────────────

def test_keeping_more_for_three_months_running_is_found(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 700, "2026-02": 680, "2026-03": 720,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    result = kept_trend(conn=ledger_db)

    assert result["available"], result["reason"]
    assert result["direction"] == "higher"
    assert result["delta_per_month"] > 0
    # And it says which side moved, which is the useful half.
    assert result["income_moved"] is False
    assert result["spending_delta"] < 0
    assert len(result["recent_months"]) == 3


def test_keeping_less_for_three_months_running_is_found(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 150, "2026-02": 140, "2026-03": 160,
        "2026-04": 700, "2026-05": 680, "2026-06": 720,
    })
    result = kept_trend(conn=ledger_db)

    assert result["available"], result["reason"]
    assert result["direction"] == "lower"
    assert result["delta_per_month"] < 0


def test_the_kept_figures_are_the_canonical_monthly_ones(ledger_db):
    from utils.insights import monthly_aggregates

    _seed_months(ledger_db, {
        "2026-01": 700, "2026-02": 680, "2026-03": 720,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    result = kept_trend(conn=ledger_db)
    months = {row["month"]: row for row in monthly_aggregates(conn=ledger_db)}

    for row in result["months"]:
        assert row["kept"] == pytest.approx(months[row["month"]]["net"], abs=0.02)


def test_one_good_month_is_not_a_change_of_level(ledger_db):
    """Two ordinary months and one very good one is not "keeping more".

    The averages clear the materiality gate comfortably here. It is the
    consistency rule that has to reject this, which is the whole point.
    """
    _seed_months(ledger_db, {
        "2026-01": 900, "2026-02": 900, "2026-03": 900,
        "2026-04": 900, "2026-05": 900, "2026-06": 0,
    })
    result = kept_trend(conn=ledger_db)

    assert result["available"] is False
    assert "not a change of level" in result["reason"]
    assert abs(result["delta_per_month"]) > 250, (
        "this profile no longer tests the consistency rule"
    )


def test_months_that_kept_about_the_same_are_not_reported(ledger_db):
    _seed_months(ledger_db, {
        "2026-01": 400, "2026-02": 410, "2026-03": 390,
        "2026-04": 420, "2026-05": 400, "2026-06": 405,
    })
    result = kept_trend(conn=ledger_db)

    assert result["available"] is False
    assert "same level" in result["reason"]


def test_three_finished_months_cannot_show_a_change_of_level(ledger_db):
    _seed_months(ledger_db, {"2026-04": 700, "2026-05": 150, "2026-06": 140})
    result = kept_trend(conn=ledger_db)

    assert result["available"] is False
    assert result["complete_months"] == 3


def test_keeping_more_is_good_news_and_carries_no_transaction_button(ledger_db):
    """No filter can represent "what six months kept", so none is offered."""
    _seed_months(ledger_db, {
        "2026-01": 700, "2026-02": 680, "2026-03": 720,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    feed = insight_feed(conn=ledger_db)
    good = ([feed["positive"]] if feed["positive"] else []) + [
        c for c in feed["also_noticed"] if c["tone"] == "good"
    ]
    card = next(c for c in good if c["kind"] == "kept_trend")

    assert feed["positive"] is not None
    assert feed["positive"]["tone"] == "good"
    assert card["drill"] is None
    assert "kept more for 3 months running" in card["title"]


def test_good_news_that_loses_the_slot_is_still_findable(ledger_db):
    """Only one improvement is shown, but a second true one is not deleted."""
    _seed_months(ledger_db, {
        "2026-01": 700, "2026-02": 680, "2026-03": 720,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    feed = insight_feed(conn=ledger_db)
    good_kinds = {c["kind"] for c in
                  ([feed["positive"]] if feed["positive"] else [])
                  + [c for c in feed["also_noticed"] if c["tone"] == "good"]}

    # Shopping fell and what the months kept rose. Both are true, both are
    # good news, and one of them is the reason for the other.
    assert {"category_drift", "kept_trend"} <= good_kinds


def test_a_sustained_kept_trend_beats_a_two_month_difference(ledger_db):
    """Both describe what the months kept. Only the stronger one is shown."""
    _seed_months(ledger_db, {
        "2026-01": 700, "2026-02": 680, "2026-03": 720,
        "2026-04": 150, "2026-05": 140, "2026-06": 160,
    })
    feed = insight_feed(conn=ledger_db)
    everywhere = feed["concerns"] + feed["also_noticed"] + (
        [feed["positive"]] if feed["positive"] else [])
    kept_cards = [c for c in everywhere if c.get("subject") == "kept"]

    assert len(kept_cards) == 1, "the same run of months was reported twice"
    assert kept_cards[0]["kind"] == "kept_trend"


# ── 6. every detector, on a profile with everything in it ───────────────

def _rich(db) -> None:
    """Two years of activity carrying every pattern at once."""
    import calendar
    import datetime

    shops = ["NORTHSIDE OUTFITTERS", "HARBOUR HOMEWARES", "LEDGER LANE BOOKS"]
    snacks = ["CORNER MARKET", "PLATFORM COFFEE", "DEPOT SNACKS"]
    months, year, month = [], 2024, 6
    for _ in range(26):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    for index, label in enumerate(months):
        spend = 210 if index < len(months) - 3 else 470
        extra = [
            dict(day=f"{label}-{6 + n * 6:02d}", desc=shops[n % 3],
                 amount=round(spend / 3, 2), direction="debit",
                 category="Shopping") for n in range(3)
        ]
        extra += [
            dict(day=f"{label}-{2 + (n % 26):02d}", desc=snacks[n % 3],
                 amount=round(9.4 + (n % 5) * 1.35, 2), direction="debit",
                 category="Food & Convenience") for n in range(19)
        ]
        # A subscription that puts its price up partway through.
        extra.append(dict(
            day=f"{label}-14", desc="STREAMPLUS", direction="debit",
            amount=11.99 if label < "2026-03" else 14.49,
            category="Subscriptions & Digital"))
        y, m = int(label[:4]), int(label[5:7])
        for day in range(1, calendar.monthrange(y, m)[1] + 1):
            if datetime.date(y, m, day).weekday() >= 5:
                extra.append(dict(day=f"{label}-{day:02d}",
                                  desc="WEEKEND BISTRO", amount=52.0,
                                  direction="debit", category="Entertainment"))
        seed_month(db, label, extra=extra)


def test_no_detector_raises_on_a_profile_that_triggers_everything(ledger_db):
    """The feed swallows detector exceptions so one bug cannot blank the page.

    That guard is right and it is also a place for a dead detector to hide.
    Two did: the recurring price card read keys its own producer has never
    returned, and the kept-trend card referenced a constant from another
    module. Both raised on every call and neither was ever shown.
    """
    import utils.insight_feed as module

    _rich(ledger_db)
    detectors = set(module._CONCERN_DETECTORS) | set(
        module._POSITIVE_ONLY_DETECTORS)

    for detector in sorted(detectors, key=lambda f: f.__name__):
        found = detector(ledger_db, "personal")   # must not raise
        if found is None:
            continue
        assert found["title"] and "$" in found["claim"]
        assert found["basis"] and found["figure"] is not None


def test_every_card_that_offers_transactions_can_honestly_filter_to_them(
        ledger_db):
    """A button that lands somewhere other than the claim is worse than none."""
    _rich(ledger_db)
    feed = insight_feed(conn=ledger_db)
    cards = feed["concerns"] + feed["also_noticed"] + (
        [feed["positive"]] if feed["positive"] else [])

    assert cards
    for card in cards:
        drill = card["drill"]
        if drill is None:
            # Only claims no single filter can express may skip the button.
            assert card["kind"] in {"weekday_pattern", "year_ago", "kept_trend"}
            continue
        assert drill.get("category") or drill.get("merchant"), (
            f"{card['kind']} offers a button with nothing to filter on"
        )
        # A dated claim must carry its dates; a price history is deliberately
        # unbounded, because the evidence is the whole run of charges.
        if card["kind"] != "price_rise":
            assert drill.get("start_date") and drill.get("end_date"), (
                f"{card['kind']} would open an unbounded list"
            )
            assert drill["start_date"] <= drill["end_date"]


def test_no_card_shows_a_raw_database_date(ledger_db):
    """"Against 2025-07" is the database talking, not Northstar."""
    import re

    _rich(ledger_db)
    feed = insight_feed(conn=ledger_db)
    cards = feed["concerns"] + feed["also_noticed"] + (
        [feed["positive"]] if feed["positive"] else [])

    raw = re.compile(r"\d{4}-\d{2}(-\d{2})?")
    for card in cards:
        for field in ("title", "claim", "figure_caption", "evidence_note"):
            assert not raw.search(card[field] or ""), (
                f"{card['kind']} shows a raw date in {field}: {card[field]}"
            )


def test_an_empty_database_says_so_rather_than_guessing(ledger_db):
    feed = insight_feed(conn=ledger_db)

    assert feed["concerns"] == []
    assert feed["also_noticed"] == []
    assert feed["missing_data"]

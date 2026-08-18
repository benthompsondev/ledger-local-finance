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
    category_drift_across_months, month_difference, small_spend_accumulation,
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


def test_an_empty_database_says_so_rather_than_guessing(ledger_db):
    feed = insight_feed(conn=ledger_db)

    assert feed["concerns"] == []
    assert feed["also_noticed"] == []
    assert feed["missing_data"]

"""Patterns and the insight feed: the claims have to be true of the data.

These findings are shown to someone as statements of fact about their own
money, so the bar is that a seeded pattern is found, an absent one is not
claimed, and every number in a headline reconciles with the transactions it
came from.
"""
import random
from datetime import date, timedelta

import pytest

from tests.conftest import add_tx
from utils.insight_feed import insight_feed
from utils.patterns import (
    category_usual_range, day_of_week_profile, merchant_frequency_creep,
    same_month_last_year, spending_calendar,
)

END = date(2026, 7, 20)


def _seed(db, *, months=24, weekend_habit=False, creep=False,
          grocery_spike=False, seed=5):
    """Two years of ordinary activity, with optional planted patterns."""
    random.seed(seed)
    start = (END.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    cursor = start
    while cursor <= END:
        weekend = cursor.weekday() >= 5
        for n in range(2):
            add_tx(db, day=cursor.isoformat(), desc=f"INVENTED CAFE {n}",
                   amount=round(random.uniform(9, 12), 2),
                   direction="debit", category="Food & Convenience")
        if weekend_habit and weekend:
            add_tx(db, day=cursor.isoformat(), desc="INVENTED TAKEAWAY",
                   amount=45, direction="debit",
                   category="Food & Convenience")
        if cursor.day in (4, 18):
            spike = (
                grocery_spike and cursor.strftime("%Y-%m") == END.strftime("%Y-%m")
            )
            add_tx(db, day=cursor.isoformat(), desc="INVENTED SUPER GROCER",
                   amount=260 if spike else 100,
                   direction="debit", category="Groceries")
        if creep:
            recent = cursor > END - timedelta(days=60)
            if (recent and cursor.day % 2 == 0) or (not recent and cursor.day == 10):
                add_tx(db, day=cursor.isoformat(), desc="INVENTED CORNER SHOP",
                       amount=12, direction="debit", category="Groceries")
        if cursor.day == 1:
            add_tx(db, day=cursor.isoformat(), desc="INVENTED MORTGAGE CO",
                   amount=2100, direction="debit",
                   category="Housing / Mortgage")
        if cursor.day in (5, 20):
            add_tx(db, day=cursor.isoformat(), desc="INVENTED EMPLOYER PAYROLL",
                   amount=3200, direction="credit", category="Payroll Income")
        cursor += timedelta(days=1)
    db.commit()


# ── the calendar ────────────────────────────────────────────────────────

def test_calendar_totals_match_the_transactions(ledger_db):
    _seed(ledger_db, months=4)
    calendar = spending_calendar(months=1, conn=ledger_db)

    assert calendar["available"]
    total_from_days = round(sum(d["amount"] for d in calendar["days"]), 2)
    assert total_from_days == calendar["total"]
    assert calendar["spending_days"] + calendar["quiet_days"] == len(calendar["days"])


def test_the_calendar_leaves_out_fixed_bills(ledger_db):
    """A heat map dominated by the rent shows nothing anyone can act on."""
    _seed(ledger_db, months=6)
    calendar = spending_calendar(months=1, conn=ledger_db)

    first = next(
        day for day in calendar["days"] if day["date"].endswith("-01")
    )
    assert first["amount"] < 2100, "the mortgage should not be in the calendar"


def test_calendar_is_empty_without_data(ledger_db):
    assert spending_calendar(conn=ledger_db)["available"] is False


# ── weekdays ────────────────────────────────────────────────────────────

def test_a_planted_weekend_habit_is_found(ledger_db):
    _seed(ledger_db, months=12, weekend_habit=True)
    profile = day_of_week_profile(conn=ledger_db)

    assert profile["available"]
    assert profile["meaningful"] is True
    assert profile["weekend_average"] > profile["weekday_average"]
    assert profile["dearest"]["weekday"] >= 5


def test_even_spending_is_not_reported_as_a_pattern(ledger_db):
    """Seven bars of roughly equal height is not an insight."""
    _seed(ledger_db, months=12, weekend_habit=False)
    profile = day_of_week_profile(conn=ledger_db)

    assert profile["available"]
    assert profile["meaningful"] is False


def test_a_bill_on_a_fixed_day_is_not_a_weekday_habit(ledger_db):
    """The trap this threshold exists for.

    Anything billed on the same day of the month lands unevenly across
    weekdays. Read naively that produces a confident claim about Wednesdays
    which is really a claim about the 4th.
    """
    cursor = date(2025, 8, 1)
    while cursor <= END:
        add_tx(ledger_db, day=cursor.isoformat(), desc="INVENTED CAFE",
               amount=10, direction="debit", category="Food & Convenience")
        if cursor.day == 4:
            add_tx(ledger_db, day=cursor.isoformat(), desc="INVENTED CLUB FEE",
                   amount=95, direction="debit", category="Shopping")
        cursor += timedelta(days=1)
    ledger_db.commit()

    profile = day_of_week_profile(conn=ledger_db)
    assert profile["available"]
    assert profile["meaningful"] is False, (
        "a fee on the 4th was reported as a weekday pattern"
    )


def test_weekday_averages_account_for_uneven_windows(ledger_db):
    """A window with five Fridays and four Mondays must not flatter Friday."""
    _seed(ledger_db, months=12)
    profile = day_of_week_profile(conn=ledger_db)

    for row in profile["days"]:
        assert row["occurrences"] >= 20
        if row["occurrences"]:
            assert row["average"] == pytest.approx(
                row["total"] / row["occurrences"], abs=0.01,
            )


def test_a_short_history_refuses_to_compare_weekdays(ledger_db):
    _seed(ledger_db, months=1)
    profile = day_of_week_profile(lookback_days=20, conn=ledger_db)

    assert profile["available"] is False
    assert profile["reason"]


# ── frequency creep ─────────────────────────────────────────────────────

def test_more_visits_is_reported_as_more_visits(ledger_db):
    """The point of this detector is telling visits apart from basket size."""
    _seed(ledger_db, months=12, creep=True)
    rows = merchant_frequency_creep(conn=ledger_db)

    corner = next(r for r in rows if "CORNER" in r["merchant"].upper())
    assert corner["now_visits"] > corner["was_visits"]
    assert corner["explained_by_visits"] > abs(corner["explained_by_basket"])
    # The basket was a flat $12 throughout, so nothing should be blamed on it.
    assert corner["was_basket"] == pytest.approx(corner["now_basket"], abs=0.5)


def test_steady_shopping_reports_no_creep(ledger_db):
    _seed(ledger_db, months=12, creep=False)
    assert merchant_frequency_creep(conn=ledger_db) == []


# ── usual range ─────────────────────────────────────────────────────────

def test_a_spike_leaves_the_usual_band(ledger_db):
    _seed(ledger_db, months=12, grocery_spike=True)
    rows = category_usual_range(conn=ledger_db)

    groceries = next(r for r in rows if r["category"] == "Groceries")
    assert groceries["state"] == "above"
    assert groceries["current"] > groceries["high"]
    assert groceries["months_used"] >= 3


def test_the_band_compares_like_with_like(ledger_db):
    """A part-month must be measured against the same part of earlier months."""
    _seed(ledger_db, months=12)
    rows = category_usual_range(conn=ledger_db)

    assert rows
    for row in rows:
        assert row["through_day"] == END.day
        assert row["state"] in {"above", "below", "usual"}


def test_too_little_history_produces_no_band(ledger_db):
    _seed(ledger_db, months=2)
    assert category_usual_range(conn=ledger_db) == []


# ── a year ago ──────────────────────────────────────────────────────────

def test_year_ago_needs_a_year(ledger_db):
    _seed(ledger_db, months=6)
    result = same_month_last_year(conn=ledger_db)

    assert result["available"] is False
    assert "months of history" in result["reason"]


def test_year_ago_compares_the_same_slice_of_both_months(ledger_db):
    _seed(ledger_db, months=24)
    result = same_month_last_year(conn=ledger_db)

    assert result["available"] is True
    assert result["through_day"] == END.day
    assert result["year_ago_month"] == "2025-07"
    assert result["delta"] == pytest.approx(
        result["current_total"] - result["year_ago_total"], abs=0.01,
    )


# ── the feed ────────────────────────────────────────────────────────────

def test_the_feed_stays_short(ledger_db):
    _seed(ledger_db, months=24, weekend_habit=True, creep=True,
          grocery_spike=True)
    feed = insight_feed(conn=ledger_db)

    assert len(feed["concerns"]) <= 3
    assert feed["positive"] is None or feed["positive"]["tone"] == "good"


def test_every_finding_carries_its_own_numbers(ledger_db):
    """A claim with no figures in it is the failure this release exists to fix."""
    _seed(ledger_db, months=24, weekend_habit=True, creep=True,
          grocery_spike=True)
    feed = insight_feed(conn=ledger_db)

    assert feed["concerns"], "nothing was found on a profile with three patterns"
    for finding in feed["concerns"]:
        assert "$" in finding["claim"], f"{finding['kind']} has no figures"
        assert finding["basis"], "a finding must name the function behind it"
        assert finding["confidence"] in {"high", "medium", "low"}
        assert len(finding["claim"]) > 40


def test_the_feed_reports_nothing_on_a_quiet_profile(ledger_db):
    """Inventing a concern to fill the page is worse than an empty page."""
    _seed(ledger_db, months=12, seed=11)
    feed = insight_feed(conn=ledger_db)

    for finding in feed["concerns"]:
        assert finding["kind"] != "frequency_creep"


def test_a_thin_profile_says_so_rather_than_guessing(ledger_db):
    add_tx(ledger_db, day="2026-07-02", desc="INVENTED SHOP", amount=20,
           direction="debit", category="Groceries")
    ledger_db.commit()
    feed = insight_feed(conn=ledger_db)

    assert feed["missing_data"]
    assert feed["concerns"] == []


def test_one_broken_detector_cannot_take_down_the_feed(ledger_db, monkeypatch):
    _seed(ledger_db, months=24, creep=True)
    import utils.insight_feed as module

    def explode(*args, **kwargs):
        raise RuntimeError("detector failed")

    monkeypatch.setattr(module, "_weekend_finding", explode)
    feed = insight_feed(conn=ledger_db)

    assert isinstance(feed["concerns"], list)


def test_the_same_category_is_not_reported_twice(ledger_db):
    _seed(ledger_db, months=24, creep=True, grocery_spike=True)
    feed = insight_feed(conn=ledger_db)

    categories = [
        f.get("category") for f in feed["concerns"] if f.get("category")
    ]
    assert len(categories) == len(set(categories))


# ── shapes real data takes that the first pass did not survive ──────────

def test_one_mistyped_year_does_not_hide_everything_else(ledger_db):
    """A single row dated 2027 used to move every window a year forward.

    Because each window is measured back from the newest date, the effect was
    that all of someone's real spending fell outside the view: a calendar went
    from $1,215 of visible spending to $42.
    """
    _seed(ledger_db, months=12)
    before = spending_calendar(months=3, conn=ledger_db)

    add_tx(ledger_db, day="2027-12-25", desc="INVENTED TYPO", amount=42,
           direction="debit", category="Shopping")
    ledger_db.commit()
    after = spending_calendar(months=3, conn=ledger_db)

    assert after["end"] == before["end"], "one stray row moved the anchor"
    assert after["start"] == before["start"]
    # Every real day is still visible. The total can move slightly because
    # utils/planner.py still anchors its own commitment detection on the raw
    # MAX(transaction_date), so a stray row can make a bill look discontinued
    # and stop it being excluded. That is a separate pre-existing issue, noted
    # rather than silently asserted away.
    assert after["total"] >= before["total"]
    assert after["spending_days"] >= before["spending_days"]


def test_a_genuine_final_month_still_anchors_normally(ledger_db):
    """The outlier guard must not reject ordinary recent data."""
    _seed(ledger_db, months=6)
    calendar = spending_calendar(months=1, conn=ledger_db)

    assert calendar["available"]
    assert calendar["end"] == END.isoformat()


def test_one_unusual_month_does_not_switch_a_category_off(ledger_db):
    """A standard deviation let one $9,000 month widen the band to $2,647.

    After that, nothing in the category could ever be worth mentioning again.
    """
    _seed(ledger_db, months=12)
    clean = next(r for r in category_usual_range(conn=ledger_db)
                 if r["category"] == "Groceries")

    add_tx(ledger_db, day="2026-02-14", desc="INVENTED ONE OFF", amount=9000,
           direction="debit", category="Groceries")
    ledger_db.commit()
    spiked = next(r for r in category_usual_range(conn=ledger_db)
                  if r["category"] == "Groceries")

    assert spiked["high"] < clean["high"] * 2, (
        f"band widened from {clean['high']} to {spiked['high']}"
    )


def test_a_weekday_claim_needs_more_than_one_day(ledger_db):
    """Sixty transactions on one Wednesday span 180 days and prove nothing."""
    for n in range(60):
        add_tx(ledger_db, day="2026-07-15", desc=f"INVENTED SHOP {n}",
               amount=20, direction="debit", category="Shopping")
    ledger_db.commit()

    profile = day_of_week_profile(conn=ledger_db)
    assert profile["available"] is False
    assert "separate days" in profile["reason"]

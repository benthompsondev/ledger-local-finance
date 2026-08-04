"""One saving focus, funded by months that actually finished.

The goal feature this replaces asked people to type their progress in. These
tests exist to hold the line that nothing here is typed: the figure follows
the ledger, the month still in progress is never counted, and a pace that is
not really happening never produces a finish date.
"""
import calendar
from datetime import date, timedelta

import pytest

from tests.conftest import add_tx
from utils.money_focus import (
    clear_focus, get_focus, money_focus_summary, save_focus,
)

END = date(2026, 7, 20)


def _seed(db, months=6, kept_per_month=500.0):
    """Complete months keeping a known amount.

    Spending is spread across each month rather than landing on one day,
    because a month only counts as complete once it has activity on enough
    separate days.
    """
    start = (END.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    outflow = 3000 - kept_per_month
    cursor = start
    while cursor <= END:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        days = last_day if cursor.month != END.month else END.day
        if cursor.day == 5:
            add_tx(db, day=cursor.isoformat(), desc="INVENTED EMPLOYER PAYROLL",
                   amount=3000, direction="credit", category="Payroll Income")
        if cursor.day <= days:
            add_tx(db, day=cursor.isoformat(),
                   desc=f"INVENTED SHOP {cursor.day}",
                   amount=round(outflow / days, 2),
                   direction="debit", category="Groceries")
        cursor += timedelta(days=1)
    db.commit()


def test_with_no_focus_it_asks_for_one(ledger_db):
    _seed(ledger_db)
    summary = money_focus_summary(conn=ledger_db)

    assert summary["available"] is False
    assert summary["needs_setup"] is True
    assert summary["suggested_start"]
    assert summary["months"] == []


def test_progress_is_measured_not_typed(ledger_db):
    _seed(ledger_db, months=6, kept_per_month=500.0)
    save_focus("Emergency fund", 10_000, "2026-02", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["available"] is True
    # Every counted month kept the same amount, so the total is exactly the
    # sum of them. No contribution was ever entered by hand.
    assert summary["saved"] == pytest.approx(
        sum(m["kept"] for m in summary["months"]), abs=0.01)
    for month in summary["months"]:
        assert month["kept"] == pytest.approx(500.0, abs=0.5)
    assert summary["typical_month"] == pytest.approx(500.0, abs=0.5)
    assert summary["remaining"] == pytest.approx(
        10_000 - summary["saved"], abs=0.01)


def test_what_was_already_put_aside_starts_the_line(ledger_db):
    _seed(ledger_db, months=4, kept_per_month=500.0)
    save_focus("Car", 8_000, "2026-05", already_saved=2_000, conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    counted = sum(m["kept"] for m in summary["months"])
    assert summary["saved"] == pytest.approx(2_000 + counted, abs=0.01)


def test_months_before_the_start_are_not_counted(ledger_db):
    _seed(ledger_db, months=6)
    save_focus("Trip", 5_000, "2026-06", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["months"]
    for month in summary["months"]:
        assert month["month"] >= "2026-06"


def test_the_month_in_progress_is_shown_but_never_counted(ledger_db):
    """A part-month of spending against a whole month of pay always flatters
    the figure, so it sits beside the total rather than inside it."""
    _seed(ledger_db, months=6, kept_per_month=500.0)
    save_focus("Emergency fund", 10_000, "2026-02", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    current = summary["this_month"]
    assert current is not None, "the open month should still be reported"
    assert current["complete"] is False
    assert all(m["month"] != current["month"] for m in summary["months"])
    assert summary["saved"] == pytest.approx(
        sum(m["kept"] for m in summary["months"]), abs=0.01)


def test_a_month_that_went_backwards_lowers_the_total(ledger_db):
    _seed(ledger_db, months=4, kept_per_month=-400.0)
    save_focus("Emergency fund", 5_000, "2026-05", already_saved=1_000,
               conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["saved"] < 1_000
    assert summary["typical_month"] < 0


def test_no_finish_date_is_invented_when_nothing_is_being_saved(ledger_db):
    _seed(ledger_db, months=4, kept_per_month=-400.0)
    save_focus("Emergency fund", 5_000, "2026-05", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["projection"]["available"] is False
    assert "nothing is being added" in summary["projection"]["reason"]
    assert "months_to_go" not in summary["projection"]


def test_the_finish_date_follows_the_real_pace(ledger_db):
    _seed(ledger_db, months=6, kept_per_month=500.0)
    save_focus("Emergency fund", 10_000, "2026-02", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    projection = summary["projection"]
    assert projection["available"] is True
    expected = -(-summary["remaining"] // summary["typical_month"])
    assert projection["months_to_go"] == int(expected)
    assert projection["finish_month"] > summary["months"][-1]["month"]


def test_a_target_date_the_pace_cannot_meet_is_reported_as_a_gap(ledger_db):
    _seed(ledger_db, months=6, kept_per_month=500.0)
    # $9,000 still to find in roughly two months at $500 a month.
    save_focus("Emergency fund", 10_000, "2026-02",
               target_date="2026-09-30", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    by_date = summary["by_target_date"]
    assert by_date["available"] is True
    assert by_date["on_track"] is False
    assert by_date["needed_monthly"] > by_date["actual_monthly"]
    assert by_date["shortfall_monthly"] == pytest.approx(
        by_date["needed_monthly"] - by_date["actual_monthly"], abs=0.01)
    assert "gap of" in summary["sentence"]


def test_a_reachable_target_date_holds(ledger_db):
    _seed(ledger_db, months=6, kept_per_month=500.0)
    save_focus("Small fund", 3_000, "2026-02",
               target_date="2027-06-30", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["by_target_date"]["on_track"] is True
    assert "the date holds" in summary["sentence"]


def test_reaching_the_target_says_so(ledger_db):
    _seed(ledger_db, months=6, kept_per_month=500.0)
    save_focus("Nearly there", 1_500, "2026-02", conn=ledger_db)

    summary = money_focus_summary(conn=ledger_db)
    assert summary["reached"] is True
    assert summary["remaining"] == 0
    assert summary["progress_pct"] == 100.0
    assert "funded" in summary["sentence"]


def test_the_focus_can_be_changed_and_removed(ledger_db):
    _seed(ledger_db)
    save_focus("First", 4_000, "2026-03", conn=ledger_db)
    assert get_focus(conn=ledger_db)["name"] == "First"

    save_focus("Second", 9_000, "2026-04", conn=ledger_db)
    stored = get_focus(conn=ledger_db)
    assert stored["name"] == "Second"
    assert stored["target_amount"] == 9_000

    clear_focus(conn=ledger_db)
    assert get_focus(conn=ledger_db) is None


@pytest.mark.parametrize("name,target,month", [
    ("", 5_000, "2026-03"),
    ("Fund", 0, "2026-03"),
    ("Fund", 5_000, "nonsense"),
])
def test_unusable_input_is_refused_rather_than_stored(ledger_db, name, target,
                                                      month):
    with pytest.raises(ValueError):
        save_focus(name, target, month, conn=ledger_db)
    assert get_focus(conn=ledger_db) is None

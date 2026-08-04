"""Month-end pace and category reconciliation across calendar boundaries.

A 31-day month has no counterpart day in a 28, 29 or 30-day one. The comparison
must cap at the shared day, but the current month's own totals must never be
shortened: they have to keep reconciling with canonical current-month spending.

Regression cover for the July 31 defect, where the current month was truncated
to the previous month's length and current-month spending silently vanished.
"""
import calendar
from datetime import date

import pytest

from tests.conftest import add_tx
from utils.insights import category_pace_breakdown, spending_pace

# (analysis date, days in the prior month) — every shape a year produces.
BOUNDARY_CASES = [
    pytest.param(date(2026, 7, 31), 30, id="jul31-after-30-day-june"),
    pytest.param(date(2026, 5, 31), 30, id="may31-after-30-day-april"),
    pytest.param(date(2026, 3, 31), 28, id="mar31-after-28-day-february"),
    pytest.param(date(2024, 3, 31), 29, id="mar31-after-leap-february"),
    pytest.param(date(2026, 1, 31), 31, id="jan31-after-31-day-december"),
    pytest.param(date(2026, 7, 20), 30, id="ordinary-day-20"),
]


def _seed_ten_a_day(conn, analysis_date):
    """One invented $10 purchase on every day of the current and prior month."""
    prev_year, prev_month = (
        (analysis_date.year, analysis_date.month - 1)
        if analysis_date.month > 1 else (analysis_date.year - 1, 12)
    )
    for year, month in ((prev_year, prev_month),
                        (analysis_date.year, analysis_date.month)):
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            add_tx(
                conn,
                day=f"{year:04d}-{month:02d}-{day:02d}",
                desc=f"INVENTED MARKET {year}{month}{day}",
                amount=10.0,
                direction="debit",
                category="Groceries",
            )


@pytest.mark.parametrize("analysis_date,prev_days", BOUNDARY_CASES)
def test_current_month_total_covers_every_day_through_analysis_date(
    ledger_db, analysis_date, prev_days,
):
    _seed_ten_a_day(ledger_db, analysis_date)

    pace = spending_pace(conn=ledger_db, today=analysis_date)

    expected = analysis_date.day * 10.0
    assert pace["current_total"] == pytest.approx(expected)
    assert pace["current_through_day"] == analysis_date.day
    assert len(pace["current"]) == analysis_date.day


@pytest.mark.parametrize("analysis_date,prev_days", BOUNDARY_CASES)
def test_comparison_caps_at_the_shared_day(ledger_db, analysis_date, prev_days):
    _seed_ten_a_day(ledger_db, analysis_date)

    pace = spending_pace(conn=ledger_db, today=analysis_date)

    shared_day = min(analysis_date.day, prev_days)
    assert pace["comparison_through_day"] == shared_day
    assert pace["previous_total_same_day"] == pytest.approx(shared_day * 10.0)
    # The delta compares equal spans, so an even $10/day pace shows no drift
    # even when the current month runs one day longer than the prior one.
    assert pace["current_total_comparable"] == pytest.approx(shared_day * 10.0)
    assert pace["delta"] == pytest.approx(0.0)


@pytest.mark.parametrize("analysis_date,prev_days", BOUNDARY_CASES)
def test_category_pace_total_reconciles_with_current_month(
    ledger_db, analysis_date, prev_days,
):
    _seed_ten_a_day(ledger_db, analysis_date)

    breakdown = category_pace_breakdown(conn=ledger_db, today=analysis_date)
    pace = spending_pace(conn=ledger_db, today=analysis_date)

    expected = analysis_date.day * 10.0
    assert breakdown["total"] == pytest.approx(expected)
    assert breakdown["total"] == pytest.approx(pace["current_total"])
    assert sum(item["amount"] for item in breakdown["items"]) == pytest.approx(expected)


def test_every_category_is_summed_into_the_headline(ledger_db):
    """The reconciliation that broke: categories present only on the trailing
    day must still reach the total."""
    analysis_date = date(2026, 7, 31)
    for index, category in enumerate(
        ["Groceries", "Restaurant", "Gas", "Drug Store", "E-Games"]
    ):
        add_tx(
            ledger_db,
            day="2026-07-31",
            desc=f"INVENTED SHOP {index}",
            amount=10.0 + index,
            direction="debit",
            category=category,
        )

    breakdown = category_pace_breakdown(conn=ledger_db, today=analysis_date)

    # Categorization may relabel these merchants, so assert on reconciliation
    # rather than on category names: nothing spent on day 31 may be dropped.
    expected = sum(10.0 + index for index in range(5))
    assert breakdown["total"] == pytest.approx(expected)
    assert sum(item["amount"] for item in breakdown["items"]) == pytest.approx(expected)
    assert breakdown["items"], "trailing-day spending produced no categories"


def test_flexible_and_total_views_both_cover_the_trailing_day(ledger_db):
    analysis_date = date(2026, 7, 31)
    _seed_ten_a_day(ledger_db, analysis_date)

    total_view = spending_pace(conn=ledger_db, today=analysis_date)
    flexible_view = spending_pace(
        conn=ledger_db, today=analysis_date, flexible_only=True,
    )

    assert total_view["current_through_day"] == 31
    assert flexible_view["current_through_day"] == 31
    assert total_view["current_total"] == pytest.approx(310.0)
    # Flexible removes reviewed commitments, never calendar days.
    assert len(flexible_view["current"]) == 31


def test_wording_inputs_flag_a_short_comparison_month(ledger_db):
    """The UI decides its wording from these two fields, so they must disagree
    exactly when the prior month ended earlier."""
    _seed_ten_a_day(ledger_db, date(2026, 7, 31))
    short = spending_pace(conn=ledger_db, today=date(2026, 7, 31))
    assert short["current_through_day"] == 31
    assert short["comparison_through_day"] == 30
    assert short["previous_days_in_month"] == 30

    equal = spending_pace(conn=ledger_db, today=date(2026, 7, 20))
    assert equal["current_through_day"] == equal["comparison_through_day"] == 20


def test_category_deltas_use_equal_spans_not_the_full_month(ledger_db):
    """A category that only spends on day 31 must not report that as growth
    against a prior month that had no day 31."""
    for day in range(1, 31):
        add_tx(ledger_db, day=f"2026-06-{day:02d}", desc="INVENTED MARKET",
               amount=10.0, direction="debit", category="Groceries")
    for day in range(1, 31):
        add_tx(ledger_db, day=f"2026-07-{day:02d}", desc="INVENTED MARKET",
               amount=10.0, direction="debit", category="Groceries")
    add_tx(ledger_db, day="2026-07-31", desc="INVENTED MARKET",
           amount=99.0, direction="debit", category="Groceries")

    breakdown = category_pace_breakdown(conn=ledger_db, today=date(2026, 7, 31))
    groceries = next(i for i in breakdown["items"] if i["category"] == "Groceries")

    # Full month-to-date, including the trailing day.
    assert groceries["amount"] == pytest.approx(399.0)
    # But the comparison ignores the day June never had.
    assert groceries["previous"] == pytest.approx(300.0)
    assert groceries["delta"] == pytest.approx(0.0)


def _seed_four_months(conn):
    """$10 every day, April through July 2026."""
    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        year, month_number = map(int, month.split("-"))
        for day in range(1, calendar.monthrange(year, month_number)[1] + 1):
            add_tx(
                conn,
                day=f"{month}-{day:02d}",
                desc=f"INVENTED MARKET {month}{day}",
                amount=10.0,
                direction="debit",
                category="Groceries",
            )


def test_category_and_overall_three_month_deltas_agree(ledger_db):
    """When one category holds all the spending, its average delta has to
    match the overall one. They disagreed because the category compared a
    day-30 figure against an average built from whole months."""
    _seed_four_months(ledger_db)
    analysis_date = date(2026, 7, 31)

    pace = spending_pace(conn=ledger_db, today=analysis_date)
    breakdown = category_pace_breakdown(conn=ledger_db, today=analysis_date)
    groceries = next(
        item for item in breakdown["items"] if item["category"] == "Groceries"
    )

    assert groceries["amount"] == pytest.approx(310.0)
    assert groceries["average"] == pytest.approx(303.33, abs=0.01)
    assert groceries["average_delta"] == pytest.approx(6.67, abs=0.01)
    assert pace["average_90_day_delta"] == pytest.approx(
        groceries["average_delta"], abs=0.01
    )


def test_prior_month_delta_stays_capped_to_the_shared_day(ledger_db):
    """Fixing the average must not loosen the adjacent-month comparison."""
    _seed_four_months(ledger_db)

    breakdown = category_pace_breakdown(conn=ledger_db, today=date(2026, 7, 31))
    groceries = next(
        item for item in breakdown["items"] if item["category"] == "Groceries"
    )

    # June has no day 31, so an even $10/day pace shows no month-over-month
    # drift even though July's own total is $10 higher.
    assert groceries["previous"] == pytest.approx(300.0)
    assert groceries["delta"] == pytest.approx(0.0)
    assert breakdown["comparison_through_day"] == 30
    assert breakdown["current_through_day"] == 31


def test_category_totals_still_reconcile_after_the_average_change(ledger_db):
    _seed_four_months(ledger_db)

    breakdown = category_pace_breakdown(conn=ledger_db, today=date(2026, 7, 31))
    pace = spending_pace(conn=ledger_db, today=date(2026, 7, 31))

    assert breakdown["total"] == pytest.approx(pace["current_total"])
    assert sum(i["amount"] for i in breakdown["items"]) == pytest.approx(310.0)

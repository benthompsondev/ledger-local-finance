"""A bill belongs to the month it is actually due in.

All figures are invented.

`forecast_month` asked one question about every reviewed commitment: has this
merchant been seen since the 1st? For rent that is the right question. For a
quarterly premium or an annual property-tax invoice it is the wrong one, and
it fails in the expensive direction — an annual $2,400 bill due in November
had not been seen this month in June either, so June was charged the full
$2,400. So was July, and August, and every other month of the year.

Reproduced before the fix, forecasting 2026-06 against an annual $2,400 bill
expected 2026-11-20 and a quarterly $540 premium expected 2026-07-16:

    upcoming_bills_total   $2,940.00      (both, in a month neither is due)
    projected_spending     $5,331.00
    risk_level             danger

Both belonged to other months. A slow bill is now placed by the occurrence
its cadence expects, and the plain monthly rule is left alone.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import pytest

from tests.conftest import add_tx, seed_month
from utils.analysis_period import reset_supported_bounds_cache
from utils.planner import (
    bills_and_commitments, commitment_placement, forecast_month,
)

RENT = 1800.0
PAYROLL = 2500.0
ANNUAL_TAX = 2400.0
QUARTERLY_PREMIUM = 540.0


# ── placement, decided directly ──────────────────────────────────────────
#
# commitment_placement is the whole of the fix, so it is asserted on its own
# before any of it is read through a forecast.

def _item(**overrides) -> dict:
    base = {
        "merchant": "INVENTED MERCHANT", "est_amount": 100.0,
        "cadence": "monthly", "period_months": 1,
        "last_seen": "", "expected_next": "", "included_in_forecast": True,
    }
    base.update(overrides)
    return base


def test_annual_bill_due_in_november_is_not_a_june_bill() -> None:
    placement = commitment_placement(_item(
        merchant="INVENTED CITY PROPERTY TAX", est_amount=ANNUAL_TAX,
        cadence="annual", period_months=12,
        last_seen="2025-11-20", expected_next="2026-11-20",
    ), "2026-06")

    assert placement == "other_month"


def test_quarterly_bill_due_in_july_is_not_a_june_bill() -> None:
    placement = commitment_placement(_item(
        merchant="INVENTED INSURANCE CO", est_amount=QUARTERLY_PREMIUM,
        cadence="quarterly", period_months=3,
        last_seen="2026-04-16", expected_next="2026-07-16",
    ), "2026-06")

    assert placement == "other_month"


def test_quarterly_bill_due_later_in_june_is_a_june_bill() -> None:
    """Not yet paid, but genuinely landing this month. Must stay visible."""
    placement = commitment_placement(_item(
        merchant="INVENTED INSURANCE CO", est_amount=QUARTERLY_PREMIUM,
        cadence="quarterly", period_months=3,
        last_seen="2026-03-24", expected_next="2026-06-24",
    ), "2026-06")

    assert placement == "due"


def test_a_bill_already_paid_this_month_is_not_counted_again() -> None:
    for cadence, period, last_seen in (
        ("monthly", 1, "2026-06-01"),
        ("quarterly", 3, "2026-06-16"),
        ("annual", 12, "2026-06-20"),
    ):
        assert commitment_placement(_item(
            cadence=cadence, period_months=period, last_seen=last_seen,
            expected_next="2026-09-16",
        ), "2026-06") == "paid", cadence


def test_monthly_rent_not_yet_seen_is_still_an_upcoming_bill() -> None:
    """The monthly rule is unchanged: silence means it is probably coming."""
    placement = commitment_placement(_item(
        merchant="INVENTED LANDLORD", est_amount=RENT,
        cadence="monthly", period_months=1,
        last_seen="2026-05-01", expected_next="2026-06-02",
    ), "2026-06")

    assert placement == "due"


def test_a_monthly_bill_is_placed_without_needing_an_expected_date() -> None:
    """Subscriptions reach the forecast with no expected_next at all."""
    assert commitment_placement(_item(
        cadence="monthly", period_months=1, last_seen="2026-05-14",
        expected_next=None,
    ), "2026-06") == "due"


def test_a_slow_bill_with_no_expected_date_is_not_charged_to_this_month():
    """Guessing costs the user real money eleven months out of twelve."""
    assert commitment_placement(_item(
        cadence="annual", period_months=12, last_seen="2025-11-20",
        expected_next=None,
    ), "2026-06") == "other_month"


def test_an_occurrence_whose_date_has_passed_is_marked_overdue() -> None:
    """It must not read as an ordinary upcoming bill."""
    assert commitment_placement(_item(
        cadence="quarterly", period_months=3, last_seen="2026-02-16",
        expected_next="2026-05-16",
    ), "2026-06") == "overdue"


# ── the year boundary ────────────────────────────────────────────────────

def test_a_january_occurrence_expected_from_december_history() -> None:
    """String months compare correctly across the year only if the year is
    part of the comparison. '2026-01' < '2025-10' is false; '01' < '10' is
    not, which is the shape of bug this pins."""
    quarterly = _item(
        merchant="INVENTED INSURANCE CO", est_amount=QUARTERLY_PREMIUM,
        cadence="quarterly", period_months=3,
        last_seen="2025-10-15", expected_next="2026-01-15",
    )

    assert commitment_placement(quarterly, "2026-01") == "due"
    assert commitment_placement(quarterly, "2025-12") == "other_month"
    assert commitment_placement(quarterly, "2026-02") == "overdue"


def test_a_december_occurrence_is_not_charged_to_the_following_january():
    annual = _item(
        cadence="annual", period_months=12,
        last_seen="2024-12-05", expected_next="2025-12-05",
    )

    assert commitment_placement(annual, "2025-12") == "due"
    assert commitment_placement(annual, "2026-01") == "overdue"
    assert commitment_placement(annual, "2025-06") == "other_month"


# ── through a real forecast ──────────────────────────────────────────────

@pytest.fixture
def slow_bills(ledger_db):
    """Five ordinary months, one annual bill and one quarterly premium.

    June is imported through the 6th. The annual bill is due in November and
    the quarterly premium in July, so June should see neither.
    """
    for month in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        seed_month(ledger_db, month)

    add_tx(ledger_db, day="2026-06-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    add_tx(ledger_db, day="2026-06-05", desc="EMPLOYER PAYROLL",
           amount=PAYROLL, direction="credit", category="Payroll Income")
    add_tx(ledger_db, day="2026-06-05", desc="HYDRO CO", amount=90.0,
           direction="debit", category="Utilities / Bills")
    for day in range(2, 7):
        add_tx(ledger_db, day=f"2026-06-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")

    # Annual: two November occurrences, so the next is 2026-11-20.
    for year in (2024, 2025):
        add_tx(ledger_db, day=f"{year}-11-20",
               desc="INVENTED CITY PROPERTY TAX", amount=ANNUAL_TAX,
               direction="debit", category="Utilities / Bills")
    # Quarterly: four occurrences ending 2026-04-16, so next is 2026-07-16.
    for quarter in range(4):
        stamp = date(2026, 4, 16) - timedelta(days=91 * quarter)
        add_tx(ledger_db, day=stamp.isoformat(), desc="INVENTED INSURANCE CO",
               amount=QUARTERLY_PREMIUM, direction="debit",
               category="Insurance")
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def test_the_detector_still_finds_both_slow_bills(slow_bills) -> None:
    """The fix must not work by making slow bills invisible again."""
    items = {
        item["merchant"]: item
        for item in bills_and_commitments(conn=slow_bills)["items"]
    }

    assert items["INVENTED CITY PROPERTY TAX"]["cadence"] == "annual"
    assert items["INVENTED CITY PROPERTY TAX"]["expected_next"] == "2026-11-20"
    assert items["INVENTED INSURANCE CO"]["cadence"] == "quarterly"
    assert items["INVENTED INSURANCE CO"]["expected_next"] == "2026-07-16"


def test_june_is_charged_for_neither_slow_bill(slow_bills) -> None:
    """The reproduction: $2,940 of other months' bills, and a danger verdict."""
    forecast = forecast_month("2026-06", conn=slow_bills, today="2026-06-06")

    assert forecast["upcoming_bills_total"] == pytest.approx(0.0)
    assert forecast["overdue_bills_total"] == pytest.approx(0.0)
    assert forecast["projected_spending"] < 3000.0, (
        "a November bill and a July bill were charged against June"
    )
    assert forecast["risk_level"] != "danger"


def test_july_is_charged_for_the_quarterly_premium_once(slow_bills) -> None:
    """And the annual bill still stays out of it."""
    forecast = forecast_month("2026-07", conn=slow_bills, today="2026-07-02")

    assert forecast["upcoming_bills_total"] == pytest.approx(
        QUARTERLY_PREMIUM + RENT + 90.0
    ), "July owes rent, hydro and the premium — nothing else"
    due = {row["merchant"] for row in forecast["upcoming_bills"]}
    assert "INVENTED INSURANCE CO" in due
    assert "INVENTED CITY PROPERTY TAX" not in due


def test_november_is_charged_for_the_annual_bill(slow_bills) -> None:
    forecast = forecast_month("2026-11", conn=slow_bills, today="2026-11-02")

    due = {
        row["merchant"]: row["amount"]
        for row in forecast["upcoming_bills"] if row["status"] == "due"
    }
    assert due.get("INVENTED CITY PROPERTY TAX") == pytest.approx(ANNUAL_TAX)


def test_the_full_occurrence_and_the_monthly_reserve_are_never_both_charged(
    slow_bills,
) -> None:
    """The invoice belongs to the forecast; the set-aside belongs to Plan.

    Charging a $540 premium to July and also removing its $180 monthly
    reserve there would take $720 out of one month for a $540 bill.
    """
    bills = bills_and_commitments(conn=slow_bills)
    reserve = float(bills["nonmonthly_monthly_reserve"])
    assert reserve > 0, "the fixture must actually have a nonmonthly reserve"

    july = forecast_month("2026-07", conn=slow_bills, today="2026-07-02")
    june = forecast_month("2026-06", conn=slow_bills, today="2026-06-06")

    # July carries the invoice at face value and nothing on top of it.
    assert july["upcoming_bills_total"] == pytest.approx(
        QUARTERLY_PREMIUM + RENT + 90.0
    )
    # June carries neither the invoice nor the reserve.
    assert june["upcoming_bills_total"] == pytest.approx(0.0)
    assert june["projected_spending"] == pytest.approx(
        june["fixed_paid_so_far"] + june["flexible_so_far"]
        + june["projected_remaining_flexible"], abs=0.02,
    )


def test_a_bill_paid_this_month_is_not_added_on_top_of_itself(slow_bills):
    """June's rent is already in mtd_spending; it cannot also be upcoming."""
    forecast = forecast_month("2026-06", conn=slow_bills, today="2026-06-06")

    assert forecast["mtd_spending"] >= RENT
    assert all(
        row["merchant"] != "LANDLORD RENT"
        for row in forecast["upcoming_bills"]
    )
    assert forecast["projected_spending"] < forecast["mtd_spending"] + RENT


def test_monthly_rent_is_still_forecast_when_the_month_has_not_paid_it(
    slow_bills,
) -> None:
    forecast = forecast_month("2026-07", conn=slow_bills, today="2026-07-02")

    rent = next(
        row for row in forecast["upcoming_bills"]
        if row["merchant"] == "LANDLORD RENT"
    )
    assert rent["status"] == "due"
    assert rent["amount"] == pytest.approx(RENT)


# ── overdue is reported, not disguised ───────────────────────────────────

@pytest.fixture
def overdue_premium(ledger_db):
    """A quarterly premium whose May occurrence never arrived."""
    for month in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        seed_month(ledger_db, month)
    for day in range(1, 7):
        add_tx(ledger_db, day=f"2026-06-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")
    # Occurrences 91 days apart ending 2026-02-16, so 2026-05-16 was expected
    # and never came.
    for quarter in range(4):
        stamp = date(2026, 2, 16) - timedelta(days=91 * quarter)
        add_tx(ledger_db, day=stamp.isoformat(), desc="INVENTED INSURANCE CO",
               amount=QUARTERLY_PREMIUM, direction="debit",
               category="Insurance")
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def test_an_overdue_bill_is_allowed_for_but_reported_separately(
    overdue_premium,
) -> None:
    forecast = forecast_month("2026-06", conn=overdue_premium,
                              today="2026-06-06")
    if forecast["overdue_bills_count"] == 0:
        pytest.fail(
            "the fixture stopped producing an overdue occurrence; "
            f"bills seen: {forecast['upcoming_bills']}"
        )

    # Money is still set against the month, so nobody is told it is free.
    assert forecast["committed_bills_total"] >= forecast["overdue_bills_total"]
    # But it is not sitting in the ordinary upcoming figure.
    assert forecast["upcoming_bills_total"] == pytest.approx(
        forecast["committed_bills_total"] - forecast["overdue_bills_total"]
    )
    assert "not been imported" in forecast["overdue_bills_note"]
    assert any(
        row["status"] == "overdue" for row in forecast["upcoming_bills"]
    )


def test_a_clean_month_carries_no_overdue_wording(slow_bills) -> None:
    forecast = forecast_month("2026-06", conn=slow_bills, today="2026-06-06")

    assert forecast["overdue_bills_count"] == 0
    assert forecast["overdue_bills_note"] == ""


# ── the arithmetic still adds up ─────────────────────────────────────────

def test_projected_spending_is_its_own_components(slow_bills) -> None:
    """No hidden term, in any month, whichever bills fall due."""
    for month, as_of in (
        ("2026-06", "2026-06-06"), ("2026-07", "2026-07-02"),
        ("2026-11", "2026-11-02"),
    ):
        forecast = forecast_month(month, conn=slow_bills, today=as_of)
        assert forecast["projected_spending"] == pytest.approx(
            forecast["fixed_paid_so_far"]
            + forecast["committed_bills_total"]
            + forecast["flexible_so_far"]
            + forecast["projected_remaining_flexible"],
            abs=0.02,
        ), month


def test_the_annual_bill_falls_due_in_exactly_one_month(slow_bills):
    """The defect charged the full invoice as an ordinary upcoming bill in
    all twelve months. It is now due in November and in no other month.

    Months after November read the same occurrence as overdue, because this
    fixture's imports stop in June and the bill is therefore still unpaid.
    That state is counted, but it is labelled, and it is not the confident
    "upcoming bill" the defect produced. Importing November's statement ends
    it: the occurrence reads as paid and the next one moves to 2027.
    """
    due_months, overdue_months = [], []
    for number in range(1, 13):
        month = f"2026-{number:02d}"
        last_day = calendar.monthrange(2026, number)[1]
        forecast = forecast_month(
            month, conn=slow_bills, today=f"{month}-{last_day:02d}",
        )
        for row in forecast["upcoming_bills"]:
            if row["merchant"] != "INVENTED CITY PROPERTY TAX":
                continue
            assert row["amount"] == pytest.approx(ANNUAL_TAX)
            (due_months if row["status"] == "due" else overdue_months).append(
                month
            )

    assert due_months == ["2026-11"], (
        f"an annual bill fell due in {len(due_months)} months: {due_months}"
    )
    assert all(month > "2026-11" for month in overdue_months), (
        f"a bill was overdue before its date: {overdue_months}"
    )

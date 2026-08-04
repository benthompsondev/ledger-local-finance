"""Plan may not count a payday that came and went without the money.

All figures are invented.

`forecast_month` set expected income to the whole historical monthly baseline
and judged risk on it, no matter how late in the month it was or whether the
expected payroll date had already passed. Reproduced before the fix, with six
complete months of two $2,500 deposits and the second July deposit absent:

    mtd_income                $2,500.00
    expected_income_forecast  $4,583.33
    projected_net               -$66.00
    expected_projected_net    $2,434.00
    risk_level                on_track
    confidence                normal

The household was two thirds of the way through a month having received half
its income, and the screen said on track with normal confidence. A budgeting
app telling someone they have money they do not have is the worst failure it
has available.

Expected income now covers deposits whose date is still ahead. Money that was
due and did not arrive is dropped, and the verdict it was propping up with it.
"""
from __future__ import annotations

import pytest

from tests.conftest import add_tx, seed_month
from utils.analysis_period import reset_supported_bounds_cache
from utils.analytics import scheduled_income_outlook
from utils.planner import forecast_month

PAYROLL = 2500.0
RENT = 1800.0


def _open_month(conn, month: str, *, through: int, payrolls=(5, 19),
                payroll_amount: float = PAYROLL) -> None:
    """The month being forecast: rent, groceries, and chosen paydays."""
    add_tx(conn, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    add_tx(conn, day=f"{month}-05", desc="HYDRO CO", amount=90.0,
           direction="debit", category="Utilities / Bills")
    for day in payrolls:
        if day <= through:
            desc = "EMPLOYER PAYROLL" if day == 5 else "EMPLOYER PAYROLL 2"
            add_tx(conn, day=f"{month}-{day:02d}", desc=desc,
                   amount=payroll_amount, direction="credit",
                   category="Payroll Income")
    for day in range(2, min(through, 28) + 1):
        add_tx(conn, day=f"{month}-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")
    # The forecast anchors on the last imported row, so the month has to
    # actually reach the day the test is asking about.
    add_tx(conn, day=f"{month}-{through:02d}", desc="COFFEE STOP", amount=6.0,
           direction="debit", category="Food & Convenience")
    conn.commit()
    reset_supported_bounds_cache()


@pytest.fixture
def twice_monthly(ledger_db):
    """Six complete months paying on the 5th and the 19th."""
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


# ── the reproduction ─────────────────────────────────────────────────────

def test_day_28_with_the_second_payroll_missing_is_not_on_track(twice_monthly):
    _open_month(twice_monthly, "2026-07", through=28, payrolls=(5,))

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-28")

    assert forecast["mtd_income"] == pytest.approx(PAYROLL)
    assert forecast["risk_level"] != "on_track", (
        "half the month's income never arrived and the screen said on track"
    )
    assert forecast["confidence"] != "normal", (
        "a missing payday cannot be reported with normal confidence"
    )
    assert forecast["missed_income_total"] == pytest.approx(PAYROLL)
    # The money that did not arrive is not counted as though it had.
    assert forecast["expected_income_forecast"] == pytest.approx(PAYROLL)
    assert forecast["expected_income_remaining"] == pytest.approx(0.0)


def test_the_missing_payday_is_explained_in_plain_language(twice_monthly):
    _open_month(twice_monthly, "2026-07", through=28, payrolls=(5,))

    note = forecast_month("2026-07", conn=twice_monthly,
                          today="2026-07-28")["income_note"]

    assert "2,500" in note
    assert "not counted as income" in note


def test_day_28_with_both_payrolls_present_may_be_on_track(twice_monthly):
    """The control. Nothing about the fix may punish an ordinary month."""
    _open_month(twice_monthly, "2026-07", through=28)

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-28")

    assert forecast["mtd_income"] == pytest.approx(2 * PAYROLL)
    assert forecast["missed_income_total"] == pytest.approx(0.0)
    assert forecast["risk_level"] == "on_track"
    assert forecast["confidence"] == "normal"


def test_day_2_after_the_first_payroll_is_not_a_false_danger(twice_monthly):
    """Early in the month the rest of the payroll is still credible."""
    _open_month(twice_monthly, "2026-07", through=2, payrolls=())
    add_tx(twice_monthly, day="2026-07-02", desc="EMPLOYER PAYROLL",
           amount=PAYROLL, direction="credit", category="Payroll Income")
    twice_monthly.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-02")

    assert forecast["risk_level"] != "danger", (
        "one payroll in and a full month of rent paid is an ordinary day 2"
    )
    assert forecast["missed_income_total"] == pytest.approx(0.0)
    assert forecast["expected_income_remaining"] > 0, (
        "income still to come this month should support the outlook"
    )


def test_an_expected_payday_still_ahead_is_allowed_to_count(twice_monthly):
    _open_month(twice_monthly, "2026-07", through=10, payrolls=(5,))

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-10")

    # The 19th has not happened yet, so it is credible.
    assert forecast["expected_income_remaining"] == pytest.approx(PAYROLL)
    assert forecast["expected_income_forecast"] == pytest.approx(2 * PAYROLL)
    assert forecast["missed_income_total"] == pytest.approx(0.0)
    assert forecast["expected_income_basis"] == "scheduled_arrivals"


def test_a_payday_a_few_days_late_is_not_yet_called_missing(twice_monthly):
    """Payroll slips for weekends and bank holidays."""
    _open_month(twice_monthly, "2026-07", through=21, payrolls=(5,))

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-21")

    assert forecast["missed_income_total"] == pytest.approx(0.0)
    assert forecast["expected_income_remaining"] == pytest.approx(PAYROLL)


def test_once_the_grace_period_passes_the_income_is_removed(twice_monthly):
    """Nine days past the date is not running late any more."""
    _open_month(twice_monthly, "2026-07", through=28, payrolls=(5,))

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-28")

    assert forecast["expected_income_remaining"] == pytest.approx(0.0)
    assert forecast["missed_income_total"] == pytest.approx(PAYROLL)


# ── received and expected stay different numbers ─────────────────────────

def test_projected_income_stays_received_only(twice_monthly):
    for through, as_of in ((2, "2026-07-02"), (10, "2026-07-10"),
                           (28, "2026-07-28")):
        conn = twice_monthly
        conn.execute("DELETE FROM transactions WHERE transaction_date LIKE ?",
                     ("2026-07%",))
        conn.commit()
        _open_month(conn, "2026-07", through=through, payrolls=(5,))

        forecast = forecast_month("2026-07", conn=conn, today=as_of)

        assert forecast["projected_income"] == pytest.approx(
            forecast["mtd_income"]
        ), f"day {through}: the authoritative income counted unreceived money"
        assert forecast["projected_income_source"] == "received_only"
        assert forecast["expected_income_forecast"] >= forecast["mtd_income"]


def test_safe_to_spend_never_reads_the_expectation(twice_monthly):
    from utils.checkin import safe_to_spend_summary

    _open_month(twice_monthly, "2026-07", through=10, payrolls=(5,))

    forecast = forecast_month("2026-07", conn=twice_monthly,
                              today="2026-07-10")
    summary = safe_to_spend_summary(conn=twice_monthly, today="2026-07-10")

    assert forecast["expected_income_remaining"] > 0
    assert summary["amount"] != forecast["expected_income_forecast"]
    # Safe to Spend is balances minus confirmed reservations, and says so.
    assert (summary["reserved"] or {}).get("basis") != "expected_income"


# ── irregular income is not a payday ─────────────────────────────────────

def test_a_one_off_windfall_is_not_treated_as_scheduled_payroll(ledger_db):
    """It raises the monthly average. It promises nothing about next month."""
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(ledger_db, day="2026-03-11", desc="INVENTED ONE OFF INHERITANCE",
           amount=9000.0, direction="credit", category="Income")
    ledger_db.commit()
    reset_supported_bounds_cache()
    _open_month(ledger_db, "2026-07", through=28)

    outlook = scheduled_income_outlook(
        "2026-07", anchor=__import__("datetime").date(2026, 7, 28),
        conn=ledger_db,
    )

    assert outlook["scheduled_detected"] is True
    sources = {row["source"] for row in outlook["sources"]}
    assert not any("INHERITANCE" in source for source in sources), (
        "a single windfall was scheduled as if it were a payday"
    )
    # Both real paydays arrived, so nothing is missing despite the average
    # having been dragged upward by the windfall.
    assert outlook["missed"] == pytest.approx(0.0)
    assert outlook["scheduled_total"] == pytest.approx(2 * PAYROLL)


def test_an_inflated_baseline_cannot_invent_expected_income(ledger_db):
    """The old formula took max(received, baseline), so the windfall month
    quietly raised what every later month was said to expect."""
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(ledger_db, day="2026-03-11", desc="INVENTED ONE OFF INHERITANCE",
           amount=9000.0, direction="credit", category="Income")
    ledger_db.commit()
    reset_supported_bounds_cache()
    _open_month(ledger_db, "2026-07", through=28)

    from utils.analytics import typical_monthly_income

    baseline = typical_monthly_income(conn=ledger_db)["amount"]
    forecast = forecast_month("2026-07", conn=ledger_db, today="2026-07-28")

    assert baseline > 2 * PAYROLL, "the fixture must inflate the baseline"
    assert forecast["expected_income_forecast"] == pytest.approx(2 * PAYROLL), (
        "the windfall was counted as income this month expects"
    )


# ── other payroll shapes ─────────────────────────────────────────────────

def _complete_month(conn, month: str, deposits: tuple = ()) -> None:
    """An ordinary complete month with exactly the deposits asked for.

    ``seed_month`` always writes two payroll rows, and even a token cent of
    them forms its own detectable rhythm. These fixtures are about which
    rhythms exist, so they have to control the income side exactly.
    """
    import calendar as _cal

    last_day = _cal.monthrange(int(month[:4]), int(month[5:7]))[1]
    add_tx(conn, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    add_tx(conn, day=f"{month}-05", desc="HYDRO CO", amount=90.0,
           direction="debit", category="Utilities / Bills")
    for day in range(2, 18):
        add_tx(conn, day=f"{month}-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")
    # Late-month row so statement_coverage calls the month complete.
    add_tx(conn, day=f"{month}-{last_day - 2:02d}", desc="COFFEE STOP",
           amount=6.0, direction="debit", category="Food & Convenience")
    for day, desc, amount in deposits:
        add_tx(conn, day=f"{month}-{day:02d}", desc=desc, amount=amount,
               direction="credit", category="Payroll Income")


@pytest.fixture
def monthly_payroll(ledger_db):
    """One deposit a month, on the 25th, and no other income at all."""
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        _complete_month(ledger_db, month,
                        ((25, "INVENTED MONTHLY SALARY", 5000.0),))
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def test_one_payroll_a_month_still_ahead_is_credible(monthly_payroll):
    _open_month(monthly_payroll, "2026-07", through=10, payrolls=())

    forecast = forecast_month("2026-07", conn=monthly_payroll,
                              today="2026-07-10")

    assert forecast["expected_income_remaining"] == pytest.approx(5000.0)
    assert forecast["missed_income_total"] == pytest.approx(0.0)


def test_one_payroll_a_month_that_never_came_is_missing(monthly_payroll):
    _open_month(monthly_payroll, "2026-07", through=31, payrolls=())

    forecast = forecast_month("2026-07", conn=monthly_payroll,
                              today="2026-07-31")

    assert forecast["missed_income_total"] == pytest.approx(5000.0)
    assert forecast["expected_income_remaining"] == pytest.approx(0.0)
    assert forecast["risk_level"] != "on_track"


@pytest.fixture
def biweekly_payroll(ledger_db):
    """Every fourteen days from 2026-01-02, so July pays on the 3rd, 17th
    and 31st."""
    from datetime import date, timedelta

    when = date(2026, 1, 2)
    while when < date(2026, 7, 1):
        add_tx(ledger_db, day=when.isoformat(),
               desc="INVENTED BIWEEKLY PAYROLL", amount=2000.0,
               direction="credit", category="Payroll Income")
        when += timedelta(days=14)
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        add_tx(ledger_db, day=f"{month}-01", desc="LANDLORD RENT",
               amount=RENT, direction="debit", category="Housing / Mortgage")
        add_tx(ledger_db, day=f"{month}-27", desc="COFFEE STOP", amount=6.0,
               direction="debit", category="Food & Convenience")
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def test_a_biweekly_history_places_more_than_one_payday_in_the_month(
    biweekly_payroll,
):
    from datetime import date

    outlook = scheduled_income_outlook(
        "2026-07", anchor=date(2026, 7, 1), conn=biweekly_payroll,
    )

    assert outlook["scheduled_detected"] is True
    entry = outlook["sources"][0]
    assert entry["cadence"] in {"biweekly", "weekly"}
    assert len(entry["expected_dates"]) >= 2, (
        "a fortnightly payroll pays at least twice in a 31-day month"
    )
    assert outlook["scheduled_total"] >= 4000.0


def test_a_biweekly_payday_that_passed_unpaid_is_missing(biweekly_payroll):
    from datetime import date

    add_tx(biweekly_payroll, day="2026-07-03",
           desc="INVENTED BIWEEKLY PAYROLL", amount=2000.0,
           direction="credit", category="Payroll Income")
    biweekly_payroll.commit()
    reset_supported_bounds_cache()

    outlook = scheduled_income_outlook(
        "2026-07", anchor=date(2026, 7, 26), conn=biweekly_payroll,
    )

    # The 17th is well past its grace period and only the 3rd arrived.
    assert outlook["received"] == pytest.approx(2000.0)
    assert outlook["missed"] == pytest.approx(2000.0)


# ── boundaries ───────────────────────────────────────────────────────────

def test_a_payday_on_the_31st_survives_a_thirty_day_month(twice_monthly):
    """Clamped into the month rather than rolling into the next one."""
    from datetime import date

    for month in ("2026-01", "2026-03", "2026-05"):
        add_tx(twice_monthly, day=f"{month}-31",
               desc="INVENTED MONTH END BONUS", amount=400.0,
               direction="credit", category="Payroll Income")
    twice_monthly.commit()
    reset_supported_bounds_cache()

    outlook = scheduled_income_outlook(
        "2026-06", anchor=date(2026, 6, 30), conn=twice_monthly,
    )
    bonus = [row for row in outlook["sources"]
             if "BONUS" in row["source"].upper()]

    for row in bonus:
        for when in row["expected_dates"]:
            assert when.startswith("2026-06")
            assert when <= "2026-06-30"


def test_the_year_boundary_reads_december_history(ledger_db):
    """A January forecast has to look back across the year change."""
    from datetime import date

    for month in ("2025-08", "2025-09", "2025-10",
                  "2025-11", "2025-12"):
        seed_month(ledger_db, month)
    ledger_db.commit()
    reset_supported_bounds_cache()
    _open_month(ledger_db, "2026-01", through=10, payrolls=(5,))

    outlook = scheduled_income_outlook(
        "2026-01", anchor=date(2026, 1, 10), conn=ledger_db,
    )

    assert outlook["scheduled_detected"] is True, (
        "December history was not carried across the year boundary"
    )
    assert outlook["received"] == pytest.approx(PAYROLL)
    assert outlook["expected_remaining"] == pytest.approx(PAYROLL)
    assert outlook["missed"] == pytest.approx(0.0)


def test_a_thin_history_makes_no_scheduled_claim(ledger_db):
    """Two deposits is a coincidence, not a payday."""
    from datetime import date

    add_tx(ledger_db, day="2026-05-05", desc="INVENTED PAYROLL",
           amount=PAYROLL, direction="credit", category="Payroll Income")
    add_tx(ledger_db, day="2026-06-05", desc="INVENTED PAYROLL",
           amount=PAYROLL, direction="credit", category="Payroll Income")
    ledger_db.commit()
    reset_supported_bounds_cache()

    outlook = scheduled_income_outlook(
        "2026-07", anchor=date(2026, 7, 20), conn=ledger_db,
    )

    assert outlook["scheduled_detected"] is False
    assert outlook["missed"] == pytest.approx(0.0)
    assert outlook["expected_remaining"] == pytest.approx(0.0)


def test_without_a_schedule_the_baseline_decays_across_the_month(ledger_db):
    """No rhythm to point at, so the claim on unreceived income shrinks as
    the month runs out instead of holding firm until the 31st."""
    for month in ("2026-01", "2026-02", "2026-03",
                  "2026-04", "2026-05", "2026-06"):
        _complete_month(ledger_db, month)
    # Irregular freelance income: real, but on no rhythm at all.
    for day, amount in (("2026-02-09", 3000.0), ("2026-03-27", 1200.0),
                        ("2026-05-14", 4100.0), ("2026-06-02", 900.0)):
        add_tx(ledger_db, day=day, desc=f"INVENTED CLIENT {day}",
               amount=amount, direction="credit", category="Income")
    ledger_db.commit()
    reset_supported_bounds_cache()
    _open_month(ledger_db, "2026-07", through=28, payrolls=())

    early = forecast_month("2026-07", conn=ledger_db, today="2026-07-05")
    late = forecast_month("2026-07", conn=ledger_db, today="2026-07-28")

    assert early["expected_income_basis"] == "prorated_baseline"
    assert early["expected_income_remaining"] > late["expected_income_remaining"]
    assert late["missed_income_total"] == pytest.approx(0.0), (
        "income that was never scheduled cannot be reported as missed"
    )

"""A forecast may not multiply a bill that has already been paid.

All figures are invented.

`forecast_month` projected month-end spending as
``mtd_spending * days_in_month / days_elapsed``. Rent lands on the 1st, so on
day one that multiplied the whole month's largest commitment by the number of
days in the month. A household paying $1,800 rent was told it would spend
$55,800 and that it was in danger, and the alarm persisted for roughly the
first three weeks of every month before the arithmetic caught up with itself.

The correct shape counts each kind of money once:

    fixed already paid
  + reviewed fixed commitments not yet paid
  + flexible spending so far
  + projected remaining flexible spending

Only the last term is projected, because only flexible spending is the kind
of thing that continues at a rate. A bill is an event, not a rate.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

import utils.database as db
from tests.conftest import add_tx
from utils.analysis_period import reset_supported_bounds_cache
from utils.planner import forecast_month

RENT = 1800.0
PAYROLL = 2500.0
GROCERY = 84.20


def _month_of(offset_back: int, from_month: date) -> date:
    month = from_month
    for _ in range(offset_back):
        month = (month - timedelta(days=1)).replace(day=1)
    return month


def _seed_month(conn, month: str, *, through_day: int = 31) -> None:
    """One ordinary month: rent on the 1st, payroll twice, groceries spread."""
    def add(day: int, desc: str, amount: float, direction: str, category: str):
        if day <= through_day:
            add_tx(conn, day=f"{month}-{day:02d}", desc=desc, amount=amount,
                   direction=direction, category=category)

    add(1, "LANDLORD RENT", RENT, "debit", "Housing / Mortgage")
    add(2, "EMPLOYER PAYROLL", PAYROLL, "credit", "Payroll Income")
    add(5, "HYDRO CO", 92.40, "debit", "Utilities / Bills")
    add(16, "EMPLOYER PAYROLL 2", PAYROLL, "credit", "Payroll Income")
    for day in (3, 7, 11, 14, 19, 22, 25, 28):
        add(day, f"GROCER {day}", GROCERY, "debit", "Groceries")


@pytest.fixture
def steady(ledger_db):
    """Six identical complete months behind the current one."""
    today = date.today()
    first = date(today.year, today.month, 1)
    for back in range(6, 0, -1):
        _seed_month(ledger_db, _month_of(back, first).strftime("%Y-%m"))
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def _current(day: int) -> tuple[str, str]:
    first = date.today().replace(day=1)
    return first.strftime("%Y-%m"), f"{first.strftime('%Y-%m')}-{day:02d}"


# ── the reproduction ─────────────────────────────────────────────────────

def test_day_one_rent_is_counted_once(steady) -> None:
    """$1,800 of rent is $1,800, not $1,800 times the days in the month."""
    month, as_of = _current(1)
    add_tx(steady, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)

    assert forecast["projected_spending"] < RENT * 3, (
        f"day-one rent of {RENT} projected {forecast['projected_spending']}"
    )
    # A real month for this household is about $2,566.
    assert 1800 <= forecast["projected_spending"] <= 4000


def test_day_two_payroll_does_not_create_a_disaster(steady) -> None:
    """Rent paid, one payroll received, the rest of the month ahead."""
    month, as_of = _current(2)
    add_tx(steady, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    add_tx(steady, day=f"{month}-02", desc="EMPLOYER PAYROLL", amount=PAYROLL,
           direction="credit", category="Payroll Income")
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)

    assert forecast["projected_spending"] <= 4000, forecast["projected_spending"]
    assert forecast["risk_level"] != "danger", (
        "a balanced month reported as danger on day two"
    )


def test_a_fixed_bill_never_enters_the_pace_extrapolation(steady) -> None:
    """Adding rent must move the projection by rent, not by a multiple."""
    month, as_of = _current(4)
    add_tx(steady, day=f"{month}-03", desc="GROCER 3", amount=GROCERY,
           direction="debit", category="Groceries")
    steady.commit()
    reset_supported_bounds_cache()
    without_rent = forecast_month(
        plan_month=month, conn=steady, today=as_of)["projected_spending"]

    add_tx(steady, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    steady.commit()
    reset_supported_bounds_cache()
    with_rent = forecast_month(
        plan_month=month, conn=steady, today=as_of)["projected_spending"]

    added = with_rent - without_rent
    assert added <= RENT * 1.05, (
        f"rent of {RENT} raised the projection by {added:.2f}"
    )


def test_flexible_purchases_are_still_projected(steady) -> None:
    """The fix must not stop projecting the spending that does continue."""
    month, as_of = _current(10)
    for day in (2, 4, 6, 8, 10):
        add_tx(steady, day=f"{month}-{day:02d}", desc=f"GROCER {day}",
               amount=GROCERY, direction="debit", category="Groceries")
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)
    spent = forecast["mtd_spending"]

    assert forecast["projected_spending"] > spent, (
        "ten days of groceries projected no further spending at all"
    )


# ── income stays in its lane ─────────────────────────────────────────────

def test_expected_payroll_supports_the_outlook_but_not_cash_now(
    steady,
) -> None:
    """The month outlook may expect payroll. Cash available now may not."""
    from utils.checkin import safe_to_spend_summary

    month, as_of = _current(3)
    add_tx(steady, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)
    summary = safe_to_spend_summary(conn=steady, today=as_of)

    assert forecast["expected_income_forecast"] >= forecast["mtd_income"]
    assert forecast["projected_income"] == forecast["mtd_income"], (
        "the authoritative projection counted money that has not arrived"
    )
    # Safe to Spend is a separate concept and must not inherit the
    # outlook's expectation.
    assert summary["amount"] != forecast["expected_income_forecast"]


def test_a_missing_payroll_alone_is_not_a_catastrophe(steady) -> None:
    month, as_of = _current(5)
    add_tx(steady, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    for day in (3, 5):
        add_tx(steady, day=f"{month}-{day:02d}", desc=f"GROCER {day}",
               amount=GROCERY, direction="debit", category="Groceries")
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)

    assert forecast["projected_spending"] <= 4000
    assert forecast["risk_level"] != "danger"


# ── sparse data is qualified, not confident ──────────────────────────────

def test_a_sparse_early_month_is_qualified_not_confident(ledger_db) -> None:
    """No history at all, one row on day one. Nothing can be claimed."""
    month, as_of = _current(1)
    add_tx(ledger_db, day=f"{month}-01", desc="LANDLORD RENT", amount=RENT,
           direction="debit", category="Housing / Mortgage")
    ledger_db.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=ledger_db, today=as_of)

    assert forecast["risk_level"] in {"insufficient_data", "watch"}, (
        f"a confident {forecast['risk_level']} from one day of data"
    )
    assert forecast["confidence"] in {"low", "insufficient"}


def test_history_raises_confidence(steady) -> None:
    month, as_of = _current(15)
    _seed_month(steady, month, through_day=15)
    steady.commit()
    reset_supported_bounds_cache()

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)

    assert forecast["confidence"] == "normal"


# ── the duplicate Safe to Spend is gone ──────────────────────────────────

def test_the_forecast_no_longer_carries_a_second_safe_to_spend(
    steady,
) -> None:
    """remaining_spending_cap duplicated the canonical number and could drift."""
    month, as_of = _current(10)

    forecast = forecast_month(plan_month=month, conn=steady, today=as_of)

    assert "remaining_spending_cap" not in forecast


# ── the projection is stable across the month ────────────────────────────

def test_the_projection_does_not_swing_wildly_through_the_month(
    steady,
) -> None:
    """The old model ran from $55,800 on day one to accurate by day 28."""
    month = _current(1)[0]
    seen: list[float] = []
    for day in (1, 3, 6, 10, 15, 20, 28):
        steady.execute("DELETE FROM transactions WHERE transaction_date LIKE ?",
                       (f"{month}-%",))
        steady.commit()
        _seed_month(steady, month, through_day=day)
        steady.commit()
        reset_supported_bounds_cache()
        seen.append(forecast_month(
            plan_month=month, conn=steady,
            today=f"{month}-{day:02d}")["projected_spending"])

    assert max(seen) <= min(seen) * 2.5, (
        f"the projection swung from {min(seen):.2f} to {max(seen):.2f} "
        "across one month of identical spending"
    )

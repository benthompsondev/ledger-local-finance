"""The weekly allowance is the next seven days' share of what is left.

All figures are invented.

`safe_to_spend_summary` documents weekly_amount as "amount for the next 7
days (== amount when fewer than 7 days remain or the period has ended)".
The flow fallback ignored that and always computed ``amount * 7 / 30.4``,
a fixed share of an average month regardless of how much of the month was
actually left.

With $2,190 remaining and 26 days to go it showed $504.28 when the seven-day
share is $589.62 — and the same figure drives "stay within $X this week", so
the instruction was wrong in the safe direction on a long month and the
unsafe direction on a short one: with three days left it still suggested a
week's worth of spending.
"""
from __future__ import annotations

import pytest

from utils.checkin import weekly_allowance


@pytest.mark.parametrize("amount,days_left,expected_daily,expected_weekly", [
    # More than a week left: a day's share, times seven.
    (2190.0, 26, 84.23, 589.62),
    (2190.0, 8, 273.75, 1916.25),
    # Exactly a week or less: the whole remaining amount is this week's.
    (2190.0, 7, 312.86, 2190.0),
    (2190.0, 6, 365.0, 2190.0),
    (2190.0, 1, 2190.0, 2190.0),
])
def test_the_weekly_share_follows_the_days_that_remain(
    amount, days_left, expected_daily, expected_weekly,
) -> None:
    result = weekly_allowance(amount, days_left)

    assert result["daily_amount"] == pytest.approx(expected_daily, abs=0.01)
    assert result["weekly_amount"] == pytest.approx(expected_weekly, abs=0.01)


def test_a_finished_period_offers_no_rate_at_all(ledger_db=None) -> None:
    """Zero days left cannot produce a daily rate or a weekly instruction."""
    result = weekly_allowance(2190.0, 0)

    assert result["daily_amount"] is None
    assert result["weekly_amount"] is None


def test_a_negative_day_count_is_treated_as_finished() -> None:
    result = weekly_allowance(2190.0, -3)

    assert result["daily_amount"] is None
    assert result["weekly_amount"] is None


def test_the_weekly_share_never_exceeds_what_is_left() -> None:
    """The instruction is a budget, not permission to overspend."""
    for days in range(1, 32):
        result = weekly_allowance(1000.0, days)
        assert result["weekly_amount"] <= 1000.0 + 0.01, (
            f"with {days} days left it suggested more than remains"
        )


def test_zero_remaining_is_zero_not_absent() -> None:
    """Nothing left is a real answer; it is not missing data."""
    result = weekly_allowance(0.0, 10)

    assert result["daily_amount"] == 0.0
    assert result["weekly_amount"] == 0.0


def test_the_old_fixed_month_share_is_gone() -> None:
    """2.5.3 returned amount * 7 / 30.4 whatever the days remaining."""
    result = weekly_allowance(2190.0, 26)

    assert result["weekly_amount"] != pytest.approx(504.28, abs=0.01)


# ── the summary itself, not just the helper ──────────────────────────────

def test_every_safe_to_spend_path_reconciles(ledger_db) -> None:
    """Whatever basis produced the number, the rates must agree with it."""
    from datetime import date, timedelta

    from tests.conftest import add_tx
    from utils.analysis_period import reset_supported_bounds_cache
    from utils.checkin import safe_to_spend_summary

    today = date.today()
    first = today.replace(day=1)
    month = first
    for _ in range(4):
        month = (month - timedelta(days=1)).replace(day=1)
        ym = month.strftime("%Y-%m")
        add_tx(ledger_db, day=f"{ym}-01", desc="LANDLORD RENT", amount=1800.0,
               direction="debit", category="Housing / Mortgage")
        add_tx(ledger_db, day=f"{ym}-02", desc="EMPLOYER PAYROLL",
               amount=2500.0, direction="credit", category="Payroll Income")
        add_tx(ledger_db, day=f"{ym}-16", desc="EMPLOYER PAYROLL 2",
               amount=2500.0, direction="credit", category="Payroll Income")
        for day in (3, 8, 13, 18, 23, 27):
            add_tx(ledger_db, day=f"{ym}-{day:02d}", desc=f"GROCER {day}",
                   amount=84.20, direction="debit", category="Groceries")
    ledger_db.commit()
    reset_supported_bounds_cache()

    summary = safe_to_spend_summary(conn=ledger_db)
    if not summary.get("available"):
        # An unavailable number must still be coherent: no rate, and a
        # reason. That is a real assertion, not a reason to skip.
        assert summary.get("reason")
        assert summary.get("daily_amount") in (None, 0.0)
        return

    days_left = int(summary["days_left"])
    amount = float(summary["amount"])
    if days_left <= 0:
        assert summary["daily_amount"] is None
        assert summary["weekly_amount"] in (None, 0.0)
        return
    expected = weekly_allowance(amount, days_left)
    assert summary["weekly_amount"] == pytest.approx(
        expected["weekly_amount"], abs=0.02
    ), (
        f"{summary['reserved'].get('basis')} basis disagreed with the "
        "canonical weekly share"
    )

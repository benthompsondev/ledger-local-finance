"""Northstar 1.5.0 — one number you can trust.

Two defects this release fixes:

1. "Typical monthly income" summed a median per income source, counting a
   source that arrives three months in six as if it arrived every month, and
   dropped auto-detected payroll entirely until the user confirmed it in
   Settings. The baseline must instead be the median of actual complete-month
   income totals, over an adaptive window, and must state that window.
2. Safe to Spend showed the whole monthly allowance no matter how much of it
   had already been spent. It must subtract flexible spending so far.
"""
import pytest

from tests.conftest import add_tx


def _month_days(db, month, *, days, amount=5.0, category="Food & Convenience"):
    """Coverage days so the month registers as complete."""
    for day in days:
        add_tx(
            db, day=f"{month}-{day:02d}", desc=f"{month} COFFEE {day}",
            amount=amount, direction="debit", category=category,
        )


def _seed_months(db, months, *, payroll=3000.0, etransfer_months=(),
                 etransfer=900.0, mortgage=1200.0, last_month_days=None):
    for index, month in enumerate(months):
        last = index == len(months) - 1
        days = (
            last_month_days if (last and last_month_days is not None)
            else range(1, 27)
        )
        _month_days(db, month, days=days)
        add_tx(
            db, day=f"{month}-05", desc="INVENTED EMPLOYER PAYROLL",
            amount=payroll, direction="credit", category="Payroll Income",
        )
        if index in etransfer_months:
            add_tx(
                db, day=f"{month}-12",
                desc="INTERAC E-TRANSFER FROM INVENTED ROOMMATE",
                amount=etransfer, direction="credit", category="Other Income",
            )
        add_tx(
            db, day=f"{month}-01", desc="INVENTED MORTGAGE",
            amount=mortgage, direction="debit", category="Housing / Mortgage",
        )
    db.commit()


# ── 1. adaptive income baseline ──────────────────────────────────────────

def test_typical_income_uses_complete_month_totals_not_summed_medians(
    ledger_db,
):
    """A source present in 3 of 6 months must not count as monthly income.

    Steady $3,000 payroll every month plus a $900 e-transfer in three of six
    months is a true typical month of $3,450 — not $3,900 (the old
    sum-of-per-source-medians) and not $900 (the old default, which dropped
    the payroll).
    """
    from utils.analytics import typical_monthly_income

    _seed_months(
        ledger_db,
        ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"),
        etransfer_months={0, 2, 4},
    )
    result = typical_monthly_income(conn=ledger_db)

    assert result["amount"] == pytest.approx(3450.0)
    assert result["months_used"] == 6
    assert result["first_month"] == "2026-01"
    assert result["last_month"] == "2026-06"


def test_income_window_grows_with_history(ledger_db):
    """1 month uses that month; 2-5 use all; 6+ use the latest six."""
    from utils.analytics import typical_monthly_income

    _seed_months(ledger_db, ("2025-11",))
    one = typical_monthly_income(conn=ledger_db)
    assert one["months_used"] == 1
    assert one["confidence"] == "low"

    _seed_months(ledger_db, ("2025-12", "2026-01"))
    three = typical_monthly_income(conn=ledger_db)
    assert three["months_used"] == 3
    assert three["first_month"] == "2025-11"

    _seed_months(
        ledger_db,
        ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06"),
    )
    eight = typical_monthly_income(conn=ledger_db)
    # Eight complete months exist; only the latest six may be used.
    assert eight["months_used"] == 6
    assert eight["first_month"] == "2026-01"
    assert eight["last_month"] == "2026-06"


def test_typical_income_states_its_basis(ledger_db):
    from utils.analytics import typical_monthly_income

    _seed_months(ledger_db, ("2026-01", "2026-02", "2026-03"))
    result = typical_monthly_income(conn=ledger_db)
    label = result["basis_label"]
    assert "3 complete months" in label
    assert "January" in label and "March" in label


def test_partial_current_month_never_enters_the_baseline(ledger_db):
    """A half-imported current month must not drag the typical figure down."""
    from utils.analytics import typical_monthly_income

    _seed_months(
        ledger_db, ("2026-04", "2026-05", "2026-06"),
        last_month_days=range(1, 6),  # 2026-06 is clearly partial
    )
    result = typical_monthly_income(conn=ledger_db)
    assert result["last_month"] == "2026-05"
    assert result["months_used"] == 2


# ── 2. remaining, not the whole allowance ────────────────────────────────

def test_safe_to_spend_subtracts_flexible_spending_so_far(ledger_db):
    """The headline number is what is LEFT, not the month's whole allowance."""
    from utils.checkin import flow_safe_to_spend

    _seed_months(ledger_db, ("2026-04", "2026-05", "2026-06"))
    # Current partial month with real flexible spending in it.
    _month_days(ledger_db, "2026-07", days=range(1, 11))  # 10 x $5 = $50
    add_tx(
        ledger_db, day="2026-07-03", desc="INVENTED ELECTRONICS SHOP",
        amount=400, direction="debit", category="Shopping",
    )
    add_tx(
        ledger_db, day="2026-07-01", desc="INVENTED MORTGAGE",
        amount=1200, direction="debit", category="Housing / Mortgage",
    )
    ledger_db.commit()

    flow = flow_safe_to_spend(ledger_db, today="2026-07-10")

    assert flow is not None
    # $450 of flexible spending happened ($400 shopping + $50 coffee); the
    # $1,200 mortgage is a fixed commitment and must not be counted twice.
    assert flow["flexible_spent"] == pytest.approx(450.0)
    assert flow["remaining"] == pytest.approx(
        round(flow["monthly_plan"] - flow["flexible_spent"], 2)
    )
    assert flow["remaining"] < flow["monthly_plan"]


def test_remaining_never_goes_below_zero(ledger_db):
    from utils.checkin import flow_safe_to_spend

    _seed_months(ledger_db, ("2026-04", "2026-05", "2026-06"))
    _month_days(ledger_db, "2026-07", days=range(1, 11))
    add_tx(
        ledger_db, day="2026-07-03", desc="INVENTED ELECTRONICS SHOP",
        amount=99000, direction="debit", category="Shopping",
    )
    ledger_db.commit()

    flow = flow_safe_to_spend(ledger_db, today="2026-07-10")
    assert flow["remaining"] == 0.0


def test_summary_reports_the_remaining_number_and_is_not_balance_checked(
    ledger_db,
):
    from utils.checkin import safe_to_spend_summary

    _seed_months(ledger_db, ("2026-04", "2026-05", "2026-06"))
    _month_days(ledger_db, "2026-07", days=range(1, 11))
    add_tx(
        ledger_db, day="2026-07-03", desc="INVENTED ELECTRONICS SHOP",
        amount=400, direction="debit", category="Shopping",
    )
    ledger_db.commit()

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-10")
    reserved = sts["reserved"]

    assert sts["available"] is True
    assert reserved["basis"] == "flow"
    # No balances were entered, so the number must say so.
    assert reserved["balance_checked"] is False
    assert reserved["flexible_spent"] == pytest.approx(450.0)
    assert sts["amount"] == pytest.approx(
        round(reserved["monthly_plan"] - reserved["flexible_spent"], 2)
    )
    # The stated income basis travels with the number.
    assert reserved["income_months_used"] >= 1
    assert "complete month" in reserved["income_basis_label"]

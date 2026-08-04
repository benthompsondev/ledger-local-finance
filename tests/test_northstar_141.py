"""Northstar 1.4.1 — flow-first Safe to Spend.

The budget number (typical monthly income minus recurring commitments minus a
savings target) must show from transaction history alone: no account balances
and no saved plan required. Balances only refine it into the cash-reconciled
number when present; they never gate it.
"""
import pytest

from tests.conftest import add_tx


def _seed_income_and_bills(db):
    for month in ("2026-04", "2026-05", "2026-06"):
        # Coverage days so the months register, recurring payroll, and one
        # recurring fixed commitment.
        for day in range(1, 16):
            add_tx(
                db, day=f"{month}-{day:02d}", desc=f"{month} COFFEE {day}",
                amount=5, direction="debit", category="Food & Convenience",
            )
        add_tx(
            db, day=f"{month}-05", desc="INVENTED EMPLOYER PAYROLL",
            amount=3000, direction="credit", category="Payroll Income",
        )
        add_tx(
            db, day=f"{month}-01", desc="INVENTED MORTGAGE",
            amount=1500, direction="debit", category="Housing / Mortgage",
        )
    db.commit()


def test_flow_number_shows_without_balances_or_plan(ledger_db):
    from utils.checkin import safe_to_spend_summary

    _seed_income_and_bills(ledger_db)
    # Current-month activity proves this is a live July budget, not June's
    # leftover amount being carried across the month boundary.
    add_tx(
        ledger_db, day="2026-07-01", desc="INVENTED JULY COFFEE",
        amount=5, direction="debit", category="Food & Convenience",
    )
    ledger_db.commit()
    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-01")

    # No balances, no saved plan were created, yet a number is available.
    assert sts["available"] is True
    reserved = sts["reserved"]
    assert reserved["basis"] == "flow"

    # The monthly plan reconciles: income − recurring − savings.
    expected_plan = max(
        0.0,
        round(
            reserved["reliable_income"]
            - reserved["recurring_commitments"]
            - reserved["savings_target"],
            2,
        ),
    )
    assert reserved["monthly_plan"] == pytest.approx(expected_plan)
    # 1.5.0: the headline is what is LEFT, so flexible spending already done
    # this month comes off the plan.
    assert sts["amount"] == pytest.approx(
        round(expected_plan - reserved["flexible_spent"], 2)
    )
    # Savings defaults to 15% of income when the user has not set a goal.
    assert reserved["savings_target"] == pytest.approx(
        round(reserved["reliable_income"] * 0.15, 2)
    )
    assert reserved["reliable_income"] > 0
    assert reserved["recurring_commitments"] > 0


def test_one_complete_month_is_enough(ledger_db):
    """1.5.0: a single COMPLETE month produces a number, marked low confidence.

    The old contract refused to quote anything under two months. Ben's rule is
    that one month of data means one month of income, so the number shows and
    the basis states exactly how thin it is.
    """
    from tests.conftest import seed_month
    from utils.checkin import safe_to_spend_summary

    seed_month(ledger_db, "2026-06")
    sts = safe_to_spend_summary(conn=ledger_db, today="2026-06-28")

    assert sts["available"] is True
    reserved = sts["reserved"]
    assert reserved["income_months_used"] == 1
    assert reserved["balance_checked"] is False
    assert "June" in reserved["income_basis_label"]


def test_one_unfinished_month_is_not_enough(ledger_db):
    """A month still in progress cannot describe a typical month."""
    from utils.checkin import safe_to_spend_summary

    for day in range(1, 16):
        add_tx(
            ledger_db, day=f"2026-06-{day:02d}", desc=f"COFFEE {day}",
            amount=5, direction="debit", category="Food & Convenience",
        )
    add_tx(
        ledger_db, day="2026-06-05", desc="INVENTED EMPLOYER PAYROLL",
        amount=3000, direction="credit", category="Payroll Income",
    )
    ledger_db.commit()

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-01")
    assert sts["available"] is False


def test_no_history_at_all_reports_unavailable(ledger_db):
    """The number must never be invented out of nothing."""
    from utils.checkin import safe_to_spend_summary

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-01")
    assert sts["available"] is False

"""Canonical Safe to Spend: formula integrity, pacing, and the
zero-days / period-ended regression (the '-$783/day' bug)."""
import pytest

from utils.checkin import safe_to_spend_summary


def test_formula_components_reconcile(steady_db):
    sts = safe_to_spend_summary(conn=steady_db, today="2026-07-06")
    assert sts["available"] is True
    r = sts["reserved"]
    cash_cushion = (
        r["liquid_balance"]
        + r["stable_income_remaining"]
        - r["bills_remaining"]
        - r["subscriptions_remaining"]
        - r["credit_card_liability"]
        - r["savings_target"]
        - r["debt_or_fee_reserve"]
        - r["buffer"]
    )
    assert r["cash_cushion_after_bills"] == pytest.approx(
        cash_cushion, abs=0.02
    )
    assert sts["amount"] == pytest.approx(
        min(r["flexible_remaining"], cash_cushion), abs=0.02
    )
    # Plan income (5000) is the expected-income source when a plan exists.
    assert r["income_expected"] == pytest.approx(5000.0)
    assert r["spending_so_far"] > 0  # informational; already reflected in balances
    # Savings target flows straight from the saved plan.
    assert r["savings_target"] == pytest.approx(800.0)


def test_daily_and_weekly_pacing_valid_period(steady_db):
    # steady_db models 2026-07-06. Supplying that clock keeps this a pacing
    # test instead of letting the wall clock turn it into a period-ended test.
    sts = safe_to_spend_summary(conn=steady_db, today="2026-07-06")
    assert sts["period_ended"] is False
    assert sts["days_left"] > 0
    assert sts["daily_amount"] == pytest.approx(
        sts["amount"] / sts["days_left"], abs=0.02)
    if sts["days_left"] > 7:
        assert sts["weekly_amount"] == pytest.approx(
            sts["amount"] / sts["days_left"] * 7, abs=0.02)
    else:
        assert sts["weekly_amount"] == pytest.approx(sts["amount"], abs=0.02)


def test_zero_days_never_produces_daily_rate(ended_db):
    """Regression: a 0-days-left period used to report the whole month's
    total as a daily pace ('-$783/day')."""
    sts = safe_to_spend_summary(conn=ended_db)
    assert sts["days_left"] == 0
    assert sts["period_ended"] is True
    assert sts["daily_amount"] is None
    # Weekly falls back to the total remaining, not a divided number.
    assert sts["weekly_amount"] == pytest.approx(sts["amount"], abs=0.02)


def test_closed_month_remainder_is_never_presented_as_available_now(steady_db):
    """A July allowance cannot remain spendable after the calendar enters August."""
    sts = safe_to_spend_summary(conn=steady_db, today="2026-08-01")

    assert sts["available"] is False
    assert sts["period_ended"] is True
    assert sts["amount"] == 0.0
    assert sts["daily_amount"] is None
    assert sts["setup_screen"] == "add-data"
    assert "July 2026" in sts["reason"]
    assert "money available now" in sts["reason"]


def test_negative_amount_maps_to_danger(steady_db):
    from utils.database import upsert_monthly_plan, replace_category_targets
    # Crank the savings target far above income so amount goes negative.
    upsert_monthly_plan({
        "month": "2026-07", "mode": "tight",
        "income_target": 5000.0, "spending_target": 3400.0,
        "savings_target": 50000.0, "fixed_obligations": 1890.0,
        "flexible_allowance": 0.0,
        "detected_commitments_at_save": 1890.0,
        "fixed_override_reason": "", "notes": "stress",
    }, conn=steady_db)
    steady_db.commit()
    sts = safe_to_spend_summary(conn=steady_db, today="2026-07-06")
    assert sts["amount"] < 0
    assert sts["status"] == "danger"


def test_empty_db_reports_unavailable(ledger_db):
    sts = safe_to_spend_summary(conn=ledger_db)
    assert sts["available"] is False
    assert sts["reason"]
    assert sts["daily_amount"] is None


def test_plan_without_balances_falls_back_to_the_flow_number(ledger_db):
    """1.5.0: missing balances downgrade the number, they do not withhold it.

    Balances used to be a hard gate, which left the whole app stuck on "not
    ready". Now the flow estimate shows and is explicitly marked as not
    balance-checked.
    """
    from tests.conftest import seed_month, save_plan
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06")
    sts = safe_to_spend_summary(conn=ledger_db, today="2026-06-25")
    assert sts["available"] is True
    assert sts["reserved"]["basis"] == "flow"
    assert sts["reserved"]["balance_checked"] is False


def test_stable_income_uses_completed_months_not_three_paycheque_window(ledger_db):
    from tests.conftest import add_tx
    from utils.analytics import income_confirmation_candidates, stable_income_summary
    from utils.database import set_income_source_preference

    for month in ("2026-04", "2026-05", "2026-06"):
        for day in ("05", "19"):
            add_tx(
                ledger_db, day=f"{month}-{day}", desc="INVENTED EMPLOYER PAYROLL",
                amount=2000, direction="credit", category="Payroll Income",
            )
    for day in ("01", "15", "29"):
        add_tx(
            ledger_db, day=f"2026-07-{day}", desc="INVENTED EMPLOYER PAYROLL",
            amount=2000, direction="credit", category="Payroll Income",
        )
    ledger_db.commit()

    candidate = income_confirmation_candidates(conn=ledger_db)[0]
    set_income_source_preference(
        candidate["source_normalized"], "confirmed", conn=ledger_db,
    )
    ledger_db.commit()
    summary = stable_income_summary(conn=ledger_db)

    assert summary["monthly_amount"] == 4000.0
    assert summary["basis"] == "payroll_history"
    assert summary["months_used"] == 3

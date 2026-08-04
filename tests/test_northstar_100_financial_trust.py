"""Northstar 1.0 financial-trust regressions.

All figures and descriptions are invented. These tests exercise the exact
failure shapes found during the 1.0 review without using statement data.
"""
from __future__ import annotations

import pytest

from tests.conftest import add_tx, save_plan, seed_month
from utils.checkin import safe_to_spend_summary, weekly_checkin
from utils.database import (
    create_account,
    get_connection,
    insert_account_balance,
    list_accounts,
    update_account,
)


def _linked_balance(conn, account_id: int, name: str, kind: str,
                    balance: float, as_of: str = "2026-07-15") -> None:
    insert_account_balance({
        "account_ref": account_id,
        "account_name": name,
        "account_kind": kind,
        "balance": balance,
        "currency": "CAD",
        "as_of_date": as_of,
    }, conn=conn)


def _seed_current_month_context(conn) -> None:
    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(conn, month)
    add_tx(
        conn,
        day="2026-07-05",
        desc="INVENTED EMPLOYER PAYROLL",
        amount=3000,
        direction="credit",
        category="Payroll Income",
    )
    add_tx(
        conn,
        day="2026-07-15",
        desc="INVENTED LARGE PURCHASE",
        amount=2600,
        direction="debit",
        category="Shopping",
    )
    save_plan(conn, "2026-07", income=6000, spending=4000, savings=500)


def test_missing_future_payroll_is_never_spendable_or_on_track(ledger_db):
    """A missing second payroll cannot create a large green safe-now value."""
    _seed_current_month_context(ledger_db)
    chequing_id = create_account({
        "name": "Everyday Chequing", "type": "chequing",
    }, conn=ledger_db)
    card_id = create_account({
        "name": "Everyday Card", "type": "credit_card",
    }, conn=ledger_db)
    _linked_balance(ledger_db, chequing_id, "Everyday Chequing", "chequing", 4000)
    _linked_balance(ledger_db, card_id, "Everyday Card", "credit_card", 500)
    ledger_db.commit()

    packet = weekly_checkin(conn=ledger_db, today="2026-07-16")
    safe = packet["safe_to_spend"]

    assert safe["reserved"]["stable_income_received"] == pytest.approx(3000)
    assert safe["reserved"]["stable_income_remaining"] == 0
    assert safe["reserved"]["future_income_forecast"] == pytest.approx(3000)
    assert safe["reserved"]["future_income_included"] is False
    assert packet["progress"]["risk_level"] == "danger"
    assert packet["verdict"]["state"] == "behind"
    assert all(action["id"] != "stay_within" for action in packet["actions"])


def test_savings_balance_is_excluded_until_user_marks_it_available(ledger_db):
    _seed_current_month_context(ledger_db)
    chequing_id = create_account({
        "name": "Everyday Chequing", "type": "chequing",
    }, conn=ledger_db)
    savings_id = create_account({
        "name": "Emergency Savings", "type": "savings",
    }, conn=ledger_db)
    card_id = create_account({
        "name": "Everyday Card", "type": "credit_card",
    }, conn=ledger_db)
    _linked_balance(ledger_db, chequing_id, "Everyday Chequing", "chequing", 1200)
    _linked_balance(ledger_db, savings_id, "Emergency Savings", "savings", 9200)
    _linked_balance(ledger_db, card_id, "Everyday Card", "credit_card", 500)
    ledger_db.commit()

    accounts = {row["name"]: row for row in list_accounts(conn=ledger_db)}
    assert accounts["Everyday Chequing"]["available_for_spending"] == 1
    assert accounts["Emergency Savings"]["available_for_spending"] == 0

    protected = safe_to_spend_summary(conn=ledger_db, today="2026-07-16")
    assert protected["reserved"]["available_balance"] == pytest.approx(1200)
    assert protected["reserved"]["excluded_balance"] == pytest.approx(9200)

    assert update_account(
        savings_id, {"available_for_spending": 1}, conn=ledger_db,
    )
    ledger_db.commit()
    included = safe_to_spend_summary(conn=ledger_db, today="2026-07-16")
    assert included["reserved"]["cash_cushion_after_bills"] == pytest.approx(
        protected["reserved"]["cash_cushion_after_bills"] + 9200
    )
    assert included["amount"] == pytest.approx(
        min(
            included["reserved"]["flexible_remaining"],
            included["reserved"]["cash_cushion_after_bills"],
        )
    )


def test_safe_to_spend_breakdown_reconciles_without_future_income(ledger_db):
    _seed_current_month_context(ledger_db)
    chequing_id = create_account({
        "name": "Everyday Chequing", "type": "chequing",
    }, conn=ledger_db)
    card_id = create_account({
        "name": "Everyday Card", "type": "credit_card",
    }, conn=ledger_db)
    _linked_balance(ledger_db, chequing_id, "Everyday Chequing", "chequing", 5000)
    _linked_balance(ledger_db, card_id, "Everyday Card", "credit_card", 500)
    ledger_db.commit()

    safe = safe_to_spend_summary(conn=ledger_db, today="2026-07-16")
    reserved = safe["reserved"]
    expected = (
        reserved["available_balance"]
        - reserved["bills_remaining"]
        - reserved["subscriptions_remaining"]
        - reserved["credit_card_liability"]
        - reserved["savings_target"]
        - reserved["debt_or_fee_reserve"]
        - reserved["buffer"]
    )
    assert reserved["cash_cushion_after_bills"] == pytest.approx(
        expected, abs=0.02
    )
    assert safe["amount"] == pytest.approx(
        min(reserved["flexible_remaining"], expected), abs=0.02
    )
    assert reserved["future_income_included"] is False


def test_automatic_shared_mortgage_rule_does_not_change_commitment(ledger_db):
    from utils.shared_expenses import save_shared_settings, set_shared_rule
    from utils.planner import bills_and_commitments

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month, rent=2000)
    save_shared_settings(
        ledger_db, shared_with_name="Household partner",
        default_user_share_pct=50,
    )
    set_shared_rule(
        ledger_db, scope_type="merchant", scope_value="LANDLORD RENT",
        user_share_pct=50,
    )
    ledger_db.commit()

    bills = bills_and_commitments(conn=ledger_db)
    mortgage = next(
        item for item in bills["items"]
        if item.get("merchant") == "LANDLORD RENT"
    )
    assert mortgage["household_amount"] == pytest.approx(2000)
    assert mortgage["est_amount"] == pytest.approx(2000)
    household_total = sum(
        float(item.get("household_amount") or 0)
        for item in bills["items"] if item.get("included_in_forecast")
    )
    assert bills["commitment_monthly_estimate"] == pytest.approx(household_total)


def test_insights_has_one_amount_basis_when_automatic_share_rules_exist(ledger_db):
    from desktop.engine.ledger_engine import insights_summary_action
    from utils.shared_expenses import save_shared_settings, set_shared_rule

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month, rent=2000)
    save_shared_settings(
        ledger_db, shared_with_name="Household partner",
        default_user_share_pct=50,
    )
    set_shared_rule(
        ledger_db, scope_type="merchant", scope_value="LANDLORD RENT",
        user_share_pct=50,
    )
    ledger_db.commit()

    personal = insights_summary_action({"period_days": 90, "share_view": "personal"})
    household = insights_summary_action({"period_days": 90, "share_view": "household"})
    personal_rent = next(
        row for row in personal["recurring"] if row["merchant"] == "LANDLORD RENT"
    )
    household_rent = next(
        row for row in household["recurring"] if row["merchant"] == "LANDLORD RENT"
    )
    assert personal_rent["avg_amount"] == pytest.approx(2000)
    assert personal_rent["household_amount"] == pytest.approx(2000)
    assert household_rent["avg_amount"] == pytest.approx(2000)
    personal_housing = next(
        row for row in personal["categories"]
        if row["category"] == "Housing / Mortgage"
    )
    household_housing = next(
        row for row in household["categories"]
        if row["category"] == "Housing / Mortgage"
    )
    assert household_housing["total"] == pytest.approx(personal_housing["total"])


def test_home_plan_insights_and_charts_share_one_cashflow_truth(ledger_db):
    """Every native surface must consume the canonical role calculation."""
    from desktop.engine.ledger_engine import (
        _plan_payload, home_dashboard_action, home_summary,
        insights_summary_action,
    )
    from utils.analytics import compute_cashflow, find_recurring
    from utils.checkin import meaningful_changes

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(
        ledger_db, day="2026-07-02", desc="INVENTED EMPLOYER PAYROLL",
        amount=2400, direction="credit", category="Payroll Income",
    )
    add_tx(
        ledger_db, day="2026-07-04", desc="INTERAC E-TRANSFER FROM FRIEND",
        amount=180, direction="credit", category="Transfer In",
    )
    add_tx(
        ledger_db, day="2026-07-06", desc="INVENTED GROCER",
        amount=125, direction="debit", category="Groceries",
    )
    add_tx(
        ledger_db, day="2026-07-07", desc="INVENTED GROCER REFUND",
        amount=25, direction="credit", category="Refund / Credit",
        account_type="mastercard",
    )
    ledger_db.execute(
        "UPDATE transactions SET subcategory='Groceries' "
        "WHERE raw_description='INVENTED GROCER REFUND'"
    )
    add_tx(
        ledger_db, day="2026-07-08", desc="PAYMENT TO MASTERCARD",
        amount=600, direction="debit", category="Credit Card Payment",
        is_transfer=1,
    )
    add_tx(
        ledger_db, day="2026-07-09", desc="TRANSFER TO SAVINGS",
        amount=500, direction="debit", category="Internal Transfer",
        is_transfer=1,
    )
    add_tx(
        ledger_db, day="2026-07-10", desc="TRANSFER TO INVENTED BROKERAGE",
        amount=350, direction="debit", category="Investments",
        is_transfer=1,
    )
    save_plan(ledger_db, "2026-07", income=4800, spending=2800, savings=500)
    account_id = create_account(
        {"name": "Everyday Chequing", "type": "chequing"}, conn=ledger_db,
    )
    _linked_balance(
        ledger_db, account_id, "Everyday Chequing", "chequing", 4100,
        "2026-07-10",
    )
    ledger_db.commit()

    home = home_summary({"period_days": 30})
    insights = insights_summary_action({"period_days": 30})
    dashboard = home_dashboard_action({"period_days": 30})
    expected = compute_cashflow(
        home["period_start"], home["period_end"], conn=ledger_db,
    )
    plan_conn = get_connection()
    try:
        plan = _plan_payload(plan_conn)
    finally:
        plan_conn.close()

    assert home["progress"]["mtd_income"] == expected["income"]
    assert home["progress"]["mtd_spending"] == expected["spending"]
    assert insights["cash_flow_summary"]["money_in"] == expected["income"]
    assert insights["cash_flow_summary"]["spending"] == expected["spending"]
    assert insights["cash_flow_summary"]["net_cash_flow"] == expected["net"]
    assert dashboard["income_total"] == expected["income"]
    assert sum(row["amount"] for row in dashboard["categories"]["items"]) == pytest.approx(
        expected["spending"], abs=0.02,
    )
    assert sum(row["total"] for row in insights["categories"]) == pytest.approx(
        expected["spending"], abs=0.02,
    )
    assert plan["safe_to_spend"]["amount"] == home["safe_to_spend"]["amount"]
    assert plan["safe_to_spend"]["reserved"] == home["safe_to_spend"]["reserved"]
    assert plan["outcome"]["state"] == home["verdict"]["state"]
    assert plan["outcome"]["headline"] == home["verdict"]["headline"]
    assert plan["risk_level"] == home["progress"]["risk_level"]

    excluded_names = {
        "INVENTED EMPLOYER PAYROLL", "PAYMENT TO MASTERCARD",
        "TRANSFER TO SAVINGS", "TRANSFER TO INVENTED BROKERAGE",
    }
    recurring_names = {str(row.get("merchant") or "") for row in find_recurring(conn=ledger_db)}
    assert recurring_names.isdisjoint(excluded_names)
    finding_text = str(meaningful_changes(conn=ledger_db)).upper()
    assert "PAYMENT TO MASTERCARD" not in finding_text
    assert "TRANSFER TO SAVINGS" not in finding_text


def test_inferred_biweekly_payroll_needs_one_explicit_confirmation(ledger_db):
    from utils.analytics import (
        income_confirmation_candidates, stable_income_summary,
    )
    from utils.database import set_income_source_preference

    for month in ("2026-04", "2026-05", "2026-06"):
        for day in ("05", "19"):
            add_tx(
                ledger_db, day=f"{month}-{day}",
                desc="INVENTED STABLE DEPOSIT", amount=2050,
                direction="credit", category="Payroll Income",
            )
    add_tx(
        ledger_db, day="2026-07-02", desc="INVENTED CURRENT-MONTH GROCER",
        amount=25, direction="debit", category="Groceries",
    )
    ledger_db.execute(
        "UPDATE transactions SET category_source='protected',transaction_type=NULL "
        "WHERE raw_description='INVENTED STABLE DEPOSIT'"
    )
    ledger_db.commit()

    assert stable_income_summary(conn=ledger_db)["confirmed"] is False
    candidates = income_confirmation_candidates(conn=ledger_db)
    assert len(candidates) == 1
    assert candidates[0]["monthly_amount"] == pytest.approx(4100)
    assert candidates[0]["cadence"] == "about every two weeks"

    set_income_source_preference(
        candidates[0]["source_normalized"], "confirmed", conn=ledger_db,
    )
    ledger_db.commit()
    stable = stable_income_summary(conn=ledger_db)
    assert stable["confirmed"] is True
    assert stable["monthly_amount"] == pytest.approx(4100)


def test_interac_from_and_to_are_cashflow_until_explicitly_internal():
    from utils.financial_semantics import classify_transaction_type

    assert classify_transaction_type(
        "INTERAC E-TRANSFER FROM JAMIE", "chequing", "credit", "Transfer In",
    ) == "other_income"
    assert classify_transaction_type(
        "INTERAC E-TRANSFER TO JAMIE", "chequing", "debit", "Transfer Out",
    ) == "spending"
    assert classify_transaction_type(
        "INTERAC E-TRANSFER FROM MY SAVINGS", "chequing", "credit",
        "Internal Transfer",
    ) == "internal_transfer"


def test_demo_profile_uses_canonical_signed_amounts(tmp_path):
    import sqlite3

    from scripts.create_demo_data import main as create_demo_data

    db_path = tmp_path / "finance.db"
    assert create_demo_data([
        "--out", str(db_path), "--force", "--months", "3",
    ]) == 0
    conn = sqlite3.connect(db_path)
    try:
        purchase_min, purchase_max = conn.execute(
            "SELECT MIN(amount),MAX(amount) FROM transactions "
            "WHERE transaction_type='spending'"
        ).fetchone()
        payroll_min = conn.execute(
            "SELECT MIN(amount) FROM transactions "
            "WHERE transaction_type='employment_income'"
        ).fetchone()[0]
        transfers = conn.execute(
            "SELECT amount,transaction_type FROM transactions "
            "WHERE category='Internal Transfer'"
        ).fetchall()
    finally:
        conn.close()

    assert purchase_min < 0 and purchase_max < 0
    assert payroll_min > 0
    assert {row[1] for row in transfers} == {"internal_transfer"}
    assert {row[0] > 0 for row in transfers} == {False, True}

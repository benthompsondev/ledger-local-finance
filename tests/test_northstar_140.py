"""Northstar 1.4 planning, reconciliation, and Safe-to-Spend regressions.

All values are invented and every database comes from pytest's disposable
``tmp_path`` fixture.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import add_tx, save_plan, seed_balances, seed_month


def _linked_balance(conn, account_id: int, name: str, kind: str,
                    amount: float, as_of: str) -> None:
    from utils.database import insert_account_balance

    insert_account_balance({
        "account_ref": account_id,
        "account_name": name,
        "account_kind": kind,
        "balance": amount,
        "currency": "CAD",
        "as_of_date": as_of,
    }, conn=conn)


def test_missing_card_balance_falls_back_to_flow_number(ledger_db):
    from utils.checkin import safe_to_spend_summary
    from utils.database import create_account, upsert_monthly_plan

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(
        ledger_db, day="2026-07-10", desc="INVENTED CARD PURCHASE",
        amount=75, direction="debit", category="Groceries",
        account_type="credit_card",
    )
    chequing = create_account({
        "name": "Daily Chequing", "type": "chequing",
    }, conn=ledger_db)
    card = create_account({
        "name": "Everyday Card", "type": "credit_card",
    }, conn=ledger_db)
    ledger_db.execute(
        "UPDATE transactions SET account_ref=? "
        "WHERE account_type='credit_card'",
        (card,),
    )
    _linked_balance(
        ledger_db, chequing, "Daily Chequing", "chequing", 5000, "2026-07-10",
    )
    upsert_monthly_plan({
        "month": "2026-07", "mode": "normal",
        "income_target": 5000, "spending_target": 3505,
        "fixed_obligations": 1905, "flexible_allowance": 1600,
        "savings_target": 750, "safety_buffer": 250,
        "detected_commitments_at_save": 1905,
        "fixed_override_reason": "",
    }, conn=ledger_db)
    ledger_db.commit()

    # 1.4.1: a missing card balance no longer blocks Safe to Spend. The flow
    # budget number (typical income − recurring − savings) still shows; only
    # the cash-reconciled refinement waits for the balance.
    missing = safe_to_spend_summary(conn=ledger_db, today="2026-07-10")
    assert missing["available"] is True
    assert missing["reserved"].get("basis") == "flow"
    assert missing["amount"] >= 0

    # A stale card balance likewise falls back to the flow number, not a wall.
    _linked_balance(
        ledger_db, card, "Everyday Card", "credit_card", 400, "2026-07-09",
    )
    ledger_db.commit()
    stale = safe_to_spend_summary(conn=ledger_db, today="2026-07-10")
    assert stale["available"] is True
    assert stale["reserved"].get("basis") == "flow"

    # Once every balance is current, the cash-reconciled path takes over and
    # reserves the real card liability.
    _linked_balance(
        ledger_db, card, "Everyday Card", "credit_card", 400, "2026-07-10",
    )
    ledger_db.commit()
    ready = safe_to_spend_summary(conn=ledger_db, today="2026-07-10")
    assert ready["available"] is True
    assert ready["reserved"]["credit_card_liability"] == 400


def test_reliable_income_uses_recurring_medians_not_refunds(ledger_db):
    from utils.analytics import reliable_income_summary
    from utils.database import set_income_source_preference

    for month, payroll in (
        ("2026-04", 4000), ("2026-05", 4200), ("2026-06", 4000),
    ):
        for day in range(1, 15):
            add_tx(
                ledger_db, day=f"{month}-{day:02d}",
                desc=f"{month} COVERAGE {day}", amount=1,
                direction="debit", category="Internal Transfer",
                is_transfer=1,
            )
        add_tx(
            ledger_db, day=f"{month}-28", desc=f"{month} COVERAGE END",
            amount=1, direction="debit", category="Internal Transfer",
            is_transfer=1,
        )
        add_tx(
            ledger_db, day=f"{month}-05", desc="INVENTED EMPLOYER PAYROLL",
            amount=payroll, direction="credit", category="Payroll Income",
            merchant="Invented Employer",
        )
        add_tx(
            ledger_db, day=f"{month}-10",
            desc="INTERAC E-TRANSFER FROM INVENTED TENANT",
            amount=200, direction="credit", category="Transfer In",
            merchant="Invented Tenant",
        )
        add_tx(
            ledger_db, day=f"{month}-20", desc="MONTHLY INTEREST",
            amount=10, direction="credit", category="Interest Income",
            merchant="Invented Bank Interest",
        )
        add_tx(
            ledger_db, day=f"{month}-22", desc="INVENTED STORE REFUND",
            amount=500, direction="credit", category="Refund / Credit",
            merchant="Invented Store",
        )
    add_tx(
        ledger_db, day="2026-07-01", desc="CURRENT MONTH ANCHOR",
        amount=1, direction="debit", category="Groceries",
    )
    payroll_key = ledger_db.execute(
        "SELECT merchant_normalized FROM transactions "
        "WHERE transaction_type='employment_income' LIMIT 1"
    ).fetchone()[0]
    set_income_source_preference(
        payroll_key, "confirmed", conn=ledger_db,
    )
    ledger_db.commit()

    reliable = reliable_income_summary(conn=ledger_db)

    assert reliable["confirmed"] is True
    assert reliable["monthly_amount"] == pytest.approx(4210)
    kinds = {source["kind"] for source in reliable["sources"]}
    assert kinds == {"Payroll", "Interest", "Recurring money in"}
    assert all("refund" not in source["source"].lower()
               for source in reliable["sources"])


def test_deliberate_fixed_override_survives_until_commitments_change(ledger_db):
    from utils.database import upsert_monthly_plan
    from utils.planner import (
        bills_and_commitments, plan_commitment_agreement,
    )

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    bills = bills_and_commitments(conn=ledger_db)
    detected = bills["commitment_monthly_estimate"]
    upsert_monthly_plan({
        "month": "2026-07", "mode": "normal", "income_target": 5000,
        "spending_target": 3300, "fixed_obligations": 1700,
        "flexible_allowance": 1600, "savings_target": 750,
        "safety_buffer": 250,
        "detected_commitments_at_save": detected,
        "fixed_override_reason": "Rent changes next month",
    }, conn=ledger_db)
    plan = ledger_db.execute(
        "SELECT * FROM monthly_plans WHERE month='2026-07'"
    ).fetchone()
    accepted = plan_commitment_agreement(
        dict(plan), bills=bills, conn=ledger_db,
    )
    assert accepted["agreed"] is True
    assert accepted["override_active"] is True

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        add_tx(
                ledger_db, day=f"{month}-12", desc="INVENTED INSURANCE",
                amount=50, direction="debit", category="Utilities / Bills",
            merchant="Invented Insurance",
        )
    ledger_db.commit()
    changed = plan_commitment_agreement(dict(plan), conn=ledger_db)
    assert changed["agreed"] is False
    assert changed["needs_review"] is True
    assert changed["detected"] > accepted["detected"]


def test_completed_goal_contribution_only_reserves_the_remainder(ledger_db):
    from utils import database as db
    from utils.goals import goal_plan_summary

    goal_id = db.insert_goal({
        "name": "Emergency reserve", "type": "emergency_fund",
        "target_amount": 5000, "current_amount": 0,
        "status": "active", "planned_monthly_contribution": 800,
        "contribution_frequency": "monthly", "include_in_plan": 1,
    }, conn=ledger_db)
    db.record_goal_contribution(
        goal_id, 300, "2026-07-08", conn=ledger_db,
    )
    ledger_db.commit()

    summary = goal_plan_summary(
        800, conn=ledger_db, today=date(2026, 7, 10),
    )
    assert summary["effective_savings_target"] == 800
    assert summary["contributed_this_month"] == 300
    assert summary["remaining_savings_target"] == 500
    assert summary["goal_allocations"][0]["remaining"] == 500


def test_flexible_pace_excludes_fixed_commitments_and_keeps_total_view(ledger_db):
    from utils.insights import spending_pace

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month, grocery_days=1, grocery_amount=100)
    add_tx(
        ledger_db, day="2026-07-01", desc="LANDLORD RENT",
        amount=1800, direction="debit", category="Housing / Mortgage",
    )
    add_tx(
        ledger_db, day="2026-07-08", desc="INVENTED GROCER JULY",
        amount=125, direction="debit", category="Groceries",
    )
    ledger_db.commit()

    total = spending_pace(
        conn=ledger_db, today=date(2026, 7, 10),
    )
    flexible = spending_pace(
        conn=ledger_db, today=date(2026, 7, 10), flexible_only=True,
    )

    assert total["current_total"] == pytest.approx(1925)
    assert flexible["current_total"] == pytest.approx(125)
    assert total["view"] == "total"
    assert flexible["view"] == "flexible"


def test_stale_data_never_reports_plan_ready_to_use(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    save_plan(ledger_db, date.today().strftime("%Y-%m"))
    seed_balances(ledger_db, date.today().isoformat())

    payload = _plan_payload(ledger_db)

    assert payload["outcome"]["state"] == "stale_data"
    assert payload["readiness"]["forecast_ready"] is False
    freshness = next(
        item for item in payload["readiness"]["missing"]
        if item["id"] == "freshness"
    )
    assert freshness["screen"] == "add-data"
    assert "latest" in freshness["label"].lower()


def test_insights_has_one_income_card_and_inline_review_controls():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "desktop" / "src" / "InsightsView.tsx"
    ).read_text(encoding="utf-8")
    assert "Ranked income sources" not in source
    # Named for what the choice does, not for how steady the deposits look.
    # "Stable: Yes / No" read as a judgement about variability while the
    # exclusion moved no number; it now removes the source from planning
    # income, so the button has to say that.
    assert "Use as income" in source
    assert "Exclude" in source
    assert "Automatic" in source
    assert "Not recurring" in source

    settings = (
        Path(__file__).resolve().parents[1]
        / "desktop" / "src" / "SettingsView.tsx"
    ).read_text(encoding="utf-8")
    assert "Use as stable income" not in settings
    assert "Exclude from stable income" not in settings
    assert "Use as income" in settings
    assert "Excluded from planning income" in settings

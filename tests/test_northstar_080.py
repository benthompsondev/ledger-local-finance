"""Sanitized regressions for Northstar 0.8 shared finances and review flow."""
from __future__ import annotations

import io
import json

import pytest

from desktop.engine import ledger_engine


def request(action: str, params: dict | None = None) -> dict:
    output = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params or {}}) + "\n"),
        output,
    )
    payload = json.loads(output.getvalue())
    assert code == 0, payload
    return payload["data"]


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def account(name="Invented chequing", kind="chequing") -> int:
    return request("create_account", {"name": name, "type": kind})["created_id"]


def tx(account_id: int, day: str, description: str, amount: float,
       direction: str, category: str, merchant: str = "") -> dict:
    return request("add_manual_transaction", {
        "account_id": account_id, "date": day, "description": description,
        "merchant": merchant, "amount": amount, "direction": direction,
        "category": category,
    })["transaction"]


def test_automatic_shared_rule_is_inert_for_rows_and_plan(engine_env):
    aid = account()
    for month in ("2026-04", "2026-05", "2026-06"):
        tx(aid, f"{month}-01", "NESTO MORTGAGE", 2622.45, "debit",
           "Housing / Mortgage", "Nesto")
    request("save_shared_settings", {
        "shared_with_name": "Taylor", "default_user_share_pct": 50,
    })
    request("set_shared_rule", {
        "scope_type": "merchant", "scope_value": "Nesto Mortgage",
        "user_share_pct": 50, "enabled": True,
    })

    rows = request("list_transactions", {"search": "Nesto"})["transactions"]
    assert rows[0]["amount"] == -2622.45
    assert rows[0]["household_cost"] == 2622.45
    assert rows[0]["user_share"] == 2622.45
    assert rows[0]["other_share"] == 0
    plan = request("plan_summary")
    mortgage = next(item for item in plan["fixed_commitments"]
                    if "NESTO" in item["merchant"].upper())
    assert mortgage["household_amount"] == 2622.45
    assert mortgage["amount"] == 2622.45


def test_partner_reimbursement_is_money_in_but_not_payroll_or_personal_net(engine_env):
    aid = account()
    tx(aid, "2026-04-05", "INVENTED EMPLOYER PAYROLL", 2000, "credit",
       "Payroll Income", "Invented Employer")
    spend = tx(aid, "2026-04-06", "SHARED GROCER", 200, "debit",
               "Groceries", "Shared Grocer")
    request("save_shared_settings", {
        "shared_with_name": "Taylor", "default_user_share_pct": 50,
    })
    request("correct_transaction", {
        "id": spend["id"], "category": "Groceries",
        "transaction_type": "spending", "shared_expense_override": True,
        "shared_user_share_pct": 50,
    })
    reimbursement = tx(
        aid, "2026-04-07", "INTERAC E-TRANSFER FROM TAYLOR", 100,
        "credit", "Income", "Taylor",
    )
    review = request("list_transactions", {"search": "TAYLOR"})["transactions"][0]
    assert review["suggested_transaction_type"] == "shared_reimbursement"
    request("correct_transaction", {
        "id": reimbursement["id"], "category": "Income",
        "transaction_type": "shared_reimbursement",
    })
    personal = request("insights_summary", {"period_days": 30, "share_view": "personal"})
    household = request("insights_summary", {"period_days": 30, "share_view": "household"})
    assert personal["cash_flow_summary"]["money_in"] == 2100
    assert personal["cash_flow_summary"]["shared_reimbursements"] == 100
    assert personal["cash_flow_summary"]["spending"] == 100
    assert personal["cash_flow_summary"]["net_cash_flow"] == 1900
    assert household["cash_flow_summary"]["spending"] == 100
    assert household["cash_flow_summary"]["net_cash_flow"] == 1900
    assert personal["cash_flow_summary"]["stable_income_received"] == 2000


def test_review_editor_payload_and_actionable_forecast_links(engine_env):
    aid = account()
    mystery = tx(aid, "2026-06-01", "INVENTED MYSTERY SHOP", 750, "debit",
                 "Uncategorized", "Mystery Shop")
    quick = request("list_transactions", {"quick_review": True})
    assert quick["transactions"][0]["id"] == mystery["id"]
    assert quick["transactions"][0]["review_priority"] >= 750
    corrected = request("correct_transaction", {
        "id": mystery["id"], "category": "Shopping",
        "transaction_type": "spending", "apply_to_matching": False,
        "remember_rule": True,
    })
    assert corrected["transaction"]["category"] == "Shopping"
    assert corrected["rule_saved"] is True
    plan = request("plan_summary")
    destinations = {
        item["id"]: item["screen"]
        for item in (
            plan["readiness"]["missing"]
            + plan["readiness"]["advisories"]
        )
    }
    assert destinations["plan"] == "plan#commitment-review"
    assert any(
        key.startswith("balance:")
        and screen == "insights#account-balances"
        for key, screen in destinations.items()
    )
    assert destinations["income"] == "settings#income-sources"


def test_investing_goal_supports_quarterly_manual_progress(engine_env):
    created = request("create_goal", {
        "name": "Invented index fund", "type": "investing",
        "target_amount": 12000, "current_amount": 1000,
        "monthly_contribution": 750, "contribution_frequency": "quarterly",
        "progress_method": "manual",
    })
    goal = created["goals"][0]
    assert goal["contribution_frequency"] == "quarterly"
    assert goal["contribution_target"] == 750
    request("contribute_goal", {
        "goal_id": goal["id"], "amount": 250,
        "date": "2026-07-15", "note": "Invented contribution",
    })
    current = request("goals_summary")["goals"][0]
    assert current["contributed_this_period"] == 250
    templates = request("goals_summary")["templates"]
    assert next(t for t in templates if t["type"] == "investing")["primary"] is True
    assert next(t for t in templates if t["type"] == "true_expense")["primary"] is True

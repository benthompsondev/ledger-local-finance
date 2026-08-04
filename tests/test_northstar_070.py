"""Sanitized regressions for the Northstar 0.7 clarity pass."""
from __future__ import annotations

import io
import json

import pytest

from desktop.engine import ledger_engine
from utils.financial_semantics import classify_transaction_type


def _request(payload: dict) -> tuple[int, dict]:
    output = io.StringIO()
    code = ledger_engine.run(io.StringIO(json.dumps(payload) + "\n"), output)
    return code, json.loads(output.getvalue())


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def _account() -> int:
    code, payload = _request({
        "action": "create_account",
        "params": {"name": "Invented chequing", "type": "chequing"},
    })
    assert code == 0
    return payload["data"]["created_id"]


def _tx(account_id: int, day: str, description: str, amount: float,
        direction: str, category: str, merchant: str = "") -> None:
    code, payload = _request({
        "action": "add_manual_transaction",
        "params": {"account_id": account_id, "date": day,
                   "description": description, "merchant": merchant,
                   "amount": amount, "direction": direction,
                   "category": category},
    })
    assert code == 0, payload


def test_stable_payroll_is_not_redefined_by_saved_plan(engine_env) -> None:
    account_id = _account()
    for month in ("2026-04", "2026-05", "2026-06"):
        for day in ("05", "19"):
            _tx(account_id, f"{month}-{day}", "INVENTED PAYROLL", 2000,
                "credit", "Payroll Income", "INVENTED EMPLOYER")
        for day in range(1, 15):
            _tx(account_id, f"{month}-{day:02d}",
                f"{month} COVERAGE {day}", 1, "debit", "Internal Transfer")
        _tx(account_id, f"{month}-28", f"{month} COVERAGE END", 1,
            "debit", "Internal Transfer")
    _tx(account_id, "2026-07-02", "INVENTED GROCER", 20, "debit", "Groceries")
    code, rejected = _request({"action": "save_plan", "params": {
        "month": "2026-06", "mode": "normal", "income_target": 9999,
        "fixed_obligations": 1000, "flexible_allowance": 7999,
        "savings_target": 500, "safety_buffer": 500,
        "fixed_override_reason": "Synthetic override",
    }})
    assert code == 1
    assert "reliable income floor" in rejected["error"]
    code, _ = _request({"action": "save_plan", "params": {
        "month": "2026-07", "mode": "normal", "income_target": 4000,
        "fixed_obligations": 0, "flexible_allowance": 3000,
        "savings_target": 500, "safety_buffer": 500,
    }})
    assert code == 0

    code, payload = _request({"action": "plan_summary"})
    assert code == 0
    stable = payload["data"]["payroll_income"]
    assert stable["confirmed"] is True
    assert stable["monthly_amount"] == 4000
    assert stable["label"] == "Confirmed recurring payroll"


def test_plan_preview_owns_the_exact_equation(engine_env) -> None:
    code, payload = _request({"action": "preview_plan", "params": {
        "mode": "normal", "income_target": 5000,
        "fixed_obligations": 2200, "savings_target": 1000,
        "safety_buffer": 200,
    }})
    assert code == 0
    assert payload["data"]["equation"] == {
        "income": 5000.0, "fixed": 2200.0, "savings": 1000.0,
        "nonmonthly_reserves": 0.0,
        "buffer": 200.0, "flexible": 1600.0, "unallocated": 1600.0,
        "coherent": True,
        "explanation": (
            "Typical monthly income minus fixed costs, nonmonthly reserves, "
            "savings, and buffer leaves one flexible spending amount."
        ),
    }


def test_questrade_movement_is_not_income() -> None:
    assert classify_transaction_type(
        "EFT DEPOSIT FROM QUESTRADE", "chequing", "credit", "Income"
    ) == "investment_transfer"


def test_goal_lifecycle_and_quick_net_worth_are_local(engine_env) -> None:
    code, created = _request({"action": "create_goal", "params": {
        "name": "Invented emergency fund", "type": "emergency_fund",
        "target_amount": 6000, "current_amount": 1500,
        "target_date": "2026-12-31", "monthly_contribution": 500,
        "progress_method": "manual",
    }})
    assert code == 0
    goal = created["data"]["goals"][0]
    assert goal["milestone"] == "First quarter reached"
    assert goal["required_monthly_pace"] > 0

    code, paused = _request({"action": "update_goal", "params": {
        "goal_id": goal["id"], "name": goal["name"],
        "type": goal["type"], "target_amount": goal["target_amount"],
        "target_date": goal["target_date"], "monthly_contribution": 500,
        "progress_method": "manual", "status": "paused",
    }})
    assert code == 0
    assert paused["data"]["goals"][0]["status"] == "paused"

    code, completed = _request({"action": "update_goal", "params": {
        "goal_id": goal["id"], "name": goal["name"],
        "type": goal["type"], "target_amount": goal["target_amount"],
        "target_date": goal["target_date"], "monthly_contribution": 500,
        "progress_method": "manual", "status": "completed",
    }})
    assert code == 0
    completed_goal = completed["data"]["goals"][0]
    assert completed_goal["status"] == "completed"
    assert completed_goal["progress_pct"] == 1.0
    assert completed_goal["milestone"] == "Complete"
    assert completed_goal["gap"] == 0

    code, estimate = _request({"action": "save_quick_net_worth", "params": {
        "total_assets": 12000, "total_liabilities": 3500,
        "as_of_date": "2026-07-01", "period_days": 30,
    }})
    assert code == 0
    assert estimate["data"]["quick_net_worth"]["net_worth"] == 8500
    assert estimate["data"]["net_worth"]["calculated"] is False

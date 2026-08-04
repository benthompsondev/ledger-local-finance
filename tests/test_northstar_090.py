"""Sanitized regressions for the Northstar 0.9 quality pass."""
from __future__ import annotations

import io
import json
from datetime import date

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


def test_setup_actions_resolve_to_native_destinations(engine_env):
    home = request("home_summary", {"period_days": 30})
    assert home["verdict"]["setup_steps"] == [
        {"label": "Import a statement", "screen": "add-data"}
    ]
    assert all(action["screen"] for action in home["actions"])

    plan = request("plan_summary")
    routes = {
        item["id"]: item["screen"]
        for item in (
            plan["readiness"]["missing"]
            + plan["readiness"]["advisories"]
        )
    }
    assert any(
        key.startswith("balance:")
        and screen == "settings#planning-balances"
        for key, screen in routes.items()
    )
    assert routes["history"] == "add-data"
    assert routes["plan"] == "plan#commitment-review"


@pytest.mark.parametrize(("description", "direction", "expected"), [
    ("PLAYSTATION NETWORK PURCHASE", "debit", "Gaming / Entertainment"),
    ("OPENAI CHATGPT SUBSCRIPTION", "debit", "Subscriptions & Digital"),
    ("ANTHROPIC SAN FRANCISCO", "debit", "Subscriptions & Digital"),
    ("RUNWARE AI SERVICE", "debit", "Subscriptions & Digital"),
    ("DEEPLEARNING.AI COURSE", "debit", "Subscriptions & Digital"),
    ("PORKBUN DOMAIN RENEWAL", "debit", "Domains / Web services"),
    ("THRIFTYS CLOTHING", "debit", "Shopping / Clothing"),
    ("PETSMART STORE", "debit", "Pets"),
    ("CRA PAYMENT", "debit", "Government / Taxes"),
    ("BILL PAYMENT - CRA REVENUE", "debit", "Government / Taxes"),
    ("NESTO HOME LOAN", "debit", "Housing / Mortgage"),
    ("INTERAC e-Transfer To: INVENTED PERSON", "debit", "E-transfer / Personal payment"),
])
def test_safe_merchant_and_etransfer_mappings(
    engine_env, description, direction, expected
):
    from utils import database
    from utils.categorizer import enrich_transaction

    database.init_db()
    with database.get_connection() as conn:
        enriched = enrich_transaction({
            "raw_description": description,
            "amount": -25.0 if direction == "debit" else 25.0,
            "direction": direction,
            "account_type": "chequing",
        }, conn=conn)
    assert enriched["category"] == expected
    assert enriched["transaction_type"] in {"spending", "mortgage_payment"}


def test_person_name_rule_never_auto_classifies_etransfer(engine_env):
    from utils import database
    from utils.categorizer import enrich_transaction

    database.init_db()
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO learned_rules (merchant_normalized,category,subcategory,enabled) "
            "VALUES ('INVENTED PERSON','Shopping','',1)"
        )
        outgoing = enrich_transaction({
            "raw_description": "INTERAC e-Transfer To: INVENTED PERSON",
            "amount": -40.0, "direction": "debit", "account_type": "chequing",
        }, conn=conn)
        incoming = enrich_transaction({
            "raw_description": "INTERAC e-Transfer From: INVENTED PERSON",
            "amount": 40.0, "direction": "credit", "account_type": "chequing",
        }, conn=conn)
    assert outgoing["category"] == "E-transfer / Personal payment"
    assert outgoing["category_source"] == "import_rule"
    assert incoming["category"] == "Income"
    assert incoming["transaction_type"] == "other_income"


def test_savings_withdrawal_to_owned_institution_stays_internal(engine_env):
    from utils import database
    from utils.categorizer import enrich_transaction

    database.init_db()
    with database.get_connection() as conn:
        row = enrich_transaction({
            "raw_description": "Internet Withdrawal to Tangerine",
            "amount": -200.0, "direction": "debit", "account_type": "savings",
        }, conn=conn)
    assert row["category"] == "Internal Transfer"
    assert row["transaction_type"] == "internal_transfer"


def test_cashback_is_income_and_never_reduces_spending(engine_env):
    from utils import database
    from utils.categorizer import enrich_transaction
    from utils.financial_semantics import cashflow_role, cashflow_totals

    database.init_db()
    with database.get_connection() as conn:
        row = enrich_transaction({
            "raw_description": "CREDIT CARD REWARDS REDEMPTION",
            "amount": 25.00,
            "direction": "credit",
            "account_type": "savings",
        }, conn=conn)
    assert row["category"] == "Rewards / Cashback"
    assert row["transaction_type"] == "rewards_credit"
    assert cashflow_role(row) == "income"
    assert row["is_flagged"] == 0
    totals = cashflow_totals([
        {"amount": -100, "transaction_type": "spending"}, row,
    ])
    assert totals["income"] == 25
    assert totals["refund_income"] == 25
    assert totals["spending"] == 100


def test_cashflow_drilldown_filters_match_headline_semantics(engine_env):
    account_id = request("create_account", {"name": "Invented account", "type": "chequing"})["created_id"]
    rows = [
        ("2026-07-01", "PAYROLL DEPOSIT", 1000, "credit", "Payroll Income"),
        ("2026-07-02", "INVENTED STORE", -75, "debit", "Shopping"),
        ("2026-07-03", "TRANSFER TO SAVINGS", -250, "debit", "Internal Transfer"),
        ("2026-07-04", "INVENTED STORE REFUND", 20, "credit", "Refund / Credit"),
    ]
    for day, description, amount, direction, category in rows:
        request("add_manual_transaction", {"account_id": account_id, "date": day,
            "description": description, "merchant": description, "amount": abs(amount),
            "direction": direction, "category": category})
    income = request("list_transactions", {"cashflow_role": "income"})
    spending = request("list_transactions", {"cashflow_role": "spending"})
    net = request("list_transactions", {"cashflow_role": "net"})
    assert {row["transaction_type"] for row in income["transactions"]} == {
        "employment_income", "refund",
    }
    assert [row["transaction_type"] for row in spending["transactions"]] == ["spending"]
    assert len(net["transactions"]) == 3


def test_goal_streak_uses_real_contributions_and_milestones_can_be_hidden(engine_env):
    created = request("create_goal", {"name": "Invented reserve", "type": "general_savings",
        "target_amount": 1000, "current_amount": 0, "monthly_contribution": 100,
        "progress_method": "manual", "show_milestones": False})
    goal_id = created["created_id"]
    today = date.today()
    previous_month = today.month - 1 or 12
    previous_year = today.year if today.month > 1 else today.year - 1
    request("contribute_goal", {"goal_id": goal_id, "amount": 100,
        "date": f"{previous_year:04d}-{previous_month:02d}-15", "note": "Invented"})
    request("contribute_goal", {"goal_id": goal_id, "amount": 100,
        "date": today.replace(day=15).isoformat(), "note": "Invented"})
    goal = request("goals_summary")["goals"][0]
    assert goal["contribution_streak_months"] == 2
    assert goal["show_milestones"] is False


def test_react_sources_keep_durable_route_targets():
    app = open("desktop/src/App.tsx", encoding="utf-8").read()
    home = open("desktop/src/HomeView.tsx", encoding="utf-8").read()
    settings = open("desktop/src/SettingsView.tsx", encoding="utf-8").read()
    assert "focusAnchor" in app and "focusToken" in app
    assert 'transactions?quickReview=1' in app or "quickReview" in app
    assert "onNavigate(action.screen)" in home
    assert 'id="merchant-rules"' in settings
    assert 'id="recurring-costs"' in settings

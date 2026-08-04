"""Regressions for the Northstar 1.2 Insights/trust pass.

Covers the verified defects:
  * a fixed commitment (mortgage) whose personal-share total differs between
    months must never surface as a behavioral "meaningful change";
  * behavioral findings must not be built from a partial current month;
  * every finding drill uses structured native fields, never a Streamlit page.
"""
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


def _account(name="Everyday Chequing", kind="chequing") -> int:
    return request("create_account", {"name": name, "type": kind})["created_id"]


def _tx(account_id, day, description, amount, direction, category, merchant=""):
    return request("add_manual_transaction", {
        "account_id": account_id, "date": day, "description": description,
        "merchant": merchant, "amount": amount, "direction": direction,
        "category": category,
    })["transaction"]


def _seed_complete_month(aid, month, eom):
    """Give a month >=14 distinct days ending near month-end -> 'complete'."""
    _tx(aid, f"2026-{month}-05", "INVENTED EMPLOYER PAYROLL", 3200.0, "credit",
        "Payroll Income", "Invented Employer")
    for d in range(6, 22):  # 16 distinct coffee days
        _tx(aid, f"2026-{month}-{d:02d}", "DAILY CAFE", 5.25, "debit",
            "Food & Convenience", "Daily Cafe")
    _tx(aid, f"2026-{month}-{eom - 1:02d}", "MONTH-END SHOP", 40.0, "debit",
        "Shopping", "Month End Shop")


def test_fixed_commitment_share_shift_is_not_a_meaningful_change(engine_env):
    from utils.checkin import meaningful_changes

    aid = _account()
    mort = {}
    for day, mo in [("2026-04-01", "04"), ("2026-05-02", "05"),
                    ("2026-06-01", "06")]:
        mort[mo] = _tx(aid, day, "NESTO MORTGAGE", 2622.45, "debit",
                       "Housing / Mortgage", "Nesto")
    for mo, eom in [("04", 30), ("05", 31), ("06", 30)]:
        _seed_complete_month(aid, mo, eom)
    # A real, controllable increase that SHOULD surface.
    _tx(aid, "2026-05-10", "SUPER GROCER", 180.0, "debit", "Groceries", "Super Grocer")
    _tx(aid, "2026-06-11", "SUPER GROCER", 320.0, "debit", "Groceries", "Super Grocer")

    # Identical mortgage, but the two latest complete months carry different
    # personal shares — an attribution shift, not a spending change.
    request("save_shared_settings", {"shared_with_name": "Taylor",
                                      "default_user_share_pct": 50})
    for mo, pct in [("04", 50), ("05", 40), ("06", 50)]:
        request("correct_transaction", {
            "id": mort[mo]["id"], "category": "Housing / Mortgage",
            "transaction_type": "spending", "shared_expense_override": True,
            "shared_user_share_pct": pct,
        })

    changes = meaningful_changes()
    titles = " | ".join(c["title"] for c in changes)
    assert "Housing / Mortgage" not in titles, titles
    assert "Groceries" in titles, titles
    for change in changes:
        drill = change.get("drill") or {}
        assert "page" not in drill
        if drill.get("category"):
            assert drill["cashflow_role"] == "spending"
            assert drill["start_date"] <= drill["end_date"]


def test_no_behavioral_findings_without_two_complete_months(engine_env):
    from utils.checkin import meaningful_changes

    aid = _account()
    _seed_complete_month(aid, "05", 31)          # one complete month
    _tx(aid, "2026-05-02", "NESTO MORTGAGE", 2622.45, "debit",
        "Housing / Mortgage", "Nesto")
    # Partial current month only (far from month end).
    for d in (2, 3, 6):
        _tx(aid, f"2026-06-{d:02d}", "DAILY CAFE", 5.25, "debit",
            "Food & Convenience", "Daily Cafe")

    # With fewer than two complete months, monthly_review can only fall back to
    # partial data. A behavioral finding built from that would be false, so the
    # surface must stay empty rather than invent a change.
    assert meaningful_changes() == []


def test_income_review_completes_and_clears_readiness(engine_env):
    aid = _account()
    # Two complete months of payroll: recognized but not yet auto-stable
    # (auto-stability needs three months), so it must be confirmed explicitly.
    for mo, eom in [("04", 30), ("05", 31)]:
        _seed_complete_month(aid, mo, eom)
    _tx(aid, "2026-06-03", "DAILY CAFE", 5.25, "debit",
        "Food & Convenience", "Daily Cafe")

    settings = request("category_settings")
    assert settings["income_sources"], "expected a recognized payroll source"
    payroll = max(settings["income_sources"], key=lambda s: s["total"])
    assert payroll["status"] == "automatic"  # unreviewed, not silently stable

    before = request("plan_summary")["readiness"]
    assert before["review"]["income_reviewed"] is False
    assert any(m["id"] == "income" for m in before["missing"])

    request("set_income_source_preference",
            {"source_normalized": payroll["source_normalized"], "status": "confirmed"})

    after = request("plan_summary")["readiness"]
    assert after["review"]["income_reviewed"] is True
    assert after["review"]["last_reviewed"]
    assert not any(m["id"] == "income" for m in after["missing"])


def test_recurring_confirmation_and_exclusion_persist(engine_env):
    from utils.analytics import find_recurring

    aid = _account()
    # A gym membership across three months: bill-like and stable.
    for day in ("2026-04-08", "2026-05-08", "2026-06-08"):
        _tx(aid, day, "CITY FITNESS", 55.0, "debit", "Fitness", "City Fitness")
    merchant = next(r for r in find_recurring()
                    if "FITNESS" in (r["merchant_normalized"] or "").upper())
    key = merchant["merchant_normalized"]

    request("set_recurring_preference", {"merchant_normalized": key, "status": "recurring"})
    confirmed = next(r for r in find_recurring() if r["merchant_normalized"] == key)
    assert confirmed["recurring_status"] == "confirmed"

    request("set_recurring_preference", {"merchant_normalized": key, "status": "not_recurring"})
    assert all(r["merchant_normalized"] != key for r in find_recurring())


def test_spending_pace_respects_share_view(engine_env):
    from datetime import date
    from utils.insights import spending_pace

    aid = _account()
    mort = _tx(aid, "2026-06-01", "NESTO MORTGAGE", 2622.45, "debit",
               "Housing / Mortgage", "Nesto")
    request("save_shared_settings", {"shared_with_name": "Taylor",
                                      "default_user_share_pct": 50})
    request("correct_transaction", {
        "id": mort["id"], "category": "Housing / Mortgage",
        "transaction_type": "spending", "shared_expense_override": True,
        "shared_user_share_pct": 50,
    })

    personal = spending_pace(today=date(2026, 6, 20), share_view="personal")
    household = spending_pace(today=date(2026, 6, 20), share_view="household")
    # My-share pace counts half the shared mortgage; household counts the full
    # amount — the pace chart must honour the same toggle as the categories.
    assert personal["current_total"] == 1311.23
    assert household["current_total"] == 2622.45


def test_category_pace_includes_equivalent_day_average(engine_env):
    from datetime import date
    from utils.insights import category_pace_breakdown

    aid = _account()
    for mo, eom in (("03", 31), ("04", 30), ("05", 31)):
        _seed_complete_month(aid, mo, eom)
    for mo, amt in [("03", 100.0), ("04", 200.0), ("05", 300.0), ("06", 260.0)]:
        _tx(aid, f"2026-{mo}-10", "SUPER GROCER", amt, "debit",
            "Groceries", "Super Grocer")

    cp = category_pace_breakdown(today=date(2026, 6, 10))
    assert cp["has_average"]
    groceries = next(i for i in cp["items"] if i["category"] == "Groceries")
    assert groceries["average"] == 200.0          # mean of Mar/Apr/May same day
    assert groceries["previous"] == 300.0         # last month same day
    assert groceries["average_delta"] == 60.0     # 260 this month vs 200 avg

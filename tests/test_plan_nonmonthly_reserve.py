"""The nonmonthly reserve must reach every part of the plan equation.

Quarterly and annual commitments are set aside monthly. That reserve is real
money already spoken for, so proposal, equation, preview, save, validation and
outlook all have to subtract it exactly once. Previously only the equation
helper knew about it and every caller passed zero, so the plan advertised
flexible room that the reserve had already claimed.
"""
import json
from datetime import date, timedelta

import pytest

from desktop.engine import ledger_engine


def _request(payload: dict) -> tuple[int, dict]:
    import io
    out = io.StringIO()
    code = ledger_engine.run(io.StringIO(json.dumps(payload) + "\n"), out)
    return code, json.loads(out.getvalue())


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.planner.plan_target_month", lambda today=None: "2026-07",
    )
    return tmp_path


def _seed_income_and_quarterly_bill(env):
    """Invented salary plus a quarterly insurance bill on a stable cadence."""
    from utils import database as db
    from tests.conftest import add_tx

    # Bind and migrate the same disposable database the engine will use.
    ledger_engine._bind_database()
    conn = db.get_connection()
    anchor = date(2026, 7, 15)
    # Twelve months of invented salary so income baselines are stable.
    for month_back in range(12):
        month_date = anchor - timedelta(days=30 * month_back)
        add_tx(conn, day=month_date.isoformat(), desc="INVENTED EMPLOYER PAYROLL",
               amount=4000.0, direction="credit", category="Payroll Income")
    # A quarterly commitment, four occurrences ninety days apart.
    for quarter in range(4):
        bill_date = anchor - timedelta(days=90 * quarter)
        add_tx(conn, day=bill_date.isoformat(), desc="INVENTED INSURANCE CO",
               amount=300.0, direction="debit", category="Insurance")
    conn.commit()
    conn.close()


def test_equation_subtracts_the_reserve_exactly_once():
    equation = ledger_engine._plan_equation({
        "income_target": 4000.0,
        "fixed_obligations": 1000.0,
        "savings_target": 400.0,
        "safety_buffer": 200.0,
        "nonmonthly_reserves": 100.0,
    })

    assert equation["nonmonthly_reserves"] == 100.0
    # 4000 - 1000 - 100 - 400 - 200
    assert equation["flexible"] == pytest.approx(2300.0)
    assert equation["coherent"] is True


def test_reserve_is_not_double_counted_against_fixed():
    with_reserve = ledger_engine._plan_equation({
        "income_target": 3000.0, "fixed_obligations": 1000.0,
        "savings_target": 0.0, "safety_buffer": 0.0,
        "nonmonthly_reserves": 250.0,
    })
    without_reserve = ledger_engine._plan_equation({
        "income_target": 3000.0, "fixed_obligations": 1000.0,
        "savings_target": 0.0, "safety_buffer": 0.0,
        "nonmonthly_reserves": 0.0,
    })

    assert without_reserve["flexible"] - with_reserve["flexible"] == pytest.approx(250.0)


def test_an_impossible_plan_explains_itself_instead_of_going_silent():
    equation = ledger_engine._plan_equation({
        "income_target": 1000.0,
        "fixed_obligations": 700.0,
        "savings_target": 300.0,
        "safety_buffer": 100.0,
        "nonmonthly_reserves": 200.0,
    })

    assert equation["coherent"] is False
    explanation = equation["explanation"]
    # Names the shortfall, the reserve, and a lever the user can actually move.
    assert "300.00" in explanation
    assert "nonmonthly reserves" in explanation
    assert "savings" in explanation or "buffer" in explanation


def test_a_negative_plan_cannot_be_saved(engine_env):
    _seed_income_and_quarterly_bill(engine_env)

    code, response = _request({
        "action": "save_plan",
        "params": {
            "month": "2026-07", "mode": "normal",
            "income_target": 1000.0, "fixed_obligations": 900.0,
            "flexible_allowance": 500.0, "savings_target": 400.0,
            "safety_buffer": 200.0,
        },
    })

    assert code != 0 or response.get("error")
    message = json.dumps(response)
    assert "exceed" in message or "more than it has" in message


def test_preview_derives_the_reserve_without_being_told(engine_env):
    """The client never sends the reserve, so a preview cannot show more
    flexible room than a save would accept."""
    _seed_income_and_quarterly_bill(engine_env)

    code, response = _request({
        "action": "preview_plan",
        "params": {
            "mode": "normal", "income_target": 4000.0,
            "fixed_obligations": 1000.0, "savings_target": 400.0,
            "safety_buffer": 200.0, "apply_preset": False,
        },
    })

    assert code == 0
    equation = response["data"]["equation"]
    assert "nonmonthly_reserves" in equation
    assert equation["nonmonthly_reserves"] >= 0.0
    assert response["data"]["values"]["nonmonthly_reserves"] == (
        equation["nonmonthly_reserves"]
    )
    # Flexible always equals the full equation, reserve included.
    assert equation["flexible"] == pytest.approx(max(0.0, round(
        equation["income"] - equation["fixed"]
        - equation["nonmonthly_reserves"] - equation["savings"]
        - equation["buffer"], 2,
    )))


def test_plan_payload_and_outlook_carry_the_reserve(engine_env):
    _seed_income_and_quarterly_bill(engine_env)

    code, response = _request({"action": "plan_summary", "params": {}})

    assert code == 0
    data = response["data"]
    equation = data["equation"]
    reserve = data["nonmonthly_monthly_reserve"]

    # The equation reads the same reserve the commitments panel displays.
    assert equation["nonmonthly_reserves"] == pytest.approx(reserve)
    assert equation["flexible"] == pytest.approx(max(0.0, round(
        equation["income"] - equation["fixed"] - equation["nonmonthly_reserves"]
        - equation["savings"] - equation["buffer"], 2,
    )))
    # And the three-month outlook shows it as its own line.
    for row in data["outlook"]:
        assert row["nonmonthly_reserves"] == pytest.approx(
            equation["nonmonthly_reserves"]
        )


def test_saved_plan_survives_reload_with_the_reserve_applied(engine_env):
    _seed_income_and_quarterly_bill(engine_env)

    save_code, save_response = _request({
        "action": "save_plan",
        "params": {
            "month": "2026-07", "mode": "normal",
            "income_target": 4000.0, "fixed_obligations": 800.0,
            "flexible_allowance": 2000.0, "savings_target": 400.0,
            "safety_buffer": 200.0,
            "fixed_override_reason": "Invented figure for this test.",
        },
    })
    assert save_code == 0, save_response

    reload_code, reload_response = _request({"action": "plan_summary", "params": {}})
    assert reload_code == 0
    equation = reload_response["data"]["equation"]

    assert equation["flexible"] == pytest.approx(max(0.0, round(
        equation["income"] - equation["fixed"] - equation["nonmonthly_reserves"]
        - equation["savings"] - equation["buffer"], 2,
    )))


# ── Nonmonthly bills must not also be counted as regular fixed costs ──


def _bills(items):
    return {"items": items}


def test_regular_fixed_excludes_nonmonthly_commitments():
    """The defect: a quarterly invoice counted at full value in fixed costs
    and again through the reserve, so one $100/month bill removed $400."""
    from utils.planner import regular_monthly_fixed_total

    total = regular_monthly_fixed_total(_bills([
        {"group": "fixed_commitments", "est_amount": 1000.0,
         "monthly_setaside": 1000.0, "included_in_forecast": True},
        {"group": "nonmonthly_commitments", "est_amount": 300.0,
         "monthly_setaside": 100.0, "included_in_forecast": True},
    ]))

    assert total == pytest.approx(1000.0), (
        "the quarterly invoice must not appear in regular fixed costs"
    )


def test_regular_fixed_is_zero_for_a_quarterly_only_ledger():
    from utils.planner import regular_monthly_fixed_total

    total = regular_monthly_fixed_total(_bills([
        {"group": "nonmonthly_commitments", "est_amount": 300.0,
         "monthly_setaside": 100.0, "included_in_forecast": True},
    ]))

    assert total == pytest.approx(0.0)


def test_regular_fixed_skips_items_outside_the_forecast():
    from utils.planner import regular_monthly_fixed_total

    total = regular_monthly_fixed_total(_bills([
        {"group": "fixed_commitments", "est_amount": 500.0,
         "monthly_setaside": 500.0, "included_in_forecast": True},
        {"group": "recurring_variable_merchants", "est_amount": 900.0,
         "monthly_setaside": 900.0, "included_in_forecast": False},
    ]))

    assert total == pytest.approx(500.0)


def _seed_monthly_and_quarterly(env):
    """$1,000/month housing plus a $300 quarterly insurance premium."""
    from utils import database as db
    from tests.conftest import add_tx

    ledger_engine._bind_database()
    conn = db.get_connection()
    anchor = date(2026, 7, 15)
    for month_back in range(12):
        stamp = anchor - timedelta(days=30 * month_back)
        add_tx(conn, day=stamp.isoformat(), desc="INVENTED EMPLOYER PAYROLL",
               amount=4000.0, direction="credit", category="Payroll Income")
        add_tx(conn, day=stamp.isoformat(), desc="INVENTED LANDLORD",
               amount=1000.0, direction="debit",
               category="Housing / Mortgage")
    for quarter in range(4):
        stamp = anchor - timedelta(days=90 * quarter)
        add_tx(conn, day=stamp.isoformat(), desc="INVENTED INSURANCE CO",
               amount=300.0, direction="debit", category="Insurance")
    conn.commit()
    conn.close()


def test_monthly_and_quarterly_are_each_counted_once(engine_env):
    """$4,000 income, $1,000 monthly housing, $300 quarterly at $100/mo,
    $600 savings, $200 buffer leaves $2,100."""
    _seed_monthly_and_quarterly(engine_env)

    code, response = _request({"action": "plan_summary", "params": {}})
    assert code == 0, response
    data = response["data"]
    equation = data["equation"]

    assert equation["fixed"] == pytest.approx(1000.0), (
        "housing only; the quarterly premium belongs in the reserve"
    )
    assert equation["nonmonthly_reserves"] == pytest.approx(100.0)
    assert data["fixed_obligations_suggestion"] == pytest.approx(1000.0)
    # Nothing is removed twice.
    assert equation["fixed"] + equation["nonmonthly_reserves"] == pytest.approx(1100.0)


def test_saved_plan_agreement_uses_the_same_fixed_basis(engine_env):
    """Plan drift must compare against the displayed fixed costs, so a
    nonmonthly bill cannot make a correct plan look like an override."""
    _seed_monthly_and_quarterly(engine_env)

    code, response = _request({
        "action": "save_plan",
        "params": {
            "month": "2026-07", "mode": "normal",
            "income_target": 4000.0, "fixed_obligations": 1000.0,
            "flexible_allowance": 2100.0, "savings_target": 600.0,
            "safety_buffer": 200.0,
        },
    })
    assert code == 0, response

    reload_code, reload_response = _request({"action": "plan_summary", "params": {}})
    assert reload_code == 0
    data = reload_response["data"]
    equation = data["equation"]

    assert equation["fixed"] == pytest.approx(1000.0)
    assert equation["nonmonthly_reserves"] == pytest.approx(100.0)
    assert equation["flexible"] == pytest.approx(2100.0)
    agreement = data["readiness"]["plan_agreement"]
    assert agreement["detected"] == pytest.approx(1000.0), (
        "drift must be measured against regular fixed costs, not invoices"
    )
    assert agreement["agreed"] is True, (
        "saving the displayed fixed cost should not read as a drift override"
    )

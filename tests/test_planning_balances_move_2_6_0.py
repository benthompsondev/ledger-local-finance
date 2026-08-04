"""Moving a control between screens must not move a number.

All figures are invented.

Current balances and the spendable-account choice used to live on Insights,
inside the block that also drew a second net worth. 2.6.0 moves those two
controls into Settings under "Planning balances" and repoints every link that
sent people to the old anchors.

The whole risk of a move like this is that something quietly reads a different
value afterwards. Safe to Spend is the one number every screen must agree on,
so it is pinned here against the same invented data before and after.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from desktop.engine import ledger_engine
from tests.conftest import add_tx, seed_balances, seed_month

REPO = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = REPO / "desktop" / "src" / "SettingsView.tsx"

NEW_ANCHORS = ("settings#planning-balances", "settings#spendable-accounts")
OLD_ANCHORS = ("insights#account-balances", "insights#spending-accounts")


def _request(action: str, **params):
    import io
    out = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params}) + "\n"), out,
    )
    return code, json.loads(out.getvalue())


def _ok(action: str, **params):
    code, response = _request(action, **params)
    assert code == 0, response
    return response["data"]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    ledger_engine._bind_database()
    return tmp_path


# ── the controls still work, from their new home ─────────────────────────

def test_a_balance_can_still_be_recorded(engine):
    _ok("create_account", name="Invented Chequing", type="chequing")
    account = _ok("list_accounts")["accounts"][0]

    after = _ok("add_account_balance", account_ref=account["id"],
                balance=5400.0, as_of_date="2026-06-30", note="June statement")

    assert [b["balance"] for b in after["balances"]] == [5400.0]
    assert _ok("get_planning_balances")["balances"][0]["balance"] == 5400.0


def test_a_balance_can_be_updated_without_stacking_up(engine):
    _ok("create_account", name="Invented Chequing", type="chequing")
    account = _ok("list_accounts")["accounts"][0]
    _ok("add_account_balance", account_ref=account["id"], balance=5400.0,
        as_of_date="2026-06-30", note="")

    after = _ok("add_account_balance", account_ref=account["id"],
                balance=5900.0, as_of_date="2026-07-31", note="")

    assert len(after["balances"]) == 1, "the latest snapshot is the one shown"
    assert after["balances"][0]["balance"] == 5900.0


def test_spendable_choices_can_still_be_changed(engine):
    _ok("create_account", name="Invented Savings", type="savings")
    account = _ok("list_accounts")["accounts"][0]

    turned_on = _ok("set_account_spending_availability",
                    account_id=account["id"], available=True)
    assert turned_on["accounts"][0]["available_for_spending"] is True

    turned_off = _ok("set_account_spending_availability",
                     account_id=account["id"], available=False)
    assert turned_off["accounts"][0]["available_for_spending"] is False
    assert _ok("get_planning_balances")["accounts"][0][
        "available_for_spending"] is False


def test_a_debt_account_still_cannot_be_made_spendable(engine):
    _ok("create_account", name="Invented Card", type="credit_card")
    account = _ok("list_accounts")["accounts"][0]

    code, response = _request("set_account_spending_availability",
                              account_id=account["id"], available=True)

    assert code != 0 or response.get("error")
    assert "cannot be available" in json.dumps(response)


# ── Safe to Spend is untouched by the move ───────────────────────────────

def _cash_checked_ledger(conn) -> None:
    """The state in which Safe to Spend reconciles against real cash.

    It needs current-month activity — a finished month correctly reports
    nothing — a balance for every account kind the transactions touch, and a
    saved plan. Without all three it falls back to the flow estimate, which
    would hide any change in how balances are read.
    """
    from utils.database import create_account, insert_account_balance
    from tests.conftest import save_plan

    for month in ("2026-05", "2026-06"):
        seed_month(conn, month)
    add_tx(conn, day="2026-07-01", desc="INVENTED LANDLORD RENT",
           amount=1800.0, direction="debit", category="Housing / Mortgage")
    add_tx(conn, day="2026-07-05", desc="INVENTED EMPLOYER PAYROLL",
           amount=2500.0, direction="credit", category="Payroll Income")
    for day in (3, 8, 12):
        add_tx(conn, day=f"2026-07-{day:02d}", desc=f"INVENTED GROCER {day}",
               amount=84.20, direction="debit", category="Groceries")
    for name, kind, balance in (
        ("Invented Chequing", "chequing", 6000.0),
        ("Invented Card", "credit_card", 500.0),
    ):
        account_id = create_account({
            "name": name, "type": kind, "institution": "",
            "currency": "CAD", "opening_balance": 0.0,
        }, conn=conn)
        insert_account_balance({
            "account_ref": account_id, "account_name": name,
            "account_kind": kind, "balance": balance, "currency": "CAD",
            "as_of_date": "2026-07-12", "notes": "",
        }, conn=conn)
    save_plan(conn, "2026-07", income=5000.0, spending=3400.0, savings=800.0)
    conn.commit()


def test_safe_to_spend_is_the_same_number_after_the_move(ledger_db):
    """Pinned against explicit invented data, not against a previous run.

    Any drift in what Safe to Spend reads, or in which balances it counts,
    shows up here as a changed figure.
    """
    from utils.checkin import safe_to_spend_summary

    _cash_checked_ledger(ledger_db)

    summary = safe_to_spend_summary(conn=ledger_db, today="2026-07-15")

    assert summary["available"] is True, summary.get("reason")
    assert summary["reserved"]["balance_checked"] is True, (
        "the balance path stopped being used"
    )
    assert summary["amount"] == pytest.approx(3147.4, abs=0.01), (
        "Safe to Spend changed when the balance controls moved screens"
    )


def test_the_spendable_toggle_still_decides_what_counts_as_cash(ledger_db):
    """Wherever the checkbox lives, it moves money between two buckets.

    It does not raise Safe to Spend, and it should not: the figure is capped
    by the plan, so a bigger savings balance is more cash available, not more
    money the plan says may be spent. What the toggle must still control is
    which balances count as reachable at all.
    """
    from utils.checkin import safe_to_spend_summary
    from utils.database import (
        create_account, insert_account_balance, update_account,
    )

    _cash_checked_ledger(ledger_db)
    account_id = create_account({
        "name": "Invented Savings", "type": "savings",
        "institution": "", "currency": "CAD", "opening_balance": 0.0,
    }, conn=ledger_db)
    insert_account_balance({
        "account_ref": account_id, "account_name": "Invented Savings",
        "account_kind": "savings", "balance": 9000.0, "currency": "CAD",
        "as_of_date": "2026-07-12", "notes": "",
    }, conn=ledger_db)
    ledger_db.commit()

    protected = safe_to_spend_summary(conn=ledger_db, today="2026-07-15")
    update_account(account_id, {"available_for_spending": True}, conn=ledger_db)
    ledger_db.commit()
    included = safe_to_spend_summary(conn=ledger_db, today="2026-07-15")

    assert protected["reserved"]["excluded_balance"] == pytest.approx(9000.0)
    assert included["reserved"]["excluded_balance"] == pytest.approx(0.0)
    assert (
        included["reserved"]["available_balance"]
        - protected["reserved"]["available_balance"]
    ) == pytest.approx(9000.0), "the toggle stopped moving the balance"
    # And the plan-capped figure is unmoved by it, which is the point.
    assert included["amount"] == pytest.approx(protected["amount"])


def test_home_and_plan_still_agree_after_the_move(engine):
    """The rule this repo has: one canonical Safe to Spend."""
    from utils import database as db
    from tests.conftest import seed_month as seed

    conn = db.get_connection()
    seed(conn, "2026-06")
    conn.commit()
    conn.close()

    home = _ok("home_summary")
    plan = _ok("plan_summary")

    assert (home.get("safe_to_spend") or {}).get("amount") == (
        (plan.get("safe_to_spend") or {}).get("amount")
    )


# ── every link points at the new controls ────────────────────────────────

@pytest.mark.parametrize("relative", [
    "desktop/engine/ledger_engine.py",
    "utils/checkin.py",
    "desktop/src/HomeView.tsx",
    "desktop/src/PlanView.tsx",
])
def test_no_link_still_targets_the_removed_insights_anchors(relative: str):
    source = (REPO / relative).read_text(encoding="utf-8")

    for old in OLD_ANCHORS:
        assert old not in source, f"{relative} still links to {old}"


def test_the_engine_sends_people_to_the_new_settings_anchors():
    source = (REPO / "desktop" / "engine" / "ledger_engine.py").read_text(
        encoding="utf-8",
    )

    for anchor in NEW_ANCHORS:
        assert anchor in source, f"nothing links to {anchor}"


def test_settings_actually_has_those_anchors():
    """A link to an id that does not exist scrolls nowhere."""
    source = SETTINGS.read_text(encoding="utf-8")

    for anchor in NEW_ANCHORS:
        element_id = anchor.split("#", 1)[1]
        assert f'id="{element_id}"' in source, (
            f"Settings has no element with id {element_id}"
        )


def test_the_anchor_effect_focuses_a_control_not_just_the_heading():
    """Home and Plan send people here to change one thing."""
    source = SETTINGS.read_text(encoding="utf-8")

    assert "scrollIntoView" in source
    assert 'querySelector<HTMLElement>("select,input,button")' in source
    assert ".focus(" in source


def test_the_readiness_task_points_at_settings(ledger_db):
    """The setup link a user actually clicks."""
    from utils.checkin import safe_to_spend_summary

    seed_month(ledger_db, "2026-06")
    ledger_db.commit()

    summary = safe_to_spend_summary(conn=ledger_db, today="2026-07-01")
    blob = json.dumps(summary)

    assert "insights#account-balances" not in blob
    if "planning-balances" in blob:
        assert "settings#planning-balances" in blob

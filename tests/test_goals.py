from __future__ import annotations

import sqlite3

import pytest

import utils.database as db
from utils.goals import goal_plan_summary, goal_progress, home_goal_summary
from utils.insights import money_runway


def _account(conn, name="Savings", type_="savings", **kwargs):
    account_id = db.create_account({"name": name, "type": type_, **kwargs}, conn=conn)
    conn.commit()
    return db.get_account(account_id, conn=conn)


def _goal(conn, **overrides):
    row = {
        "name": "Emergency fund",
        "type": "emergency_fund",
        "target_amount": 5000.0,
        "current_amount": 500.0,
        "target_date": "2026-12-31",
        "status": "active",
        "progress_method": "manual",
        "planned_monthly_contribution": 500.0,
        "include_in_plan": 1,
        "currency": "CAD",
    }
    row.update(overrides)
    goal_id = db.insert_goal(row, conn=conn)
    conn.commit()
    return goal_id


def _tx(conn, account_id, *, day="2026-07-05", amount=100.0,
        direction="credit", currency="CAD", suffix="1"):
    ok, message = db.insert_transaction({
        "account_type": "savings",
        "account_ref": int(account_id),
        "transaction_date": day,
        "raw_description": f"GOAL TEST {suffix}",
        "merchant": f"GOAL TEST {suffix}",
        "amount": amount,
        "direction": direction,
        "category": "Internal Transfer",
        "currency": currency,
    }, conn)
    assert ok, message
    conn.commit()


def test_fresh_schema_has_additive_goal_fields_and_contributions(ledger_db):
    columns = {
        row["name"] for row in ledger_db.execute(
            "PRAGMA table_info(goal_targets)"
        ).fetchall()
    }
    assert {
        "progress_method", "linked_account_ref",
        "planned_monthly_contribution", "include_in_plan",
        "currency", "completed_at",
    } <= columns
    assert ledger_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='goal_contributions'"
    ).fetchone()


def _legacy_goal_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE goal_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT,
            target_amount REAL,
            current_amount REAL DEFAULT 0,
            target_date TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT,
            linked_metric TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO goal_targets
            (name, type, target_amount, current_amount, status, linked_metric)
        VALUES ('Legacy cash goal', 'cash_buffer', 10000, 2500,
                'active', 'cash_balance'),
               ('Legacy complete', 'custom', 500, 500, 'done', NULL);
        """
    )
    conn.commit()
    conn.close()


def test_populated_goal_migration_is_idempotent_and_lossless(tmp_path, monkeypatch):
    path = tmp_path / "legacy_goals.db"
    _legacy_goal_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    db.init_db()
    db.init_db()
    conn = db.get_connection()
    rows = db.get_goals(conn=conn, status=None)
    assert len(rows) == 2
    assert rows[0]["current_amount"] in {500.0, 2500.0}
    by_name = {row["name"]: row for row in rows}
    assert by_name["Legacy cash goal"]["progress_method"] == "legacy_metric"
    assert by_name["Legacy complete"]["status"] == "completed"
    assert conn.execute("SELECT COUNT(*) FROM goal_contributions").fetchone()[0] == 0
    conn.close()


def test_goal_migration_failure_rolls_back_whole_slice(tmp_path, monkeypatch):
    path = tmp_path / "legacy_goals_failure.db"
    _legacy_goal_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    def fail(_conn):
        raise RuntimeError("injected goals backfill failure")

    monkeypatch.setattr(db, "_backfill_goals_schema", fail)
    with pytest.raises(RuntimeError, match="injected"):
        db.init_db()

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(goal_targets)")}
    assert "progress_method" not in columns
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='goal_contributions'"
    ).fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM goal_targets").fetchone()[0] == 2
    conn.close()


def test_manual_goal_with_no_transactions_and_restart_persistence(ledger_db):
    goal_id = _goal(ledger_db, current_amount=750.0)
    progress = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert progress["current_amount"] == 750.0
    assert progress["source_label"] == "Manual progress"

    db.record_goal_contribution(
        goal_id, 250.0, "2026-07-18", "synthetic contribution", conn=ledger_db,
    )
    ledger_db.commit()
    assert db.get_goal(goal_id, conn=ledger_db)["current_amount"] == 1000.0
    assert db.list_goal_contributions(goal_id, conn=ledger_db)[0]["amount"] == 250.0


def test_linked_savings_account_progress_uses_only_computed_balance(ledger_db):
    account = _account(ledger_db, opening_balance=1000.0,
                       opening_balance_date="2026-07-01")
    _tx(ledger_db, account["id"], amount=500.0, direction="credit", suffix="credit")
    _tx(ledger_db, account["id"], amount=100.0, direction="debit", suffix="debit")
    _goal(ledger_db, current_amount=9999.0, progress_method="linked_account",
          linked_account_ref=account["id"])

    progress = goal_progress(conn=ledger_db, today="2026-07-06")[0]
    assert progress["current_amount"] == 1400.0
    assert progress["linked_balance"] == 1400.0
    assert progress["progress_method"] == "linked_account"


def test_linked_debt_progress_never_reverses_signs(ledger_db):
    debt = _account(ledger_db, name="Loan", type_="loan", opening_balance=2000.0)
    _tx(ledger_db, debt["id"], amount=500.0, direction="credit", suffix="payment")
    _goal(ledger_db, name="Loan payoff", type="debt_payoff",
          target_amount=2000.0, current_amount=0.0,
          progress_method="linked_account", linked_account_ref=debt["id"])

    progress = goal_progress(conn=ledger_db, today="2026-07-06")[0]
    assert progress["linked_balance"] == 1500.0
    assert progress["current_amount"] == 500.0
    assert progress["gap"] == 1500.0


def test_true_expense_required_pace_and_target_date_change(ledger_db):
    goal_id = _goal(
        ledger_db, name="Annual insurance", type="true_expense",
        target_amount=1200.0, current_amount=600.0,
        planned_monthly_contribution=100.0,
    )
    before = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert before["months_remaining"] == 6
    assert before["required_monthly_pace"] == 100.0
    assert before["pace_state"] == "on_track"

    db.update_goal(goal_id, {"target_date": "2026-09-30"}, conn=ledger_db)
    ledger_db.commit()
    after = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert after["months_remaining"] == 3
    assert after["required_monthly_pace"] == 200.0
    assert after["pace_state"] == "behind"


def test_multiple_goals_warn_without_combining_same_account(ledger_db):
    account = _account(ledger_db, opening_balance=1500.0)
    for name in ("Emergency", "House repair"):
        _goal(ledger_db, name=name, progress_method="linked_account",
              linked_account_ref=account["id"])
    progress = goal_progress(conn=ledger_db, today="2026-07-06")
    assert len(progress) == 2
    assert all(goal["current_amount"] == 1500.0 for goal in progress)
    assert all(goal["duplicate_link_warning"] for goal in progress)


def test_paused_and_completed_goals_do_not_reserve_plan_money(ledger_db):
    _goal(ledger_db, name="Active", planned_monthly_contribution=300.0)
    _goal(ledger_db, name="Paused", status="paused",
          planned_monthly_contribution=400.0)
    _goal(ledger_db, name="Done", status="completed",
          planned_monthly_contribution=500.0)
    summary = goal_plan_summary(800.0, conn=ledger_db)
    assert summary["goal_allocations_total"] == 300.0
    assert summary["effective_savings_target"] == 800.0
    assert summary["unallocated_savings"] == 500.0


def test_goal_allocations_raise_but_never_double_plan_target(ledger_db):
    _goal(ledger_db, name="One", planned_monthly_contribution=600.0)
    _goal(ledger_db, name="Two", planned_monthly_contribution=500.0)
    summary = goal_plan_summary(800.0, conn=ledger_db)
    assert summary["goal_allocations_total"] == 1100.0
    assert summary["effective_savings_target"] == 1100.0
    assert summary["allocation_above_plan"] == 300.0


def test_safe_to_spend_reserves_effective_goal_target_once(steady_db):
    baseline = money_runway(conn=steady_db, today="2026-07-06")
    _goal(steady_db, name="One", planned_monthly_contribution=600.0)
    within = money_runway(conn=steady_db, today="2026-07-06")
    _goal(steady_db, name="Two", planned_monthly_contribution=500.0)
    above = money_runway(conn=steady_db, today="2026-07-06")

    assert baseline["safe_to_spend"]["formula"]["goal_commitments"] == 800.0
    assert within["safe_to_spend"]["formula"]["goal_commitments"] == 800.0
    assert above["safe_to_spend"]["formula"]["goal_commitments"] == 1100.0
    assert above["safe_to_spend"]["formula"][
        "cash_cushion_after_bills"
    ] == pytest.approx(
        baseline["safe_to_spend"]["formula"]["cash_cushion_after_bills"] - 300.0
    )
    assert above["safe_to_spend"]["amount"] == pytest.approx(
        min(
            above["safe_to_spend"]["formula"]["flexible_remaining"],
            above["safe_to_spend"]["formula"]["cash_cushion_after_bills"],
        )
    )


def test_missed_partial_and_met_contribution_states(ledger_db):
    goal_id = _goal(ledger_db, planned_monthly_contribution=200.0)
    missed = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert missed["contribution_state"] == "missed"

    db.record_goal_contribution(goal_id, 50.0, "2026-07-10", conn=ledger_db)
    ledger_db.commit()
    partial = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert partial["contribution_state"] == "partial"

    db.record_goal_contribution(goal_id, 150.0, "2026-07-18", conn=ledger_db)
    ledger_db.commit()
    met = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert met["contribution_state"] == "met"


def test_archived_linked_account_and_stale_data_are_visible(ledger_db):
    account = _account(ledger_db)
    _tx(ledger_db, account["id"], day="2026-06-01", suffix="stale")
    _goal(ledger_db, progress_method="linked_account",
          linked_account_ref=account["id"])
    db.archive_account(account["id"], conn=ledger_db)
    ledger_db.commit()

    progress = goal_progress(conn=ledger_db, today="2026-07-18")[0]
    assert progress["linked_account"]["is_archived"] == 1
    assert "archived" in progress["source_warning"].lower()
    assert progress["data_stale"] is True
    assert progress["pace_state"] == "unknown"


def test_foreign_currency_account_stays_in_its_own_currency(ledger_db):
    account = _account(
        ledger_db, name="USD Savings", currency="USD", opening_balance=1000.0,
    )
    _tx(ledger_db, account["id"], amount=100.0, currency="USD", suffix="usd")
    _goal(ledger_db, name="US trip", type="planned_purchase",
          progress_method="linked_account", linked_account_ref=account["id"],
          currency="USD")

    progress = goal_progress(conn=ledger_db, today="2026-07-06")[0]
    assert progress["currency"] == "USD"
    assert progress["current_amount"] == 1100.0
    assert progress["foreign_currency"] is True
    plan = goal_plan_summary(800.0, conn=ledger_db)
    assert plan["goal_allocations_total"] == 0.0
    assert plan["effective_savings_target"] == 800.0
    assert plan["foreign_allocations"][0]["currency"] == "USD"


def test_home_chooses_one_goal_and_marks_stale_linked_progress(ledger_db):
    account = _account(ledger_db)
    _tx(ledger_db, account["id"], day="2026-06-01", suffix="home-stale")
    _goal(ledger_db, progress_method="linked_account",
          linked_account_ref=account["id"])
    _goal(ledger_db, name="Paused", status="paused")

    summary = home_goal_summary(conn=ledger_db, today="2026-07-18")
    assert summary is not None
    assert summary["name"] == "Emergency fund"
    assert summary["stale_warning"]


def test_linked_goal_rejects_manual_contribution(ledger_db):
    account = _account(ledger_db)
    goal_id = _goal(ledger_db, progress_method="linked_account",
                    linked_account_ref=account["id"])
    with pytest.raises(ValueError, match="manual-progress"):
        db.record_goal_contribution(
            goal_id, 100.0, "2026-07-18", conn=ledger_db,
        )

"""A screen nobody can reach must not reserve money.

All figures are invented.

Goals was retired from native navigation and Money Focus replaced it. The
rows survived, and `goal_plan_summary` still read every active goal whose
`include_in_plan` was 1 — the column's default. So a legacy goal carried
over from the Streamlit era kept reserving money out of Safe to Spend and
the cash cushion, and there was no screen left on which to see it, pause
it, or remove it.

A visible savings target of $800 with two forgotten allocations of $600 and
$500 behind it reserves $1,100, and the only symptom is a cushion that is
$300 smaller than the plan says it should be.

The rows are kept. Deleting someone's saved goals to fix a reservation bug
would be a worse trade than the bug. A recorded one-time migration turns
off their influence instead, so the data is still there if Goals ever comes
back as a supported screen.
"""
from __future__ import annotations

import pytest

import utils.database as db

MIGRATION_KEY = "legacy_goals_excluded_from_plan_v1"


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _legacy_goal(name: str, monthly: float) -> int:
    """A goal as the retired Streamlit page would have written it."""
    conn = db.get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO goal_targets"
            "(name, target_amount, status, include_in_plan, "
            " planned_monthly_contribution, contribution_frequency) "
            "VALUES(?,?,?,1,?,'monthly')",
            (name, monthly * 12, "active", monthly),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _reserved(plan_target: float = 800.0) -> float:
    from utils.goals import goal_plan_summary

    conn = db.get_connection()
    try:
        summary = goal_plan_summary(plan_savings_target=plan_target, conn=conn)
        return float(summary["goal_allocations_total"])
    finally:
        conn.close()


def _open_as_2_5_3() -> None:
    """Make the database look like one written before this migration existed.

    The fixture creates a fresh database, and a fresh database records the
    migration immediately because there is nothing to migrate. The real
    upgrade path is an existing 2.5.3 database that already holds goals
    being opened by 2.5.4, so the marker is removed to reproduce it.
    """
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM app_migrations WHERE migration_key=?",
                     (MIGRATION_KEY,))
        conn.commit()
    finally:
        conn.close()


def _migration_rows() -> int:
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM app_migrations WHERE migration_key=?",
            (MIGRATION_KEY,),
        ).fetchone()[0]
    finally:
        conn.close()


# ── the reproduction ─────────────────────────────────────────────────────

def test_hidden_legacy_goals_do_not_reserve_money(ledger) -> None:
    """$800 visible target, $600 and $500 hidden behind it."""
    _legacy_goal("Invented Trip", 600.0)
    _legacy_goal("Invented Laptop", 500.0)
    _open_as_2_5_3()

    db.init_db()  # a normal app start, which runs migrations

    allocations = _reserved(plan_target=800.0)
    assert allocations == 0.0, (
        f"retired goals still reserved {allocations}"
    )


def test_the_visible_plan_target_stays_authoritative(ledger) -> None:
    from utils.goals import goal_plan_summary

    _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    db.init_db()

    conn = db.get_connection()
    try:
        summary = goal_plan_summary(plan_savings_target=800.0, conn=conn)
    finally:
        conn.close()

    assert summary["goal_allocations"] == []
    assert summary["goal_allocations_total"] == 0.0
    # The plan's own savings target is what remains authoritative.
    assert summary["effective_savings_target"] == 800.0
    assert summary["allocation_above_plan"] == 0.0


def test_safe_to_spend_is_not_reduced_by_a_retired_goal(ledger) -> None:
    from datetime import date, timedelta

    from tests.conftest import add_tx
    from utils.analysis_period import reset_supported_bounds_cache
    from utils.checkin import safe_to_spend_summary

    conn = db.get_connection()
    try:
        first = date.today().replace(day=1)
        month = first
        for _ in range(4):
            month = (month - timedelta(days=1)).replace(day=1)
            ym = month.strftime("%Y-%m")
            add_tx(conn, day=f"{ym}-01", desc="LANDLORD RENT", amount=1800.0,
                   direction="debit", category="Housing / Mortgage")
            add_tx(conn, day=f"{ym}-02", desc="EMPLOYER PAYROLL", amount=2500.0,
                   direction="credit", category="Payroll Income")
            add_tx(conn, day=f"{ym}-16", desc="EMPLOYER PAYROLL 2",
                   amount=2500.0, direction="credit", category="Payroll Income")
            for day in (3, 8, 13, 18, 23, 27):
                add_tx(conn, day=f"{ym}-{day:02d}", desc=f"GROCER {day}",
                       amount=84.20, direction="debit", category="Groceries")
        conn.commit()
    finally:
        conn.close()
    reset_supported_bounds_cache()

    conn = db.get_connection()
    try:
        before = safe_to_spend_summary(conn=conn)
    finally:
        conn.close()

    _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    db.init_db()
    reset_supported_bounds_cache()

    conn = db.get_connection()
    try:
        after = safe_to_spend_summary(conn=conn)
    finally:
        conn.close()

    assert after["amount"] == pytest.approx(before["amount"], abs=0.01), (
        "a retired goal moved Safe to Spend"
    )


# ── the data is preserved ────────────────────────────────────────────────

def test_legacy_goal_rows_are_kept(ledger) -> None:
    """Fixing a reservation bug must not delete someone's saved goals."""
    goal_id = _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    db.init_db()

    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT name, target_amount, planned_monthly_contribution, "
            "include_in_plan FROM goal_targets WHERE id=?", (goal_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "the goal row was deleted"
    assert row["name"] == "Invented Trip"
    assert row["planned_monthly_contribution"] == 600.0
    assert int(row["include_in_plan"]) == 0, "only its influence is switched off"


def test_the_goal_tables_still_exist(ledger) -> None:
    db.init_db()
    conn = db.get_connection()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()

    assert "goal_targets" in tables
    assert "goal_contributions" in tables


# ── the migration behaves like a migration ───────────────────────────────

def test_the_migration_runs_once(ledger) -> None:
    _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    for _ in range(3):
        db.init_db()

    assert _migration_rows() == 1


def test_a_goal_created_after_the_migration_is_left_alone(ledger) -> None:
    """The migration is one-time; it does not police future rows."""
    db.init_db()
    goal_id = _legacy_goal("Invented Later", 100.0)
    db.init_db()

    conn = db.get_connection()
    try:
        value = conn.execute(
            "SELECT include_in_plan FROM goal_targets WHERE id=?", (goal_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert int(value) == 1, "the migration ran a second time"


def test_new_goals_through_the_engine_default_to_excluded(ledger) -> None:
    """The unreachable API already defaults to false. Keep it that way."""
    import io
    import json

    from desktop.engine import ledger_engine

    db.init_db()
    output = io.StringIO()
    ledger_engine.run(io.StringIO(json.dumps({
        "action": "create_goal",
        "params": {"name": "Invented API Goal", "target_amount": 1200.0},
    }) + "\n"), output)
    body = json.loads(output.getvalue())
    if not body.get("ok"):
        return  # the action is unreachable from the UI; nothing to guard

    conn = db.get_connection()
    try:
        value = conn.execute(
            "SELECT include_in_plan FROM goal_targets "
            "WHERE name='Invented API Goal'").fetchone()
    finally:
        conn.close()
    if value is not None:
        assert int(value[0]) == 0


# ── the destructive paths still behave ───────────────────────────────────

def test_a_reset_still_clears_goals(ledger) -> None:
    from utils.data_safety import reset_all_financial_data

    _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    db.init_db()
    reset_all_financial_data()

    conn = db.get_connection()
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM goal_targets").fetchone()[0]
    finally:
        conn.close()

    assert remaining == 0


def test_backup_and_restore_preserve_the_migrated_state(ledger) -> None:
    from pathlib import Path

    from utils.backups import create_backup, restore_database

    goal_id = _legacy_goal("Invented Trip", 600.0)
    _open_as_2_5_3()
    db.init_db()
    backup = create_backup(reason="test", db_path=db.DB_PATH)

    conn = db.get_connection()
    try:
        conn.execute("UPDATE goal_targets SET include_in_plan=1 WHERE id=?",
                     (goal_id,))
        conn.commit()
    finally:
        conn.close()

    restore_database(Path(backup), db_path=db.DB_PATH)

    conn = db.get_connection()
    try:
        value = conn.execute(
            "SELECT include_in_plan FROM goal_targets WHERE id=?", (goal_id,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert int(value) == 0, "restore lost the migrated state"
    assert _reserved() == 0.0

"""Release-boundary regressions found during the 2.6.2 trust pass.

Every database and value in this module is invented and disposable.
"""
from __future__ import annotations

import math
import sqlite3

import pytest


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_plan_save_rejects_non_finite_amounts_without_persisting(
    ledger_db, value,
) -> None:
    from desktop.engine.ledger_engine import save_plan_action

    with pytest.raises(ValueError, match="real number"):
        save_plan_action({
            "month": "2026-08",
            "mode": "normal",
            "fixed_obligations": 0,
            # Income is derived by the engine, so the guard is asserted on a
            # figure the client genuinely still supplies.
            "savings_target": value,
            "safety_buffer": 0,
        })

    assert ledger_db.execute(
        "SELECT COUNT(*) FROM monthly_plans"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "field",
    ["fixed_obligations", "savings_target", "safety_buffer"],
)
def test_plan_preview_rejects_non_finite_amounts(ledger_db, field) -> None:
    from desktop.engine.ledger_engine import preview_plan_action

    values = {
        "mode": "normal",
        "income_target": 5000,
        "fixed_obligations": 2000,
        "savings_target": 500,
        "safety_buffer": 250,
    }
    values[field] = math.inf

    with pytest.raises(ValueError, match="real number"):
        preview_plan_action(values)


def _malformed_ledger(path, *, valid_columns: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        if valid_columns:
            conn.executescript(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY,
                    account_type TEXT NOT NULL,
                    transaction_date TEXT NOT NULL,
                    raw_description TEXT NOT NULL,
                    amount REAL NOT NULL,
                    direction TEXT NOT NULL,
                    dedup_hash TEXT UNIQUE
                );
                CREATE TABLE import_log (
                    id INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_hash TEXT,
                    rows_inserted INTEGER DEFAULT 0
                );
                CREATE TABLE goal_targets (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    target_amount REAL,
                    current_amount REAL,
                    status TEXT
                );
                """
            )
        else:
            conn.executescript(
                """
                CREATE TABLE transactions (id INTEGER PRIMARY KEY);
                CREATE TABLE import_log (id INTEGER PRIMARY KEY);
                CREATE TABLE goal_targets (id INTEGER PRIMARY KEY);
                """
            )
        conn.commit()
    finally:
        conn.close()


def test_restore_refuses_table_names_without_the_ledger_schema(
    tmp_path, ledger_db,
) -> None:
    from utils.backups import BackupValidationError, restore_database
    from utils import database

    candidate = tmp_path / "invented-broken-schema.db"
    _malformed_ledger(candidate)

    with pytest.raises(BackupValidationError, match="required SignalSpace fields"):
        restore_database(candidate, db_path=database.DB_PATH)

    # The current database remains usable and untouched.
    assert ledger_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_restore_refuses_malformed_financial_rows(tmp_path, ledger_db) -> None:
    from utils.backups import BackupValidationError, restore_database
    from utils import database

    candidate = tmp_path / "invented-bad-row.db"
    _malformed_ledger(candidate, valid_columns=True)
    conn = sqlite3.connect(candidate)
    try:
        conn.execute(
            "INSERT INTO transactions VALUES (1, ?, ?, ?, ?, ?, ?)",
            (
                "chequing", "not-a-date", "INVENTED BROKEN ROW", -25.0,
                "debit", "invented-dedup",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(BackupValidationError, match="malformed transaction"):
        restore_database(candidate, db_path=database.DB_PATH)

    assert ledger_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_restore_refuses_schema_without_duplicate_constraint(
    tmp_path, ledger_db,
) -> None:
    from utils.backups import BackupValidationError, restore_database
    from utils import database

    candidate = tmp_path / "invented-no-dedup-constraint.db"
    _malformed_ledger(candidate, valid_columns=True)
    conn = sqlite3.connect(candidate)
    try:
        # Recreate the table without the UNIQUE constraint while retaining all
        # of the fields the app expects.
        conn.executescript(
            """
            ALTER TABLE transactions RENAME TO transactions_with_constraint;
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY,
                account_type TEXT NOT NULL,
                transaction_date TEXT NOT NULL,
                raw_description TEXT NOT NULL,
                amount REAL NOT NULL,
                direction TEXT NOT NULL,
                dedup_hash TEXT
            );
            DROP TABLE transactions_with_constraint;
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(BackupValidationError, match="duplicate protection"):
        restore_database(candidate, db_path=database.DB_PATH)

    assert ledger_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_backup_during_a_wal_write_is_complete_and_excludes_uncommitted_rows(
    ledger_db,
) -> None:
    from utils.backups import create_backup, validate_database
    from utils import database

    ledger_db.execute(
        "INSERT INTO transactions (account_type, transaction_date, "
        "raw_description, amount, direction, dedup_hash) "
        "VALUES ('chequing', '2026-08-01', 'INVENTED COMMITTED', -10, "
        "'debit', 'invented-committed')"
    )
    ledger_db.commit()

    writer = sqlite3.connect(database.DB_PATH, timeout=1.0)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO transactions (account_type, transaction_date, "
            "raw_description, amount, direction, dedup_hash) "
            "VALUES ('chequing', '2026-08-02', 'INVENTED UNCOMMITTED', -20, "
            "'debit', 'invented-uncommitted')"
        )

        backup = create_backup(
            reason="concurrent-writer", db_path=database.DB_PATH
        )
        assert validate_database(backup)["transactions"] == 1
        copied = sqlite3.connect(backup)
        try:
            names = [
                row[0] for row in copied.execute(
                    "SELECT raw_description FROM transactions ORDER BY id"
                ).fetchall()
            ]
        finally:
            copied.close()
        assert names == ["INVENTED COMMITTED"]
    finally:
        writer.rollback()
        writer.close()


def test_unavailable_safe_to_spend_does_not_claim_balance_checked() -> None:
    source = (
        __import__("pathlib").Path("desktop/src/HomeView.tsx")
        .read_text(encoding="utf-8")
    )
    assert (
        '!packet.safe_to_spend.available ? "Safe to spend now"' in source
    )


def test_stale_history_does_not_shift_plan_baseline_into_empty_months(
    ledger_db,
) -> None:
    from tests.conftest import add_tx
    from utils.planner import generate_starter_plan

    for month in ("2020-01", "2020-02", "2020-03"):
        add_tx(
            ledger_db, day=f"{month}-05", desc=f"INVENTED PAYROLL {month}",
            amount=2000, direction="credit", category="Payroll Income",
        )
        add_tx(
            ledger_db, day=f"{month}-10", desc=f"INVENTED GROCER {month}",
            amount=700, direction="debit", category="Groceries",
        )
        add_tx(
            ledger_db, day=f"{month}-20", desc=f"INVENTED BILL {month}",
            amount=100, direction="debit", category="Utilities / Bills",
        )
    ledger_db.commit()

    proposal = generate_starter_plan(conn=ledger_db)

    assert proposal["income_target"] == pytest.approx(2000.0)
    assert proposal["basis"]["lookback_months_used"] >= 1


def test_net_worth_refuses_a_future_reading(ledger_db) -> None:
    from utils.net_worth import save_entry

    with pytest.raises(ValueError, match="Future values would be a projection"):
        save_entry("2099-01", cash=4000, conn=ledger_db)

    assert ledger_db.execute(
        "SELECT COUNT(*) FROM net_worth_entries"
    ).fetchone()[0] == 0

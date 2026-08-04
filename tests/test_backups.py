"""Persistent data-root and SQLite backup/restore regression tests."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import utils.database as db
from utils.backups import (
    BackupValidationError,
    create_backup,
    list_backups,
    restore_database,
    validate_database,
)
from utils.platform_utils import (
    APP_ROOT,
    get_config_path,
    get_data_dir,
    get_exports_dir,
    get_logs_dir,
)


def _insert_marker(conn, description: str) -> None:
    ok, message = db.insert_transaction({
        "account_type": "chequing",
        "transaction_date": "2026-07-18",
        "raw_description": description,
        "merchant": description,
        "amount": 10.0,
        "direction": "debit",
        "category": "Test",
    }, conn)
    assert ok, message
    conn.commit()


def test_repo_mode_paths_remain_backward_compatible(monkeypatch):
    monkeypatch.delenv("LEDGER_DATA_DIR", raising=False)
    assert get_data_dir() == APP_ROOT / "data"
    assert get_config_path() == APP_ROOT / "config.json"
    assert get_exports_dir() == APP_ROOT / "exports"
    assert get_logs_dir() == APP_ROOT


def test_data_dir_override_is_resolved_before_database_import(tmp_path):
    data_root = tmp_path / "Ledger Data"
    env = os.environ.copy()
    env["LEDGER_DATA_DIR"] = str(data_root)
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from utils.database import real_db_path; print(real_db_path())",
        ],
        cwd=Path(__file__).parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == data_root / "finance.db"


def test_online_backup_is_valid_and_contains_committed_rows(ledger_db):
    _insert_marker(ledger_db, "BACKUP MARKER")
    path = create_backup(reason="manual-test", conn=ledger_db)
    details = validate_database(path)
    assert details["quick_check"] == "ok"
    assert details["transactions"] == 1
    assert list_backups()[0]["path"] == path
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_backup_retention_removes_only_oldest_generated_files(ledger_db):
    _insert_marker(ledger_db, "RETENTION MARKER")
    paths = [
        create_backup(reason=f"retention-{index}", conn=ledger_db, retain=2)
        for index in range(3)
    ]
    remaining = {item["path"] for item in list_backups()}
    assert len(remaining) == 2
    assert paths[0] not in remaining
    assert set(paths[1:]) == remaining


def test_restore_replaces_live_database_and_keeps_pre_restore_backup(
    ledger_db,
):
    _insert_marker(ledger_db, "KEEP IN BACKUP")
    selected = create_backup(reason="restore-source", conn=ledger_db)
    _insert_marker(ledger_db, "REMOVE AFTER RESTORE")
    ledger_db.close()

    result = restore_database(selected)
    assert result["pre_restore_backup"].is_file()
    assert result["restart_recommended"] is True
    conn = db.get_connection()
    try:
        descriptions = {
            row[0] for row in conn.execute(
                "SELECT raw_description FROM transactions"
            ).fetchall()
        }
    finally:
        conn.close()
    assert descriptions == {"KEEP IN BACKUP"}


def test_invalid_restore_fails_before_touching_live_database(
    ledger_db, tmp_path,
):
    _insert_marker(ledger_db, "LIVE DATA")
    invalid = tmp_path / "not-a-database.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(BackupValidationError):
        restore_database(invalid)
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0] == 1


def test_failed_post_swap_validation_rolls_back_original_database(
    ledger_db, monkeypatch,
):
    _insert_marker(ledger_db, "ORIGINAL")
    selected = create_backup(reason="rollback-source", conn=ledger_db)
    _insert_marker(ledger_db, "ORIGINAL NEWER")
    ledger_db.close()

    import utils.backups as backups
    original_validate = backups.validate_database
    live = Path(db.DB_PATH).resolve()
    live_validations = 0

    def fail_once_after_swap(path, **kwargs):
        nonlocal live_validations
        if Path(path).resolve() == live:
            live_validations += 1
            if live_validations == 1:
                raise BackupValidationError("injected post-swap failure")
        return original_validate(path, **kwargs)

    monkeypatch.setattr(backups, "validate_database", fail_once_after_swap)
    with pytest.raises(BackupValidationError, match="injected"):
        restore_database(selected)

    conn = sqlite3.connect(live)
    try:
        descriptions = {
            row[0] for row in conn.execute(
                "SELECT raw_description FROM transactions"
            ).fetchall()
        }
    finally:
        conn.close()
    assert descriptions == {"ORIGINAL", "ORIGINAL NEWER"}


def test_pre_migration_backup_preserves_legacy_database(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY, marker TEXT);"
        "INSERT INTO transactions(marker) VALUES ('legacy marker');"
    )
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)

    # A later migration may fail on this deliberately incomplete schema. The
    # recovery point must still exist before any attempted schema change.
    with pytest.raises(sqlite3.DatabaseError):
        db.init_db()
    backups = list((tmp_path / "backups").glob("*pre-migration.db"))
    assert len(backups) == 1
    restored = sqlite3.connect(backups[0])
    try:
        assert restored.execute(
            "SELECT marker FROM transactions"
        ).fetchone()[0] == "legacy marker"
    finally:
        restored.close()

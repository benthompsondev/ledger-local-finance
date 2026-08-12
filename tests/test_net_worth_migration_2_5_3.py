"""Carrying legacy net worth forward happens once, and stays done.

All figures are invented.

The 2.5.2 carry-forward ran on every call to `_ensure_table`, which is every
read and every write. Its only guard was `INSERT OR IGNORE` against the
canonical row existing, so "this month has no reading" was treated as "this
month was never migrated". Deleting a carried-forward reading therefore
returned True and then the very next read put it straight back.

The inserts also rode on whatever connection happened to be open. When
`list_entries()` opened its own connection to read, the migration wrote rows
and the connection closed without committing, so the work was rolled back and
repeated forever.

A migration marker has to record that the migration ran, not that its output
is currently present, because the whole point of a deletable row is that its
absence is a legitimate state.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import utils.database as db
from utils.net_worth import (
    delete_entry, list_entries, net_worth_overview, save_entry,
)

LEGACY_KEY = "net_worth_entries_from_legacy_v1"


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _legacy_estimate(month_day: str, assets: float, liabilities: float) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO net_worth_estimates"
            "(total_assets, total_liabilities, as_of_date) VALUES(?,?,?)",
            (assets, liabilities, month_day),
        )
        conn.commit()
    finally:
        conn.close()


def _legacy_snapshot(month_day: str, assets: float, liabilities: float) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO net_worth_snapshots"
            "(as_of_date, total_assets, total_liabilities, net_worth) "
            "VALUES(?,?,?,?)",
            (month_day, assets, liabilities, assets - liabilities),
        )
        conn.commit()
    finally:
        conn.close()


def _migration_rows() -> int:
    conn = db.get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM app_migrations WHERE migration_key=?",
            (LEGACY_KEY,),
        ).fetchone()[0]
    finally:
        conn.close()


# ── the defect ───────────────────────────────────────────────────────────

def test_a_deleted_carried_forward_reading_stays_deleted(ledger) -> None:
    """The reproduction. Deleting used to succeed and then undo itself."""
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)

    assert [e["month"] for e in list_entries()] == ["2026-02"]
    assert delete_entry("2026-02") is True

    assert list_entries() == [], "the migration recreated the deleted reading"
    assert net_worth_overview()["has_entries"] is False


def test_it_stays_deleted_across_fresh_connections(ledger) -> None:
    """Relaunch behaviour: every call opens its own connection."""
    _legacy_snapshot("2026-03-31", 30000.0, 5000.0)
    assert [e["month"] for e in list_entries()] == ["2026-03"]
    delete_entry("2026-03")

    for _ in range(5):
        assert list_entries() == []


def test_the_migration_is_recorded_durably(ledger) -> None:
    """The marker is the evidence, not the presence of the output row."""
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()

    assert _migration_rows() == 1

    delete_entry("2026-02")
    list_entries()
    list_entries()

    assert _migration_rows() == 1, "the migration ran again"


def test_the_carried_forward_rows_survive_a_reopen(ledger) -> None:
    """A read must commit its migration, not roll it back on close."""
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()

    conn = db.get_connection()
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM net_worth_entries"
        ).fetchone()[0]
    finally:
        conn.close()

    assert stored == 1, "the carried-forward row was never committed"


# ── both legacy sources ──────────────────────────────────────────────────

def test_both_legacy_tables_are_carried_forward(ledger) -> None:
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    _legacy_snapshot("2026-05-31", 31000.0, 6000.0)

    entries = {e["month"]: e for e in list_entries()}

    assert set(entries) == {"2026-02", "2026-05"}
    assert entries["2026-02"]["net"] == 22000.0
    assert entries["2026-05"]["net"] == 25000.0
    for entry in entries.values():
        assert "breakdown" in entry["note"]


def test_a_canonical_reading_is_never_overwritten(ledger) -> None:
    """A deliberate correction outranks anything carried forward."""
    save_entry("2026-02", cash=999.0)
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)

    entries = list_entries()

    assert len(entries) == 1
    assert entries[0]["net"] == 999.0


def test_a_correction_after_migration_is_kept(ledger) -> None:
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()

    save_entry("2026-02", cash=1234.0)

    entries = list_entries()
    assert len(entries) == 1
    assert entries[0]["net"] == 1234.0
    for _ in range(3):
        assert list_entries()[0]["net"] == 1234.0


def test_saving_another_month_does_not_rerun_the_migration(ledger) -> None:
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()
    delete_entry("2026-02")

    save_entry("2026-08", cash=500.0)

    assert [e["month"] for e in list_entries()] == ["2026-08"]


# ── it interacts correctly with the destructive paths ────────────────────

def test_a_reset_does_not_resurrect_a_legacy_reading(ledger) -> None:
    from utils.data_safety import reset_all_financial_data

    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()

    reset_all_financial_data()

    assert list_entries() == []


def test_backup_and_restore_preserve_the_migrated_state(ledger) -> None:
    from utils.backups import create_backup, restore_database

    _legacy_estimate("2026-02-14", 26000.0, 4000.0)
    list_entries()
    delete_entry("2026-02")
    assert list_entries() == []

    backup = create_backup(reason="test", db_path=db.DB_PATH)
    save_entry("2026-07", cash=42.0)
    restore_database(Path(backup), db_path=db.DB_PATH)

    assert list_entries() == [], (
        "restoring brought back a reading the backup did not contain"
    )


# ── month validation, so a stray suffix cannot edit a real month ─────────

@pytest.mark.parametrize("month", [
    "2026-01-garbage", "2026-01 ", " 2026-01", "2026-01x", "2026-01/",
    "2026-01\n", "2026-01;DROP",
])
def test_a_month_with_a_suffix_cannot_modify_that_month(ledger, month) -> None:
    save_entry("2026-01", cash=1000.0)

    with pytest.raises(ValueError):
        save_entry(month, cash=9999.0)

    assert list_entries()[0]["net"] == 1000.0, (
        f"{month!r} was truncated and overwrote January"
    )


@pytest.mark.parametrize("month", [
    "2026-01-garbage", "2026-01 ", "2026-01x",
])
def test_a_month_with_a_suffix_cannot_delete_that_month(ledger, month) -> None:
    save_entry("2026-01", cash=1000.0)

    with pytest.raises(ValueError):
        delete_entry(month)

    assert [e["month"] for e in list_entries()] == ["2026-01"]


def test_a_real_full_date_is_still_accepted_from_legacy_rows(ledger) -> None:
    """The carry-forward reads dated rows, so that path stays supported.

    It goes through the migration rather than through save_entry, which is
    why save_entry can now be strict without breaking it.
    """
    _legacy_estimate("2026-02-14", 26000.0, 4000.0)

    assert [e["month"] for e in list_entries()] == ["2026-02"]

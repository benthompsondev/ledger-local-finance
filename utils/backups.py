"""SQLite-safe backup and restore helpers for SpendShape's local database."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


DEFAULT_RETENTION = 20
_LOCK = threading.RLock()
_REQUIRED_TABLES = {"transactions", "import_log", "goal_targets"}
_REQUIRED_COLUMNS = {
    "transactions": {
        "id", "account_type", "transaction_date", "raw_description",
        "amount", "direction", "dedup_hash",
    },
    "import_log": {"id", "filename", "file_hash", "rows_inserted"},
    "goal_targets": {"id", "name", "target_amount", "current_amount", "status"},
}
_LEGACY_TABLES = _REQUIRED_TABLES | {
    "budgets", "profiles", "learned_rules", "recommendations_log"
}


class BackupValidationError(ValueError):
    """Raised when a selected file is not a usable SpendShape database."""


def _active_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path).resolve()
    from utils import database
    return Path(database.DB_PATH).resolve()


def backup_dir(db_path: Optional[Path] = None) -> Path:
    """Keep backups beside the active DB, including isolated test DBs."""
    return _active_db_path(db_path).parent / "backups"


def _safe_reason(reason: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", (reason or "manual").lower())
    return value.strip("-")[:48] or "manual"


def validate_database(path: Path, *, allow_legacy: bool = False) -> dict:
    """Validate SQLite integrity and the minimum SpendShape schema read-only."""
    candidate = Path(path).resolve()
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise BackupValidationError("The selected backup is missing or empty.")
    uri = f"file:{candidate.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise BackupValidationError(
                f"SQLite integrity check failed: {quick_check}"
            )
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        if allow_legacy and not (tables & _LEGACY_TABLES):
            raise BackupValidationError(
                "This SQLite file does not contain a recognizable SpendShape table."
            )
        if missing and not allow_legacy:
            raise BackupValidationError(
                "This is not a SpendShape backup. Missing tables: "
                + ", ".join(missing)
            )
        if not allow_legacy:
            for table, required in _REQUIRED_COLUMNS.items():
                columns = {
                    row[1] for row in conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                missing_columns = sorted(required - columns)
                if missing_columns:
                    raise BackupValidationError(
                        "This backup is missing required SpendShape fields in "
                        f"{table}: {', '.join(missing_columns)}"
                    )
            dedup_is_unique = False
            for index in conn.execute(
                "PRAGMA index_list(transactions)"
            ).fetchall():
                if not index[2]:
                    continue
                indexed = {
                    row[2] for row in conn.execute(
                        f"PRAGMA index_info({index[1]})"
                    ).fetchall()
                }
                if indexed == {"dedup_hash"}:
                    dedup_is_unique = True
                    break
            if not dedup_is_unique:
                raise BackupValidationError(
                    "This backup is missing SpendShape's transaction duplicate "
                    "protection. It was not restored."
                )
            invalid = conn.execute(
                """
                SELECT id FROM transactions
                WHERE transaction_date IS NULL
                   OR strftime('%Y-%m-%d', transaction_date) IS NULL
                   OR strftime('%Y-%m-%d', transaction_date) != transaction_date
                   OR amount IS NULL
                   OR typeof(amount) NOT IN ('integer', 'real')
                   OR NOT (amount < 1e308 AND amount > -1e308)
                   OR direction NOT IN (
                       'credit', 'debit', 'payment', 'transfer', 'cancelled'
                   )
                LIMIT 1
                """
            ).fetchone()
            if invalid is not None:
                raise BackupValidationError(
                    "This backup contains a malformed transaction and was "
                    "not restored."
                )
        tx_count = int(conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]) if "transactions" in tables else 0
        return {
            "path": candidate,
            "size_bytes": candidate.stat().st_size,
            "transactions": tx_count,
            "quick_check": quick_check,
        }
    except sqlite3.DatabaseError as exc:
        raise BackupValidationError(
            f"The selected file is not a valid SQLite database: {exc}"
        ) from exc
    finally:
        if "conn" in locals():
            conn.close()


def _copy_database(source: sqlite3.Connection, destination: Path) -> None:
    dest = sqlite3.connect(destination)
    try:
        source.backup(dest)
        dest.commit()
        # Backups should be self-contained portable files. The source may be
        # in WAL mode, so normalize the destination back to DELETE journaling
        # before it leaves this function.
        dest.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        dest.close()


def _delete_temporary_database(path: Path) -> None:
    """Remove a staged database and any WAL sidecars it created."""
    for candidate in (
        path, Path(str(path) + "-wal"), Path(str(path) + "-shm")
    ):
        if candidate.exists():
            candidate.unlink()


def create_backup(*, reason: str = "manual",
                  db_path: Optional[Path] = None,
                  conn: Optional[sqlite3.Connection] = None,
                  retain: int = DEFAULT_RETENTION,
                  allow_legacy: bool = False) -> Path:
    """Create an atomic, validated backup using SQLite's online API."""
    source_path = _active_db_path(db_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"SpendShape database not found: {source_path}")
    target_dir = backup_dir(source_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    final = target_dir / f"finance-{stamp}-{_safe_reason(reason)}.db"
    temp = target_dir / f".{final.name}.{uuid.uuid4().hex}.tmp"

    with _LOCK:
        opened = conn is None
        source = conn or sqlite3.connect(source_path)
        try:
            _copy_database(source, temp)
            validate_database(temp, allow_legacy=allow_legacy)
            os.replace(temp, final)
            _apply_retention(target_dir, retain=retain)
            return final
        finally:
            if opened:
                source.close()
            _delete_temporary_database(temp)


def _apply_retention(directory: Path, *, retain: int) -> None:
    if retain < 1:
        return
    files = sorted(
        directory.glob("finance-*.db"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    for stale in files[retain:]:
        stale.unlink()


def list_backups(db_path: Optional[Path] = None) -> list[dict]:
    directory = backup_dir(db_path)
    if not directory.exists():
        return []
    rows = []
    for path in sorted(directory.glob("finance-*.db"), reverse=True):
        stat = path.stat()
        rows.append({
            "path": path,
            "name": path.name,
            "created_at": datetime.fromtimestamp(stat.st_mtime),
            "size_bytes": stat.st_size,
        })
    return rows


def save_uploaded_backup(filename: str, payload: bytes,
                         *, db_path: Optional[Path] = None) -> Path:
    """Persist and validate an uploaded candidate without touching live data."""
    directory = backup_dir(db_path)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_stem = _safe_reason(Path(filename).stem)
    final = directory / f"finance-{stamp}-uploaded-{safe_stem}.db"
    temp = directory / f".{final.name}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(payload)
        validate_database(temp)
        os.replace(temp, final)
        return final
    finally:
        _delete_temporary_database(temp)


def restore_database(candidate: Path, *, db_path: Optional[Path] = None) -> dict:
    """Restore a validated backup and preserve the old DB for rollback.

    The selected file is copied through SQLite into a staged database. The
    current DB receives its own pre-restore backup before SQLite transactionally
    replaces its pages. If post-restore validation fails, that recovery point
    is copied back immediately. Using SQLite for both directions avoids unsafe
    raw copies and Windows WAL-sidecar file-lock problems.
    """
    live = _active_db_path(db_path)
    selected = Path(candidate).resolve()
    if selected == live:
        raise BackupValidationError(
            "Choose a backup file, not the active SpendShape database."
        )
    validate_database(selected)
    live.parent.mkdir(parents=True, exist_ok=True)
    staged = live.parent / f".{live.name}.restore-{uuid.uuid4().hex}.tmp"

    with _LOCK:
        source = sqlite3.connect(
            f"file:{selected.as_posix()}?mode=ro", uri=True
        )
        try:
            _copy_database(source, staged)
        finally:
            source.close()
        validate_database(staged)
        pre_restore = create_backup(
            reason="pre-restore", db_path=live
        ) if live.exists() else None
        restore_started = False
        try:
            source = sqlite3.connect(
                f"file:{staged.as_posix()}?mode=ro", uri=True
            )
            destination = sqlite3.connect(live)
            try:
                restore_started = True
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source.close()
            result = validate_database(live)
        except Exception:
            if restore_started and pre_restore is not None:
                recovery = sqlite3.connect(
                    f"file:{pre_restore.as_posix()}?mode=ro", uri=True
                )
                destination = sqlite3.connect(live)
                try:
                    recovery.backup(destination)
                    destination.commit()
                finally:
                    destination.close()
                    recovery.close()
            raise
        finally:
            _delete_temporary_database(staged)
        return {
            **result,
            "restored_from": selected,
            "pre_restore_backup": pre_restore,
            "restart_recommended": True,
        }

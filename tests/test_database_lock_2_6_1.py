"""Reading must never wait behind a write nobody needed.

All data here is invented and disposable.

The installed 2.6.0 app reported `database is locked` seventeen times in one
session, across money_focus, insights_summary, review_summary, ai_settings,
insight_feed, category_settings and preview_plan. Every one of those is a
**read**. In WAL mode a read never blocks on a writer, so a read-only action
should not have been able to produce that message at all.

It could, because every request runs in its own short-lived sidecar and every
sidecar called `init_db()`, which opened a write transaction whether or not
there was anything to write. On an already-current database that was about
five milliseconds of no-op DDL — and five milliseconds of *exclusive* no-op
DDL, once per request, across a screen that fires ten requests at once, is a
queue. Measured directly before the fix: with another connection holding a
write lock, `init_db()` failed after 31.5 seconds. The same call now returns
in under two milliseconds without taking a lock at all.

The fix is to stamp the database with a fingerprint of the schema this build
would create, and to skip the entire write path when it already matches.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import utils.database as db


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    return tmp_path


@pytest.fixture
def current(fresh):
    """A database already carrying this build's schema."""
    db.init_db()
    return fresh


# ── the fingerprint ──────────────────────────────────────────────────────

def test_a_brand_new_database_is_never_mistaken_for_current(fresh):
    assert db._schema_is_current(db.DB_PATH) is False
    db.DB_PATH.touch()
    assert db._schema_is_current(db.DB_PATH) is False, "an empty file is not a schema"


def test_the_fingerprint_is_non_zero(fresh):
    """Zero is what an uninitialised database reports, so it can never be
    a valid stamp."""
    assert db._schema_fingerprint() != 0


def test_initialising_stamps_the_database(current):
    conn = sqlite3.connect(current / "finance.db")
    try:
        stamped = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()

    assert stamped == db._schema_fingerprint()
    assert db._schema_is_current(db.DB_PATH) is True


def test_a_changed_schema_invalidates_the_stamp(current, monkeypatch):
    """Otherwise a new table would never be created on an existing install."""
    monkeypatch.setattr(
        db, "_SCHEMA_SQL",
        db._SCHEMA_SQL + "\nCREATE TABLE IF NOT EXISTS invented_later (id INTEGER);",
    )

    assert db._schema_is_current(db.DB_PATH) is False
    db.init_db()
    assert db._schema_is_current(db.DB_PATH) is True
    conn = db.get_connection()
    try:
        conn.execute("SELECT id FROM invented_later").fetchall()
    finally:
        conn.close()


def test_a_changed_migration_list_invalidates_the_stamp(current, monkeypatch):
    monkeypatch.setattr(
        db, "_SCHEMA_STEPS", db._SCHEMA_STEPS + ("migrate_something_new",),
    )

    assert db._schema_is_current(db.DB_PATH) is False


def test_a_bumped_revision_invalidates_the_stamp(current, monkeypatch):
    """The escape hatch for changing what an existing migration does."""
    monkeypatch.setattr(db, "_SCHEMA_REVISION", db._SCHEMA_REVISION + 1)

    assert db._schema_is_current(db.DB_PATH) is False


# ── the actual defect ────────────────────────────────────────────────────

def test_a_current_database_opens_no_write_transaction(current):
    """The whole fix, stated as one behaviour.

    Another connection holds the write lock. Before this change `init_db()`
    waited out its full busy timeout and then raised `database is locked`;
    every read-only screen action went through that call first.
    """
    blocker = sqlite3.connect(current / "finance.db", timeout=0.1)
    blocker.execute("PRAGMA busy_timeout=100")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        db.init_db()  # must not raise, and must not wait
    finally:
        blocker.rollback()
        blocker.close()


def test_reads_still_work_while_a_writer_holds_the_lock(current):
    """WAL's actual guarantee, which the old startup write was hiding."""
    from tests.conftest import add_tx

    seed = db.get_connection()
    add_tx(seed, day="2026-06-01", desc="INVENTED GROCER", amount=42.0,
           direction="debit", category="Groceries")
    seed.commit()
    seed.close()

    blocker = sqlite3.connect(current / "finance.db", timeout=0.1)
    blocker.execute("PRAGMA busy_timeout=100")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        db.init_db()
        reader = db.get_connection()
        try:
            count = reader.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
        finally:
            reader.close()
    finally:
        blocker.rollback()
        blocker.close()

    assert count == 1


def test_init_on_a_current_database_is_fast(current):
    """Not a benchmark: a guard that the write path is genuinely skipped.

    The old no-op path did real DDL work in an exclusive transaction. If this
    ever creeps back up, the fast path has stopped being taken.
    """
    import time

    started = time.perf_counter()
    for _ in range(20):
        db.init_db()
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"20 no-op inits took {elapsed:.2f}s"


# ── simultaneous sidecars ────────────────────────────────────────────────

def _init_in_thread(path, results, index):
    """Each thread gets its own connection, as a separate process would."""
    try:
        db.init_db()
        conn = db.get_connection()
        try:
            conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
        finally:
            conn.close()
        results[index] = "ok"
    except Exception as cause:  # noqa: BLE001 - the failure is the result
        results[index] = f"{type(cause).__name__}: {cause}"


def test_many_starts_against_a_fresh_database_all_succeed(fresh):
    """Ten screens mounting at once against a database that does not exist."""
    results = [None] * 10
    threads = [
        threading.Thread(target=_init_in_thread, args=(fresh, results, i))
        for i in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    failures = [r for r in results if r != "ok"]
    assert not failures, failures
    assert db._schema_is_current(db.DB_PATH) is True


def test_many_starts_against_a_current_database_all_succeed(current):
    results = [None] * 16
    threads = [
        threading.Thread(target=_init_in_thread, args=(current, results, i))
        for i in range(16)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    failures = [r for r in results if r != "ok"]
    assert not failures, failures


_PROCESS_INIT_SCRIPT = r"""
import os
import time
from pathlib import Path
from utils import database

start = Path(os.environ["NORTHSTAR_TEST_START"])
deadline = time.monotonic() + 30
while not start.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("test start signal never arrived")
    time.sleep(0.005)

database.init_db()
with database.get_connection() as conn:
    conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
"""


def _run_independent_sidecars(root, count=8):
    """Start real interpreters together, as the packaged one-shot engine does."""
    start = root / "start-sidecars"
    env = os.environ.copy()
    env["LEDGER_DATA_DIR"] = str(root)
    env["NORTHSTAR_TEST_START"] = str(start)
    env.pop("LEDGER_DEMO_DB", None)
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _PROCESS_INIT_SCRIPT],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(count)
    ]
    start.write_text("go", encoding="utf-8")
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        if process.returncode != 0:
            failures.append(
                f"exit {process.returncode}: {(stderr or stdout).strip()}"
            )
    assert not failures, failures


def test_independent_processes_create_one_fresh_database(fresh):
    _run_independent_sidecars(fresh)
    assert db._schema_is_current(db.DB_PATH) is True


def test_independent_processes_share_one_current_database(current):
    _run_independent_sidecars(current)
    assert db._schema_is_current(db.DB_PATH) is True


def test_independent_processes_upgrade_a_2_6_0_database(current):
    # 2.6.0 had the current tables but no schema fingerprint.
    conn = sqlite3.connect(current / "finance.db")
    try:
        conn.execute("PRAGMA user_version=0")
        conn.commit()
    finally:
        conn.close()

    _run_independent_sidecars(current)
    assert db._schema_is_current(db.DB_PATH) is True


def test_the_schema_lock_never_blocks_forever(current):
    """Best-effort by design: an unavailable lock must not stop startup."""
    with db._schema_write_lock(db.DB_PATH):
        # Re-entering from the same process must still complete.
        with db._schema_write_lock(db.DB_PATH):
            pass


# ── migration behaviour is preserved ─────────────────────────────────────

def test_a_legacy_database_is_backed_up_before_it_is_touched(fresh):
    """Unchanged behaviour, re-asserted because the call site moved."""
    path = fresh / "finance.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, marker TEXT)")
    conn.execute("INSERT INTO transactions (marker) VALUES ('legacy marker')")
    conn.commit()
    conn.close()

    assert db._schema_requires_migration(path) is True
    with pytest.raises(sqlite3.DatabaseError):
        db.init_db()

    backups = list((fresh / "backups").glob("*pre-migration.db"))
    assert len(backups) == 1
    restored = sqlite3.connect(backups[0])
    try:
        assert restored.execute(
            "SELECT marker FROM transactions"
        ).fetchone()[0] == "legacy marker"
    finally:
        restored.close()


def test_a_failed_migration_leaves_the_database_unstamped(fresh):
    """So the next launch retries rather than skipping the work."""
    path = fresh / "finance.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, marker TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.DatabaseError):
        db.init_db()

    assert db._schema_is_current(path) is False, (
        "a half-finished migration was stamped as current"
    )


def test_a_database_from_the_previous_build_still_gets_migrated(current):
    """The upgrade path the fast path must never swallow.

    2.6.0 wrote no stamp at all, so every existing install arrives here with
    user_version 0 and takes the full path exactly once.
    """
    from tests.conftest import reopen_as_older_build

    reopen_as_older_build()
    assert db._schema_is_current(db.DB_PATH) is False

    # Remove a recorded migration, as a genuinely older database would lack it.
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM app_migrations")
        conn.commit()
    finally:
        conn.close()

    db.init_db()

    conn = db.get_connection()
    try:
        recorded = conn.execute(
            "SELECT COUNT(*) FROM app_migrations"
        ).fetchone()[0]
    finally:
        conn.close()

    assert recorded > 0, "migrations were skipped for an older database"
    assert db._schema_is_current(db.DB_PATH) is True


def test_a_fresh_database_is_still_fully_created(fresh):
    db.init_db()

    conn = db.get_connection()
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    for expected in ("transactions", "accounts", "monthly_plans",
                     "account_balances", "app_migrations", "net_worth_entries"):
        assert expected in tables, f"{expected} was not created"


# ── the whole burst, end to end ──────────────────────────────────────────

def test_a_screenful_of_mixed_actions_reports_no_lock(fresh):
    """Home, Insights, review and Plan together, as navigation produces them."""
    import io

    from desktop.engine import ledger_engine

    def call(action, params):
        out = io.StringIO()
        code = ledger_engine.run(
            io.StringIO(json.dumps({"action": action, "params": params}) + "\n"),
            out,
        )
        return code, out.getvalue()

    burst = [
        ("money_focus", {}),
        ("insights_summary", {"period_days": 30}),
        ("review_summary", {}),
        ("ai_settings", {}),
        ("insight_feed", {}),
        ("category_settings", {}),
        ("preview_plan", {"mode": "normal", "apply_preset": False}),
        ("home_summary", {"period_days": 30}),
        ("plan_summary", {}),
        ("net_worth_trend", {}),
    ]
    ledger_engine._bind_database()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(burst)) as pool:
        outcomes = list(pool.map(lambda spec: call(*spec), burst))

    locked = [body for _code, body in outcomes if "database is locked" in body]
    assert not locked, f"{len(locked)} lock errors: {locked[:2]}"

"""
SQLite database layer for Finance Dashboard.
Tables: transactions, investments, contributions, import_log, budgets,
        profiles, score_weights, watch_list, recommendations_log

Pass 28: demo-mode resolution.
  Setting environment variable LEDGER_DEMO_DB=1 redirects DB_PATH from
  data/finance.db to data/finance.demo.db. The demo DB is built by
  scripts/create_demo_data.py and contains FAKE data only — never
  Ben's real finances. is_demo_db() and demo_db_path() are exposed
  for the launcher banner and Diagnostics page.
"""
import os
import sqlite3
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from utils.platform_utils import get_data_dir


# One-time, data-safe refresh for imports created with the signed financial
# semantics contract but before the current high-confidence merchant rules.
# Deliberate user edits and saved-rule results are never rewritten.
_BUILTIN_CATEGORIZATION_MIGRATION = "builtin-categorization-0.9.0"
_INVESTMENT_SEMANTICS_MIGRATION = "investment-semantics-0.7.0"
# Goals was retired from native navigation, but active legacy goal rows kept
# reserving money out of Safe to Spend with no screen left to see or stop them.
# Their influence is switched off once; the rows themselves are preserved.
_LEGACY_GOALS_MIGRATION = "legacy_goals_excluded_from_plan_v1"

_DATA_DIR    = get_data_dir()
_REAL_DB     = _DATA_DIR / "finance.db"
_DEMO_DB     = _DATA_DIR / "finance.demo.db"


def is_demo_mode() -> bool:
    """Return True iff the LEDGER_DEMO_DB environment flag is set
    truthy. Centralised so the launcher / banner / smoke tests all
    agree on what "demo mode" means."""
    return (os.environ.get("LEDGER_DEMO_DB", "")
            .strip().lower() in {"1", "true", "yes", "on"})


def demo_db_path() -> Path:
    """The on-disk path the demo seed script writes to."""
    return _DEMO_DB


def real_db_path() -> Path:
    """The on-disk path real imports write to."""
    return _REAL_DB


# Resolved at import time. Tests / smoke override DB_PATH directly,
# so they stay unaffected. Production app code reads DB_PATH only.
DB_PATH = _DEMO_DB if is_demo_mode() else _REAL_DB


def get_connection() -> sqlite3.Connection:
    # Native screens use short-lived sidecar processes. A second request can
    # arrive while the first is finishing schema setup, so wait for brief
    # SQLite locks before negotiating WAL instead of failing immediately.
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# The complete current schema, as one statement script. Lifted out of
# init_db() so it can be fingerprinted: see _SCHEMA_FINGERPRINT below.
_SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS transactions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_type        TEXT NOT NULL,
            account_id          TEXT,
            transaction_date    TEXT NOT NULL,
            posted_date         TEXT,
            raw_description     TEXT NOT NULL,
            merchant            TEXT,
            merchant_normalized TEXT,
            amount              REAL NOT NULL,
            currency            TEXT DEFAULT 'CAD',
            foreign_amount      REAL,
            foreign_currency    TEXT,
            fx_rate             REAL,
            category            TEXT,
            subcategory         TEXT,
            category_source     TEXT DEFAULT 'unknown',
            category_rule_id    INTEGER,
            category_explanation TEXT,
            category_updated_at TEXT,
            manually_edited_at  TEXT,
            direction           TEXT NOT NULL,
            transaction_type    TEXT,
            is_transfer         INTEGER DEFAULT 0,
            is_flagged          INTEGER DEFAULT 0,
            flag_reason         TEXT,
            parse_confidence    TEXT DEFAULT 'high',
            reward_points       REAL,
            statement_period    TEXT,
            statement_memo      TEXT,
            statement_category  TEXT,
            statement_subcategory TEXT,
            import_batch_id     INTEGER,
            dedup_hash          TEXT UNIQUE,
            notes               TEXT,
            shared_expense_override INTEGER,
            shared_user_share_pct REAL,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_tx_date      ON transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_tx_category  ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_tx_direction ON transactions(direction);
        CREATE INDEX IF NOT EXISTS idx_tx_merchant  ON transactions(merchant);
        CREATE INDEX IF NOT EXISTS idx_tx_batch     ON transactions(import_batch_id);

        CREATE TABLE IF NOT EXISTS investments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name    TEXT NOT NULL,
            account_type    TEXT,
            ticker          TEXT,
            security_name   TEXT,
            quantity        REAL,
            book_value      REAL,
            market_value    REAL NOT NULL,
            currency        TEXT DEFAULT 'CAD',
            as_of_date      TEXT NOT NULL,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS contributions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            account_type    TEXT NOT NULL,
            year            INTEGER NOT NULL,
            contributed     REAL DEFAULT 0,
            room_available  REAL,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS budgets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            category        TEXT NOT NULL UNIQUE,
            amount          REAL NOT NULL,
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS import_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            filename        TEXT NOT NULL,
            file_hash       TEXT,
            account_type    TEXT,
            statement_period TEXT,
            rows_parsed     INTEGER DEFAULT 0,
            rows_inserted   INTEGER DEFAULT 0,
            rows_skipped    INTEGER DEFAULT 0,
            rows_flagged    INTEGER DEFAULT 0,
            errors          TEXT,
            semantics_version INTEGER DEFAULT 2,
            imported_at     TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS data_repair_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            repair_kind         TEXT NOT NULL,
            backup_path         TEXT NOT NULL,
            batches_affected    INTEGER DEFAULT 0,
            rows_affected       INTEGER DEFAULT 0,
            manual_rows_kept    INTEGER DEFAULT 0,
            review_before       INTEGER DEFAULT 0,
            review_after        INTEGER DEFAULT 0,
            details_json        TEXT DEFAULT '{}',
            completed_at        TEXT DEFAULT (datetime('now','localtime'))
        );

        -- v2.0 new tables --------------------------------------------------

        CREATE TABLE IF NOT EXISTS profiles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            is_active   INTEGER DEFAULT 0,
            budgets_json    TEXT DEFAULT '{}',
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );

        -- Pass 36: default score weights rebalanced around everyday
        -- usefulness:
        --   savings 40   - net cashflow is the primary signal
        --   diversity 30 - now "Spending control" (controllable spend
        --                  concentration + cut focus)
        --   debt 15      - exact statement interest/fees only
        --   consistency 15 - rolling positive-net months / data rhythm
        CREATE TABLE IF NOT EXISTS score_weights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            savings_weight  REAL DEFAULT 40,
            diversity_weight REAL DEFAULT 30,
            debt_weight     REAL DEFAULT 15,
            consistency_weight REAL DEFAULT 15,
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS watch_list (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant    TEXT NOT NULL UNIQUE,
            reason      TEXT,
            added_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS recommendations_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_key         TEXT NOT NULL,
            title           TEXT,
            category        TEXT,
            state           TEXT DEFAULT 'active',
            snoozed_until   TEXT,
            annual_impact   REAL,
            notes           TEXT,
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- v7 new tables ----------------------------------------------------

        -- Stored timestamps below are UTC: the `datetime('now')` default and
        -- every writer that touches these columns must agree, or `updated_at`
        -- can land earlier than `created_at` for a single write. Tables that
        -- deliberately store local time (accounts, review_log, shared_expense_*)
        -- use datetime('now','localtime') in BOTH places for the same reason.

        -- User-taught merchant→category mappings. Consulted by categorizer
        -- BEFORE the static ruleset so user corrections are durable.
        CREATE TABLE IF NOT EXISTS learned_rules (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_normalized TEXT NOT NULL UNIQUE,
            category            TEXT NOT NULL,
            subcategory         TEXT,
            source              TEXT DEFAULT 'user',
            enabled             INTEGER DEFAULT 1,
            example_description TEXT,
            source_transaction_id INTEGER,
            source_import_batch_id INTEGER,
            hit_count           INTEGER DEFAULT 0,
            last_used_at        TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_learned_merchant ON learned_rules(merchant_normalized);

        CREATE TABLE IF NOT EXISTS recurring_preferences (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_normalized TEXT NOT NULL UNIQUE,
            status              TEXT NOT NULL CHECK(status IN ('recurring','not_recurring')),
            display_name        TEXT,
            category            TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS income_source_preferences (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_normalized   TEXT NOT NULL UNIQUE,
            status              TEXT NOT NULL CHECK(status IN ('confirmed','excluded')),
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS shared_expense_settings (
            id                      INTEGER PRIMARY KEY CHECK(id=1),
            shared_with_name        TEXT DEFAULT '',
            default_user_share_pct  REAL NOT NULL DEFAULT 50,
            partner_matcher         TEXT DEFAULT '',
            updated_at              TEXT DEFAULT (datetime('now','localtime'))
        );
        INSERT OR IGNORE INTO shared_expense_settings
            (id,shared_with_name,default_user_share_pct,partner_matcher)
        VALUES (1,'',50,'');

        CREATE TABLE IF NOT EXISTS shared_expense_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_type      TEXT NOT NULL CHECK(scope_type IN ('merchant','recurring','category')),
            scope_value     TEXT NOT NULL,
            user_share_pct  REAL NOT NULL,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            updated_at      TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(scope_type,scope_value)
        );

        CREATE TABLE IF NOT EXISTS net_worth_estimates (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            total_assets        REAL NOT NULL DEFAULT 0,
            total_liabilities   REAL NOT NULL DEFAULT 0,
            as_of_date          TEXT NOT NULL,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS category_change_undo (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payload_json TEXT NOT NULL,
            changed_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── Pass 19: investment snapshots + net worth tracking ──────────
        --
        -- The legacy `investments` table is a single flat row-per-holding
        -- with no history. Pass 19 introduces an explicit two-tier model:
        --   investment_snapshot_batches  — one per CSV upload / manual snap
        --   investment_positions          — per-holding rows under a batch
        --
        -- This preserves portfolio history so net worth can be tracked
        -- over time. The legacy `investments` table is kept untouched so
        -- existing manual entries continue to work.

        CREATE TABLE IF NOT EXISTS investment_snapshot_batches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file     TEXT,
            file_hash       TEXT,
            as_of_date      TEXT NOT NULL,
            row_count       INTEGER DEFAULT 0,
            total_market_value_native REAL,
            currencies_seen TEXT,
            mixed_currency  INTEGER DEFAULT 0,
            notes           TEXT,
            imported_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inv_snap_date
            ON investment_snapshot_batches(as_of_date);

        CREATE TABLE IF NOT EXISTS investment_positions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_batch_id INTEGER NOT NULL,
            account_name      TEXT,
            account_type      TEXT,
            account_number_masked TEXT,
            ticker            TEXT,
            exchange          TEXT,
            security_name     TEXT,
            security_type     TEXT,
            quantity          REAL,
            market_price      REAL,
            market_price_currency TEXT,
            book_value_cad    REAL,
            book_value_market REAL,
            market_value      REAL,
            market_value_currency TEXT,
            unrealized_return REAL,
            unrealized_return_currency TEXT,
            position_direction TEXT,
            created_at        TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (snapshot_batch_id)
                REFERENCES investment_snapshot_batches(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_inv_pos_batch
            ON investment_positions(snapshot_batch_id);
        CREATE INDEX IF NOT EXISTS idx_inv_pos_ticker
            ON investment_positions(ticker);

        -- Manual cash / debt balances. Type drives whether it counts as
        -- an asset or a liability. Snapshots accumulate over time so we
        -- can chart net worth without overwriting prior data.
        CREATE TABLE IF NOT EXISTS account_balances (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_ref INTEGER,
            account_name TEXT NOT NULL,
            account_kind TEXT NOT NULL,   -- cash|chequing|savings|credit_card|loan|mortgage|other_asset|other_liability
            balance     REAL NOT NULL,
            currency    TEXT DEFAULT 'CAD',
            as_of_date  TEXT NOT NULL,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_acct_bal_date
            ON account_balances(as_of_date);

        -- The canonical net worth record: one reading per month, entered by
        -- hand. Created here rather than lazily by utils/net_worth.py so it
        -- is a first-class table, which is what lets reset_preview count it
        -- and reset_all_financial_data clear it. See utils/net_worth.py for
        -- why net worth is measured rather than projected.
        CREATE TABLE IF NOT EXISTS net_worth_entries (
            month        TEXT PRIMARY KEY,
            cash         REAL NOT NULL DEFAULT 0,
            investments  REAL NOT NULL DEFAULT 0,
            other_assets REAL NOT NULL DEFAULT 0,
            liabilities  REAL NOT NULL DEFAULT 0,
            note         TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            updated_at   TEXT DEFAULT (datetime('now','localtime'))
        );

        -- Computed/manual net worth snapshots. Each row represents one
        -- "as-of" net worth reading. Source breakdown is JSON for
        -- forward-compatibility (per-account contributions, etc).
        CREATE TABLE IF NOT EXISTS net_worth_snapshots (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date        TEXT NOT NULL,
            total_assets      REAL DEFAULT 0,
            total_liabilities REAL DEFAULT 0,
            net_worth         REAL DEFAULT 0,
            source_breakdown  TEXT,
            currency          TEXT DEFAULT 'CAD',
            mixed_currency    INTEGER DEFAULT 0,
            notes             TEXT,
            created_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_nw_snap_date
            ON net_worth_snapshots(as_of_date);

        -- ── Pass 35c: authoritative statement summary (Mastercard) ───────
        -- The Mastercard PDF's first page carries the bank's own numbers
        -- for interest_charges / fees / cash_advances / new_balance /
        -- payment_due_date. Pre-Pass-35c, downstream scoring guessed
        -- from transaction rows and produced wrong totals (e.g. matched
        -- INTERAC e-Transfers via LIKE '%INTEREST%'). Persisting the
        -- summary lets compute_score use the truth value when one
        -- exists, and falls back to the transaction-based heuristic
        -- only when the summary is missing.
        --
        -- One row per import batch (FK → import_log.id). On
        -- delete_import_batch the row cascades automatically via the
        -- explicit DELETE in delete_import_batch() — there is no
        -- ON DELETE CASCADE here because import_log doesn't enforce
        -- foreign keys app-wide.
        CREATE TABLE IF NOT EXISTS statement_summaries (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id          INTEGER NOT NULL,
            account_type             TEXT,
            statement_period_label   TEXT,
            statement_start_date     TEXT,
            statement_end_date       TEXT,
            previous_balance         REAL,
            payments_and_credits     REAL,
            transactions_total       REAL,
            cash_advances_total      REAL,
            adjustments_total        REAL,
            interest_charges         REAL,
            fees                     REAL,
            new_balance              REAL,
            minimum_payment_due      REAL,
            payment_due_date         TEXT,
            created_at               TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_stmt_summary_batch
            ON statement_summaries(import_batch_id);
        CREATE INDEX IF NOT EXISTS idx_stmt_summary_dates
            ON statement_summaries(statement_start_date, statement_end_date);

        -- ── Pass 21: month plan + goals ───────────────────────────────────
        --
        -- Three tables form the planning loop. Additive — the legacy
        -- `budgets` table still works for ad-hoc per-category targets.
        --
        --   monthly_plans            — one row per (month, plan slot)
        --   category_budget_targets  — per-category targets within a plan
        --   goal_targets             — long-running goals (cash buffer,
        --                              net worth, debt, etc)

        CREATE TABLE IF NOT EXISTS monthly_plans (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            month               TEXT NOT NULL,    -- 'YYYY-MM'
            mode                TEXT,              -- normal|tight|reset|aggressive_save|sub_cleanup|debt_recovery|stabilize
            income_target       REAL,
            spending_target     REAL,
            savings_target      REAL,
            fixed_obligations   REAL DEFAULT 0,
            flexible_allowance  REAL DEFAULT 0,
            safety_buffer       REAL DEFAULT 0,
            detected_commitments_at_save REAL,
            fixed_override_reason TEXT,
            net_worth_target    REAL,
            notes               TEXT,
            created_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            UNIQUE(month)
        );
        CREATE INDEX IF NOT EXISTS idx_mp_month ON monthly_plans(month);

        CREATE TABLE IF NOT EXISTS category_budget_targets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            monthly_plan_id     INTEGER NOT NULL,
            category            TEXT NOT NULL,
            target_amount       REAL NOT NULL,
            basis               TEXT,    -- 'recent_avg'|'recent_avg_minus_X'|'fixed'|'manual'
            difficulty          TEXT,    -- 'conservative'|'normal'|'tight'|'watch'
            created_at          TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (monthly_plan_id)
                REFERENCES monthly_plans(id) ON DELETE CASCADE,
            UNIQUE(monthly_plan_id, category)
        );
        CREATE INDEX IF NOT EXISTS idx_cbt_plan
            ON category_budget_targets(monthly_plan_id);

        CREATE TABLE IF NOT EXISTS goal_targets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            type                TEXT,    -- emergency_fund|cash_buffer|net_worth|debt_reduction|investment_contribution|savings_rate|sub_reduction|custom
            target_amount       REAL,
            current_amount      REAL DEFAULT 0,
            target_date         TEXT,
            status              TEXT DEFAULT 'active',  -- active|paused|done|abandoned
            notes               TEXT,
            linked_metric       TEXT,    -- 'net_worth'|'investments'|'cash_balance'|null  (auto-update source)
            progress_method     TEXT DEFAULT 'manual', -- manual|linked_account|legacy_metric
            linked_account_ref  INTEGER,
            planned_monthly_contribution REAL DEFAULT 0,
            contribution_frequency TEXT DEFAULT 'monthly',
            include_in_plan     INTEGER DEFAULT 1,
            show_milestones     INTEGER DEFAULT 1,
            currency            TEXT DEFAULT 'CAD',
            completed_at        TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (linked_account_ref) REFERENCES accounts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_goal_status ON goal_targets(status);

        -- Weekly check-in history (Home page). One row per completed
        -- review. Local-only, like everything else in this database.
        CREATE TABLE IF NOT EXISTS review_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            completed_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            planning_period     TEXT,     -- 'YYYY-MM' the review covered
            data_through        TEXT,     -- latest transaction date at completion
            safe_to_spend       REAL,     -- canonical value at completion
            primary_action      TEXT,     -- acknowledged primary action title
            notes               TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_review_log_completed
            ON review_log(completed_at);

        -- First-class accounts. `type` reuses the account_kind
        -- vocabulary already used by manual Net Worth balances.
        -- is_migrated marks accounts created by the legacy backfill;
        -- they participate in the legacy dedup-hash bridge.
        CREATE TABLE IF NOT EXISTS accounts (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT NOT NULL,
            type                 TEXT NOT NULL,
            institution          TEXT,
            currency             TEXT DEFAULT 'CAD',
            is_archived          INTEGER DEFAULT 0,
            opening_balance      REAL DEFAULT 0,
            opening_balance_date TEXT,
            available_for_spending INTEGER,
            is_migrated          INTEGER DEFAULT 0,
            created_at           TEXT DEFAULT (datetime('now','localtime')),
            updated_at           TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_accounts_archived
            ON accounts(is_archived);

        -- Reusable CSV import profiles: a column mapping that worked
        -- once, keyed by the file's header signature so future files
        -- from the same bank map automatically. Additive; local-only.
        CREATE TABLE IF NOT EXISTS import_profiles (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            header_signature    TEXT NOT NULL UNIQUE,
            mapping_json        TEXT NOT NULL,
            headers_json        TEXT,
            created_at          TEXT DEFAULT (datetime('now','localtime')),
            updated_at          TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS app_migrations (
            migration_key       TEXT PRIMARY KEY,
            applied_at          TEXT DEFAULT (datetime('now','localtime')),
            rows_scanned        INTEGER DEFAULT 0,
            rows_changed        INTEGER DEFAULT 0,
            details_json        TEXT
        );
"""


# Every schema step that runs after _SCHEMA_SQL, in order. Listed here so
# adding, removing or reordering one changes the fingerprint automatically and
# existing databases get the new step.
#
# Changing what an *existing* step does, without renaming it, is the one case
# the fingerprint cannot see. Bump _SCHEMA_REVISION when you do that.
_SCHEMA_STEPS = (
    "seed_score_weights",
    "migrate_categorization_schema",
    "columns:transactions:transaction_type,shared_expense_override,shared_user_share_pct",
    "columns:import_log:semantics_version",
    "columns:account_balances:account_ref",
    "columns:recurring_preferences:display_name,category",
    "migrate_etransfer_cashflow_semantics",
    "migrate_investment_semantics_070",
    "migrate_builtin_categorization",
    "columns:import_profiles:account_type,amount_convention,profile_version",
    "migrate_accounts_schema",
    "migrate_goals_schema",
    "migrate_retire_legacy_goal_reservations",
    "migrate_monthly_plan_schema",
    "migrate_rec_log_unique",
)
_SCHEMA_REVISION = 1


def _schema_fingerprint() -> int:
    """A non-zero 32-bit stamp for the schema this build would create.

    Stored in the database's own ``user_version`` header field once the schema
    has been brought up to date. It changes whenever the schema script or the
    list of steps changes, so a stale database never takes the fast path.
    """
    import zlib

    payload = _SCHEMA_SQL + "\n".join(_SCHEMA_STEPS) + str(_SCHEMA_REVISION)
    # Fold to 31 bits and force non-zero: 0 is what a brand-new database
    # reports, and that must always mean "not initialised".
    return (zlib.crc32(payload.encode("utf-8")) & 0x7FFFFFFF) or 1


def _schema_is_current(path) -> bool:
    """True when this database already has exactly this build's schema.

    Deliberately read-only, and cheap: one header read on a read-only
    connection. Any doubt at all returns False, which routes the caller into
    the full write path.
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            stamped = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        return int(stamped or 0) == _schema_fingerprint()
    except (sqlite3.Error, OSError):
        return False


# Threads inside one process coordinate here; the file lock below is only
# needed between processes. An RLock so a nested acquisition on one thread is
# free rather than a deadlock against its own held file handle.
_SCHEMA_THREAD_LOCK = threading.RLock()
_SCHEMA_DEPTH = threading.local()
# How long to wait for another process to finish building the schema. The
# work itself is well under a second; this is only a ceiling so a crashed
# process holding the file can never wedge a launch permanently.
_SCHEMA_LOCK_TIMEOUT_SECONDS = 20.0


@contextmanager
def _schema_write_lock(path):
    """Let one process at a time build or migrate the schema.

    Several one-shot sidecars start together whenever a screen mounts. Without
    this they would all run the same CREATE/ALTER work at once and pile up on
    SQLite's write lock; with it, the first does the work and the rest wait,
    re-check, and find nothing left to do.

    Advisory and best-effort by design. If the lock cannot be taken the caller
    still proceeds: every statement under it is idempotent, so the worst case
    is the old behaviour, never a refusal to start.
    """
    with _SCHEMA_THREAD_LOCK:
        depth = getattr(_SCHEMA_DEPTH, "value", 0)
        _SCHEMA_DEPTH.value = depth + 1
        handle = None
        locked = False
        try:
            if depth == 0:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handle = open(path.with_name(path.name + ".init-lock"), "a+b")
                    deadline = time.monotonic() + _SCHEMA_LOCK_TIMEOUT_SECONDS
                    while True:
                        try:
                            if os.name == "nt":
                                import msvcrt
                                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(
                                    handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB,
                                )
                            locked = True
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                break
                            time.sleep(0.02)
                except OSError:
                    handle = None
            yield
        finally:
            _SCHEMA_DEPTH.value = depth
            if handle is not None:
                if locked:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()


def init_db():
    """Bring the database up to the current schema, writing only if needed.

    Every screen action runs in its own short-lived sidecar process, and each
    one used to open a write transaction here even when there was nothing to
    do. Reads never block reads in WAL mode, so those unnecessary writes were
    the only reason a read-only action such as Insights or Coach could ever
    report `database is locked`: the requests queued behind each other's
    pointless write lock rather than behind any real work.

    A database already carrying this build's schema now takes a read-only
    path and opens no write transaction at all.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _schema_is_current(DB_PATH):
        return

    with _schema_write_lock(DB_PATH):
        # Another process may have finished the work while we waited.
        if _schema_is_current(DB_PATH):
            return
        _initialize_schema()


def _initialize_schema():
    """Create or migrate the schema. Only ever called under the schema lock."""
    if _schema_requires_migration(DB_PATH):
        # Import lazily to keep the database layer usable by the backup module.
        # The backup happens before CREATE/ALTER statements touch the file.
        from utils.backups import create_backup
        create_backup(
            reason="pre-migration", db_path=DB_PATH, allow_legacy=True
        )
    with get_connection() as conn:
        conn.executescript(_SCHEMA_SQL)

        # Seed default score weights row if empty.
        # Pass 36: defaults are 40/30/15/15. Existing rows are migrated
        # only when they are one of SpendShape's known historical defaults,
        # preserving truly custom user choices.
        count = conn.execute("SELECT COUNT(*) FROM score_weights").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO score_weights (savings_weight, diversity_weight, debt_weight, consistency_weight) "
                "VALUES (40, 30, 15, 15)"
            )
            conn.commit()
        else:
            row = conn.execute(
                "SELECT id, savings_weight, diversity_weight, debt_weight, consistency_weight "
                "FROM score_weights ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                old = (
                    float(row["savings_weight"] or 0),
                    float(row["diversity_weight"] or 0),
                    float(row["debt_weight"] or 0),
                    float(row["consistency_weight"] or 0),
                )
                if old in ((30.0, 20.0, 25.0, 25.0),
                           (40.0, 15.0, 20.0, 25.0)):
                    conn.execute(
                        "UPDATE score_weights SET savings_weight=40, "
                        "diversity_weight=30, debt_weight=15, "
                        "consistency_weight=15, updated_at=datetime('now') "
                        "WHERE id=?",
                        (row["id"],),
                    )
                    conn.commit()

        # ── v7 migrations: AI + categorization metadata ────────────
        # Keep this slice atomic. ALTER TABLE is transactional in SQLite when
        # it runs inside an explicit savepoint, so a failed index or backfill
        # cannot leave an existing finance database half-upgraded.
        _migrate_categorization_schema(conn)

        # Financial semantics are additive. Existing rows are not silently
        # rewritten; analytics derives a safe legacy fallback when this is NULL.
        _ensure_columns(conn, "transactions", [
            ("transaction_type", "TEXT"),
            ("shared_expense_override", "INTEGER"),
            ("shared_user_share_pct", "REAL"),
        ])
        _ensure_columns(conn, "import_log", [
            # Existing rows predate the signed-amount contract. New imports
            # explicitly write version 2 in persist_import_batch().
            ("semantics_version", "INTEGER DEFAULT 1"),
        ])
        _ensure_columns(conn, "account_balances", [
            ("account_ref", "INTEGER"),
        ])
        _ensure_columns(conn, "recurring_preferences", [
            ("display_name", "TEXT"),
            ("category", "TEXT"),
        ])
        _migrate_etransfer_cashflow_semantics(conn)
        _migrate_investment_semantics_070(conn)
        _migrate_builtin_categorization(conn)
        _ensure_columns(conn, "import_profiles", [
            ("account_type", "TEXT"),
            ("amount_convention", "TEXT"),
            ("profile_version", "INTEGER DEFAULT 2"),
        ])

        # ── v8 migration: first-class accounts ─────────────────────
        _migrate_accounts_schema(conn)

        # ── v9 migration: purposeful goals + contribution tracking ─
        _migrate_goals_schema(conn)
        # After the goal schema, because it reads include_in_plan and
        # that column is added just above on an upgrading database.
        _migrate_retire_legacy_goal_reservations(conn)

        # ── v10 migration: native monthly-plan inputs ──────────────
        _migrate_monthly_plan_schema(conn)

        # ── Pass 15 migration: ensure recommendations_log.rec_key is UNIQUE ──
        # `set_rec_state` uses `INSERT … ON CONFLICT(rec_key) DO UPDATE`, which
        # requires a UNIQUE or PRIMARY KEY constraint on the conflict target.
        # The original schema only had `id` as PK and `rec_key` as plain TEXT,
        # so every Mark Done / Snooze / Ignore action crashed with:
        #     OperationalError: ON CONFLICT clause does not match any
        #     PRIMARY KEY or UNIQUE constraint
        # This migration adds a UNIQUE INDEX on `rec_key`. If duplicates exist
        # in older databases (none seen so far, but defensive), the most
        # recently-updated row wins and the rest are deleted before the index
        # is created so the migration cannot fail.
        _migrate_rec_log_unique(conn)
        conn.commit()
        # Stamp last, and only after everything above committed. A database
        # that failed part way through keeps its old stamp and is brought up
        # to date again on the next launch rather than being skipped.
        conn.execute(f"PRAGMA user_version = {_schema_fingerprint()}")
        conn.commit()


def _ensure_columns(conn, table: str, columns: list[tuple[str, str]]):
    """Idempotent ALTER TABLE … ADD COLUMN for each (name, type) not present."""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, coltype in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def _migrate_etransfer_cashflow_semantics(conn) -> None:
    """Repair rows imported under the old blanket e-transfer exclusion.

    Only the legacy ``personal_transfer`` type is touched. A user who already
    marked a row as ``Internal Transfer`` therefore keeps that explicit choice.
    """
    conn.execute("""
        UPDATE transactions
        SET transaction_type='other_income', category='Income', subcategory='',
            category_source='semantics_migration', is_transfer=0,
            parse_confidence='high', is_flagged=0, flag_reason=''
        WHERE transaction_type='personal_transfer'
          AND UPPER(COALESCE(raw_description,'')) LIKE '%INTERAC E-TRANSFER%'
          AND direction='credit'
          AND COALESCE(category,'') NOT IN ('Transfer','Internal Transfer','Savings')
    """)
    conn.execute("""
        UPDATE transactions
        SET transaction_type='spending',
            category=CASE WHEN category='Transfer Out' THEN 'Uncategorized'
                          ELSE category END,
            subcategory=CASE WHEN category='Transfer Out' THEN ''
                             ELSE subcategory END,
            category_source='semantics_migration', is_transfer=0,
            parse_confidence='medium', is_flagged=0, flag_reason=''
        WHERE transaction_type='personal_transfer'
          AND UPPER(COALESCE(raw_description,'')) LIKE '%INTERAC E-TRANSFER%'
          AND direction='debit'
          AND COALESCE(category,'') NOT IN ('Transfer','Internal Transfer','Savings')
    """)


def _migrate_investment_semantics_070(conn) -> None:
    """Exclude recognized broker movements that older imports called income."""
    applied = conn.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key=?",
        (_INVESTMENT_SEMANTICS_MIGRATION,),
    ).fetchone()
    if applied:
        return
    before = conn.total_changes
    conn.execute(
        """
        UPDATE transactions
        SET transaction_type='investment_transfer', category='Investments',
            subcategory='Investment transfer', category_source='protected',
            category_explanation='Recognized broker movement; excluded from cash flow.',
            is_transfer=1, is_flagged=0, flag_reason='',
            category_updated_at=datetime('now','localtime')
        WHERE transaction_type='employment_income'
          AND manually_edited_at IS NULL
          AND COALESCE(category_source,'') NOT IN ('user_edit','user_bulk','user_rule','user')
          AND (
            UPPER(COALESCE(raw_description,'')) LIKE '%QUESTRADE%'
            OR UPPER(COALESCE(merchant,'')) LIKE '%QUESTRADE%'
            OR UPPER(COALESCE(raw_description,'')) LIKE '%WEALTHSIMPLE%'
            OR UPPER(COALESCE(merchant,'')) LIKE '%WEALTHSIMPLE%'
          )
        """
    )
    changed = conn.total_changes - before
    conn.execute(
        "INSERT INTO app_migrations "
        "(migration_key,rows_scanned,rows_changed,details_json) VALUES (?,?,?,?)",
        (_INVESTMENT_SEMANTICS_MIGRATION, changed, changed, json.dumps({
            "scope": "unmodified imported broker deposits",
            "preserves_source_sign": True,
        })),
    )
def _migrate_retire_legacy_goal_reservations(conn) -> None:
    """Stop retired Goals reserving money nobody can see.

    Goals is gone from native navigation, but `goal_plan_summary` still read
    every active goal whose include_in_plan
    was 1 — the column default. A goal carried over from the Streamlit era
    therefore kept taking money out of Safe to Spend and the cash cushion,
    and there was no screen left on which to inspect, pause or remove it. A
    visible $800 savings target with two forgotten $600 and $500
    allocations behind it reserved $1,100.

    The rows are kept. Deleting someone's saved goals to fix a reservation
    bug is a worse trade than the bug, and the data is worth having if
    Goals ever returns as a supported screen. Only the influence is
    switched off.

    One-time, so a goal created deliberately after this point is left
    alone.
    """
    applied = conn.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key=?",
        (_LEGACY_GOALS_MIGRATION,),
    ).fetchone()
    if applied:
        return
    before = conn.total_changes
    conn.execute(
        "UPDATE goal_targets SET include_in_plan=0 "
        "WHERE COALESCE(include_in_plan, 1) != 0"
    )
    changed = conn.total_changes - before
    conn.execute(
        "INSERT INTO app_migrations "
        "(migration_key,rows_scanned,rows_changed,details_json) VALUES (?,?,?,?)",
        (_LEGACY_GOALS_MIGRATION, changed, changed, json.dumps({
            "reason": "Goals is not reachable in the native app. Rows are "
                      "preserved; plan influence is removed.",
            "rows_deleted": 0,
        })),
    )


def _migrate_builtin_categorization(conn) -> None:
    """Apply current safe rules once to signed-contract imported rows.

    Earlier releases correctly repaired signs and cash-flow semantics but did
    not revisit built-in merchant categories after those rules improved. That
    left upgraded databases with rows such as a recognized mortgage still
    stored as Uncategorized. Re-enrichment is limited to semantics v2 import
    batches and skips every deliberate user provenance marker.
    """
    applied = conn.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key=?",
        (_BUILTIN_CATEGORIZATION_MIGRATION,),
    ).fetchone()
    if applied:
        return

    from utils.categorizer import enrich_transaction
    from utils.financial_semantics import consolidate_material_review

    rows = conn.execute(
        """
        SELECT transactions.*
        FROM transactions
        JOIN import_log ON import_log.id=transactions.import_batch_id
        WHERE COALESCE(import_log.semantics_version,1)>=2
          AND transactions.manually_edited_at IS NULL
          AND COALESCE(transactions.category_source,'') NOT IN
              ('user_edit','user_bulk','user_rule')
        ORDER BY transactions.id
        """
    ).fetchall()
    refreshed_rows = []
    for row in rows:
        refreshed = dict(row)
        enrich_transaction(refreshed, conn=conn)
        refreshed_rows.append(refreshed)
    # Rebuild the material review queue as one population. Re-enriching rows
    # individually would flag every historical large occurrence instead of
    # one useful merchant-level confirmation.
    consolidate_material_review(refreshed_rows)

    changed = category_changes = type_changes = review_changes = 0
    conn.execute("SAVEPOINT builtin_categorization_refresh")
    try:
        for row, refreshed in zip(rows, refreshed_rows):
            before = dict(row)
            fields = (
                "merchant_normalized", "category", "subcategory",
                "category_source", "category_rule_id",
                "category_explanation", "transaction_type", "is_transfer",
                "is_flagged", "flag_reason", "parse_confidence",
            )
            if all(refreshed.get(key) == before.get(key) for key in fields):
                continue
            category_changes += int(
                refreshed.get("category") != before.get("category")
            )
            type_changes += int(
                refreshed.get("transaction_type") != before.get("transaction_type")
            )
            review_changes += int(
                (refreshed.get("is_flagged"), refreshed.get("flag_reason"))
                != (before.get("is_flagged"), before.get("flag_reason"))
            )
            conn.execute(
                """
                UPDATE transactions
                SET merchant_normalized=?, category=?, subcategory=?,
                    category_source=?, category_rule_id=?,
                    category_explanation=?, transaction_type=?, is_transfer=?,
                    is_flagged=?, flag_reason=?, parse_confidence=?,
                    category_updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                tuple(refreshed.get(key) for key in fields) + (before["id"],),
            )
            changed += 1
        conn.execute(
            """
            INSERT INTO app_migrations
                (migration_key,rows_scanned,rows_changed,details_json)
            VALUES (?,?,?,?)
            """,
            (
                _BUILTIN_CATEGORIZATION_MIGRATION,
                len(rows),
                changed,
                json.dumps({
                    "scope": "signed imported rows without user provenance",
                    "preserves_source_sign": True,
                    "category_changes": category_changes,
                    "transaction_type_changes": type_changes,
                    "review_changes": review_changes,
                }),
            ),
        )
        conn.execute("RELEASE SAVEPOINT builtin_categorization_refresh")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT builtin_categorization_refresh")
        conn.execute("RELEASE SAVEPOINT builtin_categorization_refresh")
        raise
def _migrate_categorization_schema(conn) -> None:
    """Atomically add and backfill categorization audit metadata."""
    conn.execute("SAVEPOINT categorization_schema")
    try:
        _ensure_columns(conn, "transactions", [
            ("ai_suggested_category",    "TEXT"),
            ("ai_suggested_subcategory", "TEXT"),
            ("ai_confidence",            "REAL"),
            ("ai_provider",              "TEXT"),
            ("ai_model",                 "TEXT"),
            ("ai_rationale",             "TEXT"),
            ("ai_suggested_at",          "TEXT"),
            # NULL = pending, 1 = accepted, 0 = rejected. User-driven only.
            ("ai_accepted",              "INTEGER"),
            # Additive and nullable so historical categories remain intact.
            ("merchant_normalized",      "TEXT"),
            ("category_source",          "TEXT DEFAULT 'unknown'"),
            ("category_rule_id",         "INTEGER"),
            ("category_explanation",     "TEXT"),
            ("category_updated_at",      "TEXT"),
            ("manually_edited_at",       "TEXT"),
            ("statement_memo",           "TEXT"),
            ("statement_category",       "TEXT"),
            ("statement_subcategory",    "TEXT"),
        ])
        _ensure_columns(conn, "learned_rules", [
            ("enabled",                  "INTEGER DEFAULT 1"),
            ("example_description",      "TEXT"),
            ("source_transaction_id",    "INTEGER"),
            ("source_import_batch_id",   "INTEGER"),
            ("updated_at",               "TEXT"),
        ])
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tx_merchant_normalized "
            "ON transactions(merchant_normalized)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learned_enabled "
            "ON learned_rules(enabled, merchant_normalized)"
        )
        _backfill_categorization_metadata(conn)
        conn.execute("RELEASE SAVEPOINT categorization_schema")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT categorization_schema")
        conn.execute("RELEASE SAVEPOINT categorization_schema")
        raise


def _backfill_categorization_metadata(conn) -> None:
    """Populate only newly-added matcher metadata on historical rows.

    Existing categories are intentionally untouched: an old manual correction
    is financial history even when SpendShape cannot prove where it came from.
    """
    from utils.categorizer import normalize_merchant

    rows = conn.execute(
        "SELECT id, raw_description, merchant FROM transactions "
        "WHERE merchant_normalized IS NULL OR merchant_normalized=''"
    ).fetchall()
    for row in rows:
        matcher = normalize_merchant(
            row["raw_description"] or row["merchant"] or ""
        )
        conn.execute(
            "UPDATE transactions SET merchant_normalized=? WHERE id=?",
            (matcher, int(row["id"])),
        )

    # Older learned rules used the display merchant as their exact key. Move
    # each rule to the new canonical key when that key is not already owned.
    # A collision remains as a disabled legacy row, making resolution
    # deterministic without silently deleting a conflicting user decision.
    rules = conn.execute(
        "SELECT * FROM learned_rules ORDER BY id"
    ).fetchall()
    for rule in rules:
        old = (rule["merchant_normalized"] or "").strip()
        canonical = normalize_merchant(old)
        if not canonical or canonical == old:
            continue
        owner = conn.execute(
            "SELECT id FROM learned_rules WHERE merchant_normalized=?",
            (canonical,),
        ).fetchone()
        if owner is None:
            conn.execute(
                "UPDATE learned_rules SET merchant_normalized=?, "
                "updated_at=COALESCE(updated_at, datetime('now')) WHERE id=?",
                (canonical, int(rule["id"])),
            )
        else:
            conn.execute(
                "UPDATE learned_rules SET enabled=0, "
                "updated_at=COALESCE(updated_at, datetime('now')) WHERE id=?",
                (int(rule["id"]),),
            )


def _migrate_rec_log_unique(conn) -> None:
    """Idempotent migration: ensure `recommendations_log.rec_key` has a UNIQUE
    index so `INSERT … ON CONFLICT(rec_key) DO UPDATE` works.

    Steps:
      1. Check if a UNIQUE index already exists for rec_key.
      2. If not, dedup any existing duplicate rec_keys (keep newest per key).
      3. Create the unique index.

    Safe to run on every startup. No-ops once the index exists.
    """
    # Step 1: index already present?
    for idx in conn.execute("PRAGMA index_list(recommendations_log)").fetchall():
        if idx["unique"]:
            cols = conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            if len(cols) == 1 and cols[0]["name"] == "rec_key":
                return  # already migrated

    # Step 2: dedup. For each rec_key with multiple rows, keep the row with
    # MAX(id) — that's the most recently inserted (id is AUTOINCREMENT).
    # We delete the rest BEFORE creating the unique index so it can't fail.
    conn.execute("""
        DELETE FROM recommendations_log
        WHERE id NOT IN (
            SELECT MAX(id) FROM recommendations_log GROUP BY rec_key
        )
    """)

    # Step 3: create the unique index.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rec_log_rec_key "
        "ON recommendations_log(rec_key)"
    )


# ─────────────────────────────────────────────
# Core transaction helpers (unchanged)
# ─────────────────────────────────────────────

def _occurrence_suffix(occurrence: int) -> str:
    """Distinguish the 2nd, 3rd... identical row within one statement.

    Occurrence 0 deliberately adds nothing, so fingerprints already stored in
    existing databases keep matching. Without that, everyone's next re-import
    would look entirely new and double-count their history.
    """
    return f"|#{int(occurrence)}" if occurrence else ""


def compute_dedup_hash(account_type: str, transaction_date: str,
                       raw_description: str, amount: float,
                       occurrence: int = 0) -> str:
    key = (f"{account_type}|{transaction_date}|"
           f"{raw_description.strip().lower()}|{amount:.2f}"
           f"{_occurrence_suffix(occurrence)}")
    return hashlib.sha256(key.encode()).hexdigest()


def compute_dedup_hash_v2(account_ref: int, transaction_date: str,
                          raw_description: str, amount: float,
                          occurrence: int = 0) -> str:
    """Account-aware duplicate fingerprint (rows with an account_ref).

    Keyed by the account's stable id instead of the loose account_type
    string, so identical activity on two accounts of the same type can
    coexist while true re-imports still collide."""
    key = (f"acct{int(account_ref)}|{transaction_date}|"
           f"{raw_description.strip().lower()}|{amount:.2f}"
           f"{_occurrence_suffix(occurrence)}")
    return hashlib.sha256(key.encode()).hexdigest()


def dedup_group_key(tx: dict) -> tuple:
    """What makes two rows look identical, before occurrence is applied.

    Callers importing a file group rows by this to number repeats in the
    order they appear in the statement.
    """
    return (
        str(tx.get("account_ref") or "") or str(tx.get("account_type") or ""),
        str(tx.get("transaction_date") or ""),
        str(tx.get("raw_description") or "").strip().lower(),
        round(float(tx.get("amount") or 0.0), 2),
    )


def transaction_dedup_state(
    tx: dict, conn: sqlite3.Connection, occurrence: Optional[int] = 0,
) -> tuple[str, bool]:
    """Return the persisted fingerprint and exact insert-time duplicate state.

    ``occurrence`` is which copy of an identical-looking row this is within
    the statement being imported: 0 for the first, 1 for the second, and so
    on. Two genuine same-day transit fares therefore get different
    fingerprints and both survive, while re-importing that same file produces
    the same two fingerprints and inserts nothing.

    Pass ``None`` for a standalone insert such as manual entry, where the
    user typing the row again is a deliberate act rather than a duplicate
    file: the first unused occurrence is claimed automatically.
    """
    if occurrence is None:
        probe = 0
        while True:
            fingerprint, exists = transaction_dedup_state(
                tx, conn, occurrence=probe,
            )
            if not exists:
                return fingerprint, False
            probe += 1
            if probe > 500:  # pathological; fall back to reporting duplicate
                return fingerprint, True

    legacy_hash = compute_dedup_hash(
        tx.get("account_type", ""),
        tx.get("transaction_date", ""),
        tx.get("raw_description", ""),
        tx.get("amount", 0.0),
        occurrence,
    )
    account_ref = tx.get("account_ref")
    if account_ref:
        # Account-aware fingerprint: identical-looking charges on two
        # different accounts (e.g. two credit cards) are NOT duplicates.
        dedup = compute_dedup_hash_v2(
            int(account_ref),
            tx.get("transaction_date", ""),
            tx.get("raw_description", ""),
            tx.get("amount", 0.0),
            occurrence,
        )
        # Compatibility bridge: rows imported before accounts existed
        # carry the legacy hash. Re-importing that same file into the
        # row's own (or a not-yet-assigned) account must still skip —
        # but a legacy hash on a DIFFERENT account never blocks.
        existing = conn.execute(
            """
            SELECT id FROM transactions
            WHERE dedup_hash = ?
               OR (dedup_hash = ?
                   AND (account_ref IS NULL OR account_ref = ?))
            """,
            (dedup, legacy_hash, int(account_ref)),
        ).fetchone()
    else:
        dedup = legacy_hash
        existing = conn.execute(
            "SELECT id FROM transactions WHERE dedup_hash = ?", (dedup,)
        ).fetchone()
    return dedup, existing is not None


def delete_transaction(tx_id: int,
                       conn: Optional[sqlite3.Connection] = None) -> bool:
    """Remove one transaction outright.

    For rows that should never have been in the ledger: a statement imported
    against the wrong account, a duplicate the fingerprint could not catch, a
    manual entry typed twice. Correcting a category is the usual answer and is
    reversible; this is not, so the interface asks first.

    The dedup fingerprint goes with it, which matters: without that, deleting
    a row and re-importing the same statement would silently do nothing.
    """
    opened = conn is None
    c = conn or get_connection()
    try:
        row = c.execute(
            "SELECT id FROM transactions WHERE id = ?", (int(tx_id),)
        ).fetchone()
        if not row:
            return False
        # Anything hanging off the row goes too, so a later undo or review
        # cannot resurrect half of it.
        for table, column in (
            ("category_change_undo", "transaction_id"),
            ("transaction_notes", "transaction_id"),
        ):
            try:
                c.execute(f"DELETE FROM {table} WHERE {column} = ?",
                          (int(tx_id),))
            except sqlite3.OperationalError:
                pass  # That table is not in this schema version.
        c.execute("DELETE FROM transactions WHERE id = ?", (int(tx_id),))
        c.commit()
        return True
    finally:
        if opened:
            c.close()


def insert_transaction(tx: dict, conn: sqlite3.Connection,
                       occurrence: Optional[int] = 0) -> tuple[bool, str]:
    """Insert one transaction unless it is a true duplicate.

    ``occurrence`` numbers identical-looking rows within a single statement
    so genuine repeat purchases survive; see ``transaction_dedup_state``.
    """
    from utils.categorizer import normalize_merchant

    tx.setdefault(
        "merchant_normalized",
        normalize_merchant(tx.get("raw_description") or tx.get("merchant") or ""),
    )
    tx.setdefault("category_source", "unknown")
    dedup, duplicate = transaction_dedup_state(tx, conn, occurrence)
    if duplicate:
        return False, "duplicate"
    tx["dedup_hash"] = dedup
    columns = ", ".join(tx.keys())
    placeholders = ", ".join(["?"] * len(tx))
    conn.execute(
        f"INSERT INTO transactions ({columns}) VALUES ({placeholders})",
        list(tx.values()),
    )
    # Count a learned-rule hit only after the transaction is actually inserted.
    # Parsing previews and duplicate rows must not inflate rule usage.
    if tx.get("category_source") == "user_rule" and tx.get("category_rule_id"):
        conn.execute(
            "UPDATE learned_rules SET hit_count=COALESCE(hit_count,0)+1, "
            "last_used_at=datetime('now','localtime') WHERE id=?",
            (int(tx["category_rule_id"]),),
        )
    return True, "ok"


def insert_import_log(log: dict, conn: sqlite3.Connection) -> int:
    if "errors" in log and isinstance(log["errors"], list):
        log["errors"] = json.dumps(log["errors"])
    # Stamp localtime explicitly: the table default is UTC, but
    # review_log (weekly check-ins) stores localtime, and the import-
    # result panel compares the two to decide what's "unreviewed".
    if "imported_at" not in log:
        from datetime import datetime as _dt
        log["imported_at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ", ".join(log.keys())
    placeholders = ", ".join(["?"] * len(log))
    cursor = conn.execute(
        f"INSERT INTO import_log ({columns}) VALUES ({placeholders})",
        list(log.values()),
    )
    return cursor.lastrowid


# ─────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────

def get_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    account_type: Optional[str] = None,
    exclude_transfers: bool = True,
    exclude_payments: bool = True,
    directions: Optional[list] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    conditions = []
    params = []

    if start_date:
        conditions.append("transaction_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("transaction_date <= ?")
        params.append(end_date)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if account_type:
        conditions.append("account_type = ?")
        params.append(account_type)
    if exclude_transfers:
        conditions.append("is_transfer = 0")
    if exclude_payments:
        conditions.append("direction != 'payment'")
    if directions:
        placeholders = ", ".join(["?"] * len(directions))
        conditions.append(f"direction IN ({placeholders})")
        params.extend(directions)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM transactions {where} ORDER BY transaction_date DESC",
        params,
    ).fetchall()

    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def get_monthly_summary(year: int, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """
    Monthly income/spending summary for a calendar year.
    Uses the same logic as compute_cashflow() in analytics.py (v8 model):
      income  = direction='credit' AND amount > 0
                AND direction NOT IN ('payment','cancelled','transfer')
                AND category NOT IN ('Credit Card Payment','Cancelled')
      spending = direction='debit'
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled')
                 minus refund credits (amount < 0)
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    from collections import defaultdict
    from utils.financial_semantics import cashflow_totals
    from utils.shared_expenses import apply_shared_view
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT * FROM transactions WHERE strftime('%Y', transaction_date)=?",
        (str(year),),
    ).fetchall():
        tx = dict(row)
        grouped[str(tx.get("transaction_date") or "")[:7]].append(tx)
    result = []
    for month in sorted(grouped):
        totals = cashflow_totals(
            apply_shared_view(grouped[month], conn, "personal"), view="personal"
        )
        result.append({"month": month, **totals, "tx_count": len(grouped[month])})
    if close:
        conn.close()
    return result


def get_category_totals(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    share_view: str = "personal",
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Category totals for spending charts (donut, bars).
    Includes only canonical spending types. Refunds, reimbursements, and
    rewards credits are income and never alter category purchase amounts.
    account_type: optional filter ('chequing', 'savings', 'mastercard', or None/all).
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    acct_clause = ""
    params: list = [start_date, end_date]
    if account_type and account_type != "all":
        acct_clause = " AND account_type = ?"
        params.append(account_type)

    from collections import defaultdict
    from config.categories import SPENDING_CATEGORIES
    from utils.financial_semantics import cashflow_role
    from utils.shared_expenses import apply_shared_view
    totals: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "tx_count": 0})
    gross_spending = 0.0
    rows = conn.execute(
        "SELECT * FROM transactions WHERE transaction_date BETWEEN ? AND ?"
        + acct_clause, params,
    ).fetchall()
    for tx in apply_shared_view((dict(row) for row in rows), conn, share_view):
        category = tx.get("category") or "Uncategorized"
        role = cashflow_role(tx)
        amount = abs(float(tx.get("_cashflow_amount") or 0))
        if role == "spending":
            if category not in SPENDING_CATEGORIES:
                category = "Uncategorized"
            totals[category]["total"] += amount
            totals[category]["tx_count"] += 1
            gross_spending += amount

    target_total = round(gross_spending, 2)
    rounded: dict[str, float] = {
        category: round(data["total"], 2)
        for category, data in totals.items()
        if data["total"] > 0 and target_total > 0
    }
    if rounded:
        residual = round(round(target_total, 2) - sum(rounded.values()), 2)
        largest = max(rounded, key=rounded.get)
        rounded[largest] = round(rounded[largest] + residual, 2)
    result = [
        {"category": category, "total": rounded[category],
         "tx_count": data["tx_count"]}
        for category, data in totals.items() if category in rounded
    ]
    result.sort(key=lambda r: r["total"], reverse=True)
    if close:
        conn.close()
    return result


def get_flagged_transactions(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute(
        "SELECT * FROM transactions WHERE is_flagged = 1 ORDER BY transaction_date DESC"
    ).fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def update_transaction(tx_id: int, updates: dict, conn: Optional[sqlite3.Connection] = None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    sets = ", ".join([f"{k} = ?" for k in updates.keys()])
    conn.execute(f"UPDATE transactions SET {sets} WHERE id = ?", [*updates.values(), tx_id])
    if close:
        conn.commit()
        conn.close()


def get_import_log(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT * FROM import_log ORDER BY imported_at DESC").fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def delete_import_batch(batch_id: int, conn: Optional[sqlite3.Connection] = None) -> int:
    """Compatibility wrapper for the transaction-safe batch undo path."""
    from utils.categorization_service import undo_import_batch

    result = undo_import_batch(int(batch_id), conn=conn)
    return int(result.get("transactions_deleted", 0))


# ── Pass 35c: statement-summary persistence ─────────────────────────
# Authoritative interest / fees / cash-advances / new-balance values
# from the Mastercard PDF statement summary. The Tangerine Mastercard
# parser returns these as `result["statement_summary"]`; the Import
# page persists them via upsert_statement_summary() right after
# inserting the import_log row.

_STATEMENT_SUMMARY_COLUMNS = (
    "import_batch_id", "account_type",
    "statement_period_label",
    "statement_start_date", "statement_end_date",
    "previous_balance", "payments_and_credits",
    "transactions_total", "cash_advances_total",
    "adjustments_total", "interest_charges",
    "fees", "new_balance",
    "minimum_payment_due", "payment_due_date",
)


def upsert_statement_summary(
    summary: dict,
    *,
    import_batch_id: int,
    account_type: str = "mastercard",
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Insert (or replace) the statement summary for an import batch.

    Only persists when at least one summary field is non-None — there's
    no point storing an entirely empty row from a parser that couldn't
    find the summary block.

    Returns the row id (or 0 when skipped).
    """
    if not summary:
        return 0
    # Skip when every persisted field is None / missing.
    if not any(
        summary.get(k) is not None
        for k in _STATEMENT_SUMMARY_COLUMNS
        if k not in ("import_batch_id", "account_type")
    ):
        return 0

    close = False
    if conn is None:
        conn = get_connection()
        close = True

    # Replace any prior summary for this batch (idempotent re-import).
    conn.execute(
        "DELETE FROM statement_summaries WHERE import_batch_id=?",
        (int(import_batch_id),),
    )
    values = [
        int(import_batch_id),
        account_type,
        summary.get("statement_period_label") or "",
        summary.get("statement_start_date"),
        summary.get("statement_end_date"),
        summary.get("previous_balance"),
        summary.get("payments_and_credits"),
        summary.get("transactions_total"),
        summary.get("cash_advances_total"),
        summary.get("adjustments_total"),
        summary.get("interest_charges"),
        summary.get("fees"),
        summary.get("new_balance"),
        summary.get("minimum_payment_due"),
        summary.get("payment_due_date"),
    ]
    placeholders = ",".join(["?"] * len(_STATEMENT_SUMMARY_COLUMNS))
    cols = ",".join(_STATEMENT_SUMMARY_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO statement_summaries ({cols}) VALUES ({placeholders})",
        values,
    )
    rid = cur.lastrowid
    if close:
        conn.commit()
        conn.close()
    return int(rid or 0)


def get_statement_summary_for_batch(
    batch_id: int,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[dict]:
    """Return the persisted statement summary for a batch, or None."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        row = conn.execute(
            "SELECT * FROM statement_summaries WHERE import_batch_id=?",
            (int(batch_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        if close:
            conn.close()
        return None
    if close:
        conn.close()
    return dict(row) if row else None


def get_statement_summaries_in_range(
    start_date: str,
    end_date: str,
    *,
    account_type: Optional[str] = "mastercard",
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Return all statement summaries that overlap [start_date, end_date].

    A summary overlaps when its statement_end_date >= start_date AND
    statement_start_date <= end_date. Used by compute_score to pick up
    the right Mastercard statement(s) for the scoring window.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        params: list = [end_date, start_date]
        sql = (
            "SELECT * FROM statement_summaries "
            "WHERE statement_start_date IS NOT NULL "
            "  AND statement_end_date   IS NOT NULL "
            "  AND statement_start_date <= ? "
            "  AND statement_end_date   >= ? "
        )
        if account_type:
            sql += " AND account_type = ? "
            params.append(account_type)
        sql += " ORDER BY statement_end_date DESC"
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if close:
        conn.close()
    return [dict(r) for r in rows]


def get_investments(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT * FROM investments ORDER BY as_of_date DESC, account_name").fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def upsert_investment(inv: dict, conn: sqlite3.Connection):
    columns = ", ".join(inv.keys())
    placeholders = ", ".join(["?"] * len(inv))
    conn.execute(
        f"INSERT OR REPLACE INTO investments ({columns}) VALUES ({placeholders})",
        list(inv.values()),
    )


def delete_all_transactions(conn: Optional[sqlite3.Connection] = None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM import_log")
    if close:
        conn.commit()
        conn.close()


def get_budgets(conn=None) -> dict:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT category, amount FROM budgets ORDER BY category").fetchall()
    result = {r["category"]: r["amount"] for r in rows}
    if close:
        conn.close()
    return result


def upsert_budget(category: str, amount: float, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO budgets (category, amount) VALUES (?,?) "
        "ON CONFLICT(category) DO UPDATE SET amount=excluded.amount, updated_at=datetime('now')",
        (category, amount),
    )
    if close:
        conn.commit()
        conn.close()


def delete_budget(category: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM budgets WHERE category=?", (category,))
    if close:
        conn.commit()
        conn.close()


def rerun_categorization(conn=None, *, preserve_manual: bool = True):
    """Reprocess automatic categories without erasing reviewed decisions.

    Manual, bulk, and accepted-AI categories are user history. The Settings
    action preserves them by default; callers must opt in explicitly if they
    ever need a destructive full rebuild.
    """
    from utils.categorizer import enrich_transaction
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    if preserve_manual:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE manually_edited_at IS NULL "
            "AND category_source IN ('import_rule','user_rule','protected')"
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transactions").fetchall()

    updated = 0
    for row in rows:
        original = dict(row)
        candidate = {
            "raw_description": original.get("raw_description") or "",
            "amount": original.get("amount") or 0,
            "direction": original.get("direction") or "",
            "account_type": original.get("account_type") or "",
            "is_transfer": 0,
        }
        enriched = enrich_transaction(candidate, conn=conn)

        conn.execute("""
            UPDATE transactions
            SET category=?, subcategory=?, merchant=?, merchant_normalized=?,
                parse_confidence=?, is_flagged=?, flag_reason=?, is_transfer=?,
                category_source=?, category_rule_id=?, category_explanation=?,
                category_updated_at=datetime('now','localtime')
            WHERE id=?
        """, (
            enriched.get("category"), enriched.get("subcategory"),
            enriched.get("merchant"), enriched.get("merchant_normalized"),
            enriched.get("parse_confidence"), enriched.get("is_flagged", 0),
            enriched.get("flag_reason"), enriched.get("is_transfer", 0),
            enriched.get("category_source"), enriched.get("category_rule_id"),
            enriched.get("category_explanation"), int(row["id"]),
        ))
        updated += 1

    if close:
        conn.commit()
        conn.close()
    return updated


# ─────────────────────────────────────────────
# v2.0: Profiles
# ─────────────────────────────────────────────

def get_profiles(conn=None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def get_active_profile(conn=None) -> Optional[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute("SELECT * FROM profiles WHERE is_active=1 LIMIT 1").fetchone()
    result = dict(row) if row else None
    if close:
        conn.close()
    return result


def upsert_profile(name: str, description: str = "", budgets_json: str = "{}", notes: str = "", conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO profiles (name, description, budgets_json, notes) VALUES (?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
        "budgets_json=excluded.budgets_json, notes=excluded.notes, updated_at=datetime('now')",
        (name, description, budgets_json, notes),
    )
    if close:
        conn.commit()
        conn.close()


def set_active_profile(profile_name: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("UPDATE profiles SET is_active=0")
    conn.execute("UPDATE profiles SET is_active=1 WHERE name=?", (profile_name,))
    if close:
        conn.commit()
        conn.close()


def delete_profile(profile_name: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM profiles WHERE name=?", (profile_name,))
    if close:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# v2.0: Score weights
# ─────────────────────────────────────────────

def get_score_weights(conn=None) -> dict:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute("SELECT * FROM score_weights ORDER BY id LIMIT 1").fetchone()
    # Pass 36 defaults: 40 / 30 / 15 / 15. Existing user rows are
    # returned untouched; this only matters when the table is empty.
    result = dict(row) if row else {
        "savings_weight": 40, "diversity_weight": 30,
        "debt_weight": 15, "consistency_weight": 15
    }
    if close:
        conn.close()
    return result


def save_score_weights(savings: float, diversity: float, debt: float, consistency: float, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    # Always update the single row; if missing insert it
    existing = conn.execute("SELECT id FROM score_weights LIMIT 1").fetchone()
    if existing:
        conn.execute(
            "UPDATE score_weights SET savings_weight=?, diversity_weight=?, "
            "debt_weight=?, consistency_weight=?, updated_at=datetime('now') WHERE id=?",
            (savings, diversity, debt, consistency, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO score_weights (savings_weight, diversity_weight, debt_weight, consistency_weight) "
            "VALUES (?,?,?,?)",
            (savings, diversity, debt, consistency),
        )
    if close:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# v2.0: Watch list
# ─────────────────────────────────────────────

def get_watch_list(conn=None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT * FROM watch_list ORDER BY merchant").fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def add_to_watch_list(merchant: str, reason: str = "", conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO watch_list (merchant, reason) VALUES (?,?) "
        "ON CONFLICT(merchant) DO UPDATE SET reason=excluded.reason, added_at=datetime('now')",
        (merchant, reason),
    )
    if close:
        conn.commit()
        conn.close()


def remove_from_watch_list(merchant: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM watch_list WHERE merchant=?", (merchant,))
    if close:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# v2.0: Recommendations log (snooze/done state)
# ─────────────────────────────────────────────

def get_rec_states(conn=None) -> dict:
    """Returns {rec_key: {state, snoozed_until, notes}} for all logged recs."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("SELECT * FROM recommendations_log").fetchall()
    result = {r["rec_key"]: dict(r) for r in rows}
    if close:
        conn.close()
    return result


def set_rec_state(rec_key: str, state: str, title: str = "", annual_impact: float = 0,
                  snoozed_until: str = None, notes: str = "", conn=None):
    """state: 'active' | 'snoozed' | 'done' | 'ignored'"""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO recommendations_log (rec_key, title, state, snoozed_until, annual_impact, notes) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(rec_key) DO UPDATE SET state=excluded.state, "
        "snoozed_until=excluded.snoozed_until, notes=excluded.notes, updated_at=datetime('now')",
        (rec_key, title, state, snoozed_until, annual_impact, notes),
    )
    if close:
        conn.commit()
        conn.close()


def clear_rec_state(rec_key: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM recommendations_log WHERE rec_key=?", (rec_key,))
    if close:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# v2.0: Onboarding helpers
# ─────────────────────────────────────────────

def has_data(conn=None) -> bool:
    """Returns True if any transactions have been imported."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if close:
        conn.close()
    return count > 0


# ─────────────────────────────────────────────
# v7: Learned rules (user corrections → durable rules)
# ─────────────────────────────────────────────

def get_learned_rule(merchant_normalized: str, conn=None) -> Optional[dict]:
    from utils.categorizer import normalize_merchant
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM learned_rules WHERE merchant_normalized=?",
        (normalize_merchant(merchant_normalized),),
    ).fetchone()
    result = dict(row) if row else None
    if close:
        conn.close()
    return result


def upsert_learned_rule(merchant_normalized: str, category: str,
                        subcategory: Optional[str] = None, source: str = "user",
                        *, enabled: bool = True,
                        example_description: Optional[str] = None,
                        source_transaction_id: Optional[int] = None,
                        source_import_batch_id: Optional[int] = None,
                        conn=None) -> int:
    from utils.categorizer import normalize_merchant

    matcher = normalize_merchant(merchant_normalized)
    if not matcher:
        raise ValueError("A saved merchant rule needs a non-empty matcher.")
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO learned_rules "
        "(merchant_normalized, category, subcategory, source, enabled, "
        " example_description, source_transaction_id, source_import_batch_id, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,datetime('now')) "
        "ON CONFLICT(merchant_normalized) DO UPDATE SET "
        "category=excluded.category, subcategory=excluded.subcategory, "
        "source=excluded.source, enabled=excluded.enabled, "
        "example_description=COALESCE(excluded.example_description, learned_rules.example_description), "
        "source_transaction_id=COALESCE(excluded.source_transaction_id, learned_rules.source_transaction_id), "
        "source_import_batch_id=COALESCE(excluded.source_import_batch_id, learned_rules.source_import_batch_id), "
        "updated_at=datetime('now')",
        (matcher, category, subcategory, source, 1 if enabled else 0,
         example_description, source_transaction_id, source_import_batch_id),
    )
    row = conn.execute(
        "SELECT id FROM learned_rules WHERE merchant_normalized=?", (matcher,)
    ).fetchone()
    if close:
        conn.commit()
        conn.close()
    return int(row["id"])


def set_learned_rule_enabled(merchant_normalized: str, enabled: bool,
                             conn=None) -> bool:
    from utils.categorizer import normalize_merchant
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute(
        "UPDATE learned_rules SET enabled=?, updated_at=datetime('now') "
        "WHERE merchant_normalized=?",
        (1 if enabled else 0, normalize_merchant(merchant_normalized)),
    )
    if close:
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def delete_learned_rule(merchant_normalized: str, conn=None):
    from utils.categorizer import normalize_merchant
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "DELETE FROM learned_rules WHERE merchant_normalized=?",
        (normalize_merchant(merchant_normalized),),
    )
    if close:
        conn.commit()
        conn.close()


def update_learned_rule_by_id(rule_id: int, *, category: str,
                              subcategory: Optional[str] = None,
                              enabled: bool = True,
                              example_description: Optional[str] = None,
                              conn=None) -> bool:
    """Edit one exact rule row without re-normalizing its matcher.

    ID-based lifecycle operations keep migrated overlap rows inspectable and
    prevent Settings from accidentally updating a different canonical rule.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute(
        "UPDATE learned_rules SET category=?, subcategory=?, enabled=?, "
        "example_description=?, updated_at=datetime('now') "
        "WHERE id=?",
        (category, subcategory, 1 if enabled else 0,
         example_description, int(rule_id)),
    )
    if close:
        conn.commit()
        conn.close()
    return cur.rowcount == 1


def delete_learned_rule_by_id(rule_id: int, conn=None) -> bool:
    """Delete one exact learned-rule row; historical transactions stay put."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute("DELETE FROM learned_rules WHERE id=?", (int(rule_id),))
    if close:
        conn.commit()
        conn.close()
    return cur.rowcount == 1


def list_learned_rules(conn=None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute(
        "SELECT * FROM learned_rules ORDER BY merchant_normalized"
    ).fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def list_recurring_preferences(conn=None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM recurring_preferences ORDER BY merchant_normalized"
    ).fetchall()]
    if close:
        conn.close()
    return rows


def set_recurring_preference(merchant_normalized: str, status: str,
                             display_name: str = "", category: str = "",
                             conn=None) -> int:
    matcher = str(merchant_normalized or "").strip()
    if not matcher:
        raise ValueError("Choose a merchant before changing recurring status.")
    if status not in {"recurring", "not_recurring"}:
        raise ValueError("Recurring status must be recurring or not recurring.")
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO recurring_preferences "
        "(merchant_normalized,status,display_name,category) VALUES (?,?,?,?) "
        "ON CONFLICT(merchant_normalized) DO UPDATE SET status=excluded.status, "
        "display_name=excluded.display_name, category=excluded.category, "
        "updated_at=datetime('now')",
        (matcher, status, str(display_name or "").strip() or None,
         str(category or "").strip() or None),
    )
    row = conn.execute(
        "SELECT id FROM recurring_preferences WHERE merchant_normalized=?", (matcher,)
    ).fetchone()
    if close:
        conn.commit()
        conn.close()
    return int(row["id"])


def list_income_source_preferences(conn=None) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM income_source_preferences ORDER BY source_normalized"
    ).fetchall()]
    if close:
        conn.close()
    return rows


def set_income_source_preference(source_normalized: str, status: str,
                                 conn=None) -> int:
    source = str(source_normalized or "").strip()
    if not source:
        raise ValueError("Choose an income source first.")
    if status not in {"confirmed", "excluded"}:
        raise ValueError("Income source status must be confirmed or excluded.")
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO income_source_preferences (source_normalized,status) "
        "VALUES (?,?) ON CONFLICT(source_normalized) DO UPDATE SET "
        "status=excluded.status, updated_at=datetime('now')",
        (source, status),
    )
    row = conn.execute(
        "SELECT id FROM income_source_preferences WHERE source_normalized=?",
        (source,),
    ).fetchone()
    if close:
        conn.commit()
        conn.close()
    return int(row["id"])


def clear_recurring_preference(merchant_normalized: str, conn=None) -> bool:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute(
        "DELETE FROM recurring_preferences WHERE merchant_normalized=?",
        (str(merchant_normalized or "").strip(),),
    )
    if close:
        conn.commit()
        conn.close()
    return bool(cur.rowcount)


def bump_learned_rule_hit(merchant_normalized: str, conn=None):
    from utils.categorizer import normalize_merchant
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "UPDATE learned_rules SET hit_count=COALESCE(hit_count,0)+1, "
        "last_used_at=datetime('now') WHERE merchant_normalized=?",
        (normalize_merchant(merchant_normalized),),
    )
    if close:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# v7: AI suggestion helpers
# ─────────────────────────────────────────────

def apply_ai_suggestion(tx_id: int, suggestion: dict, conn=None):
    """Store an AI suggestion on a transaction WITHOUT changing its category.
    suggestion keys: category, subcategory, confidence, provider, model, rationale."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("""
        UPDATE transactions
        SET ai_suggested_category=?, ai_suggested_subcategory=?, ai_confidence=?,
            ai_provider=?, ai_model=?, ai_rationale=?,
            ai_suggested_at=datetime('now'), ai_accepted=NULL
        WHERE id=?
    """, (
        suggestion.get("category"),
        suggestion.get("subcategory"),
        suggestion.get("confidence"),
        suggestion.get("provider"),
        suggestion.get("model"),
        suggestion.get("rationale"),
        tx_id,
    ))
    if close:
        conn.commit()
        conn.close()


def accept_ai_suggestion(tx_id: int, conn=None):
    """Accept the stored suggestion: copy ai_suggested_* into the canonical
    category/subcategory and mark ai_accepted=1."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("""
        UPDATE transactions
        SET category = ai_suggested_category,
            subcategory = ai_suggested_subcategory,
            ai_accepted = 1,
            category_source = 'ai',
            category_rule_id = NULL,
            category_explanation = 'Accepted AI suggestion after user review.',
            category_updated_at = datetime('now','localtime'),
            manually_edited_at = datetime('now','localtime')
        WHERE id=? AND ai_suggested_category IS NOT NULL
    """, (tx_id,))
    if close:
        conn.commit()
        conn.close()


def reject_ai_suggestion(tx_id: int, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("UPDATE transactions SET ai_accepted=0 WHERE id=?", (tx_id,))
    if close:
        conn.commit()
        conn.close()


def get_ai_candidates(limit: int = 50, conn=None) -> list[dict]:
    """Rows eligible for AI suggestion: uncategorized / Misc / low confidence.

    Excludes:
      * Rows the user has already explicitly accepted or rejected a
        suggestion on.
      * Rows whose `flag_reason='reviewed'` — Pass 35d retroactive
        cleanup. Some pre-Pass-34a saves cleared `is_flagged` but did
        NOT promote `parse_confidence`, so rows like the SQ *C&C
        GAMEBRIDGE / SQ *THE SHIP cases kept reappearing in the
        Low-confidence queue. The user has already confirmed those
        rows; respect that decision.
      * Rows that are uncategorized only because they are transfers.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute("""
        SELECT * FROM transactions
        WHERE (category IS NULL OR category='' OR category='Misc' OR parse_confidence='low')
          AND direction IN ('debit','credit')
          AND is_transfer=0
          AND (ai_accepted IS NULL OR ai_accepted IS 0)
          AND (ai_suggested_at IS NULL)
          AND (flag_reason IS NULL OR flag_reason != 'reviewed')
        ORDER BY transaction_date DESC
        LIMIT ?
    """, (limit,)).fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def mark_transaction_reviewed(tx_id: int, conn=None) -> bool:
    """Pass 35d: explicit "mark reviewed" action for the Review page.

    Idempotently sets is_flagged=0, flag_reason='reviewed', and
    parse_confidence='high' on the given row. Useful for the
    "Mark reviewed" fallback button that takes a stuck low-confidence
    row out of the queue when it already has a valid category.

    Returns True when the row exists (regardless of whether it
    already had these values), False when no such row.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute(
        "UPDATE transactions "
        "SET is_flagged=0, flag_reason='reviewed', "
        "    parse_confidence='high' "
        "WHERE id=?",
        (int(tx_id),),
    )
    if close:
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def apply_category_to_merchant(merchant_normalized: str, category: str,
                               subcategory: Optional[str] = None,
                               only_uncertain: bool = True,
                               conn=None) -> int:
    """Apply a category to every transaction matching this normalised merchant.
    If only_uncertain=True, skips rows the user has already hand-categorized
    (i.e. rows where category is set and is not Misc and is not low confidence).
    Returns number of rows updated.

    Also syncs `is_transfer` against the new category — a row whose category
    moves OUT of the non-cashflow set (Transfer/Credit Card Payment/Payment/
    Cancelled) needs `is_transfer=0` so it shows up in spending; a row whose
    category moves INTO that set needs `is_transfer=1`. Without this sync the
    UI appears to "not take" — the category column changes but downstream
    spending queries still skip the row because `is_transfer=1`.
    """
    from utils.categorizer import normalize_merchant
    matcher = normalize_merchant(merchant_normalized)
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    # Mirrors enrich_transaction's _NON_CASHFLOW set (kept local to avoid a
    # circular import).
    _is_transfer_target = 1 if category in (
        "Transfer", "Credit Card Payment", "Payment", "Cancelled"
    ) else 0

    if only_uncertain:
        cur = conn.execute("""
            UPDATE transactions
            SET category=?, subcategory=?, is_transfer=?,
                category_source='user_bulk', category_rule_id=NULL,
                category_explanation='Bulk category decision by user.',
                category_updated_at=datetime('now','localtime'),
                manually_edited_at=datetime('now','localtime')
            WHERE merchant_normalized=?
              AND (category IS NULL OR category='' OR category='Misc'
                   OR parse_confidence='low')
              AND direction NOT IN ('payment','transfer','cancelled')
        """, (category, subcategory, _is_transfer_target, matcher))
    else:
        cur = conn.execute(
            "UPDATE transactions SET category=?, subcategory=?, is_transfer=?, "
            "category_source='user_bulk', category_rule_id=NULL, "
            "category_explanation='Bulk category decision by user.', "
            "category_updated_at=datetime('now','localtime'), "
            "manually_edited_at=datetime('now','localtime') "
            "WHERE merchant_normalized=? "
            "AND direction NOT IN ('payment','transfer','cancelled')",
            (category, subcategory, _is_transfer_target, matcher),
        )
    n = cur.rowcount
    if close:
        conn.commit()
        conn.close()
    return n


# ─────────────────────────────────────────────
# Pass 16: explicit-IDs apply for Review force / safe save
# ─────────────────────────────────────────────
#
# Why this exists
# ───────────────
# `apply_category_to_merchant` updates by `merchant=?` but reports `cur.rowcount`
# which (a) doesn't tell you which IDs changed, and (b) can't combine with a
# flag-clear in a way the UI can verify per-row.
#
# Manual testing (Pass 15): user clicks `⚠ Save + Force apply (all 12)` with
# **Clear flag** checked, expecting all matching rows to come out of the
# Review queue. The DB layer correctly updated all 12 categories, but the flag
# was only cleared on the row whose Save button was clicked. The other 6
# flagged rows stayed in the queue. To the user, force apply
# "didn't work."
#
# Fix: a single helper that takes the explicit list of transaction IDs to act
# on, performs UPDATE … WHERE id IN (…), and reports how many rows changed
# category and how many flags were cleared. The Review page computes the ID
# list from the same SELECT it uses to display "(all N matching)" so the
# button promise and the DB action can never disagree.
def get_merchant_transaction_ids(merchant_normalized: str,
                                 *,
                                 only_uncertain: bool = False,
                                 conn=None) -> list[int]:
    """Return the IDs of all transactions matching this merchant. If
    `only_uncertain=True`, only rows that are NULL/empty/Misc/low-confidence
    are returned — the rows safe to overwrite without losing user work.
    """
    from utils.categorizer import normalize_merchant
    matcher = normalize_merchant(merchant_normalized)
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if only_uncertain:
        rows = conn.execute(
            "SELECT id FROM transactions WHERE merchant_normalized=? "
            "AND (category IS NULL OR category='' OR category='Misc' "
            "OR parse_confidence='low') "
            "AND direction NOT IN ('payment','transfer','cancelled') "
            "ORDER BY transaction_date DESC",
            (matcher,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM transactions WHERE merchant_normalized=? "
            "AND direction NOT IN ('payment','transfer','cancelled') "
            "ORDER BY transaction_date DESC",
            (matcher,),
        ).fetchall()
    out = [int(r["id"]) for r in rows]
    if close:
        conn.close()
    return out


def apply_category_by_ids(tx_ids: list[int], category: str,
                          subcategory: Optional[str] = None,
                          *,
                          clear_flags: bool = False,
                          flag_reason_when_cleared: str = "reviewed",
                          category_source: str = "user_bulk",
                          category_explanation: str = "Bulk category decision by user.",
                          allow_protected: bool = False,
                          conn=None) -> dict:
    """Apply `category` (and optionally clear flags) on an explicit ID list.

    Returns a dict with verified post-update counts:
        {
          'requested':         int   # length of tx_ids passed in
          'now_with_category': int   # rows where category == target after update
          'flags_cleared':     int   # rows that were is_flagged=1 AND now is 0
          'is_transfer_synced':int   # rows where is_transfer matches expected
        }

    Why verify by re-SELECTing instead of trusting cur.rowcount: SQLite's
    rowcount counts matched rows, which includes rows whose category was
    already the target. The user wants to see the *semantic* count.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    if not tx_ids:
        out = {"requested": 0, "now_with_category": 0,
               "flags_cleared": 0, "is_transfer_synced": 0,
               "skipped_protected": 0}
        if close:
            conn.close()
        return out

    # Remove duplicates while preserving the UI's selection order.
    tx_ids = list(dict.fromkeys(int(i) for i in tx_ids))
    skipped_protected = 0
    if not allow_protected:
        input_marks = ",".join(["?"] * len(tx_ids))
        states = conn.execute(
            f"SELECT id, direction, category FROM transactions "
            f"WHERE id IN ({input_marks})", tx_ids,
        ).fetchall()
        protected_categories = {
            "Transfer", "Internal Transfer", "Credit Card Payment", "Payment",
            "Cancelled", "Refund / Credit",
            "Reimbursement / Insurance Reimbursement", "Fees / Interest",
            "Cash Advance", "Interest Income", "Rewards / Cashback",
        }
        protected_ids = {
            int(r["id"]) for r in states
            if r["direction"] in ("payment", "transfer", "cancelled")
            or r["category"] in protected_categories
        }
        skipped_protected = len(protected_ids)
        tx_ids = [i for i in tx_ids if i not in protected_ids]
        if not tx_ids:
            out = {"requested": 0, "now_with_category": 0,
                   "flags_cleared": 0, "is_transfer_synced": 0,
                   "skipped_protected": skipped_protected}
            if close:
                conn.close()
            return out

    expected_xfer = 1 if category in (
        "Transfer", "Credit Card Payment", "Payment", "Cancelled"
    ) else 0

    # Build placeholder string for IN-clause. SQLite allows up to ~999 params,
    # which is well above any realistic merchant row count.
    placeholders = ",".join(["?"] * len(tx_ids))

    # Snapshot which IDs were flagged BEFORE the update so we can verify the
    # exact number of flags cleared (vs. counting "currently un-flagged" which
    # could include rows that were never flagged in the first place).
    if clear_flags:
        before_rows = conn.execute(
            f"SELECT id, is_flagged FROM transactions WHERE id IN ({placeholders})",
            tx_ids,
        ).fetchall()
        was_flagged = {int(r["id"]) for r in before_rows if int(r["is_flagged"] or 0) == 1}
    else:
        was_flagged = set()

    # Single UPDATE. `is_transfer` is synced from the new category so spending
    # queries pick the row up immediately. Flags are cleared atomically when
    # requested.
    #
    # Pass 34a hotfix: when the user clears the flag we also promote
    # parse_confidence away from 'low' to 'high'. Background: a row whose
    # category is now real but whose parse_confidence is still 'low' keeps
    # showing up in get_ai_candidates() and the Review "Uncategorized /
    # low-confidence" filter — it appears the row never went away. The
    # category column is a manual review action, so the right semantic is
    # "this row was confirmed by a human" → high confidence. We only touch
    # parse_confidence under clear_flags=True so non-flag-clearing bulk
    # apply paths (rare) keep their pre-existing confidence.
    if clear_flags:
        conn.execute(
            f"UPDATE transactions SET category=?, subcategory=?, "
            f"is_transfer=?, is_flagged=0, flag_reason=?, "
            f"parse_confidence='high', category_source=?, category_rule_id=NULL, "
            f"category_explanation=?, category_updated_at=datetime('now','localtime'), "
            f"manually_edited_at=datetime('now','localtime') "
            f"WHERE id IN ({placeholders})",
            [category, subcategory, expected_xfer, flag_reason_when_cleared,
             category_source, category_explanation, *tx_ids],
        )
    else:
        conn.execute(
            f"UPDATE transactions SET category=?, subcategory=?, is_transfer=?, "
            f"category_source=?, category_rule_id=NULL, category_explanation=?, "
            f"category_updated_at=datetime('now','localtime'), "
            f"manually_edited_at=datetime('now','localtime') "
            f"WHERE id IN ({placeholders})",
            [category, subcategory, expected_xfer, category_source,
             category_explanation, *tx_ids],
        )

    # Verification pass — re-read the same IDs and count semantic outcomes.
    after_rows = conn.execute(
        f"SELECT id, category, is_flagged, is_transfer FROM transactions "
        f"WHERE id IN ({placeholders})",
        tx_ids,
    ).fetchall()

    now_with_category = sum(1 for r in after_rows if r["category"] == category)
    is_transfer_synced = sum(1 for r in after_rows
                              if int(r["is_transfer"] or 0) == expected_xfer)
    flags_cleared = sum(
        1 for r in after_rows
        if int(r["id"]) in was_flagged and int(r["is_flagged"] or 0) == 0
    )

    out = {
        "requested":         len(tx_ids),
        "now_with_category": int(now_with_category),
        "flags_cleared":     int(flags_cleared),
        "is_transfer_synced": int(is_transfer_synced),
        "skipped_protected": int(skipped_protected),
    }
    if close:
        conn.commit()
        conn.close()
    return out


# ─────────────────────────────────────────────
# Pass 19 — investments snapshot helpers
# ─────────────────────────────────────────────
#
# These helpers wrap the new investment_snapshot_batches /
# investment_positions tables. They are intentionally additive — the
# legacy `investments` table and its helpers (`get_investments`,
# `upsert_investment`) are untouched so manually-entered holdings keep
# working exactly as before.

def insert_investment_snapshot(batch: dict, positions: list[dict],
                               conn: Optional[sqlite3.Connection] = None) -> int:
    """Insert a snapshot batch + its position rows in one transaction.
    Returns the new batch_id. Caller commits.

    Required batch keys: as_of_date.
    Optional:            source_file, file_hash, notes, currencies_seen,
                         mixed_currency, total_market_value_native, row_count.
    Each position dict is column-aligned with investment_positions
    (any missing columns become NULL).
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    # Auto-fill row_count + total if not provided.
    batch = dict(batch)
    batch.setdefault("row_count", len(positions))
    if "total_market_value_native" not in batch:
        batch["total_market_value_native"] = sum(
            (p.get("market_value") or 0) for p in positions
        )

    cols = ", ".join(batch.keys())
    qmarks = ", ".join(["?"] * len(batch))
    cur = conn.execute(
        f"INSERT INTO investment_snapshot_batches ({cols}) VALUES ({qmarks})",
        list(batch.values()),
    )
    batch_id = cur.lastrowid

    pos_columns = [
        "snapshot_batch_id", "account_name", "account_type",
        "account_number_masked", "ticker", "exchange", "security_name",
        "security_type", "quantity", "market_price", "market_price_currency",
        "book_value_cad", "book_value_market", "market_value",
        "market_value_currency", "unrealized_return",
        "unrealized_return_currency", "position_direction",
    ]
    for p in positions:
        row = [batch_id] + [p.get(c) for c in pos_columns[1:]]
        qm = ", ".join(["?"] * len(pos_columns))
        conn.execute(
            f"INSERT INTO investment_positions ({', '.join(pos_columns)}) "
            f"VALUES ({qm})",
            row,
        )

    if close:
        conn.commit()
        conn.close()
    return batch_id


def get_investment_snapshots(conn: Optional[sqlite3.Connection] = None,
                             limit: int = 50) -> list[dict]:
    """Return snapshot batches ordered most-recent first."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute(
        "SELECT * FROM investment_snapshot_batches "
        "ORDER BY as_of_date DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def get_latest_investment_snapshot(conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """Return the most-recent snapshot batch + its positions, or None."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM investment_snapshot_batches "
        "ORDER BY as_of_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if not row:
        if close: conn.close()
        return None
    batch = dict(row)
    pos = conn.execute(
        "SELECT * FROM investment_positions WHERE snapshot_batch_id=? "
        "ORDER BY market_value DESC NULLS LAST",
        (batch["id"],),
    ).fetchall()
    batch["positions"] = [dict(r) for r in pos]
    if close:
        conn.close()
    return batch


def delete_investment_snapshot(batch_id: int,
                               conn: Optional[sqlite3.Connection] = None) -> int:
    """Delete a snapshot batch + its positions. Returns deleted position count."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cur = conn.execute(
        "DELETE FROM investment_positions WHERE snapshot_batch_id=?",
        (int(batch_id),),
    )
    n = cur.rowcount
    conn.execute(
        "DELETE FROM investment_snapshot_batches WHERE id=?", (int(batch_id),),
    )
    if close:
        conn.commit()
        conn.close()
    return n


# ─────────────────────────────────────────────
# Pass 19 — account balance + net worth helpers
# ─────────────────────────────────────────────

# Map account_kind → asset/liability for net-worth math. Anything not in
# this dict is treated as "asset" (defensive default) but with a warning
# at compute time.
ASSET_KINDS = {
    "cash", "chequing", "savings", "investment", "other_asset",
}
LIABILITY_KINDS = {
    "credit_card", "loan", "mortgage", "other_liability",
}


def insert_account_balance(rec: dict, conn: Optional[sqlite3.Connection] = None) -> int:
    """Insert one account balance snapshot. Returns the new id.

    Required keys: account_name, account_kind, balance, as_of_date.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cols = ", ".join(rec.keys())
    qm = ", ".join(["?"] * len(rec))
    cur = conn.execute(
        f"INSERT INTO account_balances ({cols}) VALUES ({qm})",
        list(rec.values()),
    )
    new_id = cur.lastrowid
    if close:
        conn.commit()
        conn.close()
    return new_id


def get_account_balances(conn: Optional[sqlite3.Connection] = None,
                         latest_only: bool = True) -> list[dict]:
    """Return account balances. If latest_only, only the most-recent row
    per linked account is returned. Legacy unlinked snapshots fall back to
    their saved name and kind."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if latest_only:
        # Newest as_of_date per account, with id only as a tie-breaker.
        #
        # This used to be MAX(id), which is the order rows were *typed*, not
        # the date they are about. Recording July and then backfilling
        # January made January the latest balance, so every screen reading
        # this — the net worth prefill included — silently went backwards by
        # six months. The grouping is unchanged, so linked accounts still
        # group by account_ref and legacy unlinked rows by name and kind.
        rows = conn.execute("""
            SELECT * FROM account_balances ab
            WHERE ab.id = (
                SELECT other.id FROM account_balances other
                WHERE CASE
                        WHEN other.account_ref IS NOT NULL
                            THEN 'id:' || other.account_ref
                        ELSE 'name:' || LOWER(other.account_name)
                             || ':' || other.account_kind
                      END
                    = CASE
                        WHEN ab.account_ref IS NOT NULL
                            THEN 'id:' || ab.account_ref
                        ELSE 'name:' || LOWER(ab.account_name)
                             || ':' || ab.account_kind
                      END
                ORDER BY other.as_of_date DESC, other.id DESC
                LIMIT 1
            )
            ORDER BY account_kind, account_name
        """).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM account_balances ORDER BY as_of_date DESC, id DESC"
        ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def delete_account_balance(bal_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM account_balances WHERE id=?", (int(bal_id),))
    if close:
        conn.commit()
        conn.close()


def compute_net_worth_now(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Compute current net worth from latest account_balances + latest
    investment snapshot. Read-only — does NOT insert a snapshot row.

    Returns:
        {
          total_assets, total_liabilities, net_worth,
          breakdown:  [{label, kind, value, currency}],
          as_of_date: latest contributing date,
          mixed_currency: bool,
          missing:    list[str]   # e.g. ["no investment snapshot"],
        }
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    breakdown: list[dict] = []
    currencies: set[str] = set()
    dates: list[str] = []
    missing: list[str] = []

    # Latest account balances (one per account).
    latest_balances = get_account_balances(conn=conn, latest_only=True)
    balanced_refs = {int(b["account_ref"]) for b in latest_balances
                     if b.get("account_ref") is not None}
    balanced_names = {str(b.get("account_name") or "").strip().lower()
                      for b in latest_balances}
    for bal in latest_balances:
        kind = (bal.get("account_kind") or "").lower()
        currencies.add((bal.get("currency") or "CAD").upper())
        if bal.get("as_of_date"):
            dates.append(bal["as_of_date"])
        breakdown.append({
            "label":    bal["account_name"],
            "kind":     kind,
            "value":    float(bal.get("balance") or 0),
            "currency": bal.get("currency") or "CAD",
            "is_asset": kind in ASSET_KINDS,
        })

    # Latest investment snapshot folded in as one composite asset row.
    snap = get_latest_investment_snapshot(conn=conn)
    if snap:
        snap_total = sum((p.get("market_value") or 0) for p in snap.get("positions") or [])
        for p in snap.get("positions") or []:
            cur = (p.get("market_value_currency") or "CAD").upper()
            currencies.add(cur)
        if snap.get("as_of_date"):
            dates.append(snap["as_of_date"])
        breakdown.append({
            "label":    f"Investments (snapshot {snap.get('as_of_date','')})",
            "kind":     "investment",
            "value":    float(snap_total or 0),
            "currency": "mixed" if snap.get("mixed_currency") else "CAD",
            "is_asset": True,
        })
    else:
        missing.append("no investment snapshot")

    if not breakdown:
        missing.append("no account balances or investments")

    unbalanced_accounts = [
        r["name"] for r in conn.execute(
            "SELECT id,name FROM accounts WHERE is_archived=0 ORDER BY name"
        ).fetchall()
        if int(r["id"]) not in balanced_refs
        and str(r["name"] or "").strip().lower() not in balanced_names
    ]
    if unbalanced_accounts:
        missing.append(f"{len(unbalanced_accounts)} account(s) missing balances")

    mixed_currency = len({c for c in currencies if c}) > 1
    configured = bool(breakdown)
    total_assets = sum(b["value"] for b in breakdown if b["is_asset"])
    total_liab   = sum(b["value"] for b in breakdown if not b["is_asset"])
    net_worth    = total_assets - total_liab

    if close:
        conn.close()

    return {
        "total_assets":      float(total_assets),
        "total_liabilities": float(total_liab),
        "net_worth":         float(net_worth),
        "breakdown":         breakdown,
        "as_of_date":        max(dates) if dates else "",
        "mixed_currency":    len({c for c in currencies if c}) > 1,
        "currencies":        sorted(currencies),
        "missing":           missing,
        "configured":        configured,
        "calculated":        configured and not mixed_currency,
        "status": ("mixed_currency" if mixed_currency else
                   "partial" if configured and unbalanced_accounts else
                   "configured" if configured else "not_configured"),
        "missing_account_count": len(unbalanced_accounts),
    }


def insert_net_worth_snapshot(snap: dict,
                              conn: Optional[sqlite3.Connection] = None) -> int:
    """Insert a net_worth_snapshots row. Caller commits."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if "source_breakdown" in snap and not isinstance(snap["source_breakdown"], str):
        snap = dict(snap)
        snap["source_breakdown"] = json.dumps(snap["source_breakdown"])
    cols = ", ".join(snap.keys())
    qm = ", ".join(["?"] * len(snap))
    cur = conn.execute(
        f"INSERT INTO net_worth_snapshots ({cols}) VALUES ({qm})",
        list(snap.values()),
    )
    new_id = cur.lastrowid
    if close:
        conn.commit()
        conn.close()
    return new_id


def get_net_worth_snapshots(conn: Optional[sqlite3.Connection] = None,
                            limit: int = 200) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute(
        "SELECT * FROM net_worth_snapshots "
        "ORDER BY as_of_date ASC, id ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


# ─────────────────────────────────────────────
# Pass 21 — monthly plans / budget targets / goals
# ─────────────────────────────────────────────

def _utc_timestamp() -> str:
    """One unambiguous timestamp shape for newly persisted plan versions."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def upsert_monthly_plan(plan: dict, conn: Optional[sqlite3.Connection] = None) -> int:
    """Insert or update a plan for a month. Returns the plan id.

    Required keys: month ('YYYY-MM'). Other fields optional.
    Uses UNIQUE(month) so re-saving updates instead of duplicating.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    plan = dict(plan)
    stamp = _utc_timestamp()
    # The old path let SQLite create `created_at` in UTC while Python wrote
    # `updated_at` in local time.  Near a month boundary the two stamps could
    # describe the same save on different calendar dates.  New plan versions
    # carry an explicit UTC marker for both fields; existing `created_at`
    # values remain untouched on conflict.
    plan.setdefault("created_at", stamp)
    plan["updated_at"] = stamp
    cols = ", ".join(plan.keys())
    qm = ", ".join(["?"] * len(plan))
    updates = ", ".join(f"{k}=excluded.{k}" for k in plan.keys()
                        if k not in ("id", "created_at", "month"))
    conn.execute(
        f"INSERT INTO monthly_plans ({cols}) VALUES ({qm}) "
        f"ON CONFLICT(month) DO UPDATE SET {updates}",
        list(plan.values()),
    )
    row = conn.execute(
        "SELECT id FROM monthly_plans WHERE month=?", (plan["month"],),
    ).fetchone()
    plan_id = int(row["id"]) if row else 0
    if close:
        conn.commit()
        conn.close()
    return plan_id


def get_monthly_plan(month: str, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """Return the plan for `month` (YYYY-MM) with category_targets joined."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM monthly_plans WHERE month=?", (month,),
    ).fetchone()
    if not row:
        if close: conn.close()
        return None
    plan = dict(row)
    targets = conn.execute(
        "SELECT * FROM category_budget_targets WHERE monthly_plan_id=? "
        "ORDER BY target_amount DESC",
        (plan["id"],),
    ).fetchall()
    plan["category_targets"] = [dict(t) for t in targets]
    if close:
        conn.close()
    return plan


def get_applicable_plan(month: str,
                        conn: Optional[sqlite3.Connection] = None
                        ) -> Optional[dict]:
    """The plan that applies to `month`: the month's own plan when one
    exists, otherwise the most recently saved plan (statements lag the
    calendar, so the newest saved plan is the user's standing intent).

    Every engine that reads a plan (Safe to Spend, progress, watchlist
    targets, recommendations) should use THIS so they all agree.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    plan = get_monthly_plan(month, conn=conn)
    if plan is None:
        row = conn.execute(
            "SELECT month FROM monthly_plans ORDER BY month DESC LIMIT 1"
        ).fetchone()
        if row:
            plan = get_monthly_plan(row["month"], conn=conn)
    if close:
        conn.close()
    return plan


def list_monthly_plans(conn: Optional[sqlite3.Connection] = None,
                       limit: int = 12) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    rows = conn.execute(
        "SELECT * FROM monthly_plans ORDER BY month DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def replace_category_targets(plan_id: int, targets: list[dict],
                             conn: Optional[sqlite3.Connection] = None) -> int:
    """Replace all targets for a plan with the given list. Returns count.

    Each target dict needs: category, target_amount; optional basis,
    difficulty.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "DELETE FROM category_budget_targets WHERE monthly_plan_id=?",
        (int(plan_id),),
    )
    n = 0
    for t in targets:
        conn.execute(
            "INSERT INTO category_budget_targets "
            "(monthly_plan_id, category, target_amount, basis, difficulty) "
            "VALUES (?,?,?,?,?)",
            (int(plan_id), t["category"], float(t["target_amount"]),
             t.get("basis"), t.get("difficulty")),
        )
        n += 1
    if close:
        conn.commit()
        conn.close()
    return n


def insert_goal(goal: dict, conn: Optional[sqlite3.Connection] = None) -> int:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cols = ", ".join(goal.keys())
    qm = ", ".join(["?"] * len(goal))
    cur = conn.execute(
        f"INSERT INTO goal_targets ({cols}) VALUES ({qm})",
        list(goal.values()),
    )
    new_id = cur.lastrowid
    if close:
        conn.commit()
        conn.close()
    return new_id


def get_goals(conn: Optional[sqlite3.Connection] = None,
              status: Optional[str] = "active") -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if status:
        rows = conn.execute(
            "SELECT * FROM goal_targets WHERE status=? "
            "ORDER BY created_at DESC", (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM goal_targets ORDER BY created_at DESC"
        ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def spending_balance_summary(
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Split current balances into spendable cash, protected assets, and cards.

    A linked account's persisted ``available_for_spending`` choice is
    authoritative. Legacy unlinked balance snapshots use the same conservative
    defaults as new accounts: chequing and cash are available; savings,
    investments, and other assets are protected. Liability balances never
    become spendable.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        balances = get_account_balances(conn=conn, latest_only=True)
        account_rows = conn.execute(
            "SELECT id,name,type,available_for_spending FROM accounts"
        ).fetchall()
        accounts = {int(row["id"]): dict(row) for row in account_rows}
        available_total = 0.0
        excluded_total = 0.0
        card_liability = 0.0
        details: list[dict] = []
        for balance in balances:
            ref = balance.get("account_ref")
            account = accounts.get(int(ref)) if ref is not None else None
            kind = str(
                (account or {}).get("type")
                or balance.get("account_kind")
                or "other_asset"
            ).lower()
            value = float(balance.get("balance") or 0)
            if kind == "credit_card":
                card_liability += abs(value)
                available = False
            elif kind in LIABILITY_KINDS:
                available = False
            elif account is not None:
                available = bool(int(
                    account.get("available_for_spending") or 0
                ))
            else:
                available = kind in DEFAULT_SPENDABLE_ACCOUNT_TYPES

            positive_value = max(0.0, value)
            if kind in ASSET_KINDS:
                if available:
                    available_total += positive_value
                else:
                    excluded_total += positive_value
            details.append({
                "account_ref": int(ref) if ref is not None else None,
                "account_name": (
                    (account or {}).get("name")
                    or balance.get("account_name")
                    or "Account"
                ),
                "account_kind": kind,
                "balance": round(value, 2),
                "available_for_spending": available,
            })
        return {
            "available_balance": round(available_total, 2),
            "excluded_balance": round(excluded_total, 2),
            "credit_card_liability": round(card_liability, 2),
            "accounts": details,
        }
    finally:
        if close:
            conn.close()


def balance_reconciliation(
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Reconcile required spending accounts with their latest balances.

    Safe to Spend needs one current snapshot for every active chequing and
    credit-card account. A snapshot is current only through its ``as_of_date``;
    any newer transaction on that account reopens reconciliation. Savings and
    investment balances remain optional because they are not spending inputs.

    Older databases without first-class account rows are handled conservatively
    from their unlinked balance names/kinds and legacy transaction account
    types. This keeps the migration readable without pretending a missing card
    snapshot is a zero-dollar liability.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        from utils.analysis_period import supported_latest_date

        supported = supported_latest_date(conn=conn)
        supported_iso = supported.isoformat() if supported else "0000-00-00"
        latest_balances = get_account_balances(conn=conn, latest_only=True)
        active = list_accounts(conn=conn)
        required = [
            account for account in active
            if str(account.get("type") or "").lower()
            in {"chequing", "credit_card"}
        ]
        items: list[dict] = []

        if required:
            by_ref = {
                int(row["account_ref"]): row
                for row in latest_balances
                if row.get("account_ref") is not None
            }
            for account in required:
                account_id = int(account["id"])
                balance = by_ref.get(account_id)
                as_of = str((balance or {}).get("as_of_date") or "")
                last_activity = str(account.get("last_activity") or "")
                newer_import = bool(
                    as_of and last_activity and last_activity > as_of
                )
                status = (
                    "missing" if not balance
                    else "out_of_date" if newer_import
                    else "current"
                )
                items.append({
                    "account_ref": account_id,
                    "account_name": account.get("name") or "Account",
                    "account_kind": account.get("type") or "",
                    "as_of_date": as_of,
                    "last_activity": last_activity,
                    "status": status,
                    "ready": status == "current",
                    "imports_newer_than_balance": newer_import,
                })
            active_kinds = {
                str(account.get("type") or "").lower()
                for account in required
            }
            unlinked_kinds = {
                str(row[0] or "").lower()
                for row in conn.execute(
                    "SELECT DISTINCT account_type FROM transactions "
                    "WHERE account_ref IS NULL"
                ).fetchall()
            }
            if "mastercard" in unlinked_kinds:
                unlinked_kinds.add("credit_card")
            for kind in ("chequing", "credit_card"):
                if kind in active_kinds or kind not in unlinked_kinds:
                    continue
                aliases = (
                    {kind, "mastercard"}
                    if kind == "credit_card" else {kind}
                )
                candidates = [
                    row for row in latest_balances
                    if row.get("account_ref") is None
                    and str(row.get("account_kind") or "").lower() in aliases
                ]
                balance = max(
                    candidates,
                    key=lambda row: (
                        str(row.get("as_of_date") or ""),
                        int(row.get("id") or 0),
                    ),
                    default=None,
                )
                placeholders = ",".join("?" for _ in aliases)
                last = conn.execute(
                    f"SELECT MAX(transaction_date) FROM transactions "
                    f"WHERE account_ref IS NULL "
                    f"AND LOWER(account_type) IN ({placeholders}) "
                    f"AND transaction_date <= ?",
                    (*tuple(sorted(aliases)), supported_iso),
                ).fetchone()[0]
                as_of = str((balance or {}).get("as_of_date") or "")
                last_activity = str(last or "")
                newer_import = bool(
                    as_of and last_activity and last_activity > as_of
                )
                status = (
                    "missing" if not balance
                    else "out_of_date" if newer_import else "current"
                )
                items.append({
                    "account_ref": None,
                    "account_name": (
                        (balance or {}).get("account_name")
                        or ("Credit card" if kind == "credit_card" else "Chequing")
                    ),
                    "account_kind": kind,
                    "as_of_date": as_of,
                    "last_activity": last_activity,
                    "status": status,
                    "ready": status == "current",
                    "imports_newer_than_balance": newer_import,
                })
        else:
            # Legacy fallback: infer only the two required account kinds. A
            # card with transactions but no matching balance remains missing.
            legacy_kinds = {
                str(row[0] or "").lower()
                for row in conn.execute(
                    "SELECT DISTINCT account_type FROM transactions"
                ).fetchall()
            }
            if "mastercard" in legacy_kinds:
                legacy_kinds.add("credit_card")
            required_kinds = [
                kind for kind in ("chequing", "credit_card")
                if kind in legacy_kinds or any(
                    str(row.get("account_kind") or "").lower() == kind
                    for row in latest_balances
                )
            ]
            for kind in required_kinds:
                aliases = {kind, "mastercard"} if kind == "credit_card" else {kind}
                candidates = [
                    row for row in latest_balances
                    if str(row.get("account_kind") or "").lower() in aliases
                ]
                balance = max(
                    candidates,
                    key=lambda row: (
                        str(row.get("as_of_date") or ""), int(row.get("id") or 0)
                    ),
                    default=None,
                )
                placeholders = ",".join("?" for _ in aliases)
                last = conn.execute(
                    f"SELECT MAX(transaction_date) FROM transactions "
                    f"WHERE LOWER(account_type) IN ({placeholders}) "
                    f"AND transaction_date <= ?",
                    (*tuple(sorted(aliases)), supported_iso),
                ).fetchone()[0]
                as_of = str((balance or {}).get("as_of_date") or "")
                last_activity = str(last or "")
                newer_import = bool(
                    as_of and last_activity and last_activity > as_of
                )
                status = (
                    "missing" if not balance
                    else "out_of_date" if newer_import
                    else "current"
                )
                items.append({
                    "account_ref": None,
                    "account_name": (
                        (balance or {}).get("account_name")
                        or ("Credit card" if kind == "credit_card" else "Chequing")
                    ),
                    "account_kind": kind,
                    "as_of_date": as_of,
                    "last_activity": last_activity,
                    "status": status,
                    "ready": status == "current",
                    "imports_newer_than_balance": newer_import,
                })

        missing = [item for item in items if item["status"] == "missing"]
        out_of_date = [
            item for item in items if item["status"] == "out_of_date"
        ]
        return {
            "ready": bool(items) and not missing and not out_of_date,
            "required_count": len(items),
            "current_count": sum(1 for item in items if item["ready"]),
            "missing_count": len(missing),
            "out_of_date_count": len(out_of_date),
            "imports_newer_than_balance": bool(out_of_date),
            "accounts": items,
        }
    finally:
        if close:
            conn.close()


def get_goal(goal_id: int,
             conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM goal_targets WHERE id=?", (int(goal_id),),
    ).fetchone()
    if close:
        conn.close()
    return dict(row) if row else None


def update_goal(goal_id: int, updates: dict,
                conn: Optional[sqlite3.Connection] = None) -> None:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    updates = dict(updates)
    # UTC, matching the column default. Kept in the unmarked
    # "YYYY-MM-DD HH:MM:SS" shape because get_goals orders by created_at as
    # text, and a second shape would sort ahead of every existing row.
    updates["updated_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    sets = ", ".join(f"{k}=?" for k in updates.keys())
    conn.execute(
        f"UPDATE goal_targets SET {sets} WHERE id=?",
        list(updates.values()) + [int(goal_id)],
    )
    if close:
        conn.commit()
        conn.close()


def delete_goal(goal_id: int,
                conn: Optional[sqlite3.Connection] = None) -> None:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM goal_targets WHERE id=?", (int(goal_id),))
    if close:
        conn.commit()
        conn.close()


_CURRENT_TABLES = {
    "transactions", "investments", "contributions", "budgets",
    "import_log", "profiles", "score_weights", "watch_list",
    "recommendations_log", "learned_rules", "recurring_preferences", "category_change_undo", "investment_snapshot_batches",
    "investment_positions", "net_worth_snapshots", "account_balances",
    "statement_summaries", "monthly_plans", "category_budget_targets",
    "goal_targets", "review_log", "accounts", "import_profiles",
    "income_source_preferences", "net_worth_estimates", "net_worth_entries",
    "shared_expense_settings", "shared_expense_rules",
    "goal_contributions", "data_repair_log", "app_migrations",
}

_CURRENT_COLUMNS = {
    "transactions": {
        "merchant_normalized", "category_source", "ai_suggested_category",
        "account_ref", "shared_expense_override", "shared_user_share_pct",
    },
    "learned_rules": {"enabled", "source_import_batch_id"},
    "recurring_preferences": {"display_name", "category"},
    "import_profiles": {"suggested_account_ref"},
    "import_log": {"semantics_version"},
    "account_balances": {"account_ref"},
    "goal_targets": {
        "progress_method", "linked_account_ref",
        "planned_monthly_contribution", "contribution_frequency",
        "include_in_plan", "currency",
        "completed_at",
    },
    "monthly_plans": {
        "fixed_obligations", "flexible_allowance", "safety_buffer",
    },
}


def _schema_requires_migration(path) -> bool:
    """Return True when an existing SpendShape DB predates the current schema.

    This is intentionally read-only and conservative. A database containing
    any Ledger-era tables but missing a current table or additive column gets
    a recovery point before ``init_db`` changes it.
    """
    path = path.resolve()
    if not path.exists() or path.stat().st_size == 0:
        return False
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if not tables:
            return False
        if not _CURRENT_TABLES.issubset(tables):
            return True
        for table, required in _CURRENT_COLUMNS.items():
            columns = {
                row[1] for row in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if not required.issubset(columns):
                return True
        migration = conn.execute(
            "SELECT 1 FROM app_migrations WHERE migration_key=?",
            (_BUILTIN_CATEGORIZATION_MIGRATION,),
        ).fetchone()
        if migration is None:
            return True
        investment_migration = conn.execute(
            "SELECT 1 FROM app_migrations WHERE migration_key=?",
            (_INVESTMENT_SEMANTICS_MIGRATION,),
        ).fetchone()
        if investment_migration is None:
            return True
        for index in conn.execute(
            "PRAGMA index_list(recommendations_log)"
        ).fetchall():
            if index[2]:
                cols = conn.execute(
                    f"PRAGMA index_info({index[1]})"
                ).fetchall()
                if len(cols) == 1 and cols[0][2] == "rec_key":
                    return False
        return True
    except sqlite3.DatabaseError:
        # Let the normal init/open path surface the corruption clearly. A raw
        # file copy here could create false confidence in an invalid backup.
        return False
    finally:
        if "conn" in locals():
            conn.close()


def record_goal_contribution(goal_id: int, amount: float,
                             contributed_on: Optional[str] = None,
                             notes: str = "",
                             conn: Optional[sqlite3.Connection] = None) -> int:
    """Record a positive manual contribution and advance manual progress.

    The log row and goal total update are one transaction. Linked-account
    goals reject manual contributions so progress sources never get combined
    silently.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    amount = float(amount)
    if amount <= 0:
        if close:
            conn.close()
        raise ValueError("Contribution amount must be greater than zero.")
    goal = get_goal(goal_id, conn=conn)
    if not goal:
        if close:
            conn.close()
        raise ValueError("Goal not found.")
    method = (goal.get("progress_method") or "manual").strip().lower()
    if method != "manual":
        if close:
            conn.close()
        raise ValueError(
            "Manual contributions are only available for manual-progress goals."
        )
    day = contributed_on or date.today().isoformat()
    # Validate before the savepoint so malformed dates fail without writes.
    date.fromisoformat(day)
    conn.execute("SAVEPOINT goal_contribution")
    try:
        cur = conn.execute(
            "INSERT INTO goal_contributions "
            "(goal_id, amount, contributed_on, notes) VALUES (?,?,?,?)",
            (int(goal_id), amount, day, (notes or "").strip()),
        )
        conn.execute(
            "UPDATE goal_targets SET current_amount="
            "COALESCE(current_amount,0)+?, updated_at=datetime('now') "
            "WHERE id=?",
            (amount, int(goal_id)),
        )
        conn.execute("RELEASE SAVEPOINT goal_contribution")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT goal_contribution")
        conn.execute("RELEASE SAVEPOINT goal_contribution")
        if close:
            conn.close()
        raise
    if close:
        conn.commit()
        conn.close()
    return int(cur.lastrowid)


def list_goal_contributions(goal_id: Optional[int] = None,
                            conn: Optional[sqlite3.Connection] = None
                            ) -> list[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if goal_id is None:
        rows = conn.execute(
            "SELECT * FROM goal_contributions "
            "ORDER BY contributed_on DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM goal_contributions WHERE goal_id=? "
            "ORDER BY contributed_on DESC, id DESC",
            (int(goal_id),),
        ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def verify_merchant_category(merchant_normalized: str, category: str,
                             conn=None) -> dict:
    """Read-back verification used by the Review page after a force / safe
    update. Returns the actual current state — `cur.rowcount` is unreliable for
    "rows that semantically changed" (SQLite counts every matched row, even if
    its category was already the new value). The Save handler shows the user
    `actual_with_category` so the displayed count matches the database.

    Returns:
        {
          'total':                int  total rows for this merchant
          'with_category':        int  rows where category == target after commit
          'without_category':     int  rows where category != target after commit
          'is_transfer_mismatch': int  rows where is_transfer doesn't match expected
        }
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    expected_xfer = 1 if category in (
        "Transfer", "Credit Card Payment", "Payment", "Cancelled"
    ) else 0
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN category = ? THEN 1 ELSE 0 END) AS with_category,
            SUM(CASE WHEN category != ? OR category IS NULL THEN 1 ELSE 0 END) AS without_category,
            SUM(CASE WHEN category = ? AND is_transfer != ? THEN 1 ELSE 0 END) AS is_transfer_mismatch
        FROM transactions WHERE merchant=?
    """, (category, category, category, expected_xfer, merchant_normalized)).fetchone()
    out = {
        "total":                int(row["total"] or 0),
        "with_category":        int(row["with_category"] or 0),
        "without_category":     int(row["without_category"] or 0),
        "is_transfer_mismatch": int(row["is_transfer_mismatch"] or 0),
    }
    if close:
        conn.close()
    return out


# ─────────────────────────────────────────────
# Weekly check-in history (Home page)
# ─────────────────────────────────────────────
def insert_checkin(entry: dict, conn: Optional[sqlite3.Connection] = None) -> int:
    """Record a completed weekly check-in. Returns the new row id.

    Expected keys: planning_period, data_through, safe_to_spend,
    primary_action, notes (all optional except none — a bare completion
    with just the timestamp is still valid).
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cursor = conn.execute(
        """
        INSERT INTO review_log
            (planning_period, data_through, safe_to_spend,
             primary_action, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            entry.get("planning_period"),
            entry.get("data_through"),
            entry.get("safe_to_spend"),
            entry.get("primary_action"),
            entry.get("notes"),
        ),
    )
    conn.commit()
    rid = cursor.lastrowid
    if close:
        conn.close()
    return rid


def get_last_checkin(conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """Most recent completed check-in, or None."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM review_log ORDER BY completed_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if close:
        conn.close()
    return dict(row) if row else None


# ─────────────────────────────────────────────
# Accounts (v8)
# ─────────────────────────────────────────────
# Legacy transactions.account_type strings → migrated-account definitions.
# Deliberately generic names: the backfill never guesses institutions.
_MIGRATED_ACCOUNT_MAP = {
    "chequing":   ("Chequing (migrated)",    "chequing"),
    "savings":    ("Savings (migrated)",     "savings"),
    "mastercard": ("Credit card (migrated)", "credit_card"),
    "cash":       ("Cash (migrated)",        "cash"),
}
_MIGRATED_FALLBACK = ("Imported (migrated)", "other_asset")

ACCOUNT_TYPES = [
    "chequing", "savings", "cash", "credit_card",
    "loan", "mortgage", "investment", "other_asset", "other_liability",
]
DEFAULT_SPENDABLE_ACCOUNT_TYPES = {"chequing", "cash"}
ACCOUNT_TYPE_LABELS = {
    "chequing":        "Chequing",
    "savings":         "Savings",
    "cash":            "Cash",
    "credit_card":     "Credit card",
    "loan":            "Loan / line of credit",
    "mortgage":        "Mortgage",
    "investment":      "Investment",
    "other_asset":     "Other asset",
    "other_liability": "Other debt",
}
LIABILITY_ACCOUNT_TYPES = {"credit_card", "loan", "mortgage",
                           "other_liability"}


def _migrate_accounts_schema(conn) -> None:
    """Atomically add account_ref columns and backfill migrated accounts.

    Idempotent: reruns are no-ops once transactions carry account_ref.
    Never touches amounts, categories, provenance, or dedup hashes of
    existing rows.
    """
    conn.execute("SAVEPOINT accounts_schema")
    try:
        _ensure_columns(conn, "accounts", [
            ("available_for_spending", "INTEGER"),
        ])
        conn.execute(
            "UPDATE accounts SET available_for_spending="
            "CASE WHEN type IN ('chequing','cash') THEN 1 ELSE 0 END "
            "WHERE available_for_spending IS NULL"
        )
        _ensure_columns(conn, "transactions", [
            ("account_ref", "INTEGER"),
        ])
        _ensure_columns(conn, "import_profiles", [
            ("suggested_account_ref", "INTEGER"),
        ])

        # Backfill: one migrated account per distinct legacy
        # account_type that still has unassigned rows.
        rows = conn.execute(
            """
            SELECT DISTINCT COALESCE(account_type, '') AS at
            FROM transactions WHERE account_ref IS NULL
            """
        ).fetchall()
        for r in rows:
            legacy = (r["at"] or "").strip().lower()
            name, acct_type = _MIGRATED_ACCOUNT_MAP.get(
                legacy, _MIGRATED_FALLBACK)
            acct = conn.execute(
                "SELECT id FROM accounts WHERE name=? AND is_migrated=1",
                (name,),
            ).fetchone()
            if acct:
                acct_id = acct["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO accounts "
                    "(name, type, available_for_spending, is_migrated) "
                    "VALUES (?, ?, ?, 1)",
                    (name, acct_type,
                     1 if acct_type in DEFAULT_SPENDABLE_ACCOUNT_TYPES else 0),
                )
                acct_id = cur.lastrowid
            conn.execute(
                "UPDATE transactions SET account_ref=? "
                "WHERE account_ref IS NULL "
                "  AND COALESCE(account_type,'') = ?",
                (acct_id, r["at"]),
            )
        conn.execute("RELEASE SAVEPOINT accounts_schema")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT accounts_schema")
        conn.execute("RELEASE SAVEPOINT accounts_schema")
        raise


def _migrate_goals_schema(conn) -> None:
    """Atomically extend the original goals table without rewriting history.

    Older goals keep their manual amount or legacy computed metric. New goals
    can link one first-class account and reserve a monthly contribution in the
    existing savings plan. The contribution log is intentionally separate so
    a manual progress update remains inspectable after an app restart.
    """
    conn.execute("SAVEPOINT goals_schema")
    try:
        _ensure_columns(conn, "goal_targets", [
            ("progress_method", "TEXT DEFAULT 'manual'"),
            ("linked_account_ref", "INTEGER"),
            ("planned_monthly_contribution", "REAL DEFAULT 0"),
            ("contribution_frequency", "TEXT DEFAULT 'monthly'"),
            ("include_in_plan", "INTEGER DEFAULT 1"),
            ("show_milestones", "INTEGER DEFAULT 1"),
            ("currency", "TEXT DEFAULT 'CAD'"),
            ("completed_at", "TEXT"),
        ])
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                contributed_on TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (goal_id) REFERENCES goal_targets(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_linked_account "
            "ON goal_targets(linked_account_ref)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_contributions_goal_date "
            "ON goal_contributions(goal_id, contributed_on)"
        )
        _backfill_goals_schema(conn)
        conn.execute("RELEASE SAVEPOINT goals_schema")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT goals_schema")
        conn.execute("RELEASE SAVEPOINT goals_schema")
        raise


def _migrate_monthly_plan_schema(conn) -> None:
    """Add the explicit inputs used by the native Plan workflow."""
    conn.execute("SAVEPOINT monthly_plan_schema")
    try:
        _ensure_columns(conn, "monthly_plans", [
            ("fixed_obligations", "REAL DEFAULT 0"),
            ("flexible_allowance", "REAL DEFAULT 0"),
            ("safety_buffer", "REAL DEFAULT 0"),
            ("detected_commitments_at_save", "REAL"),
            ("fixed_override_reason", "TEXT"),
        ])
        conn.execute("RELEASE SAVEPOINT monthly_plan_schema")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT monthly_plan_schema")
        conn.execute("RELEASE SAVEPOINT monthly_plan_schema")
        raise


def _backfill_goals_schema(conn) -> None:
    """Preserve old goal behavior while normalizing retired status names."""
    conn.execute(
        "UPDATE goal_targets SET progress_method='legacy_metric' "
        "WHERE linked_metric IS NOT NULL AND linked_metric != '' "
        "AND COALESCE(progress_method, 'manual')='manual'"
    )
    conn.execute(
        "UPDATE goal_targets SET progress_method='manual' "
        "WHERE progress_method IS NULL OR progress_method=''"
    )
    conn.execute(
        "UPDATE goal_targets SET planned_monthly_contribution=0 "
        "WHERE planned_monthly_contribution IS NULL"
    )
    conn.execute(
        "UPDATE goal_targets SET contribution_frequency='monthly' "
        "WHERE contribution_frequency IS NULL OR contribution_frequency=''"
    )
    conn.execute(
        "UPDATE goal_targets SET include_in_plan=1 "
        "WHERE include_in_plan IS NULL"
    )
    conn.execute(
        "UPDATE goal_targets SET currency='CAD' "
        "WHERE currency IS NULL OR currency=''"
    )
    conn.execute(
        "UPDATE goal_targets SET status='completed', "
        "completed_at=COALESCE(completed_at, updated_at, datetime('now')) "
        "WHERE status='done'"
    )


def create_account(account: dict,
                   conn: Optional[sqlite3.Connection] = None) -> int:
    """Create an account. Returns the new id.

    Required: name, type (one of ACCOUNT_TYPES). Optional: institution,
    currency, opening_balance, opening_balance_date.
    """
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        name = (account.get("name") or "").strip()
        acct_type = (account.get("type") or "").strip()
        if not name:
            raise ValueError("Account name is required.")
        if acct_type not in ACCOUNT_TYPES:
            raise ValueError(f"Unknown account type: {acct_type!r}")
        dup = conn.execute(
            "SELECT id FROM accounts WHERE lower(name)=lower(?) "
            "AND is_archived=0",
            (name,),
        ).fetchone()
        if dup:
            raise ValueError(
                f"An active account named '{name}' already exists.")
        cur = conn.execute(
            """
            INSERT INTO accounts (name, type, institution, currency,
                                  opening_balance, opening_balance_date,
                                  available_for_spending)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, acct_type,
                (account.get("institution") or "").strip() or None,
                (account.get("currency") or "CAD").strip().upper() or "CAD",
                float(account.get("opening_balance") or 0.0),
                account.get("opening_balance_date") or None,
                int(bool(account.get(
                    "available_for_spending",
                    acct_type in DEFAULT_SPENDABLE_ACCOUNT_TYPES,
                ))),
            ),
        )
        if close:
            conn.commit()
        return int(cur.lastrowid)
    finally:
        if close:
            conn.close()


def list_accounts(conn: Optional[sqlite3.Connection] = None,
                  include_archived: bool = False) -> list[dict]:
    """Accounts with transaction counts and latest activity."""
    from utils.analysis_period import supported_latest_date

    close = False
    if conn is None:
        conn = get_connection()
        close = True
    supported = supported_latest_date(conn=conn)
    supported_iso = supported.isoformat() if supported else "0000-00-00"
    where = "" if include_archived else "WHERE a.is_archived = 0"
    rows = conn.execute(
        f"""
        SELECT a.*,
               COUNT(t.id)               AS tx_count,
               MAX(CASE WHEN t.transaction_date <= ?
                   THEN t.transaction_date END) AS last_activity
        FROM accounts a
        LEFT JOIN transactions t ON t.account_ref = a.id
        {where}
        GROUP BY a.id
        ORDER BY a.is_archived, a.name COLLATE NOCASE
        """,
        (supported_iso,),
    ).fetchall()
    out = [dict(r) for r in rows]
    if close:
        conn.close()
    return out


def get_account(account_id: int,
                conn: Optional[sqlite3.Connection] = None
                ) -> Optional[dict]:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute("SELECT * FROM accounts WHERE id=?",
                       (int(account_id),)).fetchone()
    if close:
        conn.close()
    return dict(row) if row else None


def update_account(account_id: int, updates: dict,
                   conn: Optional[sqlite3.Connection] = None) -> bool:
    """Update editable fields. Type changes are allowed but the caller
    must confirm with the user (they change net-worth math)."""
    allowed = {"name", "type", "institution", "currency",
               "opening_balance", "opening_balance_date", "is_archived",
               "available_for_spending"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return False
    if "type" in fields and fields["type"] not in ACCOUNT_TYPES:
        raise ValueError(f"Unknown account type: {fields['type']!r}")
    if "available_for_spending" in fields:
        fields["available_for_spending"] = int(bool(
            fields["available_for_spending"]
        ))
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    sets = ", ".join(f"{k}=?" for k in fields)
    cur = conn.execute(
        f"UPDATE accounts SET {sets}, "
        f"updated_at=datetime('now','localtime') WHERE id=?",
        [*fields.values(), int(account_id)],
    )
    if close:
        conn.commit()
        conn.close()
    return cur.rowcount > 0


def archive_account(account_id: int, archived: bool = True,
                    conn: Optional[sqlite3.Connection] = None) -> bool:
    """Archive (or restore) an account. History is always preserved —
    there is deliberately no delete for accounts holding transactions."""
    return update_account(account_id,
                          {"is_archived": 1 if archived else 0},
                          conn=conn)

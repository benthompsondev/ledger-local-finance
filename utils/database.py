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
from pathlib import Path
from datetime import date, datetime
from typing import Optional

_DATA_DIR    = Path(__file__).parent.parent / "data"
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            account_type        TEXT NOT NULL,
            account_id          TEXT,
            transaction_date    TEXT NOT NULL,
            posted_date         TEXT,
            raw_description     TEXT NOT NULL,
            merchant            TEXT,
            amount              REAL NOT NULL,
            currency            TEXT DEFAULT 'CAD',
            foreign_amount      REAL,
            foreign_currency    TEXT,
            fx_rate             REAL,
            category            TEXT,
            subcategory         TEXT,
            direction           TEXT NOT NULL,
            is_transfer         INTEGER DEFAULT 0,
            is_flagged          INTEGER DEFAULT 0,
            flag_reason         TEXT,
            parse_confidence    TEXT DEFAULT 'high',
            reward_points       REAL,
            statement_period    TEXT,
            import_batch_id     INTEGER,
            dedup_hash          TEXT UNIQUE,
            notes               TEXT,
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
            imported_at     TEXT DEFAULT (datetime('now'))
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

        -- User-taught merchant→category mappings. Consulted by categorizer
        -- BEFORE the static ruleset so user corrections are durable.
        CREATE TABLE IF NOT EXISTS learned_rules (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_normalized TEXT NOT NULL UNIQUE,
            category            TEXT NOT NULL,
            subcategory         TEXT,
            source              TEXT DEFAULT 'user',
            hit_count           INTEGER DEFAULT 0,
            last_used_at        TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_learned_merchant ON learned_rules(merchant_normalized);

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
            net_worth_target    REAL,
            notes               TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now')),
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
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_goal_status ON goal_targets(status);
        """)

        # Seed default score weights row if empty.
        # Pass 36: defaults are 40/30/15/15. Existing rows are migrated
        # only when they are one of Ledger's known historical defaults,
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

        # ── v7 migrations: AI categorization metadata on transactions ─────
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
        ])

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


def _ensure_columns(conn, table: str, columns: list[tuple[str, str]]):
    """Idempotent ALTER TABLE … ADD COLUMN for each (name, type) not present."""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, coltype in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


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

def compute_dedup_hash(account_type: str, transaction_date: str, raw_description: str, amount: float) -> str:
    key = f"{account_type}|{transaction_date}|{raw_description.strip().lower()}|{amount:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()


def insert_transaction(tx: dict, conn: sqlite3.Connection) -> tuple[bool, str]:
    dedup = compute_dedup_hash(
        tx.get("account_type", ""),
        tx.get("transaction_date", ""),
        tx.get("raw_description", ""),
        tx.get("amount", 0.0),
    )
    existing = conn.execute(
        "SELECT id FROM transactions WHERE dedup_hash = ?", (dedup,)
    ).fetchone()
    if existing:
        return False, "duplicate"
    tx["dedup_hash"] = dedup
    columns = ", ".join(tx.keys())
    placeholders = ", ".join(["?"] * len(tx))
    conn.execute(
        f"INSERT INTO transactions ({columns}) VALUES ({placeholders})",
        list(tx.values()),
    )
    return True, "ok"


def insert_import_log(log: dict, conn: sqlite3.Connection) -> int:
    if "errors" in log and isinstance(log["errors"], list):
        log["errors"] = json.dumps(log["errors"])
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

    rows = conn.execute("""
        SELECT
            strftime('%Y-%m', transaction_date) AS month,
            SUM(CASE
                WHEN direction = 'credit'
                 AND amount > 0
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled')
                THEN amount ELSE 0
            END) AS income,
            -- Gross spending (debits); Transfer excluded = only self-transfers remain there
            SUM(CASE
                WHEN direction = 'debit'
                 AND direction NOT IN ('payment','cancelled','transfer')
                 AND category NOT IN ('Credit Card Payment','Cancelled','Transfer')
                THEN ABS(amount) ELSE 0
            END)
            -- minus refund offsets (MC credits with amount < 0)
            - SUM(CASE
                WHEN direction = 'credit' AND amount < 0
                THEN ABS(amount) ELSE 0
              END) AS spending,
            COUNT(*) AS tx_count
        FROM transactions
        WHERE strftime('%Y', transaction_date) = ?
        GROUP BY month
        ORDER BY month
    """, (str(year),)).fetchall()
    result = [dict(r) for r in rows]
    if close:
        conn.close()
    return result


def get_category_totals(
    start_date: str,
    end_date: str,
    account_type: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Category totals for spending charts (donut, bars).
    v8: includes Transfer Out (INTERAC e-Transfers sent to people) as real spending.
    Excludes only CC payments, cancelled, and savings pullbacks.
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

    rows = conn.execute(f"""
        SELECT category, SUM(ABS(amount)) AS total, COUNT(*) AS tx_count
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction = 'debit'
          AND direction NOT IN ('cancelled', 'payment', 'transfer')
          AND category NOT IN ('Credit Card Payment', 'Cancelled', 'Transfer')
          {acct_clause}
        GROUP BY category
        ORDER BY total DESC
    """, params).fetchall()
    result = [dict(r) for r in rows]
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
    """Delete all transactions for a batch and its import_log entry. Returns number of transactions deleted."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    cursor = conn.execute("DELETE FROM transactions WHERE import_batch_id = ?", (batch_id,))
    deleted = cursor.rowcount
    conn.execute("DELETE FROM import_log WHERE id = ?", (batch_id,))
    # Pass 35c: also cascade-clean the persisted statement summary for
    # this batch, if any. Defensive: the table may not exist on very
    # old databases, so wrap in try/except.
    try:
        conn.execute(
            "DELETE FROM statement_summaries WHERE import_batch_id = ?",
            (batch_id,),
        )
    except sqlite3.OperationalError:
        pass
    if close:
        conn.commit()
        conn.close()
    return deleted


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


def rerun_categorization(conn=None):
    from utils.categorizer import categorize, normalize_merchant, should_flag
    close = False
    if conn is None:
        conn = get_connection()
        close = True

    rows = conn.execute(
        "SELECT id, raw_description, amount, direction, account_type FROM transactions"
    ).fetchall()

    # Non-cashflow categories — any debit/credit landing here should be is_transfer=1.
    # Mirrors the consistency pass in enrich_transaction().
    _NON_CASHFLOW = frozenset({"Transfer", "Credit Card Payment", "Payment", "Cancelled"})

    updated = 0
    for row in rows:
        raw  = row["raw_description"]
        amt  = row["amount"]
        dir_ = row["direction"]

        if dir_ in ("payment", "transfer", "cancelled"):
            # These rows keep their direction/category. Still sync is_transfer.
            conn.execute(
                "UPDATE transactions SET is_transfer=1 WHERE id=? AND is_transfer!=1",
                (row["id"],),
            )
            continue

        merchant = normalize_merchant(raw)

        # Learned rules win over static rules
        learned = conn.execute(
            "SELECT category, subcategory FROM learned_rules WHERE merchant_normalized=?",
            (merchant,),
        ).fetchone()
        if learned:
            cat = learned["category"]
            sub = learned["subcategory"] or ""
            conf = 1.0
        else:
            cat, sub, conf = categorize(raw, amt, dir_)
        conf_str = "high" if conf >= 0.9 else ("medium" if conf >= 0.7 else "low")
        conf_float = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(conf_str, 0.5)
        flagged, reason = should_flag(raw, dir_, abs(amt), conf_float)

        # Derive is_transfer from final category (same logic as enrich_transaction)
        is_tr = 1 if cat in _NON_CASHFLOW else 0

        conn.execute("""
            UPDATE transactions
            SET category=?, subcategory=?, merchant=?, parse_confidence=?,
                is_flagged=?, flag_reason=?, is_transfer=?
            WHERE id=?
        """, (cat, sub, merchant, conf_str, 1 if flagged else 0, reason, is_tr, row["id"]))
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
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    row = conn.execute(
        "SELECT * FROM learned_rules WHERE merchant_normalized=?",
        (merchant_normalized,),
    ).fetchone()
    result = dict(row) if row else None
    if close:
        conn.close()
    return result


def upsert_learned_rule(merchant_normalized: str, category: str,
                        subcategory: Optional[str] = None, source: str = "user",
                        conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "INSERT INTO learned_rules (merchant_normalized, category, subcategory, source) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(merchant_normalized) DO UPDATE SET "
        "category=excluded.category, subcategory=excluded.subcategory, source=excluded.source",
        (merchant_normalized, category, subcategory, source),
    )
    if close:
        conn.commit()
        conn.close()


def delete_learned_rule(merchant_normalized: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute("DELETE FROM learned_rules WHERE merchant_normalized=?", (merchant_normalized,))
    if close:
        conn.commit()
        conn.close()


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


def bump_learned_rule_hit(merchant_normalized: str, conn=None):
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    conn.execute(
        "UPDATE learned_rules SET hit_count=COALESCE(hit_count,0)+1, "
        "last_used_at=datetime('now') WHERE merchant_normalized=?",
        (merchant_normalized,),
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
            ai_accepted = 1
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
            SET category=?, subcategory=?, is_transfer=?
            WHERE merchant=?
              AND (category IS NULL OR category='' OR category='Misc'
                   OR parse_confidence='low')
        """, (category, subcategory, _is_transfer_target, merchant_normalized))
    else:
        cur = conn.execute(
            "UPDATE transactions SET category=?, subcategory=?, is_transfer=? "
            "WHERE merchant=?",
            (category, subcategory, _is_transfer_target, merchant_normalized),
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
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if only_uncertain:
        rows = conn.execute(
            "SELECT id FROM transactions WHERE merchant=? "
            "AND (category IS NULL OR category='' OR category='Misc' "
            "OR parse_confidence='low') ORDER BY transaction_date DESC",
            (merchant_normalized,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM transactions WHERE merchant=? "
            "ORDER BY transaction_date DESC",
            (merchant_normalized,),
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
               "flags_cleared": 0, "is_transfer_synced": 0}
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
            f"parse_confidence='high' "
            f"WHERE id IN ({placeholders})",
            [category, subcategory, expected_xfer, flag_reason_when_cleared, *tx_ids],
        )
    else:
        conn.execute(
            f"UPDATE transactions SET category=?, subcategory=?, is_transfer=? "
            f"WHERE id IN ({placeholders})",
            [category, subcategory, expected_xfer, *tx_ids],
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
    per (account_name, account_kind) is returned."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    if latest_only:
        rows = conn.execute("""
            SELECT * FROM account_balances ab
            WHERE id IN (
                SELECT MAX(id) FROM account_balances
                GROUP BY account_name, account_kind
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
    for bal in get_account_balances(conn=conn, latest_only=True):
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
    plan["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


def update_goal(goal_id: int, updates: dict,
                conn: Optional[sqlite3.Connection] = None) -> None:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    updates = dict(updates)
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

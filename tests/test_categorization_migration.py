"""Populated legacy-database upgrade coverage for categorization metadata."""
from __future__ import annotations

import sqlite3

import pytest

import utils.database as db
from utils.categorization_service import undo_import_batch


def _create_populated_legacy_db(path) -> None:
    """Create the relevant schema as it existed before recent metadata."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_type TEXT NOT NULL,
                account_id TEXT,
                transaction_date TEXT NOT NULL,
                posted_date TEXT,
                raw_description TEXT NOT NULL,
                merchant TEXT,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'CAD',
                foreign_amount REAL,
                foreign_currency TEXT,
                fx_rate REAL,
                category TEXT,
                subcategory TEXT,
                direction TEXT NOT NULL,
                is_transfer INTEGER DEFAULT 0,
                is_flagged INTEGER DEFAULT 0,
                flag_reason TEXT,
                parse_confidence TEXT DEFAULT 'high',
                reward_points REAL,
                statement_period TEXT,
                import_batch_id INTEGER,
                dedup_hash TEXT UNIQUE,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                ai_suggested_category TEXT,
                ai_suggested_subcategory TEXT,
                ai_confidence REAL,
                ai_provider TEXT,
                ai_model TEXT,
                ai_rationale TEXT,
                ai_suggested_at TEXT,
                ai_accepted INTEGER
            );
            CREATE TABLE import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_hash TEXT,
                account_type TEXT,
                statement_period TEXT,
                rows_parsed INTEGER DEFAULT 0,
                rows_inserted INTEGER DEFAULT 0,
                rows_skipped INTEGER DEFAULT 0,
                rows_flagged INTEGER DEFAULT 0,
                errors TEXT,
                imported_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE learned_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merchant_normalized TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                subcategory TEXT,
                source TEXT DEFAULT 'user',
                hit_count INTEGER DEFAULT 0,
                last_used_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE statement_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_batch_id INTEGER NOT NULL,
                account_type TEXT,
                statement_period_label TEXT,
                statement_start_date TEXT,
                statement_end_date TEXT,
                previous_balance REAL,
                payments_and_credits REAL,
                transactions_total REAL,
                cash_advances_total REAL,
                adjustments_total REAL,
                interest_charges REAL,
                fees REAL,
                new_balance REAL,
                minimum_payment_due REAL,
                payment_due_date TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO import_log
                (id, filename, rows_parsed, rows_inserted, imported_at)
            VALUES
                (1, 'legacy-one.csv', 3, 3, '2026-06-01 10:00:00'),
                (2, 'legacy-two.csv', 2, 2, '2026-07-01 10:00:00');
            INSERT INTO transactions
                (account_type, transaction_date, raw_description, merchant,
                 amount, category, direction, is_transfer, import_batch_id,
                 dedup_hash, notes)
            VALUES
                ('mastercard','2026-06-01','SQ *LEGACY CAFE STORE 1234',
                 'Legacy Cafe',12.50,'Food & Convenience','debit',0,1,'d1',NULL),
                ('chequing','2026-06-02','TANGERINE CREDIT CARD PAYMENT',
                 'Tangerine Payment',500.00,'Payment','payment',1,1,'d2',NULL),
                ('savings','2026-06-03','INTERNET TRANSFER TO CHEQUING',
                 'Internal Transfer',300.00,'Transfer','transfer',1,1,'d3',NULL),
                ('chequing','2026-07-01','EMPLOYER PAYROLL',
                 'Employer Payroll',2500.00,'Payroll Income','credit',0,2,'d4',NULL),
                ('mastercard','2026-07-02','LEGACY MARKET',
                 'Legacy Market',80.00,'Groceries','debit',0,2,'d5',
                 'category corrected manually before provenance existed');
            INSERT INTO learned_rules
                (merchant_normalized, category, source)
            VALUES ('SQ *LEGACY CAFE STORE 1234','Food & Convenience','user');
            INSERT INTO statement_summaries
                (import_batch_id, account_type, statement_start_date,
                 statement_end_date, new_balance)
            VALUES
                (1,'mastercard','2026-06-01','2026-06-30',512.50),
                (2,'mastercard','2026-07-01','2026-07-31',80.00);
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_populated_legacy_migration_is_idempotent_and_preserves_history(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy.db"
    _create_populated_legacy_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    db.init_db()
    db.init_db()
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, amount, category, category_source, "
            "merchant_normalized FROM transactions ORDER BY id"
        ).fetchall()
        assert len(rows) == 5
        assert sum(float(r["amount"]) for r in rows) == pytest.approx(3392.50)
        assert [r["category"] for r in rows] == [
            "Food & Convenience", "Payment", "Transfer",
            "Payroll Income", "Groceries",
        ]
        assert {r["category_source"] for r in rows} == {"unknown"}
        assert all(r["merchant_normalized"] for r in rows)

        migrated_rule = db.list_learned_rules(conn=conn)
        assert len(migrated_rule) == 1
        assert migrated_rule[0]["merchant_normalized"] == "LEGACY CAFE"
        assert migrated_rule[0]["enabled"] == 1

        new_rule_id = db.upsert_learned_rule(
            "NEW LEGACY SHOP", "Shopping", conn=conn
        )
        assert new_rule_id > 0
        conn.commit()

        result = undo_import_batch(1, conn=conn)
        conn.commit()
        assert result["transactions_deleted"] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE import_batch_id=2"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM statement_summaries WHERE import_batch_id=1"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM statement_summaries WHERE import_batch_id=2"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_failed_categorization_backfill_rolls_back_schema_slice(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy-failure.db"
    _create_populated_legacy_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    real_backfill = db._backfill_categorization_metadata

    def fail_after_backfill(conn):
        real_backfill(conn)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(db, "_backfill_categorization_metadata", fail_after_backfill)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        db.init_db()

    raw = sqlite3.connect(path)
    try:
        tx_columns = {
            row[1] for row in raw.execute("PRAGMA table_info(transactions)")
        }
        rule_columns = {
            row[1] for row in raw.execute("PRAGMA table_info(learned_rules)")
        }
        assert "merchant_normalized" not in tx_columns
        assert "enabled" not in rule_columns
        assert raw.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
    finally:
        raw.close()

    monkeypatch.setattr(db, "_backfill_categorization_metadata", real_backfill)
    db.init_db()
    upgraded = sqlite3.connect(path)
    try:
        assert "merchant_normalized" in {
            row[1] for row in upgraded.execute("PRAGMA table_info(transactions)")
        }
    finally:
        upgraded.close()

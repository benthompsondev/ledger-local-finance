"""Grouped bulk categorization and transaction-safe import-batch undo."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import utils.database as db
from utils.analytics import compute_cashflow
from utils.categorization_service import (
    apply_bulk_categorization,
    merchant_review_groups,
    preview_bulk_categorization,
    preview_import_batch_undo,
    undo_import_batch,
)
from utils.import_summary import summarize_import
from utils.import_service import persist_import_batch
from scripts.create_demo_data import main as create_demo_data


def _batch(conn, name, *, inserted=0, skipped=0, at="2026-07-17 10:00:00"):
    bid = db.insert_import_log({
        "filename": name, "account_type": "Generic CSV",
        "statement_period": "2026-07", "rows_parsed": inserted + skipped,
        "rows_inserted": inserted, "rows_skipped": skipped, "imported_at": at,
    }, conn)
    conn.commit()
    return bid


def _insert(conn, bid, desc, *, day, amount, category="Uncategorized",
            flagged=1, source="import_rule"):
    ok, msg = db.insert_transaction({
        "account_type": "mastercard", "transaction_date": day,
        "raw_description": desc, "merchant": desc.title(),
        "amount": amount, "direction": "debit", "category": category,
        "parse_confidence": "low" if flagged else "high",
        "is_flagged": flagged, "is_transfer": 0,
        "import_batch_id": bid, "category_source": source,
    }, conn)
    assert ok, msg
    row = conn.execute(
        "SELECT id FROM transactions WHERE raw_description=?", (desc,)
    ).fetchone()
    conn.commit()
    return int(row["id"])


def test_grouped_normalized_merchant_and_bulk_preview_apply(ledger_db):
    bid = _batch(ledger_db, "july.csv", inserted=3)
    ids = [
        _insert(ledger_db, bid, "SQ *TIM HORTONS STORE 1234", day="2026-07-01", amount=8),
        _insert(ledger_db, bid, "TIM HORTONS TERMINAL 9876", day="2026-07-03", amount=9),
        _insert(ledger_db, bid, "TIM HORTONS REF A19BC8821", day="2026-07-05", amount=10),
    ]
    groups = merchant_review_groups(batch_ids=[bid], conn=ledger_db)
    assert len(groups) == 1
    assert groups[0]["merchant_normalized"] == "TIM HORTONS"
    assert groups[0]["count"] == 3
    assert groups[0]["total_amount"] == pytest.approx(27)

    before_total = ledger_db.execute(
        "SELECT SUM(amount) FROM transactions WHERE import_batch_id=?", (bid,)
    ).fetchone()[0]
    preview = preview_bulk_categorization(ids[:2], "Food & Convenience", conn=ledger_db)
    assert preview["selected_count"] == 2
    assert preview["change_count"] == 2
    result = apply_bulk_categorization(
        ids[:2], "Food & Convenience", save_rule=True, conn=ledger_db
    )
    ledger_db.commit()
    assert result["applied_count"] == preview["selected_count"]
    cats = [r["category"] for r in ledger_db.execute(
        "SELECT category FROM transactions ORDER BY id"
    ).fetchall()]
    assert cats == ["Food & Convenience", "Food & Convenience", "Uncategorized"]
    after_total = ledger_db.execute(
        "SELECT SUM(amount) FROM transactions WHERE import_batch_id=?", (bid,)
    ).fetchone()[0]
    assert after_total == before_total
    rule = db.get_learned_rule("TIM HORTONS", conn=ledger_db)
    assert rule and rule["category"] == "Food & Convenience"
    assert rule["source_import_batch_id"] == bid


def test_undo_removes_only_selected_batch_and_preserves_rules(ledger_db):
    first = _batch(ledger_db, "first.csv", inserted=2, at="2026-07-17 10:00:00")
    second = _batch(ledger_db, "second.csv", inserted=1, at="2026-07-17 10:05:00")
    first_ids = [
        _insert(ledger_db, first, "DEMO SHOP STORE 123", day="2026-07-01", amount=20),
        _insert(ledger_db, first, "DEMO SHOP TERMINAL 456", day="2026-07-02", amount=30),
    ]
    _insert(ledger_db, second, "KEEP SHOP", day="2026-07-03", amount=40)
    apply_bulk_categorization(first_ids, "Shopping", save_rule=True, conn=ledger_db)
    db.upsert_statement_summary(
        {"statement_start_date": "2026-07-01",
         "statement_end_date": "2026-07-31", "new_balance": 50.0},
        import_batch_id=first, conn=ledger_db,
    )
    # Simulate older/null batch provenance: the source transaction still
    # identifies the rule as belonging to the batch being removed.
    ledger_db.execute(
        "UPDATE learned_rules SET source_import_batch_id=NULL"
    )
    ledger_db.commit()

    preview = preview_import_batch_undo(first, conn=ledger_db)
    assert preview["transaction_count"] == 2
    assert preview["edited_count"] == 2
    assert preview["rule_count"] == 1
    assert preview["statement_summary_count"] == 1
    result = undo_import_batch(first, conn=ledger_db)
    ledger_db.commit()
    assert result["undone"] is True
    assert list(
        (Path(db.DB_PATH).parent / "backups").glob(
            f"*pre-import-undo-{first}.db"
        )
    )
    assert result["transactions_deleted"] == 2
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE import_batch_id=?", (second,)
    ).fetchone()[0] == 1
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM import_log WHERE id=?", (second,)
    ).fetchone()[0] == 1
    assert db.list_learned_rules(conn=ledger_db)
    rule = db.list_learned_rules(conn=ledger_db)[0]
    assert rule["source_transaction_id"] is None
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM statement_summaries WHERE import_batch_id=?",
        (first,),
    ).fetchone()[0] == 0
    assert summarize_import(conn=ledger_db)["inserted"] == 1

    again = undo_import_batch(first, conn=ledger_db)
    assert again == {"undone": False, "reason": "not_found", "batch_id": first}


def test_duplicate_only_batch_undo_is_safe(ledger_db):
    bid = _batch(ledger_db, "duplicate.csv", inserted=0, skipped=12)
    preview = preview_import_batch_undo(bid, conn=ledger_db)
    assert preview["duplicates_only"] is True
    assert preview["transaction_count"] == 0
    assert undo_import_batch(bid, conn=ledger_db)["undone"] is True
    ledger_db.commit()
    assert ledger_db.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0


def test_undo_updates_downstream_cashflow(ledger_db):
    keep = _batch(ledger_db, "keep.csv", inserted=1)
    remove = _batch(ledger_db, "remove.csv", inserted=1, at="2026-07-17 10:05:00")
    _insert(ledger_db, keep, "KEEP", day="2026-07-01", amount=25, category="Groceries", flagged=0)
    _insert(ledger_db, remove, "REMOVE", day="2026-07-02", amount=75, category="Shopping", flagged=0)
    before = compute_cashflow("2026-07-01", "2026-07-31", conn=ledger_db)
    undo_import_batch(remove, conn=ledger_db)
    ledger_db.commit()
    after = compute_cashflow("2026-07-01", "2026-07-31", conn=ledger_db)
    assert before["spending"] - after["spending"] == pytest.approx(75)


def test_undo_rolls_back_if_log_delete_fails(ledger_db):
    bid = _batch(ledger_db, "blocked.csv", inserted=1)
    _insert(ledger_db, bid, "ROLLBACK ME", day="2026-07-01", amount=55)
    ledger_db.execute(
        f"""CREATE TRIGGER block_batch_delete BEFORE DELETE ON import_log
            WHEN OLD.id={bid}
            BEGIN SELECT RAISE(ABORT, 'blocked for test'); END"""
    )
    ledger_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        undo_import_batch(bid, conn=ledger_db)
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE import_batch_id=?", (bid,)
    ).fetchone()[0] == 1
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM import_log WHERE id=?", (bid,)
    ).fetchone()[0] == 1


def test_bulk_update_rolls_back_on_partial_failure(ledger_db):
    bid = _batch(ledger_db, "bulk-fail.csv", inserted=2)
    ids = [
        _insert(ledger_db, bid, "ROLLBACK SHOP STORE 11", day="2026-07-01", amount=10),
        _insert(ledger_db, bid, "ROLLBACK SHOP STORE 22", day="2026-07-02", amount=20),
    ]
    ledger_db.execute(
        f"""CREATE TRIGGER block_second_bulk BEFORE UPDATE ON transactions
            WHEN OLD.id={ids[1]} AND NEW.category='Shopping'
            BEGIN SELECT RAISE(ABORT, 'blocked bulk update'); END"""
    )
    ledger_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        apply_bulk_categorization(ids, "Shopping", save_rule=True, conn=ledger_db)
    rows = ledger_db.execute(
        "SELECT category FROM transactions WHERE id IN (?,?) ORDER BY id", ids
    ).fetchall()
    assert [r["category"] for r in rows] == ["Uncategorized", "Uncategorized"]
    assert db.list_learned_rules(conn=ledger_db) == []


def test_bulk_update_fails_closed_for_stale_selection(ledger_db):
    bid = _batch(ledger_db, "stale.csv", inserted=1)
    tx_id = _insert(
        ledger_db, bid, "STALE SHOP", day="2026-07-01", amount=10
    )
    with pytest.raises(RuntimeError, match="changed before"):
        apply_bulk_categorization(
            [tx_id, 999999], "Shopping", conn=ledger_db
        )
    assert ledger_db.execute(
        "SELECT category FROM transactions WHERE id=?", (tx_id,)
    ).fetchone()[0] == "Uncategorized"


def test_bulk_rule_rejects_unresolved_category(ledger_db):
    bid = _batch(ledger_db, "unresolved.csv", inserted=1)
    tx_id = _insert(
        ledger_db, bid, "UNRESOLVED SHOP", day="2026-07-01", amount=10
    )
    with pytest.raises(ValueError, match="reviewed category"):
        apply_bulk_categorization(
            [tx_id], "Uncategorized", save_rule=True, conn=ledger_db
        )
    assert db.list_learned_rules(conn=ledger_db) == []


def test_atomic_file_persistence_rolls_back_log_rows_and_summary(ledger_db):
    ledger_db.execute(
        """CREATE TRIGGER fail_second_import BEFORE INSERT ON transactions
           WHEN NEW.raw_description='FAIL SECOND ROW'
           BEGIN SELECT RAISE(ABORT, 'blocked import'); END"""
    )
    ledger_db.commit()
    txs = [
        {
            "account_type": "mastercard", "transaction_date": "2026-07-01",
            "raw_description": "FIRST ROW", "amount": 10.0,
            "direction": "debit", "category": "Shopping", "is_transfer": 0,
        },
        {
            "account_type": "mastercard", "transaction_date": "2026-07-02",
            "raw_description": "FAIL SECOND ROW", "amount": 20.0,
            "direction": "debit", "category": "Shopping", "is_transfer": 0,
        },
    ]
    with pytest.raises(sqlite3.IntegrityError):
        persist_import_batch(
            {"filename": "atomic.csv", "rows_parsed": 2}, txs,
            statement_summary={
                "statement_start_date": "2026-07-01",
                "statement_end_date": "2026-07-31",
                "new_balance": 30.0,
            },
            conn=ledger_db,
        )
    assert ledger_db.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0
    assert ledger_db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM statement_summaries"
    ).fetchone()[0] == 0


def test_demo_seed_rows_do_not_claim_an_import_batch(tmp_path):
    demo_path = tmp_path / "finance.demo.db"
    assert create_demo_data([
        "--out", str(demo_path), "--force", "--months", "1",
    ]) == 0

    conn = sqlite3.connect(demo_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE import_batch_id IS NOT NULL"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0
    finally:
        conn.close()

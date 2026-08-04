"""Regression tests for legacy repair and destructive reset safety."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

import utils.database as db
from parsers.csv_import import parse_csv
from utils.backups import restore_database
from utils.data_safety import (
    plan_legacy_repair,
    preview_legacy_data,
    repair_legacy_data,
    reset_all_financial_data,
)
from utils.financial_semantics import (
    consolidate_material_review,
    promote_recurring_income,
)


def _legacy_fixture(ledger_db, tmp_path):
    source = tmp_path / "invented legacy statement.csv"
    source.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-07-01,OTHER,EXAMPLE PAYROLL,Deposit,2000.00\n"
        "2026-07-02,DEBIT,COFFEE PLACE,Purchase,-7.50\n"
        "2026-07-03,OTHER,INTERAC E-TRANSFER FROM: SAMPLE PERSON,Deposit,125.00\n"
        "2026-07-04,OTHER,INTERAC E-TRANSFER TO: OWN SAVINGS,Transfer,-90.00\n",
        encoding="utf-8",
    )
    account_id = db.create_account(
        {"name": "Example Chequing", "type": "chequing"}, conn=ledger_db
    )
    parsed = parse_csv(source, account_type="chequing")["transactions"]
    for tx in parsed:
        tx["account_ref"] = account_id
        tx["account_id"] = "Example Chequing"
    promote_recurring_income(parsed)
    consolidate_material_review(parsed)

    batch_id = db.insert_import_log({
        "filename": source.name,
        "file_hash": hashlib.md5(source.read_bytes()).hexdigest(),
        "account_type": "Generic CSV",
        "rows_parsed": len(parsed),
        "rows_inserted": len(parsed),
        "rows_flagged": len(parsed),
        "semantics_version": 1,
    }, ledger_db)
    for index, current in enumerate(parsed):
        legacy = dict(current)
        legacy["amount"] = abs(float(current["amount"]))
        # The old importer allowed a descriptive DEBIT/CREDIT column to
        # override source sign. Deliberately make direction unreliable here.
        legacy["direction"] = "debit" if index != 1 else "credit"
        legacy["transaction_type"] = None
        legacy["category"] = "Fees / Interest" if index else "Income"
        legacy["category_source"] = "protected" if index else "import_rule"
        legacy["is_flagged"] = 1
        legacy["flag_reason"] = "legacy review"
        legacy["import_batch_id"] = batch_id
        if index == 3:
            legacy.update({
                "category": "Internal Transfer",
                "category_source": "user_edit",
                "manually_edited_at": "2026-07-10 12:00:00",
            })
        ok, reason = db.insert_transaction(legacy, ledger_db)
        assert ok, reason
    ledger_db.commit()
    return source, batch_id, parsed


def test_legacy_repair_uses_source_signs_and_preserves_user_edit(
    ledger_db, tmp_path,
) -> None:
    source, batch_id, parsed = _legacy_fixture(ledger_db, tmp_path)
    preview = preview_legacy_data(conn=ledger_db)
    assert preview["batch_count"] == 1
    assert preview["affected_rows"] == 4
    assert preview["manual_rows"] == 1
    assert preview["review_before"] == 4

    plan = plan_legacy_repair({batch_id: parsed}, conn=ledger_db)
    assert plan["ready"] is True
    assert plan["matched_rows"] == 4
    ledger_db.close()

    result = repair_legacy_data({batch_id: parsed})
    assert result["rows_repaired"] == 4
    assert result["batches_repaired"] == 1
    assert result["manual_rows_preserved"] == 1
    assert result["review_before"] == 4
    assert result["review_after"] == 0
    assert result["backup_path"]

    repaired = db.get_connection()
    try:
        rows = [dict(row) for row in repaired.execute(
            "SELECT * FROM transactions ORDER BY transaction_date"
        ).fetchall()]
        assert [row["amount"] for row in rows] == [2000, -7.5, 125, -90]
        assert [row["direction"] for row in rows] == [
            "credit", "debit", "credit", "debit",
        ]
        assert rows[0]["transaction_type"] == "employment_income"
        assert rows[1]["transaction_type"] == "spending"
        assert rows[2]["transaction_type"] == "other_income"
        assert rows[3]["transaction_type"] == "internal_transfer"
        assert rows[3]["category"] == "Internal Transfer"
        assert rows[3]["category_source"] == "user_edit"
        assert rows[3]["manually_edited_at"] == "2026-07-10 12:00:00"
        assert repaired.execute(
            "SELECT semantics_version FROM import_log WHERE id=?", (batch_id,)
        ).fetchone()[0] == 2
        assert preview_legacy_data(conn=repaired)["needed"] is False
    finally:
        repaired.close()

    backup = sqlite3.connect(result["backup_path"])
    try:
        assert backup.execute(
            "SELECT COUNT(*) FROM transactions WHERE amount < 0"
        ).fetchone()[0] == 0
    finally:
        backup.close()


def test_incomplete_repair_stops_before_backup_or_write(ledger_db, tmp_path) -> None:
    _source, batch_id, parsed = _legacy_fixture(ledger_db, tmp_path)
    original = [tuple(row) for row in ledger_db.execute(
        "SELECT id,amount,direction,category FROM transactions ORDER BY id"
    )]
    ledger_db.close()

    with pytest.raises(ValueError, match="preview is incomplete"):
        repair_legacy_data({batch_id: parsed[:-1]})

    check = db.get_connection()
    try:
        assert [tuple(row) for row in check.execute(
            "SELECT id,amount,direction,category FROM transactions ORDER BY id"
        )] == original
    finally:
        check.close()


def test_reset_backs_up_all_financial_data_and_restore_recovers_it(
    ledger_db, tmp_path,
) -> None:
    _source, _batch_id, _parsed = _legacy_fixture(ledger_db, tmp_path)
    db.upsert_profile("Example profile", conn=ledger_db)
    db.upsert_learned_rule("coffee place", "Food & Convenience", conn=ledger_db)
    db.upsert_monthly_plan({
        "month": "2026-07", "mode": "normal", "income_target": 3000,
        "spending_target": 1800, "savings_target": 500,
    }, conn=ledger_db)
    db.insert_goal({
        "name": "Example goal", "type": "general_savings",
        "target_amount": 1000, "current_amount": 100,
    }, conn=ledger_db)
    db.insert_account_balance({
        "account_ref": 1, "account_name": "Example Chequing",
        "account_kind": "chequing", "balance": 500,
        "as_of_date": "2026-07-05",
    }, conn=ledger_db)
    ledger_db.commit()
    ledger_db.close()

    result = reset_all_financial_data(complete_reset=False)
    assert result["reset"] is True
    assert result["complete_reset"] is False
    assert result["removed"]["transaction_count"] == 4
    assert result["backup_path"]

    empty = db.get_connection()
    try:
        for table in (
            "transactions", "import_log", "accounts", "monthly_plans",
            "goal_targets", "account_balances", "learned_rules", "profiles",
        ):
            assert empty.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        empty.close()

    restored = restore_database(result["backup_path"])
    assert restored["restart_recommended"] is True
    recovered = db.get_connection()
    try:
        assert recovered.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 4
        assert recovered.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        assert recovered.execute("SELECT COUNT(*) FROM monthly_plans").fetchone()[0] == 1
        assert recovered.execute("SELECT COUNT(*) FROM goal_targets").fetchone()[0] == 1
        assert recovered.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0] == 1
        assert recovered.execute("SELECT COUNT(*) FROM learned_rules").fetchone()[0] == 1
        assert recovered.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
    finally:
        recovered.close()

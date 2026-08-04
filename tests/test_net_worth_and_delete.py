"""Removing a row that should not exist.

The net worth half of this module moved to test_net_worth_entries_2_5_2.py
when net worth stopped being a projection and became a monthly reading.
"""
import pytest

from tests.conftest import add_tx
from utils.database import delete_transaction


# ── removing a transaction ──────────────────────────────────────────────

def test_a_removed_transaction_stops_counting(ledger_db):
    add_tx(ledger_db, day="2026-07-02", desc="INVENTED SHOP", amount=40,
           direction="debit", category="Groceries")
    add_tx(ledger_db, day="2026-07-03", desc="INVENTED MISTAKE", amount=900,
           direction="debit", category="Groceries")
    ledger_db.commit()

    row = ledger_db.execute(
        "SELECT id FROM transactions WHERE raw_description LIKE '%MISTAKE%'"
    ).fetchone()
    assert delete_transaction(row[0], conn=ledger_db) is True

    remaining = ledger_db.execute(
        "SELECT COUNT(*), COALESCE(SUM(ABS(amount)), 0) FROM transactions"
    ).fetchone()
    assert remaining[0] == 1
    assert remaining[1] == pytest.approx(40.0, abs=0.01)


def test_removing_a_row_lets_the_same_statement_import_again(ledger_db):
    """Without clearing the fingerprint, re-importing would silently do nothing."""
    from utils.database import insert_transaction

    tx = {
        "transaction_date": "2026-07-02",
        "raw_description": "INVENTED SHOP",
        "amount": -40.0,
        "direction": "debit",
        "category": "Groceries",
        "transaction_type": "spending",
        "account_type": "chequing",
    }
    ok, _ = insert_transaction(dict(tx), ledger_db)
    assert ok
    ledger_db.commit()

    row = ledger_db.execute("SELECT id FROM transactions").fetchone()
    delete_transaction(row[0], conn=ledger_db)

    ok_again, reason = insert_transaction(dict(tx), ledger_db)
    assert ok_again, f"re-import was refused as {reason}"


def test_removing_a_row_that_is_not_there_is_not_an_error(ledger_db):
    assert delete_transaction(999_999, conn=ledger_db) is False

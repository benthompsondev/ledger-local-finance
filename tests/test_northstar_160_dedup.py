"""Northstar 1.6.0 — occurrence-aware duplicate detection.

The insert fingerprint was account + date + description + amount, so a genuine
second identical purchase on the same day (two transit fares, two coffees) was
indistinguishable from a re-imported row and was silently discarded. That is
data loss: the row never appears anywhere, and re-importing the file cannot
recover it because the re-import is exactly what the fingerprint rejects.

Both properties have to hold at once:
  - two identical purchases in ONE file both survive
  - importing that same file again inserts nothing
"""
import pytest

from utils.financial_semantics import canonicalize_transaction


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def _fare(day="2026-05-04", amount=-3.35, desc="INVENTED TRANSIT FARE"):
    return canonicalize_transaction({
        "account_type": "chequing",
        "transaction_date": day,
        "raw_description": desc,
        "merchant": desc,
        "amount": amount,
        "direction": "debit",
        "category": "Gas / Transport",
        "is_transfer": 0,
        "is_flagged": 0,
    })


def _count(conn, needle="TRANSIT"):
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE raw_description LIKE ?",
        (f"%{needle}%",),
    ).fetchone()[0]


def _import(conn, rows):
    """Run rows through the real batch path, which owns occurrence counting."""
    from utils.import_service import persist_import_batch

    return persist_import_batch(
        {"filename": "invented.csv", "account_type": "chequing",
         "rows_parsed": len(rows)},
        [dict(r) for r in rows],
        conn=conn,
    )


def test_two_identical_purchases_in_one_file_both_survive(ledger_db):
    _import(ledger_db, [_fare(), _fare()])
    assert _count(ledger_db) == 2


def test_reimporting_the_same_file_inserts_nothing(ledger_db):
    rows = [_fare(), _fare()]
    first = _import(ledger_db, rows)
    assert first["inserted"] == 2
    assert _count(ledger_db) == 2

    second = _import(ledger_db, rows)
    assert second["inserted"] == 0
    assert second["skipped"] == 2
    # The decisive assertion: no growth, and nothing lost either.
    assert _count(ledger_db) == 2


def test_three_identical_purchases_all_survive(ledger_db):
    _import(ledger_db, [_fare(), _fare(), _fare()])
    assert _count(ledger_db) == 3
    _import(ledger_db, [_fare(), _fare(), _fare()])
    assert _count(ledger_db) == 3


def test_a_later_file_with_fewer_copies_still_dedups(ledger_db):
    """A corrected export with one fare must not add a fourth row."""
    _import(ledger_db, [_fare(), _fare(), _fare()])
    _import(ledger_db, [_fare()])
    assert _count(ledger_db) == 3


def test_a_later_file_with_more_copies_adds_only_the_new_ones(ledger_db):
    """Two already stored, a corrected export shows four: add exactly two."""
    _import(ledger_db, [_fare(), _fare()])
    assert _count(ledger_db) == 2
    _import(ledger_db, [_fare(), _fare(), _fare(), _fare()])
    assert _count(ledger_db) == 4


def test_different_amounts_are_never_confused(ledger_db):
    _import(ledger_db, [_fare(amount=-3.35), _fare(amount=-4.10)])
    assert _count(ledger_db) == 2


def test_manual_entry_refuses_an_identical_row_by_default(engine_env):
    """Hitting save twice is far more common than buying the same thing twice."""
    from tests.test_native_desktop_engine import _request

    _, payload = _request({"action": "create_account", "params": {
        "name": "Invented Chequing", "type": "chequing"}})
    account_id = payload["data"]["created_id"]
    spec = {"account_id": account_id, "date": "2026-05-04",
            "description": "INVENTED TRANSIT FARE", "amount": 3.35,
            "direction": "debit", "category": "Gas / Transport"}

    code, _ = _request({"action": "add_manual_transaction", "params": spec})
    assert code == 0
    code, payload = _request({"action": "add_manual_transaction",
                              "params": spec})
    assert code != 0
    assert "Add it anyway" in str(payload)


def test_manual_entry_keeps_a_second_copy_the_user_confirms(engine_env):
    """Only the user can tell two genuine fares from a double-submit."""
    from tests.test_native_desktop_engine import _request

    _, payload = _request({"action": "create_account", "params": {
        "name": "Invented Chequing", "type": "chequing"}})
    account_id = payload["data"]["created_id"]
    spec = {"account_id": account_id, "date": "2026-05-04",
            "description": "INVENTED TRANSIT FARE", "amount": 3.35,
            "direction": "debit", "category": "Gas / Transport"}

    assert _request({"action": "add_manual_transaction",
                     "params": spec})[0] == 0
    code, _ = _request({"action": "add_manual_transaction",
                        "params": {**spec, "allow_duplicate": True}})
    assert code == 0

    from utils.database import get_connection
    conn = get_connection()
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE raw_description LIKE '%TRANSIT%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert stored == 2


def test_the_first_occurrence_fingerprint_is_unchanged(ledger_db):
    """Existing databases must keep deduplicating against rows already stored.

    Occurrence 0 has to hash exactly as it did before this change, or every
    user's next re-import would double-count their whole history.
    """
    from utils.database import compute_dedup_hash, transaction_dedup_state

    tx = _fare()
    stored, _dup = transaction_dedup_state(tx, ledger_db, occurrence=0)
    assert stored == compute_dedup_hash(
        tx["account_type"], tx["transaction_date"],
        tx["raw_description"], tx["amount"],
    )

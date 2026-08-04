"""Import completion summary: session grouping, materiality gating,
plumbing exclusions, and the duplicates-only state."""
import pytest

import utils.database as db
from utils.import_summary import (
    latest_unreviewed_batches, material_review_items, summarize_import,
)
from tests.conftest import add_tx, seed_month


def _make_batch(conn, filename="stmt.pdf", *, inserted=0, skipped=0,
                imported_at=None, account="Tangerine Chequing PDF",
                period="2026-06"):
    log = {
        "filename": filename, "account_type": account,
        "statement_period": period, "rows_parsed": inserted + skipped,
        "rows_inserted": inserted, "rows_skipped": skipped,
    }
    if imported_at:
        log["imported_at"] = imported_at
    bid = db.insert_import_log(log, conn)
    conn.commit()
    return bid


def _batch_tx(conn, bid, *, day, desc, amount, direction="debit",
              category="Groceries", is_flagged=0):
    add_tx(conn, day=day, desc=desc, amount=amount, direction=direction,
           category=category, is_flagged=is_flagged)
    conn.execute(
        "UPDATE transactions SET import_batch_id=? WHERE raw_description=?",
        (bid, desc))
    conn.commit()


def test_no_batches_means_unavailable(ledger_db):
    assert summarize_import(conn=ledger_db) == {"available": False}


def test_summary_counts_and_money(ledger_db):
    seed_month(ledger_db, "2026-06")
    bid = _make_batch(ledger_db, inserted=3, skipped=2,
                      imported_at="2026-07-17 10:00:00")
    _batch_tx(ledger_db, bid, day="2026-06-10", desc="B GROCER",
              amount=80.0)
    _batch_tx(ledger_db, bid, day="2026-06-11", desc="B PAY",
              amount=2500.0, direction="credit",
              category="Payroll Income")
    # Plumbing must NOT count toward income/spending added.
    _batch_tx(ledger_db, bid, day="2026-06-12", desc="B CCPAY",
              amount=500.0, category="Credit Card Payment")

    s = summarize_import(conn=ledger_db)
    assert s["available"] is True
    assert s["inserted"] == 3 and s["skipped"] == 2
    assert s["duplicates_only"] is False
    assert s["spending_added"] == pytest.approx(80.0)
    assert s["income_added"] == pytest.approx(2500.0)
    assert "2026-06" in s["months_touched"]
    assert s["data_through"]


def test_duplicates_only_state(ledger_db):
    seed_month(ledger_db, "2026-06")
    _make_batch(ledger_db, inserted=0, skipped=42,
                imported_at="2026-07-17 10:00:00")
    s = summarize_import(conn=ledger_db)
    assert s["duplicates_only"] is True
    assert s["inserted"] == 0 and s["skipped"] == 42


def test_materiality_split(ledger_db):
    bid = _make_batch(ledger_db, inserted=3,
                      imported_at="2026-07-17 10:00:00")
    # Material: big uncategorized purchase.
    _batch_tx(ledger_db, bid, day="2026-06-10", desc="BIG MYSTERY",
              amount=180.0, category="Uncategorized", is_flagged=1)
    # Material regardless of size: a fee row.
    _batch_tx(ledger_db, bid, day="2026-06-11", desc="TINY FEE",
              amount=4.5, category="Fees / Interest", is_flagged=1)
    # Deferrable: small uncategorized.
    _batch_tx(ledger_db, bid, day="2026-06-12", desc="SMALL MYSTERY",
              amount=6.0, category="Uncategorized", is_flagged=1)

    r = material_review_items(conn=ledger_db, batch_ids=[bid])
    assert r["material_count"] == 2
    assert r["deferred_count"] == 1
    assert r["material_total"] == pytest.approx(184.5)
    names = [i["raw_description"] for i in r["items"]]
    assert "BIG MYSTERY" in names and "TINY FEE" in names


def test_checkin_completion_clears_summary(ledger_db):
    seed_month(ledger_db, "2026-06")
    _make_batch(ledger_db, inserted=5,
                imported_at="2026-07-17 10:00:00")
    assert summarize_import(conn=ledger_db)["available"] is True
    # Completing a check-in AFTER the import marks it reviewed.
    ledger_db.execute(
        "INSERT INTO review_log (completed_at, planning_period) "
        "VALUES ('2026-07-17 11:00:00', '2026-07')")
    ledger_db.commit()
    assert summarize_import(conn=ledger_db) == {"available": False}


def test_session_window_groups_recent_batches_only(ledger_db):
    seed_month(ledger_db, "2026-06")
    _make_batch(ledger_db, "old.pdf", inserted=9,
                imported_at="2026-07-17 08:00:00")
    _make_batch(ledger_db, "a.pdf", inserted=3,
                imported_at="2026-07-17 10:00:00")
    _make_batch(ledger_db, "b.pdf", inserted=4,
                imported_at="2026-07-17 10:05:00")
    batches = latest_unreviewed_batches(conn=ledger_db)
    names = {b["filename"] for b in batches}
    # The 8:00 batch is outside the 15-minute session window.
    assert names == {"a.pdf", "b.pdf"}
    s = summarize_import(conn=ledger_db, batches=batches)
    assert s["inserted"] == 7

"""The analysis anchor is memoized per request. It must never go stale.

Home, Insights, Plan, Coach and patterns all ask how far the data reaches, and
each answer used to rescan every transaction date. One profiled Insights
request did that work 24 times. The memo removes the repeat scans, but a wrong
anchor is far worse than a slow one, so these tests pin the invalidation.
"""
from datetime import date

import pytest

from tests.conftest import add_tx
from utils import analysis_period
from utils.analysis_period import (
    _BOUNDS_MEMO,
    reset_supported_bounds_cache,
    supported_latest_date,
)


@pytest.fixture(autouse=True)
def _clean_memo():
    reset_supported_bounds_cache()
    yield
    reset_supported_bounds_cache()


def _seed_week(conn, month: str, start_day: int = 1, days: int = 6):
    for offset in range(days):
        add_tx(
            conn,
            day=f"{month}-{start_day + offset:02d}",
            desc=f"INVENTED MARKET {month}{start_day + offset}",
            amount=25.0,
            direction="debit",
            category="Groceries",
        )


def test_repeat_reads_scan_the_database_once(ledger_db, monkeypatch):
    _seed_week(ledger_db, "2026-07")

    calls = {"n": 0}
    real = analysis_period._compute_supported_bounds

    def counting(c, reference):
        calls["n"] += 1
        return real(c, reference)

    monkeypatch.setattr(analysis_period, "_compute_supported_bounds", counting)

    today = date(2026, 7, 31)
    for _ in range(10):
        supported_latest_date(conn=ledger_db, today=today)

    assert calls["n"] == 1, "the anchor should be computed once, not per caller"


def test_new_rows_invalidate_the_anchor_without_an_explicit_reset(ledger_db):
    """The failure mode that matters: import more data, get a stale answer."""
    _seed_week(ledger_db, "2026-06", start_day=1, days=6)
    today = date(2026, 7, 31)

    first = supported_latest_date(conn=ledger_db, today=today)
    assert first is not None and first.month == 6

    # No reset call. Writing on the same connection must be enough.
    _seed_week(ledger_db, "2026-07", start_day=10, days=6)
    second = supported_latest_date(conn=ledger_db, today=today)

    assert second is not None and second.month == 7, (
        "the anchor kept a pre-import answer after new rows were written"
    )
    assert second > first


def test_deleting_rows_also_invalidates_the_anchor(ledger_db):
    _seed_week(ledger_db, "2026-06", start_day=1, days=6)
    _seed_week(ledger_db, "2026-07", start_day=10, days=6)
    today = date(2026, 7, 31)

    assert supported_latest_date(conn=ledger_db, today=today).month == 7

    ledger_db.execute(
        "DELETE FROM transactions WHERE transaction_date >= '2026-07-01'"
    )
    ledger_db.commit()

    assert supported_latest_date(conn=ledger_db, today=today).month == 6


def test_different_reference_dates_do_not_share_an_answer(ledger_db):
    _seed_week(ledger_db, "2026-06", start_day=1, days=6)
    _seed_week(ledger_db, "2026-07", start_day=10, days=6)

    june_view = supported_latest_date(conn=ledger_db, today=date(2026, 6, 30))
    july_view = supported_latest_date(conn=ledger_db, today=date(2026, 7, 31))

    assert june_view is not None and june_view.month == 6
    assert july_view is not None and july_view.month == 7


def test_reset_clears_everything(ledger_db):
    _seed_week(ledger_db, "2026-07")
    supported_latest_date(conn=ledger_db, today=date(2026, 7, 31))
    assert _BOUNDS_MEMO

    reset_supported_bounds_cache()

    assert not _BOUNDS_MEMO


def test_the_engine_clears_the_memo_at_each_request_boundary(monkeypatch, tmp_path):
    """_bind_database runs once per request and is the engine's boundary."""
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    from desktop.engine import ledger_engine

    _BOUNDS_MEMO[(1, 1, "2026-07-31")] = (None, None, "none")
    ledger_engine._bind_database()

    assert not _BOUNDS_MEMO

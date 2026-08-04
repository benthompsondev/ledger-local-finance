"""One database's answer must never be read out of another database's cache.

All data is invented.

`_supported_bounds` memoizes "how far does the imported history reach" for
the duration of a request, because a single Insights request asked that
question twenty-four times. The memo was keyed on ``id(connection)``.

CPython reuses an object's id as soon as the object is freed, so a closed
connection's id can be handed straight to a new connection on a *completely
different* database. The write counter did not save us: a reader that has
written nothing reports ``total_changes == 0``, and so does a fresh empty
database, so the two keys matched exactly and the empty database answered
with the populated one's dates.

That produced a real, order-dependent suite failure — `test_freshness_no_data`
seeing `getting_stale` instead of `no_data` — which passed in isolation and
failed in a full run, because whether the id is reused depends on allocation
patterns that change between runs. A test that only passes sometimes is worse
than one that fails, so the reproduction below is deterministic: it builds the
exact contaminated state that id reuse produces, rather than waiting for luck.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from utils import analysis_period


def _populate(path, dates: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transactions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_date TEXT, "
        "direction TEXT DEFAULT 'debit')"
    )
    conn.executemany(
        "INSERT INTO transactions(transaction_date) VALUES(?)",
        [(d,) for d in dates],
    )
    conn.commit()
    return conn


def _empty(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transactions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_date TEXT, "
        "direction TEXT DEFAULT 'debit')"
    )
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def clean_memo():
    analysis_period.reset_supported_bounds_cache()
    yield
    analysis_period.reset_supported_bounds_cache()


def test_an_empty_database_is_empty_however_the_cache_is_keyed(tmp_path):
    """The deterministic reproduction.

    Rather than waiting for CPython to reuse an id, this builds the state
    that reuse produces: a memo entry recorded against the identity that a
    different database's connection now answers to. Under the old key shape
    that entry was a direct hit.
    """
    reference = date(2026, 7, 20)
    busy = _populate(tmp_path / "busy.db",
                     ["2026-07-06", "2026-07-08", "2026-07-10"])
    try:
        first, last, _ = analysis_period._supported_bounds(busy, reference)
        assert first is not None and last is not None
    finally:
        busy.close()

    empty = _empty(tmp_path / "empty.db")
    try:
        # Exactly what id reuse leaves behind: an entry whose identity
        # component matches this connection, whose write counter matches
        # (both are 0 for a reader), and whose reference date matches.
        analysis_period._BOUNDS_MEMO.clear()
        analysis_period._BOUNDS_MEMO[
            (id(empty), empty.total_changes, reference.isoformat())
        ] = (date(2026, 7, 6), date(2026, 7, 10), "high")

        first, last, _ = analysis_period._supported_bounds(empty, reference)

        assert first is None and last is None, (
            "an empty database answered with another database's dates"
        )
    finally:
        empty.close()


def test_the_memo_is_not_keyed_on_a_reusable_identity(tmp_path):
    """Locks the model-level fix, not the symptom.

    id() is the only part of the old key that a different object could ever
    match, so a key that no longer contains a bare id cannot be hit by a
    different connection at all.
    """
    reference = date(2026, 7, 20)
    busy = _populate(tmp_path / "busy.db",
                     ["2026-07-06", "2026-07-08", "2026-07-10"])
    try:
        analysis_period._supported_bounds(busy, reference)
        assert analysis_period._BOUNDS_MEMO, "nothing was cached to inspect"
        (key,) = analysis_period._BOUNDS_MEMO
        assert key[0] is busy, (
            "the memo identifies the connection by a value another object "
            f"can hold: {key[0]!r}"
        )
        assert key[0] != id(busy)
    finally:
        busy.close()


def test_two_live_databases_do_not_share_an_answer(tmp_path):
    """The plain property, with both connections alive at once."""
    reference = date(2026, 7, 20)
    busy = _populate(tmp_path / "busy.db",
                     ["2026-07-06", "2026-07-08", "2026-07-10"])
    empty = _empty(tmp_path / "empty.db")
    try:
        busy_first, busy_last, _ = analysis_period._supported_bounds(
            busy, reference)
        empty_first, empty_last, _ = analysis_period._supported_bounds(
            empty, reference)

        assert busy_first is not None and busy_last is not None
        assert empty_first is None and empty_last is None
        # And back again: the second read must not have poisoned the first.
        again_first, again_last, _ = analysis_period._supported_bounds(
            busy, reference)
        assert (again_first, again_last) == (busy_first, busy_last)
    finally:
        busy.close()
        empty.close()


def test_a_reused_identity_across_many_connections_stays_isolated(tmp_path):
    """Opportunistic: actually try to make CPython reuse the id.

    This is the real-world mechanism. It is written so that it asserts the
    same property whether or not the reuse happens on a given run, so it can
    never be a flaky test — it simply gets stronger when reuse occurs.
    """
    reference = date(2026, 7, 20)
    busy = _populate(tmp_path / "busy.db",
                     ["2026-07-06", "2026-07-08", "2026-07-10"])
    analysis_period._supported_bounds(busy, reference)
    freed_id = id(busy)
    busy.close()
    del busy

    reused = False
    for index in range(200):
        empty = _empty(tmp_path / f"empty-{index}.db")
        try:
            if id(empty) == freed_id:
                reused = True
            first, last, _ = analysis_period._supported_bounds(
                empty, reference)
            assert first is None and last is None, (
                f"empty database {index} answered with cached dates "
                f"(id reused: {id(empty) == freed_id})"
            )
        finally:
            empty.close()
        if reused:
            break
    # Not asserted: whether reuse happened is up to the allocator. The
    # property above held either way, which is the point.


def test_writing_still_invalidates_the_cache_on_one_connection(tmp_path):
    """The behaviour the memo exists for must survive the key change."""
    reference = date(2026, 7, 20)
    conn = _empty(tmp_path / "ledger.db")
    try:
        first, last, _ = analysis_period._supported_bounds(conn, reference)
        assert first is None and last is None

        conn.executemany(
            "INSERT INTO transactions(transaction_date) VALUES(?)",
            [("2026-07-06",), ("2026-07-08",), ("2026-07-10",)],
        )
        conn.commit()

        first, last, _ = analysis_period._supported_bounds(conn, reference)
        assert first == date(2026, 7, 6)
        assert last == date(2026, 7, 10)
    finally:
        conn.close()


def test_the_same_connection_still_hits_the_cache(tmp_path):
    """A fix that simply disabled the memo would pass every test above."""
    reference = date(2026, 7, 20)
    conn = _populate(tmp_path / "ledger.db",
                     ["2026-07-06", "2026-07-08", "2026-07-10"])
    try:
        analysis_period._supported_bounds(conn, reference)
        assert len(analysis_period._BOUNDS_MEMO) == 1

        calls = {"count": 0}
        real = analysis_period._compute_supported_bounds

        def counted(c, ref):
            calls["count"] += 1
            return real(c, ref)

        analysis_period._compute_supported_bounds = counted
        try:
            for _ in range(5):
                analysis_period._supported_bounds(conn, reference)
        finally:
            analysis_period._compute_supported_bounds = real

        assert calls["count"] == 0, (
            "the memo stopped memoizing, so every screen rescans every date"
        )
    finally:
        conn.close()

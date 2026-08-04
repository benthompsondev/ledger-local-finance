"""Public-beta trust regressions for the Home dashboard payload.

All rows are invented. These cases cover the Home cashflow chart, which reads
month completeness to decide which months it is allowed to reason about.
"""
from __future__ import annotations

import io
import json

import pytest

from desktop.engine import ledger_engine
from tests.conftest import add_tx, seed_month


def _request(payload: dict) -> dict:
    """Run one one-shot engine request exactly like the Tauri shell does."""
    output = io.StringIO()
    ledger_engine.run(io.StringIO(json.dumps(payload) + "\n"), output)
    body = json.loads(output.getvalue())
    assert body["ok"], body
    return body["data"]


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def gapped_history(engine_env, monkeypatch, tmp_path):
    """Four whole months plus one that was only imported to the 8th."""
    import utils.database as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    conn = db.get_connection()
    for month in ("2026-01", "2026-02", "2026-04", "2026-05"):
        seed_month(conn, month)
    # March stops on the 8th: a real hole in the imported history.
    add_tx(conn, day="2026-03-01", desc="LANDLORD RENT", amount=1800.0,
           direction="debit", category="Housing / Mortgage")
    add_tx(conn, day="2026-03-05", desc="EMPLOYER PAYROLL", amount=2500.0,
           direction="credit", category="Payroll Income")
    for day in (2, 3, 6, 8):
        add_tx(conn, day=f"2026-03-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")
    conn.commit()
    conn.close()
    return tmp_path


def test_statement_coverage_calls_the_half_imported_month_partial(
    gapped_history,
) -> None:
    """Guards the premise of the test below."""
    import utils.database as db
    from utils.insights import statement_coverage

    conn = db.get_connection()
    try:
        coverage = statement_coverage(conn=conn)
    finally:
        conn.close()

    assert "2026-03" in coverage["partial_months"]
    assert "2026-03" not in coverage["complete_months"]


def test_home_cashflow_never_calls_a_partial_month_complete(
    gapped_history,
) -> None:
    """A half-imported month must not be charted as a finished one.

    The chart reads complete months to pick the best and weakest month, the
    typical savings rate and the trend direction. Deciding completeness by
    comparing the month against the calendar instead of reading the canonical
    coverage flag let a month with eight days of data set those figures.
    """
    payload = _request({"action": "home_dashboard", "params": {"days": 30}})

    charted = {row["month"]: row["complete"] for row in payload["cashflow"]}
    assert charted["2026-03"] is False, charted
    for month in ("2026-01", "2026-02", "2026-04", "2026-05"):
        assert charted[month] is True, charted


def test_home_cashflow_completeness_matches_monthly_aggregates(
    gapped_history,
) -> None:
    """One canonical answer, not two implementations of the same rule."""
    import utils.database as db
    from utils.insights import monthly_aggregates

    conn = db.get_connection()
    try:
        canonical = {row["month"]: bool(row["complete"])
                     for row in monthly_aggregates(conn=conn)}
    finally:
        conn.close()

    payload = _request({"action": "home_dashboard", "params": {"days": 30}})

    for row in payload["cashflow"]:
        assert row["complete"] == canonical[row["month"]], (
            f"{row['month']}: chart said {row['complete']}, "
            f"monthly_aggregates said {canonical[row['month']]}"
        )


@pytest.fixture
def month_nearly_over(engine_env, monkeypatch, tmp_path):
    """Two finished months, plus a current month imported to the 28th.

    Three days of the month are still to come and only one of its two
    paydays has landed, so treating it as finished understates both its
    income and its spending.
    """
    import datetime

    import utils.analysis_period as analysis_period
    import utils.database as db
    import utils.insights as insights

    frozen = datetime.date(2026, 8, 28)

    class _Frozen(datetime.date):
        @classmethod
        def today(cls):
            return frozen

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    conn = db.get_connection()
    for month in ("2026-06", "2026-07"):
        seed_month(conn, month)
    add_tx(conn, day="2026-08-01", desc="LANDLORD RENT", amount=1800.0,
           direction="debit", category="Housing / Mortgage")
    add_tx(conn, day="2026-08-05", desc="EMPLOYER PAYROLL", amount=2500.0,
           direction="credit", category="Payroll Income")
    for day in range(2, 18):
        add_tx(conn, day=f"2026-08-{day:02d}", desc=f"GROCER {day}",
               amount=30.0, direction="debit", category="Groceries")
    add_tx(conn, day="2026-08-28", desc="COFFEE STOP", amount=6.0,
           direction="debit", category="Food & Convenience")
    conn.commit()
    conn.close()

    # Both modules matter. statement_coverage reads its own clock, and it
    # takes its date bounds from analysis_period, which caps at the reference
    # day. Freezing only one leaves the August rows looking future-dated, and
    # the month is then partial for the wrong reason.
    monkeypatch.setattr(insights, "date", _Frozen)
    monkeypatch.setattr(analysis_period, "date", _Frozen)
    analysis_period.reset_supported_bounds_cache()
    return tmp_path


def test_a_month_still_running_is_never_complete(month_nearly_over) -> None:
    """The remaining days are not missing data, they have not happened.

    The end-of-month tolerance cannot tell "this statement stopped on the
    28th" apart from "today is the 28th", so without an explicit check a
    current month satisfies every coverage threshold in its final week.
    """
    import utils.database as db
    from utils.insights import statement_coverage

    conn = db.get_connection()
    try:
        coverage = statement_coverage(conn=conn)
    finally:
        conn.close()

    assert "2026-08" in coverage["partial_months"], coverage
    assert coverage["complete_months"] == ["2026-06", "2026-07"], coverage


def test_a_part_month_never_sets_the_typical_income_baseline(
    month_nearly_over,
) -> None:
    """Averaging in a month holding one of its two paydays quotes low."""
    import utils.database as db
    from utils.analytics import typical_monthly_income

    conn = db.get_connection()
    try:
        baseline = typical_monthly_income(conn=conn)
    finally:
        conn.close()

    assert baseline["amount"] == 5000.00, baseline
    assert baseline["months_used"] == 2, baseline


def test_a_part_month_is_not_charted_against_finished_months(
    month_nearly_over,
) -> None:
    payload = _request({"action": "home_dashboard", "params": {"days": 30}})

    charted = {row["month"]: row["complete"] for row in payload["cashflow"]}
    assert charted["2026-08"] is False, charted
    assert charted["2026-07"] is True, charted

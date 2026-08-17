"""Monthly plan timestamps use one explicit UTC convention on new writes."""
from __future__ import annotations


def _plan(month: str, savings: float = 500.0) -> dict:
    return {
        "month": month,
        "mode": "normal",
        "income_target": 5000.0,
        "spending_target": 4000.0,
        "savings_target": savings,
    }


def test_new_plan_gets_matching_explicit_utc_stamps(ledger_db, monkeypatch):
    import utils.database as db

    monkeypatch.setattr(db, "_utc_timestamp",
                        lambda: "2026-07-01T03:30:00Z")
    db.upsert_monthly_plan(_plan("2026-07"), conn=ledger_db)

    row = ledger_db.execute(
        "SELECT created_at, updated_at FROM monthly_plans WHERE month='2026-07'"
    ).fetchone()
    assert row["created_at"] == "2026-07-01T03:30:00Z"
    assert row["updated_at"] == "2026-07-01T03:30:00Z"


def test_resave_preserves_creation_and_refreshes_update_in_utc(
    ledger_db, monkeypatch,
):
    import utils.database as db

    stamps = iter(("2026-07-01T03:30:00Z", "2026-07-15T16:45:00Z"))
    monkeypatch.setattr(db, "_utc_timestamp", lambda: next(stamps))
    db.upsert_monthly_plan(_plan("2026-07"), conn=ledger_db)
    db.upsert_monthly_plan(_plan("2026-07", savings=750.0), conn=ledger_db)

    row = ledger_db.execute(
        "SELECT created_at, updated_at, savings_target FROM monthly_plans "
        "WHERE month='2026-07'"
    ).fetchone()
    assert row["created_at"] == "2026-07-01T03:30:00Z"
    assert row["updated_at"] == "2026-07-15T16:45:00Z"
    assert row["savings_target"] == 750.0


def test_utc_stamps_do_not_repeat_during_dst_fallback(ledger_db, monkeypatch):
    """Two local 01:30 instants remain distinct because storage is UTC."""
    import utils.database as db

    stamps = iter(("2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"))
    monkeypatch.setattr(db, "_utc_timestamp", lambda: next(stamps))
    db.upsert_monthly_plan(_plan("2026-11"), conn=ledger_db)
    db.upsert_monthly_plan(_plan("2026-11", savings=650.0), conn=ledger_db)

    row = ledger_db.execute(
        "SELECT created_at, updated_at FROM monthly_plans WHERE month='2026-11'"
    ).fetchone()
    assert row["created_at"] == "2026-11-01T05:30:00Z"
    assert row["updated_at"] == "2026-11-01T06:30:00Z"

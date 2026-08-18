"""Persistence timestamps use SQLite's UTC convention on every write path."""
from __future__ import annotations

from datetime import datetime, timezone

import utils.database as db


def _assert_recent_utc(value: str) -> None:
    stored = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now_utc - stored).total_seconds()) < 5


def test_learned_rule_writes_keep_sqlite_utc_time_base(ledger_db):
    rule_id = db.upsert_learned_rule(
        "SYNTHETIC MERCHANT", "Groceries", conn=ledger_db,
    )
    db.set_learned_rule_enabled(
        "SYNTHETIC MERCHANT", False, conn=ledger_db,
    )
    db.update_learned_rule_by_id(
        rule_id, category="Food & Convenience", conn=ledger_db,
    )

    row = ledger_db.execute(
        "SELECT updated_at FROM learned_rules WHERE id=?", (rule_id,)
    ).fetchone()
    _assert_recent_utc(row["updated_at"])


def test_goal_writes_keep_sqlite_utc_time_base(ledger_db):
    goal_id = db.insert_goal({
        "name": "Synthetic reserve",
        "type": "emergency_fund",
        "target_amount": 1000.0,
        "current_amount": 100.0,
        "status": "active",
        "progress_method": "manual",
        "include_in_plan": 0,
        "currency": "CAD",
    }, conn=ledger_db)
    db.update_goal(goal_id, {"target_amount": 1200.0}, conn=ledger_db)
    row = ledger_db.execute(
        "SELECT created_at, updated_at FROM goal_targets WHERE id=?",
        (goal_id,),
    ).fetchone()
    _assert_recent_utc(row["created_at"])
    _assert_recent_utc(row["updated_at"])

    db.record_goal_contribution(
        goal_id, 25.0, contributed_on="2026-08-18", conn=ledger_db,
    )
    updated = ledger_db.execute(
        "SELECT updated_at FROM goal_targets WHERE id=?", (goal_id,)
    ).fetchone()
    _assert_recent_utc(updated["updated_at"])

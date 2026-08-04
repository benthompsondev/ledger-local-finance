"""Sanitized regression coverage for the Northstar 0.6.0 daily-use pass."""
from __future__ import annotations

import utils.database as db
from desktop.engine.ledger_engine import (
    correct_transaction_action,
    list_transactions_action,
    plan_summary_action,
)
from utils.analytics import find_recurring


def _insert(
    conn, *, day: str, description: str, amount: float, category: str,
    category_source: str = "import_rule", transaction_type: str = "spending",
    import_batch_id: int | None = None, manually_edited_at: str | None = None,
) -> None:
    ok, reason = db.insert_transaction({
        "account_type": "chequing",
        "transaction_date": day,
        "raw_description": description,
        "merchant": description.title(),
        "amount": amount,
        "direction": "credit" if amount > 0 else "debit",
        "category": category,
        "category_source": category_source,
        "transaction_type": transaction_type,
        "import_batch_id": import_batch_id,
        "manually_edited_at": manually_edited_at,
    }, conn)
    assert ok, reason


def test_upgrade_refreshes_safe_categories_and_preserves_user_edits(
    ledger_db,
) -> None:
    batch_id = db.insert_import_log({
        "filename": "invented-history.csv",
        "file_hash": "invented-v2-batch",
        "account_type": "Generic CSV",
        "rows_parsed": 2,
        "rows_inserted": 2,
        "semantics_version": 2,
    }, ledger_db)
    _insert(
        ledger_db, day="2026-06-01",
        description="SAMPLE MORTGAGE MONTHLY PAYMENT", amount=-900,
        category="Uncategorized", import_batch_id=batch_id,
    )
    _insert(
        ledger_db, day="2026-06-02",
        description="SAMPLE MORTGAGE HARDWARE SHOP", amount=-45,
        category="Home Improvement", category_source="user_edit",
        transaction_type="spending", import_batch_id=batch_id,
        manually_edited_at="2026-06-03 12:00:00",
    )
    ledger_db.execute(
        "DELETE FROM app_migrations WHERE migration_key=?",
        (db._BUILTIN_CATEGORIZATION_MIGRATION,),
    )
    ledger_db.commit()
    ledger_db.close()

    db.init_db()
    upgraded = db.get_connection()
    try:
        rows = upgraded.execute(
            "SELECT amount,category,category_source,transaction_type,"
            "manually_edited_at FROM transactions ORDER BY transaction_date"
        ).fetchall()
        assert rows[0]["amount"] == -900
        assert rows[0]["category"] == "Housing / Mortgage"
        assert rows[0]["transaction_type"] == "mortgage_payment"
        assert rows[1]["category"] == "Home Improvement"
        assert rows[1]["category_source"] == "user_edit"
        assert rows[1]["manually_edited_at"] == "2026-06-03 12:00:00"
        migration = upgraded.execute(
            "SELECT rows_scanned,rows_changed FROM app_migrations "
            "WHERE migration_key=?",
            (db._BUILTIN_CATEGORIZATION_MIGRATION,),
        ).fetchone()
        assert tuple(migration) == (1, 1)
    finally:
        upgraded.close()

    # A second launch is idempotent and keeps the signed source amount.
    db.init_db()
    check = db.get_connection()
    try:
        assert check.execute(
            "SELECT amount FROM transactions ORDER BY id LIMIT 1"
        ).fetchone()[0] == -900
    finally:
        check.close()


def test_suggested_review_filter_and_one_click_correction(ledger_db) -> None:
    _insert(
        ledger_db, day="2026-06-01", description="SAMPLE NEIGHBOURHOOD MARKET",
        amount=-30, category="Groceries", category_source="user_edit",
    )
    _insert(
        ledger_db, day="2026-07-01", description="SAMPLE NEIGHBOURHOOD MARKET",
        amount=-42, category="Uncategorized",
    )
    ledger_db.commit()

    payload = list_transactions_action({"suggested_only": True, "limit": 50})
    assert payload["total"] == 1
    assert payload["review"]["suggested"] == 1
    row = payload["transactions"][0]
    assert row["suggested_category"] == "Groceries"
    assert "Used for this merchant" in row["suggestion_reason"]

    correct_transaction_action({
        "id": row["id"], "category": row["suggested_category"],
        "note": "", "apply_to_matching": False, "remember_rule": False,
    })
    refreshed = list_transactions_action({"suggested_only": True, "limit": 50})
    assert refreshed["total"] == 0
    assert refreshed["review"]["suggested"] == 0


def test_plan_hides_forecast_until_required_inputs_are_ready(ledger_db) -> None:
    _insert(
        ledger_db, day="2026-07-01", description="SAMPLE PAYROLL",
        amount=2500, category="Payroll Income",
        category_source="protected", transaction_type="employment_income",
    )
    _insert(
        ledger_db, day="2026-07-02", description="SAMPLE GROCER",
        amount=-80, category="Groceries",
    )
    ledger_db.commit()

    payload = plan_summary_action({})
    assert payload["safe_to_spend"]["available"] is False
    assert payload["readiness"]["forecast_ready"] is False
    assert payload["readiness"]["plan_confirmed"] is False
    assert payload["readiness"]["balance_count"] == 0
    missing_labels = [item["label"] for item in payload["readiness"]["missing"]]
    advisory_labels = [
        item["label"] for item in payload["readiness"]["advisories"]
    ]
    assert "Confirm this month’s plan" in missing_labels
    plan_item = next(
        item for item in payload["readiness"]["missing"]
        if item["label"] == "Confirm this month’s plan"
    )
    assert plan_item["screen"] == "plan#commitment-review"
    assert "Add Chequing balance" in advisory_labels


def test_recurring_cost_includes_explainable_next_date(ledger_db) -> None:
    for day in ("2026-04-05", "2026-05-05", "2026-06-05"):
        _insert(
            ledger_db, day=day, description="SAMPLE HOME INTERNET",
            amount=-75, category="Utilities / Bills",
        )
    ledger_db.commit()

    recurring = find_recurring(conn=ledger_db)
    assert len(recurring) == 1
    assert recurring[0]["cadence"] == "monthly"
    assert recurring[0]["last_seen"] == "2026-06-05"
    assert recurring[0]["expected_next"] == "2026-07-05"


def test_repeated_variable_merchant_is_watch_only_until_confirmed(ledger_db) -> None:
    for day in ("2026-04-05", "2026-05-05", "2026-06-05"):
        _insert(
            ledger_db, day=day, description="SAMPLE NEIGHBOURHOOD GROCER",
            amount=-75, category="Groceries",
        )
    ledger_db.commit()

    recurring = find_recurring(conn=ledger_db)
    assert len(recurring) == 1
    assert recurring[0]["planning_relevant"] is False

    merchant_key = ledger_db.execute(
        "SELECT merchant_normalized FROM transactions LIMIT 1"
    ).fetchone()[0]
    ledger_db.execute(
        "INSERT INTO recurring_preferences "
        "(merchant_normalized,status,updated_at) VALUES (?,?,datetime('now'))",
        (merchant_key, "recurring"),
    )
    ledger_db.commit()

    recurring = find_recurring(conn=ledger_db)
    assert len(recurring) == 1
    assert recurring[0]["category"] == "Groceries"
    assert recurring[0]["recurring_status"] == "confirmed"
    assert recurring[0]["planning_relevant"] is True

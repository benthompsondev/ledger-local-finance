"""Financial-comparison regressions for statement-aware month coverage.

Every row is invented.  These tests model the difference between a household
month assembled from unrelated account fragments and one account imported in
several legitimate files.
"""
from __future__ import annotations

from datetime import date

import pytest


def _account(conn, name: str, kind: str = "chequing") -> int:
    from utils.database import create_account

    return int(create_account({"name": name, "type": kind}, conn=conn))


def _batch(conn, account_id: int, name: str, rows: list[tuple[str, str, float]],
           *, statement_summary: dict | None = None) -> int:
    from utils.import_service import persist_import_batch

    txs = []
    for day, description, amount in rows:
        txs.append({
            "account_ref": account_id,
            "account_type": "chequing",
            "transaction_date": day,
            "raw_description": description,
            "merchant": description,
            "amount": amount,
            "direction": "credit" if amount > 0 else "debit",
            "category": "Payroll Income" if amount > 0 else "Groceries",
            "transaction_type": "income" if amount > 0 else "spending",
            "statement_period": name,
        })
    result = persist_import_batch(
        {
            "filename": f"{name}.csv",
            "file_hash": f"invented-{name}",
            "account_type": "chequing",
            "statement_period": name,
            "rows_parsed": len(txs),
        },
        txs,
        account_id=str(account_id),
        statement_summary=statement_summary,
        statement_account_type="chequing",
        import_mode="new",
        conn=conn,
    )
    conn.commit()
    return int(result["batch_id"])


def _whole_month(conn, account_id: int, month: str, *, spending: float) -> None:
    _batch(conn, account_id, f"{month}-whole", [
        (f"{month}-01", f"{month} PAYROLL", 2000.00),
        (f"{month}-05", f"{month} GROCER A", -spending / 3),
        (f"{month}-16", f"{month} GROCER B", -spending / 3),
        (f"{month}-28", f"{month} GROCER C", -spending / 3),
    ])


def _july_like_history(conn) -> None:
    account_id = _account(conn, "Invented Everyday Account")
    for month, spending in (("2026-05", 300.0), ("2026-06", 600.0)):
        _whole_month(conn, account_id, month, spending=spending)

    # One account, two normal exports. Neither file spans July by itself, but
    # together they carry substantial, ordered coverage from the 1st to 31st.
    _batch(conn, account_id, "2026-07-a", [
        ("2026-07-01", "JULY PAYROLL", 2000.0),
        ("2026-07-04", "JULY GROCER 04", -100.0),
        ("2026-07-09", "JULY GROCER 09", -100.0),
        ("2026-07-15", "JULY GROCER 15", -100.0),
    ])
    _batch(conn, account_id, "2026-07-b", [
        ("2026-07-17", "JULY GROCER 17", -100.0),
        ("2026-07-22", "JULY GROCER 22", -100.0),
        ("2026-07-28", "JULY GROCER 28", -100.0),
        ("2026-07-31", "JULY GROCER 31", -100.0),
    ])
    _batch(conn, account_id, "2026-08-partial", [
        ("2026-08-02", "AUGUST GROCER 02", -50.0),
        ("2026-08-10", "AUGUST GROCER 10", -75.0),
    ])


def test_july_like_multi_file_month_drives_every_comparison(ledger_db) -> None:
    from utils.insights import (
        category_pace_breakdown, monthly_aggregates, spending_pace,
        statement_coverage,
    )

    _july_like_history(ledger_db)

    coverage = statement_coverage(conn=ledger_db)
    july = coverage["statement_coverage_by_month"]["2026-07"]
    aggregates = {row["month"]: row for row in monthly_aggregates(conn=ledger_db)}
    pace = spending_pace(conn=ledger_db, today=date(2026, 8, 10))
    categories = category_pace_breakdown(
        conn=ledger_db, today=date(2026, 8, 10),
    )

    assert july["complete"] is True, july["reason"]
    assert july["coverage_basis"] == "account_imports"
    assert aggregates["2026-07"]["complete"] is True
    assert pace["previous_total_same_day"] == pytest.approx(200.0)
    assert pace["average_90_day_month_count"] == 3
    assert categories["comparison_available"] is True
    assert categories["average_month_count"] == 3
    groceries = next(row for row in categories["items"] if row["category"] == "Groceries")
    assert groceries["previous"] == pytest.approx(200.0)


def test_cross_account_mosaic_remains_partial(ledger_db) -> None:
    from utils.insights import statement_coverage

    early = _account(ledger_db, "Invented Early Account")
    late = _account(ledger_db, "Invented Late Card", "credit_card")
    _batch(ledger_db, early, "early", [
        ("2026-07-01", "EARLY 01", -10.0),
        ("2026-07-05", "EARLY 05", -10.0),
        ("2026-07-09", "EARLY 09", -10.0),
    ])
    _batch(ledger_db, late, "late", [
        ("2026-07-22", "LATE 22", -10.0),
        ("2026-07-27", "LATE 27", -10.0),
        ("2026-07-31", "LATE 31", -10.0),
    ])

    july = statement_coverage(conn=ledger_db)[
        "statement_coverage_by_month"
    ]["2026-07"]
    assert july["complete"] is False
    assert july["coverage_basis"] == "none"


def test_singleton_import_fragments_do_not_manufacture_coverage(ledger_db) -> None:
    from utils.insights import statement_coverage

    account_id = _account(ledger_db, "Invented Fragmented Account")
    for day in (1, 15, 31):
        _batch(ledger_db, account_id, f"fragment-{day}", [
            (f"2026-07-{day:02d}", f"FRAGMENT {day}", -10.0),
        ])

    july = statement_coverage(conn=ledger_db)[
        "statement_coverage_by_month"
    ]["2026-07"]
    assert july["complete"] is False
    assert july["coverage_basis"] == "none"


def test_authoritative_statement_dates_cover_a_quiet_month(ledger_db) -> None:
    from utils.insights import statement_coverage

    account_id = _account(ledger_db, "Invented Quiet Account")
    _batch(
        ledger_db,
        account_id,
        "quiet-july",
        [("2026-07-15", "ONLY JULY PURCHASE", -25.0)],
        statement_summary={
            "statement_period_label": "July 2026",
            "statement_start_date": "2026-07-01",
            "statement_end_date": "2026-07-31",
        },
    )

    july = statement_coverage(conn=ledger_db)[
        "statement_coverage_by_month"
    ]["2026-07"]
    assert july["complete"] is True
    assert july["coverage_basis"] == "statement_dates"


def test_home_and_insights_share_july_completeness_and_cashflow(ledger_db) -> None:
    from desktop.engine.ledger_engine import (
        home_dashboard_action, insights_summary_action,
    )

    _july_like_history(ledger_db)
    home = home_dashboard_action({"period_days": 30})
    insights = insights_summary_action({"period_days": 30})

    home_july = next(row for row in home["cashflow"] if row["month"] == "2026-07")
    insights_july = next(
        row for row in insights["monthly"] if row["month"] == "2026-07"
    )
    for field in ("complete", "income", "spending", "net", "savings_rate"):
        assert home_july[field] == insights_july[field]
    assert home_july["complete"] is True

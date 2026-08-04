"""
Shared fixtures for Ledger's focused regression tests.

Each test gets an isolated SQLite database (tmp_path) by overriding
utils.database.DB_PATH — the documented override point ("Tests / smoke
override DB_PATH directly"). All rows are synthetic and deterministic;
no real statements, no demo DB, no network.
"""
from __future__ import annotations

import calendar
from datetime import date

import pytest

import utils.database as db


@pytest.fixture()
def ledger_db(tmp_path, monkeypatch):
    """Empty initialized Ledger DB. Returns an open connection."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_ledger.db")
    db.init_db()
    conn = db.get_connection()
    yield conn
    conn.close()


def add_tx(conn, *, day, desc, amount, direction, category,
           account_type="chequing", merchant=None, is_transfer=0,
           is_flagged=0):
    from utils.financial_semantics import canonicalize_transaction

    signed_amount = abs(float(amount)) if direction == "credit" else -abs(float(amount))
    tx = canonicalize_transaction({
        "account_type": account_type,
        "transaction_date": day,
        "raw_description": desc,
        "merchant": merchant or desc,
        "amount": signed_amount,
        "direction": direction,
        "category": category,
        "is_transfer": is_transfer,
        "is_flagged": is_flagged,
    })
    ok, msg = db.insert_transaction(tx, conn)
    assert ok, f"fixture insert failed: {msg} ({desc} {day})"


def seed_month(conn, month: str, *, rent=1800.0, hydro=90.0, sub=15.0,
               payroll=2500.0, grocery_days=16, grocery_amount=30.0,
               extra=None):
    """One synthetic 'complete' month: payroll ×2, fixed bills, one
    subscription, groceries spread over enough distinct days, and a
    late-month row so statement_coverage marks the month complete."""
    y, m = int(month[:4]), int(month[5:7])
    last_day = calendar.monthrange(y, m)[1]

    add_tx(conn, day=f"{month}-01", desc="LANDLORD RENT", amount=rent,
           direction="debit", category="Housing / Mortgage")
    add_tx(conn, day=f"{month}-05", desc="HYDRO CO", amount=hydro,
           direction="debit", category="Utilities / Bills")
    add_tx(conn, day=f"{month}-10", desc="STREAMCO", amount=sub,
           direction="debit", category="Subscriptions & Digital",
           account_type="mastercard")
    add_tx(conn, day=f"{month}-05", desc="EMPLOYER PAYROLL", amount=payroll,
           direction="credit", category="Payroll Income")
    add_tx(conn, day=f"{month}-19", desc="EMPLOYER PAYROLL 2", amount=payroll,
           direction="credit", category="Payroll Income")
    for d in range(2, 2 + grocery_days):
        add_tx(conn, day=f"{month}-{d:02d}", desc=f"GROCER {d}",
               amount=grocery_amount, direction="debit",
               category="Groceries")
    # Late-month anchor so days_until_eom <= 7 (complete-month rule).
    add_tx(conn, day=f"{month}-{last_day - 2:02d}", desc="COFFEE STOP",
           amount=6.0, direction="debit", category="Food & Convenience")

    # Internal money movement that must NEVER count as income/spending:
    add_tx(conn, day=f"{month}-20", desc="PAYMENT TO MASTERCARD",
           amount=500.0, direction="debit",
           category="Credit Card Payment", is_transfer=1)
    add_tx(conn, day=f"{month}-20", desc="PAYMENT RECEIVED THANK YOU",
           amount=500.0, direction="credit",
           category="Credit Card Payment",
           account_type="mastercard", is_transfer=1)
    add_tx(conn, day=f"{month}-21", desc="TO SAVINGS ACCOUNT",
           amount=400.0, direction="debit",
           category="Internal Transfer", is_transfer=1)

    for row in (extra or []):
        add_tx(conn, **row)
    conn.commit()


def save_plan(conn, month: str, *, income=5000.0, spending=3400.0,
              savings=800.0, mode="normal"):
    from utils.planner import bills_and_commitments

    fixed = round(float(
        bills_and_commitments(conn=conn).get(
            "commitment_monthly_estimate"
        ) or 0
    ), 2)
    plan_id = db.upsert_monthly_plan({
        "month": month, "mode": mode,
        "income_target": income, "spending_target": spending,
        "savings_target": savings,
        "fixed_obligations": fixed,
        "flexible_allowance": max(0.0, spending - fixed),
        "detected_commitments_at_save": fixed,
        "fixed_override_reason": "",
        "notes": "test plan",
    }, conn=conn)
    conn.commit()
    return plan_id


def seed_balances(conn, as_of: str, *, liquid=6000.0, card=500.0):
    db.insert_account_balance({
        "account_name": "Synthetic Chequing", "account_kind": "chequing",
        "balance": liquid, "currency": "CAD", "as_of_date": as_of,
    }, conn=conn)
    db.insert_account_balance({
        "account_name": "Synthetic Card", "account_kind": "credit_card",
        "balance": card, "currency": "CAD", "as_of_date": as_of,
    }, conn=conn)
    conn.commit()


@pytest.fixture()
def steady_db(ledger_db):
    """Three complete months (2026-04..06) + a partial July through
    07-05, with a saved July plan. 'Today' for tests: 2026-07-06."""
    conn = ledger_db
    seed_month(conn, "2026-04")
    seed_month(conn, "2026-05", extra=[
        dict(day="2026-05-12", desc="BIG BOX STORE", amount=50.0,
             direction="debit", category="Shopping"),
    ])
    # June adds a material comparable Shopping jump (+$150) and an immaterial
    # Pets blip (+$10) to exercise the materiality threshold.
    seed_month(conn, "2026-06", extra=[
        dict(day="2026-06-12", desc="BIG BOX STORE", amount=200.0,
             direction="debit", category="Shopping"),
        dict(day="2026-06-13", desc="PET TREATS", amount=10.0,
             direction="debit", category="Pets"),
    ])
    # Partial July: payroll + a few groceries, data through 07-05.
    add_tx(conn, day="2026-07-03", desc="EMPLOYER PAYROLL", amount=2500.0,
           direction="credit", category="Payroll Income")
    for d in (2, 3, 4, 5):
        add_tx(conn, day=f"2026-07-{d:02d}", desc=f"GROCER J{d}",
               amount=30.0, direction="debit", category="Groceries")
    save_plan(conn, "2026-07")
    seed_balances(conn, "2026-07-05")
    conn.commit()
    return conn


@pytest.fixture()
def ended_db(ledger_db):
    """Data ends exactly on the last day of the newest month (June 30),
    so the planning period has zero days remaining."""
    conn = ledger_db
    for month in ("2026-05", "2026-06"):
        seed_month(conn, month)
    add_tx(conn, day="2026-06-30", desc="LAST DAY LUNCH", amount=12.0,
           direction="debit", category="Food & Convenience")
    save_plan(conn, "2026-06")
    seed_balances(conn, "2026-06-30")
    conn.commit()
    return conn


def reopen_as_older_build(conn=None) -> None:
    """Make the current database look like one an earlier build wrote.

    Since 2.6.1 the schema is stamped into ``PRAGMA user_version`` so a
    database already carrying this build's schema can skip the write path
    entirely — that skip is what stopped read-only screens reporting
    `database is locked`.

    A database from an older build carries an older stamp, or none, so it
    still takes the full path and still gets its migrations. Tests that
    simulate an upgrade by rolling back a migration marker have to roll the
    stamp back too, or they are describing a state no real install can be in:
    current schema, missing migration.
    """
    close = conn is None
    if close:
        conn = db.get_connection()
    try:
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        if close:
            conn.close()

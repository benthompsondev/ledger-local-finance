"""Sanitized regression coverage for the two real-world CSV structures.

All names, dates, and values are invented. Only the column shapes and the
transaction patterns that exposed the financial-model bug are preserved.
"""
from datetime import date

from parsers.csv_import import parse_csv
from utils.analytics import compute_cashflow, find_recurring
from utils.database import (
    _migrate_etransfer_cashflow_semantics, get_category_totals,
    insert_transaction,
)
from utils.financial_semantics import promote_recurring_income
from utils.insights import category_pace_breakdown, spending_pace


def _write_asset_csv(tmp_path):
    path = tmp_path / "sanitized-chequing.CSV"
    path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-01-02,OTHER,NORTH WIND SERVICES,Deposit,2100.00\n"
        "2026-01-05,OTHER,HOME MORTGAGE,Withdrawal,-900.00\n"
        "2026-01-08,OTHER,TANGERINE CREDIT CARD PAYMENT,Payment,-300.00\n"
        "2026-01-10,OTHER,INTERNET TRANSFER TO SAVINGS,Move,-250.00\n"
        "2026-01-12,OTHER,WEALTHSIMPLE CONTRIBUTION,Invest,-100.00\n"
        "2026-01-14,OTHER,INTERAC E-TRANSFER TO: SAMPLE PERSON,Transfer,-50.00\n"
        "2026-01-16,OTHER,INTERAC E-TRANSFER FROM: SAMPLE CLIENT,Transfer,125.00\n"
        "2026-02-02,OTHER,NORTH WIND SERVICES,Deposit,2080.00\n"
        "2026-02-05,OTHER,HOME MORTGAGE,Withdrawal,-900.00\n"
        "2026-03-02,OTHER,NORTH WIND SERVICES,Deposit,2120.00\n"
        "2026-03-05,ATM,HOME MORTGAGE,Withdrawal,-900.00\n",
        encoding="utf-8",
    )
    return path


def _write_card_csv(tmp_path):
    path = tmp_path / "sanitized-card.CSV"
    path.write_text(
        "Transaction date,Transaction,Name,Memo,Amount\n"
        "1/3/2026,DEBIT,LOBLAWS TEST MARKET,Purchase,-80.00\n"
        "1/8/2026,CREDIT,PAYMENT THANK YOU,Payment,300.00\n"
        "1/15/2026,CREDIT,LOBLAWS TEST MARKET,Merchant credit,20.00\n"
        "2/3/2026,DEBIT,LOBLAWS TEST MARKET,Purchase,-82.00\n"
        "3/3/2026,DEBIT,LOBLAWS TEST MARKET,Purchase,-78.00\n",
        encoding="utf-8",
    )
    return path


def test_canonical_import_cashflow_and_recurring_invariants(ledger_db, tmp_path):
    asset = parse_csv(_write_asset_csv(tmp_path), account_type="chequing")
    card = parse_csv(_write_card_csv(tmp_path), account_type="credit_card")
    assert not asset["errors"] and not card["errors"]
    promote_recurring_income(asset["transactions"])

    rows = asset["transactions"] + card["transactions"]
    original_total = sum(tx["amount"] for tx in rows)
    for tx in rows:
        ok, reason = insert_transaction(tx, ledger_db)
        assert ok, reason
    ledger_db.commit()

    by_desc = {}
    for tx in rows:
        by_desc.setdefault(tx["raw_description"], []).append(tx)
    assert by_desc["NORTH WIND SERVICES"][0]["direction"] == "credit"
    assert by_desc["NORTH WIND SERVICES"][0]["amount"] == 2100.00
    assert all(t["transaction_type"] == "employment_income"
               for t in by_desc["NORTH WIND SERVICES"])
    assert by_desc["LOBLAWS TEST MARKET"][0]["direction"] == "debit"
    assert by_desc["LOBLAWS TEST MARKET"][0]["amount"] == -80.00
    assert by_desc["PAYMENT THANK YOU"][0]["amount"] == 300.00
    assert by_desc["TANGERINE CREDIT CARD PAYMENT"][0]["amount"] == -300.00
    assert by_desc["PAYMENT THANK YOU"][0]["transaction_type"] == "credit_card_payment"
    assert by_desc["TANGERINE CREDIT CARD PAYMENT"][0]["transaction_type"] == "credit_card_payment"
    assert by_desc["INTERNET TRANSFER TO SAVINGS"][0]["transaction_type"] == "internal_transfer"
    assert by_desc["WEALTHSIMPLE CONTRIBUTION"][0]["transaction_type"] == "investment_transfer"
    assert by_desc["INTERAC E-TRANSFER TO: SAMPLE PERSON"][0]["transaction_type"] == "spending"
    assert by_desc["INTERAC E-TRANSFER TO: SAMPLE PERSON"][0]["category"] == "E-transfer / Personal payment"
    assert by_desc["INTERAC E-TRANSFER FROM: SAMPLE CLIENT"][0]["transaction_type"] == "other_income"
    assert by_desc["INTERAC E-TRANSFER FROM: SAMPLE CLIENT"][0]["category"] == "Income"
    assert by_desc["HOME MORTGAGE"][0]["transaction_type"] == "mortgage_payment"
    assert by_desc["LOBLAWS TEST MARKET"][1]["transaction_type"] == "refund"

    totals = compute_cashflow("2026-01-01", "2026-03-31", conn=ledger_db)
    assert totals["income"] == 6445.00
    assert totals["spending_gross"] == 2990.00
    assert totals["refund_income"] == 20.00
    assert totals["refund_offset"] == 0.00
    assert totals["spending"] == 2990.00

    categories = get_category_totals("2026-01-01", "2026-03-31", conn=ledger_db)
    assert "Income" not in {r["category"] for r in categories}
    assert "Payroll Income" not in {r["category"] for r in categories}
    assert sum(r["total"] for r in categories) == totals["spending"]
    pace = category_pace_breakdown(
        conn=ledger_db, today=date(2026, 1, 31), limit=20
    )
    assert pace["total"] == 1030.00
    daily = spending_pace(conn=ledger_db, today=date(2026, 1, 31))
    assert daily["current_total"] == 1030.00
    assert not {r["category"] for r in pace["items"]}.intersection({
        "Income", "Payroll Income", "Transfer", "Transfer In", "Transfer Out",
        "Investments", "Internal Transfer", "Credit Card Payment",
    })
    recurring = find_recurring(conn=ledger_db)
    recurring_categories = {r["category"] for r in recurring}
    assert "Housing / Mortgage" in recurring_categories
    assert not recurring_categories.intersection({
        "Income", "Payroll Income", "Investments", "Internal Transfer",
        "Credit Card Payment", "Refund / Credit",
    })
    assert sum(float(r["amount"]) for r in ledger_db.execute(
        "SELECT amount FROM transactions"
    ).fetchall()) == original_total
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
    ).fetchone()[0] == 0


def test_refund_income_never_rewrites_spending_categories(ledger_db):
    """Refund-like credits are income; purchase categories stay exact."""
    rows = [
        {
            "transaction_date": "2026-04-01",
            "raw_description": "INVENTED GROCER",
            "amount": -100.0, "direction": "debit", "account_type": "chequing",
            "category": "Groceries", "transaction_type": "spending",
        },
        {
            "transaction_date": "2026-04-02",
            "raw_description": "INVENTED SHOP",
            "amount": -50.0, "direction": "debit", "account_type": "credit_card",
            "category": "Shopping", "transaction_type": "spending",
        },
        {
            "transaction_date": "2026-04-03",
            "raw_description": "INVENTED BENEFITS",
            "amount": 30.0, "direction": "credit", "account_type": "chequing",
            "category": "Reimbursement / Insurance Reimbursement",
            "subcategory": "Invented provider", "transaction_type": "reimbursement",
        },
        {
            "transaction_date": "2026-04-04",
            "raw_description": "INVENTED REWARDS",
            "amount": 20.0, "direction": "credit", "account_type": "credit_card",
            "category": "Rewards / Cashback", "subcategory": "Invented program",
            "transaction_type": "rewards_credit",
        },
    ]
    for row in rows:
        inserted, reason = insert_transaction(row, ledger_db)
        assert inserted, reason
    ledger_db.commit()

    cashflow = compute_cashflow("2026-04-01", "2026-04-30", conn=ledger_db)
    categories = get_category_totals(
        "2026-04-01", "2026-04-30", conn=ledger_db,
    )

    assert cashflow["income"] == 50.0
    assert cashflow["refund_income"] == 50.0
    assert cashflow["spending"] == 150.0
    assert sum(row["total"] for row in categories) == cashflow["spending"]
    assert {row["category"] for row in categories} == {"Groceries", "Shopping"}
    by_category = {row["category"]: row["total"] for row in categories}
    assert by_category == {"Groceries": 100.0, "Shopping": 50.0}


def test_explicit_internal_category_overrides_etransfer_cashflow(tmp_path):
    tx = parse_csv(_write_asset_csv(tmp_path), account_type="chequing")[
        "transactions"
    ][5]
    from utils.financial_semantics import classify_transaction_type

    assert tx["raw_description"] == "INTERAC E-TRANSFER TO: SAMPLE PERSON"
    assert classify_transaction_type(
        tx["raw_description"], tx["account_type"], tx["direction"],
        "Internal Transfer",
    ) == "internal_transfer"


def test_legacy_personal_etransfers_are_migrated_to_cashflow(ledger_db):
    ledger_db.executemany(
        """
        INSERT INTO transactions
            (transaction_date, raw_description, account_type, amount,
             direction, category, subcategory, transaction_type,
             is_transfer, dedup_hash)
        VALUES (?, ?, 'chequing', ?, ?, ?, 'Personal transfer',
                'personal_transfer', 1, ?)
        """,
        [
            ("2026-05-01", "INTERAC E-TRANSFER FROM: SAMPLE SENDER", 85.0,
             "credit", "Transfer In", "legacy-in"),
            ("2026-05-02", "INTERAC E-TRANSFER TO: SAMPLE RECIPIENT", -40.0,
             "debit", "Transfer Out", "legacy-out"),
        ],
    )
    _migrate_etransfer_cashflow_semantics(ledger_db)
    rows = [dict(row) for row in ledger_db.execute(
        "SELECT * FROM transactions ORDER BY transaction_date"
    ).fetchall()]
    assert rows[0]["transaction_type"] == "other_income"
    assert rows[0]["category"] == "Income"
    assert rows[0]["amount"] == 85.0
    assert rows[1]["transaction_type"] == "spending"
    assert rows[1]["category"] == "Uncategorized"
    assert rows[1]["amount"] == -40.0


def test_unknown_asset_credit_is_not_fabricated_income(tmp_path):
    path = tmp_path / "one.CSV"
    path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-04-01,OTHER,UNEXPLAINED CREDIT,Deposit,750.00\n",
        encoding="utf-8",
    )
    tx = parse_csv(path, account_type="chequing")["transactions"][0]
    assert tx["direction"] == "credit"
    assert tx["transaction_type"] == "ambiguous_inflow"
    assert tx["category"] == "Uncategorized"
    assert tx["is_flagged"] == 1

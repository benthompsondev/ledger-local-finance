"""Accounts model: CRUD, legacy migration/backfill, account-aware
duplicate detection, and deterministic balances."""
import pytest

import utils.database as db
from utils.balances import account_balance, all_account_balances
from tests.conftest import add_tx, seed_month


# ── CRUD ──────────────────────────────────────────────────────────────
def test_create_list_update_archive(ledger_db):
    aid = db.create_account({"name": "Everyday Chequing",
                             "type": "chequing",
                             "institution": "Test Bank",
                             "opening_balance": 500.0},
                            conn=ledger_db)
    assert aid > 0
    accts = db.list_accounts(conn=ledger_db)
    assert any(a["name"] == "Everyday Chequing" for a in accts)
    with pytest.raises(ValueError):
        db.create_account({"name": "everyday chequing",
                           "type": "chequing"}, conn=ledger_db)
    with pytest.raises(ValueError):
        db.create_account({"name": "Bad", "type": "hedge_fund"},
                          conn=ledger_db)
    assert db.update_account(aid, {"institution": "Other Bank"},
                             conn=ledger_db)
    assert db.get_account(aid, conn=ledger_db)["institution"] == "Other Bank"
    assert db.archive_account(aid, conn=ledger_db)
    assert db.list_accounts(conn=ledger_db) == []
    assert len(db.list_accounts(conn=ledger_db,
                                include_archived=True)) == 1
    assert db.archive_account(aid, archived=False, conn=ledger_db)
    assert len(db.list_accounts(conn=ledger_db)) == 1


# ── Migration / backfill ──────────────────────────────────────────────
# The fixtures insert rows AFTER init_db, leaving account_ref NULL —
# which is exactly what a legacy database looks like. Running the
# migration explicitly simulates upgrading that database.
def test_backfill_assigns_migrated_accounts(steady_db):
    db._migrate_accounts_schema(steady_db)
    unassigned = steady_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_ref IS NULL"
    ).fetchone()[0]
    assert unassigned == 0
    names = {a["name"]: a for a in
             db.list_accounts(conn=steady_db, include_archived=True)}
    assert "Chequing (migrated)" in names
    assert "Credit card (migrated)" in names
    assert names["Credit card (migrated)"]["type"] == "credit_card"
    assert names["Chequing (migrated)"]["is_migrated"] == 1


def test_migration_is_idempotent_and_lossless(steady_db):
    db._migrate_accounts_schema(steady_db)
    before = steady_db.execute(
        "SELECT COUNT(*), ROUND(SUM(ABS(amount)),2) FROM transactions"
    ).fetchone()
    n_accounts = len(db.list_accounts(conn=steady_db,
                                      include_archived=True))
    db._migrate_accounts_schema(steady_db)
    db._migrate_accounts_schema(steady_db)
    after = steady_db.execute(
        "SELECT COUNT(*), ROUND(SUM(ABS(amount)),2) FROM transactions"
    ).fetchone()
    assert tuple(before) == tuple(after)
    assert len(db.list_accounts(conn=steady_db,
                                include_archived=True)) == n_accounts


def test_migration_failure_rolls_back(ledger_db, monkeypatch):
    seed_month(ledger_db, "2026-06")
    ledger_db.execute("UPDATE transactions SET account_ref = NULL")
    ledger_db.commit()
    original = db._ensure_columns

    def _boom(conn, table, columns):
        original(conn, table, columns)
        if table == "import_profiles":
            raise RuntimeError("injected failure")

    monkeypatch.setattr(db, "_ensure_columns", _boom)
    with pytest.raises(RuntimeError):
        db._migrate_accounts_schema(ledger_db)
    monkeypatch.setattr(db, "_ensure_columns", original)
    # Rolled back: still unassigned, no partial accounts created…
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_ref IS NOT NULL"
    ).fetchone()[0] == 0
    # …and no data lost.
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions").fetchone()[0] > 0
    # A clean rerun then succeeds.
    db._migrate_accounts_schema(ledger_db)
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_ref IS NULL"
    ).fetchone()[0] == 0


def test_migration_preserves_categories_and_provenance(steady_db):
    db._migrate_accounts_schema(steady_db)
    row = steady_db.execute(
        "SELECT category, import_batch_id, account_ref FROM transactions "
        "WHERE raw_description='LANDLORD RENT' LIMIT 1"
    ).fetchone()
    assert row["category"] == "Housing / Mortgage"
    assert row["account_ref"] is not None


# ── Account-aware duplicate detection ─────────────────────────────────
def _tx(desc, account_ref=None, account_type="mastercard", amount=54.0):
    tx = {"account_type": account_type, "transaction_date": "2026-07-06",
          "raw_description": desc, "amount": amount,
          "direction": "debit", "category": "Shopping"}
    if account_ref:
        tx["account_ref"] = account_ref
    return tx


def test_same_charge_two_cards_not_duplicates(ledger_db):
    card_a = db.create_account({"name": "Card A", "type": "credit_card"},
                               conn=ledger_db)
    card_b = db.create_account({"name": "Card B", "type": "credit_card"},
                               conn=ledger_db)
    ok1, _ = db.insert_transaction(_tx("COFFEE SHOP", card_a), ledger_db)
    ok2, _ = db.insert_transaction(_tx("COFFEE SHOP", card_b), ledger_db)
    assert ok1 and ok2
    # Same charge again on card A → duplicate.
    ok3, why = db.insert_transaction(_tx("COFFEE SHOP", card_a), ledger_db)
    assert not ok3 and why == "duplicate"


def test_legacy_rows_still_block_reimport_into_their_account(ledger_db):
    # A pre-accounts row (no account_ref, legacy hash)…
    ok, _ = db.insert_transaction(_tx("OLD CHARGE"), ledger_db)
    assert ok
    db._migrate_accounts_schema(ledger_db)
    migrated = next(a for a in db.list_accounts(conn=ledger_db,
                                                include_archived=True)
                    if a["is_migrated"])
    # …re-imported into its migrated account → still a duplicate…
    ok2, why = db.insert_transaction(
        _tx("OLD CHARGE", migrated["id"]), ledger_db)
    assert not ok2 and why == "duplicate"
    # …but the same-looking charge on a brand-new account inserts.
    other = db.create_account({"name": "Second Card",
                               "type": "credit_card"}, conn=ledger_db)
    ok3, _ = db.insert_transaction(_tx("OLD CHARGE", other), ledger_db)
    assert ok3


def test_manual_matches_imported_duplicate_same_account(ledger_db):
    acct = db.create_account({"name": "Chequing", "type": "chequing"},
                             conn=ledger_db)
    ok1, _ = db.insert_transaction(
        _tx("HYDRO BILL", acct, account_type="chequing"), ledger_db)
    ok2, why = db.insert_transaction(
        _tx("HYDRO BILL", acct, account_type="chequing"), ledger_db)
    assert ok1 and not ok2 and why == "duplicate"


# ── Balances ──────────────────────────────────────────────────────────
def _acct(ledger_db, name, type_, **kw):
    aid = db.create_account({"name": name, "type": type_, **kw},
                            conn=ledger_db)
    return db.get_account(aid, conn=ledger_db)


def _add(conn, acct, desc, amount, direction, day="2026-07-05",
         currency="CAD"):
    ok, why = db.insert_transaction({
        "account_type": acct["type"], "account_ref": acct["id"],
        "transaction_date": day, "raw_description": desc,
        "amount": amount, "direction": direction, "currency": currency,
        "category": "Misc",
    }, conn)
    assert ok, why


def test_chequing_balance_income_and_spending(ledger_db):
    a = _acct(ledger_db, "Chq", "chequing", opening_balance=1000.0)
    _add(ledger_db, a, "PAYROLL", 2500.0, "credit")
    _add(ledger_db, a, "RENT", 1800.0, "debit")
    b = account_balance(a, conn=ledger_db)
    assert b["balance"] == pytest.approx(1000 + 2500 - 1800)
    assert b["networth_contribution"] == pytest.approx(1700.0)


def test_credit_card_purchases_and_payments(ledger_db):
    a = _acct(ledger_db, "Visa", "credit_card", opening_balance=250.0)
    _add(ledger_db, a, "STORE PURCHASE", 100.0, "debit")
    _add(ledger_db, a, "PAYMENT THANK YOU", 300.0, "credit")
    # Negative-credit refund (historical MC convention) reduces owed.
    _add(ledger_db, a, "REFUND", -20.0, "credit")
    b = account_balance(a, conn=ledger_db)
    assert b["balance"] == pytest.approx(250 + 100 - 300 - 20)
    assert b["networth_contribution"] == pytest.approx(-30.0)


def test_loan_and_cash_and_empty_accounts(ledger_db):
    loan = _acct(ledger_db, "Car loan", "loan", opening_balance=8000.0)
    _add(ledger_db, loan, "LOAN PAYMENT", 400.0, "credit")
    cash = _acct(ledger_db, "Wallet", "cash")
    _add(ledger_db, cash, "MARKET", 25.0, "debit")
    empty = _acct(ledger_db, "New savings", "savings")
    assert account_balance(loan, conn=ledger_db)["balance"] == pytest.approx(7600.0)
    assert account_balance(cash, conn=ledger_db)["balance"] == pytest.approx(-25.0)
    assert account_balance(empty, conn=ledger_db)["balance"] == pytest.approx(0.0)


def test_opening_balance_date_excludes_prior_rows(ledger_db):
    a = _acct(ledger_db, "Chq2", "chequing", opening_balance=5000.0,
              opening_balance_date="2026-07-01")
    _add(ledger_db, a, "OLD SPEND", 999.0, "debit", day="2026-06-15")
    _add(ledger_db, a, "NEW SPEND", 100.0, "debit", day="2026-07-02")
    b = account_balance(a, conn=ledger_db)
    assert b["balance"] == pytest.approx(5000 - 100)


def test_transfer_between_accounts_nets_to_zero(ledger_db):
    chq = _acct(ledger_db, "Chq3", "chequing", opening_balance=1000.0)
    sav = _acct(ledger_db, "Sav3", "savings")
    _add(ledger_db, chq, "TO SAVINGS", 400.0, "debit")
    _add(ledger_db, sav, "FROM CHEQUING", 400.0, "credit")
    totals = all_account_balances(conn=ledger_db)
    # Money moved between assets: total unchanged (1000).
    assert totals["networth_contribution"] == pytest.approx(1000.0)


def test_foreign_currency_never_merged(ledger_db):
    cad = _acct(ledger_db, "CAD Chq", "chequing", opening_balance=100.0)
    usd = _acct(ledger_db, "USD Acct", "chequing", currency="USD",
                opening_balance=50.0)
    _add(ledger_db, usd, "US DEPOSIT", 10.0, "credit", currency="USD")
    # A USD transaction inside the CAD account is excluded + counted.
    _add(ledger_db, cad, "US PURCHASE", 30.0, "debit", currency="USD")
    b_cad = account_balance(cad, conn=ledger_db)
    assert b_cad["balance"] == pytest.approx(100.0)
    assert b_cad["foreign_currency_tx"] == 1
    totals = all_account_balances(conn=ledger_db)
    assert totals["networth_contribution"] == pytest.approx(100.0)
    assert len(totals["foreign_accounts"]) == 1
    assert totals["foreign_accounts"][0]["balance"] == pytest.approx(60.0)


def test_archived_accounts_excluded_from_totals(ledger_db):
    a = _acct(ledger_db, "Old chq", "chequing", opening_balance=700.0)
    db.archive_account(a["id"], conn=ledger_db)
    totals = all_account_balances(conn=ledger_db)
    assert totals["networth_contribution"] == pytest.approx(0.0)
    with_arch = all_account_balances(conn=ledger_db,
                                     include_archived=True)
    assert with_arch["networth_contribution"] == pytest.approx(700.0)

"""The latest balance is the newest dated one, and currencies do not add up.

All figures are invented.

Two defects, both in the path that prefills a monthly net worth reading.

`get_account_balances(latest_only=True)` selected `MAX(id)` per account.
Rows are keyed by the date they are *about*, not the order they were typed,
so backfilling January after recording July made January the "latest"
balance and the prefill quietly went backwards by six months.

`suggested_entry` then summed `abs(balance)` across every account regardless
of currency, so a USD 500 account and a CAD 1,000 account produced cash of
1,500. Northstar has no exchange rate and has never invented one anywhere
else, so adding them is the one thing it must not do.
"""
from __future__ import annotations

import pytest

import utils.database as db
from utils.database import get_account_balances
from utils.net_worth import suggested_entry


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _account(name: str, kind: str) -> int:
    return int(db.create_account({"name": name, "type": kind}))


def _balance(account_ref: int, balance: float, as_of_date: str,
             currency: str = "CAD") -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO account_balances"
            "(account_ref, account_name, account_kind, balance, currency, "
            " as_of_date) "
            "SELECT id, name, type, ?, ?, ? FROM accounts WHERE id=?",
            (balance, currency, as_of_date, account_ref),
        )
        conn.commit()
    finally:
        conn.close()


# ── dated, not insertion-ordered ─────────────────────────────────────────

def test_the_latest_balance_is_the_newest_dated_one(ledger) -> None:
    """The reproduction: July recorded first, January backfilled after."""
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 9000.0, "2026-07-31")
    _balance(chequing, 1000.0, "2026-01-31")

    latest = get_account_balances(latest_only=True)

    assert len(latest) == 1
    assert latest[0]["balance"] == 9000.0, (
        "a backdated snapshot replaced a newer one"
    )
    assert latest[0]["as_of_date"] == "2026-07-31"


def test_the_prefill_uses_the_newest_dated_balance(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 9000.0, "2026-07-31")
    _balance(chequing, 1000.0, "2026-01-31")

    assert suggested_entry("2026-08")["cash"] == 9000.0


def test_insertion_order_does_not_matter(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-01-31")
    _balance(chequing, 9000.0, "2026-07-31")

    assert get_account_balances(latest_only=True)[0]["balance"] == 9000.0


def test_the_newest_row_wins_a_tie_on_the_same_date(ledger) -> None:
    """A correction typed on the same day is the one that counts."""
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-07-31")
    _balance(chequing, 1250.0, "2026-07-31")

    assert get_account_balances(latest_only=True)[0]["balance"] == 1250.0


def test_each_account_keeps_its_own_latest(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    savings = _account("Invented Savings", "savings")
    _balance(chequing, 9000.0, "2026-07-31")
    _balance(savings, 12000.0, "2026-06-30")
    _balance(chequing, 1000.0, "2026-01-31")

    by_name = {b["account_name"]: b for b in
               get_account_balances(latest_only=True)}

    assert by_name["Invented Chequing"]["balance"] == 9000.0
    assert by_name["Invented Savings"]["balance"] == 12000.0


def test_legacy_unlinked_snapshots_still_group_by_name_and_kind(
    ledger,
) -> None:
    """Rows from before accounts existed carry no account_ref."""
    conn = db.get_connection()
    try:
        for balance, as_of in ((500.0, "2026-07-31"), (100.0, "2026-01-31")):
            conn.execute(
                "INSERT INTO account_balances"
                "(account_name, account_kind, balance, currency, as_of_date) "
                "VALUES(?,?,?,?,?)",
                ("Invented Legacy", "chequing", balance, "CAD", as_of),
            )
        conn.commit()
    finally:
        conn.close()

    latest = get_account_balances(latest_only=True)

    assert len(latest) == 1
    assert latest[0]["balance"] == 500.0


# ── currencies are never added together ──────────────────────────────────

def test_a_foreign_currency_account_is_not_added_to_cash(ledger) -> None:
    """The reproduction: CAD 1,000 plus USD 500 became 1,500."""
    chequing = _account("Invented Chequing", "chequing")
    usd = _account("Invented US Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-07-31", currency="CAD")
    _balance(usd, 500.0, "2026-07-31", currency="USD")

    prefill = suggested_entry("2026-08")

    assert prefill["cash"] == 1000.0, "a USD balance was added to CAD cash"


def test_an_excluded_account_is_surfaced_not_hidden(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    usd = _account("Invented US Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-07-31", currency="CAD")
    _balance(usd, 500.0, "2026-07-31", currency="USD")

    prefill = suggested_entry("2026-08")

    assert prefill["excluded"], "the USD account vanished without a word"
    excluded = prefill["excluded"][0]
    assert excluded["account"] == "Invented US Chequing"
    assert excluded["currency"] == "USD"
    assert excluded["amount"] == 500.0
    assert prefill["excluded_currencies"] == ["USD"]


def test_every_included_source_reports_its_currency(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-07-31", currency="CAD")

    sources = suggested_entry("2026-08")["sources"]

    assert sources[0]["currency"] == "CAD"


def test_a_blank_currency_is_treated_as_the_local_one(ledger) -> None:
    """Older rows predate the currency column being filled in."""
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO account_balances"
            "(account_name, account_kind, balance, currency, as_of_date) "
            "VALUES(?,?,?,?,?)",
            ("Invented Old", "chequing", 700.0, None, "2026-07-31"),
        )
        conn.commit()
    finally:
        conn.close()

    prefill = suggested_entry("2026-08")

    assert prefill["cash"] == 700.0
    assert prefill["excluded"] == []


def test_nothing_is_excluded_when_every_account_is_local(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    _balance(chequing, 1000.0, "2026-07-31", currency="CAD")

    prefill = suggested_entry("2026-08")

    assert prefill["excluded"] == []
    assert prefill["excluded_currencies"] == []


# ── every account kind lands in the right figure ─────────────────────────

@pytest.mark.parametrize("kind,field", [
    ("chequing", "cash"),
    ("savings", "cash"),
    ("cash", "cash"),
    ("investment", "investments"),
    ("other_asset", "other_assets"),
    ("credit_card", "liabilities"),
    ("loan", "liabilities"),
    ("mortgage", "liabilities"),
    ("other_liability", "liabilities"),
])
def test_each_account_kind_lands_in_the_right_figure(
    ledger, kind, field,
) -> None:
    account = _account(f"Invented {kind}", kind)
    _balance(account, 1234.0, "2026-07-31")

    prefill = suggested_entry("2026-08")

    assert prefill[field] == 1234.0, f"{kind} did not land in {field}"
    assert sum(
        prefill[other] for other in
        ("cash", "investments", "other_assets", "liabilities")
        if other != field
    ) == 0.0


def test_a_debt_is_prefilled_as_a_positive_magnitude(ledger) -> None:
    """Debts are entered positive and subtracted, however they are stored."""
    card = _account("Invented Card", "credit_card")
    _balance(card, -1430.0, "2026-07-31")

    prefill = suggested_entry("2026-08")

    assert prefill["liabilities"] == 1430.0
    assert prefill["net"] == -1430.0


def test_an_unknown_account_kind_is_left_out_rather_than_guessed(
    ledger,
) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO account_balances"
            "(account_name, account_kind, balance, currency, as_of_date) "
            "VALUES(?,?,?,?,?)",
            ("Invented Mystery", "timeshare", 5000.0, "CAD", "2026-07-31"),
        )
        conn.commit()
    finally:
        conn.close()

    prefill = suggested_entry("2026-08")

    assert prefill["net"] == 0.0
    assert prefill["cash"] == 0.0


def test_investment_positions_are_not_counted_twice(ledger) -> None:
    """The prefill reads account balances only, never holdings as well."""
    brokerage = _account("Invented Brokerage", "investment")
    _balance(brokerage, 48250.0, "2026-07-31")

    prefill = suggested_entry("2026-08")

    assert prefill["investments"] == 48250.0

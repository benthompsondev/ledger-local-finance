"""Adversarial CSV cases that must never become confident financial data."""

import pytest

from parsers.csv_import import parse_csv
from utils.csv_mapping import parse_csv_mapped


def _write(tmp_path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _mapping():
    return {
        "date_col": "When",
        "desc_col": "What",
        "amount_mode": "signed",
        "amount_col": "Value",
        "positive_is": "credit",
    }


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path / "engine-data"))
    return tmp_path


def test_saved_mapping_uses_one_date_convention_for_the_file(tmp_path):
    path = _write(
        tmp_path, "mapped-day-first.csv",
        "When,What,Value\n"
        "02/04/2026,INVENTED CAFE,-10.00\n"
        "15/04/2026,INVENTED STORE,-20.00\n",
    )

    result = parse_csv_mapped(path, _mapping())

    assert result["blocked"] is False
    assert [tx["transaction_date"] for tx in result["transactions"]] == [
        "2026-04-02", "2026-04-15",
    ]


def test_saved_mapping_preserves_currency_safety_column(tmp_path):
    path = _write(
        tmp_path, "mapped-mixed-currency.csv",
        "When,What,Value,Currency\n"
        "2026-04-02,INVENTED A,-10.00,CAD\n"
        "2026-04-03,INVENTED B,-20.00,USD\n",
    )

    result = parse_csv_mapped(path, _mapping())

    assert result["blocked"] is True
    assert "mixes cad and usd" in " ".join(
        result["receipt"]["problems"]
    ).lower()


def test_saved_mapping_preserves_unsigned_direction_column(tmp_path):
    path = _write(
        tmp_path, "mapped-directions.csv",
        "When,What,Value,Direction\n"
        "2026-04-02,INVENTED PURCHASE,10.00,Debit\n"
        "2026-04-03,INVENTED DEPOSIT,100.00,Credit\n",
    )

    result = parse_csv_mapped(path, _mapping())

    assert result["blocked"] is False
    assert [tx["amount"] for tx in result["transactions"]] == [-10.0, 100.0]


@pytest.mark.parametrize(
    "name,text,mapping,problem",
    [
        (
            "mixed-decimals.csv",
            "When;What;Value\n"
            "2026-04-02;INVENTED A;-10,00\n"
            "2026-04-03;INVENTED B;-9.50\n",
            _mapping(),
            "mixes comma-decimal",
        ),
        (
            "blank-signed.csv",
            "When,What,Value\n"
            "2026-04-02,INVENTED A,-10.00\n"
            "2026-04-03,INVENTED B,\n",
            _mapping(),
            "blank signed amount",
        ),
        (
            "infinite.csv",
            "When,What,Value\n"
            "2026-04-02,INVENTED A,-10.00\n"
            "2026-04-03,INVENTED B,1e309\n",
            _mapping(),
            "amount spendshape cannot read",
        ),
        (
            "broken-date.csv",
            "When,What,Value\n"
            "2026-04-02,INVENTED A,-10.00\n"
            "BROKEN,INVENTED B,-20.00\n",
            _mapping(),
            "date spendshape cannot read",
        ),
    ],
)
def test_saved_mapping_cannot_bypass_fail_closed_checks(
    tmp_path, name, text, mapping, problem,
):
    result = parse_csv_mapped(_write(tmp_path, name, text), mapping)

    assert result["blocked"] is True
    assert problem in " ".join(result["receipt"]["problems"]).lower()


@pytest.mark.parametrize(
    "name,text,problem",
    [
        (
            "auto-mixed-decimals.csv",
            "Date;Description;Amount\n"
            "2026-04-02;INVENTED A;-10,00\n"
            "2026-04-03;INVENTED B;-9.50\n",
            "mixes comma-decimal",
        ),
        (
            "auto-infinite.csv",
            "Date,Description,Amount\n"
            "2026-04-02,INVENTED A,-10.00\n"
            "2026-04-03,INVENTED B,1e309\n",
            "amount spendshape cannot read",
        ),
        (
            "duplicate-header.csv",
            "Date,Description,Amount,Amount\n"
            "2026-04-02,INVENTED A,-10.00,-999.00\n",
            "repeats the column heading",
        ),
        (
            "competing-amounts.csv",
            "Date,Description,Amount,Transaction Amount\n"
            "2026-04-02,INVENTED A,-10.00,-999.00\n",
            "more than one column was detected as amount",
        ),
        (
            "both-sides.csv",
            "Date,Description,Debit,Credit\n"
            "2026-04-02,INVENTED A,10.00,20.00\n",
            "both a debit and a credit",
        ),
        (
            "unknown-direction.csv",
            "Date,Description,Amount,Direction\n"
            "2026-04-02,INVENTED A,10.00,Debit\n"
            "2026-04-03,INVENTED B,20.00,Mystery\n",
            "unknown value 'mystery'",
        ),
        (
            "blank-direction.csv",
            "Date,Description,Amount,Direction\n"
            "2026-04-02,INVENTED A,10.00,Debit\n"
            "2026-04-03,INVENTED B,20.00,\n"
            "2026-04-04,INVENTED C,1000.00,Credit\n",
            "unknown value 'blank'",
        ),
        (
            "missing-date-total-merchant.csv",
            "Date,Description,Amount\n"
            "2026-04-02,INVENTED A,-10.00\n"
            ",TOTAL WINE AND MORE,-20.00\n"
            "2026-04-04,INVENTED C,-30.00\n",
            "date spendshape cannot read",
        ),
    ],
)
def test_automatic_parser_refuses_contradictory_columns(
    tmp_path, name, text, problem,
):
    result = parse_csv(_write(tmp_path, name, text))

    assert result["blocked"] is True
    assert problem in " ".join(result["receipt"]["problems"]).lower()


def test_empty_signed_column_falls_through_to_populated_split_columns(tmp_path):
    path = _write(
        tmp_path, "empty-signed-valid-split.csv",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED PURCHASE,,10.00,\n"
        "2026-04-03,INVENTED DEPOSIT,,,1000.00\n",
    )

    result = parse_csv(path)

    assert result["blocked"] is False
    assert [tx["amount"] for tx in result["transactions"]] == [-10.0, 1000.0]


def test_matching_redundant_amount_layout_is_accepted(tmp_path):
    path = _write(
        tmp_path, "matching-redundant-layout.csv",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED PURCHASE,-10.00,10.00,\n"
        "2026-04-03,INVENTED DEPOSIT,1000.00,,1000.00\n",
    )

    result = parse_csv(path)

    assert result["blocked"] is False
    assert [tx["amount"] for tx in result["transactions"]] == [-10.0, 1000.0]


def test_conflicting_redundant_amount_layout_is_blocked(tmp_path):
    path = _write(
        tmp_path, "conflicting-redundant-layout.csv",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED PURCHASE,10.00,10.00,\n",
    )

    result = parse_csv(path)

    assert result["blocked"] is True
    assert "does not match" in " ".join(
        result["receipt"]["problems"]
    ).lower()


def test_preview_counts_repeated_genuine_rows_like_confirmation(ledger_db):
    from desktop.engine.ledger_engine import _preview_duplicate_counts
    from utils.database import create_account, get_account
    from utils.import_service import persist_import_batch

    account_id = create_account(
        {"name": "Invented Card", "type": "credit_card"}, conn=ledger_db,
    )
    account = get_account(account_id, conn=ledger_db)
    rows = [
        {
            "account_type": "credit_card",
            "transaction_date": "2026-04-02",
            "raw_description": "INVENTED TRANSIT FARE",
            "amount": -4.25,
            "direction": "debit",
            "category": "Gas / Transport",
        },
        {
            "account_type": "credit_card",
            "transaction_date": "2026-04-02",
            "raw_description": "INVENTED TRANSIT FARE",
            "amount": -4.25,
            "direction": "debit",
            "category": "Gas / Transport",
        },
    ]

    ledger_db.execute("SAVEPOINT preview_counts")
    preview = _preview_duplicate_counts(
        rows, account, "csv", ledger_db,
        filename="invented.csv", file_hash="invented-repeat",
        import_mode=None, exact_reimport=False,
    )
    assert preview[:2] == (2, 0)
    ledger_db.execute("ROLLBACK TO SAVEPOINT preview_counts")
    ledger_db.execute("RELEASE SAVEPOINT preview_counts")

    from utils.account_assignment import apply_account_to_transactions
    persisted_rows = [dict(row) for row in rows]
    apply_account_to_transactions(persisted_rows, account)
    persist_import_batch(
        {"filename": "invented.csv", "file_hash": "invented-repeat"},
        persisted_rows, conn=ledger_db,
    )

    ledger_db.execute("SAVEPOINT preview_counts")
    preview = _preview_duplicate_counts(
        rows, account, "csv", ledger_db,
        filename="invented.csv", file_hash="invented-repeat",
        import_mode=None, exact_reimport=True,
    )
    assert preview[:2] == (0, 2)
    ledger_db.execute("ROLLBACK TO SAVEPOINT preview_counts")
    ledger_db.execute("RELEASE SAVEPOINT preview_counts")


def test_saved_profile_is_blocked_in_preview_and_confirm(engine_env, tmp_path):
    from tests.test_native_desktop_engine import _request
    from utils import database as db
    from utils.csv_mapping import save_import_profile

    path = _write(
        tmp_path, "saved-profile-mixed.csv",
        "When;What;Value\n"
        "2026-04-02;INVENTED A;-10,00\n"
        "2026-04-03;INVENTED B;-9.50\n",
    )
    code, payload = _request({
        "action": "create_account",
        "params": {"name": "Invented Chequing", "type": "chequing"},
    })
    assert code == 0, payload
    account_id = payload["data"]["created_id"]

    conn = db.get_connection()
    save_import_profile(
        "Invented saved profile", ["When", "What", "Value"], _mapping(),
        account_type="chequing", conn=conn,
    )
    conn.commit()
    conn.close()

    code, payload = _request({
        "action": "preview_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code == 0, payload
    assert payload["data"]["files"][0]["blocked"] is True

    code, payload = _request({
        "action": "confirm_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code != 0
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    conn.close()


def test_saved_profile_keeps_direction_through_confirmation(engine_env, tmp_path):
    from tests.test_native_desktop_engine import _request
    from utils import database as db
    from utils.csv_mapping import save_import_profile

    path = _write(
        tmp_path, "saved-profile-directions.csv",
        "When,What,Value,Direction\n"
        "2026-04-02,INVENTED PURCHASE,10.00,Debit\n"
        "2026-04-03,INVENTED DEPOSIT,100.00,Credit\n",
    )
    code, payload = _request({
        "action": "create_account",
        "params": {"name": "Invented Chequing", "type": "chequing"},
    })
    assert code == 0, payload
    account_id = payload["data"]["created_id"]

    conn = db.get_connection()
    save_import_profile(
        "Invented direction profile", ["When", "What", "Value", "Direction"],
        _mapping(), account_type="chequing", conn=conn,
    )
    conn.commit()
    conn.close()

    code, payload = _request({
        "action": "confirm_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code == 0, payload
    conn = db.get_connection()
    amounts = [
        row[0] for row in conn.execute(
            "SELECT amount FROM transactions ORDER BY transaction_date"
        ).fetchall()
    ]
    conn.close()
    assert amounts == [-10.0, 100.0]


def test_saved_profile_blocks_mixed_currencies_end_to_end(engine_env, tmp_path):
    from tests.test_native_desktop_engine import _request
    from utils import database as db
    from utils.csv_mapping import save_import_profile

    path = _write(
        tmp_path, "saved-profile-currencies.csv",
        "When,What,Value,Currency\n"
        "2026-04-02,INVENTED A,-10.00,CAD\n"
        "2026-04-03,INVENTED B,-20.00,USD\n",
    )
    code, payload = _request({
        "action": "create_account",
        "params": {"name": "Invented Chequing", "type": "chequing"},
    })
    assert code == 0, payload
    account_id = payload["data"]["created_id"]

    conn = db.get_connection()
    save_import_profile(
        "Invented currency profile", ["When", "What", "Value", "Currency"],
        _mapping(), account_type="chequing", conn=conn,
    )
    conn.commit()
    conn.close()

    code, payload = _request({
        "action": "preview_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code == 0, payload
    assert payload["data"]["files"][0]["blocked"] is True

    code, payload = _request({
        "action": "confirm_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code != 0
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    conn.close()

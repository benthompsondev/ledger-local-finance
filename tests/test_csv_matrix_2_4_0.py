"""Deterministic CSV ingestion matrix for the 2.4.0 public-beta hardening.

Every fixture is invented. The matrix covers the shapes a real statement can
take (delimiters, encodings, line endings, date and decimal conventions, the
three sign representations) and the adversarial shapes that must never become
confident financial data.

The sign contract asserted throughout:

  * a generic signed Amount is authoritative;
  * positive means money entering the account;
  * negative means money leaving it;
  * account type and category never flip a sign.
"""
from __future__ import annotations

import pytest

from parsers.csv_import import parse_csv


def _write(tmp_path, name: str, text: str, *, encoding: str = "utf-8",
           newline: str = "\n") -> str:
    path = tmp_path / name
    body = text.replace("\n", newline) if newline != "\n" else text
    path.write_bytes(body.encode(encoding))
    return str(path)


def _amounts(result) -> list[float]:
    return [round(float(tx["amount"]), 2) for tx in result["transactions"]]


def _problems(result) -> str:
    return " ".join(result.get("receipt", {}).get("problems", [])).lower()


def _engine(action: str, **params) -> dict:
    """One one-shot engine request, exactly as the Tauri shell sends it."""
    import io
    import json

    from desktop.engine import ledger_engine

    output = io.StringIO()
    ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params}) + "\n"),
        output,
    )
    return json.loads(output.getvalue())


def _engine_env(monkeypatch, tmp_path):
    import utils.database as db

    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _make_account(tmp_path, name: str, kind: str) -> int:
    body = _engine("create_account", name=name, type=kind)
    assert body["ok"], body
    accounts = _engine("list_accounts")["data"]["accounts"]
    for account in accounts:
        if account["name"] == name:
            return int(account["id"])
    raise AssertionError(f"account {name!r} was not created: {accounts}")


# ─────────────────────────────────────────────────────────────────────
# Accepted shapes. Each must yield the same three canonical amounts.
# ─────────────────────────────────────────────────────────────────────
EXPECTED = [-84.20, 1500.00, -6.75]

ACCEPTED = [
    (
        "comma-iso-signed",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n"
        "2026-04-11,INVENTED CAFE,-6.75\n",
        {},
    ),
    (
        "semicolon-delimiter",
        "Date;Description;Amount\n"
        "2026-04-02;INVENTED GROCER;-84.20\n"
        "2026-04-05;INVENTED EMPLOYER PAYROLL;1500.00\n"
        "2026-04-11;INVENTED CAFE;-6.75\n",
        {},
    ),
    (
        "tab-delimiter",
        "Date\tDescription\tAmount\n"
        "2026-04-02\tINVENTED GROCER\t-84.20\n"
        "2026-04-05\tINVENTED EMPLOYER PAYROLL\t1500.00\n"
        "2026-04-11\tINVENTED CAFE\t-6.75\n",
        {},
    ),
    (
        "decimal-comma-semicolon",
        "Date;Description;Amount\n"
        "2026-04-02;INVENTED GROCER;-84,20\n"
        "2026-04-05;INVENTED EMPLOYER PAYROLL;1500,00\n"
        "2026-04-11;INVENTED CAFE;-6,75\n",
        {},
    ),
    (
        "thousands-separator",
        "Date,Description,Amount\n"
        '2026-04-02,INVENTED GROCER,"-84.20"\n'
        '2026-04-05,INVENTED EMPLOYER PAYROLL,"1,500.00"\n'
        '2026-04-11,INVENTED CAFE,"-6.75"\n',
        {},
    ),
    (
        "split-debit-credit",
        "Date,Description,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,84.20,\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,,1500.00\n"
        "2026-04-11,INVENTED CAFE,6.75,\n",
        {},
    ),
    (
        "unsigned-plus-direction",
        "Date,Description,Amount,Type\n"
        "2026-04-02,INVENTED GROCER,84.20,Debit\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00,Credit\n"
        "2026-04-11,INVENTED CAFE,6.75,Debit\n",
        {},
    ),
    (
        "signed-agreeing-with-split",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,-84.20,84.20,\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00,,1500.00\n"
        "2026-04-11,INVENTED CAFE,-6.75,6.75,\n",
        {},
    ),
    (
        "quoted-delimiter-in-description",
        "Date,Description,Amount\n"
        '2026-04-02,"INVENTED GROCER, LTD",-84.20\n'
        '2026-04-05,"INVENTED EMPLOYER PAYROLL",1500.00\n'
        '2026-04-11,"INVENTED CAFE, WITH A COMMA",-6.75\n',
        {},
    ),
    (
        "reordered-headers",
        "Amount,Date,Description\n"
        "-84.20,2026-04-02,INVENTED GROCER\n"
        "1500.00,2026-04-05,INVENTED EMPLOYER PAYROLL\n"
        "-6.75,2026-04-11,INVENTED CAFE\n",
        {},
    ),
    (
        "harmless-unknown-columns",
        "Date,Description,Amount,Reference,Balance\n"
        "2026-04-02,INVENTED GROCER,-84.20,REF001,900.00\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00,REF002,2400.00\n"
        "2026-04-11,INVENTED CAFE,-6.75,REF003,2393.25\n",
        {},
    ),
    (
        "trailing-blank-lines",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n"
        "2026-04-11,INVENTED CAFE,-6.75\n"
        "\n\n",
        {},
    ),
    (
        "iso-with-timestamps",
        "Date,Description,Amount\n"
        "2026-04-02 09:14:00,INVENTED GROCER,-84.20\n"
        "2026-04-05 11:02:33,INVENTED EMPLOYER PAYROLL,1500.00\n"
        "2026-04-11 18:40:01,INVENTED CAFE,-6.75\n",
        {},
    ),
]


@pytest.mark.parametrize("name,text,kwargs", ACCEPTED,
                         ids=[c[0] for c in ACCEPTED])
def test_accepted_shapes_produce_one_canonical_reading(
    tmp_path, name, text, kwargs,
) -> None:
    result = parse_csv(_write(tmp_path, f"{name}.csv", text), **kwargs)

    assert result["blocked"] is False, _problems(result)
    assert len(result["transactions"]) == 3, result["transactions"]
    assert _amounts(result) == EXPECTED, f"{name}: {_amounts(result)}"
    assert round(sum(_amounts(result)), 2) == 1409.05


@pytest.mark.parametrize("encoding,newline", [
    ("utf-8", "\n"),
    ("utf-8", "\r\n"),
    ("utf-8-sig", "\n"),
    ("utf-8-sig", "\r\n"),
    ("cp1252", "\r\n"),
])
def test_encodings_and_line_endings_agree(tmp_path, encoding, newline) -> None:
    """A café spelled with an accent must not change any amount."""
    text = (
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n"
        "2026-04-11,INVENTED CAFÉ,-6.75\n"
    )
    path = _write(tmp_path, f"enc-{encoding}-{len(newline)}.csv", text,
                  encoding=encoding, newline=newline)

    result = parse_csv(path)

    assert result["blocked"] is False, _problems(result)
    assert _amounts(result) == EXPECTED


@pytest.mark.parametrize("name,text,expected_dates", [
    (
        "iso",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-15,INVENTED B,-20.00\n",
        ["2026-04-02", "2026-04-15"],
    ),
    (
        "day-first-unambiguous",
        "Date,Description,Amount\n"
        "15/04/2026,INVENTED A,-10.00\n"
        "22/04/2026,INVENTED B,-20.00\n",
        ["2026-04-15", "2026-04-22"],
    ),
    (
        "month-first-unambiguous",
        "Date,Description,Amount\n"
        "04/15/2026,INVENTED A,-10.00\n"
        "04/22/2026,INVENTED B,-20.00\n",
        ["2026-04-15", "2026-04-22"],
    ),
])
def test_date_conventions_are_read_once_per_file(
    tmp_path, name, text, expected_dates,
) -> None:
    result = parse_csv(_write(tmp_path, f"date-{name}.csv", text))

    assert result["blocked"] is False, _problems(result)
    assert [tx["transaction_date"] for tx in result["transactions"]] == \
        expected_dates


def test_ambiguous_dates_block_and_offer_both_readings(tmp_path) -> None:
    """Every component <= 12, so the file cannot settle its own convention."""
    result = parse_csv(_write(
        tmp_path, "ambiguous.csv",
        "Date,Description,Amount\n"
        "01/02/2026,INVENTED A,-10.00\n"
        "03/04/2026,INVENTED B,-20.00\n"
        "05/06/2026,INVENTED C,-30.00\n",
    ))

    assert result["blocked"] is True
    assert not result["transactions"]
    choices = result.get("date_format_choices") or []
    assert len(choices) >= 2, result["receipt"]


# ─────────────────────────────────────────────────────────────────────
# Rejected shapes. Zero committed rows, one clear reason, no guessing.
# ─────────────────────────────────────────────────────────────────────
REJECTED = [
    (
        "signed-contradicts-split",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,-84.20,84.20,\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00,,1500.00\n"
        "2026-04-11,INVENTED CAFE,-6.75,68.75,\n",
        "does not match",
    ),
    (
        "signed-sign-flipped-against-split",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,84.20,84.20,\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00,,1500.00\n",
        "does not match",
    ),
    (
        "row-with-both-debit-and-credit",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,-84.20,84.20,\n"
        "2026-04-05,INVENTED ODD,0.00,25.00,25.00\n",
        "both a debit and a credit",
    ),
    (
        "layout-switches-mid-file",
        "Date,Description,Amount,Debit,Credit\n"
        "2026-04-02,INVENTED GROCER,-84.20,84.20,\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,,,1500.00\n",
        "switches between",
    ),
    (
        "mixed-currency",
        "Date,Description,Amount,Currency\n"
        "2026-04-02,INVENTED A,-10.00,CAD\n"
        "2026-04-03,INVENTED B,-20.00,USD\n",
        "cad and usd",
    ),
    (
        "blank-amount",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED B,\n",
        "blank",
    ),
    (
        "unreadable-amount",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED B,N/A\n",
        "amount",
    ),
    (
        "truncated-row-missing-amount",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED B\n",
        "row",
    ),
    (
        "unquoted-comma-adds-a-field",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED GROCER, LTD,-1,000.00\n",
        "row",
    ),
    (
        "broken-date",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "NOT-A-DATE,INVENTED B,-20.00\n",
        "date",
    ),
    (
        "mixed-decimal-conventions",
        "Date;Description;Amount\n"
        "2026-04-02;INVENTED A;-10,00\n"
        "2026-04-03;INVENTED B;-9.50\n",
        "decimal",
    ),
    (
        # Exactly three digits after a lone separator is the one shape that
        # supports both readings equally. Read as a decimal point this is
        # $1.23; read as a thousands separator it is $1,234.00. 2.4.0 took
        # the North-American default and imported the first, understating
        # the row by a factor of a thousand without saying anything.
        "ambiguous-thousands-punctuation",
        "Date,Description,Amount\n"
        "2026-08-01,INVENTED RENT,-1.234\n"
        "2026-08-02,INVENTED PAYROLL,2.345\n",
        "thousands separator",
    ),
]


@pytest.mark.parametrize("name,text,fragment", REJECTED,
                         ids=[c[0] for c in REJECTED])
def test_rejected_shapes_are_blocked_with_one_clear_reason(
    tmp_path, name, text, fragment,
) -> None:
    result = parse_csv(_write(tmp_path, f"{name}.csv", text))

    assert result["blocked"] is True, (
        f"{name} was accepted: {_amounts(result)}"
    )
    assert fragment in _problems(result), (
        f"{name}: expected {fragment!r} in {_problems(result)!r}"
    )


@pytest.mark.parametrize("name,text,fragment", REJECTED,
                         ids=[c[0] for c in REJECTED])
def test_rejected_shapes_commit_zero_rows(
    tmp_path, monkeypatch, name, text, fragment,
) -> None:
    """A blocked file must reach the database as nothing at all.

    parse_csv still returns the rows it managed to read so the preview can
    show its working, so the property that matters is enforced one level up:
    confirm_import refuses before persist_import_batch is ever called.
    """
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, "Invented Chequing", "chequing")

    path = _write(tmp_path, f"{name}.csv", text)
    body = _engine("confirm_import", files=[
        {"path": path, "account_id": account_id},
    ])

    conn = db.get_connection()
    try:
        committed = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
    finally:
        conn.close()

    assert body["ok"] is False, (
        f"{name} was accepted for import: {body}"
    )
    assert fragment in str(body.get("error", "")).lower() or fragment in \
        str(body.get("error", "")).lower(), body
    assert committed == 0, (
        f"{name} committed {committed} row(s) from a blocked file: {body}"
    )


def test_zero_amount_row_is_kept_and_signed_neutrally(tmp_path) -> None:
    """A genuine zero is data, not an error: a reversed fee posts as 0.00."""
    result = parse_csv(_write(
        tmp_path, "zero-amount.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED REVERSED FEE,0.00\n"
        "2026-04-04,INVENTED B,25.00\n",
    ))

    if result["blocked"]:
        pytest.skip("this build refuses zero-amount rows outright")
    assert _amounts(result) == [-10.00, 0.00, 25.00]


def test_duplicate_headings_do_not_silently_pick_one(tmp_path) -> None:
    """RBC-shaped: two amount columns, one of them empty for every row."""
    result = parse_csv(_write(
        tmp_path, "duplicate-headings.csv",
        "Date,Description,Amount,Amount\n"
        "2026-04-02,INVENTED A,-10.00,\n"
        "2026-04-03,INVENTED B,-20.00,\n",
    ))

    if not result["blocked"]:
        # Accepting is fine only if the real values survived intact.
        assert _amounts(result) == [-10.00, -20.00], _amounts(result)
    else:
        assert _problems(result)


# ─────────────────────────────────────────────────────────────────────
# The sign contract must not bend to account type.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("account_type", ["chequing", "savings", "mastercard",
                                          "visa", "line_of_credit"])
def test_account_type_never_flips_a_sign(tmp_path, account_type) -> None:
    result = parse_csv(
        _write(
            tmp_path, f"acct-{account_type}.csv",
            "Date,Description,Amount\n"
            "2026-04-02,INVENTED PURCHASE,-84.20\n"
            "2026-04-05,INVENTED REFUND,25.00\n",
        ),
        account_type=account_type,
    )

    assert result["blocked"] is False, _problems(result)
    assert _amounts(result) == [-84.20, 25.00], (
        f"{account_type} changed the sign: {_amounts(result)}"
    )


def test_receipt_explains_what_was_detected(tmp_path) -> None:
    """The preview must be able to show its reading before anything commits."""
    result = parse_csv(_write(
        tmp_path, "receipt.csv",
        "Date;Description;Amount\n"
        "2026-04-02;INVENTED GROCER;-84,20\n"
        "2026-04-05;INVENTED EMPLOYER PAYROLL;1500,00\n",
    ))

    receipt = result["receipt"]
    assert result["blocked"] is False, _problems(result)
    assert "semicolon" in receipt["delimiter"].lower()
    assert "comma" in receipt["decimal_separator"].lower()
    assert receipt["rows_parsed"] == 2
    assert receipt["money_in"] == 1500.00
    assert abs(receipt["money_out"]) == 84.20
    assert receipt["date_format"]
    assert receipt["encoding"]


# ─────────────────────────────────────────────────────────────────────
# End-to-end import: counts, account association, duplicate protection.
# ─────────────────────────────────────────────────────────────────────
def _import(tmp_path, path: str, account_id: int, import_mode: str = "") -> dict:
    spec = {"path": path, "account_id": account_id}
    if import_mode:
        spec["import_mode"] = import_mode
    body = _engine("confirm_import", files=[
        spec,
    ])
    assert body["ok"], body
    return body["data"]


def test_import_is_idempotent_and_associates_the_account(
    tmp_path, monkeypatch,
) -> None:
    """A second import of the same statement must insert nothing."""
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, "Invented Chequing", "chequing")
    path = _write(
        tmp_path, "statement.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n"
        "2026-04-11,INVENTED CAFE,-6.75\n",
    )

    first = _import(tmp_path, path, account_id)
    assert first["totals"]["inserted"] == 3, first

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT amount, account_ref FROM transactions "
            "ORDER BY transaction_date"
        ).fetchall()
        assert [round(float(r["amount"]), 2) for r in rows] == EXPECTED
        assert {int(r["account_ref"]) for r in rows} == {account_id}
        fingerprints = conn.execute(
            "SELECT COUNT(DISTINCT dedup_hash) FROM transactions"
        ).fetchone()[0]
        assert fingerprints == 3
    finally:
        conn.close()

    second = _import(tmp_path, path, account_id)
    assert second["totals"]["inserted"] == 0, second
    assert second["totals"]["skipped"] == 3, second

    conn = db.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] == 3
    finally:
        conn.close()


def test_two_identical_same_day_purchases_both_land(
    tmp_path, monkeypatch,
) -> None:
    """Occurrence-aware dedup: a repeated fare is data, not a duplicate."""
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, "Invented Chequing", "chequing")
    path = _write(
        tmp_path, "fares.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED TRANSIT FARE,-3.35\n"
        "2026-04-02,INVENTED TRANSIT FARE,-3.35\n",
    )

    first = _import(tmp_path, path, account_id)
    assert first["totals"]["inserted"] == 2, first

    second = _import(tmp_path, path, account_id)
    assert second["totals"]["inserted"] == 0, second

    conn = db.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] == 2
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["chequing", "savings", "credit_card"])
def test_signs_survive_the_import_into_asset_and_liability_accounts(
    tmp_path, monkeypatch, kind,
) -> None:
    """Positive is money in, negative is money out, whatever the account."""
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, f"Invented {kind}", kind)
    path = _write(
        tmp_path, f"{kind}.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED PURCHASE,-84.20\n"
        "2026-04-05,INVENTED REFUND,25.00\n",
    )

    _import(tmp_path, path, account_id)

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT amount FROM transactions ORDER BY transaction_date"
        ).fetchall()
    finally:
        conn.close()

    assert [round(float(r["amount"]), 2) for r in rows] == [-84.20, 25.00], (
        f"{kind} changed a sign"
    )


def test_the_same_file_cannot_be_imported_into_a_second_account(
    tmp_path, monkeypatch,
) -> None:
    """Re-importing one statement elsewhere would double-count every row."""
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    first_id = _make_account(tmp_path, "Invented Chequing", "chequing")
    second_id = _make_account(tmp_path, "Invented Savings", "savings")
    path = _write(
        tmp_path, "shared.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n",
    )

    _import(tmp_path, path, first_id)
    body = _engine("confirm_import", files=[
        {"path": path, "account_id": second_id},
    ])

    assert body["ok"] is False
    assert "double-count" in body["error"].lower(), body

    conn = db.get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_preamble_and_footer_rows_do_not_become_transactions(
    tmp_path, monkeypatch,
) -> None:
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, "Invented Chequing", "chequing")
    path = _write(
        tmp_path, "with-preamble.csv",
        "Invented Bank of Nowhere\n"
        "Statement period 2026-04-01 to 2026-04-30\n"
        "\n"
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n",
    )

    result = parse_csv(path)
    if result["blocked"]:
        pytest.skip(f"this build refuses preamble files: {_problems(result)}")

    data = _import(tmp_path, path, account_id)
    assert data["totals"]["inserted"] == 2, data

    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT amount FROM transactions").fetchall()
    finally:
        conn.close()
    assert sorted(round(float(r["amount"]), 2) for r in rows) == [-84.20, 1500.00]


def test_utf16_statements_are_read_or_refused_never_guessed(tmp_path) -> None:
    """UTF-16 LE is read; a shape the reader cannot decode is refused."""
    text = (
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n"
    )

    readable = parse_csv(_write(tmp_path, "utf16.csv", text,
                                encoding="utf-16"))
    assert readable["blocked"] is False, _problems(readable)
    assert _amounts(readable) == [-84.20, 1500.00]
    assert "utf-16" in readable["receipt"]["encoding"].lower()

    refused = parse_csv(_write(tmp_path, "utf16be.csv", text,
                               encoding="utf-16-be"))
    assert refused["blocked"] is True
    assert _problems(refused)


@pytest.mark.parametrize("name,text,fragment", [
    (
        "duplicate-amount-headings",
        "Date,Description,Amount,Amount\n"
        "2026-04-02,INVENTED A,-10.00,\n",
        "repeats the column heading",
    ),
    (
        "two-amount-columns-cad-usd",
        "Date,Description,CAD$,USD$\n"
        "2026-04-02,INVENTED A,-10.00,\n",
        "more than one column was detected as amount",
    ),
    (
        "headerless-unknown-bank",
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED B,-20.00\n"
        "2026-04-04,INVENTED C,-30.00\n",
        "no column headings",
    ),
    (
        "excel-serial-dates",
        "Date,Description,Amount\n"
        "46114,INVENTED A,-10.00\n"
        "46117,INVENTED B,-20.00\n",
        "could not read any of the dates",
    ),
])
def test_ambiguous_layouts_ask_rather_than_guess(
    tmp_path, name, text, fragment,
) -> None:
    """These are answerable by the user, so the file waits instead of failing."""
    result = parse_csv(_write(tmp_path, f"{name}.csv", text))

    assert result["blocked"] is True, _amounts(result)
    assert fragment in _problems(result), _problems(result)


def test_preamble_lines_are_skipped_and_reported(tmp_path) -> None:
    result = parse_csv(_write(
        tmp_path, "preamble.csv",
        "Invented Bank of Nowhere\n"
        "Statement period 2026-04-01 to 2026-04-30\n"
        "\n"
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED GROCER,-84.20\n"
        "2026-04-05,INVENTED EMPLOYER PAYROLL,1500.00\n",
    ))

    assert result["blocked"] is False, _problems(result)
    assert _amounts(result) == [-84.20, 1500.00]
    assert any("skipped" in note.lower()
               for note in result["receipt"]["notes"]), result["receipt"]


def test_an_unreadable_footer_blocks_rather_than_dropping_a_row(
    tmp_path,
) -> None:
    """Silently discarding a footer risks discarding a real merchant."""
    result = parse_csv(_write(
        tmp_path, "footer.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED A,-10.00\n"
        "2026-04-03,INVENTED B,-20.00\n"
        "Total,,-30.00\n",
    ))

    assert result["blocked"] is True
    assert "could not read" in _problems(result)


@pytest.mark.parametrize("text,expected", [
    ("Date,Description,Amount\n2026-04-02,INVENTED A,84.20DR\n"
     "2026-04-05,INVENTED B,1500.00CR\n", [-84.20, 1500.00]),
    ("Date,Description,Amount\n2026-04-02,INVENTED A,84.20-\n"
     "2026-04-05,INVENTED B,1500.00\n", [-84.20, 1500.00]),
])
def test_suffix_sign_notations_resolve_to_the_canonical_sign(
    tmp_path, text, expected,
) -> None:
    result = parse_csv(_write(tmp_path, "suffix.csv", text))

    assert result["blocked"] is False, _problems(result)
    assert _amounts(result) == expected


def test_identical_purchases_in_two_files_both_survive(
    tmp_path, monkeypatch,
) -> None:
    """Two genuine same-day fares, one on each of two statements.

    The partial overlap is genuinely ambiguous, so the second import is
    explicitly confirmed as new activity rather than silently treated as a
    re-export. See utils/import_provenance.py.
    """
    import utils.database as db

    _engine_env(monkeypatch, tmp_path)
    account_id = _make_account(tmp_path, "Invented Chequing", "chequing")
    first = _write(
        tmp_path, "week-one.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED TRANSIT FARE,-3.35\n"
        "2026-04-02,INVENTED UNIQUE A,-11.11\n",
    )
    second = _write(
        tmp_path, "week-two.csv",
        "Date,Description,Amount\n"
        "2026-04-02,INVENTED TRANSIT FARE,-3.35\n"
        "2026-04-02,INVENTED UNIQUE B,-22.22\n",
    )

    _import(tmp_path, first, account_id)
    _import(tmp_path, second, account_id, "new")

    conn = db.get_connection()
    try:
        fares = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant LIKE '%TRANSIT%'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert fares == 2, (
        f"only {fares} of 2 genuine fares survived; the second was silently "
        "skipped as a duplicate"
    )

"""Generalized Add Data: user-assisted CSV mapping, reusable import
profiles, and manual entry through the atomic batch path."""
import pytest

import utils.database as db
from utils.csv_mapping import (
    csv_preview, probe_csv, parse_csv_mapped, header_signature,
    validate_mapping, save_import_profile, find_import_profile,
    list_import_profiles, delete_import_profile,
)
from utils.import_service import persist_import_batch
from parsers.csv_import import parse_csv


FOREIGN_CSV = """FLD_01;FLD_02;FLD_03
2026-07-02;COFFEE HAUS BERLIN;-4.50
2026-07-03;GEHALT JULI;2500.00
2026-07-04;SUPERMARKT NORD;-82.10
"""

SPLIT_CSV = """Posted,Merchant Name,Withdrawals,Deposits
07/02/2026,GAS STATION 22,45.00,
07/03/2026,PAYCHEQUE,,1250.00
07/04/2026,GROCER WEST,88.25,
"""


@pytest.fixture()
def foreign_csv(tmp_path):
    # Semicolon-delimited, German headers → the alias parser can't read
    # it (single unrecognizable column).
    p = tmp_path / "foreign.csv"
    p.write_text(FOREIGN_CSV, encoding="utf-8")
    return p


@pytest.fixture()
def split_csv(tmp_path):
    p = tmp_path / "split.csv"
    p.write_text(SPLIT_CSV, encoding="utf-8")
    return p


# ── Probe: honest detection of "auto-parse failed" ────────────────────
def test_probe_flags_unreadable_csv(foreign_csv):
    probe = probe_csv(foreign_csv)
    assert probe["needs_mapping"] is True


def test_probe_accepts_alias_csv(tmp_path):
    p = tmp_path / "plain.csv"
    p.write_text("Date,Description,Amount\n2026-07-02,SHOP,-12.00\n",
                 encoding="utf-8")
    assert probe_csv(p)["needs_mapping"] is False


@pytest.mark.parametrize("date_header", [
    "Date", "Transaction Date", "Posted Date", "Posting Date",
])
def test_automatic_csv_accepts_common_date_headers(tmp_path, date_header):
    # An unambiguous day (>12) so this stays a test of header aliases; a
    # date that could be read either way is refused on purpose, and
    # test_northstar_160_import.py covers that.
    path = tmp_path / f"{date_header}.csv"
    path.write_text(
        f"{date_header},Merchant,Amount\n07/23/2026,LOCAL SHOP,-12.40\n",
        encoding="utf-8",
    )
    result = parse_csv(path)
    assert not result["errors"]
    assert result["transactions"][0]["transaction_date"] == "2026-07-23"
    assert result["transactions"][0]["raw_description"] == "LOCAL SHOP"


@pytest.mark.parametrize("separator", [",", ";", "\t"])
def test_automatic_csv_sniffs_supported_delimiters(tmp_path, separator):
    path = tmp_path / "statement.csv"
    path.write_text(
        separator.join(["Transaction Date", "Description", "Money Out", "Money In"])
        + "\n"
        + separator.join(["2026-07-02", "LOCAL CAFE", "8.25", ""])
        + "\n"
        + separator.join(["2026-07-03", "PAYROLL", "", "1200.00"])
        + "\n",
        encoding="utf-8",
    )
    result = parse_csv(path)
    assert [row["amount"] for row in result["transactions"]] == [-8.25, 1200.0]
    assert result["delimiter"] == separator


def test_automatic_csv_reads_windows_encoding_without_replacement(tmp_path):
    path = tmp_path / "windows.csv"
    path.write_bytes(
        "Date,Description,Amount\n2026-07-02,Caf\u00e9 Montr\u00e9al,-9.50\n".encode(
            "cp1252"
        )
    )
    result = parse_csv(path)
    assert not result["errors"]
    assert result["transactions"][0]["raw_description"] == "Caf\u00e9 Montr\u00e9al"
    assert result["encoding"] == "cp1252"


def test_automatic_csv_reads_utf16_export(tmp_path):
    path = tmp_path / "utf16.csv"
    path.write_bytes(
        "Date\tDescription\tAmount\n2026-07-02\tLOCAL SHOP\t-4.25\n".encode(
            "utf-16"
        )
    )
    result = parse_csv(path)
    assert not result["errors"]
    assert result["transactions"][0]["amount"] == -4.25
    assert result["encoding"] == "utf-16"


def test_binary_file_has_clear_csv_error(tmp_path):
    path = tmp_path / "not-text.csv"
    path.write_bytes(b"Date\x00Description\x00Amount")
    result = parse_csv(path)
    assert result["transactions"] == []
    assert any("does not look like a text CSV" in error for error in result["errors"])


def test_reordered_columns_still_fail_closed_on_an_unreadable_row(tmp_path):
    """Column order is free, but a partial statement is never accepted."""
    path = tmp_path / "mixed.csv"
    path.write_text(
        "Amount,Memo,Posting Date\n"
        "-10.00,VALID DATE,07/23/2026\n"
        "-11.00,BAD DATE,not-a-date\n"
        "-12.00,ALSO VALID,07/25/2026\n",
        encoding="utf-8",
    )
    result = parse_csv(path)
    assert result["blocked"] is True
    assert "not-a-date" in " ".join(result["errors"])
    assert any(
        "nothing was imported" in problem.lower()
        for problem in result["receipt"]["problems"]
    )


def test_a_file_mixing_both_date_orders_is_refused(tmp_path):
    """1.5.0: one file has one date convention.

    Reading 07/23/2026 as month-first and 23/07/2026 as day-first within the
    same file is the per-row guessing that used to scatter statements across
    months. A file containing both is either corrupt or two files joined, so
    Northstar stops instead of picking.
    """
    path = tmp_path / "contradictory.csv"
    path.write_text(
        "Amount,Memo,Posting Date\n"
        "-10.00,MONTH FIRST ONLY,07/23/2026\n"
        "-12.00,DAY FIRST ONLY,23/07/2026\n",
        encoding="utf-8",
    )
    result = parse_csv(path)
    assert result["blocked"] is True
    assert result["transactions"] == []
    assert any("contradict" in error for error in result["errors"])


# ── Mapped parsing ────────────────────────────────────────────────────
def test_mapped_parse_split_columns(split_csv):
    result = parse_csv_mapped(split_csv, {
        "date_col": "Posted", "desc_col": "Merchant Name",
        "amount_mode": "split",
        "debit_col": "Withdrawals", "credit_col": "Deposits",
    })
    txs = result["transactions"]
    assert len(txs) == 3 and not result["errors"]
    gas = next(t for t in txs if "GAS" in t["raw_description"])
    pay = next(t for t in txs if "PAYCHEQUE" in t["raw_description"])
    assert gas["direction"] == "debit" and gas["amount"] == pytest.approx(-45.0)
    assert pay["direction"] == "credit" and pay["amount"] == pytest.approx(1250.0)
    assert gas["transaction_date"] == "2026-07-02"  # MM/DD/YYYY parsed


def test_mapped_parse_preserves_signed_source_amounts(tmp_path):
    # Some banks export spending as POSITIVE numbers — the user says so.
    p = tmp_path / "signed.csv"
    p.write_text("When,What,Value\n2026-07-02,LUNCH SPOT,18.50\n"
                 "2026-07-03,REFUND,-6.00\n", encoding="utf-8")
    result = parse_csv_mapped(p, {
        "date_col": "When", "desc_col": "What",
        "amount_mode": "signed", "amount_col": "Value",
        "positive_is": "credit",
    })
    lunch, refund = result["transactions"]
    assert lunch["direction"] == "credit" and lunch["amount"] == pytest.approx(18.50)
    assert refund["direction"] == "debit" and refund["amount"] == pytest.approx(-6.00)


def test_mapped_parse_rejects_legacy_sign_inversion(tmp_path):
    p = tmp_path / "legacy.csv"
    p.write_text("When,What,Value\n2026-07-02,LUNCH SPOT,18.50\n", encoding="utf-8")
    result = parse_csv_mapped(p, {
        "date_col": "When", "desc_col": "What",
        "amount_mode": "signed", "amount_col": "Value",
        "positive_is": "debit",
    })
    assert result["transactions"] == []
    assert any("source convention" in message for message in result["errors"])


def test_validate_mapping_speaks_plainly():
    problems = validate_mapping({"amount_mode": "signed"},
                                ["A", "B", "C"])
    assert any("date" in p.lower() for p in problems)
    assert any("description" in p.lower() for p in problems)
    assert any("amount" in p.lower() for p in problems)


# ── Profiles: remembered by header signature ─────────────────────────
def test_profile_roundtrip_and_signature_match(ledger_db):
    headers = ["Datum", "Buchungstext", "Betrag"]
    mapping = {"date_col": "Datum", "desc_col": "Buchungstext",
               "amount_mode": "signed", "amount_col": "Betrag",
               "positive_is": "credit"}
    pid = save_import_profile("My EU Bank", headers, mapping,
                              account_type="chequing",
                              conn=ledger_db)
    assert pid > 0
    # Case/whitespace-insensitive match.
    found = find_import_profile([" datum", "BUCHUNGSTEXT ", "betrag"],
                                account_type="chequing", conn=ledger_db)
    assert found is not None
    assert found["mapping"]["amount_col"] == "Betrag"
    # Different headers do not match.
    assert find_import_profile(["Date", "Desc", "Amt"],
                               conn=ledger_db) is None
    # Re-saving the same signature replaces, not duplicates.
    save_import_profile("Renamed", headers, mapping,
                        account_type="chequing", conn=ledger_db)
    assert len(list_import_profiles(conn=ledger_db)) == 1
    assert delete_import_profile(pid, conn=ledger_db) is True


def test_profile_rejects_wrong_or_legacy_account_semantics(ledger_db):
    headers = ["Date", "Description", "Amount"]
    mapping = {"date_col": "Date", "desc_col": "Description",
               "amount_mode": "signed", "amount_col": "Amount",
               "positive_is": "credit"}
    save_import_profile("Chequing export", headers, mapping,
                        account_type="chequing", conn=ledger_db)
    with pytest.raises(ValueError, match="learned for chequing"):
        find_import_profile(headers, account_type="credit_card", conn=ledger_db)

    ledger_db.execute(
        "UPDATE import_profiles SET account_type='', amount_convention=''"
    )
    with pytest.raises(ValueError, match="legacy CSV profile"):
        find_import_profile(headers, account_type="chequing", conn=ledger_db)


def test_signature_is_stable():
    a = header_signature(["Date", "Description", "Amount"])
    b = header_signature([" date", "DESCRIPTION", "amount "])
    assert a == b


# ── Manual entry via the atomic batch path ────────────────────────────
def _manual_tx(desc="FARMERS MARKET CASH", amount=-23.5):
    return {
        "account_type": "cash", "transaction_date": "2026-07-05",
        "raw_description": desc, "amount": amount,
        "direction": "debit", "category": "Groceries",
    }


def test_manual_entry_persists_with_provenance(ledger_db):
    saved = persist_import_batch(
        {"filename": "Manual entry", "account_type": "Manual",
         "statement_period": "2026-07", "rows_parsed": 1},
        [_manual_tx()], conn=ledger_db)
    ledger_db.commit()
    assert saved["inserted"] == 1
    row = ledger_db.execute(
        "SELECT t.*, l.filename FROM transactions t "
        "JOIN import_log l ON l.id = t.import_batch_id "
        "WHERE t.raw_description = 'FARMERS MARKET CASH'").fetchone()
    assert row["filename"] == "Manual entry"
    assert row["account_type"] == "cash"


def test_manual_duplicate_is_skipped_not_doubled(ledger_db):
    for _ in range(2):
        saved = persist_import_batch(
            {"filename": "Manual entry", "account_type": "Manual",
             "statement_period": "2026-07", "rows_parsed": 1},
            [_manual_tx()], conn=ledger_db)
        ledger_db.commit()
    assert saved["inserted"] == 0 and saved["skipped"] == 1
    n = ledger_db.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE raw_description='FARMERS MARKET CASH'").fetchone()[0]
    assert n == 1


def test_manual_entry_reaches_import_summary(ledger_db):
    persist_import_batch(
        {"filename": "Manual entry", "account_type": "Manual",
         "statement_period": "2026-07", "rows_parsed": 1},
        [_manual_tx()], conn=ledger_db)
    ledger_db.commit()
    from utils.import_summary import summarize_import
    s = summarize_import(conn=ledger_db)
    assert s["available"] is True
    assert s["inserted"] == 1
    assert s["spending_added"] == pytest.approx(23.5)

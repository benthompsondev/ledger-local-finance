"""Northstar 1.5.0 — CSV import compatibility matrix.

Every layout below is invented, bank-neutral, and describes the same four
transactions: three outflows totalling -$60.00 and one inflow of $1,000.00,
all dated April 2026.

A layout may end in one of two acceptable states:

  IMPORTS   parsed correctly and cleared for confirm
  BLOCKED   refused with an explanation, `blocked` set

What must never happen is a silent wrong answer: rows imported with the
wrong month, amounts off by a factor of a hundred, or every row at $0.00
while the import reports success. Those were real defects; each has a test.
"""
from pathlib import Path

import pytest

from parsers.csv_import import parse_csv

EXPECTED_OUT = -60.0
EXPECTED_IN = 1000.0


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "statement.csv"
    path.write_text(text, encoding="utf-8")
    return path


def _totals(result):
    txs = result["transactions"]
    return (
        round(sum(t["amount"] for t in txs if t["amount"] < 0), 2),
        round(sum(t["amount"] for t in txs if t["amount"] > 0), 2),
    )


# ── layouts that must import cleanly ─────────────────────────────────────

CLEAN = {
    "tangerine_style": """Date,Transaction,Name,Memo,Amount
04/02/2026,DEBIT,INVENTED COFFEE CO,Card purchase,-10.00
04/05/2026,DEBIT,INVENTED GROCER,Card purchase,-20.00
04/09/2026,DEBIT,INVENTED FUEL,Card purchase,-30.00
04/15/2026,CREDIT,INVENTED EMPLOYER,Payroll,1000.00
""",
    "iso_dates_signed_amount": """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
""",
    "split_debit_credit": """Transaction Date,Description,Debit,Credit
2026-04-02,INVENTED COFFEE CO,10.00,
2026-04-05,INVENTED GROCER,20.00,
2026-04-09,INVENTED FUEL,30.00,
2026-04-15,INVENTED EMPLOYER,,1000.00
""",
    "posting_date_header": """Details,Posting Date,Description,Amount,Type
DEBIT,04/02/2026,INVENTED COFFEE CO,-10.00,DEBIT
DEBIT,04/05/2026,INVENTED GROCER,-20.00,DEBIT
DEBIT,04/09/2026,INVENTED FUEL,-30.00,DEBIT
CREDIT,04/15/2026,INVENTED EMPLOYER,1000.00,CREDIT
""",
    "semicolon_delimited": """Date;Description;Amount
2026-04-02;INVENTED COFFEE CO;-10.00
2026-04-05;INVENTED GROCER;-20.00
2026-04-09;INVENTED FUEL;-30.00
2026-04-15;INVENTED EMPLOYER;1000.00
""",
    "trailing_total_row": """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00

,TOTAL,940.00
""",
    # ── previously broken, fixed in 1.5.0 ────────────────────────────────
    "day_first_dates": """Date,Description,Amount
02/04/2026,INVENTED COFFEE CO,-10.00
05/04/2026,INVENTED GROCER,-20.00
09/04/2026,INVENTED FUEL,-30.00
15/04/2026,INVENTED EMPLOYER,1000.00
""",
    "european_decimal_comma": """Date;Description;Amount
2026-04-02;INVENTED COFFEE CO;-10,00
2026-04-05;INVENTED GROCER;-20,00
2026-04-09;INVENTED FUEL;-30,00
2026-04-15;INVENTED EMPLOYER;1000,00
""",
    "currency_headed_amount_column": '''"Account Type","Transaction Date","Description 1","Description 2","CAD$"
"Chequing","2026-04-02","INVENTED COFFEE CO","STORE 12",-10.00
"Chequing","2026-04-05","INVENTED GROCER","STORE 44",-20.00
"Chequing","2026-04-09","INVENTED FUEL","PUMP 3",-30.00
"Chequing","2026-04-15","PAYROLL DEP","INVENTED EMPLOYER",1000.00
''',
    "preamble_rows_before_header": """Account Statement
Account: INVENTED-0000
Period: April 2026

Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
""",
    "compact_yyyymmdd_dates": """First Bank Card,Transaction Type,Date Posted,Transaction Amount,Description
0000,DEBIT,20260402,-10.00,INVENTED COFFEE CO
0000,DEBIT,20260405,-20.00,INVENTED GROCER
0000,DEBIT,20260409,-30.00,INVENTED FUEL
0000,CREDIT,20260415,1000.00,INVENTED EMPLOYER
""",
    "unsigned_amount_with_direction_column": """Date,Description,Type,Amount
2026-04-02,INVENTED COFFEE CO,Debit,10.00
2026-04-05,INVENTED GROCER,Debit,20.00
2026-04-09,INVENTED FUEL,Debit,30.00
2026-04-15,INVENTED EMPLOYER,Credit,1000.00
""",
    # No single cell settles the day/month order here, but only the
    # day-first reading keeps these four rows inside one statement period;
    # month-first would scatter them across February to November.
    "ambiguous_order_resolved_by_date_span": """Date,Description,Amount
02/04/2026,INVENTED COFFEE CO,-10.00
05/04/2026,INVENTED GROCER,-20.00
09/04/2026,INVENTED FUEL,-30.00
11/04/2026,INVENTED EMPLOYER,1000.00
""",
}


@pytest.mark.parametrize("label", sorted(CLEAN))
def test_layout_imports_correctly(tmp_path, label):
    result = parse_csv(_write(tmp_path, CLEAN[label]), account_type="chequing")

    assert not result["blocked"], (
        f"{label} was blocked: {result['receipt']['problems']}"
    )
    txs = result["transactions"]
    assert len(txs) == 4, f"{label}: expected 4 rows, got {len(txs)}"

    out, inflow = _totals(result)
    assert out == pytest.approx(EXPECTED_OUT), f"{label}: outflow wrong"
    assert inflow == pytest.approx(EXPECTED_IN), f"{label}: inflow wrong"

    months = {t["transaction_date"][:7] for t in txs}
    assert months == {"2026-04"}, f"{label}: dates landed in {months}"


# ── layouts that must be refused, not guessed ────────────────────────────

BLOCKED = {
    # Some rows can only be day-first, others only month-first.
    "contradictory_date_order": """Date,Description,Amount
13/04/2026,INVENTED COFFEE CO,-10.00
04/22/2026,INVENTED GROCER,-20.00
09/04/2026,INVENTED FUEL,-30.00
15/04/2026,INVENTED EMPLOYER,1000.00
""",
    # A headerless file whose shape matches no bank Northstar knows. (One
    # that DOES match a known layout is now read with that bank's
    # conventions; see tests/test_bank_presets.py.)
    "no_header_row": """04/02/2026,INVENTED COFFEE CO,10.00,x,y,z,w
04/05/2026,INVENTED GROCER,20.00,x,y,z,w
04/09/2026,INVENTED FUEL,30.00,x,y,z,w
04/15/2026,INVENTED EMPLOYER,1000.00,x,y,z,w
""",
    # Opaque internal field codes, which no alias list can be expected to
    # cover. (German and French headings are read directly now; see
    # tests/test_csv_world.py.)
    "unknown_header_names": """FLD_01,FLD_02,FLD_03
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
""",
}


@pytest.mark.parametrize("label", sorted(BLOCKED))
def test_layout_is_refused_rather_than_guessed(tmp_path, label):
    result = parse_csv(_write(tmp_path, BLOCKED[label]), account_type="chequing")

    assert result["blocked"] is True, f"{label} should not have been accepted"
    assert result["receipt"]["problems"], f"{label}: no explanation given"
    assert result["needs_mapping"] is True


def test_headerless_file_suggests_column_roles(tmp_path):
    """Refusing is not enough; the user needs a way forward."""
    result = parse_csv(
        _write(tmp_path, BLOCKED["no_header_row"]), account_type="chequing",
    )
    assert result["header"] == [f"column_{i}" for i in range(1, 8)]
    roles = result["suggested_roles"]
    assert roles.get("column_1") == "transaction_date"
    assert roles.get("column_2") == "raw_description"


# ── the receipt shown before anything is saved ───────────────────────────

def test_preview_receipt_reports_every_detected_convention(tmp_path):
    result = parse_csv(
        _write(tmp_path, CLEAN["european_decimal_comma"]),
        account_type="chequing",
    )
    receipt = result["receipt"]

    assert receipt["rows_parsed"] == 4
    assert receipt["first_date"] == "2026-04-02"
    assert receipt["last_date"] == "2026-04-15"
    assert receipt["months_spanned"] == 1
    assert receipt["money_in"] == pytest.approx(1000.0)
    assert receipt["money_out"] == pytest.approx(-60.0)
    assert receipt["date_format"] == "YYYY-MM-DD"
    assert "comma" in receipt["decimal_separator"]
    assert receipt["delimiter"] == "semicolon"
    assert receipt["zero_value_rows"] == 0
    assert receipt["has_header"] is True


def test_preview_reports_skipped_preamble_lines(tmp_path):
    result = parse_csv(
        _write(tmp_path, CLEAN["preamble_rows_before_header"]),
        account_type="chequing",
    )
    assert result["receipt"]["skipped_lines"] == 4
    assert result["receipt"]["rows_parsed"] == 4


def test_day_first_detection_is_reported_to_the_user(tmp_path):
    result = parse_csv(
        _write(tmp_path, CLEAN["day_first_dates"]), account_type="chequing",
    )
    assert result["receipt"]["date_format"] == "DD/MM/YYYY"
    assert any("day-first" in note for note in result["receipt"]["notes"])


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_the_engine_refuses_to_confirm_a_blocked_file(engine_env, tmp_path):
    """A receipt nobody enforces is decoration. Confirm must actually stop."""
    from tests.test_native_desktop_engine import _request

    path = _write(tmp_path, BLOCKED["contradictory_date_order"])
    code, payload = _request({
        "action": "preview_import", "params": {"paths": [str(path)]},
    })
    assert code == 0
    entry = payload["data"]["files"][0]
    assert entry["blocked"] is True
    assert entry["error"]

    code, payload = _request({
        "action": "create_account",
        "params": {"name": "Invented Chequing", "type": "chequing"},
    })
    account_id = payload["data"]["created_id"]
    code, payload = _request({
        "action": "confirm_import",
        "params": {"files": [{"path": str(path), "account_id": account_id}]},
    })
    assert code != 0, "a blocked file must not import"


def test_an_all_zero_import_is_blocked(tmp_path):
    """The RBC-shaped defect: rows parse, but no amount column was found."""
    text = """Date,Description,Mystery Column
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, text), account_type="chequing")
    assert result["blocked"] is True
    assert any(
        "could not find any transaction amounts" in problem.lower()
        for problem in result["receipt"]["problems"]
    )

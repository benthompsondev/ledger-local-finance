"""Northstar 1.6.0 — the importer stops guessing wrong quietly.

1.5.0 made the importer decide a file's shape once and fail closed. These are
the cases that still slipped through, all of them silent: a file that imports
"successfully" while saying something untrue about the money.
"""
from pathlib import Path

import pytest

from parsers.csv_import import parse_csv


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


def test_short_direction_markers_do_not_invert_the_ledger(tmp_path):
    """`D` and `C` were unrecognised, so every withdrawal became income.

    The file imported with no complaint at all, reporting $1,060 in and $0
    out against a true $60 out. Nothing about the result looked wrong.
    """
    result = parse_csv(_write(tmp_path, """Date,Description,Type,Amount
2026-04-02,INVENTED COFFEE CO,D,10.00
2026-04-05,INVENTED GROCER,D,20.00
2026-04-09,INVENTED FUEL,D,30.00
2026-04-15,INVENTED EMPLOYER,C,1000.00
"""), account_type="chequing")

    assert not result["blocked"]
    out, inflow = _totals(result)
    assert out == pytest.approx(-60.0)
    assert inflow == pytest.approx(1000.0)


def test_trailing_minus_amounts_are_read(tmp_path):
    """Mainframe exports write a negative as `10.00-`."""
    result = parse_csv(_write(tmp_path, """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,10.00-
2026-04-05,INVENTED GROCER,20.00-
2026-04-09,INVENTED FUEL,30.00-
2026-04-15,INVENTED EMPLOYER,1000.00
"""), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_currency_prefixed_amounts_are_read(tmp_path):
    result = parse_csv(_write(tmp_path, """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,CAD -10.00
2026-04-05,INVENTED GROCER,CAD -20.00
2026-04-09,INVENTED FUEL,CAD -30.00
2026-04-15,INVENTED EMPLOYER,CAD 1000.00
"""), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_one_unreadable_amount_blocks_the_whole_file(tmp_path):
    """A single junk amount used to import as $0 and pass unnoticed.

    Recording $40 of spending where the statement says $60 is worse than
    refusing the file: the user has no way to notice the missing $20.
    """
    result = parse_csv(_write(tmp_path, """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,~~BROKEN~~
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
"""), account_type="chequing")

    assert result["blocked"] is True
    assert any("amount" in p.lower() for p in result["receipt"]["problems"])


def test_a_blank_amount_is_not_treated_as_unreadable(tmp_path):
    """Split debit/credit files leave one side empty on every row."""
    result = parse_csv(_write(tmp_path, """Date,Description,Debit,Credit
2026-04-02,INVENTED COFFEE CO,10.00,
2026-04-05,INVENTED GROCER,20.00,
2026-04-09,INVENTED FUEL,30.00,
2026-04-15,INVENTED EMPLOYER,,1000.00
"""), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_mixed_currencies_are_refused(tmp_path):
    """Adding CAD to USD produces a number that means nothing."""
    result = parse_csv(_write(tmp_path, """Date,Description,Amount,Currency
2026-04-02,INVENTED COFFEE CO,-10.00,CAD
2026-04-05,INVENTED US STORE,-20.00,USD
2026-04-09,INVENTED FUEL,-30.00,CAD
2026-04-15,INVENTED EMPLOYER,1000.00,CAD
"""), account_type="chequing")

    assert result["blocked"] is True
    assert any(
        "currenc" in p.lower() for p in result["receipt"]["problems"]
    )


def test_a_single_currency_column_is_fine(tmp_path):
    result = parse_csv(_write(tmp_path, """Date,Description,Amount,Currency
2026-04-02,INVENTED COFFEE CO,-10.00,CAD
2026-04-05,INVENTED GROCER,-20.00,CAD
2026-04-09,INVENTED FUEL,-30.00,CAD
2026-04-15,INVENTED EMPLOYER,1000.00,CAD
"""), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_timestamped_dates_are_read(tmp_path):
    """Exports that carry a time of day are ordinary, not corrupt."""
    result = parse_csv(_write(tmp_path, """Date,Description,Amount
2026-04-02 14:31:00,INVENTED COFFEE CO,-10.00
2026-04-05 09:02:11,INVENTED GROCER,-20.00
2026-04-09 18:44:02,INVENTED FUEL,-30.00
2026-04-15 07:15:39,INVENTED EMPLOYER,1000.00
"""), account_type="chequing")

    assert not result["blocked"]
    assert {t["transaction_date"] for t in result["transactions"]} == {
        "2026-04-02", "2026-04-05", "2026-04-09", "2026-04-15",
    }


def test_a_genuinely_ambiguous_date_file_asks_instead_of_assuming(tmp_path):
    """Every day and month below 13, and no ordering to settle it.

    1.5.0 assumed month-first and told the user to "switch it" using a
    control that does not exist. Either the file gives evidence or Northstar
    asks; it must not invent a convention and then blame the reader.
    """
    result = parse_csv(_write(tmp_path, """Date,Description,Amount
01/02/2026,INVENTED COFFEE CO,-10.00
03/04/2026,INVENTED GROCER,-20.00
05/06/2026,INVENTED FUEL,-30.00
07/08/2026,INVENTED EMPLOYER,1000.00
"""), account_type="chequing")

    assert result["blocked"] is True
    assert result["needs_mapping"] is True
    assert any(
        "DD/MM" in p or "day-first" in p for p in result["receipt"]["problems"]
    )


def test_an_explicit_date_format_resolves_an_ambiguous_file(tmp_path):
    """Asking is only reasonable if the answer can be supplied."""
    path = _write(tmp_path, """Date,Description,Amount
01/02/2026,INVENTED COFFEE CO,-10.00
03/04/2026,INVENTED GROCER,-20.00
05/06/2026,INVENTED FUEL,-30.00
07/08/2026,INVENTED EMPLOYER,1000.00
""")
    result = parse_csv(path, account_type="chequing", date_format="%d/%m/%Y")

    assert not result["blocked"]
    assert sorted(t["transaction_date"] for t in result["transactions"]) == [
        "2026-02-01", "2026-04-03", "2026-06-05", "2026-08-07",
    ]

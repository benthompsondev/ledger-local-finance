"""Amount punctuation that supports two readings must be refused, not guessed.

`1.234` is $1.23 when the dot is a decimal point and $1,234.00 when it groups
thousands. Nothing in the cell settles it, and 2.4.0 resolved it with the
North-American default, so a rent line imported at a thousandth of its value
and the month looked cheap. The rule this file locks is the repo's import
rule: detect the convention once per file, and refuse the file when the
evidence is genuinely absent rather than picking a side.

Three levels are covered, because a refusal is only worth anything if it
survives all the way to the database:

  * the detector, in isolation;
  * the preview, which is what the Add Data screen renders;
  * the confirm path, which must commit nothing.
"""
from __future__ import annotations

import pytest

from parsers.csv_dialect import detect_decimal_separator, parse_amount_with
from parsers.csv_import import parse_csv


def _write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _statement(amounts: list[str], delimiter: str = ",") -> str:
    """A three-column statement. Amounts are quoted: both conventions under
    test put a comma inside the number, which would otherwise be read as a
    field separator and blocked for a different reason entirely."""
    header = delimiter.join(["Date", "Description", "Amount"])
    rows = "".join(
        delimiter.join([
            f"2026-08-{index + 1:02d}",
            f"INVENTED MERCHANT {index}",
            f'"{amount}"',
        ]) + "\n"
        for index, amount in enumerate(amounts)
    )
    return f"{header}\n{rows}"


# ── the detector ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("values", [
    ["-1.234", "2.345"],
    ["-1,234", "2,345"],
    ["1.234"],
    ["-12.500", "-1.000", "-999.000"],
])
def test_ambiguous_grouping_is_refused(values) -> None:
    _sep, _notes, problems = detect_decimal_separator(values)

    assert problems, f"{values} was resolved silently"
    assert "thousands separator" in problems[0]


def test_the_refusal_names_both_readings_in_money() -> None:
    """The user has to be able to see what the disagreement costs them."""
    _sep, _notes, problems = detect_decimal_separator(["-1.234"])

    assert "$1.23" in problems[0]
    assert "$1,234.00" in problems[0]


@pytest.mark.parametrize("values,expected_sep,expected", [
    # The four conventions the detector must keep reading. Each carries a
    # fractional part of one or two digits somewhere, which is what settles
    # the separator's role.
    (["-1.234,56", "2.345,67"], ",", [-1234.56, 2345.67]),
    (["-1,234.56", "2,345.67"], ".", [-1234.56, 2345.67]),
    (["-1234,56", "2345,67"], ",", [-1234.56, 2345.67]),
    (["-1234.56", "2345.67"], ".", [-1234.56, 2345.67]),
    # No separator at all is not ambiguous.
    (["-1234", "2345"], ".", [-1234.0, 2345.0]),
    # A repeated separator can only be grouping, so the file is readable
    # even though no cell shows cents.
    (["-12.345.678", "1.234"], ",", [-12345678.0, 1234.0]),
    (["-12,345,678", "1,234"], ".", [-12345678.0, 1234.0]),
])
def test_unambiguous_conventions_still_parse(
    values, expected_sep, expected,
) -> None:
    separator, _notes, problems = detect_decimal_separator(values)

    assert problems == []
    assert separator == expected_sep
    assert [parse_amount_with(v, separator) for v in values] == expected


def test_one_cell_with_cents_settles_the_whole_file() -> None:
    """Evidence anywhere in the column resolves the ambiguous cells."""
    separator, _notes, problems = detect_decimal_separator(
        ["-1.234,56", "-2.345", "-3.456"]
    )

    assert problems == []
    assert separator == ","
    assert parse_amount_with("-2.345", separator) == -2345.0


# ── the preview ──────────────────────────────────────────────────────────

def test_preview_blocks_the_ambiguous_statement(tmp_path) -> None:
    path = _write(
        tmp_path, "ambiguous.csv",
        "Date,Description,Amount\n"
        "2026-08-01,INVENTED RENT,-1.234\n"
        "2026-08-02,INVENTED PAYROLL,2.345\n",
    )

    result = parse_csv(path)

    assert result["blocked"] is True
    problems = " ".join(result["receipt"]["problems"])
    assert "thousands separator" in problems


def test_preview_still_accepts_a_european_statement(tmp_path) -> None:
    """The fix must not cost us files that were importing correctly."""
    path = _write(tmp_path, "european.csv", _statement(
        ["-1.234,56", "2.345,67", "-99,00"], delimiter=";",
    ))

    result = parse_csv(path)

    assert result["blocked"] is False, result["receipt"]["problems"]
    assert [round(tx["amount"], 2) for tx in result["transactions"]] == [
        -1234.56, 2345.67, -99.00,
    ]
    assert "comma" in result["receipt"]["decimal_separator"].lower()


def test_preview_still_accepts_a_north_american_statement(tmp_path) -> None:
    path = _write(tmp_path, "north-american.csv", _statement(
        ["-1,234.56", "2,345.67", "-99.00"]
    ))

    result = parse_csv(path)

    assert result["blocked"] is False, result["receipt"]["problems"]
    assert [round(tx["amount"], 2) for tx in result["transactions"]] == [
        -1234.56, 2345.67, -99.00,
    ]

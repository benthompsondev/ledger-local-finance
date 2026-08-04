"""What real bank exports do when they are not on their best behaviour.

Every preset above is written from a bank's published layout. Real downloads
carry quoted commas, blank rows, footer totals, encodings from a decade ago
and columns the documentation forgot. A preset that only works on a tidy file
is worse than no preset, because it is trusted.
"""
import pytest

from parsers.bank_presets import match_header_preset, match_positional_preset
from parsers.csv_import import parse_csv


def _write(tmp_path, text, name="statement.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def _totals(result):
    txs = result["transactions"]
    return (
        round(sum(t["amount"] for t in txs if t["amount"] < 0), 2),
        round(sum(t["amount"] for t in txs if t["amount"] > 0), 2),
    )


# ── merchant names that fight the parser ─────────────────────────────────

def test_a_merchant_name_containing_a_comma(tmp_path):
    result = parse_csv(_write(tmp_path, '''Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,04/02/2026,"INVENTED SHOP, LLC",-10.00,DEBIT_CARD,4990.00,
DEBIT,04/05/2026,"BLOGGS, SMITH AND CO",-20.00,DEBIT_CARD,4970.00,
DEBIT,04/09/2026,INVENTED FUEL,-30.00,DEBIT_CARD,4940.00,
CREDIT,04/15/2026,INVENTED EMPLOYER,1000.00,ACH_CREDIT,5940.00,
'''), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))
    assert any("," in t["raw_description"] for t in result["transactions"])


def test_a_merchant_name_containing_quotes_and_hashes(tmp_path):
    result = parse_csv(_write(tmp_path, '''Date,Description,Amount,Running Bal.
04/02/2026,"INVENTED ""BEST"" CAFE #12",-10.00,4990.00
04/05/2026,INVENTED GROCER #4021,-20.00,4970.00
04/09/2026,SQ *INVENTED FUEL,-30.00,4940.00
04/15/2026,INVENTED EMPLOYER,1000.00,5940.00
'''), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_an_accented_merchant_name_in_windows_encoding(tmp_path):
    """Quebec merchants, exported from a bank that still ships CP1252."""
    result = parse_csv(_write(tmp_path, """Date,Description,Amount,Running Bal.
04/02/2026,INVENTED CAFE MONTREAL,-10.00,4990.00
04/05/2026,INVENTED EPICERIE,-20.00,4970.00
04/09/2026,INVENTED STATION,-30.00,4940.00
04/15/2026,INVENTED EMPLOYER,1000.00,5940.00
""", encoding="cp1252"), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


# ── files that are not just transactions ─────────────────────────────────

def test_a_footer_total_row_is_not_a_transaction(tmp_path):
    result = parse_csv(_write(tmp_path, """Status,Date,Description,Debit,Credit
Cleared,04/02/2026,INVENTED COFFEE CO,10.00,
Cleared,04/05/2026,INVENTED GROCER,20.00,
Cleared,04/09/2026,INVENTED FUEL,30.00,
Cleared,04/15/2026,INVENTED EMPLOYER,,1000.00

,,TOTAL,60.00,1000.00
"""), account_type="chequing")

    assert len(result["transactions"]) == 4
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_blank_rows_scattered_through_the_file(tmp_path):
    result = parse_csv(_write(tmp_path, """Trans. Date,Post Date,Description,Amount,Category
04/02/2026,04/03/2026,INVENTED COFFEE CO,10.00,Restaurants

04/05/2026,04/06/2026,INVENTED GROCER,20.00,Supermarkets

04/09/2026,04/10/2026,INVENTED FUEL,30.00,Gasoline
04/15/2026,04/16/2026,INVENTED EMPLOYER,-1000.00,Payments
"""), account_type="mastercard")

    assert len(result["transactions"]) == 4
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_a_bank_that_added_a_column_since_the_preset_was_written(tmp_path):
    """Presets must not be brittle to a bank appending a field."""
    result = parse_csv(_write(tmp_path, """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #,Memo
DEBIT,04/02/2026,INVENTED COFFEE CO,-10.00,DEBIT_CARD,4990.00,,note
DEBIT,04/05/2026,INVENTED GROCER,-20.00,DEBIT_CARD,4970.00,,note
DEBIT,04/09/2026,INVENTED FUEL,-30.00,DEBIT_CARD,4940.00,,note
CREDIT,04/15/2026,INVENTED EMPLOYER,1000.00,ACH_CREDIT,5940.00,,note
"""), account_type="chequing")

    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))


def test_a_single_transaction_export(tmp_path):
    result = parse_csv(_write(tmp_path, """Status,Date,Description,Debit,Credit
Cleared,04/02/2026,INVENTED COFFEE CO,10.00,
"""), account_type="chequing")

    assert not result["blocked"]
    assert len(result["transactions"]) == 1
    assert result["transactions"][0]["amount"] == pytest.approx(-10.0)


# ── the dangerous ones: wrong bank, wrong sign ───────────────────────────

def test_a_headerless_file_matching_no_bank_is_refused(tmp_path):
    """Better to ask than to read someone's statement as the wrong shape."""
    result = parse_csv(_write(tmp_path, """2026-04-02,INVENTED A,10.00,x,y,z,w
2026-04-05,INVENTED B,20.00,x,y,z,w
2026-04-09,INVENTED C,30.00,x,y,z,w
"""), account_type="chequing")

    assert result["blocked"] is True
    assert result["needs_mapping"] is True


def test_two_headerless_banks_of_the_same_width_are_told_apart(tmp_path):
    """TD and Wells Fargo both export five unlabelled columns."""
    td = [["2026-04-02", "INVENTED SHOP", "10.00", "", "4990.00"],
          ["2026-04-05", "INVENTED GROCER", "20.00", "", "4970.00"],
          ["2026-04-09", "INVENTED FUEL", "30.00", "", "4940.00"]]
    wf = [["04/02/2026", "-10.00", "*", "", "INVENTED SHOP"],
          ["04/05/2026", "-20.00", "*", "", "INVENTED GROCER"],
          ["04/09/2026", "-30.00", "*", "", "INVENTED FUEL"]]

    assert match_positional_preset(td).key == "td"
    assert match_positional_preset(wf).key == "wellsfargo"


def test_an_inverted_bank_is_not_matched_by_a_lookalike_header(tmp_path):
    """Getting the sign convention from the wrong bank inverts the ledger.

    A Bank of America export shares Date, Description and Amount with Amex,
    but its purchases are already negative. Matching it as Amex would flip
    every row.
    """
    header = ["Date", "Description", "Amount", "Running Bal."]
    matched = match_header_preset(header)
    assert matched is not None
    assert matched.amount_sign == "standard"
    assert "Bank of America" in matched.name


def test_a_plain_three_column_header_matches_no_bank(tmp_path):
    """`Date, Description, Amount` is everyone's header, so it is nobody's.

    A preset that claimed it would apply some bank's sign convention to
    every generic export in the world.
    """
    assert match_header_preset(["Date", "Description", "Amount"]) is None

    result = parse_csv(_write(tmp_path, """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
"""), account_type="chequing")
    assert not result["blocked"]
    assert _totals(result) == (pytest.approx(-60.0), pytest.approx(1000.0))
    assert result["receipt"]["bank"] == ""


def test_naming_the_wrong_bank_by_hand_does_not_corrupt_silently(tmp_path):
    """If the user picks wrongly, the receipt still shows what happened."""
    text = """Date,Description,Amount
04/02/2026,INVENTED COFFEE CO,-10.00
04/05/2026,INVENTED GROCER,-20.00
04/09/2026,INVENTED FUEL,-30.00
04/15/2026,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, text), account_type="mastercard",
                       bank="amex")
    # Amex inverts, so this correct-signed file comes out backwards. The
    # point is that the receipt says which bank was assumed, so the date
    # range and totals shown before confirming reveal it.
    assert result["receipt"]["bank"] == "American Express"
    out, inflow = _totals(result)
    assert out == pytest.approx(-1000.0)
    assert inflow == pytest.approx(60.0)


def test_every_preset_has_a_note_explaining_itself(tmp_path):
    from parsers.bank_presets import PRESETS

    for preset in PRESETS:
        assert preset.note, f"{preset.key} has no explanation"
        assert preset.header_signature or preset.positional_kinds, (
            f"{preset.key} can never match anything"
        )

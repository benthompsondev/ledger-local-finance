"""Statement shapes from outside the preset list, and the traps they carry.

Every file here is invented, but every layout is a real published export
format or a real punctuation convention. The question each case asks is not
"did it import" but "did it import the right thing": a file that loads four
rows with inverted signs is worse than one that refuses.

The truth every readable case must reproduce is the same, so a wrong answer
shows up as a number rather than as a shrug: three outflows of $60.00, one
inflow of $1,000.00, all in April 2026.
"""
import pytest

from parsers.csv_import import parse_csv

WANT_OUT_TOTAL = 180.00
WANT_IN_TOTAL = 1000.00


def _write(tmp_path, name, text, encoding="utf-8", newline="\r\n"):
    path = tmp_path / name
    body = text.replace("\n", newline) if newline != "\n" else text
    path.write_bytes(body.encode(encoding))
    return str(path)


def _totals(result):
    txs = result["transactions"]
    outs = [t for t in txs if t["direction"] == "debit"]
    ins = [t for t in txs if t["direction"] == "credit"]
    return (
        len(outs),
        round(sum(abs(float(t["amount"])) for t in outs), 2),
        round(sum(abs(float(t["amount"])) for t in ins), 2),
        {str(t["transaction_date"])[:7] for t in txs},
    )


def _assert_correct(result, label):
    assert result["transactions"], f"{label}: nothing imported"
    assert not result.get("blocked"), f"{label}: refused"
    out_count, out_total, in_total, months = _totals(result)
    assert out_count == 3, f"{label}: {out_count} outflows"
    assert out_total == WANT_OUT_TOTAL, f"{label}: outflows {out_total}"
    assert in_total == WANT_IN_TOTAL, f"{label}: inflows {in_total}"
    assert months == {"2026-04"}, f"{label}: months {sorted(months)}"


# ── real export layouts ─────────────────────────────────────────────────

WORLD = {
    "monzo": """Transaction ID,Date,Time,Type,Name,Category,Amount,Currency,Notes
tx_001,02/04/2026,09:14:00,Card payment,Invented Cafe,Eating out,-60.00,GBP,
tx_002,09/04/2026,10:02:00,Card payment,Invented Grocer,Groceries,-60.00,GBP,
tx_003,16/04/2026,11:30:00,Card payment,Invented Fuel,Transport,-60.00,GBP,
tx_004,24/04/2026,08:00:00,Faster payment,Invented Employer,Income,1000.00,GBP,
""",
    "starling": """Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)
02/04/2026,Invented Cafe,INVENTED CAFE,FASTER PAYMENT,-60.00,940.00
09/04/2026,Invented Grocer,INVENTED GROCER,FASTER PAYMENT,-60.00,880.00
16/04/2026,Invented Fuel,INVENTED FUEL,FASTER PAYMENT,-60.00,820.00
24/04/2026,Invented Employer,INVENTED EMPLOYER,FASTER PAYMENT,1000.00,1820.00
""",
    "barclays": """Number,Date,Account,Amount,Subcategory,Memo
1,02/04/2026,20-00-00 12345678,-60.00,Card,INVENTED CAFE
2,09/04/2026,20-00-00 12345678,-60.00,Card,INVENTED GROCER
3,16/04/2026,20-00-00 12345678,-60.00,Card,INVENTED FUEL
4,24/04/2026,20-00-00 12345678,1000.00,Bank credit,INVENTED EMPLOYER
""",
    "revolut": """Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State
CARD_PAYMENT,Current,2026-04-02 09:14:00,2026-04-02 09:14:00,INVENTED CAFE,-60.00,0.00,CAD,COMPLETED
CARD_PAYMENT,Current,2026-04-09 10:02:00,2026-04-09 10:02:00,INVENTED GROCER,-60.00,0.00,CAD,COMPLETED
CARD_PAYMENT,Current,2026-04-16 11:30:00,2026-04-16 11:30:00,INVENTED FUEL,-60.00,0.00,CAD,COMPLETED
TOPUP,Current,2026-04-24 08:00:00,2026-04-24 08:00:00,INVENTED EMPLOYER,1000.00,0.00,CAD,COMPLETED
""",
    "commbank_headerless": """02/04/2026,-60.00,"INVENTED CAFE",940.00
09/04/2026,-60.00,"INVENTED GROCER",880.00
16/04/2026,-60.00,"INVENTED FUEL",820.00
24/04/2026,1000.00,"INVENTED EMPLOYER",1820.00
""",
    "anz": """Date,Amount,Description,Balance
02/04/2026,-60.00,INVENTED CAFE,940.00
09/04/2026,-60.00,INVENTED GROCER,880.00
16/04/2026,-60.00,INVENTED FUEL,820.00
24/04/2026,1000.00,INVENTED EMPLOYER,1820.00
""",
    "paypal": '''"Date","Time","Name","Type","Status","Currency","Gross","Fee","Net"
"04/02/2026","09:14:00","Invented Cafe","Payment","Completed","CAD","-60.00","0.00","-60.00"
"04/09/2026","10:02:00","Invented Grocer","Payment","Completed","CAD","-60.00","0.00","-60.00"
"04/16/2026","11:30:00","Invented Fuel","Payment","Completed","CAD","-60.00","0.00","-60.00"
"04/24/2026","08:00:00","Invented Employer","Payment","Completed","CAD","1000.00","0.00","1000.00"
''',
    "wise": """TransferWise ID,Date,Amount,Currency,Description,Running Balance
CARD-1,02-04-2026,-60.00,CAD,INVENTED CAFE,940.00
CARD-2,09-04-2026,-60.00,CAD,INVENTED GROCER,880.00
CARD-3,16-04-2026,-60.00,CAD,INVENTED FUEL,820.00
DEP-1,24-04-2026,1000.00,CAD,INVENTED EMPLOYER,1820.00
""",
    "mint": '''"Date","Description","Amount","Transaction Type","Category"
"4/2/2026","Invented Cafe","60.00","debit","Restaurants"
"4/9/2026","Invented Grocer","60.00","debit","Groceries"
"4/16/2026","Invented Fuel","60.00","debit","Gas & Fuel"
"4/24/2026","Invented Employer","1000.00","credit","Paycheck"
''',
    "french_headings": """Date de transaction,Libellé,Montant,Devise
2026-04-02,INVENTED CAFE,-60.00,CAD
2026-04-09,INVENTED GROCER,-60.00,CAD
2026-04-16,INVENTED FUEL,-60.00,CAD
2026-04-24,INVENTED EMPLOYER,1000.00,CAD
""",
    "german_headings": """Buchungstag;Verwendungszweck;Betrag
02.04.2026;INVENTED CAFE;-60.00
09.04.2026;INVENTED GROCER;-60.00
16.04.2026;INVENTED FUEL;-60.00
24.04.2026;INVENTED EMPLOYER;1000.00
""",
}


@pytest.mark.parametrize("label", sorted(WORLD))
def test_real_world_layouts_import_correctly(tmp_path, label):
    result = parse_csv(_write(tmp_path, f"{label}.csv", WORLD[label]))
    _assert_correct(result, label)


# ── punctuation, encodings and file structure ───────────────────────────

PLAIN = """Date,Description,Amount
2026-04-02,INVENTED CAFE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
"""

SHAPES = {
    "accounting_parentheses": """Date,Description,Amount
2026-04-02,INVENTED CAFE,(60.00)
2026-04-09,INVENTED GROCER,(60.00)
2026-04-16,INVENTED FUEL,(60.00)
2026-04-24,INVENTED EMPLOYER,1000.00
""",
    "currency_symbols": '''Date,Description,Amount
2026-04-02,INVENTED CAFE,"-$60.00"
2026-04-09,INVENTED GROCER,"-£60.00"
2026-04-16,INVENTED FUEL,"-€60.00"
2026-04-24,INVENTED EMPLOYER,"$1,000.00"
''',
    "dr_cr_suffix": """Date,Description,Amount
2026-04-02,INVENTED CAFE,60.00 DR
2026-04-09,INVENTED GROCER,60.00 DR
2026-04-16,INVENTED FUEL,60.00 DR
2026-04-24,INVENTED EMPLOYER,1000.00 CR
""",
    "month_name_dates": '''Date,Description,Amount
"Apr 2, 2026",INVENTED CAFE,-60.00
"Apr 9, 2026",INVENTED GROCER,-60.00
"Apr 16, 2026",INVENTED FUEL,-60.00
"Apr 24, 2026",INVENTED EMPLOYER,1000.00
''',
    "iso_with_timezone": """Date,Description,Amount
2026-04-02T09:14:00-04:00,INVENTED CAFE,-60.00
2026-04-09T10:02:00-04:00,INVENTED GROCER,-60.00
2026-04-16T11:30:00-04:00,INVENTED FUEL,-60.00
2026-04-24T08:00:00-04:00,INVENTED EMPLOYER,1000.00
""",
    "dotted_day_first": """Date,Description,Amount
02.04.2026,INVENTED CAFE,-60.00
09.04.2026,INVENTED GROCER,-60.00
16.04.2026,INVENTED FUEL,-60.00
24.04.2026,INVENTED EMPLOYER,1000.00
""",
    "trailing_delimiter": """Date,Description,Amount,
2026-04-02,INVENTED CAFE,-60.00,
2026-04-09,INVENTED GROCER,-60.00,
2026-04-16,INVENTED FUEL,-60.00,
2026-04-24,INVENTED EMPLOYER,1000.00,
""",
    "padded_headings": """ Date , Description , Amount
2026-04-02,INVENTED CAFE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
""",
    "newline_inside_quotes": '''Date,Description,Amount
2026-04-02,"INVENTED CAFE
STORE 12",-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
''',
    "date_like_text_in_memo": """Date,Description,Amount
2026-04-02,PAYMENT FOR 01/02/2019 INVOICE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
""",
}


@pytest.mark.parametrize("label", sorted(SHAPES))
def test_punctuation_and_structure(tmp_path, label):
    result = parse_csv(_write(tmp_path, f"{label}.csv", SHAPES[label]))
    _assert_correct(result, label)


@pytest.mark.parametrize("label,kwargs", [
    ("utf8_bom", {"encoding": "utf-8-sig"}),
    ("utf16_excel", {"encoding": "utf-16"}),
    ("lf_only", {"newline": "\n"}),
    ("cr_only", {"newline": "\r"}),
])
def test_encodings_and_line_endings(tmp_path, label, kwargs):
    result = parse_csv(_write(tmp_path, f"{label}.csv", PLAIN, **kwargs))
    _assert_correct(result, label)


@pytest.mark.parametrize("label,separator", [
    ("semicolon", ";"), ("tab", "\t"), ("pipe", "|"),
])
def test_separators(tmp_path, label, separator):
    text = PLAIN.replace(",", separator)
    result = parse_csv(_write(tmp_path, f"{label}.csv", text))
    _assert_correct(result, label)


# ── the shapes that must be refused, because guessing corrupts money ────

def test_a_row_with_extra_values_is_refused(tmp_path):
    """An unquoted separator inside a description shifts every value after it.

    This exact file used to import, recording a $1,000.00 paycheque as $1.00.
    """
    text = """Date,Description,Amount
2026-04-02,INVENTED CAFE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,$1,000.00
"""
    result = parse_csv(_write(tmp_path, "extra.csv", text))

    assert result["blocked"] is True
    assert not result["transactions"]
    assert any("more values" in p for p in result["receipt"]["problems"])
    assert any("line 5" in p for p in result["receipt"]["problems"])


def test_a_row_that_stops_before_the_amount_is_refused(tmp_path):
    """A truncated row used to import as $0.00, quietly losing the spending."""
    text = """Date,Description,Amount
2026-04-02,INVENTED CAFE,-60.00
2026-04-09,INVENTED GROCER
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, "short.csv", text))

    assert result["blocked"] is True
    assert not result["transactions"]
    assert any("stop before" in p for p in result["receipt"]["problems"])


def test_missing_optional_trailing_columns_are_still_fine(tmp_path):
    """Only a missing date or amount is fatal; a dropped note column is not."""
    text = """Date,Description,Amount,Category,Notes
2026-04-02,INVENTED CAFE,-60.00,Food
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00,Transport,checked
2026-04-24,INVENTED EMPLOYER,1000.00,Income,payday
"""
    result = parse_csv(_write(tmp_path, "optional.csv", text))
    _assert_correct(result, "optional trailing columns")


def test_an_unreadable_amount_refuses_instead_of_raising(tmp_path):
    """A cell that is not a number at all used to surface as "File read error"."""
    text = """Date,Description,Amount
2026-04-02,INVENTED CAFE,not a number
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, "unreadable.csv", text))

    assert result["blocked"] is True
    problems = " ".join(result["receipt"]["problems"])
    assert "File read error" not in problems


def test_a_row_dated_next_year_is_refused(tmp_path):
    """A statement cannot describe next year, and the damage is not local.

    Every analysis window is measured back from the newest date in the
    database, so one row with a mis-typed year pushes a person's real spending
    out of view entirely.
    """
    text = """Date,Description,Amount
2026-04-02,INVENTED CAFE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2099-04-24,INVENTED TYPO,-60.00
"""
    result = parse_csv(_write(tmp_path, "future.csv", text))

    assert result["blocked"] is True
    problems = " ".join(result["receipt"]["problems"])
    assert "more than a year from now" in problems
    assert "2099" in problems


def test_ordinary_recent_dates_are_not_refused(tmp_path):
    """The guard must not reject a normal statement."""
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=20)).isoformat()
    text = f"""Date,Description,Amount
{soon},INVENTED POSTDATED CHEQUE,-60.00
2026-04-09,INVENTED GROCER,-60.00
2026-04-16,INVENTED FUEL,-60.00
2026-04-24,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, "soon.csv", text))

    assert not result.get("blocked")
    assert len(result["transactions"]) == 4

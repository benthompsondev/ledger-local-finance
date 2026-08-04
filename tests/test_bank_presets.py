"""Real bank export shapes, Canada and the United States.

Every file below matches a bank's published column layout, with invented
account numbers, merchants, dates and amounts. Each describes the same four
transactions: three purchases totalling $60.00 out, and $1,000.00 in.

The sign convention is the reason this file exists. Discover and Amex write a
purchase as a positive number while Chase writes the same purchase as
negative, and nothing inside either file says which is which. Reading a
Discover export literally turns every purchase into income.
"""
import pytest

from parsers.csv_import import parse_csv

EXPECTED_OUT = -60.0
EXPECTED_IN = 1000.0

BANKS = {
    # ── Canada ──────────────────────────────────────────────────────────
    "tangerine": """Date,Transaction,Name,Memo,Amount
04/02/2026,DEBIT,INVENTED COFFEE CO,Point of sale,-10.00
04/05/2026,DEBIT,INVENTED GROCER,Point of sale,-20.00
04/09/2026,DEBIT,INVENTED FUEL,Point of sale,-30.00
04/15/2026,CREDIT,INVENTED EMPLOYER,Payroll,1000.00
""",
    "rbc": '''"Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2","CAD$","USD$"
"Chequing","00000-0000000","4/2/2026","","INVENTED COFFEE CO","STORE 12",-10.00,
"Chequing","00000-0000000","4/5/2026","","INVENTED GROCER","",-20.00,
"Chequing","00000-0000000","4/9/2026","","INVENTED FUEL","PUMP 3",-30.00,
"Chequing","00000-0000000","4/15/2026","","PAYROLL DEP","INVENTED EMPLOYER",1000.00,
''',
    "bmo": """First Bank Card,Transaction Type,Date Posted,Transaction Amount,Description
'0000',DEBIT,20260402,-10.00,INVENTED COFFEE CO
'0000',DEBIT,20260405,-20.00,INVENTED GROCER
'0000',DEBIT,20260409,-30.00,INVENTED FUEL
'0000',CREDIT,20260415,1000.00,INVENTED EMPLOYER
""",
    "cibc": """2026-04-02,INVENTED COFFEE CO,10.00,
2026-04-05,INVENTED GROCER,20.00,
2026-04-09,INVENTED FUEL,30.00,
2026-04-15,INVENTED EMPLOYER,,1000.00
""",
    "td": """2026-04-02,INVENTED COFFEE CO,10.00,,4990.00
2026-04-05,INVENTED GROCER,20.00,,4970.00
2026-04-09,INVENTED FUEL,30.00,,4940.00
2026-04-15,INVENTED EMPLOYER,,1000.00,5940.00
""",
    "scotiabank": """2026-04-02,-10.00,INVENTED COFFEE CO
2026-04-05,-20.00,INVENTED GROCER
2026-04-09,-30.00,INVENTED FUEL
2026-04-15,1000.00,INVENTED EMPLOYER
""",
    # ── United States ───────────────────────────────────────────────────
    "chase": """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,04/02/2026,INVENTED COFFEE CO,-10.00,DEBIT_CARD,4990.00,
DEBIT,04/05/2026,INVENTED GROCER,-20.00,DEBIT_CARD,4970.00,
DEBIT,04/09/2026,INVENTED FUEL,-30.00,DEBIT_CARD,4940.00,
CREDIT,04/15/2026,INVENTED EMPLOYER,1000.00,ACH_CREDIT,5940.00,
""",
    "bofa": """Description,,Summary Amt.
Beginning balance as of 04/01/2026,,5000.00

Date,Description,Amount,Running Bal.
04/02/2026,INVENTED COFFEE CO,-10.00,4990.00
04/05/2026,INVENTED GROCER,-20.00,4970.00
04/09/2026,INVENTED FUEL,-30.00,4940.00
04/15/2026,INVENTED EMPLOYER,1000.00,5940.00
""",
    "citi": """Status,Date,Description,Debit,Credit
Cleared,04/02/2026,INVENTED COFFEE CO,10.00,
Cleared,04/05/2026,INVENTED GROCER,20.00,
Cleared,04/09/2026,INVENTED FUEL,30.00,
Cleared,04/15/2026,INVENTED EMPLOYER,,1000.00
""",
    "capitalone": """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-04-02,2026-04-03,0000,INVENTED COFFEE CO,Dining,10.00,
2026-04-05,2026-04-06,0000,INVENTED GROCER,Merchandise,20.00,
2026-04-09,2026-04-10,0000,INVENTED FUEL,Gas,30.00,
2026-04-15,2026-04-16,0000,INVENTED EMPLOYER,Payment,,1000.00
""",
    # Discover writes purchases POSITIVE. Read literally this file says the
    # user earned $60 and spent $1,000.
    "discover": """Trans. Date,Post Date,Description,Amount,Category
04/02/2026,04/03/2026,INVENTED COFFEE CO,10.00,Restaurants
04/05/2026,04/06/2026,INVENTED GROCER,20.00,Supermarkets
04/09/2026,04/10/2026,INVENTED FUEL,30.00,Gasoline
04/15/2026,04/16/2026,INVENTED EMPLOYER,-1000.00,Payments
""",
    # Amex the same way round.
    "amex": """Date,Description,Card Member,Account #,Amount
04/02/2026,INVENTED COFFEE CO,INVENTED CARDHOLDER,-00000,10.00
04/05/2026,INVENTED GROCER,INVENTED CARDHOLDER,-00000,20.00
04/09/2026,INVENTED FUEL,INVENTED CARDHOLDER,-00000,30.00
04/15/2026,INVENTED EMPLOYER,INVENTED CARDHOLDER,-00000,-1000.00
""",
    "wellsfargo": """04/02/2026,-10.00,*,,INVENTED COFFEE CO
04/05/2026,-20.00,*,,INVENTED GROCER
04/09/2026,-30.00,*,,INVENTED FUEL
04/15/2026,1000.00,*,,INVENTED EMPLOYER
""",
}


def _write(tmp_path, name, text):
    path = tmp_path / f"{name}.csv"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("bank", sorted(BANKS))
def test_a_real_export_imports_correctly(tmp_path, bank):
    result = parse_csv(_write(tmp_path, bank, BANKS[bank]),
                       account_type="chequing")

    assert not result["blocked"], (
        f"{bank} was blocked: {result['receipt']['problems']}"
    )
    txs = result["transactions"]
    assert len(txs) == 4, f"{bank}: expected 4 rows, got {len(txs)}"

    out = round(sum(t["amount"] for t in txs if t["amount"] < 0), 2)
    inflow = round(sum(t["amount"] for t in txs if t["amount"] > 0), 2)
    assert out == pytest.approx(EXPECTED_OUT), (
        f"{bank}: money out is {out}, expected {EXPECTED_OUT}"
    )
    assert inflow == pytest.approx(EXPECTED_IN), (
        f"{bank}: money in is {inflow}, expected {EXPECTED_IN}"
    )
    assert {t["transaction_date"][:7] for t in txs} == {"2026-04"}


@pytest.mark.parametrize("bank", sorted(BANKS))
def test_the_receipt_names_the_bank(tmp_path, bank):
    """The user should be told which layout was assumed, not just trusted."""
    result = parse_csv(_write(tmp_path, bank, BANKS[bank]),
                       account_type="chequing")
    assert result["receipt"]["bank"], f"{bank} was not identified"


def test_discover_purchases_are_not_read_as_income(tmp_path):
    """The specific disaster this file exists to prevent."""
    result = parse_csv(_write(tmp_path, "discover", BANKS["discover"]),
                       account_type="mastercard")
    coffee = next(t for t in result["transactions"]
                  if "COFFEE" in t["raw_description"])
    assert coffee["amount"] < 0, "a Discover purchase must be money out"
    assert coffee["direction"] == "debit"


def test_rbc_reads_the_cad_column_not_the_empty_usd_one(tmp_path):
    result = parse_csv(_write(tmp_path, "rbc", BANKS["rbc"]),
                       account_type="chequing")
    assert result["receipt"]["zero_value_rows"] == 0


def test_an_unknown_bank_still_falls_back_to_detection(tmp_path):
    """Presets are a shortcut, never a requirement."""
    text = """Date,Description,Amount
2026-04-02,INVENTED COFFEE CO,-10.00
2026-04-05,INVENTED GROCER,-20.00
2026-04-09,INVENTED FUEL,-30.00
2026-04-15,INVENTED EMPLOYER,1000.00
"""
    result = parse_csv(_write(tmp_path, "unknown", text),
                       account_type="chequing")
    assert not result["blocked"]
    assert len(result["transactions"]) == 4


def test_choosing_the_bank_by_hand_overrides_detection(tmp_path):
    """Someone whose export is not recognised can name their bank."""
    result = parse_csv(_write(tmp_path, "amex", BANKS["amex"]),
                       account_type="mastercard", bank="amex")
    out = round(sum(t["amount"] for t in result["transactions"]
                    if t["amount"] < 0), 2)
    assert out == pytest.approx(EXPECTED_OUT)

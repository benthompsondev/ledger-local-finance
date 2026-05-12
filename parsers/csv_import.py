"""
Generic CSV import parser.
Handles flexible column mappings for any bank export CSV.
Supported date formats: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, DD-Mon-YYYY
"""
import csv
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

from utils.categorizer import enrich_transaction

DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%m-%d-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
]

COMMON_COLUMN_MAPS = {
    # date column aliases
    "date": "transaction_date",
    "transaction date": "transaction_date",
    "trans date": "transaction_date",
    "trans. date": "transaction_date",
    "posting date": "posted_date",
    "posted date": "posted_date",
    "value date": "transaction_date",
    # description aliases
    "description": "raw_description",
    "memo": "raw_description",
    "narrative": "raw_description",
    "transaction description": "raw_description",
    "details": "raw_description",
    "payee": "raw_description",
    # amount aliases
    "amount": "amount",
    "debit": "debit",
    "credit": "credit",
    "withdrawal": "debit",
    "deposit": "credit",
    "transaction amount": "amount",
    # category
    "category": "category",
    "type": "tx_type",
}


def try_parse_date(s: str) -> Optional[str]:
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_column(col: str) -> str:
    return col.strip().lower().replace("_", " ")


def detect_columns(header: list[str]) -> dict:
    """Map CSV header columns to canonical field names."""
    mapping = {}
    for raw in header:
        norm = normalize_column(raw)
        if norm in COMMON_COLUMN_MAPS:
            mapping[raw] = COMMON_COLUMN_MAPS[norm]
        else:
            mapping[raw] = norm.replace(" ", "_")
    return mapping


def parse_amount_cell(value: str) -> float:
    """Parse a cell that may be $1,234.56 or (1,234.56) for negative."""
    v = value.strip()
    negative = False
    if v.startswith("(") and v.endswith(")"):
        negative = True
        v = v[1:-1]
    v = v.replace("$", "").replace(",", "").replace(" ", "")
    try:
        result = float(v)
        return -result if negative else result
    except ValueError:
        return 0.0


def parse_csv(
    filepath: str | Path,
    account_type: str = "csv",
    account_id: Optional[str] = None,
    statement_period: Optional[str] = None,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> dict:
    """
    Parse a generic bank export CSV.
    Returns {transactions, errors, stats, statement_period, source_file}
    """
    filepath = Path(filepath)
    transactions = []
    errors = []

    try:
        with open(filepath, newline="", encoding=encoding, errors="replace") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if not reader.fieldnames:
                return {"transactions": [], "errors": ["Empty or unreadable CSV"], "stats": {}}

            col_map = detect_columns(list(reader.fieldnames))

            for row_num, row in enumerate(reader, start=2):
                try:
                    mapped = {col_map.get(k, k): v for k, v in row.items()}

                    # Date
                    tx_date = None
                    for field in ["transaction_date", "date", "trans_date"]:
                        raw_date = mapped.get(field, "").strip()
                        if raw_date:
                            tx_date = try_parse_date(raw_date)
                            break

                    if not tx_date:
                        errors.append(f"Row {row_num}: unparseable date")
                        continue

                    posted_date = try_parse_date(mapped.get("posted_date", "").strip()) if mapped.get("posted_date") else None

                    # Description
                    desc = mapped.get("raw_description", "").strip()
                    if not desc:
                        for alt in ["payee", "memo", "details", "narrative"]:
                            desc = mapped.get(alt, "").strip()
                            if desc:
                                break
                    if not desc:
                        desc = "Unknown"

                    # Amount — handle split debit/credit columns OR single amount column
                    amount = 0.0
                    if "amount" in mapped and mapped["amount"].strip():
                        amount = parse_amount_cell(mapped["amount"])
                    elif "debit" in mapped or "credit" in mapped:
                        debit_val = parse_amount_cell(mapped.get("debit", "0") or "0")
                        credit_val = parse_amount_cell(mapped.get("credit", "0") or "0")
                        # debit = positive expense, credit = positive income
                        if debit_val != 0:
                            amount = abs(debit_val)
                        elif credit_val != 0:
                            amount = -abs(credit_val)  # negative = credit/income

                    if amount == 0.0 and not desc:
                        continue  # empty row

                    tx = {
                        "account_type": account_type,
                        "account_id": account_id,
                        "transaction_date": tx_date,
                        "posted_date": posted_date,
                        "raw_description": desc,
                        "amount": amount,
                        "currency": mapped.get("currency", "CAD").strip() or "CAD",
                        "statement_period": statement_period or filepath.stem,
                    }

                    # Pre-assign category if CSV has one
                    if mapped.get("category", "").strip():
                        tx["category"] = mapped["category"].strip()

                    enrich_transaction(tx)
                    transactions.append(tx)

                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

    except Exception as e:
        errors.append(f"File read error: {e}")

    stats = {
        "total_parsed": len(transactions),
        "debits": sum(1 for t in transactions if t.get("direction") == "debit"),
        "credits": sum(1 for t in transactions if t.get("direction") == "credit"),
        "errors": len(errors),
    }

    return {
        "transactions": transactions,
        "errors": errors,
        "stats": stats,
        "statement_period": statement_period or filepath.stem,
        "source_file": filepath.name,
    }

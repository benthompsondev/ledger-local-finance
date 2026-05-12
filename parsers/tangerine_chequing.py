"""
Tangerine Chequing statement PDF parser.

Real format (verified from actual PDFs):

Normal transaction (all on ONE line):
  02 Jan 2026 Tangerine Credit Card Payment 2,500.00 4,501.59
  02 Jan 2026 INTERAC e-Transfer From: TORIRIVARD 490.00 4,991.59

Multi-line savings deposit (3 lines total):
  Internet Deposit from Tangerine Savings Account -   ← description (no date)
  02 Jan 2026 50.00 5,041.59                           ← date + amount + balance
  3031472835                                           ← orphan account number (discard)

Pattern rules:
  - Standard:  line starts with "DD Mon YYYY", rest = description + amount + balance
  - Multi-line: a non-date line contains "Internet Deposit from Tangerine Savings",
    then the NEXT line contains "DD Mon YYYY" + amount + balance
  - Opening Balance / Closing Balance: skip
  - Header rows: skip
  - Amount = second-to-last number on line; Balance = last number
  - Direction from description keywords
"""
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber is required: pip install pdfplumber")

from utils.categorizer import enrich_transaction

DATE_RE = re.compile(
    r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s+(.*)",
    re.IGNORECASE,
)
ORPHAN_ACCT_RE = re.compile(r"^\d{10}$")
NUMBER_RE = re.compile(r"[\d,]+\.\d{2}")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10,
    "nov": 11, "dec": 12,
}

SKIP_PHRASES = [
    "OPENING BALANCE", "CLOSING BALANCE",
    "TRANSACTION DATE", "CURRENT INTEREST RATE",
    "TANGERINE CHEQUING", "ACCOUNT TYPE",
    "PAGE ", "WWW.TANGERINE", "NOBODY LIKES",
    "UP TO $100", "DEPOSIT EACH CHEQUE",
    "ACCOUNTS AT A GLANCE", "ACCOUNT REGISTRATION",
    "THE DETAILS", "INTEREST EARNED",
]

DEPOSIT_KEYWORDS = [
    "EFT DEPOSIT",
    "INTERAC E-TRANSFER FROM:",
    "INTEREST PAID",
    "INTERNET DEPOSIT FROM",
]

WITHDRAWAL_KEYWORDS = [
    "EFT WITHDRAWAL",
    "INTERAC E-TRANSFER TO:",
    "NSF FEE",
    "SERVICE CHARGE",
]

PAYMENT_KEYWORDS = ["TANGERINE CREDIT CARD PAYMENT"]
TRANSFER_KEYWORDS = ["INTERNET DEPOSIT FROM TANGERINE SAVINGS"]
CANCELLED_PREFIX = "CANCELLED"


def parse_date(day: str, mon: str, year: str) -> str:
    m = MONTH_MAP[mon.lower()]
    return datetime(int(year), m, int(day)).date().isoformat()


def should_skip(line: str) -> bool:
    u = line.upper()
    for phrase in SKIP_PHRASES:
        if phrase in u:
            return True
    return False


def determine_direction_chequing(desc: str) -> str:
    u = desc.upper()
    if u.startswith(CANCELLED_PREFIX):
        return "cancelled"
    for k in PAYMENT_KEYWORDS:
        if k in u:
            return "payment"
    for k in TRANSFER_KEYWORDS:
        if k in u:
            return "transfer"
    for k in DEPOSIT_KEYWORDS:
        if k in u:
            return "credit"
    for k in WITHDRAWAL_KEYWORDS:
        if k in u:
            return "debit"
    return "debit"


def parse_line(line: str) -> Optional[dict]:
    """
    Parse a single-line transaction.
    Format: DD Mon YYYY <description> amount balance
    Returns dict or None.
    """
    m = DATE_RE.match(line)
    if not m:
        return None

    tx_date = parse_date(m.group(1), m.group(2), m.group(3))
    rest = m.group(4).strip()

    if not rest:
        return None

    # Skip balance rows
    for phrase in ["OPENING BALANCE", "CLOSING BALANCE"]:
        if phrase in rest.upper():
            return None

    # Extract all numbers from rest
    numbers = NUMBER_RE.findall(rest)
    if len(numbers) < 2:
        return None

    amount_str  = numbers[-2].replace(",", "")
    # balance_str = numbers[-1].replace(",", "")  # unused

    try:
        amount = float(amount_str)
    except ValueError:
        return None

    if amount == 0.0:
        return None

    # Description = rest with last two numbers removed
    desc = rest
    for num in reversed(numbers[-2:]):
        desc = desc.rsplit(num, 1)[0].strip()
    # Clean trailing punctuation
    desc = desc.strip().rstrip("-").strip()

    if not desc:
        return None

    return {"tx_date": tx_date, "description": desc, "amount": amount}


def build_transaction(tx_date: str, description: str, amount: float, statement_period: Optional[str]) -> Optional[dict]:
    """Build and enrich a transaction dict."""
    direction = determine_direction_chequing(description)
    is_tr = 1 if direction in ("payment", "transfer", "cancelled") else 0

    # Savings pullback is also a transfer
    if "INTERNET DEPOSIT FROM TANGERINE SAVINGS" in description.upper():
        is_tr = 1
        direction = "transfer"

    tx = {
        "account_type": "chequing",
        "transaction_date": tx_date,
        "posted_date": None,
        "raw_description": description,
        "amount": amount,
        "currency": "CAD",
        "direction": direction,
        "is_transfer": is_tr,
        "statement_period": statement_period,
    }
    enrich_transaction(tx)
    return tx


def parse_pdf(filepath: str | Path, statement_period: Optional[str] = None) -> dict:
    """
    Parse a Tangerine Chequing PDF.
    Returns {transactions, errors, stats, statement_period, source_file}
    """
    filepath = Path(filepath)
    transactions = []
    errors = []

    with pdfplumber.open(filepath) as pdf:
        all_lines = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            all_lines.extend(text.splitlines())

    i = 0
    pending_description = None  # for multi-line savings deposit

    while i < len(all_lines):
        line = all_lines[i].strip()
        i += 1

        if not line:
            continue

        # Orphan account number — always discard
        if ORPHAN_ACCT_RE.match(line):
            continue

        if should_skip(line):
            continue

        # ── Multi-line savings deposit ─────────────────────────────────
        # The description comes BEFORE the date line
        # Detect: non-date line that is a known multi-line pattern
        if not DATE_RE.match(line):
            # Could be a pending description for the NEXT date line
            # Only treat as pending if it looks like a real description (not footer text)
            if ("INTERNET DEPOSIT FROM TANGERINE SAVINGS" in line.upper() or
                    "INTERNET DEPOSIT" in line.upper()):
                # Strip the trailing dash if present
                pending_description = line.rstrip("-").strip()
            # Ignore other non-date lines (footers, headers already caught above)
            continue

        # ── Standard transaction line ──────────────────────────────────
        parsed = parse_line(line)

        if parsed is None:
            # May be a date line with no inline description when we have a pending one.
            # e.g. "02 Jan 2026 50.00 5,041.59" — description came from prior line.
            if pending_description:
                m_date = DATE_RE.match(line)
                if m_date:
                    rest = m_date.group(4).strip()
                    numbers = NUMBER_RE.findall(rest)
                    if len(numbers) >= 2:
                        tx_date = parse_date(m_date.group(1), m_date.group(2), m_date.group(3))
                        amount = float(numbers[-2].replace(",", ""))
                        if amount > 0:
                            description = pending_description
                            pending_description = None
                            tx = build_transaction(tx_date, description, amount, statement_period)
                            if tx:
                                transactions.append(tx)
            # Skip (Opening/Closing Balance or unrecognized)
            continue

        tx_date = parsed["tx_date"]
        amount = parsed["amount"]

        # If we have a pending description from the previous line, use it
        if pending_description:
            description = pending_description
            pending_description = None
        else:
            description = parsed["description"]

        tx = build_transaction(tx_date, description, amount, statement_period)
        if tx:
            transactions.append(tx)

    stats = {
        "total_parsed": len(transactions),
        "credits":   sum(1 for t in transactions if t["direction"] == "credit"),
        "debits":    sum(1 for t in transactions if t["direction"] == "debit"),
        "transfers": sum(1 for t in transactions if t.get("is_transfer")),
        "cancelled": sum(1 for t in transactions if t["direction"] == "cancelled"),
        "flagged":   sum(1 for t in transactions if t.get("is_flagged")),
        "errors":    len(errors),
    }

    return {
        "transactions": transactions,
        "errors": errors,
        "stats": stats,
        "statement_period": statement_period or filepath.stem,
        "source_file": filepath.name,
    }

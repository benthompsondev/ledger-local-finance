"""
Tangerine Savings Account statement PDF parser.

Real format (verified from actual PDFs, Feb + Mar 2026):

Statement header (page 1):
  Statement
  www.tangerine.ca
  February 01, 2026 To February 28, 2026
  ...
  The Details - Tangerine Savings Account - 3031472835

Transaction rows come in two forms:

  1. Single-line (date + description + amount + balance on one line):
     07 Feb 2026 Credit Card Rewards Redemption 18.01 119.34
     10 Feb 2026 EFT Deposit from MANULIFE 178.00 297.34
     28 Feb 2026 Interest Paid 0.03 227.37

  2. Multi-line withdrawal (description split across two lines):
     Internet Withdrawal to Tangerine Chequing Account   ← description (no date)
     13 Feb 2026 200.00 97.34                             ← date + amount + balance
     - 4010461272                                         ← orphan account number

Parsing rules:
  - Only parse "The Details - Tangerine Savings Account" section
  - Stop at "The Details - Tangerine TFSA" or any other account section
  - Opening Balance / Closing Balance → skip
  - Orphan lines matching "- NNNNNNNNNN" (dash + 10 digits) → discard
  - Direction from description keywords (see below)

Direction logic:
  DEPOSIT keywords     → direction='credit'   (real inflows)
  WITHDRAWAL keywords  → direction='transfer' (internal, savings→chequing)
  Interest Paid        → direction='credit'
  Opening/Closing bal  → skip

Double-counting prevention:
  - "Internet Withdrawal to Tangerine Chequing Account" sets direction='transfer'
    and is_transfer=1 — it is excluded from cashflow by the v8 model
  - The matching "Internet Deposit from Tangerine Savings" on the CHEQUING side
    is already direction='transfer' — no additional action needed
  - MANULIFE, rewards, interest → direction='credit', counted as real income
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

# ── Regexes ──────────────────────────────────────────────────────────────────
DATE_RE = re.compile(
    r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s+(.*)",
    re.IGNORECASE,
)
# Orphan account number lines: "- 3031472835" or "3031472835" (with or without dash)
ORPHAN_ACCT_RE = re.compile(r"^-?\s*\d{10}$")
NUMBER_RE = re.compile(r"[\d,]+\.\d{2}")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10,
    "nov": 11, "dec": 12,
}

# ── Skip phrases ─────────────────────────────────────────────────────────────
SKIP_PHRASES = [
    "OPENING BALANCE", "CLOSING BALANCE",
    "TRANSACTION DATE", "TRANSACTION DESCRIPTION",
    "CURRENT INTEREST RATE", "INTEREST EARNED YEAR TO DATE",
    "ACCOUNT(S) AT A GLANCE", "ACCOUNT REGISTRATION",
    "THE DETAILS - TANGERINE TFSA",         # stop section marker
    "THE DETAILS - TANGERINE SAVINGS",       # section header itself (not data)
    "TANGERINE SAVINGS ACCOUNT",             # the account glance row
    "TANGERINE TFSA",                        # TFSA glance row
    "PAGE ", "WWW.TANGERINE",
    "NOBODY LIKES", "PLEASE NOTE",
    "TANGERINE BANK IS", "FORWARD BANKING",
    "TANGERINE IS A REGISTERED",
    "THE CASHABLE RATE",
    "ACCOUNT TYPE", "ACCOUNT NUMBER", "ACCOUNT BALANCE",
    "CLIENT #:", "YOUR ORANGE KEY",
    "STATEMENT",
]

# ── Direction keywords ────────────────────────────────────────────────────────
DEPOSIT_KEYWORDS = [
    "EFT DEPOSIT",
    "CREDIT CARD REWARDS REDEMPTION",
    "INTEREST PAID",
    "INTEREST EARNED",
    "DEPOSIT FROM",
]
WITHDRAWAL_KEYWORDS = [
    "INTERNET WITHDRAWAL TO TANGERINE CHEQUING",
    "INTERNET WITHDRAWAL TO",
    "INTERNET TRANSFER TO",
    "WITHDRAWAL TO",
]


def parse_date(day: str, mon: str, year: str) -> str:
    m = MONTH_MAP[mon.lower()]
    return datetime(int(year), m, int(day)).date().isoformat()


def should_skip(line: str) -> bool:
    u = line.upper()
    for phrase in SKIP_PHRASES:
        if phrase in u:
            return True
    return False


def is_section_end(line: str) -> bool:
    """Returns True when we've crossed into another account's section."""
    u = line.upper()
    return "THE DETAILS - TANGERINE TFSA" in u or (
        "THE DETAILS -" in u and "SAVINGS ACCOUNT" not in u
    )


def determine_direction_savings(desc: str) -> str:
    """
    Returns direction string for a savings transaction.
    Internal withdrawals to chequing = 'transfer' (excluded from cashflow).
    Real deposits = 'credit' (counted as income).
    """
    u = desc.upper()
    for k in WITHDRAWAL_KEYWORDS:
        if k in u:
            return "transfer"
    for k in DEPOSIT_KEYWORDS:
        if k in u:
            return "credit"
    # Fallback: positive amounts in savings are credits
    return "credit"


def parse_line(line: str) -> Optional[dict]:
    """
    Parse a standard single-line savings transaction.
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

    for phrase in ["OPENING BALANCE", "CLOSING BALANCE"]:
        if phrase in rest.upper():
            return None

    numbers = NUMBER_RE.findall(rest)
    if len(numbers) < 2:
        return None

    amount_str = numbers[-2].replace(",", "")
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    if amount == 0.0:
        return None

    # Description = rest with last two numbers stripped
    desc = rest
    for num in reversed(numbers[-2:]):
        desc = desc.rsplit(num, 1)[0].strip()
    desc = desc.strip().rstrip("-").strip()

    if not desc:
        return None

    return {"tx_date": tx_date, "description": desc, "amount": amount}


def build_transaction(
    tx_date: str,
    description: str,
    amount: float,
    statement_period: Optional[str],
) -> Optional[dict]:
    """Build and enrich a savings transaction dict."""
    direction = determine_direction_savings(description)
    is_tr = 1 if direction == "transfer" else 0

    tx = {
        "account_type":       "savings",
        "transaction_date":   tx_date,
        "posted_date":        None,
        "raw_description":    description,
        "amount":             amount,
        "currency":           "CAD",
        "direction":          direction,
        "is_transfer":        is_tr,
        "statement_period":   statement_period,
    }
    enrich_transaction(tx)
    return tx


def parse_pdf(filepath: str | Path, statement_period: Optional[str] = None) -> dict:
    """
    Parse a Tangerine Savings Account PDF.
    Returns {transactions, errors, stats, statement_period, source_file}

    Only processes the "Tangerine Savings Account" section.
    Ignores TFSA and other sub-account sections.
    """
    filepath = Path(filepath)
    transactions = []
    errors = []

    with pdfplumber.open(filepath) as pdf:
        all_lines = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            all_lines.extend(text.splitlines())

    # ── Auto-detect statement period from header ──────────────────────────
    if not statement_period:
        for line in all_lines[:20]:
            # "February 01, 2026 To February 28, 2026" or "March 01, 2026 To March 31, 2026"
            m = re.search(
                r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                r"\s+\d{1,2},\s+(\d{4})",
                line, re.IGNORECASE
            )
            if m:
                month_name = m.group(1)[:3].lower()
                year = m.group(2)
                month_num = MONTH_MAP.get(month_name, 1)
                statement_period = f"{year}-{month_num:02d}"
                break

    # ── Find savings section and parse it ────────────────────────────────
    in_savings_section = False
    i = 0
    pending_description = None  # for multi-line withdrawal

    while i < len(all_lines):
        line = all_lines[i].strip()
        i += 1

        if not line:
            continue

        # Detect savings section start
        if "THE DETAILS - TANGERINE SAVINGS ACCOUNT" in line.upper():
            in_savings_section = True
            continue

        # Stop at TFSA or other account section
        if in_savings_section and is_section_end(line):
            break

        if not in_savings_section:
            continue

        # Discard orphan account number lines ("- 3031472835")
        if ORPHAN_ACCT_RE.match(line):
            continue

        if should_skip(line):
            continue

        # ── Multi-line withdrawal: description comes before the date line ──
        if not DATE_RE.match(line):
            u = line.upper()
            is_pending_desc = any(k in u for k in [
                "INTERNET WITHDRAWAL TO",
                "INTERNET TRANSFER TO",
            ])
            if is_pending_desc:
                pending_description = line.rstrip("-").strip()
            # Non-date lines that aren't known patterns are discarded
            continue

        # ── Standard date line ────────────────────────────────────────────
        parsed = parse_line(line)

        if parsed is None:
            # Could be a date line with only numbers when description was pending
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
            continue

        tx_date = parsed["tx_date"]
        amount = parsed["amount"]

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
        "credits":      sum(1 for t in transactions if t["direction"] == "credit"),
        "debits":       sum(1 for t in transactions if t["direction"] == "debit"),
        "transfers":    sum(1 for t in transactions if t.get("is_transfer")),
        "cancelled":    sum(1 for t in transactions if t["direction"] == "cancelled"),
        "flagged":      sum(1 for t in transactions if t.get("is_flagged")),
        "errors":       len(errors),
    }

    return {
        "transactions":     transactions,
        "errors":           errors,
        "stats":            stats,
        "statement_period": statement_period or filepath.stem,
        "source_file":      filepath.name,
    }

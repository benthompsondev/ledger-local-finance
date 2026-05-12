"""
parsers/investments_csv.py — Investment holdings CSV parser (Pass 19).

What this parses
────────────────
Brokerage-export "holdings report" CSVs shaped like the Questrade /
Wealthsimple / Interactive Brokers exports. The user's report has
columns roughly:

    Account Name, Account Type, Account Classification, Account Number,
    Symbol, Exchange, MIC, Name, Security Type, Quantity,
    Position Direction, Market Price, Market Price Currency,
    Book Value (CAD), Book Value Currency (CAD),
    Book Value (Market), Book Value Currency (Market),
    Market Value, Market Value Currency,
    Market Unrealized Returns, Market Unrealized Returns Currency

…plus a final non-data row like:
    "As of 2026-04-30 16:00 ET" (or similar).

What this is NOT
────────────────
• Not a price-lookup tool — market_price/market_value come from the file.
• Not an FX converter — if a portfolio mixes USD and CAD, the snapshot
  is flagged `mixed_currency=True` and totals stay in their original
  currency. The UI tells the user.
• Not transaction-level — this is a point-in-time holdings snapshot.

Returns
───────
parse_holdings_csv(path) → {
    "positions":     [dict, ...],  # column-aligned with investment_positions
    "as_of_date":    "YYYY-MM-DD" or "",
    "row_count":     int,
    "currencies":    [str, ...],
    "mixed_currency": bool,
    "total_market_value_native": float,  # naive sum across currencies
    "errors":        [str, ...],
    "source_file":   str,
}

The caller (the Investments page) shows the preview and decides whether
to commit via insert_investment_snapshot().
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── Column-name aliases ────────────────────────────────────────────────
# Keys are normalized header names (lowercase, single-spaced). Values are
# the canonical investment_positions column name.
_COL_ALIASES: dict[str, str] = {
    # Account
    "account name":           "account_name",
    "account type":           "account_type",
    "account classification": "account_classification",
    "account number":         "account_number",
    "account #":              "account_number",
    # Security identity
    "symbol":                 "ticker",
    "ticker":                 "ticker",
    "exchange":               "exchange",
    "mic":                    "mic",
    "name":                   "security_name",
    "security name":          "security_name",
    "description":            "security_name",
    "security type":          "security_type",
    "asset class":            "security_type",
    # Position
    "quantity":               "quantity",
    "shares":                 "quantity",
    "units":                  "quantity",
    "position direction":     "position_direction",
    # Pricing
    "market price":           "market_price",
    "price":                  "market_price",
    "market price currency":  "market_price_currency",
    # Book value
    "book value (cad)":       "book_value_cad",
    "book value cad":         "book_value_cad",
    "book value currency (cad)": "book_value_cad_currency",
    "book value (market)":    "book_value_market",
    "book value market":      "book_value_market",
    "book value":             "book_value_market",
    "book value currency (market)": "book_value_market_currency",
    # Market value
    "market value":           "market_value",
    "value":                  "market_value",
    "market value currency":  "market_value_currency",
    "currency":               "market_value_currency",
    # Returns
    "market unrealized returns": "unrealized_return",
    "unrealized return":         "unrealized_return",
    "unrealized gain/loss":      "unrealized_return",
    "market unrealized returns currency": "unrealized_return_currency",
}


_AS_OF_RE = re.compile(
    r"as\s*of[:\s]*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


def _norm(col: str) -> str:
    return re.sub(r"\s+", " ", (col or "").strip().lower())


def _to_float(v) -> Optional[float]:
    """Lenient float parser. Strips $, commas, parens; returns None on empty."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in {"-", "—", "n/a", "na"}:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    try:
        f = float(s)
        return -f if neg else f
    except ValueError:
        return None


def _mask_account_number(num: Optional[str]) -> Optional[str]:
    if not num:
        return None
    s = str(num).strip()
    if len(s) <= 4:
        return s
    return "•••" + s[-4:]


def _try_parse_date(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%b-%Y",
                "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_holdings_csv(filepath: str | Path,
                       encoding: str = "utf-8-sig",
                       delimiter: str = ",") -> dict:
    """Parse a holdings CSV. See module docstring for shape."""
    filepath = Path(filepath)
    positions: list[dict] = []
    errors: list[str] = []
    currencies: set[str] = set()
    as_of: str = ""

    try:
        # First pass: scrape the entire file as raw rows so we can find an
        # "As of …" footer that may sit below the data block.
        with open(filepath, newline="", encoding=encoding,
                  errors="replace") as f:
            raw_rows = list(csv.reader(f, delimiter=delimiter))
    except Exception as e:
        return {
            "positions": [], "errors": [f"File read error: {e}"],
            "as_of_date": "", "row_count": 0, "currencies": [],
            "mixed_currency": False, "total_market_value_native": 0.0,
            "source_file": filepath.name,
        }

    if not raw_rows:
        return {
            "positions": [], "errors": ["Empty CSV"],
            "as_of_date": "", "row_count": 0, "currencies": [],
            "mixed_currency": False, "total_market_value_native": 0.0,
            "source_file": filepath.name,
        }

    # Locate header row: the first row that has at least 3 known aliases.
    header_idx = -1
    for i, row in enumerate(raw_rows[:10]):
        norms = [_norm(c) for c in row]
        hits = sum(1 for n in norms if n in _COL_ALIASES)
        if hits >= 3:
            header_idx = i
            break
    if header_idx < 0:
        return {
            "positions": [],
            "errors": [
                "Could not detect a holdings header row. Expected columns "
                "like Account Name / Symbol / Quantity / Market Value."
            ],
            "as_of_date": "", "row_count": 0, "currencies": [],
            "mixed_currency": False, "total_market_value_native": 0.0,
            "source_file": filepath.name,
        }

    header = [_norm(c) for c in raw_rows[header_idx]]

    # Scan all rows for "As of …" — lives in any non-data cell.
    for r in raw_rows:
        for cell in r:
            m = _AS_OF_RE.search(str(cell or ""))
            if m:
                d = _try_parse_date(m.group(1))
                if d and not as_of:
                    as_of = d
                    break
        if as_of:
            break

    # Parse data rows.
    for ri, row in enumerate(raw_rows[header_idx + 1:],
                             start=header_idx + 2):
        if not row or all((not c or not str(c).strip()) for c in row):
            continue
        joined = " ".join(str(c) for c in row).lower()
        # Skip footer rows like "As of …", "Disclaimer", "Total", etc.
        if joined.startswith("as of") or "disclaimer" in joined:
            continue

        rec: dict = {col: row[idx] if idx < len(row) else ""
                     for idx, col in enumerate(header)}
        # Translate via aliases.
        norm_rec = {}
        for raw_col, val in rec.items():
            canonical = _COL_ALIASES.get(raw_col)
            if canonical:
                norm_rec[canonical] = val

        ticker = (norm_rec.get("ticker") or "").strip() or None
        sec_name = (norm_rec.get("security_name") or "").strip() or None
        if not ticker and not sec_name:
            # Likely a totals row or blank row — skip silently.
            continue

        market_value = _to_float(norm_rec.get("market_value"))
        mv_ccy = (norm_rec.get("market_value_currency") or "").strip().upper() or None
        if mv_ccy:
            currencies.add(mv_ccy)

        out_row = {
            "account_name":          (norm_rec.get("account_name") or "").strip() or None,
            "account_type":          (norm_rec.get("account_type") or "").strip() or None,
            "account_number_masked": _mask_account_number(norm_rec.get("account_number")),
            "ticker":                ticker,
            "exchange":              (norm_rec.get("exchange") or "").strip() or None,
            "security_name":         sec_name,
            "security_type":         (norm_rec.get("security_type") or "").strip() or None,
            "quantity":              _to_float(norm_rec.get("quantity")),
            "market_price":          _to_float(norm_rec.get("market_price")),
            "market_price_currency": (norm_rec.get("market_price_currency") or "").strip().upper() or None,
            "book_value_cad":        _to_float(norm_rec.get("book_value_cad")),
            "book_value_market":     _to_float(norm_rec.get("book_value_market")),
            "market_value":          market_value,
            "market_value_currency": mv_ccy,
            "unrealized_return":     _to_float(norm_rec.get("unrealized_return")),
            "unrealized_return_currency":
                (norm_rec.get("unrealized_return_currency") or "").strip().upper() or None,
            "position_direction":    (norm_rec.get("position_direction") or "").strip() or None,
        }

        if market_value is None and out_row["quantity"] is None:
            errors.append(f"Row {ri}: no market value or quantity — skipped")
            continue

        positions.append(out_row)

    total_native = sum((p.get("market_value") or 0) for p in positions)
    currencies_list = sorted(c for c in currencies if c)
    mixed = len(currencies_list) > 1

    return {
        "positions":                  positions,
        "as_of_date":                 as_of or datetime.now().date().isoformat(),
        "row_count":                  len(positions),
        "currencies":                 currencies_list,
        "mixed_currency":             mixed,
        "total_market_value_native":  float(total_native or 0),
        "errors":                     errors,
        "source_file":                filepath.name,
    }

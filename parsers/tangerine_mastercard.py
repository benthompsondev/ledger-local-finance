"""
Tangerine Mastercard PDF parser — v4 (bounding-box).

Uses pdfplumber extract_words() with bounding boxes to cleanly separate
the left transaction column from the right-column boilerplate (interest
rate table, reward categories, legal text).

Column layout (letter-size PDF, 612pt wide):
  Left column  x < ~370pt  — transaction dates, descriptions, amounts, rewards
  Right column x >= ~370pt — interest rate table, reward categories (DISCARDED)

Transaction row patterns after left-column filtering:
  NORMAL:   DD-Mon-YYYY  DD-Mon-YYYY  MERCHANT CITY PROV  $amt  $reward
  SPLIT-A:  MERCHANT...          (line before date line, while prev TX is complete)
            DD-Mon-YYYY  DD-Mon-YYYY  CITY PROV  $amt  $reward
  SPLIT-B:  Same as SPLIT-A but city follows on next line after date row
  SPECIAL:  DD-Mon-YYYY  DD-Mon-YYYY  PAYMENT/INTEREST/FEE  –  $amt  –
  FX:       next line below transaction: "45.20 USD @ 1.430530973"

Y-gap rules (distance from previous complete TX row):
  gap ≤ 8pt : always a city/location continuation fragment
  gap ≥ 16pt: always a merchant name preview for the next TX
  gap = 12pt: use content heuristics (province check + length)
"""
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from collections import defaultdict

# Ensure the app root is on sys.path regardless of working directory or OS.
# Uses the file's own location so it works on Windows, macOS, and Linux.
_APP_ROOT = str(Path(__file__).parent.parent.resolve())
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

try:
    import pdfplumber
except ImportError:
    raise ImportError("pdfplumber is required: pip install pdfplumber")

from utils.categorizer import enrich_transaction

# ── Regex patterns ────────────────────────────────────────────────────────────

DATE_RE = re.compile(
    r"(\d{2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})",
    re.IGNORECASE,
)
TWO_DATE_RE = re.compile(
    r"^(\d{2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4})"
    r"\s+"
    r"(\d{2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4})"
    r"\s*(.*)",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(r"(-?\$[\d,]+\.\d{2}|–)")
FX_RE     = re.compile(r"^([\d,]+\.\d+)\s+([A-Z]{3})\s+@\s+([\d.]+)\s*$")

MONTH_MAP = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}

# Canadian province / territory codes
CA_PROVINCES = frozenset({
    "AB","BC","MB","NB","NL","NS","NT","NU","ON","PE","QC","SK","YT"
})

# Lines to skip entirely (header/footer/summary rows)
SKIP_LINE_RE = re.compile(
    r"^(Here.s how you used|Previous Balance"
    r"|Transaction\s+Posted|Date\s+Date"
    r"|Page \d+ of \d+"
    r"|New Balance)"
    r"|^\s*–\s*$",   # lone em-dash lines
    re.IGNORECASE,
)

# Right-column cutoff (reward earned column ends ~354, boilerplate starts ~396)
RIGHT_COL_CUTOFF = 370

# Y-gap thresholds (in 4pt-quantized y-buckets; 1 bucket = 4pt)
# gap ≤ 2 buckets (≤ 8pt):   always a city/location continuation
# gap = 3 buckets (= 12pt):  ambiguous — use content heuristics
# gap ≥ 4 buckets (≥ 16pt): always a merchant preview for next TX
GAP_ALWAYS_CONT    = 2   # gap <= this (buckets): always city/location continuation
GAP_ALWAYS_PREVIEW = 4   # gap >= this (buckets): always merchant preview


# ── Location fragment detection ───────────────────────────────────────────────

def _is_location_fragment(text: str) -> bool:
    """
    Return True if the line looks like a trailing city/location fragment
    that belongs to the current (already-amounted) transaction.

    Catches Canadian provinces, US states, and non-special short tokens
    that represent truncated city names (e.g. 'NSW', 'FRANCISCOCA', 'MAAKONDDUB').
    """
    stripped = text.strip()

    # Characters that indicate a merchant name rather than a pure location
    MERCHANT_CHARS = set("0123456789*#./@$&")
    has_merchant_chars = any(c in stripped for c in MERCHANT_CHARS)

    # Pure Canadian province code
    if stripped in CA_PROVINCES:
        return True

    # Ends with a Canadian province code (city + prov)
    parts = stripped.split()
    if len(parts) >= 2 and parts[-1] in CA_PROVINCES:
        return True

    # Ends with a US state abbreviation (common for US merchant locations)
    # Only treat as location if the text:
    #   (a) has no merchant-indicator characters, AND
    #   (b) is short (≤ 15 chars), to avoid matching company suffixes like
    #       'CO' in 'CANNABIS CO' or 'IN' in 'SOMETHING IN'.
    US_STATES = frozenset({
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID",
        "IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS",
        "MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
        "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
        "WI","WY","DC",
    })
    if (not has_merchant_chars and len(stripped) <= 15
            and len(parts) >= 2 and parts[-1] in US_STATES):
        return True

    # Short token with no special merchant characters
    # (truncated city names: 'NSW', 'FRANCISCOCA', 'MAAKONDDUB', 'NIAGARA FALLSON')
    # True merchant names are either longer or contain special chars.
    if len(stripped) <= 15 and not has_merchant_chars:
        return True

    return False


def _cur_has_amounts(parts: list) -> bool:
    """Return True if any accumulated part already contains an amount token."""
    for p in parts:
        if AMOUNT_RE.search(p):
            return True
    return False


# ── Date / amount helpers ─────────────────────────────────────────────────────

def parse_date_str(s: str) -> str:
    m = DATE_RE.match(s.strip())
    if not m:
        raise ValueError(f"Bad date: {s!r}")
    return datetime(int(m.group(3)), MONTH_MAP[m.group(2).lower()], int(m.group(1))).date().isoformat()


def parse_amount(s: str) -> Optional[float]:
    s = s.strip()
    if s in ("–", "-", ""):
        return None
    try:
        return float(s.replace("$", "").replace(",", ""))
    except ValueError:
        return None


# ── PDF word extraction ───────────────────────────────────────────────────────

def extract_left_col_lines(page):
    """
    Extract words from the left column of a page, group by y-coordinate,
    and return a list of (text, y_bucket) tuples sorted top-to-bottom.

    Words with x0 >= RIGHT_COL_CUTOFF are discarded (right-column boilerplate).
    Words on the same row (y within ~4pt) are joined left-to-right.
    The y_bucket is the quantized row position (in 4pt units).
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)

    # Filter to left column only
    left_words = [w for w in words if w["x0"] < RIGHT_COL_CUTOFF]

    # Group by quantized y (4pt buckets to handle slight baseline variation)
    rows = defaultdict(list)
    for w in left_words:
        bucket = round(w["top"] / 4)  # quantize to 4pt
        rows[bucket].append(w)

    # Sort each row by x, then join words with a single space
    result = []
    for bucket in sorted(rows.keys()):
        row_words = sorted(rows[bucket], key=lambda w: w["x0"])
        line = " ".join(w["text"] for w in row_words).strip()
        if line:
            result.append((line, bucket))

    return result


# ── Transaction builder ───────────────────────────────────────────────────────

def build_tx(tx_date, posted_date, desc_parts, statement_period, errors):
    fx_amount = fx_currency = fx_rate = None
    amount = reward = None
    clean = []

    for part in (p.strip() for p in desc_parts if p.strip()):
        # FX line?
        fx_m = FX_RE.match(part)
        if fx_m:
            fx_amount   = float(fx_m.group(1).replace(",", ""))
            fx_currency = fx_m.group(2)
            fx_rate     = float(fx_m.group(3))
            continue

        # Amount tokens?
        amt_list = list(AMOUNT_RE.finditer(part))
        if amt_list:
            # Separate real dollar amounts from em-dash placeholders
            real_amounts = [a for a in amt_list if a.group() != "–"]
            if len(real_amounts) >= 2:
                amount = parse_amount(real_amounts[-2].group())
                reward = parse_amount(real_amounts[-1].group())
            elif len(real_amounts) == 1:
                amount = parse_amount(real_amounts[-1].group())
            # Strip all amount tokens and em-dashes from description text
            desc_only = AMOUNT_RE.sub("", part).strip()
            desc_only = re.sub(r"\s*–\s*", " ", desc_only).strip()
            if desc_only:
                clean.append(desc_only)
        else:
            clean.append(part)

    desc = re.sub(r"\s{2,}", " ", " ".join(clean)).strip()

    # Strip trailing Canadian province code (e.g. " ON", " BC")
    parts = desc.split()
    if parts and parts[-1] in CA_PROVINCES:
        desc = " ".join(parts[:-1]).strip()

    desc = desc.strip().rstrip("*").strip()

    if amount is None:
        errors.append(f"No amount: {tx_date} | {desc[:60]}")
        return None

    direction = "debit"
    if amount < 0:
        direction = "credit"
    desc_up = desc.upper()
    if "PAYMENT - THANK YOU" in desc_up:
        direction = "payment"
    if any(k in desc_up for k in ["CASH INTEREST", "PURCHASE INTEREST", "INTEREST CHARGE"]):
        direction = "debit"

    tx = {
        "account_type":     "mastercard",
        "transaction_date": tx_date,
        "posted_date":      posted_date,
        "raw_description":  desc,
        "amount":           abs(amount) if direction == "debit" else amount,
        "currency":         "CAD",
        "foreign_amount":   fx_amount,
        "foreign_currency": fx_currency,
        "fx_rate":          fx_rate,
        "direction":        direction,
        "is_transfer":      1 if direction == "payment" else 0,
        "reward_points":    reward,
        "statement_period": statement_period,
    }
    enrich_transaction(tx)
    return tx


# ── Main parser ───────────────────────────────────────────────────────────────

# ── Pass 35c: statement summary extraction (Mastercard) ──────────────
# The Mastercard statement PDF's first page carries the authoritative
# truth for interest_charges / fees / cash_advances / new_balance /
# payment_due_date. The pre-Pass-35c parser ignored page 0 entirely
# and let downstream scoring guess from transaction rows, which is how
# we ended up with a $577.79 "interest" figure for a card that owed
# $0.08 (Pass 35b root cause). This helper takes the page-0 text and
# returns a normalized summary dict. It is pure-text so it can be
# unit-tested without a real PDF fixture.

_MONTH_TOKEN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)

# Statement period line: "Statement period   Apr 8 to May 7, 2026"
# Also tolerates "Statement Period:" and an em-dash separator.
_STMT_PERIOD_RE = re.compile(
    r"Statement\s+[Pp]eriod[:\s]*"
    rf"(?P<start_mon>{_MONTH_TOKEN})\s+(?P<start_day>\d{{1,2}})"
    rf"(?:,?\s+(?P<start_year>\d{{4}}))?"
    rf"\s+(?:to|-|–|—)\s+"
    rf"(?P<end_mon>{_MONTH_TOKEN})\s+(?P<end_day>\d{{1,2}}),"
    rf"\s+(?P<end_year>\d{{4}})",
    re.IGNORECASE,
)

# Money matcher tolerates negative signs, parentheses (for credits),
# leading $ and CR suffix. We strip these out before float-parsing.
_MONEY_RE = re.compile(
    r"\(?-?\$?\s*([\d,]+\.\d{2})\s*(CR)?\)?",
    re.IGNORECASE,
)

# Payment-due-date: "Payment due date  May 28, 2026"
_DUE_DATE_RE = re.compile(
    r"(?:Payment\s+[Dd]ue\s+[Dd]ate|Minimum\s+[Pp]ayment\s+[Dd]ue\s+[Bb]y)"
    r"[:\s]*"
    rf"(?P<mon>{_MONTH_TOKEN})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})",
    re.IGNORECASE,
)

# Summary line labels mapped to output keys. Each label is matched
# case-insensitively at line start; the first money token on that
# line is taken as the value. Multiple candidate labels per key
# accommodate Tangerine's PDF layout variations.
_SUMMARY_LABELS: list[tuple[str, tuple[str, ...]]] = [
    ("previous_balance",     ("Previous Balance",)),
    ("payments_and_credits", ("Payments & other credits", "Payments & Credits",
                              "Payments and Credits", "Payments/Credits")),
    ("transactions_total",   ("Transactions", "Purchases")),
    ("cash_advances_total",  ("Cash Advances",)),
    ("adjustments_total",    ("Adjustments", "Other")),
    ("interest_charges",     ("Interest Charges", "Interest")),
    ("fees",                 ("Fees", "Service Fees", "Other Fees")),
    ("new_balance",          ("New Balance",)),
    ("minimum_payment_due",  ("Minimum Payment", "Minimum Payment Due")),
]


def _money_to_float(token: str) -> Optional[float]:
    """Parse a money token like '$0.08', '($25.00)', '12.34 CR' → float.

    Parentheses and trailing 'CR' both indicate a credit (negative).
    Returns None when the token cannot be parsed.
    """
    if token is None:
        return None
    s = token.strip()
    if not s:
        return None
    is_credit = (
        ("(" in s and ")" in s)
        or s.upper().endswith("CR")
        or s.lstrip().startswith("-")
    )
    m = _MONEY_RE.search(s)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return -val if is_credit else val


def _money_after_label(text: str, labels: tuple[str, ...]) -> Optional[float]:
    """Find the first money amount following any summary label.

    Tangerine's PDF extraction sometimes merges several summary rows into
    one long line, so labels like "Interest charges* $0.08" may not begin a
    line. This helper searches the whole first-page text and takes the money
    token immediately after the matched label instead of the first token on a
    physical line.
    """
    if not text:
        return None
    for lbl in sorted(labels, key=len, reverse=True):
        pat = re.compile(
            rf"{re.escape(lbl)}\s*\*?\s*[:\-]?\s*({_MONEY_RE.pattern})",
            re.IGNORECASE,
        )
        m = pat.search(text)
        if not m:
            continue
        val = _money_to_float(m.group(1))
        if val is not None:
            return round(val, 2)
    return None


def _iso_date(mon: str, day: str, year: str) -> Optional[str]:
    """Convert ('Apr','8','2026') style triples to '2026-04-08'. None on bad input."""
    if not (mon and day and year):
        return None
    key = mon[:3].lower()
    m = MONTH_MAP.get(key)
    if not m:
        return None
    try:
        return f"{int(year):04d}-{m:02d}-{int(day):02d}"
    except ValueError:
        return None


def extract_statement_summary(page_text: str) -> dict:
    """Extract Mastercard statement-summary fields from page-0 text.

    Returns a dict with the keys listed in _SUMMARY_LABELS plus
    statement_period_label / statement_start_date / statement_end_date
    / payment_due_date. Values are floats or ISO date strings; missing
    fields are None so callers can distinguish "absent" from "zero".
    """
    out: dict = {
        "statement_period_label": "",
        "statement_start_date":   None,
        "statement_end_date":     None,
        "previous_balance":       None,
        "payments_and_credits":   None,
        "transactions_total":     None,
        "cash_advances_total":    None,
        "adjustments_total":      None,
        "interest_charges":       None,
        "fees":                   None,
        "new_balance":            None,
        "minimum_payment_due":    None,
        "payment_due_date":       None,
    }
    if not page_text:
        return out

    # Statement period
    m = _STMT_PERIOD_RE.search(page_text)
    if m:
        start_year = m.group("start_year") or m.group("end_year")
        out["statement_start_date"] = _iso_date(
            m.group("start_mon"), m.group("start_day"), start_year,
        )
        out["statement_end_date"] = _iso_date(
            m.group("end_mon"), m.group("end_day"), m.group("end_year"),
        )
        # Reconstruct a clean human-readable label for display.
        out["statement_period_label"] = (
            f"{m.group('start_mon')} {int(m.group('start_day'))} to "
            f"{m.group('end_mon')} {int(m.group('end_day'))}, "
            f"{m.group('end_year')}"
        )

    # Payment due date
    md = _DUE_DATE_RE.search(page_text)
    if md:
        out["payment_due_date"] = _iso_date(
            md.group("mon"), md.group("day"), md.group("year"),
        )

    # Line-by-line label match. We pick the FIRST money token on the
    # matched line; Tangerine's "Interest Charges 0.08" / "Fees 0.00"
    # rows always carry exactly one amount on the same line.
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    for line in lines:
        # Skip the statement-period and due-date lines explicitly so a
        # generic label match can't pick up the day-of-month as a money
        # token by accident.
        if _STMT_PERIOD_RE.search(line) or _DUE_DATE_RE.search(line):
            continue
        for key, labels in _SUMMARY_LABELS:
            if out[key] is not None:
                continue  # already set on an earlier line
            for lbl in labels:
                # Anchor the label at line start (allow whitespace + a
                # leading bullet). Match case-insensitively.
                if not re.match(
                    rf"^\s*[•\-]?\s*{re.escape(lbl)}\b",
                    line, re.IGNORECASE,
                ):
                    continue
                # Take the first money-shaped token on the same line.
                money_match = _MONEY_RE.search(line)
                if not money_match:
                    continue
                val = _money_to_float(money_match.group(0))
                if val is not None:
                    out[key] = round(val, 2)
                break  # match found for this key on this line

    # Full-text fallback for merged summary rows. Do this after the line pass
    # so ordinary clean lines still win, but fill in fields that would be
    # missed when pdfplumber combines labels onto the same physical line.
    for key, labels in _SUMMARY_LABELS:
        if out[key] is not None:
            continue
        val = _money_after_label(page_text, labels)
        if val is not None:
            out[key] = val

    return out


def parse_pdf(filepath, statement_period=None):
    filepath = Path(filepath)
    transactions = []
    errors = []
    statement_summary: dict = {}

    with pdfplumber.open(filepath) as pdf:
        pages = pdf.pages
        n = len(pages)
        # Page 0 = summary, pages 1..N-2 = transactions, page N-1 = legal boilerplate
        if n > 2:
            tx_pages = pages[1:n - 1]
        elif n > 1:
            tx_pages = pages[1:]
        else:
            tx_pages = pages[:]

        # Pass 35c: extract the statement-summary block from page 0
        # before walking transaction pages. Best-effort: if extraction
        # fails for any reason we still return a working transaction
        # list, just without the summary.
        if n >= 1:
            try:
                page0_text = pages[0].extract_text() or ""
                statement_summary = extract_statement_summary(page0_text)
            except Exception as _e:
                errors.append(f"summary extraction failed: {_e!r}")
                statement_summary = {}

        # Collect (text, y_bucket) tuples; add a large y-offset per page
        # to avoid y collisions between pages
        all_rows = []
        for page_idx, page in enumerate(tx_pages):
            page_offset = page_idx * 100000  # 100k buckets between pages
            rows = extract_left_col_lines(page)
            all_rows.extend((text, y + page_offset) for text, y in rows)

    # ── State machine ─────────────────────────────────────────────────────────
    cur_tx_date   = None
    cur_post_date = None
    cur_parts     = []
    pending       = None   # merchant fragment seen BEFORE the next date line
    last_tx_y     = None   # y-bucket of the last complete TX row seen

    def flush():
        nonlocal cur_tx_date, cur_post_date, cur_parts
        if cur_tx_date is None:
            return
        tx = build_tx(cur_tx_date, cur_post_date, cur_parts, statement_period, errors)
        if tx:
            transactions.append(tx)
        cur_tx_date = cur_post_date = None
        cur_parts = []

    for raw, y_bucket in all_rows:
        line = raw.strip()
        if not line:
            continue

        # Skip header/footer/summary lines
        if SKIP_LINE_RE.match(line):
            continue

        # FX lines always attach to the current transaction.
        # Do NOT reset last_tx_y — the gap reference stays on the last TX row
        # so that merchant previews after FX lines are still classified correctly.
        if FX_RE.match(line):
            if cur_tx_date:
                cur_parts.append(line)
            continue

        # Two-date transaction line?
        m = TWO_DATE_RE.match(line)
        if m:
            flush()
            try:
                cur_tx_date   = parse_date_str(m.group(1))
                cur_post_date = parse_date_str(m.group(2))
            except ValueError:
                cur_tx_date = cur_post_date = None
                pending = None
                last_tx_y = y_bucket
                continue

            rest = m.group(3).strip()

            # Merge any pending merchant fragment that appeared before this line
            if pending:
                rest = f"{pending} {rest}".strip() if rest else pending
                pending = None

            cur_parts = [rest] if rest else []
            last_tx_y = y_bucket
            continue

        # ── Non-date, non-FX line ─────────────────────────────────────────────
        # Determine if this is a continuation of the current TX or a preview
        # of the next TX (merchant fragment appearing before next date line).

        if cur_tx_date is not None and _cur_has_amounts(cur_parts):
            # Current TX is complete (has amounts). Classify this line:
            gap = (y_bucket - last_tx_y) if last_tx_y is not None else 999

            if gap <= GAP_ALWAYS_CONT:
                # Small gap: city/location fragment for current TX
                if len(line) < 80:
                    cur_parts.append(line)
            elif gap >= GAP_ALWAYS_PREVIEW:
                # Large gap: merchant preview for next TX
                if len(line) < 80:
                    pending = f"{pending} {line}".strip() if pending else line
            else:
                # Ambiguous gap (12pt): use content heuristics
                if _is_location_fragment(line):
                    cur_parts.append(line)
                else:
                    if len(line) < 80:
                        pending = f"{pending} {line}".strip() if pending else line

        elif cur_tx_date is not None:
            # Inside a transaction but not yet complete (no amounts yet).
            # Continuation fragment.
            if len(line) < 80:
                cur_parts.append(line)

        else:
            # Between transactions: accumulate as pending merchant for next date line
            if len(line) < 80:
                pending = f"{pending} {line}".strip() if pending else line

    flush()

    stats = {
        "total_parsed": len(transactions),
        "debits":   sum(1 for t in transactions if t["direction"] == "debit"),
        "credits":  sum(1 for t in transactions if t["direction"] == "credit"),
        "payments": sum(1 for t in transactions if t["direction"] == "payment"),
        "flagged":  sum(1 for t in transactions if t.get("is_flagged")),
        "foreign":  sum(1 for t in transactions if t.get("foreign_currency")),
        "errors":   len(errors),
    }

    # Pass 35c: prefer the human-friendly period label extracted from
    # the statement summary when the caller didn't pass one explicitly.
    # This is what Import History will show ("Apr 8 to May 7, 2026").
    _period = (
        statement_period
        or (statement_summary.get("statement_period_label") or "").strip()
        or filepath.stem
    )

    return {
        "transactions":       transactions,
        "errors":             errors,
        "stats":              stats,
        "statement_period":   _period,
        "source_file":        filepath.name,
        # Pass 35c: full statement summary so Import can persist it,
        # compute_score can use authoritative values, and Import
        # History can display interest / fees / new balance.
        "statement_summary":  statement_summary,
    }

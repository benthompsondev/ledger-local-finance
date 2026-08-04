"""Work out how one CSV file is shaped, once, before reading any row.

The old importer decided per row: it tried a list of date formats in order
and took the first that parsed. That is why ``02/04/2026`` in a day-first
export silently became February 4th while ``15/04/2026`` two rows later
correctly became April 15th, scattering one statement across four months
with no error raised. The same per-cell guessing turned ``-10,00`` into
minus one thousand.

A statement file has exactly one convention throughout. Detect it once from
all the rows together, report what was detected, and refuse to guess when
the evidence is genuinely ambiguous.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Formats that carry no ambiguity: the field order is fixed by the text.
UNAMBIGUOUS_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
]
# Numeric day/month orders. Which one applies cannot be read off a single
# cell, only off the whole column.
DAY_FIRST = "%d/%m/%Y"
MONTH_FIRST = "%m/%d/%Y"
DAY_FIRST_DASH = "%d-%m-%Y"
MONTH_FIRST_DASH = "%m-%d-%Y"
# 02.04.2026 is normal across Europe and Latin America, and is day-first
# essentially everywhere it appears.
DAY_FIRST_DOT = "%d.%m.%Y"
MONTH_FIRST_DOT = "%m.%d.%Y"

# Synthetic formats handled by this module rather than by strptime.
COMPACT_DATE = "yyyymmdd"
EXCEL_SERIAL = "excel_serial"

_SLASH_PAIRS = (
    (DAY_FIRST, MONTH_FIRST, "/"),
    (DAY_FIRST_DASH, MONTH_FIRST_DASH, "-"),
    (DAY_FIRST_DOT, MONTH_FIRST_DOT, "."),
)
_EXCEL_EPOCH = date(1899, 12, 30)
_NUMERIC_RE = re.compile(r"^-?[\d.,\s$()]+$")


@dataclass
class CsvDialect:
    """Everything about a file's shape, decided once."""

    delimiter: str = ","
    encoding: str = ""
    skip_rows: int = 0
    has_header: bool = True
    header: list[str] = field(default_factory=list)
    date_column: str = ""
    date_format: str = ""
    decimal: str = "."
    bank: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


# ── dates ────────────────────────────────────────────────────────────────

def _strip_time_of_day(text: str) -> str:
    """Drop a trailing clock time. `2026-04-02 14:31:00` is still a date."""
    cleaned = text.replace("T", " ").strip()
    parts = cleaned.split()
    if len(parts) >= 2 and ":" in parts[-1]:
        return " ".join(parts[:-1]).strip()
    return cleaned


def parse_date_with(value: str, fmt: str) -> str | None:
    """Parse one cell using the format already decided for the file."""
    text = _strip_time_of_day((value or "").strip())
    if not text or not fmt:
        return None
    if fmt == COMPACT_DATE:
        if len(text) == 8 and text.isdigit():
            try:
                return datetime.strptime(text, "%Y%m%d").date().isoformat()
            except ValueError:
                return None
        return None
    if fmt == EXCEL_SERIAL:
        try:
            serial = int(float(text))
        except ValueError:
            return None
        if not 1 <= serial <= 60000:
            return None
        return (_EXCEL_EPOCH + timedelta(days=serial)).isoformat()
    try:
        return datetime.strptime(text, fmt).date().isoformat()
    except ValueError:
        return None


def _looks_like_a_date(text: str) -> bool:
    """Cheap check used to tell a header row from a data row."""
    text = (text or "").strip()
    if not text:
        return False
    for fmt in (*UNAMBIGUOUS_DATE_FORMATS, DAY_FIRST, MONTH_FIRST,
                DAY_FIRST_DASH, MONTH_FIRST_DASH, DAY_FIRST_DOT,
                MONTH_FIRST_DOT, COMPACT_DATE):
        if parse_date_with(text, fmt):
            return True
    return False


def _date_span(cells: list[str], fmt: str) -> int | None:
    """Days between the earliest and latest date under one reading."""
    parsed = [parse_date_with(c, fmt) for c in cells]
    if not all(parsed):
        return None
    dates = sorted(date.fromisoformat(p) for p in parsed if p)
    if not dates:
        return None
    return (dates[-1] - dates[0]).days


def detect_date_format(values: list[str]) -> tuple[str, list[str], list[str]]:
    """Choose one date format for a whole column.

    Returns (format, problems, notes). A format is only accepted if it parses
    every non-empty cell. Day-first versus month-first is settled by looking
    for a component above 12 somewhere in the column; when no cell proves it
    either way the file is genuinely ambiguous and the caller must ask.
    """
    all_cells = [c for c in (str(v or "").strip() for v in values) if c]
    if not all_cells:
        return "", ["No dates found in the date column."], []

    # A row of junk should not decide the whole file's format, so only cells
    # that some format can read are used as evidence. The rest are reported
    # per row as unreadable.
    cells = [c for c in all_cells if _looks_like_a_date(c)]
    if not cells:
        return "", [
            "Northstar could not read any of the dates in this file."
        ], []

    def parses_all(fmt: str) -> bool:
        return all(parse_date_with(c, fmt) for c in cells)

    # Before anything else: does the file mix both numeric orders?
    for day_fmt, month_fmt, _sep in _SLASH_PAIRS:
        only_day = [
            c for c in cells
            if parse_date_with(c, day_fmt) and not parse_date_with(c, month_fmt)
        ]
        only_month = [
            c for c in cells
            if parse_date_with(c, month_fmt) and not parse_date_with(c, day_fmt)
        ]
        if only_day and only_month:
            return "", [
                "The dates in this file contradict each other: "
                f"'{only_month[0]}' can only be month-first while "
                f"'{only_day[0]}' can only be day-first. Northstar will not "
                "guess which convention the file uses."
            ], []

    for fmt in UNAMBIGUOUS_DATE_FORMATS:
        if parses_all(fmt):
            return fmt, [], []
    if parses_all(COMPACT_DATE):
        return COMPACT_DATE, [], ["Dates read as YYYYMMDD."]

    for day_fmt, month_fmt, sep in _SLASH_PAIRS:
        day_ok, month_ok = parses_all(day_fmt), parses_all(month_fmt)
        if not (day_ok or month_ok):
            continue
        firsts, seconds = [], []
        for cell in cells:
            parts = cell.split(sep)
            if len(parts) < 2:
                continue
            try:
                firsts.append(int(parts[0]))
                seconds.append(int(parts[1]))
            except ValueError:
                continue
        first_over_12 = any(v > 12 for v in firsts)
        second_over_12 = any(v > 12 for v in seconds)
        if first_over_12 and day_ok:
            return day_fmt, [], ["Dates read as day-first (DD/MM/YYYY)."]
        if second_over_12 and month_ok:
            return month_fmt, [], ["Dates read as month-first (MM/DD/YYYY)."]

        # Every day and month value is 12 or lower, so no single cell settles
        # it. A statement is a short, ordered run of dates, so the reading
        # that keeps them closest together is almost always the real one:
        # 7/2, 7/3, 7/5 is three days in July, not three months.
        day_span = _date_span(cells, day_fmt)
        month_span = _date_span(cells, month_fmt)
        if day_ok and month_ok and day_span is not None and month_span is not None:
            if day_span < month_span:
                return day_fmt, [], [
                    "Dates read as day-first (DD/MM/YYYY); that is the only "
                    "reading where this file covers one statement period."
                ]
            if month_span < day_span:
                return month_fmt, [], [
                    "Dates read as month-first (MM/DD/YYYY); that is the only "
                    "reading where this file covers one statement period."
                ]
        # Nothing in the file settles it: no value above 12, and neither
        # reading keeps the rows closer together. Guessing here silently
        # scatters a statement across the wrong months, so ask instead. The
        # caller can supply an explicit format to resolve it.
        return "", [
            "These dates could be read as either DD/MM/YYYY or MM/DD/YYYY, "
            "and nothing in the file settles which. Choose the right one "
            "before importing."
        ], []

    if parses_all(EXCEL_SERIAL):
        return EXCEL_SERIAL, [], [
            "Dates read as Excel serial numbers. Check the preview carefully."
        ]
    return "", [
        "Northstar could not recognise the date format in this file."
    ], []


# ── amounts ──────────────────────────────────────────────────────────────

# A lone separator followed by exactly three digits says nothing about its
# own role. `1.234` is one thousand two hundred and thirty-four when the dot
# groups thousands, and one-point-two-three-four when it is the decimal
# point. The two readings differ by a factor of a thousand, and the file has
# to settle it somewhere else or be refused.
_AMBIGUOUS_GROUPING = re.compile(r"^\d{1,3}([.,])\d{3}$")


def _normalise_amount_text(raw: str) -> str:
    """Strip sign, currency and direction marks down to bare punctuation."""
    text = str(raw or "").strip()
    text, _suffix = _strip_direction_suffix(text)
    for symbol in CURRENCY_SYMBOLS:
        text = text.replace(symbol, "")
    text = text.replace(" ", "").strip()
    return text.strip("()").strip("-+").lstrip("-+")


def detect_decimal_separator(
    values: list[str],
) -> tuple[str, list[str], list[str]]:
    """Decide whether this file writes 1.234,56 or 1,234.56.

    Returns ``(separator, notes, problems)``. A file that supplies evidence
    for both conventions is contradictory and must be refused rather than
    parsed with the North-American default. So must a file whose only
    punctuation is a thousands-shaped group, which supports both readings
    equally well.
    """
    comma_decimal = dot_decimal = 0
    # Separators a cell proves are grouping marks by repeating them:
    # 12.345.678 has no other sensible reading.
    grouping: set[str] = set()
    # Separator -> the first cell that showed the ambiguous shape, for the
    # refusal message.
    ambiguous: dict[str, str] = {}
    for raw in values:
        text = _normalise_amount_text(raw)
        if not text:
            continue
        if re.search(r",\d{1,2}$", text) and "." not in text.rsplit(",", 1)[-1]:
            # 1234,56 or 1.234,56
            comma_decimal += 1
            continue
        if re.search(r"\.\d{1,2}$", text):
            dot_decimal += 1
            continue
        for separator in (".", ","):
            if text.count(separator) > 1:
                grouping.add(separator)
        match = _AMBIGUOUS_GROUPING.match(text)
        if match:
            ambiguous.setdefault(match.group(1), text)

    if comma_decimal and dot_decimal:
        return ".", [], [
            "This file mixes comma-decimal amounts (10,00) with dot-decimal "
            "amounts (10.00). Northstar will not guess which number "
            "convention applies."
        ]
    if comma_decimal:
        return ",", ["Amounts read with a comma decimal separator."], []
    if dot_decimal:
        return ".", [], []
    # No cell shows a fractional part, so the only evidence left is grouping.
    if grouping == {"."}:
        return ",", [
            "Amounts read with a dot thousands separator (1.234.567)."
        ], []
    if grouping == {","}:
        return ".", [], []
    if len(grouping) > 1:
        return ".", [], [
            "This file groups thousands with both dots and commas. Northstar "
            "will not guess which number convention applies."
        ]
    if ambiguous:
        separator, example = next(iter(ambiguous.items()))
        name = "dot" if separator == "." else "comma"
        as_decimal = float(example.replace(separator, "."))
        as_grouped = float(example.replace(separator, ""))
        return ".", [], [
            f"Northstar cannot tell whether the {name} in '{example}' is a "
            f"decimal point or a thousands separator: it means either "
            f"${as_decimal:,.2f} or ${as_grouped:,.2f}, and nothing else in "
            "this file settles which. Re-export the statement with the cents "
            "included (1234.56 or 1.234,56) before importing it."
        ]
    return ".", [], []


CURRENCY_TOKENS = ("CAD", "USD", "EUR", "GBP", "AUD", "NZD", "CHF", "JPY")

# Symbols a statement may print next to the number. Stripping them is safe
# because a file that mixes currencies is refused elsewhere, on the evidence
# of its currency column rather than its punctuation.
CURRENCY_SYMBOLS = "$£€¥₹₽₩¢"

# Some banks, mostly outside North America, write the direction as a suffix
# instead of a sign: "60.00 DR" is money out, "1000.00 CR" is money in.
_DEBIT_SUFFIXES = ("DR", "DB", "D")
_CREDIT_SUFFIXES = ("CR", "C")


def _strip_direction_suffix(text: str) -> tuple[str, bool | None]:
    """Split a trailing DR/CR marker off an amount. None means there was none."""
    upper = text.upper().rstrip(". ")
    for suffix in _DEBIT_SUFFIXES:
        if upper.endswith(suffix) and any(ch.isdigit() for ch in upper[:-len(suffix)]):
            return text[: len(upper) - len(suffix)].strip(), True
    for suffix in _CREDIT_SUFFIXES:
        if upper.endswith(suffix) and any(ch.isdigit() for ch in upper[:-len(suffix)]):
            return text[: len(upper) - len(suffix)].strip(), False
    return text, None


def parse_amount_with(value: str, decimal: str):
    """Parse one amount cell using the convention decided for the file.

    Returns None when the cell holds something that is not a number at all,
    so the caller can refuse the file. Reading unparseable text as 0.0 is how
    a broken row used to import as a free purchase, quietly understating a
    month's spending.

    A blank cell is a genuine zero rather than a failure: split debit/credit
    exports leave one side empty on every row.
    """
    text = str(value or "").strip()
    if not text:
        return 0.0
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    # Mainframe exports put the sign after the number: 10.00-
    if text.endswith("-"):
        negative = True
        text = text[:-1].strip()
    elif text.endswith("+"):
        text = text[:-1].strip()
    upper = text.upper()
    for token in CURRENCY_TOKENS:
        if upper.startswith(token):
            text = text[len(token):].strip()
            break
        if upper.endswith(token):
            text = text[: -len(token)].strip()
            break
    # Outside North America the direction is often a suffix, not a sign.
    text, suffix_is_debit = _strip_direction_suffix(text)
    if suffix_is_debit is not None:
        negative = suffix_is_debit
    for symbol in CURRENCY_SYMBOLS:
        text = text.replace(symbol, "")
    text = text.strip()
    text = text.replace("$", "").replace(" ", "").replace(" ", "")
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    if text.startswith("-"):
        negative = not negative
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if not text or not any(ch.isdigit() for ch in text):
        return None
    try:
        result = abs(float(text))
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return -result if negative else result


def _is_numeric_cell(text: str) -> bool:
    text = str(text or "").strip()
    return bool(text) and bool(_NUMERIC_RE.match(text)) and any(
        ch.isdigit() for ch in text
    )


# ── file shape ───────────────────────────────────────────────────────────

def find_header_row(rows: list[list[str]]) -> tuple[int, bool]:
    """Return (index of the header row, whether the file has one at all).

    Statements often carry a few account-info lines before the real header.
    A header row is one that has the file's usual number of columns and does
    not itself parse as a transaction. If the very first full-width row looks
    like data, the file is headerless.
    """
    full_rows = [r for r in rows if len([c for c in r if str(c).strip()]) > 1]
    if not full_rows:
        return 0, True
    widths = [len(r) for r in full_rows]
    modal_width = max(set(widths), key=widths.count)
    for index, row in enumerate(rows[:25]):
        if len(row) != modal_width:
            continue
        if any(_looks_like_a_date(cell) for cell in row):
            # The first properly shaped row is already a transaction.
            return index, False
        return index, True
    return 0, True


def positional_header(width: int) -> list[str]:
    return [f"column_{i + 1}" for i in range(width)]


def guess_headerless_roles(data_rows: list[list[str]]) -> dict[str, str]:
    """Best-effort roles for a file with no header, to seed the mapping screen.

    Deliberately a suggestion, never applied silently: a headerless file is
    always sent to the mapping step for confirmation.
    """
    if not data_rows:
        return {}
    width = max(len(r) for r in data_rows)
    columns = [
        [str(row[i]).strip() if i < len(row) else "" for row in data_rows]
        for i in range(width)
    ]
    roles: dict[str, str] = {}
    for index, cells in enumerate(columns):
        name = f"column_{index + 1}"
        filled = [c for c in cells if c]
        if not filled:
            continue
        if all(_looks_like_a_date(c) for c in filled):
            roles.setdefault(name, "transaction_date")
        elif all(_is_numeric_cell(c) for c in filled):
            roles[name] = "amount"
        else:
            roles.setdefault(name, "raw_description")
    # The widest text column is the description; extra text columns are notes.
    return roles

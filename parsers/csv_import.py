"""
Generic CSV import parser.
Handles flexible column mappings for any bank export CSV.
Supported date formats: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, DD-Mon-YYYY
"""
import csv
import io
import re
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from utils.categorizer import enrich_transaction, statement_memo_category
from parsers.bank_presets import (
    match_header_preset, match_positional_preset, preset_by_key,
)
from parsers.csv_dialect import (
    COMPACT_DATE, EXCEL_SERIAL, CsvDialect, detect_date_format,
    detect_decimal_separator, find_header_row, guess_headerless_roles,
    parse_amount_with, parse_date_with, positional_header,
)

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
    "date posted": "posted_date",
    "settlement date": "posted_date",
    "value date": "transaction_date",
    "date of transaction": "transaction_date",
    "booking date": "transaction_date",
    "process date": "transaction_date",
    "completed date": "transaction_date",
    # description aliases
    "description": "raw_description",
    "memo": "raw_description",
    "narrative": "raw_description",
    "transaction description": "raw_description",
    "details": "raw_description",
    "payee": "raw_description",
    "description 1": "raw_description",
    "description 2": "raw_description_2",
    "merchant": "name",
    "merchant name": "name",
    "name": "name",
    # amount aliases
    "amount": "amount",
    "debit": "debit",
    "credit": "credit",
    "withdrawal": "debit",
    "deposit": "credit",
    "withdrawals": "debit",
    "deposits": "credit",
    "transaction amount": "amount",
    "money in": "credit",
    "money out": "debit",
    "funds in": "credit",
    "funds out": "debit",
    "debit amount": "debit",
    "credit amount": "credit",
    # Currency-labelled amount columns (RBC and similar exports head the
    # amount column with the currency, which matched nothing and silently
    # imported every row at $0.00).
    "cad$": "amount",
    "usd$": "amount",
    "cad": "amount",
    "amount (cad)": "amount",
    "amount (usd)": "amount",
    "amount cad": "amount",
    "amount (gbp)": "amount",
    "amount (eur)": "amount",
    "amount (aud)": "amount",
    "amount (nzd)": "amount",
    "amount (usd$)": "amount",
    # PayPal and similar processors report the charge, the fee they took, and
    # what actually moved. "Net" is the one that hits the balance; "gross"
    # would double-count anything with a fee.
    "net": "amount",
    "net amount": "amount",
    "value": "amount",
    # French, because Canadian banks will hand a francophone customer a
    # francophone export and it should not need manual mapping.
    "date de transaction": "transaction_date",
    "date de l'operation": "transaction_date",
    "date d'operation": "transaction_date",
    "date de comptabilisation": "posted_date",
    "libelle": "raw_description",
    "libelle de l'operation": "raw_description",
    "montant": "amount",
    "retrait": "debit",
    "depot": "credit",
    "solde": "balance",
    "devise": "currency",
    # German, for the same reason.
    "datum": "transaction_date",
    "buchungstag": "transaction_date",
    "wertstellung": "posted_date",
    "beschreibung": "raw_description",
    "verwendungszweck": "raw_description",
    "buchungstext": "raw_description",
    "betrag": "amount",
    "waehrung": "currency",
    # category
    "category": "category",
    "currency": "currency",
    "direction": "direction",
    "transaction direction": "direction",
    "type": "tx_type",
    "transaction type": "tx_type",
}

CSV_DELIMITERS = (",", ";", "\t", "|")
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


def read_csv_text(
    filepath: str | Path, encoding: str | None = None,
) -> tuple[str, str]:
    """Decode a normal bank CSV strictly and report the encoding used."""
    path = Path(filepath)
    raw = path.read_bytes()
    utf16_bom = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    if b"\x00" in raw and not utf16_bom:
        raise ValueError(
            "This file does not look like a text CSV. Export it as CSV and try again."
        )
    choices = (encoding,) if encoding else (
        ("utf-16", *CSV_ENCODINGS) if utf16_bom else CSV_ENCODINGS
    )
    failures = []
    for candidate in choices:
        if not candidate:
            continue
        try:
            text = raw.decode(candidate, errors="strict")
            return text, candidate
        except (LookupError, UnicodeDecodeError) as exc:
            failures.append(f"{candidate}: {exc}")
    raise ValueError(
        "SignalSpace could not read this CSV's text encoding. Export it as "
        "UTF-8, UTF-16, or Windows CSV (CP1252), then try again."
    )


def detect_csv_format(
    filepath: str | Path, *, delimiter: str | None = None,
    encoding: str | None = None,
) -> tuple[str, str, str]:
    """Return decoded text, encoding, and a deterministic supported delimiter."""
    text, used_encoding = read_csv_text(filepath, encoding)
    if delimiter:
        if delimiter not in CSV_DELIMITERS:
            raise ValueError(
                "CSV separator must be comma, semicolon, tab, or pipe."
            )
        return text, used_encoding, delimiter
    sample = text[:16384]
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters="".join(CSV_DELIMITERS))
        used_delimiter = sniffed.delimiter
    except csv.Error:
        first_line = next((line for line in sample.splitlines() if line.strip()), "")
        used_delimiter = max(
            CSV_DELIMITERS, key=lambda candidate: first_line.count(candidate)
        )
    return text, used_encoding, used_delimiter


def try_parse_date(s: str) -> Optional[str]:
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_column(col: str) -> str:
    # Fold accents, so "Libellé" and "Währung" match the same aliases as their
    # unaccented spellings rather than falling through to manual mapping.
    folded = unicodedata.normalize("NFKD", str(col or ""))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.strip().lower().replace("_", " ")


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
    delimiter: str | None = None,
    encoding: str | None = None,
    date_format: str | None = None,
    bank: str | None = None,
    column_roles: dict[str, str] | None = None,
    conn=None,
) -> dict:
    """
    Parse a generic bank export CSV.
    Returns {transactions, errors, stats, statement_period, source_file}
    """
    filepath = Path(filepath)
    transactions = []
    errors = []
    unreadable_amounts: list[str] = []
    dialect = CsvDialect()

    try:
        text, used_encoding, used_delimiter = detect_csv_format(
            filepath, delimiter=delimiter, encoding=encoding,
        )
        dialect.encoding = used_encoding
        dialect.delimiter = used_delimiter

        with io.StringIO(text, newline="") as f:
            raw_rows = [row for row in csv.reader(f, delimiter=used_delimiter)]
        if not raw_rows:
            return _empty_result(
                filepath, statement_period, dialect,
                ["This file has no rows."],
            )

        # Where does the real header start, and is there one at all?
        header_index, has_header = find_header_row(raw_rows)
        dialect.skip_rows = header_index
        dialect.has_header = has_header
        if header_index:
            dialect.notes.append(
                f"Skipped {header_index} line(s) above the column headings."
            )

        if not has_header:
            # A headerless export cannot be mapped by column name, but its
            # shape may still identify the bank.
            data_rows = [r for r in raw_rows[header_index:] if any(
                str(c).strip() for c in r
            )]
            width = max((len(r) for r in data_rows), default=0)
            dialect.header = positional_header(width)
            chosen = (
                preset_by_key(bank) if bank
                else match_positional_preset(data_rows)
            )
            if chosen is not None and chosen.positional_roles:
                preset = chosen
                dialect.bank = preset.name
                dialect.notes.append(
                    f"Read as a {preset.name} export. {preset.note}"
                )
                header = list(dialect.header)
                col_map = {
                    header[i]: role
                    for i, role in enumerate(preset.positional_roles)
                    if i < len(header)
                }
                body = data_rows
            else:
                dialect.problems.append(
                    "This file has no column headings and does not match a "
                    "bank SignalSpace knows. Confirm which column is which, or "
                    "choose your bank."
                )
                return _empty_result(
                    filepath, statement_period, dialect, dialect.problems,
                    suggested_roles=guess_headerless_roles(data_rows),
                    needs_mapping=True,
                )
            rows = [
                {header[i]: (r[i] if i < len(r) else "")
                 for i in range(len(header))}
                for r in body
            ]
            return _parse_rows(
                filepath, statement_period, dialect, rows, col_map,
                account_type=account_type, account_id=account_id,
                header_index=header_index, date_format=date_format,
                preset=preset, conn=conn,
            )

        header = [str(c) for c in raw_rows[header_index]]
        dialect.header = header
        col_map = detect_columns(header)
        # A recognised bank overrides the generic aliases, which is how RBC's
        # empty USD column stops overwriting the CAD amount.
        preset = preset_by_key(bank) if bank else match_header_preset(header)
        if preset is not None:
            dialect.bank = preset.name
            dialect.notes.append(
                f"Read as a {preset.name} export. {preset.note}"
            )
            for raw in header:
                role = preset.column_roles.get(_norm_header(raw))
                if role:
                    col_map[raw] = role
        if column_roles is not None:
            # A user-confirmed mapping chooses roles, but it does not bypass
            # the canonical date, number, row-width, and fail-closed checks.
            explicit = {
                str(raw).strip(): role
                for raw, role in column_roles.items()
            }
            safety_roles = {"currency", "direction", "tx_type"}
            col_map = {
                raw: explicit.get(
                    str(raw).strip(),
                    col_map.get(raw, raw)
                    if col_map.get(raw) in safety_roles else raw,
                )
                for raw in header
            }
            preset = None
            dialect.notes.append("Columns read from your saved mapping.")
        column_problems = _column_role_problems(header, col_map)
        if column_problems:
            dialect.problems.extend(column_problems)
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
                needs_mapping=True,
            )
        body = [r for r in raw_rows[header_index + 1:] if any(
            str(c).strip() for c in r
        )]
        width_problems = _row_width_problems(header, body, col_map, header_index)
        if width_problems:
            dialect.problems.extend(width_problems)
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
                needs_mapping=True,
            )
        rows = [
            {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            for r in body
        ]
        if not rows:
            return _empty_result(
                filepath, statement_period, dialect,
                ["This file has headings but no transaction rows."],
            )
        return _parse_rows(
            filepath, statement_period, dialect, rows, col_map,
            account_type=account_type, account_id=account_id,
            header_index=header_index, date_format=date_format, preset=preset,
            conn=conn,
        )
    except Exception as exc:
        return _empty_result(
            filepath, statement_period, dialect, [f"File read error: {exc}"],
        )


def _norm_header(value: str) -> str:
    return normalize_column(value)


def _far_future() -> str:
    """The date past which a statement row cannot be genuine.

    Analysis elsewhere deliberately ignores the computer clock and anchors to
    the newest imported date instead. Validating an import is the one place the
    clock is the right reference: a bank cannot hand you a statement about next
    year, whatever the newest row in the database happens to say. A year of
    slack covers post-dated items and a wrong system clock.
    """
    return (datetime.now().date() + timedelta(days=365)).isoformat()


# Columns whose absence changes what a row means, rather than just leaving a
# field blank.
_LOAD_BEARING_ROLES = ("transaction_date", "posted_date", "date", "trans_date",
                       "amount", "debit", "credit", "amount_usd")


def _row_width_problems(header: list[str], body: list[list[str]],
                        col_map: dict, header_index: int) -> list[str]:
    """Refuse rows whose field count contradicts the header.

    Rows used to be padded with blanks and truncated to the header's width,
    which meant an unquoted comma inside a description turned "$1,000.00" into
    "$1", and a row that stopped early recorded a real purchase as $0.00.
    Both imported clean. A file whose rows do not agree with its own header is
    not a file whose numbers can be trusted.

    Short rows are only a problem when what is missing is load-bearing: plenty
    of exports leave trailing optional columns off entirely.
    """
    problems: list[str] = []
    needed = max(
        (index for index, name in enumerate(header)
         if col_map.get(name) in _LOAD_BEARING_ROLES),
        default=-1,
    )
    long_rows: list[int] = []
    short_rows: list[int] = []
    for offset, row in enumerate(body):
        line = header_index + 2 + offset
        if len(row) > len(header):
            long_rows.append(line)
        elif len(row) <= needed:
            short_rows.append(line)

    def _lines(rows: list[int]) -> str:
        shown = ", ".join(str(n) for n in rows[:3])
        return shown + (f" and {len(rows) - 3} more" if len(rows) > 3 else "")

    if long_rows:
        problems.append(
            f"{len(long_rows)} row(s) have more values than the file has "
            f"column headings (line {_lines(long_rows)}). That usually means a "
            "description contains an unquoted separator, which shifts every "
            "value after it. SignalSpace will not guess which value is the "
            "amount."
        )
    if short_rows:
        problems.append(
            f"{len(short_rows)} row(s) stop before the date or amount column "
            f"(line {_lines(short_rows)}). Importing those would record a real "
            "transaction as $0.00."
        )
    return problems


def _column_role_problems(
    header: list[str], col_map: dict[str, str],
) -> list[str]:
    """Refuse duplicate headings or competing columns for one money role."""
    problems: list[str] = []
    normalized = [str(value or "").strip().casefold() for value in header]
    duplicate_names = sorted({
        name for name in normalized if name and normalized.count(name) > 1
    })
    if duplicate_names:
        problems.append(
            "This file repeats the column heading "
            + ", ".join(f"'{name}'" for name in duplicate_names)
            + ". SignalSpace cannot tell those columns apart."
        )

    unique_roles = {
        "transaction_date", "posted_date", "amount", "debit", "credit",
    }
    by_role: dict[str, list[str]] = {}
    for raw, role in col_map.items():
        if role in unique_roles:
            by_role.setdefault(role, []).append(str(raw))
    for role, sources in by_role.items():
        if len(sources) > 1:
            label = role.replace("_", " ")
            problems.append(
                f"More than one column was detected as {label}: "
                + ", ".join(f"'{source}'" for source in sources)
                + ". Choose one before importing."
            )
    return problems


def _parse_rows(filepath, statement_period, dialect, rows, col_map, *,
                account_type, account_id, header_index, date_format,
                preset=None, conn=None) -> dict:
    """Turn mapped rows into transactions, once the file's shape is settled."""
    transactions: list[dict] = []
    errors: list[str] = []
    unreadable_dates: list[str] = []
    unreadable_amounts: list[str] = []
    contradictory_amounts: list[str] = []
    far_future_rows: list[str] = []
    try:

        mapped_rows = [
            {col_map.get(k, k): v for k, v in row.items()} for row in rows
        ]

        signed_amount_populated = any(
            str(row.get("amount", "")).strip() for row in mapped_rows
        )
        split_amount_populated = any(
            str(row.get(key, "")).strip()
            for row in mapped_rows for key in ("debit", "credit")
        )
        if not signed_amount_populated and not split_amount_populated:
            dialect.problems.append(
                "SignalSpace could not find any transaction amounts in this file."
            )
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
                needs_mapping=True,
            )

        # ── one date convention for the whole file ──────────────────────
        date_field = ""
        for candidate in ("transaction_date", "posted_date", "date",
                          "trans_date"):
            if any(str(r.get(candidate, "")).strip() for r in mapped_rows):
                date_field = candidate
                break
        if not date_field:
            dialect.problems.append(
                "SignalSpace could not find a date column in this file."
            )
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
                needs_mapping=True,
            )
        dialect.date_column = date_field
        date_values = [str(r.get(date_field, "")) for r in mapped_rows]
        if date_format:
            # The caller answered the ambiguity, so honour it rather than
            # re-deriving a convention.
            detected_format, date_problems, date_notes = date_format, [], [
                f"Dates read as {date_format} because you chose it."
            ]
        else:
            detected_format, date_problems, date_notes = detect_date_format(
                date_values,
            )
            if date_problems and preset is not None and preset.date_format:
                # The file cannot settle its own day/month order, but the
                # bank can: a Citi export is always month-first. Knowing that
                # is half the reason presets exist.
                if all(
                    parse_date_with(value, preset.date_format)
                    for value in date_values if str(value or "").strip()
                ):
                    detected_format, date_problems = preset.date_format, []
                    date_notes = [
                        f"Dates read as {preset.name} writes them."
                    ]
        date_format = detected_format
        dialect.date_format = date_format
        dialect.notes.extend(date_notes)
        if date_problems:
            dialect.problems.extend(date_problems)
            result = _empty_result(
                filepath, statement_period, dialect, dialect.problems,
                needs_mapping=True,
            )
            # An ambiguous date column is answerable: offer the choice rather
            # than leaving the user with a refusal and no way forward.
            if any("DD/MM" in problem for problem in date_problems):
                result["date_format_choices"] = [
                    {"value": "%d/%m/%Y", "label": "Day first (DD/MM/YYYY)",
                     "example": _format_example(date_values, "%d/%m/%Y")},
                    {"value": "%m/%d/%Y", "label": "Month first (MM/DD/YYYY)",
                     "example": _format_example(date_values, "%m/%d/%Y")},
                ]
            return result

        # ── one number convention for the whole file ────────────────────
        amount_values: list[str] = []
        for r in mapped_rows:
            for key in ("amount", "debit", "credit"):
                value = str(r.get(key, "")).strip()
                if value:
                    amount_values.append(value)
        (
            dialect.decimal,
            decimal_notes,
            decimal_problems,
        ) = detect_decimal_separator(amount_values)
        dialect.notes.extend(decimal_notes)
        if decimal_problems:
            dialect.problems.extend(decimal_problems)
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
            )

        if signed_amount_populated and split_amount_populated:
            redundant_problem = _redundant_amount_layout_problem(
                mapped_rows, dialect.decimal,
            )
            if redundant_problem:
                dialect.problems.append(redundant_problem)
                return _empty_result(
                    filepath, statement_period, dialect, dialect.problems,
                    needs_mapping=True,
                )
            dialect.notes.append(
                "The signed amount column matches the debit/credit columns; "
                "the signed source amount was preserved."
            )

        # Some exports give every amount as a positive number and put the
        # direction in a separate column. Reading those as inflows turned
        # every withdrawal into income.
        direction_column, direction_problem = _direction_column(
            mapped_rows, dialect.decimal,
        )
        if direction_problem:
            dialect.problems.append(direction_problem)
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
            )
        if direction_column:
            dialect.notes.append(
                "Amounts are unsigned; direction read from the "
                f"'{direction_column}' column."
            )

        # Money in two currencies cannot be added up. SignalSpace has no
        # conversion, so summing them would produce a total that means
        # nothing at all.
        currencies = {
            str(r.get("currency", "")).strip().upper()
            for r in mapped_rows if str(r.get("currency", "")).strip()
        }
        if len(currencies) > 1:
            dialect.problems.append(
                "This file mixes " + " and ".join(sorted(currencies))
                + ". SignalSpace cannot convert between currencies, so these "
                "cannot be combined. Import one currency per file."
            )
            return _empty_result(
                filepath, statement_period, dialect, dialect.problems,
            )

        for row_num, mapped in enumerate(mapped_rows, start=header_index + 2):
                try:
                    raw_date = str(mapped.get(date_field, "")).strip()
                    if not raw_date:
                        if _is_statement_summary_row(mapped):
                            continue
                    tx_date = parse_date_with(raw_date, date_format)
                    if not tx_date:
                        message = (
                            f"Row {row_num}: could not read the date "
                            f"'{raw_date}'"
                        )
                        errors.append(message)
                        unreadable_dates.append(message)
                        continue
                    if tx_date > _far_future():
                        # A statement cannot describe next year. This is a
                        # mis-read century, a typo, or a wrong format that
                        # happened to parse. Letting it through is worse than
                        # it looks: every later window is measured back from
                        # the newest date in the database, so one row dated
                        # 2027 pushes a person's real spending out of view
                        # entirely.
                        far_future_rows.append(f"row {row_num}: {tx_date}")
                        continue

                    posted_raw = str(mapped.get("posted_date", "")).strip()
                    posted_date = (
                        parse_date_with(posted_raw, date_format)
                        if posted_raw else None
                    )

                    # Description. A "Name" column (Tangerine CSV exports,
                    # among others) holds the actual merchant, so it outranks
                    # Memo — Memo is
                    # free-text detail that previously produced wrong merchants,
                    # bogus fee/NSF flags, and "Unknown" rows.
                    name_col = mapped.get("name", "").strip()
                    desc = name_col or mapped.get("raw_description", "").strip()
                    if not desc:
                        for alt in ["payee", "memo", "details", "narrative"]:
                            desc = mapped.get(alt, "").strip()
                            if desc:
                                break
                    if not desc:
                        desc = "Unknown"
                    memo_note = ""
                    if name_col:
                        memo_note = mapped.get("raw_description", "").strip()
                        if memo_note == desc:
                            memo_note = ""
                    # Some exports split the description over two columns
                    # ("Description 1" / "Description 2"); the second holds
                    # the branch or terminal detail.
                    extra_desc = mapped.get("raw_description_2", "").strip()
                    if extra_desc and extra_desc != desc:
                        memo_note = (
                            f"{memo_note} {extra_desc}".strip()
                            if memo_note else extra_desc
                        )

                    # Amount — signed column, split debit/credit columns, or
                    # unsigned amounts with the direction in its own column.
                    amount = 0.0
                    direction = "debit"
                    if signed_amount_populated:
                        if not mapped["amount"].strip():
                            unreadable_amounts.append(
                                f"row {row_num}: blank signed amount"
                            )
                            continue
                        raw_amount = parse_amount_with(
                            mapped["amount"], dialect.decimal,
                        )
                        if raw_amount is None:
                            # Not a number. Importing this as $0 understates
                            # the month and leaves nothing for the user to
                            # notice, so refuse the file instead.
                            unreadable_amounts.append(
                                f"row {row_num}: "
                                f"'{mapped['amount'].strip()[:24]}'"
                            )
                            continue
                        if preset is not None and preset.amount_sign == "inverted":
                            # Card issuers like Discover and Amex write a
                            # purchase as a positive number. Read literally,
                            # every purchase becomes income. Nothing in the
                            # file says which way round it is, which is why
                            # the bank has to be known.
                            raw_amount = -raw_amount
                            direction = "credit" if raw_amount > 0 else "debit"
                            amount = raw_amount
                        elif direction_column:
                            # Unsigned file: the direction column decides.
                            is_credit = _is_credit_marker(
                                mapped.get(direction_column, "")
                            )
                            amount = (
                                abs(raw_amount) if is_credit
                                else -abs(raw_amount)
                            )
                            direction = "credit" if is_credit else "debit"
                        else:
                            # The signed source value is authoritative:
                            # positive enters this account and negative leaves
                            # it. Preserve that sign through storage and UI.
                            direction = "credit" if raw_amount > 0 else "debit"
                            amount = raw_amount
                    elif split_amount_populated:
                        debit_raw = str(mapped.get("debit", "") or "").strip()
                        credit_raw = str(mapped.get("credit", "") or "").strip()
                        if not debit_raw and not credit_raw:
                            unreadable_amounts.append(
                                f"row {row_num}: blank debit and credit amounts"
                            )
                            continue
                        debit_val = parse_amount_with(
                            debit_raw or "0", dialect.decimal,
                        )
                        credit_val = parse_amount_with(
                            credit_raw or "0", dialect.decimal,
                        )
                        if debit_val is None or credit_val is None:
                            unreadable_amounts.append(f"row {row_num}")
                            continue
                        if debit_val != 0 and credit_val != 0:
                            contradictory_amounts.append(f"row {row_num}")
                            continue
                        # Split columns have no source sign, so create the same
                        # canonical account-relative sign explicitly.
                        if debit_val != 0:
                            amount = -abs(debit_val)
                            direction = "debit"
                        elif credit_val != 0:
                            amount = abs(credit_val)
                            direction = "credit"

                    if amount == 0.0 and not desc:
                        continue  # empty row

                    tx = {
                        "account_type": account_type,
                        "account_id": account_id,
                        "transaction_date": tx_date,
                        "posted_date": posted_date,
                        "raw_description": desc,
                        "amount": amount,
                        "direction": direction,
                        "currency": mapped.get("currency", "CAD").strip() or "CAD",
                        "statement_period": statement_period or filepath.stem,
                    }
                    if memo_note:
                        tx["notes"] = memo_note
                        tx["statement_memo"] = memo_note
                        memo_category = statement_memo_category(memo_note)
                        if memo_category:
                            tx["statement_category"] = memo_category[0]
                            tx["statement_subcategory"] = memo_category[1]

                    # Pre-assign category if CSV has one
                    if mapped.get("category", "").strip():
                        tx["category"] = mapped["category"].strip()

                    enrich_transaction(
                        tx, conn=conn, use_learned_rules=conn is not None,
                    )
                    transactions.append(tx)

                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")

    except Exception as e:
        errors.append(f"File read error: {e}")

    if far_future_rows:
        shown = ", ".join(far_future_rows[:3])
        more = (
            f" and {len(far_future_rows) - 3} more"
            if len(far_future_rows) > 3 else ""
        )
        dialect.problems.append(
            f"{len(far_future_rows)} row(s) are dated more than a year from "
            f"now ({shown}{more}). A statement cannot describe next year, so "
            "the dates were read wrongly. Nothing was imported."
        )

    if unreadable_dates:
        shown = ", ".join(unreadable_dates[:3])
        more = (
            f" and {len(unreadable_dates) - 3} more"
            if len(unreadable_dates) > 3 else ""
        )
        dialect.problems.append(
            f"{len(unreadable_dates)} row(s) have a date SignalSpace cannot "
            f"read ({shown}{more}). A statement must use one readable date "
            "format throughout, so nothing was imported."
        )

    if unreadable_amounts:
        shown = ", ".join(unreadable_amounts[:3])
        more = (
            f" and {len(unreadable_amounts) - 3} more"
            if len(unreadable_amounts) > 3 else ""
        )
        dialect.problems.append(
            f"{len(unreadable_amounts)} row(s) have an amount SignalSpace "
            f"cannot read ({shown}{more}). Importing them as $0.00 would "
            "understate your spending, so nothing was imported."
        )

    if contradictory_amounts:
        shown = ", ".join(contradictory_amounts[:3])
        dialect.problems.append(
            f"{len(contradictory_amounts)} row(s) contain both a debit and "
            f"a credit value ({shown}). SignalSpace will not choose one and "
            "discard the other."
        )

    zero_rows = sum(1 for t in transactions if not t.get("amount"))
    if transactions and zero_rows == len(transactions):
        # Every row came in at zero, which means the amount column was never
        # really found. Importing this would silently add worthless rows.
        dialect.problems.append(
            "Every row in this file came through as $0.00, so the amount "
            "column was not recognised. Confirm the columns before importing."
        )
    elif transactions and zero_rows > len(transactions) / 2:
        dialect.problems.append(
            f"{zero_rows} of {len(transactions)} rows came through as $0.00. "
            "Check that the right amount column was picked."
        )
    if errors and not unreadable_dates:
        dialect.problems.append(
            f"{len(errors)} row(s) could not be read. SignalSpace does not "
            "partially import a statement because the missing rows would "
            "make its totals wrong."
        )

    return _result(
        filepath, statement_period, dialect, transactions, errors,
        needs_mapping=bool(dialect.problems),
    )


def _format_example(values: list[str], fmt: str) -> str:
    """Show what a choice would actually do to this file's dates."""
    parsed = [
        parse_date_with(value, fmt) for value in values[:40]
        if str(value or "").strip()
    ]
    parsed = sorted(p for p in parsed if p)
    if not parsed:
        return ""
    if parsed[0] == parsed[-1]:
        return parsed[0]
    return f"{parsed[0]} to {parsed[-1]}"


def _receipt(dialect: CsvDialect, transactions: list[dict],
             errors: list[str]) -> dict:
    """What the preview shows before anything is saved.

    Every decision the importer made, in the user's words, so a wrong guess
    is visible rather than discovered months later in the charts.
    """
    dates = sorted(t["transaction_date"] for t in transactions
                   if t.get("transaction_date"))
    money_in = round(sum(
        t["amount"] for t in transactions if float(t.get("amount") or 0) > 0
    ), 2)
    money_out = round(sum(
        t["amount"] for t in transactions if float(t.get("amount") or 0) < 0
    ), 2)
    date_label = {
        "": "not detected",
        COMPACT_DATE: "YYYYMMDD",
        EXCEL_SERIAL: "Excel serial numbers",
    }.get(dialect.date_format) or (
        dialect.date_format.replace("%Y", "YYYY").replace("%m", "MM")
        .replace("%d", "DD").replace("%b", "Mon").replace("%B", "Month")
    )
    return {
        "rows_parsed": len(transactions),
        "rows_skipped": len(errors),
        "zero_value_rows": sum(1 for t in transactions if not t.get("amount")),
        "first_date": dates[0] if dates else "",
        "last_date": dates[-1] if dates else "",
        "months_spanned": len({d[:7] for d in dates}),
        "money_in": money_in,
        "money_out": money_out,
        "date_format": date_label,
        "decimal_separator": (
            "comma (1.234,56)" if dialect.decimal == ","
            else "dot (1,234.56)"
        ),
        "delimiter": {",": "comma", ";": "semicolon", "\t": "tab"}.get(
            dialect.delimiter, dialect.delimiter,
        ),
        "encoding": dialect.encoding,
        "bank": dialect.bank,
        "has_header": dialect.has_header,
        "skipped_lines": dialect.skip_rows,
        "notes": list(dialect.notes),
        "problems": list(dialect.problems),
    }


def _result(filepath, statement_period, dialect: CsvDialect,
            transactions: list[dict], errors: list[str], *,
            needs_mapping: bool = False,
            suggested_roles: Optional[dict] = None) -> dict:
    stats = {
        "total_parsed": len(transactions),
        "debits": sum(1 for t in transactions if t.get("direction") == "debit"),
        "credits": sum(
            1 for t in transactions if t.get("direction") == "credit"
        ),
        "errors": len(errors),
    }
    return {
        "transactions": transactions,
        "errors": errors + list(dialect.problems),
        "stats": stats,
        "statement_period": statement_period or Path(filepath).stem,
        "source_file": Path(filepath).name,
        "encoding": dialect.encoding,
        "delimiter": dialect.delimiter,
        # Fail closed: the caller must not confirm an import while this is
        # set, no matter how many rows parsed.
        "blocked": bool(dialect.problems),
        "needs_mapping": needs_mapping,
        "date_format_choices": [],
        "suggested_roles": suggested_roles or {},
        "header": list(dialect.header),
        "receipt": _receipt(dialect, transactions, errors),
    }


def _empty_result(filepath, statement_period, dialect: CsvDialect,
                  problems: list[str], *, suggested_roles: Optional[dict] = None,
                  needs_mapping: bool = False) -> dict:
    for problem in problems:
        if problem not in dialect.problems:
            dialect.problems.append(problem)
    return _result(
        filepath, statement_period, dialect, [], [],
        needs_mapping=needs_mapping, suggested_roles=suggested_roles,
    )


_CREDIT_MARKERS = {"credit", "cr", "c", "deposit", "deposits", "in",
                   "money in", "inflow", "payment received", "+"}
_DEBIT_MARKERS = {"debit", "dr", "d", "withdrawal", "withdrawals", "out",
                  "money out", "outflow", "purchase", "payment", "-"}

_STATEMENT_SUMMARY_LABELS = {
    "TOTAL", "TOTALS", "SUMMARY", "OPENING BALANCE", "BEGINNING BALANCE",
    "STARTING BALANCE", "CLOSING BALANCE", "ENDING BALANCE",
}


def _is_statement_summary_row(mapped: dict) -> bool:
    """Recognize only exact footer labels when a row has no transaction date.

    Substring matching silently dropped real merchants such as "Total Wine".
    An unfamiliar footer now blocks the file for review instead of risking the
    loss of a genuine transaction.
    """
    for key in ("name", "raw_description", "memo", "details", "narrative"):
        value = " ".join(str(mapped.get(key, "") or "").strip().upper().split())
        value = value.strip(" .:-")
        if value in _STATEMENT_SUMMARY_LABELS:
            return True
    return False


def _redundant_amount_layout_problem(
    mapped_rows: list[dict], decimal: str,
) -> str:
    """Validate exports that repeat each amount in signed and split forms."""
    for index, row in enumerate(mapped_rows, start=1):
        signed_raw = str(row.get("amount", "") or "").strip()
        debit_raw = str(row.get("debit", "") or "").strip()
        credit_raw = str(row.get("credit", "") or "").strip()
        if not signed_raw and not debit_raw and not credit_raw:
            continue
        if not signed_raw or (not debit_raw and not credit_raw):
            return (
                "This file switches between a signed amount column and "
                "separate debit/credit columns. Choose one layout before "
                f"importing (data row {index})."
            )
        signed = parse_amount_with(signed_raw, decimal)
        debit = parse_amount_with(debit_raw or "0", decimal)
        credit = parse_amount_with(credit_raw or "0", decimal)
        if signed is None or debit is None or credit is None:
            return (
                "SignalSpace could not reconcile the repeated amount columns "
                f"on data row {index}."
            )
        if debit != 0 and credit != 0:
            return (
                f"Data row {index} contains both a debit and a credit amount."
            )
        expected = credit - debit
        if abs(signed - expected) > 0.005:
            return (
                "The signed amount does not match the debit/credit columns "
                f"on data row {index}. Choose the correct layout before "
                "importing."
            )
    return ""


def _is_credit_marker(value: str) -> bool:
    return str(value or "").strip().lower() in _CREDIT_MARKERS


def _direction_column(
    mapped_rows: list[dict], decimal: str,
) -> tuple[str, str]:
    """Find a column that carries the direction for unsigned amounts.

    Only used when every amount in the file is positive; a signed amount
    column is always authoritative and must never be reversed by a label.
    """
    # An unreadable cell parses to None, and comparing that to zero used to
    # raise, surfacing as an unhelpful "File read error" instead of a refusal.
    amounts = [
        value for value in (
            parse_amount_with(str(r.get("amount", "")), decimal)
            for r in mapped_rows if str(r.get("amount", "")).strip()
        ) if value is not None
    ]
    if not amounts or any(a < 0 for a in amounts):
        return "", ""
    amount_rows = [
        row for row in mapped_rows if str(row.get("amount", "")).strip()
    ]
    for column in ("tx_type", "type", "direction"):
        values = [
            str(row.get(column, "")).strip().lower()
            for row in amount_rows
        ]
        if not any(values):
            continue
        known = _CREDIT_MARKERS | _DEBIT_MARKERS
        if not any(value in known for value in values):
            continue
        unknown = [value for value in values if value not in known]
        if unknown:
            shown = unknown[0] or "blank"
            return "", (
                f"The '{column}' column mixes recognized directions with "
                f"the unknown value '{shown}'. SignalSpace will not guess "
                "whether that row is money in or money out."
            )
        if all(v in known for v in values):
            return column, ""
    return "", ""

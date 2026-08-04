"""
User-assisted CSV column mapping with reusable import profiles.

The automatic alias parser (parsers/csv_import.py) stays the zero-
decision happy path. This module is the fallback when a bank's export
doesn't match the known aliases:

  probe_csv()            — does the automatic parser actually work on
                           this file? (row success rate, not guesses)
  csv_preview()          — headers + sample rows for the mapping UI
  parse_csv_mapped()     — parse using an explicit user mapping; output
                           is byte-compatible with parse_csv()'s shape
  header_signature()     — stable fingerprint of a header row
  save/find profile      — a mapping that worked once is remembered and
                           auto-applied to future files with the same
                           header signature (import_profiles table)

A mapping is a plain dict:
  {
    "date_col":    "Transaction Date",        # required
    "desc_col":    "Details",                 # required
    "amount_mode": "signed" | "split",        # required
    "amount_col":  "Amount",                  # when signed
    "positive_is": "credit",                  # retained for old UI callers
    "debit_col":   "Withdrawals",             # when split
    "credit_col":  "Deposits",                # when split
  }
Signed files always use the source convention: positive is money in and
negative is money out. A profile cannot override that financial truth.
"""
from __future__ import annotations

import csv
import io
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional

from parsers.csv_import import detect_columns, detect_csv_format

# Rows the auto-parser must land for a CSV to count as "understood".
PROBE_MIN_SUCCESS_RATE = 0.5


def suggest_mapping(headers: list[str]) -> dict:
    """Return a best-effort mapping for recognized headers, never a guess."""
    detected = detect_columns(headers)
    by_field: dict[str, str] = {}
    for header, field in detected.items():
        by_field.setdefault(field, header)
    mapping = {
        "date_col": by_field.get("transaction_date") or by_field.get("posted_date") or "",
        "desc_col": by_field.get("name") or by_field.get("raw_description") or "",
        "amount_mode": "signed" if by_field.get("amount") else "split",
        "amount_col": by_field.get("amount") or "",
        "debit_col": by_field.get("debit") or "",
        "credit_col": by_field.get("credit") or "",
        "positive_is": "credit",
    }
    if not mapping["amount_col"] and not (
        mapping["debit_col"] and mapping["credit_col"]
    ):
        mapping["amount_mode"] = ""
    return mapping


def csv_preview(filepath, max_rows: int = 5,
                encoding: str | None = None) -> dict:
    """Headers + up to max_rows raw sample rows for the mapping UI."""
    filepath = Path(filepath)
    try:
        text, used_encoding, delimiter = detect_csv_format(
            filepath, encoding=encoding,
        )
        with io.StringIO(text, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            headers = next(reader, None) or []
            rows = []
            for row in reader:
                if any(cell.strip() for cell in row):
                    rows.append(row[:len(headers)])
                if len(rows) >= max_rows:
                    break
        return {"ok": bool(headers), "headers": [h.strip() for h in headers],
                "rows": rows, "encoding": used_encoding, "delimiter": delimiter,
                "error": "" if headers else "No header row found."}
    except Exception as e:
        return {"ok": False, "headers": [], "rows": [],
                "encoding": "", "delimiter": "",
                "error": f"Could not read file: {e}"}


def probe_csv(filepath) -> dict:
    """Run the automatic parser and report whether it actually worked.

    needs_mapping is True when the alias-based parser produced no rows,
    or errored on more than half of the data rows.
    """
    from parsers.csv_import import parse_csv
    result = parse_csv(filepath)
    parsed = len(result.get("transactions") or [])
    errors = len(result.get("errors") or [])
    total = parsed + errors
    rate = (parsed / total) if total else 0.0
    return {
        "parsed": parsed,
        "errors": errors,
        "success_rate": round(rate, 3),
        "needs_mapping": parsed == 0 or rate < PROBE_MIN_SUCCESS_RATE,
        "result": result,
    }


def header_signature(headers: list[str]) -> str:
    """Stable fingerprint of a header row (order-sensitive,
    case/whitespace-insensitive)."""
    norm = "|".join((h or "").strip().lower() for h in headers)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def validate_mapping(mapping: dict, headers: list[str]) -> list[str]:
    """Plain-language problems with a mapping; empty list = usable."""
    problems = []
    hs = set(headers)
    if not mapping.get("date_col") or mapping["date_col"] not in hs:
        problems.append("Pick which column holds the date.")
    if not mapping.get("desc_col") or mapping["desc_col"] not in hs:
        problems.append("Pick which column holds the description.")
    mode = mapping.get("amount_mode")
    if mode == "signed":
        if not mapping.get("amount_col") or mapping["amount_col"] not in hs:
            problems.append("Pick which column holds the amount.")
        if mapping.get("positive_is", "credit") != "credit":
            problems.append("Signed amounts must keep the source convention: "
                            "positive is money in and negative is money out.")
    elif mode == "split":
        if not mapping.get("debit_col") or mapping["debit_col"] not in hs:
            problems.append("Pick the money-out (debit/withdrawal) column.")
        if not mapping.get("credit_col") or mapping["credit_col"] not in hs:
            problems.append("Pick the money-in (credit/deposit) column.")
    else:
        problems.append("Choose how amounts are stored (one signed "
                        "column, or separate in/out columns).")
    selected = [
        str(mapping.get("date_col") or ""),
        str(mapping.get("desc_col") or ""),
    ]
    if mode == "signed":
        selected.append(str(mapping.get("amount_col") or ""))
    elif mode == "split":
        selected.extend([
            str(mapping.get("debit_col") or ""),
            str(mapping.get("credit_col") or ""),
        ])
    chosen = [value for value in selected if value]
    if len(chosen) != len(set(chosen)):
        problems.append(
            "Use a different source column for the date, description, and "
            "each amount role."
        )
    return problems


def parse_csv_mapped(filepath, mapping: dict,
                     account_type: str = "csv",
                     account_id: Optional[str] = None,
                      statement_period: Optional[str] = None,
                      encoding: str | None = None,
                      delimiter: str | None = None,
                      date_format: str | None = None,
                      conn=None) -> dict:
    """Parse a CSV using an explicit user mapping.

    Output shape matches parsers.csv_import.parse_csv so the Import
    page's downstream flow (preview, persist, summary) is unchanged.
    """
    filepath = Path(filepath)
    preview = csv_preview(filepath, max_rows=0, encoding=encoding)
    headers = preview.get("headers") or []
    problems = validate_mapping(mapping, headers)
    if problems:
        # Keep the canonical blocked/receipt contract even when the mapping
        # itself is incomplete, so preview and confirm behave identically.
        from parsers.csv_dialect import CsvDialect
        from parsers.csv_import import _empty_result

        dialect = CsvDialect(
            delimiter=preview.get("delimiter") or delimiter or ",",
            encoding=preview.get("encoding") or encoding or "",
            header=headers,
        )
        return _empty_result(
            filepath, statement_period, dialect, problems,
            needs_mapping=True,
        )

    roles = {
        str(mapping["date_col"]): "transaction_date",
        str(mapping["desc_col"]): "raw_description",
    }
    if mapping["amount_mode"] == "signed":
        roles[str(mapping["amount_col"])] = "amount"
    else:
        roles[str(mapping["debit_col"])] = "debit"
        roles[str(mapping["credit_col"])] = "credit"

    from parsers.csv_import import parse_csv

    return parse_csv(
        filepath,
        account_type=account_type,
        account_id=account_id,
        statement_period=statement_period,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
        column_roles=roles,
        conn=conn,
    )


# ── Reusable import profiles ─────────────────────────────────────────
def save_import_profile(name: str, headers: list[str], mapping: dict,
                        account_type: str = "",
                        suggested_account_ref: int | None = None,
                        conn: Optional[sqlite3.Connection] = None) -> int:
    """Remember a mapping for this header signature. One profile per
    signature — saving again replaces it (the newest decision wins)."""
    from utils.database import get_connection
    opened = conn is None
    c = conn or get_connection()
    try:
        sig = header_signature(headers)
        c.execute(
            """
            INSERT INTO import_profiles (name, header_signature,
                                         mapping_json, headers_json,
                                         account_type, amount_convention,
                                         profile_version, suggested_account_ref)
            VALUES (?, ?, ?, ?, ?, ?, 3, ?)
            ON CONFLICT(header_signature) DO UPDATE SET
                name=excluded.name,
                mapping_json=excluded.mapping_json,
                headers_json=excluded.headers_json,
                account_type=excluded.account_type,
                amount_convention=excluded.amount_convention,
                profile_version=3,
                suggested_account_ref=excluded.suggested_account_ref,
                updated_at=datetime('now','localtime')
            """,
            (name.strip() or "Unnamed profile", sig,
             json.dumps(mapping), json.dumps(headers), account_type,
             ("signed:source"
              if mapping.get("amount_mode") == "signed" else "split"),
             suggested_account_ref),
        )
        row = c.execute(
            "SELECT id FROM import_profiles WHERE header_signature=?",
            (sig,),
        ).fetchone()
        if opened:
            c.commit()
        return int(row["id"]) if row else 0
    finally:
        if opened:
            c.close()


def find_import_profile(headers: list[str], account_type: str | None = None,
                        conn: Optional[sqlite3.Connection] = None
                        ) -> Optional[dict]:
    """Profile whose header signature matches, or None."""
    from utils.database import get_connection
    opened = conn is None
    c = conn or get_connection()
    try:
        row = c.execute(
            "SELECT * FROM import_profiles WHERE header_signature=?",
            (header_signature(headers),),
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["mapping"] = json.loads(out.get("mapping_json") or "{}")
        saved_type = str(out.get("account_type") or "").strip()
        if account_type and not saved_type:
            raise ValueError(
                "A legacy CSV profile matches these columns but does not record "
                "an account type. Reset or relearn that profile before importing."
            )
        if account_type and saved_type != account_type:
            raise ValueError(
                f"This CSV profile was learned for {saved_type}, not "
                f"{account_type}. Reset or relearn the profile for this account."
            )
        expected = ("signed:source"
                    if out["mapping"].get("amount_mode") == "signed" else "split")
        stored = str(out.get("amount_convention") or "")
        if int(out.get("profile_version") or 0) < 3 or stored != expected:
            raise ValueError(
                "This CSV profile predates signed source amounts or has an "
                "incompatible amount convention. "
                "Reset or relearn it before importing."
            )
        return out
    finally:
        if opened:
            c.close()


def list_import_profiles(conn: Optional[sqlite3.Connection] = None
                         ) -> list[dict]:
    from utils.database import get_connection
    opened = conn is None
    c = conn or get_connection()
    try:
        rows = c.execute(
            "SELECT * FROM import_profiles ORDER BY updated_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["mapping"] = json.loads(d.get("mapping_json") or "{}")
            out.append(d)
        return out
    finally:
        if opened:
            c.close()


def delete_import_profile(profile_id: int,
                          conn: Optional[sqlite3.Connection] = None) -> bool:
    from utils.database import get_connection
    opened = conn is None
    c = conn or get_connection()
    try:
        cur = c.execute("DELETE FROM import_profiles WHERE id=?",
                        (int(profile_id),))
        if opened:
            c.commit()
        return cur.rowcount > 0
    finally:
        if opened:
            c.close()

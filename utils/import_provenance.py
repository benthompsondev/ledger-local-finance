"""Tell a re-exported statement apart from a genuinely new one.

Two identical-looking rows are either the same transaction reported twice or
two real purchases that happened to match. Nothing in a single row settles
it, so 2.4.0 settled it by position: rows were numbered within the file they
arrived in, and the first copy in every file got the same fingerprint. Two
statements each carrying one $3.35 fare therefore collapsed into one fare,
and the second was dropped with no record that it had ever existed.

Numbering against the stored rows instead would fix that and break something
worse: re-importing an overlapping export would append a second copy of every
transaction it shared with the first, silently doubling a month.

The whole file supplies stronger evidence than one row, but it still cannot
prove whether shared activity is the same activity. Even full containment can
describe two real purchases repeated on a later statement. SpendShape therefore
uses the exact file hash when it can prove a re-import and asks the user for
every other overlap rather than making an invisible guess:

  * exact file hash                      -> re-import, number within the file
    so every row collides and is skipped;
  * it has no shared rows                -> new activity, number against what
    is already stored so every row lands;
  * it shares any rows                   -> ambiguous, require a choice.

This is provenance, not a weaker fingerprint: the duplicate check is
unchanged, and which rows came from which file is read from import_batch_id.
"""
from __future__ import annotations

import sqlite3
from typing import Literal, Optional


ImportProvenance = Literal["new", "reexport", "ambiguous"]


def _row_group(row: sqlite3.Row | dict) -> tuple:
    from utils.database import dedup_group_key

    return dedup_group_key({
        "account_ref": row["account_ref"],
        "account_type": row["account_type"],
        "transaction_date": row["transaction_date"],
        "raw_description": row["raw_description"],
        "amount": row["amount"],
    })


def classify_import_provenance(
    conn: sqlite3.Connection,
    *,
    account_ref: Optional[int],
    file_rows: list[dict],
    exclude_batch_id: Optional[int] = None,
) -> ImportProvenance:
    """Classify how this file relates to earlier imports of the account.

    ``ambiguous`` means rows match but the evidence cannot distinguish an
    updated export from genuinely repeated activity. The caller must ask the
    user which it is.

    Exact-file reimports are settled one layer above this function by the file
    hash. That matters for a one-row statement: one matching purchase is not
    enough evidence to silently discard a second real purchase.
    """
    if account_ref is None or not file_rows:
        # Without an account there is no provenance to read, and rows that
        # predate accounts carry none. Keep the older, more cautious
        # numbering: a dropped row is recoverable by re-importing, a
        # double-counted month is not obvious at all.
        return "reexport"

    dates = [
        str(r.get("transaction_date") or "") for r in file_rows
        if r.get("transaction_date")
    ]
    if not dates:
        return "reexport"
    file_start, file_end = min(dates), max(dates)

    # Bounded to the days this file covers. Rows outside them cannot be in
    # any overlap window, and on a database with years of history reading
    # them all back on every import is the difference between an import that
    # feels instant and one that does not.
    prior = conn.execute(
        """
        SELECT import_batch_id, account_ref, account_type, transaction_date,
               raw_description, amount
        FROM transactions
        WHERE account_ref = ?
          AND import_batch_id IS NOT NULL
          AND import_batch_id IS NOT ?
          AND transaction_date BETWEEN ? AND ?
        """,
        (int(account_ref), exclude_batch_id, file_start, file_end),
    ).fetchall()
    if not prior:
        return "new"

    by_batch: dict[int, list[sqlite3.Row]] = {}
    for row in prior:
        by_batch.setdefault(int(row["import_batch_id"]), []).append(row)

    for rows in by_batch.values():
        batch_dates = [str(r["transaction_date"]) for r in rows]
        start = max(min(batch_dates), file_start)
        end = min(max(batch_dates), file_end)
        if start > end:
            continue
        earlier = {
            _row_group(r) for r in rows
            if start <= str(r["transaction_date"]) <= end
        }
        if not earlier:
            continue
        from utils.database import dedup_group_key

        mine = {
            dedup_group_key(tx) for tx in file_rows
            if start <= str(tx.get("transaction_date") or "") <= end
        }
        if earlier & mine:
            return "ambiguous"
    return "new"


def file_reproduces_an_earlier_import(
    conn: sqlite3.Connection,
    *,
    account_ref: Optional[int],
    file_rows: list[dict],
    exclude_batch_id: Optional[int] = None,
) -> bool:
    """Compatibility wrapper for callers that only need the strong verdict."""
    return classify_import_provenance(
        conn,
        account_ref=account_ref,
        file_rows=file_rows,
        exclude_batch_id=exclude_batch_id,
    ) == "reexport"

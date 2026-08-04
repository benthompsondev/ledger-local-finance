"""Atomic persistence for one parsed transaction file."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


def persist_import_batch(
    import_log: dict,
    transactions: Iterable[dict],
    *,
    account_id: Optional[str] = None,
    statement_summary: Optional[dict] = None,
    statement_account_type: str = "mastercard",
    import_mode: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Persist one file's log, transactions, and summary as one unit.

    Duplicate-only imports still keep their log row. Any actual failure rolls
    back the log, every inserted transaction, learned-rule hit counters, and
    the statement summary together.
    """
    from utils.database import (
        dedup_group_key,
        get_connection,
        insert_import_log,
        insert_transaction,
        upsert_statement_summary,
    )
    from utils.financial_semantics import canonicalize_transaction
    from utils.import_provenance import classify_import_provenance

    opened = conn is None
    c = conn or get_connection()
    try:
        c.execute("SAVEPOINT persist_import_batch")
        try:
            import_record = dict(import_log)
            import_record.setdefault("semantics_version", 2)
            batch_id = insert_import_log(import_record, c)
            inserted = skipped = flagged = 0
            rows = [canonicalize_transaction(dict(t)) for t in transactions]
            for tx in rows:
                tx["import_batch_id"] = int(batch_id)
                if account_id:
                    tx["account_id"] = account_id

            account_ref = next(
                (t.get("account_ref") for t in rows if t.get("account_ref")),
                None,
            )
            # Which numbering the fingerprints use is the whole question; see
            # utils/import_provenance.py. An exact re-import or a user-confirmed
            # re-export must reproduce the earlier file's numbers so shared
            # rows collide and are skipped. A genuinely new statement must
            # continue from what is stored so identical real purchases land.
            provenance = classify_import_provenance(
                c,
                account_ref=int(account_ref) if account_ref else None,
                file_rows=rows,
                exclude_batch_id=int(batch_id),
            )
            chosen_mode = str(import_mode or "").strip().lower()
            if chosen_mode and chosen_mode not in {"new", "reexport"}:
                raise ValueError("The statement overlap choice is invalid.")
            if provenance == "ambiguous" and not chosen_mode:
                raise ValueError(
                    "Some, but not all, rows match an earlier import. Choose "
                    "whether this is an updated export or separate activity "
                    "before importing it."
                )
            effective_mode = chosen_mode or provenance
            reexport = effective_mode == "reexport"
            seen_in_file: dict[tuple, int] = {}
            for tx in rows:
                if reexport:
                    group = dedup_group_key(tx)
                    occurrence = seen_in_file.get(group, 0)
                    seen_in_file[group] = occurrence + 1
                else:
                    occurrence = None
                ok, _reason = insert_transaction(tx, c, occurrence=occurrence)
                if ok:
                    inserted += 1
                    flagged += 1 if tx.get("is_flagged") else 0
                else:
                    skipped += 1

            c.execute(
                "UPDATE import_log SET rows_inserted=?, rows_skipped=?, "
                "rows_flagged=? WHERE id=?",
                (inserted, skipped, flagged, int(batch_id)),
            )
            summary_id = 0
            if statement_summary:
                summary_id = upsert_statement_summary(
                    statement_summary,
                    import_batch_id=int(batch_id),
                    account_type=statement_account_type,
                    conn=c,
                )
            c.execute("RELEASE SAVEPOINT persist_import_batch")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT persist_import_batch")
            c.execute("RELEASE SAVEPOINT persist_import_batch")
            raise

        if opened:
            c.commit()
        return {
            "batch_id": int(batch_id),
            "inserted": int(inserted),
            "skipped": int(skipped),
            "flagged": int(flagged),
            "statement_summary_id": int(summary_id or 0),
            "provenance": provenance,
            "import_mode": effective_mode,
        }
    finally:
        if opened:
            c.close()

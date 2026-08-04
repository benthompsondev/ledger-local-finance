"""Deterministic merchant-review and import-undo operations.

This module keeps Streamlit rendering separate from financial mutations. Every
bulk action works from explicit transaction IDs, and every import undo uses a
SQLite savepoint so a failure cannot leave a half-deleted batch.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Iterable, Optional

from utils.categorizer import normalize_merchant


PROTECTED_CATEGORIES = frozenset({
    "Transfer", "Internal Transfer", "Credit Card Payment", "Payment",
    "Cancelled", "Refund / Credit", "Reimbursement / Insurance Reimbursement",
    "Fees / Interest", "Cash Advance", "Interest Income", "Rewards / Cashback",
})
PROTECTED_DIRECTIONS = frozenset({"payment", "transfer", "cancelled"})


def _connection(conn: Optional[sqlite3.Connection]):
    from utils.database import get_connection
    return (conn, False) if conn is not None else (get_connection(), True)


def _close(conn: sqlite3.Connection, opened: bool) -> None:
    if opened:
        conn.close()


def _is_protected(row) -> bool:
    return (
        (row["direction"] or "") in PROTECTED_DIRECTIONS
        or (row["category"] or "") in PROTECTED_CATEGORIES
    )


def merchant_review_groups(*, batch_ids: Optional[list[int]] = None,
                           conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Group uncertain transactions by canonical merchant matcher.

    Protected financial plumbing is excluded. Groups are ordered by repeated
    touch reduction first, then material value.
    """
    c, opened = _connection(conn)
    try:
        params: list[int] = []
        batch_clause = ""
        if batch_ids:
            marks = ",".join("?" * len(batch_ids))
            batch_clause = f" AND import_batch_id IN ({marks})"
            params.extend(int(i) for i in batch_ids)
        rows = c.execute(
            f"""
            SELECT t.id, t.transaction_date, t.raw_description,
                   t.merchant, t.merchant_normalized, t.amount,
                   t.category, t.direction, t.parse_confidence,
                   t.is_flagged, t.import_batch_id,
                   COALESCE(a.name, t.account_type) AS account_name
            FROM transactions t
            LEFT JOIN accounts a ON a.id = t.account_ref
            WHERE (t.is_flagged=1 OR t.category IS NULL OR t.category=''
                   OR t.category IN ('Misc','Uncategorized')
                   OR t.parse_confidence='low')
              {batch_clause}
            ORDER BY t.transaction_date DESC, t.id DESC
            """,
            params,
        ).fetchall()

        grouped: dict[str, list[dict]] = {}
        for raw_row in rows:
            row = dict(raw_row)
            if _is_protected(row):
                continue
            matcher = row.get("merchant_normalized") or normalize_merchant(
                row.get("raw_description") or row.get("merchant") or ""
            )
            if not matcher:
                continue
            row["merchant_normalized"] = matcher
            grouped.setdefault(matcher, []).append(row)

        out = []
        for matcher, items in grouped.items():
            categories = Counter(
                (r.get("category") or "Uncategorized") for r in items
            )
            samples = []
            for item in items:
                sample = item.get("raw_description") or item.get("merchant") or ""
                if sample and sample not in samples:
                    samples.append(sample)
                if len(samples) == 3:
                    break
            dates = [r.get("transaction_date") for r in items if r.get("transaction_date")]
            out.append({
                "merchant_normalized": matcher,
                "transaction_ids": [int(r["id"]) for r in items],
                "count": len(items),
                "total_amount": round(sum(abs(float(r.get("amount") or 0)) for r in items), 2),
                "first_date": min(dates) if dates else "",
                "last_date": max(dates) if dates else "",
                "sample_descriptions": samples,
                "categories": dict(categories),
                "suggested_category": categories.most_common(1)[0][0],
                "batch_ids": sorted({int(r["import_batch_id"]) for r in items
                                     if r.get("import_batch_id") is not None}),
                # Accounts involved — bulk actions across several
                # accounts must be explicit, never silent.
                "accounts": sorted({(r.get("account_name") or "?")
                                    for r in items}),
            })
        return sorted(out, key=lambda g: (-g["count"], -g["total_amount"],
                                          g["merchant_normalized"]))
    finally:
        _close(c, opened)


def preview_bulk_categorization(transaction_ids: Iterable[int], category: str,
                                *, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return the exact eligible row set and semantic change count."""
    c, opened = _connection(conn)
    try:
        ids = list(dict.fromkeys(int(i) for i in transaction_ids))
        if not ids:
            return {
                "selected_ids": [], "requested_count": 0,
                "selected_count": 0, "change_count": 0,
                "skipped_protected": 0, "total_amount": 0.0,
                "missing_count": 0,
                "merchant_normalized": "", "sample_description": "",
                "source_import_batch_id": None,
            }
        marks = ",".join("?" * len(ids))
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM transactions WHERE id IN ({marks}) ORDER BY id", ids
        ).fetchall()]
        eligible = [r for r in rows if not _is_protected(r)]
        matchers = {
            r.get("merchant_normalized") or normalize_merchant(
                r.get("raw_description") or r.get("merchant") or ""
            ) for r in eligible
        }
        matchers.discard("")
        batches = {int(r["import_batch_id"]) for r in eligible
                   if r.get("import_batch_id") is not None}
        return {
            "selected_ids": [int(r["id"]) for r in eligible],
            "requested_count": len(ids),
            "selected_count": len(eligible),
            "change_count": sum(1 for r in eligible if r.get("category") != category),
            "skipped_protected": len(rows) - len(eligible),
            "missing_count": len(ids) - len(rows),
            "total_amount": round(sum(abs(float(r.get("amount") or 0)) for r in eligible), 2),
            "merchant_normalized": next(iter(matchers)) if len(matchers) == 1 else "",
            "sample_description": ((eligible[0].get("raw_description") or "")
                                   if eligible else ""),
            "source_import_batch_id": next(iter(batches)) if len(batches) == 1 else None,
        }
    finally:
        _close(c, opened)


def apply_bulk_categorization(transaction_ids: Iterable[int], category: str,
                              *, save_rule: bool = False,
                              conn: Optional[sqlite3.Connection] = None) -> dict:
    """Apply one reviewed category to explicit rows, optionally saving a rule."""
    from utils.database import apply_category_by_ids, upsert_learned_rule

    if not category:
        raise ValueError("Choose a category before applying a bulk decision.")
    if save_rule and category in {"Uncategorized", "Misc"}:
        raise ValueError(
            "Future merchant rules require a reviewed category, not "
            "Uncategorized or Misc."
        )

    c, opened = _connection(conn)
    try:
        c.execute("SAVEPOINT bulk_category")
        try:
            # Re-read the explicit IDs inside the same savepoint used for the
            # mutation. Missing or newly protected rows must fail closed.
            preview = preview_bulk_categorization(
                transaction_ids, category, conn=c
            )
            if preview["missing_count"] or preview["skipped_protected"]:
                raise RuntimeError(
                    "The selected transactions changed before the bulk update. "
                    "Nothing was saved; refresh Review and try again."
                )
            if not preview["selected_ids"]:
                c.execute("RELEASE SAVEPOINT bulk_category")
                return {**preview, "applied_count": 0, "rule_id": None}

            explanation = (
                f"Categorized as {category} from your bulk approval for "
                f"{preview['merchant_normalized'] or 'selected transactions'}."
            )
            result = apply_category_by_ids(
                preview["selected_ids"], category,
                clear_flags=True,
                category_source="user_bulk",
                category_explanation=explanation,
                conn=c,
            )
            if (result["requested"] != preview["selected_count"]
                    or result["now_with_category"] != preview["selected_count"]
                    or result["is_transfer_synced"] != preview["selected_count"]):
                raise RuntimeError(
                    "The selected transactions changed before the bulk update. "
                    "Nothing was saved; refresh Review and try again."
                )
            rule_id = None
            if save_rule:
                if not preview["merchant_normalized"]:
                    raise ValueError("A future rule requires one normalized merchant group.")
                first_id = preview["selected_ids"][0]
                rule_id = upsert_learned_rule(
                    preview["merchant_normalized"], category,
                    source="user",
                    example_description=preview["sample_description"],
                    source_transaction_id=first_id,
                    source_import_batch_id=preview["source_import_batch_id"],
                    conn=c,
                )
            c.execute("RELEASE SAVEPOINT bulk_category")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT bulk_category")
            c.execute("RELEASE SAVEPOINT bulk_category")
            raise
        if opened:
            c.commit()
        return {
            **preview,
            "applied_count": int(result["now_with_category"]),
            "changed_count": int(preview["change_count"]),
            "flags_cleared": int(result["flags_cleared"]),
            "rule_id": rule_id,
        }
    finally:
        _close(c, opened)


def preview_import_batch_undo(batch_id: int,
                              *, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Describe exactly what one import batch owns before deletion."""
    c, opened = _connection(conn)
    try:
        batch = c.execute(
            "SELECT * FROM import_log WHERE id=?", (int(batch_id),)
        ).fetchone()
        if batch is None:
            return {"available": False, "batch_id": int(batch_id)}
        batch = dict(batch)
        totals = c.execute(
            """
            SELECT COUNT(*) AS transaction_count,
                   MIN(transaction_date) AS first_date,
                   MAX(transaction_date) AS last_date,
                   SUM(CASE WHEN direction='credit' AND amount>0
                       AND COALESCE(is_transfer,0)=0
                       AND COALESCE(category,'') NOT IN
                                           ('Credit Card Payment','Cancelled',
                                            'Internal Transfer','Payment','Transfer')
                       THEN amount ELSE 0 END) AS income,
                   SUM(CASE WHEN direction='debit' AND amount>0
                       AND COALESCE(is_transfer,0)=0
                       AND COALESCE(category,'') NOT IN
                                           ('Credit Card Payment','Cancelled',
                                            'Internal Transfer','Payment','Transfer')
                       THEN amount ELSE 0 END) AS spending,
                   SUM(CASE WHEN manually_edited_at IS NOT NULL
                                  OR category_source IN ('user_edit','user_bulk','ai')
                                  OR flag_reason='reviewed'
                            THEN 1 ELSE 0 END) AS edited_count
            FROM transactions WHERE import_batch_id=?
            """,
            (int(batch_id),),
        ).fetchone()
        rules = [dict(r) for r in c.execute(
            "SELECT id, merchant_normalized, category, enabled "
            "FROM learned_rules WHERE source_import_batch_id=? "
            "OR source_transaction_id IN "
            "(SELECT id FROM transactions WHERE import_batch_id=?) "
            "ORDER BY id",
            (int(batch_id), int(batch_id)),
        ).fetchall()]
        summary_count = c.execute(
            "SELECT COUNT(*) FROM statement_summaries WHERE import_batch_id=?",
            (int(batch_id),),
        ).fetchone()[0]
        return {
            "available": True,
            "batch_id": int(batch_id),
            "filename": batch.get("filename") or "?",
            "filenames": [batch.get("filename") or "?"],
            "imported_at": batch.get("imported_at") or "",
            "account_type": batch.get("account_type") or "",
            "statement_period": batch.get("statement_period") or "",
            "transaction_count": int(totals["transaction_count"] or 0),
            "logged_inserted": int(batch.get("rows_inserted") or 0),
            "duplicates_skipped": int(batch.get("rows_skipped") or 0),
            "duplicates_only": int(totals["transaction_count"] or 0) == 0
                               and int(batch.get("rows_skipped") or 0) > 0,
            "income": round(float(totals["income"] or 0), 2),
            "spending": round(float(totals["spending"] or 0), 2),
            "first_date": (totals["first_date"] or "")[:10],
            "last_date": (totals["last_date"] or "")[:10],
            "edited_count": int(totals["edited_count"] or 0),
            "rules": rules,
            "rule_count": len(rules),
            "statement_summary_count": int(summary_count or 0),
        }
    finally:
        _close(c, opened)


def undo_import_batch(batch_id: int,
                      *, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Transactionally remove only records owned by one import batch.

    Saved rules remain. Their now-dangling source transaction link is cleared,
    while the source batch ID remains as durable provenance.
    """
    c, opened = _connection(conn)
    try:
        preview = preview_import_batch_undo(batch_id, conn=c)
        if not preview["available"]:
            return {
                "undone": False, "reason": "not_found",
                "batch_id": int(batch_id),
            }
        # A destructive undo gets a recovery point before the savepoint makes
        # any changes. If backup creation fails, the undo fails closed.
        from utils.backups import create_backup
        create_backup(
            reason=f"pre-import-undo-{int(batch_id)}", conn=c
        )
        c.execute("SAVEPOINT undo_import_batch")
        try:
            c.execute(
                "UPDATE learned_rules SET source_transaction_id=NULL "
                "WHERE source_import_batch_id=? OR source_transaction_id IN "
                "(SELECT id FROM transactions WHERE import_batch_id=?)",
                (int(batch_id), int(batch_id)),
            )
            deleted_tx = c.execute(
                "DELETE FROM transactions WHERE import_batch_id=?", (int(batch_id),)
            ).rowcount
            deleted_summaries = c.execute(
                "DELETE FROM statement_summaries WHERE import_batch_id=?",
                (int(batch_id),),
            ).rowcount
            deleted_log = c.execute(
                "DELETE FROM import_log WHERE id=?", (int(batch_id),)
            ).rowcount
            if deleted_log != 1:
                raise RuntimeError("Import batch disappeared during undo.")
            c.execute("RELEASE SAVEPOINT undo_import_batch")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT undo_import_batch")
            c.execute("RELEASE SAVEPOINT undo_import_batch")
            raise
        if opened:
            c.commit()
        return {
            "undone": True,
            "batch_id": int(batch_id),
            "transactions_deleted": int(deleted_tx),
            "statement_summaries_deleted": int(deleted_summaries),
            "rules_preserved": int(preview["rule_count"]),
            "filename": preview["filename"],
        }
    finally:
        _close(c, opened)

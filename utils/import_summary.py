"""
Import completion summary — the deterministic packet behind the
"you just imported" panel on the Import page.

Design: the summary is computed from the database (import_log +
transactions), not from transient parse results, so it survives
Streamlit reruns and even app restarts. "Recent" means: batches
imported since the last completed weekly check-in (review_log) —
i.e. data the user has not yet reviewed — grouped into one import
session (batches within SESSION_WINDOW_MINUTES of the newest one).

Materiality (deterministic, shared with Home): a flagged transaction
is worth stopping for when
    ABS(amount) >= MATERIALITY_THRESHOLD
    OR its category is one of MATERIAL_CATEGORIES (fees, interest,
       cash advances, and transfer/CC-payment plumbing — misfiled
       rows there corrupt every cashflow number regardless of size).
Everything else defers to the normal Transactions/Review tools.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from config.constants import MATERIALITY_THRESHOLD

SESSION_WINDOW_MINUTES = 15

# Categories where a wrong or uncertain row is always material —
# they feed the transfer/fee exclusions that every total depends on.
MATERIAL_CATEGORIES = (
    "Fees / Interest", "Cash Advance",
    "Credit Card Payment", "Internal Transfer",
    "Transfer", "Transfer In", "Transfer Out",
)


def _conn(conn):
    from utils.database import get_connection
    if conn is not None:
        return conn, False
    return get_connection(), True


def _close(conn, opened):
    if opened:
        try:
            conn.close()
        except Exception:
            pass


def latest_unreviewed_batches(conn: Optional[sqlite3.Connection] = None
                              ) -> list[dict]:
    """Import batches newer than the last completed check-in, limited
    to the most recent import session. Empty list when the user has
    already reviewed everything (or never imported)."""
    c, opened = _conn(conn)
    try:
        from utils.database import get_last_checkin
        last = get_last_checkin(conn=c)
        cutoff = (last or {}).get("completed_at") or ""

        rows = c.execute(
            """
            SELECT * FROM import_log
            WHERE COALESCE(imported_at, '') > ?
            ORDER BY imported_at DESC, id DESC
            """,
            (cutoff,),
        ).fetchall()
        batches = [dict(r) for r in rows]
        if not batches:
            return []

        # Keep only the newest "session" of imports.
        def _ts(b):
            try:
                return datetime.fromisoformat(
                    (b.get("imported_at") or "")[:19])
            except Exception:
                return None

        newest = _ts(batches[0])
        if newest is None:
            return batches[:1]
        window = timedelta(minutes=SESSION_WINDOW_MINUTES)
        return [b for b in batches
                if _ts(b) is not None and newest - _ts(b) <= window]
    finally:
        _close(c, opened)


def material_review_items(conn: Optional[sqlite3.Connection] = None,
                          batch_ids: Optional[list[int]] = None) -> dict:
    """Split flagged rows into material (stop and look) vs deferrable.

    Returns {material_count, material_total, deferred_count, items}
    where items carries up to 5 material rows for display.
    """
    c, opened = _conn(conn)
    try:
        batch_filter = ""
        params: list = [MATERIALITY_THRESHOLD]
        if batch_ids:
            marks = ",".join("?" * len(batch_ids))
            batch_filter = f" AND import_batch_id IN ({marks})"
            params = [MATERIALITY_THRESHOLD, *batch_ids]

        cat_marks = ",".join("?" * len(MATERIAL_CATEGORIES))
        rows = c.execute(
            f"""
            SELECT id, transaction_date, merchant, raw_description,
                   amount, category, flag_reason,
                   (ABS(amount) >= ? OR category IN ({cat_marks}))
                       AS is_material
            FROM transactions
            WHERE is_flagged = 1{batch_filter}
            ORDER BY is_material DESC, ABS(amount) DESC
            """,
            [params[0], *MATERIAL_CATEGORIES, *params[1:]],
        ).fetchall()

        material = [dict(r) for r in rows if r["is_material"]]
        deferred = [r for r in rows if not r["is_material"]]
        return {
            "material_count": len(material),
            "material_total": round(sum(abs(float(r["amount"] or 0))
                                        for r in material), 2),
            "deferred_count": len(deferred),
            "items": material[:5],
        }
    finally:
        _close(c, opened)


def summarize_import(conn: Optional[sqlite3.Connection] = None,
                     batches: Optional[list[dict]] = None) -> dict:
    """The complete import-result packet for the completion panel.

    Everything is derived from the database; safe to render on any
    rerun. Returns {"available": False} when there is nothing
    unreviewed to summarize.
    """
    c, opened = _conn(conn)
    try:
        if batches is None:
            batches = latest_unreviewed_batches(conn=c)
        if not batches:
            return {"available": False}

        batch_ids = [int(b["id"]) for b in batches]
        inserted = sum(int(b.get("rows_inserted") or 0) for b in batches)
        skipped = sum(int(b.get("rows_skipped") or 0) for b in batches)

        files = [{
            "filename": b.get("filename") or "?",
            "account_type": b.get("account_type") or "—",
            "period": b.get("statement_period") or "",
            "inserted": int(b.get("rows_inserted") or 0),
            "skipped": int(b.get("rows_skipped") or 0),
        } for b in batches]

        # Money added by these batches, with the standard plumbing
        # exclusions so the numbers match every other SpendShape total.
        from utils.financial_semantics import (
            sql_type_placeholders, transaction_types_for_role,
        )
        income_types = transaction_types_for_role("income")
        spending_types = transaction_types_for_role("spending")
        marks = ",".join("?" * len(batch_ids))
        row = c.execute(
            f"""
            SELECT
              SUM(CASE WHEN transaction_type IN
                    ({sql_type_placeholders(income_types)})
                  THEN ABS(amount) ELSE 0 END) AS income_added,
              SUM(CASE WHEN transaction_type IN
                    ({sql_type_placeholders(spending_types)})
                  THEN ABS(amount) ELSE 0 END) AS spending_added,
              MIN(transaction_date) AS first_date,
              MAX(transaction_date) AS last_date
            FROM transactions
            WHERE import_batch_id IN ({marks})
            """,
            (*income_types, *spending_types, *batch_ids),
        ).fetchone()

        months_touched = [r["m"] for r in c.execute(
            f"""
            SELECT DISTINCT strftime('%Y-%m', transaction_date) AS m
            FROM transactions WHERE import_batch_id IN ({marks})
            ORDER BY m
            """,
            batch_ids,
        ).fetchall() if r["m"]]

        from utils.analysis_period import supported_latest_date

        supported = supported_latest_date(conn=c)
        data_through = supported.isoformat() if supported else ""

        from utils.insights import statement_coverage
        cov = statement_coverage(conn=c) or {}
        complete = set(cov.get("complete_months") or [])
        months_complete = [m for m in months_touched if m in complete]
        months_partial = [m for m in months_touched if m not in complete]

        review = material_review_items(conn=c, batch_ids=batch_ids)

        return {
            "available": True,
            "batch_ids": batch_ids,
            "files": files,
            "inserted": inserted,
            "skipped": skipped,
            "duplicates_only": inserted == 0 and skipped > 0,
            "income_added": round(float(row["income_added"] or 0), 2),
            "spending_added": round(float(row["spending_added"] or 0), 2),
            "first_date": (row["first_date"] or "")[:10],
            "last_date": (row["last_date"] or "")[:10],
            "data_through": data_through,
            "months_touched": months_touched,
            "months_complete": months_complete,
            "months_partial": months_partial,
            "review": review,
            "imported_at": batches[0].get("imported_at") or "",
        }
    finally:
        _close(c, opened)

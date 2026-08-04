"""Safe repair and reset operations for Northstar Ledger's local database.

Legacy CSV batches imported before the signed-amount contract stored positive
magnitudes and incomplete derived semantics. Exact signs cannot be invented
from those rows, so repair requires the original statement file. The native
Settings workflow matches that file by its saved hash, previews every affected
row, creates a validated SQLite backup, and only then updates one transaction.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Optional


SIGNED_SEMANTICS_VERSION = 2
MANUAL_CATEGORY_SOURCES = frozenset({
    "user_edit", "user_bulk", "user_rule", "ai", "manual",
})


def _conn(conn: Optional[sqlite3.Connection] = None):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _needs_repair_sql(alias: str = "t") -> str:
    return (
        f"({alias}.id IS NOT NULL AND ("
        f"COALESCE(il.semantics_version,1) < {SIGNED_SEMANTICS_VERSION} "
        f"OR COALESCE({alias}.transaction_type,'') = '' "
        f"OR ({alias}.direction='credit' AND {alias}.amount < 0) "
        f"OR ({alias}.direction='debit' AND {alias}.amount > 0)))"
    )


def preview_legacy_data(
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Return a read-only inventory of imported batches needing repair."""
    c, close = _conn(conn)
    try:
        batches = [dict(row) for row in c.execute(f"""
            SELECT il.id AS batch_id, il.filename, il.imported_at,
                   COALESCE(il.semantics_version,1) AS semantics_version,
                   COUNT(t.id) AS row_count,
                   SUM(CASE WHEN {_needs_repair_sql()} THEN 1 ELSE 0 END)
                       AS affected_rows,
                   SUM(CASE WHEN t.manually_edited_at IS NOT NULL
                                  OR t.category_source IN
                                     ('user_edit','user_bulk','user_rule','ai','manual')
                            THEN 1 ELSE 0 END) AS manual_rows,
                   SUM(CASE WHEN t.amount < 0 THEN 1 ELSE 0 END)
                       AS signed_negative_rows,
                   MIN(t.account_ref) AS account_ref,
                   MIN(COALESCE(a.name,t.account_id,t.account_type))
                       AS account_name,
                   MIN(COALESCE(a.type,t.account_type)) AS account_type
            FROM import_log il
            LEFT JOIN transactions t ON t.import_batch_id=il.id
            LEFT JOIN accounts a ON a.id=t.account_ref
            GROUP BY il.id
            HAVING affected_rows > 0
            ORDER BY il.id
        """).fetchall()]
        flagged = int(c.execute(
            "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
        ).fetchone()[0])
        return {
            "needed": bool(batches),
            "batch_count": len(batches),
            "affected_rows": sum(int(row["affected_rows"] or 0) for row in batches),
            "manual_rows": sum(int(row["manual_rows"] or 0) for row in batches),
            "review_before": flagged,
            "requires_original_files": bool(batches),
            "batches": batches,
        }
    finally:
        if close:
            c.close()


def source_identity(tx: Mapping[str, Any]) -> tuple[str, str, float]:
    """Identity shared by old magnitude rows and newly parsed signed rows."""
    return (
        str(tx.get("transaction_date") or ""),
        str(tx.get("raw_description") or "").strip().casefold(),
        round(abs(float(tx.get("amount") or 0.0)), 2),
    )


def _source_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple, dict]:
    index: dict[tuple, dict] = {}
    signs: dict[tuple, int] = {}
    for raw in rows:
        row = dict(raw)
        key = source_identity(row)
        sign = 1 if float(row.get("amount") or 0) > 0 else -1
        if key in signs and signs[key] != sign:
            raise ValueError(
                "The selected statement contains indistinguishable rows with "
                "opposite signs. Northstar stopped before changing the database."
            )
        signs[key] = sign
        index.setdefault(key, row)
    return index


def plan_legacy_repair(
    source_batches: Mapping[int, Iterable[Mapping[str, Any]]],
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Match selected source rows to legacy batches without writing anything."""
    c, close = _conn(conn)
    try:
        legacy = preview_legacy_data(conn=c)
        planned = []
        selected = {int(key): _source_index(value)
                    for key, value in source_batches.items()}
        for batch in legacy["batches"]:
            batch_id = int(batch["batch_id"])
            rows = [dict(row) for row in c.execute(
                "SELECT * FROM transactions WHERE import_batch_id=? ORDER BY id",
                (batch_id,),
            ).fetchall()]
            index = selected.get(batch_id, {})
            matched = sum(1 for row in rows if source_identity(row) in index)
            planned.append({
                **batch,
                "source_selected": batch_id in selected,
                "matched_rows": matched,
                "unmatched_rows": len(rows) - matched,
                "ready": batch_id in selected and matched == len(rows),
            })
        return {
            **legacy,
            "ready": bool(planned) and all(row["ready"] for row in planned),
            "matched_rows": sum(int(row["matched_rows"]) for row in planned),
            "unmatched_rows": sum(int(row["unmatched_rows"]) for row in planned),
            "batches": planned,
        }
    finally:
        if close:
            c.close()


def _manual_row(row: Mapping[str, Any]) -> bool:
    return bool(row.get("manually_edited_at")) or str(
        row.get("category_source") or ""
    ) in MANUAL_CATEGORY_SOURCES


def _repaired_values(existing: dict, source: dict) -> dict[str, Any]:
    """Combine source truth with either current rules or a deliberate edit."""
    from utils.database import compute_dedup_hash, compute_dedup_hash_v2
    from utils.financial_semantics import (
        EXCLUDED_TYPES, classify_transaction_type,
    )

    amount = float(source.get("amount") or 0.0)
    direction = "credit" if amount > 0 else "debit"
    values: dict[str, Any] = {
        "amount": amount,
        "direction": direction,
        "merchant": source.get("merchant") or existing.get("merchant") or "",
        "merchant_normalized": (
            source.get("merchant_normalized")
            or existing.get("merchant_normalized") or ""
        ),
    }
    if _manual_row(existing):
        tx_type = classify_transaction_type(
            str(existing.get("raw_description") or ""),
            str(existing.get("account_type") or ""),
            direction,
            str(existing.get("category") or ""),
        )
        values.update({
            "transaction_type": tx_type,
            "is_transfer": 1 if tx_type in EXCLUDED_TYPES else 0,
            "is_flagged": 0,
            "flag_reason": "",
        })
    else:
        for name in (
            "category", "subcategory", "category_source", "category_rule_id",
            "category_explanation", "parse_confidence", "is_transfer",
            "is_flagged", "flag_reason", "transaction_type",
        ):
            values[name] = source.get(name)
        values["category_updated_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    if existing.get("account_ref"):
        values["dedup_hash"] = compute_dedup_hash_v2(
            int(existing["account_ref"]),
            str(existing.get("transaction_date") or ""),
            str(existing.get("raw_description") or ""),
            amount,
        )
    else:
        values["dedup_hash"] = compute_dedup_hash(
            str(existing.get("account_type") or ""),
            str(existing.get("transaction_date") or ""),
            str(existing.get("raw_description") or ""),
            amount,
        )
    return values


def repair_legacy_data(
    source_batches: Mapping[int, Iterable[Mapping[str, Any]]],
    *,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Back up, then atomically repair every detected legacy batch."""
    from utils import database
    from utils.backups import create_backup

    target = Path(db_path or database.DB_PATH).resolve()
    before_conn = database.get_connection()
    try:
        plan = plan_legacy_repair(source_batches, conn=before_conn)
    finally:
        before_conn.close()
    if not plan["needed"]:
        return {**plan, "repaired": False, "message": "No legacy data needs repair."}
    if not plan["ready"]:
        raise ValueError(
            "Repair preview is incomplete. Choose the original file for every "
            "listed batch; Northstar has not changed the database."
        )

    backup_path = create_backup(reason="before-data-repair", db_path=target)
    conn = database.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        review_before = int(conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
        ).fetchone()[0])
        selected = {int(key): _source_index(value)
                    for key, value in source_batches.items()}
        updates: list[tuple[int, dict[str, Any], bool]] = []
        for batch in plan["batches"]:
            batch_id = int(batch["batch_id"])
            index = selected[batch_id]
            for raw in conn.execute(
                "SELECT * FROM transactions WHERE import_batch_id=? ORDER BY id",
                (batch_id,),
            ).fetchall():
                existing = dict(raw)
                source = index.get(source_identity(existing))
                if source is None:
                    raise ValueError(
                        "A source row stopped matching during repair. The "
                        "transaction was rolled back."
                    )
                updates.append((
                    int(existing["id"]),
                    _repaired_values(existing, source),
                    _manual_row(existing),
                ))

        new_hashes = [values["dedup_hash"] for _, values, _ in updates]
        if len(new_hashes) != len(set(new_hashes)):
            raise ValueError(
                "The repaired rows would create duplicate transaction keys. "
                "Northstar rolled back without changing the database."
            )
        ids = {row_id for row_id, _, _ in updates}
        placeholders = ",".join("?" for _ in new_hashes)
        conflicts = conn.execute(
            f"SELECT id FROM transactions WHERE dedup_hash IN ({placeholders})",
            new_hashes,
        ).fetchall() if new_hashes else []
        if any(int(row["id"]) not in ids for row in conflicts):
            raise ValueError(
                "A corrected transaction already exists. Northstar rolled "
                "back instead of double-counting it."
            )

        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        for row_id, _, _ in updates:
            conn.execute(
                "UPDATE transactions SET dedup_hash=? WHERE id=?",
                (f"repair:{stamp}:{row_id}", row_id),
            )
        signs_changed = categories_changed = 0
        for row_id, values, _manual in updates:
            before = conn.execute(
                "SELECT amount,category FROM transactions WHERE id=?", (row_id,)
            ).fetchone()
            signs_changed += int(float(before["amount"] or 0) != values["amount"])
            categories_changed += int(
                str(before["category"] or "") != str(values.get("category") or before["category"] or "")
            )
            sets = ", ".join(f"{name}=?" for name in values)
            conn.execute(
                f"UPDATE transactions SET {sets} WHERE id=?",
                [*values.values(), row_id],
            )
        batch_ids = [int(row["batch_id"]) for row in plan["batches"]]
        conn.executemany(
            "UPDATE import_log SET semantics_version=? WHERE id=?",
            [(SIGNED_SEMANTICS_VERSION, batch_id) for batch_id in batch_ids],
        )
        review_after = int(conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
        ).fetchone()[0])
        details = {
            "signs_changed": signs_changed,
            "categories_changed": categories_changed,
            "batch_ids": batch_ids,
        }
        conn.execute(
            "INSERT INTO data_repair_log "
            "(repair_kind,backup_path,batches_affected,rows_affected,"
            "manual_rows_kept,review_before,review_after,details_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "legacy-signed-semantics", str(backup_path), len(batch_ids),
                len(updates), sum(1 for _, _, manual in updates if manual),
                review_before, review_after, json.dumps(details),
            ),
        )
        conn.commit()
        return {
            "repaired": True,
            "backup_path": str(backup_path),
            "backup_name": backup_path.name,
            "batches_repaired": len(batch_ids),
            "rows_repaired": len(updates),
            "manual_rows_preserved": sum(
                1 for _, _, manual in updates if manual
            ),
            "signs_changed": signs_changed,
            "categories_changed": categories_changed,
            "review_before": review_before,
            "review_after": review_after,
            "message": (
                f"Repaired {len(updates)} transactions across "
                f"{len(batch_ids)} imported batches."
            ),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


RESET_TABLES = (
    "goal_contributions", "goal_targets", "category_budget_targets",
    "monthly_plans", "review_log", "statement_summaries", "transactions",
    "import_log", "investment_positions", "investment_snapshot_batches",
    "net_worth_snapshots", "account_balances", "investments",
    "contributions", "learned_rules", "import_profiles", "accounts",
    "budgets", "profiles", "watch_list", "recommendations_log",
    "recurring_preferences", "income_source_preferences",
    "net_worth_estimates", "shared_expense_rules", "shared_expense_settings",
    # Monthly net worth readings are financial data, and the most
    # personal figures in the app. Leaving them out of the reset meant
    # "remove my financial data" quietly kept them.
    "net_worth_entries",
)


def reset_preview(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    c, close = _conn(conn)
    try:
        counts = {
            table: int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in RESET_TABLES
        }
        return {
            "transaction_count": counts["transactions"],
            "import_count": counts["import_log"],
            "account_count": counts["accounts"],
            "plan_count": counts["monthly_plans"],
            "goal_count": counts["goal_targets"],
            "balance_count": counts["account_balances"],
            # Named separately rather than folded into balances: a monthly
            # net worth reading is typed by hand and cannot be recovered by
            # re-importing a statement, so the confirmation has to say how
            # many are about to go.
            "net_worth_reading_count": counts["net_worth_entries"],
            "rule_count": counts["learned_rules"] + counts["import_profiles"],
            "counts": counts,
        }
    finally:
        if close:
            c.close()


def reset_all_financial_data(
    *, complete_reset: bool = False, db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Back up, then atomically remove all financial/user-model data."""
    from utils import database
    from utils.backups import create_backup

    target = Path(db_path or database.DB_PATH).resolve()
    backup_path = create_backup(reason="before-financial-reset", db_path=target)
    conn = database.get_connection()
    try:
        before = reset_preview(conn=conn)
        conn.execute("BEGIN IMMEDIATE")
        for table in RESET_TABLES:
            conn.execute(f"DELETE FROM {table}")
        existing_sequences = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name='sqlite_sequence'"
            ).fetchall()
        }
        if existing_sequences:
            marks = ",".join("?" for _ in RESET_TABLES)
            conn.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({marks})",
                RESET_TABLES,
            )
        conn.execute(
            "INSERT INTO data_repair_log "
            "(repair_kind,backup_path,batches_affected,rows_affected,details_json) "
            "VALUES (?,?,?,?,?)",
            (
                "complete-reset" if complete_reset else "financial-reset",
                str(backup_path), before["import_count"],
                before["transaction_count"],
                json.dumps({"complete_reset": bool(complete_reset)}),
            ),
        )
        conn.commit()
        return {
            "reset": True,
            "complete_reset": bool(complete_reset),
            "backup_path": str(backup_path),
            "backup_name": backup_path.name,
            "removed": before,
            "message": (
                f"Removed {before['transaction_count']} transactions and "
                f"{before['account_count']} accounts. A restore point was kept."
            ),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

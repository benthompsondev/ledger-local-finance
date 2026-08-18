"""One-request JSON bridge from the Tauri shell to Ledger's Python engine.

The process reads exactly one JSON request from stdin, writes exactly one JSON
response to stdout, and exits. It never opens a socket or launches a browser.

Every action is narrow and typed: the Rust shell exposes one Tauri command per
action, so the frontend can never send an arbitrary request. All finance logic
stays in the existing utils/parsers modules — nothing is re-implemented here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

MAX_IMPORT_FILES = 12
PREVIEW_SAMPLE_ROWS = 12
DEFAULT_TX_LIMIT = 250
MAX_PLAN_AMOUNT = 1_000_000_000.0


def _data_root() -> Path:
    configured = os.environ.get("LEDGER_DATA_DIR", "").strip()
    if not configured:
        raise RuntimeError("LEDGER_DATA_DIR was not supplied by the desktop shell.")
    return Path(configured).expanduser().resolve()


def _logger(data_root: Path) -> logging.Logger:
    log_dir = data_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ledger.native.engine")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(
        log_dir / "native-engine.log", encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _bind_database() -> None:
    """Point Ledger's database module at the desktop data directory.

    Ledger resolves DB_PATH at import time for normal app performance. The
    desktop bridge makes its process boundary explicit so isolated launches
    and tests always use the directory supplied by the Tauri shell.
    """
    from utils import database
    from utils.analysis_period import reset_supported_bounds_cache

    filename = "finance.demo.db" if database.is_demo_mode() else "finance.db"
    database.DB_PATH = _data_root() / filename
    database.init_db()
    # One request per process, so this is the request boundary. Clearing here
    # keeps the analysis-anchor memo scoped to a single request and stops it
    # carrying stale bounds across requests in long-lived test processes.
    reset_supported_bounds_cache()


def _connection():
    from utils import database

    return database.get_connection()


def _period_bounds(params: dict[str, Any], conn) -> tuple[int, str, str, str]:
    """Resolve the shared rolling analysis period from the latest local row."""
    from datetime import date, timedelta

    from utils.analysis_period import supported_latest_date

    days = int(params.get("period_days") or 30)
    if days not in {30, 90}:
        raise ValueError("Choose either the last 30 days or last 90 days.")
    # The window ends where the data genuinely reaches. Ending it at
    # MAX(transaction_date) meant one row dated years ahead put the whole
    # rolling period somewhere with almost nothing in it.
    end = supported_latest_date(conn=conn) or date.today()
    start = end - timedelta(days=days - 1)
    return days, start.isoformat(), end.isoformat(), f"Last {days} days"


def _period_breakdown(params: dict[str, Any], conn) -> dict[str, Any]:
    """One backend-owned dataset used by Home and Insights."""
    from utils.analytics import (
        compute_cashflow, income_by_source, spending_by_category,
        stable_income_summary, top_merchants,
    )

    days, start, end, label = _period_bounds(params, conn)
    # One visible amount basis: full posted value unless that transaction has
    # an explicit user split. Legacy automatic view parameters are ignored.
    share_view = "personal"
    cashflow = compute_cashflow(start, end, share_view=share_view, conn=conn)
    categories = spending_by_category(
        start, end, share_view=share_view, conn=conn
    )
    income_sources = income_by_source(start, end, conn=conn)
    return {
        "period_days": days,
        "period_label": label,
        "period_start": start,
        "period_end": end,
        "cashflow": cashflow,
        "categories": categories,
        "income_sources": income_sources,
        "income_total": round(sum(x["total"] for x in income_sources), 2),
        "stable_income": stable_income_summary(conn=conn),
        "merchants": top_merchants(
            start, end, limit=10, share_view=share_view, conn=conn
        ),
        "share_view": share_view,
    }


def _cashflow_comparison(
    period: dict[str, Any], conn, share_view: str = "personal",
) -> dict[str, Any]:
    """Return a like-for-like prior cash-flow comparison.

    Prefer the selected rolling period when coverage is complete enough. If
    that history is unavailable, compare this month through the latest
    imported day with the previous month through the same day. A partial
    month is never compared with a full month.
    """
    import calendar
    from datetime import date, timedelta

    from utils.analytics import compute_cashflow

    start = date.fromisoformat(period["period_start"])
    end = date.fromisoformat(period["period_end"])
    days = int(period["period_days"])
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)
    coverage = conn.execute(
        "SELECT COUNT(*) AS n,MIN(transaction_date) AS first_date,"
        "MAX(transaction_date) AS last_date FROM transactions "
        "WHERE transaction_date BETWEEN ? AND ?",
        (prior_start.isoformat(), prior_end.isoformat()),
    ).fetchone()
    rolling_available = bool(
        coverage and int(coverage["n"] or 0)
        and date.fromisoformat(coverage["first_date"])
            <= prior_start + timedelta(days=7)
        and date.fromisoformat(coverage["last_date"])
            >= prior_end - timedelta(days=7)
    )
    if rolling_available:
        return {
            "available": True,
            "basis": "rolling",
            "current": period["cashflow"],
            "prior": compute_cashflow(
                prior_start.isoformat(), prior_end.isoformat(),
                share_view=share_view, conn=conn,
            ),
            "label": f"Previous {days} days",
            "prior_start": prior_start.isoformat(),
            "prior_end": prior_end.isoformat(),
        }

    current_start = end.replace(day=1)
    previous_month_end = current_start - timedelta(days=1)
    cap_day = min(end.day, calendar.monthrange(
        previous_month_end.year, previous_month_end.month,
    )[1])
    previous_same_day = previous_month_end.replace(day=cap_day)
    previous_month_start = previous_month_end.replace(day=1)
    current_same_day = end.replace(day=cap_day)
    prior_activity = conn.execute(
        "SELECT 1 FROM transactions WHERE transaction_date BETWEEN ? AND ? "
        "LIMIT 1",
        (previous_month_start.isoformat(), previous_same_day.isoformat()),
    ).fetchone()
    current_activity = conn.execute(
        "SELECT 1 FROM transactions WHERE transaction_date BETWEEN ? AND ? "
        "LIMIT 1",
        (current_start.isoformat(), current_same_day.isoformat()),
    ).fetchone()
    available = bool(prior_activity and current_activity)
    month_name = previous_month_start.strftime("%B")
    return {
        "available": available,
        "basis": "month_to_date",
        "current": compute_cashflow(
            current_start.isoformat(), current_same_day.isoformat(),
            share_view=share_view, conn=conn,
        ) if available else period["cashflow"],
        "prior": compute_cashflow(
            previous_month_start.isoformat(), previous_same_day.isoformat(),
            share_view=share_view, conn=conn,
        ) if available else {"net": 0.0},
        "label": (
            f"This month through day {cap_day} vs {month_name} through day "
            f"{cap_day}" if available else
            "No equivalent earlier period is complete enough"
        ),
        "prior_start": previous_month_start.isoformat(),
        "prior_end": previous_same_day.isoformat(),
    }


# ── Actions ─────────────────────────────────────────────────────────────


def home_summary(params: dict[str, Any]) -> dict[str, Any]:
    """Return the existing deterministic Home packet without Streamlit."""
    from utils.analysis_period import analysis_context
    from utils.checkin import weekly_checkin

    conn = _connection()
    try:
        packet = weekly_checkin(conn=conn)
        packet["analysis"] = analysis_context(conn=conn)
        period = _period_breakdown(params, conn)
        packet["period_days"] = period["period_days"]
        packet["period_label"] = period["period_label"]
        packet["period_start"] = period["period_start"]
        packet["period_end"] = period["period_end"]
        packet["progress"]["mtd_income"] = period["cashflow"]["income"]
        packet["progress"]["mtd_spending"] = period["cashflow"]["spending"]
        packet["progress"]["payroll_received"] = round(float(conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions "
            "WHERE transaction_type='employment_income' "
            "AND transaction_date BETWEEN ? AND ?",
            (period["period_start"], period["period_end"]),
        ).fetchone()[0] or 0), 2)
        packet["progress"]["shared_reimbursements"] = period["cashflow"].get(
            "shared_reimbursements", 0
        )
        net = round(float(period["cashflow"].get("net") or 0), 2)
        payroll = float(packet["progress"]["payroll_received"] or 0)
        non_payroll = round(float(period["cashflow"].get("income") or 0) - payroll, 2)
        packet["progress"].update({
            "net_cash_flow": net,
            "amount_kept": round(max(0.0, net), 2),
            "shortfall": round(max(0.0, -net), 2),
            "non_payroll_inflow": non_payroll,
            "show_payroll_breakout": bool(
                payroll > 0
                and abs(non_payroll) >= max(
                    25.0, float(period["cashflow"].get("income") or 0) * 0.05
                )
            ),
        })
        comparison = _cashflow_comparison(period, conn)
        comparison_net = float(
            comparison["current"].get("net") or 0
        )
        prior_net = float(comparison["prior"].get("net") or 0)
        packet["period_result"] = {
            "label": "Amount kept" if net >= 0 else "Spent beyond Money In",
            "amount": round(abs(net), 2),
            "positive": net >= 0,
            "sentence": (
                f"You kept ${net:,.2f} during this period."
                if net >= 0 else
                f"Spending was ${abs(net):,.2f} higher than Money In during this period."
            ),
            "comparison_available": comparison["available"],
            "comparison_basis": comparison["basis"],
            "comparison_delta": round(comparison_net - prior_net, 2),
            "comparison_delta_abs": round(
                abs(comparison_net - prior_net), 2
            ),
            "comparison_direction": (
                "improved" if comparison_net >= prior_net else "lower"
            ),
            "comparison_label": comparison["label"],
            "prior_start": comparison["prior_start"],
            "prior_end": comparison["prior_end"],
        }
        packet["stable_income"] = period["stable_income"]
        action_routes = {
            "import_data": "add-data",
            "update_plan": "plan",
            "save_plan": "plan",
            "add_balances": "settings#planning-balances",
            "review_flagged": "transactions?quickReview=1",
            "slow_pace": "plan",
        }
        for action in packet.get("actions", []):
            action["screen"] = action_routes.get(
                action.get("id"),
                "transactions" if action.get("category") else "plan",
            )
        reason = str(packet.get("safe_to_spend", {}).get("reason") or "").lower()
        if "available for spending" in reason:
            setup_screen = "settings#spendable-accounts"
            setup_label = "Review spendable accounts"
        elif "balance" in reason:
            setup_screen = "settings#planning-balances"
            setup_label = "Add current balances"
        elif packet.get("freshness", {}).get("state") == "no_data":
            setup_screen = "add-data"
            setup_label = "Import a statement"
        else:
            setup_screen = "plan"
            setup_label = "Review forecast setup"
        packet["safe_to_spend"]["setup_screen"] = setup_screen
        packet["verdict"]["setup_steps"] = (
            [] if packet.get("safe_to_spend", {}).get("available")
            else [{"label": setup_label, "screen": setup_screen}]
        )
        return packet
    finally:
        conn.close()


def _account_types() -> list[dict[str, str]]:
    from utils.database import ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS

    return [
        {"value": t, "label": ACCOUNT_TYPE_LABELS.get(t, t)}
        for t in ACCOUNT_TYPES
    ]


def _accounts_payload(conn) -> list[dict[str, Any]]:
    from utils.database import ACCOUNT_TYPE_LABELS, list_accounts

    return [
        {
            "id": int(a["id"]),
            "name": a["name"],
            "type": a["type"],
            "type_label": ACCOUNT_TYPE_LABELS.get(a["type"], a["type"]),
            "institution": a.get("institution") or "",
            "currency": a.get("currency") or "CAD",
            "available_for_spending": bool(
                a.get("available_for_spending")
            ),
            "tx_count": int(a.get("tx_count") or 0),
            "last_activity": a.get("last_activity") or "",
        }
        for a in list_accounts(conn=conn)
        if not a.get("is_archived")
    ]


def list_accounts_action(_params: dict[str, Any]) -> dict[str, Any]:
    conn = _connection()
    try:
        return {
            "accounts": _accounts_payload(conn),
            "account_types": _account_types(),
        }
    finally:
        conn.close()


def create_account_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.database import create_account

    conn = _connection()
    try:
        account_id = create_account(
            {
                "name": str(params.get("name") or ""),
                "type": str(params.get("type") or ""),
                "institution": str(params.get("institution") or ""),
                "currency": str(params.get("currency") or "CAD"),
                "opening_balance": float(params.get("opening_balance") or 0.0),
            },
            conn=conn,
        )
        conn.commit()
        return {
            "created_id": int(account_id),
            "accounts": _accounts_payload(conn),
            "account_types": _account_types(),
        }
    finally:
        conn.close()


def set_account_spending_availability_action(
    params: dict[str, Any],
) -> dict[str, Any]:
    from utils.database import get_account, update_account

    account_id = int(params.get("account_id") or 0)
    conn = _connection()
    try:
        account = get_account(account_id, conn=conn)
        if not account:
            raise ValueError("Choose an existing account.")
        if account.get("type") in {
            "credit_card", "loan", "mortgage", "other_liability",
        } and bool(params.get("available")):
            raise ValueError("Debt accounts cannot be available for spending.")
        update_account(
            account_id,
            {"available_for_spending": bool(params.get("available"))},
            conn=conn,
        )
        conn.commit()
        return {"accounts": _accounts_payload(conn)}
    finally:
        conn.close()


def _parse_file(path: Path, detected: str, conn, *,
                account_type: str | None = None,
                account_id: str | None = None,
                csv_mapping: dict[str, Any] | None = None,
                date_format: str | None = None,
                bank: str | None = None) -> dict[str, Any]:
    """Parse one statement file with the existing parsers.

    CSVs first try a saved column-mapping profile (same behaviour as the
    Streamlit Add Data page); unmapped CSVs fall back to the generic parser.
    """
    if detected == "chequing":
        from parsers.tangerine_chequing import parse_pdf as parse_chequing
        return parse_chequing(path, statement_period=None)
    if detected == "savings":
        from parsers.tangerine_savings import parse_pdf as parse_savings
        return parse_savings(path, statement_period=None)
    if detected == "mastercard":
        from parsers.tangerine_mastercard import parse_pdf as parse_mc
        return parse_mc(path, statement_period=None)
    if detected == "csv":
        from parsers.csv_import import parse_csv
        profile = None
        if csv_mapping:
            from utils.csv_mapping import parse_csv_mapped
            return parse_csv_mapped(
                path, csv_mapping,
                account_type=account_type or "csv", account_id=account_id,
                statement_period=None, date_format=date_format, conn=conn,
            )
        profile = _csv_profile_for(path, conn, account_type=account_type)
        if profile:
            from utils.csv_mapping import parse_csv_mapped

            result = parse_csv_mapped(
                path, profile["mapping"],
                account_type=account_type or "csv", account_id=account_id,
                statement_period=None, date_format=date_format, conn=conn,
            )
            result["csv_profile"] = {
                "id": int(profile["id"]),
                "name": profile.get("name") or "",
                "suggested_account_ref":
                    profile.get("suggested_account_ref"),
                "account_type": profile.get("account_type") or "",
            }
            return result
        return parse_csv(
            path, account_type=account_type or "csv", account_id=account_id,
            statement_period=None, date_format=date_format, bank=bank,
            conn=conn,
        )
    raise ValueError(
        "This file type is not supported yet. Export a CSV from your bank, "
        "or use a Tangerine PDF statement."
    )


def _csv_profile_for(path: Path, conn, *,
                     account_type: str | None = None) -> dict[str, Any] | None:
    """Return the saved import profile matching this CSV's headers, if any."""
    try:
        from utils.csv_mapping import csv_preview
        headers = csv_preview(path, max_rows=0).get("headers") or []
    except Exception:
        return None
    if not headers:
        return None
    try:
        from utils.csv_mapping import find_import_profile

        return find_import_profile(headers, account_type=account_type, conn=conn)
    except ValueError:
        raise
    except Exception:
        return None


def _sample_rows(txs: list[dict]) -> list[dict[str, Any]]:
    sample = []
    for tx in txs[:PREVIEW_SAMPLE_ROWS]:
        sample.append(
            {
                "date": tx.get("transaction_date", ""),
                "description": (tx.get("raw_description", "") or "")[:60],
                "category": tx.get("category", ""),
                "amount": float(tx.get("amount") or 0),
                "direction": tx.get("direction", ""),
            }
        )
    return sample


def _preview_totals(txs: list[dict]) -> dict[str, Any]:
    """Summarize parsed rows using Ledger's cashflow exclusions."""
    from utils.financial_semantics import cashflow_totals
    dates = sorted(
        str(tx.get("transaction_date") or "")
        for tx in txs
        if tx.get("transaction_date")
    )
    totals = cashflow_totals(txs)
    return {
        "date_start": dates[0] if dates else "",
        "date_end": dates[-1] if dates else "",
        "income": totals["income"],
        "spending": totals["spending"],
    }


def _checked_paths(params: dict[str, Any]) -> list[Path]:
    raw_paths = params.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("No files were selected.")
    if len(raw_paths) > MAX_IMPORT_FILES:
        raise ValueError(
            f"Ledger imports at most {MAX_IMPORT_FILES} files at a time."
        )
    paths = []
    for raw in raw_paths:
        path = Path(str(raw))
        if not path.is_file():
            raise ValueError(f"File not found: {path.name or path}")
        paths.append(path)
    return paths


def _preview_specs(params: dict[str, Any]) -> list[dict[str, Any]]:
    raw_specs = params.get("files")
    if raw_specs is None:
        return [{"path": str(path)} for path in _checked_paths(params)]
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ValueError("No files were selected.")
    if len(raw_specs) > MAX_IMPORT_FILES:
        raise ValueError(
            f"Ledger imports at most {MAX_IMPORT_FILES} files at a time."
        )
    specs = []
    for raw in raw_specs:
        if not isinstance(raw, dict):
            raise ValueError("The import preview selection is invalid.")
        path = Path(str(raw.get("path") or ""))
        if not path.is_file():
            raise ValueError(f"File not found: {path.name or path}")
        mapping = raw.get("mapping")
        import_mode = str(raw.get("import_mode") or "").strip().lower()
        if import_mode and import_mode not in {"new", "reexport"}:
            raise ValueError("The statement overlap choice is invalid.")
        specs.append({
            "path": str(path),
            "account_id": raw.get("account_id"),
            "mapping": mapping if isinstance(mapping, dict) else None,
            # Set when the user resolves a genuinely ambiguous date column.
            "date_format": str(raw.get("date_format") or "") or None,
            # Set when the user names their bank explicitly.
            "bank": str(raw.get("bank") or "") or None,
            # Set only after the user resolves a partial statement overlap.
            "import_mode": import_mode or None,
        })
    return specs


def _csv_mapping_payload(path: Path) -> dict[str, Any]:
    from utils.csv_mapping import csv_preview, suggest_mapping

    preview = csv_preview(path, max_rows=5)
    return {
        "headers": preview.get("headers") or [],
        "rows": preview.get("rows") or [],
        "delimiter": preview.get("delimiter") or "",
        "encoding": preview.get("encoding") or "",
        "suggested": suggest_mapping(preview.get("headers") or []),
        "error": preview.get("error") or "",
    }


def _preview_duplicate_counts(
    txs: list[dict], account: dict[str, Any] | None, detected: str,
    conn, *, filename: str, file_hash: str, import_mode: str | None,
    exact_reimport: bool,
) -> tuple[int, int, str, bool]:
    """Run the real persistence path inside preview's rollback savepoint.

    This deliberately leaves simulated rows visible to the next selected file
    until the outer preview rolls back. Preview and confirm therefore use the
    same provenance decision, occurrence numbering, and selected-file order.
    """
    if account is None:
        return len(txs), 0, "pending", False
    from utils.account_assignment import (
        PDF_STATEMENT_TYPES, apply_account_to_transactions,
    )
    from utils.import_provenance import classify_import_provenance
    from utils.import_service import persist_import_batch

    normalized = [dict(tx) for tx in txs]
    apply_account_to_transactions(
        normalized, account,
        keep_account_type=(detected in PDF_STATEMENT_TYPES),
    )
    account_ref = next(
        (tx.get("account_ref") for tx in normalized if tx.get("account_ref")),
        None,
    )
    provenance = classify_import_provenance(
        conn,
        account_ref=int(account_ref) if account_ref else None,
        file_rows=normalized,
    )
    selected_mode = "reexport" if exact_reimport else import_mode
    if provenance == "ambiguous" and not selected_mode:
        return 0, 0, provenance, True
    saved = persist_import_batch(
        {
            "filename": filename,
            "file_hash": file_hash,
            "account_type": detected,
            "rows_parsed": len(normalized),
            "errors": [],
        },
        normalized,
        account_id=str(account.get("name") or ""),
        statement_account_type=detected,
        import_mode=selected_mode,
        conn=conn,
    )
    return (
        int(saved["inserted"]), int(saved["skipped"]), provenance, False,
    )


def preview_import_action(params: dict[str, Any]) -> dict[str, Any]:
    """Detect, parse, and summarize the selected files without persisting."""
    from parsers.detect import detect_label, detect_with_confidence
    from utils.account_assignment import DETECTED_TYPE_TO_ACCOUNT_TYPE

    conn = _connection()
    try:
        # The real import path is used below to predict exact counts. Keep all
        # simulated batches in one savepoint so later selected files see the
        # earlier ones, then discard the entire preview before returning.
        conn.execute("SAVEPOINT preview_import_action")
        existing_hashes = {
            r[0]
            for r in conn.execute(
                "SELECT file_hash FROM import_log WHERE file_hash IS NOT NULL"
            ).fetchall()
        }
        files = []
        for spec in _preview_specs(params):
            path = Path(spec["path"])
            account_id = spec.get("account_id")
            account = None
            if account_id:
                from utils.database import get_account
                account = get_account(int(account_id), conn=conn)
                if not account or account.get("is_archived"):
                    raise ValueError(
                        f"The selected account for “{path.name}” is unavailable."
                    )
            raw = path.read_bytes()
            detected, confidence = detect_with_confidence(path)
            entry: dict[str, Any] = {
                "path": str(path),
                "filename": path.name,
                "detected": detected,
                "label": detect_label(detected),
                "confidence": confidence,
                "already_imported":
                    hashlib.md5(raw).hexdigest() in existing_hashes,
                "suggested_account_type":
                    DETECTED_TYPE_TO_ACCOUNT_TYPE.get(detected, ""),
                "suggested_account_ref": None,
                "csv_profile_account_type": "",
                "csv_profile_id": None,
                "needs_mapping": False,
                "blocked": False,
                "receipt": None,
                "date_format_choices": [],
                "bank_choices": [],
                "mapping": None,
                "mapping_info": None,
                "dedup_pending": account is None,
                "dedup_choice_required": False,
                "provenance_status": "pending" if account is None else "new",
                "provenance_note": "",
                "tx_count": 0,
                "new_transaction_count": 0,
                "duplicate_count": 0,
                "statement_period": "",
                "date_start": "",
                "date_end": "",
                "income": 0.0,
                "spending": 0.0,
                "sample": [],
                "errors": [],
                "error": "",
            }
            try:
                prior = None
                if account is not None and entry["already_imported"]:
                    prior = conn.execute(
                        "SELECT t.account_ref FROM import_log il "
                        "LEFT JOIN transactions t ON t.import_batch_id=il.id "
                        "WHERE il.file_hash=? AND t.account_ref IS NOT NULL "
                        "ORDER BY il.imported_at DESC LIMIT 1",
                        (hashlib.md5(raw).hexdigest(),),
                    ).fetchone()
                    if prior and int(prior["account_ref"]) != int(account["id"]):
                        from utils.database import get_account
                        prior_account = get_account(
                            int(prior["account_ref"]), conn=conn,
                        )
                        raise ValueError(
                            f"This exact file was already imported into “"
                            f"{(prior_account or {}).get('name') or 'another account'}”. "
                            "Choose that account to check duplicates, or use a fresh export."
                        )
                result = _parse_file(
                    path, detected, conn,
                    account_type=(account or {}).get("type"),
                    account_id=(account or {}).get("name"),
                    csv_mapping=spec.get("mapping"),
                    date_format=spec.get("date_format") or None,
                    bank=spec.get("bank") or None,
                )
                txs = result.get("transactions", [])
                entry["tx_count"] = len(txs)
                entry.update(_preview_totals(txs))
                entry["blocked"] = bool(result.get("blocked"))
                if entry["blocked"]:
                    new_count, duplicate_count = 0, 0
                    provenance, choice_required = "blocked", False
                else:
                    (
                        new_count, duplicate_count, provenance,
                        choice_required,
                    ) = _preview_duplicate_counts(
                        txs, account, detected, conn,
                        filename=path.name,
                        file_hash=hashlib.md5(raw).hexdigest(),
                        import_mode=spec.get("import_mode"),
                        exact_reimport=prior is not None,
                    )
                entry["new_transaction_count"] = new_count
                entry["duplicate_count"] = duplicate_count
                entry["provenance_status"] = provenance
                entry["dedup_choice_required"] = choice_required
                if choice_required:
                    entry["provenance_note"] = (
                        "Some rows match an earlier import, but not enough to "
                        "tell whether this is an updated export or separate "
                        "activity. Northstar will not guess."
                    )
                entry["statement_period"] = result.get("statement_period", "") or ""
                entry["sample"] = _sample_rows(txs)
                entry["errors"] = [str(e) for e in (result.get("errors") or [])[:8]]
                profile = result.get("csv_profile") or {}
                if profile.get("suggested_account_ref"):
                    entry["suggested_account_ref"] = int(
                        profile["suggested_account_ref"]
                    )
                entry["csv_profile_account_type"] = str(
                    profile.get("account_type") or ""
                )
                if profile.get("id"):
                    entry["csv_profile_id"] = int(profile["id"])
                entry["receipt"] = result.get("receipt")
                entry["date_format_choices"] = (
                    result.get("date_format_choices") or []
                )
                if entry["blocked"] or entry["needs_mapping"]:
                    # Naming the bank is usually quicker than mapping columns
                    # by hand, so offer it whenever a file needs help.
                    from parsers.bank_presets import preset_choices
                    entry["bank_choices"] = preset_choices()
                # The parser fails closed: when it could not settle the
                # file's date, amount or column conventions it says so, and
                # nothing may be confirmed until the user resolves it.
                if detected == "csv":
                    mapping_payload = _csv_mapping_payload(path)
                    entry["mapping_info"] = mapping_payload
                    entry["mapping"] = spec.get("mapping")
                    auto_understood = bool(txs) and len(txs) >= len(
                        result.get("errors") or []
                    )
                    entry["needs_mapping"] = bool(
                        result.get("needs_mapping")
                    ) or bool(
                        mapping_payload.get("headers")
                        and not profile
                        and not spec.get("mapping")
                        and not auto_understood
                    )
                if entry["blocked"]:
                    problems = (result.get("receipt") or {}).get("problems") or []
                    entry["error"] = problems[0] if problems else (
                        "Northstar could not read this file safely."
                    )
                elif not txs:
                    if entry["needs_mapping"]:
                        entry["error"] = ""
                    else:
                        entry["error"] = (
                            "No transactions were found. Check the file or "
                            "choose which CSV columns contain each value."
                        )
            except Exception as exc:
                entry["error"] = str(exc)
            files.append(entry)
        return {
            "files": files,
            "accounts": _accounts_payload(conn),
            "account_types": _account_types(),
        }
    finally:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT preview_import_action")
            conn.execute("RELEASE SAVEPOINT preview_import_action")
        except Exception:
            # Closing the connection below is still a rollback. Do not hide
            # the original parser or validation error with cleanup noise.
            pass
        conn.close()


def apply_csv_mapping_action(params: dict[str, Any]) -> dict[str, Any]:
    """Validate a native mapping, optionally remember it, then preview it."""
    from utils.csv_mapping import csv_preview, save_import_profile, validate_mapping
    from utils.database import get_account

    path = Path(str(params.get("path") or ""))
    mapping = params.get("mapping")
    account_id = int(params.get("account_id") or 0)
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise ValueError("Choose the CSV again before mapping its columns.")
    if not isinstance(mapping, dict):
        raise ValueError("Choose the date, description, and amount columns.")
    conn = _connection()
    try:
        account = get_account(account_id, conn=conn)
        if not account or account.get("is_archived"):
            raise ValueError("Choose the account these transactions came from.")
        preview = csv_preview(path, max_rows=5)
        problems = validate_mapping(mapping, preview.get("headers") or [])
        if problems:
            raise ValueError(" ".join(problems))
        if bool(params.get("save_profile")):
            save_import_profile(
                str(params.get("profile_name") or path.stem),
                preview.get("headers") or [], mapping,
                account_type=str(account.get("type") or ""),
                suggested_account_ref=account_id,
                conn=conn,
            )
            conn.commit()
        return preview_import_action({
            "files": [{
                "path": str(path), "account_id": account_id,
                "mapping": mapping,
            }]
        })
    finally:
        conn.close()


def confirm_import_action(params: dict[str, Any]) -> dict[str, Any]:
    """Re-parse and persist the confirmed files, one atomic batch per file."""
    from parsers.detect import detect_with_confidence, detect_label
    from utils.account_assignment import (
        PDF_STATEMENT_TYPES, apply_account_to_transactions,
    )
    from utils.database import get_account
    from utils.import_service import persist_import_batch

    raw_files = params.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("No files were confirmed for import.")
    if len(raw_files) > MAX_IMPORT_FILES:
        raise ValueError(
            f"Ledger imports at most {MAX_IMPORT_FILES} files at a time."
        )

    conn = _connection()
    try:
        results = []
        totals = {"inserted": 0, "skipped": 0, "flagged": 0}
        for spec in raw_files:
            path = Path(str(spec.get("path") or ""))
            if not path.is_file():
                raise ValueError(f"File not found: {path.name or path}")
            account_id = spec.get("account_id")
            if not account_id:
                raise ValueError(
                    f"“{path.name}” has no destination account. Every import "
                    "needs one so balances stay trustworthy."
                )
            account = get_account(int(account_id), conn=conn)
            if not account or account.get("is_archived"):
                raise ValueError(
                    f"The destination account for “{path.name}” no longer "
                    "exists or is archived."
                )

            raw = path.read_bytes()
            file_hash = hashlib.md5(raw).hexdigest()
            # The same exact file may be re-imported into ITS OWN account
            # (row-level dedup then calmly skips everything), but never into
            # a different one: account-aware duplicate detection would treat
            # every row as new there and double-count the whole statement.
            prior = conn.execute(
                """
                SELECT t.account_ref, il.imported_at
                FROM import_log il
                LEFT JOIN transactions t ON t.import_batch_id = il.id
                WHERE il.file_hash = ?
                  AND t.account_ref IS NOT NULL
                ORDER BY il.imported_at DESC LIMIT 1
                """,
                (file_hash,),
            ).fetchone()
            if (
                prior is not None
                and prior["account_ref"] is not None
                and int(prior["account_ref"]) != int(account_id)
            ):
                prior_account = get_account(int(prior["account_ref"]), conn=conn)
                prior_name = (prior_account or {}).get("name") or "another account"
                raise ValueError(
                    f"“{path.name}” was already imported into "
                    f"“{prior_name}”. Importing the exact same file into "
                    f"“{account['name']}” would double-count every "
                    "transaction. Re-import it into "
                    f"“{prior_name}”, or export a fresh statement for "
                    f"“{account['name']}”."
                )

            detected, _confidence = detect_with_confidence(path)
            result = _parse_file(
                path, detected, conn,
                account_type=account["type"], account_id=account["name"],
                csv_mapping=(
                    spec.get("mapping")
                    if isinstance(spec.get("mapping"), dict) else None
                ),
                date_format=spec.get("date_format") or None,
                bank=spec.get("bank") or None,
            )
            txs = result.get("transactions", [])
            if result.get("blocked"):
                # The parser could not settle this file's conventions. Saving
                # it anyway is how wrong dates and hundredfold amounts got
                # into the database in the first place.
                problems = (result.get("receipt") or {}).get("problems") or []
                detail = problems[0] if problems else (
                    "Northstar could not read this file safely."
                )
                raise ValueError(
                    f"“{path.name}” was not imported. {detail}"
                )
            if not txs:
                raise ValueError(
                    f"No transactions were parsed from “{path.name}”, so "
                    "nothing was imported from it."
                )
            apply_account_to_transactions(
                txs, account,
                keep_account_type=(detected in PDF_STATEMENT_TYPES),
            )
            from utils.financial_semantics import (
                consolidate_material_review, promote_recurring_income,
            )
            promote_recurring_income(txs)
            consolidate_material_review(txs)
            stats = result.get("stats", {})
            errors = result.get("errors", [])
            saved = persist_import_batch(
                {
                    "filename": path.name,
                    "file_hash": file_hash,
                    "account_type": detect_label(detected),
                    "statement_period": result.get("statement_period", ""),
                    "rows_parsed": stats.get("total_parsed", len(txs)),
                    "errors": [str(e) for e in errors],
                },
                txs,
                account_id=account["name"],
                statement_summary=result.get("statement_summary") or None,
                statement_account_type=detected,
                import_mode=(
                    "reexport" if prior is not None
                    else spec.get("import_mode")
                ),
                conn=conn,
            )
            conn.commit()
            totals["inserted"] += saved["inserted"]
            totals["skipped"] += saved["skipped"]
            totals["flagged"] += saved["flagged"]
            results.append(
                {
                    "filename": path.name,
                    "account_name": account["name"],
                    "batch_id": saved["batch_id"],
                    "inserted": saved["inserted"],
                    "skipped": saved["skipped"],
                    "flagged": saved["flagged"],
                    "provenance": saved["provenance"],
                    "import_mode": saved["import_mode"],
                }
            )
        return {"results": results, "totals": totals}
    finally:
        conn.close()


def reset_csv_profile_action(params: dict[str, Any]) -> dict[str, Any]:
    """Delete the saved profile matching one explicitly selected CSV."""
    from utils.csv_mapping import csv_preview, header_signature

    path = Path(str(params.get("path") or ""))
    if not path.is_file():
        raise ValueError("Choose the CSV again before resetting its profile.")
    headers = csv_preview(path, max_rows=0).get("headers") or []
    if not headers:
        raise ValueError("That CSV has no readable header row.")
    conn = _connection()
    try:
        deleted = conn.execute(
            "DELETE FROM import_profiles WHERE header_signature=?",
            (header_signature(headers),),
        ).rowcount
        conn.commit()
        return {"reset": bool(deleted)}
    finally:
        conn.close()


def _transaction_row(row: dict[str, Any], conn) -> dict[str, Any]:
    posted_amount = float(row.get("amount") or 0)
    is_etransfer = "INTERAC E-TRANSFER" in str(
        row.get("raw_description") or ""
    ).upper()
    from utils.shared_expenses import shared_allocation
    allocation = shared_allocation(row, conn)
    effective_amount = round(
        allocation["user_share"] if posted_amount >= 0
        else -allocation["user_share"],
        2,
    )
    return {
        "id": int(row["id"]),
        "date": row.get("transaction_date") or "",
        "account_name": row.get("account_name") or "",
        "merchant": row.get("merchant") or "",
        "description": row.get("raw_description") or "",
        "raw_description": row.get("raw_description") or "",
        "category": row.get("category") or "",
        "category_source": row.get("category_source") or "",
        "amount": effective_amount,
        "posted_amount": posted_amount,
        "direction": row.get("direction") or "",
        "flow_label": (
            "E-transfer in" if is_etransfer and posted_amount > 0
            else "E-transfer out" if is_etransfer and posted_amount < 0
            else "Money in" if posted_amount > 0 else "Money out"
        ),
        "currency": row.get("currency") or "CAD",
        "notes": row.get("notes") or "",
        "is_flagged": bool(row.get("is_flagged")),
        "flag_reason": row.get("flag_reason") or "",
        "transaction_type": row.get("transaction_type") or "",
        "suggested_category": row.get("suggested_category") or "",
        "suggestion_reason": row.get("suggestion_reason") or "",
        "suggested_transaction_type": row.get("suggested_transaction_type") or "",
        "type_suggestion_reason": row.get("type_suggestion_reason") or "",
        "is_shared": allocation["is_shared"],
        "shared_source": allocation["shared_source"],
        "user_share_pct": allocation["user_share_pct"],
        "household_cost": allocation["household_cost"],
        "user_share": allocation["user_share"],
        "other_share": allocation["other_share"],
        "shared_expense_override": row.get("shared_expense_override"),
        "review_priority": float(row.get("review_priority") or 0),
        "review_reason": row.get("review_reason") or "",
    }


def _apply_category_suggestion(
    row: dict[str, Any], history: dict[str, tuple[str, int]], conn,
) -> dict[str, Any]:
    """Attach one deterministic, explainable suggestion to an unfiled row."""
    from utils.shared_expenses import partner_reimbursement_suggestion
    reimbursement = partner_reimbursement_suggestion(row, conn)
    if reimbursement:
        row["suggested_transaction_type"] = reimbursement["transaction_type"]
        row["type_suggestion_reason"] = reimbursement["reason"]
    if row.get("category") != "Uncategorized":
        return row
    from config.categories import SPENDING_CATEGORIES
    from utils.categorizer import categorize, statement_memo_category

    memo = statement_memo_category(
        str(row.get("statement_memo") or row.get("notes") or "")
    )
    if memo:
        row["suggested_category"] = memo[0]
        row["suggestion_reason"] = "Suggested from the bank statement memo."
        return row
    merchant_key = str(row.get("merchant_normalized") or "")
    if merchant_key in history:
        suggested, count = history[merchant_key]
        row["suggested_category"] = suggested
        row["suggestion_reason"] = (
            f"Used for this merchant {count} time(s) before."
        )
        return row
    suggested, _subcategory, confidence = categorize(
        str(row.get("raw_description") or ""),
        float(row.get("amount") or 0),
        str(row.get("direction") or ""),
    )
    if (
        suggested in SPENDING_CATEGORIES
        and suggested != "Uncategorized"
        and confidence >= 0.7
    ):
        row["suggested_category"] = suggested
        row["suggestion_reason"] = "Suggested by a high-confidence built-in merchant rule."
    return row


def _rank_review_rows(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Return the exact rows shown by a review mode, in display order.

    Quick Review counts and membership must share this function so the button
    never advertises a different number from the list it opens.
    """
    if mode not in {"quick", "suggested"}:
        raise ValueError(f"Unknown review mode: {mode}")

    merchant_impact: dict[str, float] = {}
    for row in rows:
        key = str(row.get("merchant_normalized") or "")
        if key:
            merchant_impact[key] = (
                merchant_impact.get(key, 0.0)
                + abs(float(row.get("amount") or 0))
            )

    if mode == "suggested":
        selected = [
            row for row in rows
            if row.get("suggested_category")
            or row.get("suggested_transaction_type")
        ]
    else:
        selected = [
            row for row in rows
            if (
                row.get("is_flagged")
                or row.get("suggested_category")
                or row.get("suggested_transaction_type")
                or (
                    row.get("category") == "Uncategorized"
                    and (
                        abs(float(row.get("amount") or 0)) >= 500
                        or (
                            bool(row.get("merchant_normalized"))
                            and merchant_impact.get(
                                str(row.get("merchant_normalized") or ""), 0.0
                            ) >= 500
                        )
                    )
                )
            )
        ]
        compact: list[dict[str, Any]] = []
        represented_merchants: set[str] = set()
        for row in selected:
            key = str(row.get("merchant_normalized") or "")
            direct = bool(
                row.get("is_flagged")
                or row.get("suggested_category")
                or row.get("suggested_transaction_type")
                or abs(float(row.get("amount") or 0)) >= 500
            )
            if direct or not key or key not in represented_merchants:
                compact.append(row)
                if key:
                    represented_merchants.add(key)
        selected = compact

    merchant_counts: dict[str, int] = {}
    for row in selected:
        key = str(row.get("merchant_normalized") or "")
        merchant_counts[key] = merchant_counts.get(key, 0) + 1
    for row in selected:
        impact = abs(float(row.get("amount") or 0))
        repeated = merchant_counts.get(str(row.get("merchant_normalized") or ""), 0)
        plan_affecting = str(row.get("transaction_type") or "") in {
            "employment_income", "mortgage_payment", "shared_reimbursement",
        } or impact >= 500
        row["review_priority"] = round(
            impact + repeated * 25
            + (250 if row.get("suggested_category") or row.get("suggested_transaction_type") else 0)
            + (500 if plan_affecting else 0), 2
        )
        row["review_reason"] = (
            "Affects Plan or Safe to Spend" if plan_affecting
            else "Repeated merchant with a suggestion" if repeated > 1
            else "Ready for a quick correction"
        )
    selected.sort(key=lambda row: -float(row.get("review_priority") or 0))
    return selected


def list_transactions_action(params: dict[str, Any]) -> dict[str, Any]:
    from config.categories import CATEGORIES

    limit = int(params.get("limit") or DEFAULT_TX_LIMIT)
    limit = max(1, min(limit, 1000))
    account_ref = params.get("account_ref")
    search = str(params.get("search") or "").strip()
    category = str(params.get("category") or "").strip()
    direction = str(params.get("direction") or "").strip().lower()
    start_date = str(params.get("start_date") or "").strip()
    end_date = str(params.get("end_date") or "").strip()
    offset = max(0, int(params.get("offset") or 0))
    sort = str(params.get("sort") or "date")
    descending = bool(params.get("descending", True))
    flagged_only = bool(params.get("flagged_only", False))
    suggested_only = bool(params.get("suggested_only", False))
    quick_review = bool(params.get("quick_review", False))
    cashflow_role = str(params.get("cashflow_role") or "").strip().lower()
    sort_columns = {
        "date": "transactions.transaction_date",
        "amount": "ABS(transactions.amount)",
        "description": "transactions.merchant",
        "category": "transactions.category",
    }
    order_column = sort_columns.get(sort, "transactions.transaction_date")
    if flagged_only:
        order_column = (
            "(SELECT SUM(ABS(t2.amount)) FROM transactions t2 "
            "WHERE t2.merchant_normalized=transactions.merchant_normalized "
            "AND t2.category='Uncategorized')"
        )
        descending = True
    elif suggested_only:
        order_column = (
            "(SELECT SUM(ABS(t2.amount)) FROM transactions t2 "
            "WHERE t2.merchant_normalized=transactions.merchant_normalized "
            "AND t2.category='Uncategorized')"
        )
        descending = True
    order_direction = "DESC" if descending else "ASC"

    conn = _connection()
    try:
        clauses: list[str] = []
        args: list[Any] = []
        from utils.financial_semantics import (
            sql_type_placeholders, transaction_types_for_role,
        )
        values = transaction_types_for_role(cashflow_role)
        if account_ref:
            clauses.append("transactions.account_ref = ?")
            args.append(int(account_ref))
        if search:
            clauses.append(
                "(transactions.merchant LIKE ? OR "
                "transactions.raw_description LIKE ? OR "
                "transactions.notes LIKE ?)"
            )
            term = f"%{search}%"
            args.extend([term, term, term])
        if category:
            clauses.append("transactions.category = ?")
            args.append(category)
        if direction in {"debit", "credit"}:
            clauses.append("transactions.direction = ?")
            args.append(direction)
        if values:
            clauses.append(
                "transactions.transaction_type IN ("
                + sql_type_placeholders(values) + ")"
            )
            args.extend(values)
        if start_date:
            clauses.append("transactions.transaction_date >= ?")
            args.append(start_date)
        if end_date:
            clauses.append("transactions.transaction_date <= ?")
            args.append(end_date)
        if flagged_only:
            clauses.append("transactions.is_flagged = 1")
        if suggested_only:
            clauses.append("transactions.category = 'Uncategorized'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        base = (
            "FROM transactions LEFT JOIN accounts "
            "ON accounts.id = transactions.account_ref "
            f"{where}"
        )
        sql_total = int(conn.execute(
            f"SELECT COUNT(*) {base}", args
        ).fetchone()[0])
        row_sql = (
            "SELECT transactions.*, "
            "COALESCE(accounts.name, transactions.account_type) AS account_name "
            f"{base} ORDER BY {order_column} {order_direction}, "
            "transactions.id DESC"
        )
        rows = conn.execute(
            row_sql if (suggested_only or quick_review) else row_sql + " LIMIT ? OFFSET ?",
            tuple(args) if (suggested_only or quick_review) else (*args, limit, offset),
        ).fetchall()
        review = conn.execute("""
            SELECT SUM(CASE WHEN is_flagged=1 THEN 1 ELSE 0 END) AS high_priority,
                   SUM(CASE WHEN is_flagged=0 AND category='Uncategorized'
                             THEN 1 ELSE 0 END) AS deferred
            FROM transactions
        """).fetchone()
        spending_types = transaction_types_for_role("spending")
        history = {}
        for history_row in conn.execute(
            f"""
            SELECT merchant_normalized, category, COUNT(*) AS n
            FROM transactions
            WHERE category NOT IN ('Uncategorized','Misc')
              AND transaction_type IN ({sql_type_placeholders(spending_types)})
            GROUP BY merchant_normalized,category
            ORDER BY n DESC
            """,
            spending_types,
        ).fetchall():
            merchant_key = history_row["merchant_normalized"]
            if merchant_key:
                # The first row is the merchant's most-used category. Do not
                # let a later, less-common category overwrite the suggestion.
                history.setdefault(
                    merchant_key,
                    (history_row["category"], int(history_row["n"] or 0)),
                )
        decorated = [
            _apply_category_suggestion(dict(db_row), history, conn)
            for db_row in rows
        ]
        summary_rows: list[dict[str, Any]]
        if suggested_only or quick_review:
            decorated = _rank_review_rows(
                decorated, "suggested" if suggested_only else "quick"
            )
            total = len(decorated)
            summary_rows = [dict(row) for row in decorated]
            decorated = decorated[offset:offset + limit]
        else:
            total = sql_total
            summary_rows = [dict(row) for row in conn.execute(
                "SELECT transactions.* " + base, tuple(args)
            ).fetchall()]
        transaction_rows = [_transaction_row(row, conn) for row in decorated]
        from utils.financial_semantics import cashflow_totals
        from utils.shared_expenses import apply_shared_view
        summary_allocated = apply_shared_view(summary_rows, conn, "personal")
        totals = cashflow_totals(summary_allocated, view="personal")
        if cashflow_role == "income":
            filtered_total = totals["money_in"]
            filtered_total_label = "Money in"
        elif cashflow_role == "spending":
            filtered_total = totals["spending"]
            filtered_total_label = "Spending"
        elif cashflow_role == "net":
            filtered_total = totals["net"]
            filtered_total_label = "Net cash flow"
        else:
            filtered_total = round(sum(
                (1 if float(row.get("amount") or 0) >= 0 else -1)
                * float(row.get("_cashflow_amount") or 0)
                for row in summary_allocated
            ), 2)
            filtered_total_label = "Signed total"
        global_review_rows = [
            _apply_category_suggestion(dict(row), history, conn)
            for row in conn.execute("SELECT * FROM transactions").fetchall()
        ]
        suggested_count = sum(
            1 for row in global_review_rows
            if row.get("suggested_category") or row.get("suggested_transaction_type")
        )
        quick_review_count = len(_rank_review_rows(global_review_rows, "quick"))
        return {
            "transactions": transaction_rows,
            "categories": list(CATEGORIES),
            "accounts": _accounts_payload(conn),
            "total": total,
            "offset": offset,
            "limit": limit,
            "filtered_count": total,
            "filtered_total": round(float(filtered_total), 2),
            "filtered_total_label": filtered_total_label,
            "review": {
                "high_priority": int(review["high_priority"] or 0),
                "deferred": int(review["deferred"] or 0),
                "suggested": suggested_count,
                "quick_review": quick_review_count,
                "actionable": int(conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE is_flagged=1 "
                    "OR (category='Uncategorized' AND ABS(amount)>=500)"
                ).fetchone()[0] or 0),
            },
        }
    finally:
        conn.close()


def correct_transaction_action(params: dict[str, Any]) -> dict[str, Any]:
    """Apply a manual category correction — same semantics as the
    Transactions page: mark as user-edited so no rule overwrites it."""
    from datetime import datetime

    from config.categories import CATEGORIES
    from utils.database import update_transaction

    tx_id = int(params.get("id") or 0)
    category = str(params.get("category") or "").strip()
    apply_to_matching = bool(params.get("apply_to_matching"))
    remember_rule = bool(params.get("remember_rule"))
    requested_type = str(params.get("transaction_type") or "").strip()
    shared_override = params.get("shared_expense_override")
    shared_pct = params.get("shared_user_share_pct")
    if tx_id <= 0:
        raise ValueError("No transaction was selected.")
    if category not in CATEGORIES:
        raise ValueError(f"“{category}” is not a Ledger category.")
    from utils.financial_semantics import (
        BULK_CATEGORY_TYPES, MANUAL_TRANSACTION_TYPES,
        classify_transaction_type,
    )
    allowed_types = MANUAL_TRANSACTION_TYPES
    if requested_type and requested_type not in allowed_types:
        raise ValueError("Choose a valid financial type.")
    if shared_pct is not None and not 0 <= float(shared_pct) <= 100:
        raise ValueError("Your share must be between 0 and 100 percent.")

    conn = _connection()
    try:
        existing = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (tx_id,)
        ).fetchone()
        if not existing:
            raise ValueError("That transaction no longer exists.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates: dict[str, Any] = {
            "category": category,
            "category_source": "user_edit",
            "category_rule_id": None,
            "category_explanation": "Category chosen manually by the user.",
            "category_updated_at": now,
            "manually_edited_at": now,
            "is_flagged": 0,
            "flag_reason": "",
        }
        updates["transaction_type"] = requested_type or classify_transaction_type(
            existing["raw_description"], existing["account_type"],
            existing["direction"], category,
        )
        if shared_override is not None:
            updates["shared_expense_override"] = 1 if bool(shared_override) else 0
            updates["shared_user_share_pct"] = (
                round(float(shared_pct), 2) if bool(shared_override) else None
            )
        if "note" in params and params.get("note") is not None:
            updates["notes"] = str(params.get("note") or "")
        update_transaction(tx_id, updates, conn=conn)
        merchant_key = str(existing["merchant_normalized"] or "").strip()
        matching_updated = 0
        if apply_to_matching and merchant_key:
            related = conn.execute(
                "SELECT * FROM transactions WHERE merchant_normalized=? "
                "AND id!=?", (merchant_key, tx_id),
            ).fetchall()
            eligible_related = []
            for related_row in related:
                if str(related_row["transaction_type"] or "") not in BULK_CATEGORY_TYPES:
                    continue
                eligible_related.append(related_row)
                related_type = requested_type or classify_transaction_type(
                    related_row["raw_description"], related_row["account_type"],
                    related_row["direction"], category,
                )
                conn.execute("""
                    UPDATE transactions
                    SET category=?, transaction_type=?, category_source='user_bulk',
                        category_explanation=?, category_updated_at=?,
                        manually_edited_at=?, is_flagged=0, flag_reason=''
                    WHERE id=?
                """, (
                    category, related_type,
                    "Applied from a matching-merchant category decision.",
                    now, now, related_row["id"],
                ))
                matching_updated += 1
            if matching_updated:
                undo_rows = [dict(existing)] + [dict(row) for row in eligible_related]
                undo_fields = [
                    "id", "category", "transaction_type", "category_source",
                    "category_rule_id", "category_explanation",
                    "category_updated_at", "manually_edited_at", "is_flagged",
                    "flag_reason", "notes",
                    "shared_expense_override", "shared_user_share_pct",
                ]
                conn.execute(
                    "INSERT INTO category_change_undo (payload_json) VALUES (?)",
                    (json.dumps([
                        {field: row.get(field) for field in undo_fields}
                        for row in undo_rows
                    ]),),
                )
        rule_saved = False
        if remember_rule and merchant_key:
            from utils.database import upsert_learned_rule
            upsert_learned_rule(
                merchant_key, category,
                example_description=str(existing["raw_description"] or ""),
                source_transaction_id=tx_id,
                source_import_batch_id=existing["import_batch_id"],
                conn=conn,
            )
            rule_saved = True
        conn.commit()
        row = conn.execute(
            "SELECT transactions.*, "
            "COALESCE(accounts.name, transactions.account_type) AS account_name "
            "FROM transactions "
            "LEFT JOIN accounts ON accounts.id = transactions.account_ref "
            "WHERE transactions.id = ?",
            (tx_id,),
        ).fetchone()
        return {
            "transaction": _transaction_row(dict(row), conn),
            "matching_updated": matching_updated,
            "rule_saved": rule_saved,
            "undo_available": bool(matching_updated),
        }
    finally:
        conn.close()


def undo_last_category_change_action(_params: dict[str, Any]) -> dict[str, Any]:
    conn = _connection()
    try:
        entry = conn.execute(
            "SELECT * FROM category_change_undo ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not entry:
            return {"restored": 0, "available": False}
        rows = json.loads(entry["payload_json"])
        fields = [
            "category", "transaction_type", "category_source",
            "category_rule_id", "category_explanation", "category_updated_at",
            "manually_edited_at", "is_flagged", "flag_reason", "notes",
            "shared_expense_override", "shared_user_share_pct",
        ]
        for row in rows:
            conn.execute(
                "UPDATE transactions SET " + ",".join(f"{field}=?" for field in fields)
                + " WHERE id=?",
                (*[row.get(field) for field in fields], int(row["id"])),
            )
        conn.execute("DELETE FROM category_change_undo WHERE id=?", (entry["id"],))
        conn.commit()
        return {"restored": len(rows), "available": False}
    finally:
        conn.close()


def review_summary_action(_params: dict[str, Any]) -> dict[str, Any]:
    payload = list_transactions_action({"quick_review": True, "limit": 1})
    return {"count": int(payload["total"]), **payload["review"]}


def shared_settings_action(_params: dict[str, Any]) -> dict[str, Any]:
    from config.categories import SPENDING_CATEGORIES
    from utils.analytics import find_recurring
    from utils.shared_expenses import get_shared_rules, get_shared_settings
    from utils.financial_semantics import (
        sql_type_placeholders, transaction_types_for_role,
    )

    conn = _connection()
    try:
        rules = get_shared_rules(conn)
        spending_types = transaction_types_for_role("spending")
        merchants = [dict(row) for row in conn.execute(
            "SELECT merchant_normalized,MAX(merchant) AS merchant,"
            "MAX(category) AS category,COUNT(*) AS tx_count,SUM(ABS(amount)) AS total "
            "FROM transactions WHERE transaction_type IN ("
            + sql_type_placeholders(spending_types) + ") "
            "AND COALESCE(merchant_normalized,'')!='' GROUP BY merchant_normalized "
            "ORDER BY total DESC",
            spending_types,
        ).fetchall()]
        for merchant in merchants:
            merchant["total"] = round(float(merchant.get("total") or 0), 2)
        recurring = [
            row for row in find_recurring(conn=conn)
            if row.get("planning_relevant")
        ]
        return {
            "settings": get_shared_settings(conn),
            "rules": rules,
            "categories": sorted(SPENDING_CATEGORIES),
            "merchants": merchants,
            "recurring": recurring,
        }
    finally:
        conn.close()


def save_shared_settings_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.shared_expenses import save_shared_settings
    conn = _connection()
    try:
        save_shared_settings(
            conn,
            shared_with_name=str(params.get("shared_with_name") or ""),
            default_user_share_pct=float(params.get("default_user_share_pct") or 50),
        )
        conn.commit()
    finally:
        conn.close()
    return shared_settings_action({})


def set_shared_rule_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.shared_expenses import delete_shared_rule, set_shared_rule
    conn = _connection()
    try:
        values = {
            "scope_type": str(params.get("scope_type") or ""),
            "scope_value": str(params.get("scope_value") or ""),
        }
        if params.get("enabled", True):
            set_shared_rule(
                conn, **values,
                user_share_pct=float(params.get("user_share_pct") or 50),
            )
        else:
            delete_shared_rule(conn, **values)
        conn.commit()
    finally:
        conn.close()
    return shared_settings_action({})


def category_settings_action(_params: dict[str, Any]) -> dict[str, Any]:
    from config.categories import CATEGORIES
    from utils.analytics import find_recurring
    from utils.database import (
        list_income_source_preferences, list_learned_rules,
        list_recurring_preferences,
    )
    from utils.planner import bills_and_commitments
    conn = _connection()
    try:
        recurring_preferences = list_recurring_preferences(conn=conn)
        recurring = [
            row for row in find_recurring(conn=conn)
            if row.get("planning_relevant")
        ]
        visible_merchants = {row["merchant_normalized"] for row in recurring}
        # The monthly detector deliberately rejects quarterly and annual gaps
        # as irregular. Planner's cadence detector owns those commitments, so
        # expose its rows here as well or the native Settings screen cannot
        # confirm or reject them.
        for item in bills_and_commitments(conn=conn).get(
            "nonmonthly_commitments", []
        ):
            merchant_key = str(item.get("merchant_normalized") or "")
            if not merchant_key or merchant_key in visible_merchants:
                continue
            occurrences = max(1, int(item.get("occurrences") or 1))
            recurring.append({
                "merchant": item.get("merchant") or merchant_key.title(),
                "category": item.get("category") or "Uncategorized",
                "avg_amount": round(float(item.get("est_amount") or 0), 2),
                "months_seen": occurrences,
                "total": round(
                    float(item.get("est_amount") or 0) * occurrences, 2
                ),
                "tx_count": occurrences,
                "confidence": item.get("confidence") or "low",
                "cadence": item.get("cadence") or "nonmonthly",
                "recurring_status": (
                    "confirmed"
                    if item.get("recurring_status") == "recurring"
                    else "automatic"
                ),
                "merchant_normalized": merchant_key,
            })
            visible_merchants.add(merchant_key)
        # An explicit "not recurring" choice suppresses a merchant from the
        # Insights list, but it must stay visible in Settings so the user can
        # change that decision later.
        for preference in recurring_preferences:
            merchant_key = preference["merchant_normalized"]
            if merchant_key in visible_merchants:
                continue
            row = conn.execute(
                """
                SELECT COALESCE(MAX(merchant), ?) AS merchant,
                       COALESCE(MAX(category), 'Uncategorized') AS category,
                       AVG(ABS(amount)) AS avg_amount,
                       COUNT(*) AS tx_count,
                       COUNT(DISTINCT SUBSTR(transaction_date,1,7)) AS months_seen,
                       SUM(ABS(amount)) AS total
                FROM transactions
                WHERE merchant_normalized=?
                """,
                (merchant_key.title(), merchant_key),
            ).fetchone()
            if not row or not int(row["tx_count"] or 0):
                continue
            recurring.append({
                "merchant": preference.get("display_name") or row["merchant"],
                "category": preference.get("category") or row["category"],
                "avg_amount": round(float(row["avg_amount"] or 0), 2),
                "months_seen": int(row["months_seen"] or 0),
                "total": round(float(row["total"] or 0), 2),
                "tx_count": int(row["tx_count"] or 0),
                "confidence": 1.0,
                "cadence": "manual",
                "recurring_status": "excluded",
                "merchant_normalized": merchant_key,
            })
        income_preferences = {
            row["source_normalized"]: row["status"]
            for row in list_income_source_preferences(conn=conn)
        }
        income_sources = [dict(row) for row in conn.execute(
            """
            SELECT COALESCE(NULLIF(merchant_normalized,''), merchant,
                            raw_description, 'PAYROLL') AS source_normalized,
                   COALESCE(MAX(NULLIF(merchant,'')), MAX(raw_description),
                            'Payroll') AS source,
                   COUNT(*) AS tx_count,
                   COUNT(DISTINCT SUBSTR(transaction_date,1,7)) AS months_seen,
                   SUM(ABS(amount)) AS total,
                   MAX(transaction_type) AS transaction_type,
                   MAX(CASE WHEN category_source='protected' THEN 1 ELSE 0 END)
                       AS inferred_source
            FROM transactions
            WHERE transaction_type IN (
                'employment_income','interest_income','other_income'
            )
            GROUP BY source_normalized ORDER BY total DESC
            """
        ).fetchall()]
        max_income_months = max(
            (int(source["months_seen"] or 0) for source in income_sources),
            default=0,
        )
        for source in income_sources:
            source["status"] = income_preferences.get(
                source["source_normalized"],
                "confirmed" if (
                    source.get("transaction_type") == "employment_income"
                    and
                    not int(source.get("inferred_source") or 0)
                    and
                    max_income_months >= 3
                    and int(source["months_seen"] or 0) == max_income_months
                ) else "automatic",
            )
            source["total"] = round(float(source["total"] or 0), 2)
        return {
            "categories": list(CATEGORIES),
            "rules": list_learned_rules(conn=conn),
            "recurring": recurring,
            "recurring_preferences": recurring_preferences,
            "income_sources": income_sources,
        }
    finally:
        conn.close()


def update_category_rule_action(params: dict[str, Any]) -> dict[str, Any]:
    from config.categories import CATEGORIES
    from utils.database import update_learned_rule_by_id
    category = str(params.get("category") or "")
    if category not in CATEGORIES:
        raise ValueError("Choose a valid category.")
    conn = _connection()
    try:
        existing = conn.execute(
            "SELECT * FROM learned_rules WHERE id=?",
            (int(params.get("id") or 0),),
        ).fetchone()
        if not existing:
            raise ValueError("That merchant rule no longer exists.")
        changed = update_learned_rule_by_id(
            int(params.get("id") or 0), category=category,
            subcategory=existing["subcategory"],
            enabled=bool(params.get("enabled", True)),
            example_description=existing["example_description"], conn=conn,
        )
        conn.commit()
        if not changed:
            raise ValueError("That merchant rule no longer exists.")
        return category_settings_action({})
    finally:
        conn.close()


def delete_category_rule_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.database import delete_learned_rule_by_id
    conn = _connection()
    try:
        deleted = delete_learned_rule_by_id(int(params.get("id") or 0), conn=conn)
        conn.commit()
        return {"deleted": bool(deleted)}
    finally:
        conn.close()


def set_recurring_preference_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.database import clear_recurring_preference, set_recurring_preference
    from utils.financial_semantics import (
        sql_type_placeholders, transaction_types_for_role,
    )
    merchant = str(params.get("merchant_normalized") or "").strip()
    status = str(params.get("status") or "automatic")
    conn = _connection()
    try:
        if status == "automatic":
            clear_recurring_preference(merchant, conn=conn)
        else:
            set_recurring_preference(
                merchant, status,
                display_name=str(params.get("display_name") or ""),
                category=str(params.get("category") or ""),
                conn=conn,
            )
            category = str(params.get("category") or "").strip()
            if category and params.get("apply_category"):
                from config.categories import CATEGORIES
                if category not in CATEGORIES:
                    raise ValueError("Choose a valid recurring category.")
                spending_types = transaction_types_for_role("spending")
                conn.execute(
                    "UPDATE transactions SET category=?, category_source='user', "
                    "category_explanation='Recurring item corrected in Settings', "
                    "manually_edited_at=datetime('now','localtime') "
                    "WHERE merchant_normalized=? AND transaction_type IN ("
                    + sql_type_placeholders(spending_types) + ")",
                    (category, merchant, *spending_types),
                )
        conn.commit()
        payload = category_settings_action({})
        payload.update({"merchant_normalized": merchant, "status": status})
        return payload
    finally:
        conn.close()


def set_income_source_preference_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.database import set_income_source_preference

    source = str(params.get("source_normalized") or "").strip()
    status = str(params.get("status") or "confirmed").strip()
    conn = _connection()
    try:
        set_income_source_preference(source, status, conn=conn)
        conn.commit()
        return category_settings_action({})
    finally:
        conn.close()


def add_manual_transaction_action(params: dict[str, Any]) -> dict[str, Any]:
    """Insert one validated manual transaction into a first-class account."""
    from datetime import date

    from config.categories import CATEGORIES
    from utils.database import get_account, insert_transaction

    account_id = int(params.get("account_id") or 0)
    tx_date = str(params.get("date") or "").strip()
    description = str(params.get("description") or "").strip()
    direction = str(params.get("direction") or "").strip().lower()
    category = str(params.get("category") or "").strip()
    amount = float(params.get("amount") or 0)
    if account_id <= 0:
        raise ValueError("Choose an account for the transaction.")
    try:
        date.fromisoformat(tx_date)
    except ValueError as exc:
        raise ValueError("Enter a valid transaction date.") from exc
    if not description:
        raise ValueError("Enter a transaction description.")
    if direction not in {"debit", "credit"}:
        raise ValueError("Direction must be money out or money in.")
    if category not in CATEGORIES:
        raise ValueError(f"“{category}” is not a Ledger category.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    conn = _connection()
    try:
        account = get_account(account_id, conn=conn)
        if not account or account.get("is_archived"):
            raise ValueError("That account is unavailable.")
        transaction = {
            "account_type": account["type"],
            "account_id": account["name"],
            "account_ref": account_id,
            "transaction_date": tx_date,
            "raw_description": description,
            "merchant": str(params.get("merchant") or description).strip(),
            "amount": -amount if direction == "debit" else amount,
            "currency": account.get("currency") or "CAD",
            "category": category,
            "category_source": "user_edit",
            "category_explanation": "Transaction entered manually by the user.",
            "direction": direction,
            "notes": str(params.get("note") or "").strip(),
        }
        from utils.financial_semantics import canonicalize_transaction
        canonicalize_transaction(transaction)
        # An identical row is usually a double-submit, so it is refused by
        # default. It can also be two genuine same-day fares, which the user
        # is the only one able to tell apart, so they can say so and the row
        # claims the next occurrence slot instead of being discarded.
        allow_duplicate = bool(params.get("allow_duplicate"))
        ok, reason = insert_transaction(
            transaction, conn, occurrence=None if allow_duplicate else 0,
        )
        if not ok:
            raise ValueError(
                "An identical transaction already exists in this account. "
                "Choose “Add it anyway” if this really happened twice."
                if reason == "duplicate" else reason
            )
        conn.commit()
        row = conn.execute(
            "SELECT transactions.*, COALESCE(accounts.name, "
            "transactions.account_type) AS account_name FROM transactions "
            "LEFT JOIN accounts ON accounts.id=transactions.account_ref "
            "ORDER BY transactions.id DESC LIMIT 1"
        ).fetchone()
        return {"transaction": _transaction_row(dict(row), conn)}
    finally:
        conn.close()


def _plan_payload(conn, today=None) -> dict[str, Any]:
    from utils.checkin import weekly_checkin
    from utils.database import (
        balance_reconciliation, get_account_balances, get_monthly_plan,
        spending_balance_summary,
    )
    from utils.plan_result import last_plan_result
    from utils.planner import (
        PLAN_MODES, bills_and_commitments, forecast_month,
        plan_commitment_agreement, plan_confirm_proposal, plan_target_month,
        regular_monthly_fixed_total,
    )

    month = plan_target_month(today=today)
    saved = get_monthly_plan(month, conn=conn)
    proposal = plan_confirm_proposal(
        plan_month=month, conn=conn, today=today,
    )
    bills = bills_and_commitments(conn=conn)
    from utils.analytics import (
        income_confirmation_candidates, reliable_income_summary,
        stable_income_summary,
    )
    payroll_income = stable_income_summary(conn=conn)
    reliable_income = reliable_income_summary(conn=conn)
    # The one baseline Home, Safe to Spend and the planner all read.
    # One helper, shared with preview_plan and save_plan, so the figure the
    # screen shows is by construction the figure the save will use.
    _income = _planning_income(conn)
    income_basis, planning_income = _income["basis"], _income["amount"]
    income_candidates = income_confirmation_candidates(conn=conn)
    # Regular monthly costs only. Nonmonthly invoices are handled by the
    # reserve below; counting their full amount here too charged the plan
    # twice for the same bill.
    fixed_suggestion = regular_monthly_fixed_total(bills)
    if planning_income > 0:
        proposal["income_target"] = planning_income
    proposal["fixed_obligations"] = fixed_suggestion
    proposal["savings_target"] = round(
        max(0.0, float(proposal.get("income_target") or 0) * 0.15), 2
    )
    proposal["safety_buffer"] = round(
        max(0.0, min(250.0, float(proposal.get("income_target") or 0) * 0.05)),
        2,
    )
    # Quarterly and annual commitments are set aside monthly. The reserve is
    # part of the equation, so every path that computes flexible room has to
    # subtract it exactly once.
    nonmonthly_reserve = _nonmonthly_reserve(bills)
    proposal["nonmonthly_reserves"] = nonmonthly_reserve
    proposal["flexible_allowance"] = round(max(
        0.0,
        float(proposal.get("income_target") or 0)
        - fixed_suggestion
        - nonmonthly_reserve
        - float(proposal.get("savings_target") or 0)
        - float(proposal.get("safety_buffer") or 0),
    ), 2)
    agreement = plan_commitment_agreement(saved, bills=bills, conn=conn)
    active_plan = dict(saved or proposal)
    # A saved row keeps whatever income was current when it was written.
    # The equation must price the month with today's baseline, which is the
    # same one save_plan derives, so a stored figure never outranks it.
    if planning_income > 0:
        active_plan["income_target"] = planning_income
    equation = _plan_equation({
        "income_target": active_plan.get("income_target", 0),
        "fixed_obligations": active_plan.get("fixed_obligations", fixed_suggestion),
        "savings_target": active_plan.get("savings_target", 0),
        "safety_buffer": active_plan.get("safety_buffer", 0),
        "nonmonthly_reserves": nonmonthly_reserve,
    })
    checkin = weekly_checkin(conn=conn, today=today)
    safe_to_spend = checkin["safe_to_spend"]
    outcome = checkin["verdict"]
    balance_count = len(get_account_balances(conn=conn, latest_only=True))
    spending_balances = spending_balance_summary(conn=conn)
    reconciliation = balance_reconciliation(conn=conn)
    has_spendable_balance = float(
        spending_balances.get("available_balance") or 0
    ) > 0
    # Transaction activity is not statement coverage. Until imports persist
    # explicit period-end metadata per account, a quiet account cannot block a
    # plan or lower its confidence.
    coverage_complete = True
    # Measure both ends of the supported range. Guarding only the latest date
    # still let one row dated 1900 turn a few months into a century of history.
    from utils.analysis_period import supported_date_bounds

    history = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions"
    ).fetchone()
    history_days = 0
    _history_start, _history_end = supported_date_bounds(
        conn=conn, today=today,
    )
    if history and _history_start and _history_end:
        history_days = max(0, (
            _history_end - _history_start
        ).days + 1)
    history_sufficient = history_days >= 28
    def _commitment_confidence(item: dict[str, Any]) -> float:
        raw = item.get("confidence")
        if isinstance(raw, (int, float)):
            return float(raw)
        return {"high": 1.0, "medium": 0.65, "low": 0.3}.get(
            str(raw or "").lower(), 0.0
        )
    from utils.analytics import recurring_status_is_reviewed
    uncertain_commitments = sum(
        1 for item in bills.get("items", [])
        if item.get("included_in_forecast")
        and not recurring_status_is_reviewed(item.get("recurring_status"))
        and _commitment_confidence(item) < 0.8
    )
    # A single "reviewed" state for the income + recurring setup step. Both
    # preference tables carry updated_at, so the last review date is derived,
    # not separately stored. income is "reviewed" once stable income is
    # confirmed (an explicit confirmation or auto-recognized payroll); the
    # commitments step is reviewed once no uncertain recurring costs remain.
    review_ts = conn.execute(
        "SELECT MAX(updated_at) FROM ("
        "SELECT updated_at FROM income_source_preferences "
        "UNION ALL SELECT updated_at FROM recurring_preferences)"
    ).fetchone()[0]
    income_reviewed = bool(reliable_income.get("confirmed"))
    commitments_reviewed = not uncertain_commitments
    missing = []
    advisories = []
    if not saved:
        missing.append({
            "id": "plan", "label": "Confirm this month’s plan",
            "detail": "Review the monthly equation and save it once.",
            "action": "Review plan", "screen": "plan#commitment-review",
        })
    if not reconciliation.get("ready"):
        for account in reconciliation.get("accounts") or [{
            "account_name": "each chequing and credit-card account",
            "account_kind": "",
            "status": "missing",
            "as_of_date": "",
            "last_activity": "",
        }]:
            if account.get("ready"):
                continue
            stale = account.get("status") == "out_of_date"
            advisories.append({
                "id": f"balance:{account.get('account_ref') or account.get('account_kind') or 'required'}",
                "label": (
                    f"Update {account.get('account_name')} balance"
                    if stale else f"Add {account.get('account_name')} balance"
                ),
                "detail": (
                    f"Balance is as of {account.get('as_of_date')}; imports "
                    f"continue through {account.get('last_activity')}."
                    if stale else
                    "Add the current card balance to compare the monthly plan "
                    "with real cash and liabilities."
                    if account.get("account_kind") == "credit_card" else
                    "Add the current balance to compare the monthly plan "
                    "with real cash."
                ),
                "action": "Update balance" if stale else "Add balance",
                "screen": "settings#planning-balances",
            })
    elif not has_spendable_balance:
        advisories.append({
            "id": "spending_accounts",
            "label": "Choose which account is available for spending",
            "detail": (
                "Savings and investment balances stay protected unless "
                "you explicitly include an account."
            ),
            "action": "Review accounts",
            "screen": "settings#spendable-accounts",
        })
    if not reliable_income.get("confirmed"):
        missing.append({
            "id": "income", "label": "Confirm your recurring income sources",
            "detail": (
                "Confirmed recurring income can include payroll, interest, "
                "and regular deposits, but never refunds. Until it is "
                "confirmed, the plan uses an estimate from completed months."
            ),
            "action": "Review income sources", "screen": "settings#income-sources",
        })
    if saved and not agreement.get("agreed"):
        missing.append({
            "id": "plan_drift",
            "label": "Review changed fixed commitments",
            "detail": (
                f"Saved fixed costs are ${float(agreement['saved']):,.2f}; "
                f"reviewed commitments are ${float(agreement['detected']):,.2f}."
            ),
            # An anchor, not just "plan". Pointing at the screen the user is
            # already looking at made this button do nothing at all.
            "action": "Review changes", "screen": "plan#commitment-review",
        })
    if uncertain_commitments:
        missing.append({
            "id": "commitments", "label": "Review uncertain fixed commitments",
            "detail": f"{uncertain_commitments} recurring cost suggestion(s) need confirmation.",
            "action": "Review recurring costs", "screen": "settings#recurring-costs",
        })
    if not history_sufficient:
        missing.append({
            "id": "history", "label": (
                "Import your first statement" if not int(history["n"] or 0)
                else "Add one complete month of transaction history"
            ),
            "detail": "Forecast pacing needs at least 28 days of activity.",
            "action": "Add data", "screen": "add-data",
        })
    if outcome.get("state") == "stale_data":
        missing.append({
            "id": "freshness",
            "label": "Import your latest transactions",
            "detail": outcome.get("sentence") or (
                "Safe to Spend needs current transaction data."
            ),
            "action": "Add data",
            "screen": "add-data",
        })
    # At most one thing worth interrupting for. Plan used to render a queue of
    # up to six review items plus a list of optional cash checks, which made a
    # monthly decision screen read as a task manager and buried the plan
    # itself. Income sources, recurring costs and account balances are all
    # reviewable where they live (Settings and Insights); none of them needs
    # to shout from this page. Only a reason the plan itself cannot be trusted
    # earns space here, and the engine decides that, not the screen.
    plan_notice = None
    if outcome.get("state") == "stale_data":
        plan_notice = {
            "level": "warn",
            "headline": outcome.get("headline") or "Import your latest transactions",
            "detail": outcome.get("sentence") or (
                "This plan is measured against data that has stopped moving."
            ),
            "action": "Add data",
            "screen": "add-data",
        }
    elif not history_sufficient:
        plan_notice = {
            "level": "info",
            "headline": (
                "Import your first statement" if not int(history["n"] or 0)
                else "One full month of history makes this more accurate"
            ),
            "detail": (
                "Northstar needs about 28 days of activity before its income "
                "baseline settles."
            ),
            "action": "Add data",
            "screen": "add-data",
        }
    elif not equation["coherent"]:
        plan_notice = {
            "level": "warn",
            "headline": "This plan spends more than it expects to receive",
            "detail": equation.get("explanation") or "",
            "action": "",
            "screen": "",
        }
    forecast_ready = bool(
        saved and history_sufficient
        and reliable_income.get("confirmed")
        and not uncertain_commitments
        and agreement.get("agreed")
        and safe_to_spend.get("available")
        and outcome.get("state") != "stale_data"
    )
    from utils.analysis_period import analysis_context

    return {
        "month": month,
        # So the screen can name the exact date the figures describe rather
        # than implying they are about today.
        "analysis": analysis_context(conn=conn, today=today),
        # How the last finished month compared with what was intended for it.
        # Read-only, and absent unless an exact plan for a canonically
        # complete month can be proven to predate the month's end.
        "last_plan_result": last_plan_result(
            conn=conn, today=today,
        ),
        # The single most important thing about this month, or nothing.
        "plan_notice": plan_notice,
        "saved": saved,
        "proposal": proposal,
        # Values actually rendered by the plan form and equation. This keeps
        # legacy saved rows visible for provenance while ensuring the working
        # plan uses today's canonical income baseline.
        "working_plan": active_plan,
        "fixed_obligations_suggestion": fixed_suggestion,
        # The canonical baseline, identical to the one Home shows.
        "income_basis": income_basis,
        "stable_income": reliable_income,
        "reliable_income": reliable_income,
        "payroll_income": payroll_income,
        "income_confirmation_candidates": income_candidates,
        "fixed_commitments": [
            {
                "merchant": item.get("merchant") or "Recurring cost",
                "category": item.get("category") or "Uncategorized",
                "amount": round(float(item.get("est_amount") or 0), 2),
                "household_amount": round(float(
                    item.get("household_amount") or item.get("est_amount") or 0
                ), 2),
                "other_share": round(float(item.get("other_share") or 0), 2),
                "user_share_pct": float(item.get("user_share_pct") or 100),
                "is_shared": bool(item.get("is_shared")),
                "kind": (
                    "Subscription" if item.get("group") == "active_subscriptions"
                    else "Fixed bill"
                ),
                "basis": item.get("reason") or "Recognized recurring cost",
                "cadence": item.get("cadence") or "monthly",
                "monthly_setaside": round(float(
                    item.get("monthly_setaside")
                    or item.get("est_amount") or 0
                ), 2),
            }
            for item in bills.get("items", [])
            if item.get("included_in_forecast")
            and item.get("group") != "nonmonthly_commitments"
        ],
        # Bills that do not arrive every month, and what to set aside for
        # them. These were invisible before 1.6.0, so nothing was reserved
        # and the money simply vanished in whichever month they landed.
        "nonmonthly_commitments": [
            {
                "merchant": item.get("merchant") or "Recurring cost",
                "category": item.get("category") or "Uncategorized",
                "cadence": item.get("cadence") or "",
                "amount": round(float(item.get("est_amount") or 0), 2),
                "monthly_setaside": round(float(
                    item.get("monthly_setaside") or 0
                ), 2),
                "expected_next": item.get("expected_next") or "",
                "confidence": item.get("confidence") or "low",
                "note": item.get("setaside_note") or "",
            }
            for item in bills.get("nonmonthly_commitments", [])
        ],
        "nonmonthly_monthly_reserve": float(
            bills.get("nonmonthly_monthly_reserve") or 0
        ),
        "nonmonthly_annual_total": float(
            bills.get("nonmonthly_annual_total") or 0
        ),
        "price_changes": bills.get("price_changes") or [],
        "equation": equation,
        "outlook": _plan_outlook(month, equation, bills),
        "safe_to_spend": safe_to_spend,
        "outcome": outcome,
        "risk_level": checkin["progress"]["risk_level"],
        "forecast": forecast_month(
            plan_month=month, conn=conn, today=today,
        ),
        "readiness": {
            "forecast_ready": forecast_ready,
            "plan_confirmed": bool(saved),
            "balance_count": balance_count,
            "has_spendable_balance": has_spendable_balance,
            "reconciliation": reconciliation,
            "plan_agreement": agreement,
            "coverage_complete": coverage_complete,
            "cash_checked": bool(
                reconciliation.get("ready") and has_spendable_balance
            ),
            "history_sufficient": history_sufficient,
            "history_days": history_days,
            "uncertain_commitments": uncertain_commitments,
            "missing": missing,
            "advisories": advisories,
            "review": {
                "income_reviewed": income_reviewed,
                "commitments_reviewed": commitments_reviewed,
                "complete": income_reviewed and commitments_reviewed,
                "last_reviewed": review_ts or "",
            },
        },
        "modes": [
            {"value": "normal", "label": "Everyday plan",
             "description": "Balance savings with normal monthly spending."},
            {"value": "aggressive_save", "label": "Save more",
             "description": "Put more income toward savings this month."},
            {"value": "tight", "label": "Recovery month",
             "description": "Protect a larger buffer and reduce flexible room."},
        ],
        "warning": (
            "The proposal needs review because history is limited or recent "
            "spending leaves little room within the current income baseline."
            if proposal.get("insufficient_data") else ""
        ),
    }


def _nonmonthly_reserve(bills: dict[str, Any]) -> float:
    """The one monthly set-aside for quarterly and annual commitments.

    Single reader for planner's `nonmonthly_monthly_reserve` so proposal,
    equation, preview, save and validation cannot drift apart.
    """
    return round(max(0.0, float(bills.get("nonmonthly_monthly_reserve") or 0)), 2)


def _plan_impossible_message(equation: dict[str, Any]) -> str:
    """Say what overflowed and which input to move, not just that it failed.

    A disabled Save button with no reason leaves people guessing, especially
    when the culprit is the nonmonthly reserve they never typed in.
    """
    shortfall = abs(round(float(equation.get("unallocated") or 0), 2))
    parts = [
        ("fixed costs", float(equation.get("fixed") or 0)),
        ("nonmonthly reserves", float(equation.get("nonmonthly_reserves") or 0)),
        ("savings", float(equation.get("savings") or 0)),
        ("buffer", float(equation.get("buffer") or 0)),
    ]
    committed = " + ".join(
        f"{label} ${amount:,.2f}" for label, amount in parts if amount > 0
    )
    income = float(equation.get("income") or 0)
    adjustable = [
        label for label, amount in parts
        if amount > 0 and label in {"savings", "buffer"}
    ]
    if adjustable:
        suggestion = (
            f" Lower {' or '.join(adjustable)} by ${shortfall:,.2f}, or raise "
            "expected income, to make this plan add up."
        )
    else:
        suggestion = (
            f" Fixed costs and reserves alone exceed income by ${shortfall:,.2f}."
            " Review the commitments included in the forecast."
        )
    return (
        f"This plan needs ${shortfall:,.2f} more than it has. "
        f"{committed} comes to more than expected income "
        f"${income:,.2f}.{suggestion}"
    )


def _plan_amount(value: Any, label: str) -> float:
    """Return one safe, user-entered planning amount.

    JSON normally carries finite numbers, but the Python sidecar is still a
    public trust boundary: non-standard JSON and direct callers can supply
    NaN or infinity. Letting either reach SQLite makes every later comparison
    and total unreliable.
    """
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(amount):
        raise ValueError(f"{label} must be a real number.")
    if amount < 0:
        raise ValueError("Plan amounts cannot be negative.")
    if amount > MAX_PLAN_AMOUNT:
        raise ValueError(
            f"{label} looks like a typo at ${amount:,.2f}. "
            "Check the figure before saving."
        )
    return round(amount, 2)


def _planning_income(conn) -> dict[str, Any]:
    """The one planning-income basis every Plan surface must use.

    Northstar had two. The screen showed `typical_monthly_income` — the
    average of complete months, which is also what Safe to Spend and the
    planner read — while `save_plan` validated the submitted figure against
    `reliable_income_summary`, which sums a median per source across only the
    months that source appeared in. A source arriving in three months out of
    six is counted by the second as though it arrived in all six, so the two
    disagree for anyone whose deposits are not perfectly regular, and any
    difference of a cent rejected the save.

    There is now one function, and preview, display and save all call it.
    `reliable_income_summary` keeps its separate job of saying whether income
    has been *reviewed*; it no longer decides what the amount is.
    """
    from utils.analytics import typical_monthly_income

    basis = typical_monthly_income(conn=conn)
    return {
        "amount": round(float(basis.get("amount") or 0), 2),
        "basis": basis,
    }


def _plan_equation(params: dict[str, Any]) -> dict[str, Any]:
    """Return the authoritative monthly plan equation for the UI."""
    income = _plan_amount(params.get("income_target"), "Expected income")
    fixed = _plan_amount(params.get("fixed_obligations"), "Fixed costs")
    savings = _plan_amount(params.get("savings_target"), "Savings target")
    nonmonthly = _plan_amount(
        params.get("nonmonthly_reserves"), "Nonmonthly reserves"
    )
    buffer = _plan_amount(params.get("safety_buffer"), "Safety buffer")
    flexible = round(income - fixed - nonmonthly - savings - buffer, 2)
    equation = {
        "income": income,
        "fixed": fixed,
        "savings": savings,
        "nonmonthly_reserves": nonmonthly,
        "buffer": buffer,
        "flexible": max(0.0, flexible),
        "unallocated": flexible,
        "coherent": income > 0 and flexible >= 0,
        # "Reliable" overstated it: this figure is an estimate from completed
        # months until the income sources are actually confirmed.
        "explanation": (
            "Typical monthly income minus fixed costs, nonmonthly reserves, "
            "savings, and buffer leaves one flexible spending amount."
        ),
    }
    if income > 0 and flexible < 0:
        # Say what overflowed and which lever to move. The card already renders
        # this line, so the reason appears next to the failing numbers.
        equation["explanation"] = _plan_impossible_message(equation)
    return equation


def _plan_outlook(month: str, equation: dict[str, Any], bills: dict[str, Any]) -> list[dict]:
    """Project the same inspectable plan across the next three months."""
    from datetime import date
    import calendar

    year, month_number = map(int, month.split("-"))
    known = [
        {
            "merchant": item.get("merchant") or "Recurring cost",
            "amount": round(float(item.get("est_amount") or 0), 2),
            "household_amount": round(float(
                item.get("household_amount") or item.get("est_amount") or 0
            ), 2),
            "is_shared": bool(item.get("is_shared")),
        }
        for item in bills.get("items", [])
        if item.get("included_in_forecast")
    ]
    rows = []
    for offset in range(3):
        absolute = (year * 12 + month_number - 1) + offset
        y, m = divmod(absolute, 12)
        label = date(y, m + 1, 1).strftime("%B %Y")
        rows.append({
            "month": f"{y:04d}-{m + 1:02d}",
            "label": label,
            "stable_income": equation["income"],
            "fixed": equation["fixed"],
            "nonmonthly_reserves": equation["nonmonthly_reserves"],
            "savings": equation["savings"],
            "buffer": equation["buffer"],
            "flexible": equation["flexible"],
            "known_recurring": known,
            "days": calendar.monthrange(y, m + 1)[1],
        })
    return rows


def preview_plan_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.planner import bills_and_commitments

    values = dict(params)
    for key, label in (
        ("fixed_obligations", "Fixed costs"),
        ("savings_target", "Savings target"),
        ("safety_buffer", "Safety buffer"),
    ):
        values[key] = _plan_amount(params.get(key), label)
    # Both of these are derived, so both are read here rather than trusted
    # from the caller: a preview must never show more flexible room than a
    # save would allow, and it must never price the month off a different
    # income than the one the save will use.
    conn = _connection()
    try:
        values["nonmonthly_reserves"] = _nonmonthly_reserve(
            bills_and_commitments(conn=conn)
        )
        values["income_target"] = _planning_income(conn)["amount"]
    finally:
        conn.close()
    if params.get("apply_preset"):
        income = values["income_target"]
        mode = str(params.get("mode") or "normal")
        savings_rate, buffer_rate = {
            "normal": (0.15, 0.05),
            "aggressive_save": (0.20, 0.05),
            "tight": (0.15, 0.10),
        }.get(mode, (0.15, 0.05))
        values["savings_target"] = round(income * savings_rate, 2)
        values["safety_buffer"] = round(min(500.0, income * buffer_rate), 2)
    elif params.get("apply_preference"):
        preference_value = _plan_amount(
            params.get("savings_preference_value"), "Savings preference"
        )
        if params.get("savings_preference_style") == "amount":
            values["savings_target"] = preference_value
        else:
            values["savings_target"] = round(
                values["income_target"]
                * preference_value / 100.0, 2
            )
    equation = _plan_equation(values)
    return {
        "equation": equation,
        "values": {
            "savings_target": equation["savings"],
            "safety_buffer": equation["buffer"],
            "flexible_allowance": equation["flexible"],
            "nonmonthly_reserves": equation["nonmonthly_reserves"],
        },
    }


def plan_summary_action(_params: dict[str, Any]) -> dict[str, Any]:
    conn = _connection()
    try:
        return _plan_payload(conn, today=_params.get("as_of_date"))
    finally:
        conn.close()


def save_plan_action(params: dict[str, Any]) -> dict[str, Any]:
    import re

    from utils.database import upsert_monthly_plan
    from utils.planner import (
        PLAN_MODES, bills_and_commitments, regular_monthly_fixed_total,
    )

    month = str(params.get("month") or "").strip()
    mode = str(params.get("mode") or "normal").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Plan month must use YYYY-MM format.")
    if mode not in PLAN_MODES:
        raise ValueError("Choose a valid planning stance.")
    # Only what the user actually chose. Income and flexible spending are
    # both derived, and a client copy of a derived figure can only ever
    # agree with the engine or contradict it — never inform it.
    values = {
        key: _plan_amount(params.get(key), label)
        for key, label in (
            ("fixed_obligations", "Fixed costs"),
            ("savings_target", "Savings target"),
            ("safety_buffer", "Safety buffer"),
        )
    }
    # Both derived figures come from the same connection, and income comes
    # from the same helper the payload and the preview used.
    reserve_conn = _connection()
    try:
        nonmonthly_reserve = _nonmonthly_reserve(
            bills_and_commitments(conn=reserve_conn)
        )
        values["income_target"] = _planning_income(reserve_conn)["amount"]
    finally:
        reserve_conn.close()
    if values["income_target"] <= 0:
        raise ValueError(
            "Northstar cannot work out a monthly income yet. Import at least "
            "one complete month of statements and the plan can be saved."
        )
    # The reserve is derived from live commitments on every read, so it is
    # deliberately not persisted with the plan: storing it would let a saved
    # copy drift away from the commitments it represents.
    #
    # Coherence is the equation's own test — income minus every allocation
    # cannot go below zero. The separate total-versus-income check this
    # replaces asked the same question of the client's copy of the answer.
    equation = _plan_equation({**values, "nonmonthly_reserves": nonmonthly_reserve})
    if not equation["coherent"]:
        raise ValueError(
            _plan_impossible_message(equation)
        )
    values["flexible_allowance"] = equation["flexible"]
    values["spending_target"] = round(
        values["fixed_obligations"] + values["flexible_allowance"], 2
    )
    conn = _connection()
    try:
        bills = bills_and_commitments(conn=conn)
        # The override check compares against the same fixed-cost basis the
        # plan displays, so a nonmonthly bill cannot make a correct plan look
        # like a deliberate override.
        detected = regular_monthly_fixed_total(bills)
        override_reason = str(
            params.get("fixed_override_reason") or ""
        ).strip()
        if len(override_reason) > 160:
            raise ValueError("Keep the fixed-cost override reason under 160 characters.")
        if (
            abs(values["fixed_obligations"] - detected) >= 0.01
            and not override_reason
        ):
            raise ValueError(
                "Use reviewed commitments or add a short reason for the "
                "deliberate fixed-cost override."
            )
        upsert_monthly_plan({
            "month": month,
            "mode": mode,
            **values,
            "notes": str(params.get("notes") or "").strip(),
            "detected_commitments_at_save": detected,
            "fixed_override_reason": override_reason,
        }, conn=conn)
        conn.commit()
        payload = _plan_payload(conn)
        payload["warning"] = ""
        return payload
    finally:
        conn.close()


def _goals_payload(conn) -> dict[str, Any]:
    from utils.goals import GOAL_TYPES, goal_progress

    return {
        "goals": goal_progress(conn=conn),
        "goal_types": [
            {"value": value, "label": label}
            for value, label in GOAL_TYPES.items()
        ],
        "templates": [
            {"type": "emergency_fund", "label": "Emergency fund", "name": "Emergency fund", "primary": True},
            {"type": "planned_purchase", "label": "Planned purchase", "name": "Planned purchase", "primary": True},
            {"type": "investing", "label": "Investing", "name": "Investing goal", "primary": True},
            {"type": "debt_payoff", "label": "Debt payoff", "name": "Debt payoff", "primary": True},
            {"type": "general_savings", "label": "General savings", "name": "Savings goal", "primary": True},
            {"type": "true_expense", "label": "Annual or irregular expense", "name": "Annual expense", "primary": True},
        ],
        "accounts": _accounts_payload(conn),
    }


def goals_summary_action(_params: dict[str, Any]) -> dict[str, Any]:
    conn = _connection()
    try:
        return _goals_payload(conn)
    finally:
        conn.close()


def create_goal_action(params: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    from utils.database import insert_goal
    from utils.goals import GOAL_TYPES

    name = str(params.get("name") or "").strip()
    goal_type = str(params.get("type") or "general_savings").strip()
    target = float(params.get("target_amount") or 0)
    target_date = str(params.get("target_date") or "").strip()
    progress_method = str(params.get("progress_method") or "manual").strip()
    linked_account_ref = int(params.get("linked_account_ref") or 0) or None
    frequency = str(params.get("contribution_frequency") or "monthly").strip().lower()
    if not name:
        raise ValueError("Give the goal a name.")
    if goal_type not in GOAL_TYPES:
        raise ValueError("Choose a valid goal type.")
    if target <= 0:
        raise ValueError("Goal target must be greater than zero.")
    if progress_method not in {"manual", "linked_account"}:
        raise ValueError("Choose manual progress or a linked account balance.")
    if progress_method == "linked_account" and linked_account_ref is None:
        raise ValueError("Choose the account that tracks this goal.")
    if frequency not in {"monthly", "quarterly"}:
        raise ValueError("Choose a monthly or quarterly contribution target.")
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError as exc:
            raise ValueError("Enter a valid goal date.") from exc
    conn = _connection()
    try:
        goal_id = insert_goal({
            "name": name,
            "type": goal_type,
            "target_amount": target,
            "current_amount": max(0.0, float(params.get("current_amount") or 0)),
            "target_date": target_date or None,
            "status": "active",
            "progress_method": progress_method,
            "linked_account_ref": linked_account_ref,
            "planned_monthly_contribution": max(
                0.0, float(params.get("monthly_contribution") or 0)
            ),
            "contribution_frequency": frequency,
            "include_in_plan": 1 if params.get("include_in_plan", False) else 0,
            "show_milestones": 1 if params.get("show_milestones", True) else 0,
            "currency": "CAD",
            "notes": str(params.get("notes") or "").strip(),
        }, conn=conn)
        conn.commit()
        payload = _goals_payload(conn)
        payload["created_id"] = int(goal_id)
        return payload
    finally:
        conn.close()


def update_goal_action(params: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    from utils.database import get_goal, update_goal
    from utils.goals import GOAL_TYPES

    goal_id = int(params.get("goal_id") or 0)
    status = str(params.get("status") or "active").strip().lower()
    if status not in {"active", "paused", "completed", "archived"}:
        raise ValueError("Choose active, paused, completed, or archived.")
    conn = _connection()
    try:
        existing = get_goal(goal_id, conn=conn)
        if not existing:
            raise ValueError("Goal not found.")
        name = str(params.get("name") or existing.get("name") or "").strip()
        goal_type = str(params.get("type") or existing.get("type") or "general_savings")
        target = float(params.get("target_amount")
                       if params.get("target_amount") is not None
                       else existing.get("target_amount") or 0)
        target_date = str(params.get("target_date")
                          if params.get("target_date") is not None
                          else existing.get("target_date") or "").strip()
        progress_method = str(params.get("progress_method")
                              or existing.get("progress_method") or "manual")
        linked_account_ref = int(params.get("linked_account_ref") or 0) or None
        frequency = str(params.get("contribution_frequency")
                        or existing.get("contribution_frequency") or "monthly").lower()
        if not name or target <= 0 or goal_type not in GOAL_TYPES:
            raise ValueError("Goal name, type, and target must be valid.")
        if target_date:
            date.fromisoformat(target_date)
        if progress_method not in {"manual", "linked_account"}:
            raise ValueError("Choose manual progress or a linked account balance.")
        if progress_method == "linked_account" and linked_account_ref is None:
            linked_account_ref = existing.get("linked_account_ref")
        if progress_method == "linked_account" and not linked_account_ref:
            raise ValueError("Choose the account that tracks this goal.")
        if frequency not in {"monthly", "quarterly"}:
            raise ValueError("Choose a monthly or quarterly contribution target.")
        updates = {
            "name": name,
            "type": goal_type,
            "target_amount": target,
            "target_date": target_date or None,
            "status": status,
            "progress_method": progress_method,
            "linked_account_ref": (
                linked_account_ref if progress_method == "linked_account" else None
            ),
            "planned_monthly_contribution": max(
                0.0, float(params.get("monthly_contribution")
                           if params.get("monthly_contribution") is not None
                           else existing.get("planned_monthly_contribution") or 0)
            ),
            "contribution_frequency": frequency,
            "include_in_plan": 1 if params.get(
                "include_in_plan", bool(existing.get("include_in_plan", 1))
            ) else 0,
            "show_milestones": 1 if params.get(
                "show_milestones", bool(existing.get("show_milestones", 1))
            ) else 0,
            "notes": str(params.get("notes")
                         if params.get("notes") is not None
                         else existing.get("notes") or "").strip(),
            "completed_at": (
                date.today().isoformat() if status == "completed"
                else existing.get("completed_at")
            ),
        }
        update_goal(goal_id, updates, conn=conn)
        conn.commit()
        return _goals_payload(conn)
    finally:
        conn.close()


def contribute_goal_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.database import record_goal_contribution

    conn = _connection()
    try:
        record_goal_contribution(
            int(params.get("goal_id") or 0),
            float(params.get("amount") or 0),
            contributed_on=str(params.get("date") or "") or None,
            notes=str(params.get("note") or ""),
            conn=conn,
        )
        conn.commit()
        return _goals_payload(conn)
    finally:
        conn.close()


def insights_summary_action(params: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    from utils.analysis_period import analysis_context
    from utils.analytics import find_recurring
    from utils.checkin import meaningful_changes
    from utils.database import compute_net_worth_now, get_account_balances
    from utils.financial_semantics import (
        sql_type_placeholders, transaction_types_for_role,
    )
    from utils.insights import (
        _mr_classify, category_pace_breakdown, monthly_aggregates, spending_pace,
    )
    from utils.planner import analysis_anchor, bills_and_commitments

    conn = _connection()
    try:
        # Northstar has one visible amount basis. Only explicit transaction
        # splits reduce it; the former automatic household lens is retired.
        share_view = "personal"
        month = analysis_anchor(conn=conn)
        period = _period_breakdown(params, conn)
        as_of = date.fromisoformat(period["period_end"])
        comparison = _cashflow_comparison(period, conn, share_view)
        stable_received = float(conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions "
            "WHERE transaction_type='employment_income' "
            "AND transaction_date BETWEEN ? AND ?",
            (period["period_start"], period["period_end"]),
        ).fetchone()[0] or 0)
        commitments = bills_and_commitments(conn=conn)
        recurring_keys = [
            str(item.get("merchant") or "").upper()
            for item in commitments.get("items", [])
            if item.get("included_in_forecast")
        ]
        fixed_actual = 0.0
        if recurring_keys:
            placeholders = ",".join("?" for _ in recurring_keys)
            spending_types = transaction_types_for_role("spending")
            fixed_rows = conn.execute(
                "SELECT * FROM transactions "
                "WHERE UPPER(COALESCE(merchant,'')) IN (" + placeholders + ") "
                "AND transaction_type IN ("
                + sql_type_placeholders(spending_types) + ") "
                "AND transaction_date BETWEEN ? AND ?",
                (
                    *recurring_keys, *spending_types,
                    period["period_start"], period["period_end"],
                ),
            ).fetchall()
            from utils.shared_expenses import apply_shared_view
            fixed_actual = sum(
                float(row.get("_cashflow_amount") or 0)
                for row in apply_shared_view(
                    [dict(row) for row in fixed_rows], conn, share_view
                )
            )
        current_net = float(period["cashflow"].get("net") or 0)
        comparison_net = float(comparison["current"].get("net") or 0)
        prior_net = float(comparison["prior"].get("net") or 0)
        category_pace = category_pace_breakdown(
            conn=conn, today=as_of, limit=100, share_view=share_view,
        )
        # Fixed commitments (mortgage, utilities) are excluded from the
        # highlighted "biggest movers": their equivalent-day total can jump
        # from shared-split attribution rather than a spending decision, and a
        # highlighted mover reads as behavioral. They still appear in the full
        # category breakdown below.
        # A mover is a change, so there has to be something to have changed
        # from. Without a comparable previous month every delta is None and
        # the honest answer is that there are no movers to report, not that
        # every category moved by its whole amount.
        movable = (
            [item for item in category_pace["items"]
             if item.get("delta") is not None
             and _mr_classify(item["category"]) != "fixed"]
            if category_pace.get("comparison_available") else []
        )
        positive_movers = sorted(
            (item for item in movable if item["delta"] > 0),
            key=lambda item: -float(item["delta"]),
        )
        negative_movers = sorted(
            (item for item in movable if item["delta"] < 0),
            key=lambda item: float(item["delta"]),
        )
        estimate_row = conn.execute(
            "SELECT * FROM net_worth_estimates ORDER BY as_of_date DESC,id DESC LIMIT 1"
        ).fetchone()
        quick_estimate = dict(estimate_row) if estimate_row else None
        if quick_estimate:
            quick_estimate["net_worth"] = round(
                float(quick_estimate.get("total_assets") or 0)
                - float(quick_estimate.get("total_liabilities") or 0), 2
            )
        recurring_rows = [
            row for row in find_recurring(conn=conn)
            if row.get("planning_relevant")
        ][:10]
        from utils.shared_expenses import apply_commitment_share
        recurring = []
        for recurring_row in recurring_rows:
            allocated = apply_commitment_share({
                **recurring_row,
                "est_amount": recurring_row.get("avg_amount") or 0,
            }, conn)
            display_row = dict(recurring_row)
            display_row.update({
                "avg_amount": (
                    float(allocated.get("est_amount") or 0)
                    if share_view == "personal"
                    else float(allocated.get("household_amount") or 0)
                ),
                "household_amount": float(
                    allocated.get("household_amount") or 0
                ),
                "other_share": float(allocated.get("other_share") or 0),
                "is_shared": bool(allocated.get("is_shared")),
                "user_share_pct": float(
                    allocated.get("user_share_pct") or 100
                ),
            })
            recurring.append(display_row)
        return {
            "month": month,
            "analysis": analysis_context(conn=conn),
            "period_days": period["period_days"],
            "period_label": period["period_label"],
            "period_start": period["period_start"],
            "period_end": period["period_end"],
            "share_view": share_view,
            "monthly": monthly_aggregates(conn=conn, share_view=share_view)[-12:],
            # Return the complete spending-only breakdown so every API
            # consumer can reconcile to the headline total. React decides how
            # many ranked bars to show; the proportional chart must not lose
            # lower-ranked categories.
            "categories": period["categories"],
            "income_sources": period["income_sources"],
            "income_total": period["income_total"],
            "stable_income": period["stable_income"],
            "cash_flow_summary": {
                "money_in": round(float(period["cashflow"].get("income") or 0), 2),
                "shared_reimbursements": round(float(
                    period["cashflow"].get("shared_reimbursements") or 0
                ), 2),
                "stable_income_received": round(stable_received, 2),
                "spending": round(float(period["cashflow"].get("spending") or 0), 2),
                "fixed_recurring": round(fixed_actual, 2),
                "fixed_monthly_estimate": round(float(
                    commitments.get("commitment_monthly_estimate") or 0
                ), 2),
                "net_cash_flow": round(current_net, 2),
                "amount_kept": round(max(0.0, current_net), 2),
                "prior_amount_kept": round(max(0.0, prior_net), 2),
                "kept_change": round(comparison_net - prior_net, 2),
                "kept_change_abs": round(abs(comparison_net - prior_net), 2),
                "kept_direction": (
                    "up" if comparison_net >= prior_net else "down"
                ),
                "prior_available": comparison["available"],
                "comparison_label": comparison["label"],
            },
            "merchants": period["merchants"],
            "net_worth": compute_net_worth_now(conn=conn),
            "quick_net_worth": quick_estimate,
            "balances": get_account_balances(conn=conn, latest_only=True),
            "accounts": _accounts_payload(conn),
            "findings": meaningful_changes(conn=conn),
            "pace": spending_pace(conn=conn, today=as_of, share_view=share_view),
            "flexible_pace": spending_pace(
                conn=conn, today=as_of, share_view=share_view,
                flexible_only=True,
            ),
            "category_pace": category_pace,
            "category_movers": {
                "increase": positive_movers[0] if positive_movers else None,
                "decrease": negative_movers[0] if negative_movers else None,
            },
            "recurring": recurring,
        }
    finally:
        conn.close()


def ai_settings_action(_params: dict[str, Any]) -> dict[str, Any]:
    from utils.ai_assist import get_settings

    conn = _connection()
    try:
        return {"ai": get_settings(conn=conn)}
    finally:
        conn.close()


def save_ai_settings_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.ai_assist import save_settings

    conn = _connection()
    try:
        # api_key is omitted rather than blanked when the user did not retype
        # it, so saving another setting cannot silently erase the key.
        values: dict[str, Any] = {}
        for name in (
            "enabled", "base_url", "model", "scope", "months", "focus",
            "style", "essential_categories",
        ):
            if name in params:
                values[name] = params[name]
        if params.get("api_key") is not None:
            values["api_key"] = params["api_key"]
        return {"ai": save_settings(conn=conn, **values)}
    finally:
        conn.close()


def forget_ai_key_action(_params: dict[str, Any]) -> dict[str, Any]:
    from utils.ai_assist import forget_key

    conn = _connection()
    try:
        return {"ai": forget_key(conn=conn)}
    finally:
        conn.close()


def ai_payload_preview_action(params: dict[str, Any]) -> dict[str, Any]:
    """Show exactly what would leave, before anything does."""
    from utils.ai_assist import build_payload, get_settings, payload_summary

    conn = _connection()
    try:
        settings = get_settings(conn=conn)
        scope = str(params.get("scope") or settings["scope"])
        months = int(params.get("months") or settings["months"])
        payload = build_payload(conn=conn, scope=scope, months=months)
        return {"preview": payload_summary(payload), "scope": scope}
    finally:
        conn.close()


def ai_coaching_summary_action(_params: dict[str, Any]) -> dict[str, Any]:
    """Local deterministic evidence shown beside the optional AI answer."""
    from utils.ai_assist import coaching_summary

    conn = _connection()
    try:
        return coaching_summary(conn=conn)
    finally:
        conn.close()


def upcoming_money_action(params: dict[str, Any]) -> dict[str, Any]:
    """Recurring money expected in the next few weeks.

    ``as_of_date`` exists so the window is testable and so a smoke run can
    pin a date. Left out, it means the real local date, because a person
    asking what is coming means the calendar rather than the last day their
    statements happen to reach.
    """
    from utils.upcoming import HORIZON_DAYS, upcoming_money

    conn = _connection()
    try:
        return upcoming_money(
            conn=conn,
            today=params.get("as_of_date") or None,
            horizon_days=int(params.get("horizon_days") or HORIZON_DAYS),
        )
    finally:
        conn.close()


def insight_feed_action(params: dict[str, Any]) -> dict[str, Any]:
    """What Northstar noticed. Works with no AI configured at all."""
    from utils.insight_feed import insight_feed

    conn = _connection()
    try:
        return insight_feed(
            conn=conn,
            share_view=str(params.get("share_view") or "personal"),
        )
    finally:
        conn.close()


def spending_patterns_action(params: dict[str, Any]) -> dict[str, Any]:
    """The chart data behind the patterns: calendar, weekdays, a year ago."""
    from utils.patterns import (
        day_of_week_profile, same_month_last_year, spending_calendar,
    )

    conn = _connection()
    try:
        share_view = str(params.get("share_view") or "personal")
        return {
            "calendar": spending_calendar(
                months=int(params.get("months") or 3), conn=conn,
                share_view=share_view,
            ),
            "day_of_week": day_of_week_profile(conn=conn,
                                               share_view=share_view),
            "year_ago": same_month_last_year(conn=conn, share_view=share_view),
        }
    finally:
        conn.close()


def net_worth_trend_action(_params: dict[str, Any]) -> dict[str, Any]:
    """Recorded months, their movement, and a prefill for the next one."""
    from utils.net_worth import net_worth_overview

    conn = _connection()
    try:
        return net_worth_overview(conn=conn)
    finally:
        conn.close()


def save_net_worth_entry_action(params: dict[str, Any]) -> dict[str, Any]:
    """Record or correct one month. The month is the key, so saving August
    twice corrects August rather than adding a second one."""
    from utils.net_worth import net_worth_overview, save_entry

    conn = _connection()
    try:
        save_entry(
            str(params.get("month") or ""),
            cash=params.get("cash"),
            investments=params.get("investments"),
            other_assets=params.get("other_assets"),
            liabilities=params.get("liabilities"),
            note=str(params.get("note") or ""),
            conn=conn,
        )
        return net_worth_overview(conn=conn)
    finally:
        conn.close()


def delete_net_worth_entry_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.net_worth import delete_entry, net_worth_overview

    month = str(params.get("month") or "")
    conn = _connection()
    try:
        if not delete_entry(month, conn=conn):
            raise ValueError(f"There is no net worth reading for {month}.")
        return net_worth_overview(conn=conn)
    finally:
        conn.close()


def export_transactions_action(params: dict[str, Any]) -> dict[str, Any]:
    """Write every transaction out as CSV.

    A local-first app that will not give the data back is only a different
    kind of lock-in. This is the whole ledger in a shape any spreadsheet or
    other finance app can read, written where the person chose to put it.
    """
    import csv

    destination = str(params.get("path") or "").strip()
    if not destination:
        raise ValueError("Choose where to save the file.")

    columns = [
        "transaction_date", "raw_description", "merchant", "amount",
        "direction", "category", "transaction_type", "account_type", "notes",
    ]
    conn = _connection()
    try:
        available = {
            str(row[1]) for row in
            conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        # Written against whatever this database actually has, so an older
        # profile exports rather than failing on a column added later.
        selected = [name for name in columns if name in available]
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM transactions "
            "ORDER BY transaction_date, id"
        ).fetchall()
        with open(destination, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(selected)
            for row in rows:
                writer.writerow(["" if value is None else value
                                 for value in row])
        return {
            "exported": True,
            "path": destination,
            "rows": len(rows),
            "columns": selected,
        }
    finally:
        conn.close()


def delete_transaction_action(params: dict[str, Any]) -> dict[str, Any]:
    """Remove a row that should not be in the ledger at all."""
    from utils.database import delete_transaction

    tx_id = int(params.get("id") or 0)
    if not tx_id:
        raise ValueError("Choose a transaction to remove.")
    conn = _connection()
    try:
        removed = delete_transaction(tx_id, conn=conn)
        return {"deleted": bool(removed), "id": tx_id}
    finally:
        conn.close()


def explain_insight_action(params: dict[str, Any]) -> dict[str, Any]:
    """Narrate one finding. The figures are computed before this is called."""
    from utils.ai_assist import explain_finding
    from utils.insight_feed import insight_feed

    finding_id = str(params.get("finding_id") or "").strip()
    if not finding_id:
        raise ValueError("Choose something to explain first.")
    conn = _connection()
    try:
        feed = insight_feed(conn=conn)
        pool = list(feed.get("concerns") or [])
        if feed.get("positive"):
            pool.append(feed["positive"])
        finding = next(
            (item for item in pool if item.get("id") == finding_id), None,
        )
        if finding is None:
            raise ValueError(
                "That finding is no longer current. Refresh and try again."
            )
        return {"explanation": explain_finding(finding, conn=conn),
                "finding": finding}
    finally:
        conn.close()


def ai_ask_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.ai_assist import ask

    question = str(params.get("question") or "").strip()
    if not question:
        raise ValueError("Ask a question first.")
    conn = _connection()
    try:
        return ask(question, conn=conn)
    finally:
        conn.close()


def test_ai_connection_action(_params: dict[str, Any]) -> dict[str, Any]:
    """Verify provider/model/key without sending any financial data."""
    from utils.ai_assist import test_connection

    conn = _connection()
    try:
        return test_connection(conn=conn)
    finally:
        conn.close()


def save_quick_net_worth_action(params: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    assets = float(params.get("total_assets") or 0)
    liabilities = float(params.get("total_liabilities") or 0)
    as_of = str(params.get("as_of_date") or date.today().isoformat()).strip()
    date.fromisoformat(as_of)
    if assets < 0 or liabilities < 0:
        raise ValueError("Assets and debts must be positive amounts.")
    conn = _connection()
    try:
        conn.execute(
            "INSERT INTO net_worth_estimates "
            "(total_assets,total_liabilities,as_of_date) VALUES (?,?,?)",
            (assets, liabilities, as_of),
        )
        conn.commit()
        return insights_summary_action({
            "period_days": int(params.get("period_days") or 30)
        })
    finally:
        conn.close()


def add_account_balance_action(params: dict[str, Any]) -> dict[str, Any]:
    """Record an explicit balance snapshot; never infer it from transactions."""
    from datetime import date
    from utils.database import get_account, insert_account_balance

    account_ref = int(params.get("account_ref") or 0)
    as_of = str(params.get("as_of_date") or "").strip()
    balance = float(params.get("balance") or 0)
    if account_ref <= 0:
        raise ValueError("Choose an existing Northstar account.")
    if balance < 0:
        raise ValueError("Enter the balance as a positive magnitude.")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("Enter a valid balance date.") from exc

    conn = _connection()
    try:
        account = get_account(account_ref, conn=conn)
        if not account or account.get("is_archived"):
            raise ValueError("That Northstar account is unavailable.")
        insert_account_balance({
            "account_ref": account_ref,
            "account_name": account["name"],
            "account_kind": account["type"],
            "balance": balance,
            "currency": account.get("currency") or "CAD",
            "as_of_date": as_of,
            "notes": str(params.get("note") or "").strip(),
        }, conn=conn)
        conn.commit()
    finally:
        conn.close()
    return planning_balances_action({})


def _planning_balances_payload(conn) -> dict[str, Any]:
    """Accounts and their latest recorded balance, and nothing else.

    Deliberately no totals. These are planning inputs for Safe to Spend and
    Plan, not a second measurement of what someone is worth: summing them
    into an "assets" or "net worth" figure is exactly the duplicate Northstar
    just removed, and it would disagree with the monthly readings the moment
    an account was missing.
    """
    from utils.database import get_account_balances

    return {
        "accounts": _accounts_payload(conn),
        "balances": get_account_balances(conn=conn, latest_only=True),
    }


def planning_balances_action(_params: dict[str, Any]) -> dict[str, Any]:
    """Read the planning balances shown in Settings."""
    conn = _connection()
    try:
        return _planning_balances_payload(conn)
    finally:
        conn.close()


def home_dashboard_action(params: dict[str, Any]) -> dict[str, Any]:
    """Chart-ready datasets for the populated Home experience.

    Everything is computed by the existing deterministic Python services —
    the frontend only renders. The current month is explicitly marked
    partial so the UI never compares it against complete months silently.
    """
    from datetime import date

    from utils.analysis_period import analysis_context
    from utils.insights import (
        category_pace_breakdown,
        monthly_aggregates,
        spending_pace,
    )

    from utils.planner import analysis_anchor

    conn = _connection()
    try:
        today = date.today()
        monthly = monthly_aggregates(conn=conn)[-12:]
        month = analysis_anchor(conn=conn)
        latest = conn.execute(
            "SELECT MAX(transaction_date) FROM transactions "
            "WHERE strftime('%Y-%m', transaction_date)=?", (month,),
        ).fetchone()[0]
        as_of = date.fromisoformat(str(latest)) if latest else today
        period = _period_breakdown(params, conn)
        cashflow = [
            {
                "month": m["month"],
                "income": m["income"],
                "spending": m["spending"],
                "net": m["net"],
                "savings_rate": m["savings_rate"],
                # The canonical completeness flag, stamped by
                # statement_coverage and carried through monthly_aggregates.
                # Comparing the month against the calendar instead marked a
                # half-imported past month complete, and the cashflow chart
                # reads complete months to pick the best and weakest month,
                # the typical savings rate and the trend direction.
                "complete": bool(m.get("complete")),
            }
            for m in monthly
        ]
        category_pace = category_pace_breakdown(
            conn=conn, today=as_of, limit=100,
        )
        category_items = [
            {
                "category": row["category"],
                "amount": row["total"],
                "share": row["pct"],
                "previous": 0.0,
                "delta": 0.0,
            }
            for row in period["categories"]
        ]
        return {
            "generated_for": today.isoformat(),
            "analysis": analysis_context(conn=conn),
            "available": bool(cashflow),
            "cashflow": cashflow,
            "period_days": period["period_days"],
            "period_label": period["period_label"],
            "period_start": period["period_start"],
            "period_end": period["period_end"],
            "pace": spending_pace(conn=conn, today=as_of),
            "flexible_pace": spending_pace(
                conn=conn, today=as_of, flexible_only=True,
            ),
            "categories": {
                "available": bool(category_items),
                "month": period["period_label"],
                "previous_month": "",
                "through_day": int(period["period_days"]),
                "total": period["cashflow"]["spending"],
                "items": category_items,
            },
            "category_pace": category_pace,
            "income_sources": period["income_sources"],
            "income_total": period["income_total"],
            "stable_income": period["stable_income"],
            "merchants": period["merchants"],
        }
    finally:
        conn.close()


def _backup_payload() -> dict[str, Any]:
    from utils import database
    from utils.backups import list_backups

    backups = list_backups(db_path=database.DB_PATH)
    return {
        "data_directory": str(database.DB_PATH.parent),
        "database_name": database.DB_PATH.name,
        "backups": [
            {
                "path": str(item["path"]),
                "name": item["name"],
                "created_at": item["created_at"].isoformat(timespec="seconds"),
                "size_bytes": int(item["size_bytes"]),
            }
            for item in backups
        ],
    }


def backup_status_action(_params: dict[str, Any]) -> dict[str, Any]:
    return _backup_payload()


def create_backup_action(_params: dict[str, Any]) -> dict[str, Any]:
    from utils import database
    from utils.backups import create_backup

    path = create_backup(reason="manual-native", db_path=database.DB_PATH)
    payload = _backup_payload()
    payload["created"] = path.name
    return payload


def restore_backup_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils import database
    from utils.backups import restore_database

    path = Path(str(params.get("path") or ""))
    result = restore_database(path, db_path=database.DB_PATH)
    payload = _backup_payload()
    payload["restored"] = Path(result["restored_from"]).name
    payload["pre_restore_backup"] = (
        Path(result["pre_restore_backup"]).name
        if result.get("pre_restore_backup") else ""
    )
    return payload


def data_safety_status_action(_params: dict[str, Any]) -> dict[str, Any]:
    """Read-only repair/reset inventory for Settings."""
    from utils.data_safety import preview_legacy_data, reset_preview

    conn = _connection()
    try:
        return {
            "repair": preview_legacy_data(conn=conn),
            "reset": reset_preview(conn=conn),
        }
    finally:
        conn.close()


def _legacy_sources_for_paths(paths: list[Path]) -> dict[int, list[dict]]:
    """Parse explicitly selected original files for their saved legacy batch."""
    from parsers.detect import detect_with_confidence
    from utils.account_assignment import (
        PDF_STATEMENT_TYPES, apply_account_to_transactions,
    )
    from utils.categorizer import enrich_transaction
    from utils.database import get_account
    from utils.financial_semantics import (
        consolidate_material_review, promote_recurring_income,
    )

    conn = _connection()
    try:
        source_batches: dict[int, list[dict]] = {}
        for path in paths:
            file_hash = hashlib.md5(path.read_bytes()).hexdigest()
            batch = conn.execute(
                """
                SELECT il.id, MIN(t.account_ref) AS account_ref
                FROM import_log il
                JOIN transactions t ON t.import_batch_id=il.id
                WHERE il.file_hash=?
                  AND (COALESCE(il.semantics_version,1)<2
                       OR COALESCE(t.transaction_type,'')=''
                       OR (t.direction='credit' AND t.amount<0)
                       OR (t.direction='debit' AND t.amount>0))
                GROUP BY il.id
                ORDER BY il.id
                LIMIT 1
                """,
                (file_hash,),
            ).fetchone()
            if batch is None:
                raise ValueError(
                    f"“{path.name}” does not match an affected imported "
                    "batch in this database. Choose the original file shown "
                    "in the repair preview."
                )
            account = get_account(int(batch["account_ref"] or 0), conn=conn)
            if not account:
                raise ValueError(
                    f"The account for “{path.name}” is missing. Northstar "
                    "stopped without changing the database."
                )
            detected, _confidence = detect_with_confidence(path)
            parsed = _parse_file(
                path, detected, conn,
                account_type=account["type"], account_id=account["name"],
            )
            transactions = parsed.get("transactions") or []
            if not transactions:
                raise ValueError(f"No transactions were read from “{path.name}”.")
            apply_account_to_transactions(
                transactions, account,
                keep_account_type=(detected in PDF_STATEMENT_TYPES),
            )
            for tx in transactions:
                enrich_transaction(tx, conn=conn)
            promote_recurring_income(transactions)
            consolidate_material_review(transactions)
            source_batches[int(batch["id"])] = transactions
        return source_batches
    finally:
        conn.close()


def preview_legacy_repair_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.data_safety import plan_legacy_repair

    paths = _checked_paths(params)
    sources = _legacy_sources_for_paths(paths)
    conn = _connection()
    try:
        return plan_legacy_repair(sources, conn=conn)
    finally:
        conn.close()


def repair_legacy_data_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.data_safety import repair_legacy_data

    paths = _checked_paths(params)
    sources = _legacy_sources_for_paths(paths)
    return repair_legacy_data(sources)


def reset_financial_data_action(params: dict[str, Any]) -> dict[str, Any]:
    from utils.data_safety import reset_all_financial_data

    if str(params.get("confirmation") or "") != "RESET":
        raise ValueError("Type RESET exactly before removing financial data.")
    return reset_all_financial_data(
        complete_reset=bool(params.get("complete_reset"))
    )


ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "home_summary": home_summary,
    "list_accounts": list_accounts_action,
    "create_account": create_account_action,
    "set_account_spending_availability": (
        set_account_spending_availability_action
    ),
    "preview_import": preview_import_action,
    "apply_csv_mapping": apply_csv_mapping_action,
    "confirm_import": confirm_import_action,
    "reset_csv_profile": reset_csv_profile_action,
    "list_transactions": list_transactions_action,
    "correct_transaction": correct_transaction_action,
    "undo_last_category_change": undo_last_category_change_action,
    "review_summary": review_summary_action,
    "shared_settings": shared_settings_action,
    "save_shared_settings": save_shared_settings_action,
    "set_shared_rule": set_shared_rule_action,
    "category_settings": category_settings_action,
    "update_category_rule": update_category_rule_action,
    "delete_category_rule": delete_category_rule_action,
    "set_recurring_preference": set_recurring_preference_action,
    "set_income_source_preference": set_income_source_preference_action,
    "add_manual_transaction": add_manual_transaction_action,
    "plan_summary": plan_summary_action,
    "preview_plan": preview_plan_action,
    "save_plan": save_plan_action,
    "goals_summary": goals_summary_action,
    "create_goal": create_goal_action,
    "update_goal": update_goal_action,
    "contribute_goal": contribute_goal_action,
    "insights_summary": insights_summary_action,
    "save_quick_net_worth": save_quick_net_worth_action,
    "ai_settings": ai_settings_action,
    "save_ai_settings": save_ai_settings_action,
    "forget_ai_key": forget_ai_key_action,
    "ai_payload_preview": ai_payload_preview_action,
    "ai_coaching_summary": ai_coaching_summary_action,
    "insight_feed": insight_feed_action,
    "upcoming_money": upcoming_money_action,
    "net_worth_trend": net_worth_trend_action,
    "save_net_worth_entry": save_net_worth_entry_action,
    "delete_net_worth_entry": delete_net_worth_entry_action,
    "export_transactions": export_transactions_action,
    "delete_transaction": delete_transaction_action,
    "spending_patterns": spending_patterns_action,
    "explain_insight": explain_insight_action,
    "ai_ask": ai_ask_action,
    "test_ai_connection": test_ai_connection_action,
    "add_account_balance": add_account_balance_action,
    "get_planning_balances": planning_balances_action,
    "home_dashboard": home_dashboard_action,
    "backup_status": backup_status_action,
    "create_backup": create_backup_action,
    "restore_backup": restore_backup_action,
    "data_safety_status": data_safety_status_action,
    "preview_legacy_repair": preview_legacy_repair_action,
    "repair_legacy_data": repair_legacy_data_action,
    "reset_financial_data": reset_financial_data_action,
}


# Actions that add, remove or replace transaction rows. The analysis anchor is
# derived from transaction dates, so these invalidate it mid-request.
_ANCHOR_INVALIDATING_ACTIONS = frozenset({
    "confirm_import",
    "delete_transaction",
    "restore_backup",
    "reset_financial_data",
})


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    from utils.analysis_period import reset_supported_bounds_cache

    action = request.get("action")
    handler = ACTIONS.get(str(action))
    if handler is None:
        raise ValueError("Unsupported Ledger desktop action.")
    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("The desktop request params must be a JSON object.")
    if str(action) in _ANCHOR_INVALIDATING_ACTIONS:
        # These rewrite the rows the anchor is measured from, so nothing
        # memoized earlier in this request may be reused afterwards.
        reset_supported_bounds_cache()
    try:
        return {
            "ok": True,
            "data": handler(params),
            "engine": "ledger-python",
        }
    finally:
        reset_supported_bounds_cache()


def run(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    logger = None
    action = "unknown"
    try:
        data_root = _data_root()
        data_root.mkdir(parents=True, exist_ok=True)
        logger = _logger(data_root)
        line = stdin.readline()
        if not line:
            raise ValueError("The desktop shell sent an empty request.")
        request = json.loads(line)
        if not isinstance(request, dict):
            raise ValueError("The desktop request must be a JSON object.")
        action = str(request.get("action") or "unknown")
        _bind_database()
        response = handle_request(request)
        logger.info("Action %s completed successfully", action)
        exit_code = 0
    except Exception as exc:
        if logger is not None:
            logger.exception("Action %s failed", action)
        response = {"ok": False, "error": str(exc)}
        exit_code = 1
    stdout.write(json.dumps(response, separators=(",", ":"), default=str) + "\n")
    stdout.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())

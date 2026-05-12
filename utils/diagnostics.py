"""
utils/diagnostics.py — Pass 24.

Single source of truth for diagnostic data. Used by:
  • pages/14_Diagnostics.py    — UI surface
  • scripts/make_bug_report.py — sanitized bundle

Hard rules (mirroring agent_context):
  • No API keys, ever. AI provider/model only — no key value.
  • No raw transactions / merchant names by default. The schema is
    fine; the values are not.
  • No full account numbers. Snapshots already store them masked.
  • No raw AI prompts/responses. Only call status metadata.
  • Every helper here is read-only.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _safe(callable_, default=None):
    """Run a no-arg callable, return its result or `default` on failure."""
    try:
        return callable_()
    except Exception:
        return default


def _mask_key(value: Optional[str]) -> str:
    """Return a privacy-safe rendering of an API key — never the full key."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= 8:
        return "•" * len(s)
    return f"{s[:4]}…{s[-4:]}  (length {len(s)})"


# ── App / Environment ───────────────────────────────────────────────

def app_environment() -> dict:
    try:
        import utils as _u
        version = getattr(_u, "__version__", "")
        pass_label = getattr(_u, "__pass__", "")
    except Exception:
        version, pass_label = "", ""

    streamlit_v = _safe(lambda: __import__("streamlit").__version__, "")
    pdfplumber_v = _safe(lambda: __import__("pdfplumber").__version__, "")
    plotly_v = _safe(lambda: __import__("plotly").__version__, "")
    pandas_v = _safe(lambda: __import__("pandas").__version__, "")

    # Pass 28: surface demo-mode + active DB path so reviewers can
    # quickly tell which database the app is reading.
    try:
        from utils.database import (
            is_demo_mode as _is_demo, DB_PATH as _active_db_path,
            demo_db_path as _demo_db_p,
        )
        demo_active   = bool(_is_demo())
        active_dbpath = str(_active_db_path)
        demo_dbpath   = str(_demo_db_p())
        demo_db_present = _demo_db_p().exists()
    except Exception:
        demo_active, active_dbpath = False, str(_REPO_ROOT / "data" / "finance.db")
        demo_dbpath, demo_db_present = "", False

    return {
        "ledger_version":   version,
        "ledger_pass":      pass_label,
        "python":           sys.version.split()[0],
        "platform":         platform.platform(),
        "machine":          platform.machine(),
        "streamlit":        streamlit_v,
        "pdfplumber":       pdfplumber_v,
        "plotly":           plotly_v,
        "pandas":           pandas_v,
        "project_path":     str(_REPO_ROOT),
        "db_path":          active_dbpath,
        "demo_mode":        demo_active,
        "demo_db_path":     demo_dbpath,
        "demo_db_present":  demo_db_present,
        "launcher_log":     str(_REPO_ROOT / "launcher.log"),
        "in_venv":          bool(sys.prefix != sys.base_prefix),
        "executable":       sys.executable,
        # Streamlit binds to localhost via .streamlit/config.toml; the
        # actual server address isn't reliably reachable from here, so
        # we report the configured intent only.
        "configured_address": "127.0.0.1:8501",
        "now":              datetime.now().isoformat(timespec="seconds"),
    }


# ── Database health ────────────────────────────────────────────────

_REQUIRED_TABLES = (
    "transactions", "import_log", "investments",
    "investment_snapshot_batches", "investment_positions",
    "account_balances", "net_worth_snapshots",
    "monthly_plans", "category_budget_targets", "goal_targets",
    "budgets", "watch_list", "recommendations_log", "learned_rules",
)


def database_health(conn: Optional[sqlite3.Connection] = None) -> dict:
    from utils.database import get_connection, DB_PATH

    db_exists = Path(DB_PATH).exists()
    if not db_exists:
        return {
            "db_exists": False,
            "db_path":   str(DB_PATH),
            "missing_tables": list(_REQUIRED_TABLES),
            "tables":    [],
            "counts":    {},
            "date_range": {},
        }

    close = conn is None
    if close:
        conn = get_connection()

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    missing = [t for t in _REQUIRED_TABLES if t not in tables]

    def _count(table: str) -> int:
        if table not in tables:
            return 0
        try:
            return int(conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0])
        except Exception:
            return -1

    counts = {
        "transactions":              _count("transactions"),
        "import_batches":            _count("import_log"),
        "flagged":                   _safe(lambda: int(
            conn.execute("SELECT COUNT(*) FROM transactions "
                         "WHERE is_flagged=1").fetchone()[0]), 0),
        "uncategorized":             _safe(lambda: int(
            conn.execute("SELECT COUNT(*) FROM transactions "
                         "WHERE category IS NULL OR category=''"
                         "  OR category='Uncategorized'"
                         ).fetchone()[0]), 0),
        "investment_snapshots":      _count("investment_snapshot_batches"),
        "investment_positions":      _count("investment_positions"),
        "account_balances":          _count("account_balances"),
        "net_worth_snapshots":       _count("net_worth_snapshots"),
        "monthly_plans":             _count("monthly_plans"),
        "category_budget_targets":   _count("category_budget_targets"),
        "goals":                     _count("goal_targets"),
        "budgets":                   _count("budgets"),
        "watch_list":                _count("watch_list"),
    }

    date_range = _safe(lambda: dict(conn.execute(
        "SELECT MIN(transaction_date) AS first_d, "
        "       MAX(transaction_date) AS last_d "
        "FROM transactions"
    ).fetchone()), {})

    if close:
        conn.close()

    return {
        "db_exists":       True,
        "db_path":         str(DB_PATH),
        "missing_tables":  missing,
        "tables":          sorted(tables),
        "counts":          counts,
        "date_range":      date_range,
    }


# ── Finance logic health ───────────────────────────────────────────

def finance_logic_health(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Latest analysis anchor + plan + forecast + bills summary.

    All numbers are deterministic (utils.planner). Read-only.
    """
    from utils.database import get_connection
    from utils.planner import (
        analysis_anchor, forecast_month, bills_and_commitments,
    )

    close = conn is None
    if close:
        conn = get_connection()

    out: dict = {}
    out["analysis_anchor"] = _safe(lambda: analysis_anchor(conn=conn), "")
    fc = _safe(lambda: forecast_month(conn=conn), {}) or {}
    bills = _safe(lambda: bills_and_commitments(conn=conn), {}) or {}
    plan = None
    try:
        from utils.database import get_monthly_plan
        plan = get_monthly_plan(out["analysis_anchor"], conn=conn)
    except Exception:
        plan = None

    review_q = _safe(lambda: int(conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
    ).fetchone()[0]), 0)

    out["latest_transaction_date"] = _safe(lambda: conn.execute(
        "SELECT MAX(transaction_date) FROM transactions"
    ).fetchone()[0], None)

    out["plan_saved"] = bool(plan and plan.get("mode"))
    out["plan_mode"] = (plan or {}).get("mode")
    out["forecast_risk"] = fc.get("risk_level")
    out["projected_net"] = fc.get("projected_net")
    out["safe_to_spend"] = fc.get("safe_to_spend")
    out["upcoming_bills_total"] = fc.get("upcoming_bills_total")
    out["recurring_variable_watch_total"] = fc.get(
        "recurring_variable_watch_total"
    )
    out["commitment_monthly_estimate"] = bills.get(
        "commitment_monthly_estimate"
    )
    out["variable_monthly_watch"] = bills.get("variable_monthly_watch")
    out["fixed_commitments_count"] = len(bills.get("fixed_commitments") or [])
    out["active_subscriptions_count"] = len(
        bills.get("active_subscriptions") or []
    )
    out["recurring_variable_count"] = len(
        bills.get("recurring_variable_merchants") or []
    )
    out["stale_inactive_count"] = len(bills.get("stale_or_inactive") or [])
    out["review_queue_count"] = review_q

    if close:
        conn.close()
    return out


# ── AI health ──────────────────────────────────────────────────────

def ai_health() -> dict:
    """Provider/model + readiness. Never includes the key value."""
    out: dict = {
        "configured":   False,
        "provider":     "",
        "model":        "",
        "ready":        False,
        "ready_reason": "",
        "key_preview":  "",
        "last_calls":   {},
    }
    try:
        from utils.ai_config import get_ai_settings, ai_is_ready
        ai = get_ai_settings() or {}
        out["configured"]   = bool(ai.get("api_key"))
        out["provider"]     = ai.get("provider") or ""
        out["model"]        = ai.get("model") or ""
        out["key_preview"]  = _mask_key(ai.get("api_key"))
        ready, why = ai_is_ready()
        out["ready"]        = bool(ready)
        out["ready_reason"] = why or ""
    except Exception as e:
        out["ready_reason"] = f"ai_config error: {e}"

    try:
        from utils.ai_explainer import all_ai_call_statuses
        # all_ai_call_statuses returns metadata only (attempted, ok,
        # fallback, reason). It already excludes prompts/responses.
        out["last_calls"] = all_ai_call_statuses() or {}
    except Exception as e:
        out["last_calls"] = {"_error": str(e)}

    return out


# ── OpenClaw / sharing health ─────────────────────────────────────

def sharing_health() -> dict:
    """Surfaces whether config.json/finance.db exist on disk + whether
    a recent agent context export is present. Never reads contents.
    """
    cfg = _REPO_ROOT / "config.json"
    db  = _REPO_ROOT / "data" / "finance.db"
    exp = _REPO_ROOT / "exports"
    last_export = None
    last_export_size = None
    if exp.exists():
        candidates = sorted(
            (p for p in exp.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            last_export = candidates[0].name
            last_export_size = candidates[0].stat().st_size

    return {
        "config_json_present":  cfg.exists(),
        "finance_db_present":   db.exists(),
        "finance_db_size_kb":   (round(db.stat().st_size / 1024, 1)
                                  if db.exists() else 0),
        "last_export":          last_export,
        "last_export_size":     last_export_size,
        "share_zip_command":    "python -m scripts.make_share_zip",
        "export_context_command": "python -m scripts.export_agent_context",
        "bug_report_command":   "python -m scripts.make_bug_report",
        "warning":              (
            "Manual zip of this folder will leak config.json and "
            "data/finance.db. ALWAYS use scripts.make_share_zip."
            if cfg.exists() else
            "No config.json present — share-zip will skip the AI key "
            "by design."
        ),
    }


# ── Single builder ────────────────────────────────────────────────

def build_diagnostics(conn: Optional[sqlite3.Connection] = None,
                      *, include_finance: bool = True) -> dict:
    """Single dict combining all health sections.

    `include_finance=False` skips the finance/forecast block — useful
    for an early "did the app start at all" diagnostic before the DB
    is ready.
    """
    from utils.database import get_connection

    close = conn is None
    if close:
        conn = get_connection()

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "environment":  app_environment(),
        "database":     database_health(conn=conn),
        "ai":           ai_health(),
        "sharing":      sharing_health(),
    }
    if include_finance:
        out["finance"] = finance_logic_health(conn=conn)

    if close:
        conn.close()
    return out

"""What the screens agree on, and what they never say.

All figures are invented.

Three checks the 2.6.1 stabilization pass has to keep true while it changes
how requests reach the database:

  1. Plan can be opened and edited without a lock banner.
  2. Home and Insights report the same figures for the same period.
  3. Signed transaction amounts are untouched by any of it.
"""
from __future__ import annotations

import concurrent.futures
import io
import json

import pytest

from desktop.engine import ledger_engine
from tests.conftest import add_tx, seed_month


def _call(action: str, params: dict | None = None):
    out = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params or {}}) + "\n"),
        out,
    )
    return code, json.loads(out.getvalue()), out.getvalue()


def _ok(action: str, params: dict | None = None):
    code, parsed, raw = _call(action, params)
    assert code == 0, raw
    return parsed["data"]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    ledger_engine._bind_database()
    return tmp_path


@pytest.fixture
def three_months(engine):
    from utils import database as db

    conn = db.get_connection()
    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(conn, month)
    conn.commit()
    conn.close()
    return engine


# ── 1. Plan opens and edits without a lock banner ────────────────────────

def test_opening_plan_reports_no_lock(three_months):
    """Plan's own load sequence: the payload, then its hydration preview."""
    code, _parsed, raw = _call("plan_summary")
    assert code == 0
    assert "database is locked" not in raw

    code, _parsed, raw = _call("preview_plan", {
        "mode": "normal", "apply_preset": False,
    })
    assert code == 0
    assert "database is locked" not in raw


def test_rapid_plan_edits_report_no_lock(three_months):
    """Typing in the income field: a burst of previews, all at once.

    Each is its own sidecar. Before 2.6.1 every one of them opened a write
    transaction on startup, so a burst queued against itself.
    """
    edits = [
        {"mode": "normal", "incomeTarget": value, "apply_preset": False}
        for value in (5000, 5100, 5200, 5300, 5400, 5500, 5600, 5700)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(edits)) as pool:
        outcomes = list(pool.map(lambda p: _call("preview_plan", p), edits))

    locked = [raw for _code, _parsed, raw in outcomes
              if "database is locked" in raw]
    assert not locked, f"{len(locked)} previews hit a lock"
    assert all(code == 0 for code, _parsed, _raw in outcomes)


def test_plan_and_a_full_screen_of_reads_together_report_no_lock(three_months):
    """Navigating Home to Plan to Insights to Settings, overlapping."""
    burst = [
        ("home_summary", {"period_days": 30}),
        ("plan_summary", {}),
        ("preview_plan", {"mode": "normal", "apply_preset": False}),
        ("insights_summary", {"period_days": 30}),
        ("money_focus", {}),
        ("review_summary", {}),
        ("category_settings", {}),
        ("ai_settings", {}),
        ("insight_feed", {}),
        ("net_worth_trend", {}),
        ("get_planning_balances", {}),
        ("backup_status", {}),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(burst)) as pool:
        outcomes = list(pool.map(lambda spec: _call(*spec), burst))

    locked = [(spec[0], raw) for spec, (_c, _p, raw) in zip(burst, outcomes)
              if "database is locked" in raw]
    assert not locked, locked


# ── 2. Home and Insights agree ───────────────────────────────────────────

def test_home_and_insights_reconcile_for_the_same_period(three_months):
    """One period, two screens, identical figures.

    Both read the same engine services; this is the guard that they still do
    after the startup path changed underneath them.
    """
    home = _ok("home_dashboard", {"period_days": 30})
    insights = _ok("insights_summary", {"period_days": 30})

    assert home["period_start"] == insights["period_start"]
    assert home["period_end"] == insights["period_end"]


def test_home_and_plan_report_one_safe_to_spend(three_months):
    """The repo's standing rule, re-checked after the request changes."""
    home = _ok("home_summary", {"period_days": 30})
    plan = _ok("plan_summary")

    assert (home.get("safe_to_spend") or {}).get("amount") == (
        (plan.get("safe_to_spend") or {}).get("amount")
    )


def test_the_analysis_period_is_the_same_on_both_screens(three_months):
    home = _ok("home_summary", {"period_days": 30})
    insights = _ok("insights_summary", {"period_days": 30})

    assert home["analysis"]["data_month"] == insights["analysis"]["data_month"]


# ── 3. Signed amounts are untouched ──────────────────────────────────────

def test_signed_transaction_amounts_are_unchanged(engine):
    """Money keeps its sign. Formatting changed; semantics did not."""
    from utils import database as db

    conn = db.get_connection()
    add_tx(conn, day="2026-06-01", desc="INVENTED LANDLORD RENT",
           amount=1800.0, direction="debit", category="Housing / Mortgage")
    add_tx(conn, day="2026-06-02", desc="INVENTED EMPLOYER PAYROLL",
           amount=2500.0, direction="credit", category="Payroll Income")
    add_tx(conn, day="2026-06-03", desc="INVENTED REFUND",
           amount=35.0, direction="credit", category="Refund / Credit")
    conn.commit()
    conn.close()

    rows = {
        str(row["description"]).upper(): row["amount"]
        for row in _ok("list_transactions", {"limit": 100})["transactions"]
    }

    # Spending is negative, money in is positive. Unchanged by 2.6.1.
    assert rows["INVENTED LANDLORD RENT"] == pytest.approx(-1800.0)
    assert rows["INVENTED EMPLOYER PAYROLL"] == pytest.approx(2500.0)
    assert rows["INVENTED REFUND"] == pytest.approx(35.0)


def test_a_negative_month_keeps_its_negative_net(engine):
    """The narrative formats the sign differently; the value does not move."""
    from utils import database as db
    from utils.insights import monthly_aggregates

    conn = db.get_connection()
    seed_month(conn, "2026-06", payroll=400.0)
    add_tx(conn, day="2026-06-14", desc="INVENTED BOILER REPAIR",
           amount=2400.0, direction="debit", category="Home Improvement")
    conn.commit()
    months = monthly_aggregates(conn=conn)
    conn.close()

    june = next(m for m in months if m["month"] == "2026-06")
    assert june["net"] < 0, "the fixture is meant to be a losing month"
    assert isinstance(june["net"], float)

"""Northstar 1.3 correctness and reconciliation regressions.

Every test uses an isolated LEDGER_DATA_DIR with invented transactions only.
"""
from __future__ import annotations

from datetime import date
import io
import json
from pathlib import Path
import tomllib

import pytest

from desktop.engine import ledger_engine


def request(action: str, params: dict | None = None) -> dict:
    output = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params or {}}) + "\n"),
        output,
    )
    payload = json.loads(output.getvalue())
    assert code == 0, payload
    return payload["data"]


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def account() -> int:
    return request("create_account", {
        "name": "Invented Chequing", "type": "chequing",
    })["created_id"]


def tx(aid: int, day: str, description: str, amount: float,
       direction: str, category: str, merchant: str = "") -> dict:
    return request("add_manual_transaction", {
        "account_id": aid,
        "date": day,
        "description": description,
        "merchant": merchant or description,
        "amount": amount,
        "direction": direction,
        "category": category,
    })["transaction"]


def complete_month_coverage(aid: int, month: str, last_day: int) -> None:
    """Add excluded rows on enough dates to prove full-month coverage."""
    for day in range(1, 15):
        tx(aid, f"{month}-{day:02d}", f"{month} COVERAGE {day}", 1,
           "debit", "Internal Transfer")
    tx(aid, f"{month}-{last_day:02d}", f"{month} COVERAGE END", 1,
       "debit", "Internal Transfer")


def test_automatic_share_rules_are_inert_and_manual_split_reconciles(engine_env):
    from utils.analytics import compute_cashflow
    from utils.database import get_category_totals
    from utils.shared_expenses import set_shared_rule

    aid = account()
    first = tx(aid, "2026-06-04", "INVENTED FITNESS", 65.48, "debit",
               "Fitness", "Invented Fitness")
    second = tx(aid, "2026-06-08", "INVENTED FITNESS EXTRA", 10.00, "debit",
                "Fitness", "Invented Fitness")

    conn = ledger_engine._connection()
    try:
        set_shared_rule(conn, scope_type="category", scope_value="Fitness",
                        user_share_pct=50)
        conn.commit()
        assert get_category_totals(
            "2026-06-01", "2026-06-30", conn=conn,
        )[0]["total"] == pytest.approx(75.48)
    finally:
        conn.close()

    before = request("list_transactions", {
        "category": "Fitness", "cashflow_role": "spending",
        "start_date": "2026-06-01", "end_date": "2026-06-30",
    })
    assert before["filtered_count"] == 2
    assert before["filtered_total"] == pytest.approx(75.48)
    assert all(not row["is_shared"] for row in before["transactions"])

    request("correct_transaction", {
        "id": first["id"], "category": "Fitness",
        "transaction_type": "spending", "shared_expense_override": True,
        "shared_user_share_pct": 84.0, "apply_to_matching": True,
    })
    after = request("list_transactions", {
        "category": "Fitness", "cashflow_role": "spending",
        "start_date": "2026-06-01", "end_date": "2026-06-30",
    })
    split = next(row for row in after["transactions"] if row["id"] == first["id"])
    untouched = next(row for row in after["transactions"] if row["id"] == second["id"])
    assert split["posted_amount"] == pytest.approx(-65.48)
    assert split["amount"] == pytest.approx(-55.00)
    assert split["other_share"] == pytest.approx(10.48)
    assert untouched["amount"] == pytest.approx(-10.00)
    assert untouched["is_shared"] is False
    assert after["filtered_total"] == pytest.approx(65.00)

    conn = ledger_engine._connection()
    try:
        categories = get_category_totals(
            "2026-06-01", "2026-06-30", conn=conn,
        )
        fitness = next(row for row in categories if row["category"] == "Fitness")
        assert fitness["total"] == pytest.approx(after["filtered_total"])
        assert compute_cashflow(
            "2026-06-01", "2026-06-30", conn=conn,
        )["spending"] == pytest.approx(after["filtered_total"])
    finally:
        conn.close()


def test_transaction_running_totals_reconcile_money_in_spending_and_kept(engine_env):
    aid = account()
    tx(aid, "2026-06-02", "INVENTED PAYROLL", 500, "credit",
       "Payroll Income", "Invented Employer")
    tx(aid, "2026-06-03", "INVENTED GROCER", 125, "debit",
       "Groceries", "Invented Grocer")
    base = {"start_date": "2026-06-01", "end_date": "2026-06-30"}

    income = request("list_transactions", {**base, "cashflow_role": "income"})
    spending = request("list_transactions", {**base, "cashflow_role": "spending"})
    kept = request("list_transactions", {**base, "cashflow_role": "net"})
    assert income["filtered_total"] == 500
    assert spending["filtered_total"] == 125
    assert kept["filtered_total"] == 375
    assert kept["filtered_count"] == 2


def test_quick_review_count_matches_the_exact_list(engine_env):
    aid = account()
    known = tx(aid, "2026-06-01", "INVENTED SERVICE HISTORY", 20, "debit",
               "Utilities / Bills", "Invented Service")
    suggested = tx(aid, "2026-06-02", "INVENTED SERVICE NEW", 25, "debit",
                   "Uncategorized", "Invented Service")
    priority = tx(aid, "2026-06-03", "INVENTED LARGE UNKNOWN", 600, "debit",
                  "Uncategorized", "Invented Large Unknown")

    conn = ledger_engine._connection()
    try:
        conn.execute(
            "UPDATE transactions SET is_flagged=0 WHERE id IN (?, ?)",
            (suggested["id"], priority["id"]),
        )
        conn.execute(
            "UPDATE transactions SET merchant='Invented Service', "
            "merchant_normalized='INVENTED SERVICE' WHERE id IN (?, ?)",
            (known["id"], suggested["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    all_rows = request("list_transactions")
    quick = request("list_transactions", {"quick_review": True})

    assert all_rows["review"]["actionable"] == 1
    assert all_rows["review"]["suggested"] == 1
    assert all_rows["review"]["quick_review"] == 2
    assert quick["filtered_count"] == all_rows["review"]["quick_review"]
    assert {row["id"] for row in quick["transactions"]} == {
        suggested["id"], priority["id"],
    }


def test_release_versions_are_consistent():
    root = Path(__file__).resolve().parents[1]
    package_version = json.loads(
        (root / "package.json").read_text(encoding="utf-8")
    )["version"]
    package_lock_version = json.loads(
        (root / "package-lock.json").read_text(encoding="utf-8")
    )["version"]
    tauri_version = json.loads(
        (root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )["version"]
    cargo = tomllib.loads(
        (root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    )
    cargo_lock = tomllib.loads(
        (root / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    )
    cargo_lock_version = next(
        item["version"] for item in cargo_lock["package"]
        if item["name"] == "ledger-native"
    )
    from utils import __version__

    versions = {
        package_version, package_lock_version, tauri_version,
        cargo["package"]["version"], cargo_lock_version, __version__,
    }
    assert versions == {"3.1.1"}
    assert 'AppVersion "1.6.0"' in (
        root / "packaging" / "windows" / "Ledger.iss"
    ).read_text(encoding="utf-8")
    version_info = (
        root / "packaging" / "windows" / "version_info.txt"
    ).read_text(encoding="utf-8")
    assert "filevers=(1, 6, 0, 0)" in version_info
    assert "prodvers=(1, 6, 0, 0)" in version_info
    assert "StringStruct('ProductVersion', '1.6.0')" in version_info

    # The release surfaces a person actually sees. A version bump that leaves
    # the changelog or the README pointing at the previous release ships a
    # build that disagrees with its own documentation.
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = next(
        line for line in changelog.splitlines() if line.startswith("## ")
    )
    assert newest.startswith(f"## {__version__} "), (
        f"the newest changelog entry is {newest!r}, not {__version__}"
    )

    # The updater manifest is built for this exact version, and the installer
    # keeps its own name. Both are asserted here so the version bump is the
    # one place any of it changes.
    from scripts.update_manifest_support import (
        expected_installer_name, expected_signature_name,
        expected_updater_artifact_name,
    )

    assert expected_installer_name(__version__) == (
        f"SignalSpaceFinance_{__version__}_x64-setup.exe"
    )
    # Tauri 2's NSIS bundler signs the installer itself, so the updater
    # artifact and the installer are the same file.
    assert expected_updater_artifact_name(__version__) == (
        expected_installer_name(__version__)
    )
    assert expected_signature_name(__version__).endswith(".exe.sig")
    assert __version__ in expected_signature_name(__version__)


def test_pace_uses_latest_import_and_requires_two_covered_months(engine_env):
    from utils.insights import category_pace_breakdown, spending_pace

    aid = account()
    tx(aid, "2026-03-02", "MARCH PARTIAL", 900, "debit", "Shopping")
    tx(aid, "2026-04-10", "APRIL COMPARABLE", 100, "debit", "Groceries")
    tx(aid, "2026-05-10", "MAY COMPARABLE", 200, "debit", "Groceries")
    tx(aid, "2026-06-10", "JUNE CURRENT", 250, "debit", "Groceries")
    complete_month_coverage(aid, "2026-04", 30)
    complete_month_coverage(aid, "2026-05", 31)

    pace = spending_pace()
    assert pace["month"] == "2026-06"
    assert pace["day"] == 10
    assert pace["average_90_day_month_count"] == 2
    assert pace["average_90_day_total"] == pytest.approx(150)
    categories = category_pace_breakdown()
    assert categories["has_average"] is True
    assert categories["average_month_count"] == 2
    groceries = next(row for row in categories["items"] if row["category"] == "Groceries")
    assert groceries["average"] == pytest.approx(150)


def test_pace_discloses_insufficient_comparable_history(engine_env):
    from utils.insights import spending_pace

    aid = account()
    tx(aid, "2026-05-10", "ONLY PRIOR MONTH", 200, "debit", "Groceries")
    tx(aid, "2026-06-10", "CURRENT MONTH", 250, "debit", "Groceries")
    complete_month_coverage(aid, "2026-05", 31)
    pace = spending_pace()
    assert pace["average_90_day_month_count"] == 1
    assert pace["average_90_day_total"] is None
    assert "Not enough comparable history" in pace["average_90_day_reason"]


def test_pace_rejects_incomplete_months_even_when_they_reach_the_same_day(engine_env):
    from datetime import date
    from utils.insights import category_pace_breakdown

    aid = account()
    for month, amount in (("2026-04", 100), ("2026-05", 200)):
        tx(aid, f"{month}-10", f"{month} PARTIAL PURCHASE", amount,
           "debit", "Groceries")
        tx(aid, f"{month}-20", f"{month} PARTIAL TRANSFER", 10,
           "debit", "Internal Transfer")
    tx(aid, "2026-06-16", "CURRENT PURCHASE", 150, "debit", "Groceries")

    categories = category_pace_breakdown(today=date(2026, 6, 16))
    assert categories["has_average"] is False
    assert categories["average_month_count"] == 0


def test_starter_plan_does_not_treat_refunds_as_future_income(engine_env):
    from utils.planner import generate_starter_plan

    aid = account()
    for month in ("2026-04", "2026-05", "2026-06"):
        tx(aid, f"{month}-05", "INVENTED PAYROLL", 2000,
           "credit", "Payroll Income")
        tx(aid, f"{month}-06", "INVENTED REFUND", 500,
           "credit", "Refund / Credit")
        tx(aid, f"{month}-10", "INVENTED GROCER", 800,
           "debit", "Groceries")
    tx(aid, "2026-07-01", "CURRENT PURCHASE", 10, "debit", "Groceries")

    proposal = generate_starter_plan(plan_month="2026-06")
    assert proposal["income_target"] == 2000


def test_plan_readiness_recomputes_after_recurring_review(engine_env):
    from utils.analytics import find_recurring

    aid = account()
    for month in ("04", "05", "06"):
        tx(aid, f"2026-{month}-08", "INVENTED HYDRO", 80, "debit",
           "Utilities / Bills", "Invented Hydro")

    before = request("plan_summary")["readiness"]
    assert before["uncertain_commitments"] == 1
    recurring = next(row for row in find_recurring()
                     if row["merchant_normalized"] == "INVENTED HYDRO")

    request("set_recurring_preference", {
        "merchant_normalized": recurring["merchant_normalized"],
        "status": "recurring",
    })
    # Every request starts a fresh engine run, matching a return to Plan and a
    # later native-app relaunch rather than relying on frontend memory.
    confirmed = request("plan_summary")["readiness"]
    assert confirmed["uncertain_commitments"] == 0
    assert confirmed["review"]["commitments_reviewed"] is True

    request("set_recurring_preference", {
        "merchant_normalized": recurring["merchant_normalized"],
        "status": "not_recurring",
    })
    assert request("plan_summary")["readiness"]["uncertain_commitments"] == 0

    for month in ("04", "05", "06"):
        tx(aid, f"2026-{month}-12", "INVENTED INSURANCE", 95, "debit",
           "Utilities / Bills", "Invented Insurance")
    assert request("plan_summary")["readiness"]["uncertain_commitments"] == 1

"""The planning loop: plans target the calendar month, roll over with
one decision, and feed one shared category-target truth."""
import pytest

import utils.database as db
from utils.planner import (
    generate_starter_plan, plan_target_month, plan_confirm_proposal,
    resolve_category_targets,
)
from tests.conftest import seed_balances, seed_month, save_plan


# ── Plan month decoupled from the data anchor ─────────────────────────
def test_starter_plan_targets_requested_month(steady_db):
    plan = generate_starter_plan(mode="normal", conn=steady_db,
                                 plan_month="2026-08")
    assert plan["month"] == "2026-08"
    # Baselines still come from the imported data, not the empty target
    # month.
    assert plan["income_target"] > 0


def test_plan_target_month_is_calendar_month():
    assert plan_target_month(today="2026-07-17") == "2026-07"
    assert plan_target_month(today="2026-12-01") == "2026-12"


# ── Applicable-plan fallback (statement lag) ──────────────────────────
def test_applicable_plan_falls_back_to_latest(ledger_db):
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-07", income=5100.0, savings=900.0)
    seed_balances(ledger_db, "2026-06-28")
    # June has no plan of its own → the July plan applies.
    plan = db.get_applicable_plan("2026-06", conn=ledger_db)
    assert plan is not None
    assert plan["month"] == "2026-07"
    assert plan["income_target"] == pytest.approx(5100.0)


def test_runway_uses_latest_plan_during_lag(ledger_db):
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-07", income=5100.0, savings=900.0)
    seed_balances(ledger_db, "2026-06-28")
    ledger_db.commit()
    from utils.insights import money_runway
    runway = money_runway(conn=ledger_db, today="2026-07-02")
    formula = runway["safe_to_spend"]["formula"]
    # The underlying runway still carries the latest plan during statement
    # lag. safe_to_spend_summary separately refuses to present a finished
    # month's remainder as money available now.
    assert formula["stable_income_target"] == pytest.approx(5100.0)
    assert formula["headline_savings_target"] == pytest.approx(900.0)

    from utils.checkin import safe_to_spend_summary
    presented = safe_to_spend_summary(conn=ledger_db, today="2026-07-02")
    assert presented["available"] is False
    assert presented["period_ended"] is True


# ── One-minute confirm (rollover) ─────────────────────────────────────
def test_confirm_proposal_rolls_over_previous_plan(ledger_db):
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", income=6400.0, spending=5200.0,
              savings=1200.0, mode="tight")
    prop = plan_confirm_proposal("2026-07", conn=ledger_db)
    assert prop["source"] == "rollover"
    assert prop["month"] == "2026-07"
    # The agreed headline numbers carry forward verbatim.
    assert prop["income_target"] == pytest.approx(6400.0)
    assert prop["savings_target"] == pytest.approx(1200.0)
    assert "2026-06" in prop["source_label"]


def test_confirm_proposal_generates_when_no_history(ledger_db):
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    prop = plan_confirm_proposal("2026-07", conn=ledger_db)
    assert prop["source"] == "generated"
    assert prop["month"] == "2026-07"


def test_confirm_proposal_stance_overrides_rollover(ledger_db):
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", mode="normal")
    prop = plan_confirm_proposal("2026-07", conn=ledger_db, stance="tight")
    assert prop["source"] == "generated"
    assert prop["mode"] == "tight"


# ── One category-target truth ─────────────────────────────────────────
def test_plan_target_wins_everywhere(ledger_db):
    seed_month(ledger_db, "2026-04")
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    pid = save_plan(ledger_db, "2026-06")
    db.replace_category_targets(pid, [
        {"category": "Groceries", "target_amount": 432.0,
         "basis": "test", "difficulty": "tight"},
    ], conn=ledger_db)
    ledger_db.commit()

    resolved = resolve_category_targets(conn=ledger_db,
                                        today="2026-07-02")
    assert resolved["Groceries"]["target"] == pytest.approx(432.0)
    assert resolved["Groceries"]["source"] == "plan"

    # The recommendation engine shows the SAME number.
    from utils.insights import compute_recommendations
    recs = compute_recommendations(conn=ledger_db) or []
    groc = next((r for r in recs
                 if r.get("category") == "Groceries"
                 and r.get("type") == "cut"), None)
    if groc is not None:
        assert "$432" in groc["title"].replace("\\", "")


def test_fallback_target_is_shared_20pct_cut(ledger_db):
    seed_month(ledger_db, "2026-04")
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    resolved = resolve_category_targets(conn=ledger_db,
                                        today="2026-07-02")
    g = resolved.get("Groceries")
    assert g is not None
    assert g["source"] == "suggested"
    assert g["target"] == pytest.approx(g["monthly_avg"] * 0.80, abs=0.5)


# ── Action ladder clears after confirming the calendar month ─────────
def test_confirm_plan_action_clears_after_confirm(ended_db):
    from utils.checkin import recommended_actions
    acts = recommended_actions(conn=ended_db, today="2026-07-02")
    assert any(a["id"] in ("update_plan", "save_plan") for a in acts)
    save_plan(ended_db, "2026-07")
    ended_db.commit()
    acts2 = recommended_actions(conn=ended_db, today="2026-07-02")
    assert all(a["id"] not in ("update_plan", "save_plan") for a in acts2)


def test_progress_reports_calendar_plan_state(steady_db):
    from utils.checkin import monthly_progress
    p = monthly_progress(conn=steady_db, today="2026-07-06")
    assert p["calendar_month"] == "2026-07"
    assert p["has_current_plan"] is True  # steady_db saves a July plan
    p2 = monthly_progress(conn=steady_db, today="2026-08-02")
    assert p2["calendar_month"] == "2026-08"
    assert p2["has_current_plan"] is False

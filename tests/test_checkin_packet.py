"""Weekly check-in packet: freshness states, monthly progress,
materiality-gated findings, the action ladder, and completion storage."""
import pytest

import utils.database as db
from utils.checkin import (
    data_freshness, monthly_progress, meaningful_changes,
    recommended_actions, weekly_checkin,
)


# ── Data freshness ────────────────────────────────────────────────────
def test_freshness_no_data(ledger_db):
    f = data_freshness(conn=ledger_db, today="2026-07-06")
    assert f["state"] == "no_data"
    assert f["days_since"] is None


def test_freshness_states_by_age(steady_db):
    # Data through 2026-07-05.
    assert data_freshness(conn=steady_db,
                          today="2026-07-06")["state"] == "current"
    assert data_freshness(conn=steady_db,
                          today="2026-07-17")["state"] == "getting_stale"
    stale = data_freshness(conn=steady_db, today="2026-08-10")
    assert stale["state"] == "stale"
    assert stale["days_since"] == 36
    assert "Import" in stale["update_action"]


def test_freshness_flags_partial_month(steady_db):
    f = data_freshness(conn=steady_db, today="2026-07-06")
    assert "2026-07" in f["incomplete_note"]


# ── Monthly progress ──────────────────────────────────────────────────
def test_monthly_progress_uses_saved_plan(steady_db):
    p = monthly_progress(conn=steady_db, today="2026-07-06")
    assert p["month"] == "2026-07"
    assert p["has_plan"] is True
    assert p["savings_target"] == pytest.approx(800.0)
    assert p["target_rate"] == pytest.approx(800.0 / 5000.0)
    assert p["savings_gap"] == pytest.approx(
        p["projected_net"] - 800.0, abs=0.02)


def test_monthly_progress_without_plan(ledger_db):
    from tests.conftest import seed_month
    seed_month(ledger_db, "2026-06")
    p = monthly_progress(conn=ledger_db)
    assert p["has_plan"] is False
    assert p["savings_target"] == 0.0
    assert p["target_rate"] is None


def test_pace_comparison_same_point(steady_db):
    p = monthly_progress(conn=steady_db, today="2026-07-06")
    pv = p["pace_vs_prev"]
    # July through 07-05: groceries 4×30 = 120.
    assert pv["spend_now"] == pytest.approx(120.0)
    # June through 06-05: rent 1800 + hydro 90 + groceries days 2-5
    # (4×30) = 2010.
    assert pv["spend_prev_same_point"] == pytest.approx(2010.0)
    assert pv["prev_month"] == "2026-06"


# ── Meaningful changes + materiality ──────────────────────────────────
def test_changes_respect_materiality_threshold(steady_db):
    changes = meaningful_changes(conn=steady_db)
    assert 1 <= len(changes) <= 3
    titles = " | ".join(c["title"] for c in changes)
    # The $200 Shopping jump is material; the $10 Pets blip is not.
    assert "Shopping" in titles
    assert "Pets" not in titles
    for c in changes:
        assert abs(c["delta"]) >= 25.0
        assert c["period"]
        assert c["title"]


def test_changes_carry_drilldown(steady_db):
    changes = meaningful_changes(conn=steady_db)
    assert changes
    for change in changes:
        drill = change.get("drill") or {}
        # The native app drills by structured fields, never a page path. The
        # retired Streamlit "page" reference was removed so a finding cannot
        # point at a screen the shipped product does not have.
        assert "page" not in drill
        assert (
            drill.get("category")
            or drill.get("merchant")
            or drill.get("cashflow_role")
        )
        if drill.get("category"):
            assert drill["cashflow_role"] == "spending"
            assert drill["start_date"] <= drill["end_date"]


# ── Action ladder ─────────────────────────────────────────────────────
def test_actions_stale_data_wins(steady_db):
    acts = recommended_actions(conn=steady_db, today="2026-08-10")
    assert acts[0]["id"] == "import_data"
    assert len(acts) <= 2


def test_stale_data_still_gives_a_truthful_retrospective(steady_db):
    packet = weekly_checkin(conn=steady_db, today="2026-08-10")

    assert packet["verdict"]["state"] == "stale_data"
    assert "latest complete month" in packet["verdict"]["headline"]
    assert "$" in packet["verdict"]["headline"]
    assert "Import your latest statement" in packet["verdict"]["sentence"]


def test_stale_retrospective_can_report_a_shortfall(ledger_db):
    from tests.conftest import seed_month

    seed_month(ledger_db, "2026-05", payroll=500.0)
    seed_month(ledger_db, "2026-06", payroll=500.0)
    packet = weekly_checkin(conn=ledger_db, today="2026-08-10")

    assert packet["verdict"]["state"] == "stale_data"
    assert "ran $" in packet["verdict"]["headline"]
    assert "short" in packet["verdict"]["headline"]


def test_actions_quiet_week_stays_within(steady_db):
    # Current data, saved plan for the current calendar month, no
    # material flags → fallback is the calm stay-within action or a
    # category-pace nudge; never more than two, never contradictory.
    acts = recommended_actions(conn=steady_db, today="2026-07-06")
    assert 1 <= len(acts) <= 2
    assert acts[0]["id"] != "import_data"


def test_actions_flagged_rows_gate_on_materiality(steady_db):
    from tests.conftest import add_tx
    add_tx(steady_db, day="2026-07-04", desc="MYSTERY CHARGE",
           amount=180.0, direction="debit", category="Uncategorized",
           is_flagged=1)
    add_tx(steady_db, day="2026-07-04", desc="TINY MYSTERY",
           amount=4.0, direction="debit", category="Uncategorized",
           is_flagged=1)
    steady_db.commit()
    acts = recommended_actions(conn=steady_db, today="2026-07-06")
    review = next((a for a in acts if a["id"] == "review_flagged"), None)
    assert review is not None
    # Only the $180 row counts toward the headline (>= $25 gate).
    assert "1 flagged transaction" in review["title"]
    assert "$180" in review["title"]


def test_actions_expired_plan_prompts_update(ended_db):
    acts = recommended_actions(conn=ended_db, today="2026-07-02")
    assert any(a["id"] in ("update_plan", "save_plan") for a in acts)


# ── Full packet + completion ─────────────────────────────────────────
def test_weekly_checkin_packet_shape(steady_db):
    p = weekly_checkin(conn=steady_db, today="2026-07-06")
    assert p["verdict"]["state"] in {
        "no_data", "stale_data", "period_ended", "insufficient",
        "behind", "tight", "on_track",
    }
    assert p["verdict"]["sentence"]
    assert len(p["changes"]) <= 3
    assert len(p["actions"]) <= 2
    assert p["materiality_threshold"] == 25.0
    assert p["last_checkin"] is None


def test_positive_safe_amount_never_overrides_danger_forecast(ledger_db):
    """Regression for the contradictory Home state found in the live app.

    A positive remaining cap can coexist with spending that is moving too
    quickly. The verdict and action must honor the danger forecast.
    """
    from tests.conftest import add_tx, save_plan, seed_balances

    add_tx(ledger_db, day="2026-07-05", desc="SYNTHETIC PAYROLL",
           amount=3000.0, direction="credit", category="Payroll Income")
    add_tx(ledger_db, day="2026-07-05", desc="SYNTHETIC LARGE PURCHASE",
           amount=2000.0, direction="debit", category="Shopping")
    save_plan(ledger_db, "2026-07", income=10000.0,
              spending=9000.0, savings=1000.0)
    seed_balances(ledger_db, "2026-07-05", liquid=7000.0, card=500.0)
    ledger_db.commit()

    packet = weekly_checkin(conn=ledger_db, today="2026-07-06")

    assert packet["safe_to_spend"]["amount"] > 0
    assert packet["progress"]["risk_level"] == "danger"
    assert packet["verdict"]["state"] == "behind"
    assert packet["verdict"]["headline"] == "Spending is running over your plan"
    assert packet["actions"][0]["id"] == "slow_pace"


def test_checkin_completion_roundtrip(steady_db):
    rid = db.insert_checkin({
        "planning_period": "2026-07",
        "data_through": "2026-07-05",
        "safe_to_spend": 1234.56,
        "primary_action": "Stay within $300 this week",
    }, conn=steady_db)
    assert rid > 0
    last = db.get_last_checkin(conn=steady_db)
    assert last["planning_period"] == "2026-07"
    assert last["safe_to_spend"] == pytest.approx(1234.56)
    p = weekly_checkin(conn=steady_db, today="2026-07-06")
    assert p["last_checkin"]["data_through"] == "2026-07-05"


def test_verdict_turns_to_the_current_month_when_calendar_moved_on(ended_db):
    """Data and plan are June; today is July.

    This asserted state == "period_ended" until 2.5.4. That is a true
    statement about June and a useless one about July: the screen is for
    planning the month you are in, and the headline described a month the
    user could no longer change. The verdict now names July and keeps June
    in the sentence underneath as context.

    The action assertions below are unchanged and still matter: pace advice
    about "the rest of the month" must not appear for a month with no data.
    """
    p = weekly_checkin(conn=ended_db, today="2026-07-15")
    assert p["verdict"]["state"] == "awaiting_current_data"
    assert "July" in p["verdict"]["headline"]
    assert "has ended" not in p["verdict"]["headline"].lower()
    assert "June" in p["verdict"]["sentence"]
    assert all(action["id"] != "slow_pace" for action in p["actions"])
    assert all(
        "rest of the month" not in action["title"].lower()
        for action in p["actions"]
    )


def test_actions_never_say_stay_within_negative(steady_db):
    from utils.database import upsert_monthly_plan
    upsert_monthly_plan({
        "month": "2026-07", "mode": "tight",
        "income_target": 5000.0, "spending_target": 3400.0,
        "savings_target": 50000.0, "notes": "stress",
    }, conn=steady_db)
    steady_db.commit()
    acts = recommended_actions(conn=steady_db, today="2026-07-06")
    assert all(a["id"] != "stay_within" for a in acts)
    assert any(a["id"] == "pause_flex" for a in acts) or acts

"""Plan simplification — the review queue, and the bug behind it.

All transactions and plans are invented. Real use of the 2.7.0 installer
reported that "Review recurring costs" reappeared no matter how often it was
used. It was not a UI problem: the preference persisted and the read ignored
it, so the queue item could never be cleared. These tests pin the read, and
pin the surfaces the simplified Plan is allowed to show.
"""
from __future__ import annotations

import pathlib

import pytest

from tests.conftest import add_tx, save_plan, seed_month

PLAN_VIEW = pathlib.Path(__file__).resolve().parents[1] / "desktop/src/PlanView.tsx"


def _quarterly_bill(conn) -> None:
    """A bill seen twice, three months apart: nonmonthly and low confidence."""
    for day in ("2026-03-09", "2026-06-09"):
        add_tx(conn, day=day, desc="CITY WATER QUARTERLY", amount=210,
               direction="debit", category="Utilities / Bills",
               merchant="City Water")
    conn.commit()


def _uncertain(conn) -> list[dict]:
    from utils.analytics import recurring_status_is_reviewed
    from utils.planner import bills_and_commitments

    scale = {"high": 1.0, "medium": 0.65, "low": 0.3}
    out = []
    for item in bills_and_commitments(conn=conn).get("items", []):
        raw = item.get("confidence")
        confidence = (float(raw) if isinstance(raw, (int, float))
                      else scale.get(str(raw or "").lower(), 0.0))
        if (item.get("included_in_forecast")
                and not recurring_status_is_reviewed(item.get("recurring_status"))
                and confidence < 0.8):
            out.append(item)
    return out


# ── the read bug ──────────────────────────────────────────────────────────

def test_confirming_a_nonmonthly_bill_actually_clears_it(ledger_db):
    """The cadence pass has to read the same preference table as the rest.

    It hardcoded "automatic", so a quarterly bill stayed uncertain forever
    however many times the user confirmed it in Settings.
    """
    from utils.database import set_recurring_preference

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)

    pending = _uncertain(ledger_db)
    assert [i["merchant"] for i in pending] == ["City Water"]
    assert pending[0]["group"] == "nonmonthly_commitments"

    set_recurring_preference("CITY WATER QUARTERLY", "recurring", conn=ledger_db)
    ledger_db.commit()

    assert _uncertain(ledger_db) == []


def test_marking_a_nonmonthly_bill_not_recurring_also_resolves_it(ledger_db):
    """Either explicit decision is a decision; only silence is unreviewed."""
    from utils.database import set_recurring_preference

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)
    set_recurring_preference("CITY WATER QUARTERLY", "not_recurring",
                             conn=ledger_db)
    ledger_db.commit()

    assert _uncertain(ledger_db) == []


def test_an_unreviewed_nonmonthly_bill_is_still_reported(ledger_db):
    """The fix must not silently mark everything reviewed."""
    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)

    assert [i["merchant"] for i in _uncertain(ledger_db)] == ["City Water"]


# ── saving persists, and says so ──────────────────────────────────────────

def test_saving_a_plan_persists_and_reports_itself_saved(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload, save_plan_action

    seed_month(ledger_db, "2026-07")
    payload = _plan_payload(ledger_db, today="2026-08-17")
    assert payload["saved"] is None

    form = payload["working_plan"]
    save_plan_action({
        "month": payload["month"], "mode": "normal",
        "income_target": form["income_target"],
        "fixed_obligations": form["fixed_obligations"],
        "flexible_allowance": payload["equation"]["flexible"],
        "savings_target": form["savings_target"],
        "safety_buffer": form["safety_buffer"],
        "notes": "", "fixed_override_reason": "",
    })

    reloaded = _plan_payload(ledger_db, today="2026-08-17")
    assert reloaded["saved"] is not None
    assert reloaded["saved"]["month"] == payload["month"]
    # The screen reads "Saved" off this stamp, so it has to survive a reload.
    assert reloaded["saved"]["updated_at"]
    assert reloaded["saved"]["savings_target"] == pytest.approx(
        form["savings_target"]
    )


def test_editing_a_saved_plan_persists_the_new_amount(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload, save_plan_action

    seed_month(ledger_db, "2026-07")
    base = _plan_payload(ledger_db, today="2026-08-17")
    form = base["working_plan"]

    def store(savings: float) -> None:
        payload = _plan_payload(ledger_db, today="2026-08-17")
        save_plan_action({
            "month": payload["month"], "mode": "normal",
            "income_target": form["income_target"],
            "fixed_obligations": form["fixed_obligations"],
            "flexible_allowance": 0,
            "savings_target": savings,
            "safety_buffer": form["safety_buffer"],
            "notes": "", "fixed_override_reason": "",
        })

    store(400)
    assert _plan_payload(ledger_db, today="2026-08-17")["saved"][
        "savings_target"] == pytest.approx(400)
    store(925)
    after = _plan_payload(ledger_db, today="2026-08-17")
    assert after["saved"]["savings_target"] == pytest.approx(925)
    # One row per month, updated in place rather than duplicated.
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM monthly_plans WHERE month=?",
        (after["month"],),
    ).fetchone()[0] == 1


def test_a_saved_plan_no_longer_asks_to_be_confirmed(ledger_db):
    """The queue item that made a real save look like a failed one."""
    from desktop.engine.ledger_engine import _plan_payload

    seed_month(ledger_db, "2026-07")
    save_plan(ledger_db, "2026-08", savings=500)

    payload = _plan_payload(ledger_db, today="2026-08-17")
    assert "plan" not in [item["id"] for item in payload["readiness"]["missing"]]


# ── what Plan is allowed to interrupt for ─────────────────────────────────

def test_plan_notice_is_absent_when_nothing_matters(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    for month in ("2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)

    assert _plan_payload(ledger_db, today="2026-08-17")["plan_notice"] is None


def test_plan_notice_speaks_up_when_there_is_no_history(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    notice = _plan_payload(ledger_db, today="2026-08-17")["plan_notice"]

    assert notice is not None
    assert notice["level"] == "info"
    assert notice["screen"] == "add-data"


def test_plan_notice_is_at_most_one_thing(ledger_db):
    """Never a queue again: the field is a single object or nothing."""
    from desktop.engine.ledger_engine import _plan_payload

    notice = _plan_payload(ledger_db, today="2026-08-17")["plan_notice"]

    assert notice is None or isinstance(notice, dict)


# ── the screen itself ─────────────────────────────────────────────────────

def test_plan_no_longer_renders_a_review_queue() -> None:
    source = PLAN_VIEW.read_text(encoding="utf-8")

    for gone in ("readiness.missing", "readiness.advisories", "plan-readiness",
                 "income-confirmation-card", "MoneyFocusCard",
                 "commitment-review", "Monthly plan presets"):
        assert gone not in source, gone


def test_plan_reads_its_saved_state_from_the_stored_row() -> None:
    """Not from local state: a restart has to show the same thing."""
    source = PLAN_VIEW.read_text(encoding="utf-8")

    assert "const savedRecord = data?.saved" in source
    assert "savedOn(savedRecord?.updated_at)" in source


def test_plan_does_no_money_arithmetic() -> None:
    """The engine supplies every figure, including the differences."""
    source = PLAN_VIEW.read_text(encoding="utf-8")

    for banned in ("actual_kept -", "intended_kept -", "income_target -",
                   "flexible +", "- savings_target"):
        assert banned not in source, banned

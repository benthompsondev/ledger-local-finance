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


def _quarterly_bill_three_times(conn) -> None:
    """Three observations also reach the monthly recurring detector."""
    for day in ("2025-12-09", "2026-03-09", "2026-06-09"):
        add_tx(conn, day=day, desc="CITY WATER QUARTERLY", amount=210,
               direction="debit", category="Utilities / Bills",
               merchant="City Water")
    conn.commit()


def _monthly_subscription(conn) -> None:
    for day, amount in (("2026-05-04", 20), ("2026-06-04", 20),
                        ("2026-07-04", 25)):
        add_tx(conn, day=day, desc="DEMO VIDEO SUBSCRIPTION", amount=amount,
               direction="debit", category="Subscriptions & Digital",
               merchant="Demo Video")
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


def test_confirmed_renamed_quarterly_bill_stays_one_nonmonthly_reserve(ledger_db):
    """Confirmation and display edits must not turn a quarter into a month."""
    from utils.database import set_recurring_preference
    from utils.planner import bills_and_commitments, regular_monthly_fixed_total

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill_three_times(ledger_db)
    set_recurring_preference(
        "CITY WATER QUARTERLY", "recurring",
        display_name="City Water Bill", category="Utilities / Bills",
        conn=ledger_db,
    )
    ledger_db.commit()

    bills = bills_and_commitments(conn=ledger_db)
    water = [
        item for item in bills["items"]
        if item.get("merchant_normalized") == "CITY WATER QUARTERLY"
    ]

    assert len(water) == 1
    assert water[0]["merchant"] == "City Water Bill"
    assert water[0]["group"] == "nonmonthly_commitments"
    assert water[0]["recurring_status"] == "recurring"
    assert water[0]["monthly_setaside"] == pytest.approx(70)
    assert bills["nonmonthly_monthly_reserve"] == pytest.approx(70)
    assert regular_monthly_fixed_total(bills) == pytest.approx(
        sum(
            float(item.get("monthly_setaside") or item.get("est_amount") or 0)
            for item in bills["items"]
            if item.get("included_in_forecast")
            and item.get("merchant_normalized") != "CITY WATER QUARTERLY"
            and item.get("group") != "nonmonthly_commitments"
        )
    )


def test_marking_a_nonmonthly_bill_not_recurring_also_resolves_it(ledger_db):
    """Not recurring must remove both the warning and the reserved money."""
    from utils.database import set_recurring_preference
    from utils.planner import bills_and_commitments

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)
    set_recurring_preference("CITY WATER QUARTERLY", "not_recurring",
                             conn=ledger_db)
    ledger_db.commit()

    assert _uncertain(ledger_db) == []
    bills = bills_and_commitments(conn=ledger_db)
    assert "City Water" not in [
        item["merchant"] for item in bills["nonmonthly_commitments"]
    ]
    assert bills["nonmonthly_monthly_reserve"] == pytest.approx(0)


def test_an_unreviewed_nonmonthly_bill_is_still_reported(ledger_db):
    """The fix must not silently mark everything reviewed."""
    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)

    assert [i["merchant"] for i in _uncertain(ledger_db)] == ["City Water"]


def test_nonmonthly_bill_can_be_reviewed_from_native_settings(ledger_db):
    """An automatic quarterly bill must be exposed before a preference exists."""
    from desktop.engine.ledger_engine import category_settings_action

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)

    settings = category_settings_action({})
    city_water = [
        item for item in settings["recurring"]
        if item["merchant_normalized"] == "CITY WATER QUARTERLY"
    ]

    assert len(city_water) == 1
    assert city_water[0]["cadence"] == "quarterly"


def test_monthly_subscription_preference_is_not_bypassed(ledger_db):
    """Subscription detection must use the same saved status as cadence."""
    from utils.database import set_recurring_preference
    from utils.planner import bills_and_commitments

    for month in ("2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _monthly_subscription(ledger_db)

    set_recurring_preference("DEMO VIDEO", "recurring", conn=ledger_db)
    ledger_db.commit()
    confirmed = [
        item for item in bills_and_commitments(conn=ledger_db)["items"]
        if item.get("merchant_normalized") == "DEMO VIDEO"
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["recurring_status"] == "recurring"

    set_recurring_preference("DEMO VIDEO", "not_recurring", conn=ledger_db)
    ledger_db.commit()
    assert "DEMO VIDEO" not in [
        item.get("merchant_normalized")
        for item in bills_and_commitments(conn=ledger_db)["items"]
    ]


def test_nonmonthly_bill_is_not_also_presented_as_a_monthly_fixed_cost(ledger_db):
    """Plan provenance must match the equation's single-charge treatment."""
    from desktop.engine.ledger_engine import _plan_payload

    for month in ("2026-04", "2026-05", "2026-06", "2026-07"):
        seed_month(ledger_db, month)
    _quarterly_bill(ledger_db)

    payload = _plan_payload(ledger_db, today="2026-08-17")

    assert "City Water" not in [
        item["merchant"] for item in payload["fixed_commitments"]
    ]
    assert [
        item["merchant"] for item in payload["nonmonthly_commitments"]
    ] == ["City Water"]


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


def test_unsaved_plan_uses_preference_and_resave_preserves_hidden_notes() -> None:
    source = PLAN_VIEW.read_text(encoding="utf-8")

    assert "const preference = readSavingsPreference()" in source
    assert "applyPreference: true" in source
    assert "savingsPreferenceStyle: preference.style" in source
    assert "savingsPreferenceValue: preference.value" in source
    assert 'notes: data.saved?.notes ?? ""' in source


def test_plan_does_no_money_arithmetic() -> None:
    """The engine supplies every figure, including the differences."""
    source = PLAN_VIEW.read_text(encoding="utf-8")

    for banned in ("actual_kept -", "intended_kept -", "income_target -",
                   "flexible +", "- savings_target",
                   "actual_kept_abs ?? Math.abs"):
        assert banned not in source, banned

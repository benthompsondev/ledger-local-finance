"""One planning income, agreed by every layer.

All transactions and plans are invented; nothing here reads real data.

Northstar 2.7.1 shipped two definitions of "Money coming in". The Plan screen
displayed `typical_monthly_income` — the average of complete months, which is
also what Safe to Spend and the planner read — while `save_plan` validated the
submitted figure against `reliable_income_summary`, which medians each source
across only the months that source appeared in. A source arriving in three
months out of six is counted by the second as if it arrived in all six, so the
two disagree for anyone whose deposits are not perfectly regular. Any
difference of one cent rejected the save with "Monthly planning income is
anchored to the reliable income floor of $X", and the plan could never be
saved at all.

`test_the_two_income_functions_can_disagree` pins the condition itself, so
these stay meaningful even if the underlying estimators change.
"""
from __future__ import annotations

import pytest

from tests.conftest import add_tx, seed_month

MONTHS = ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07")
TODAY = "2026-08-17"


def _mismatched_income(ledger_db) -> None:
    """Data where the two income estimators genuinely disagree.

    Regular payroll every month, plus a second confirmed source that only
    lands in half of them.
    """
    from desktop.engine.ledger_engine import (
        _plan_payload, set_income_source_preference_action,
    )

    for month in MONTHS:
        seed_month(ledger_db, month)
    for month in ("2026-03", "2026-05", "2026-07"):
        add_tx(ledger_db, day=f"{month}-16", desc="CONTRACT CLIENT PAYOUT",
               amount=1500, direction="credit", category="Payroll Income",
               merchant="Contract Client")
    ledger_db.commit()
    payload = _plan_payload(ledger_db, today=TODAY)
    for candidate in payload["income_confirmation_candidates"]:
        set_income_source_preference_action({
            "source_normalized": candidate["source_normalized"],
            "status": "confirmed",
        })


def _save_what_the_screen_shows(ledger_db, payload, **overrides) -> None:
    """Submit exactly the user's three figures, as the redesigned Plan does."""
    from desktop.engine.ledger_engine import save_plan_action

    form = payload["working_plan"]
    spec = {
        "month": payload["month"], "mode": "normal",
        "fixed_obligations": form["fixed_obligations"],
        "savings_target": form["savings_target"],
        "safety_buffer": form["safety_buffer"],
        "notes": "", "fixed_override_reason": "",
    }
    spec.update(overrides)
    save_plan_action(spec)


# ── the condition this whole file exists for ──────────────────────────────

def test_the_two_income_functions_can_disagree(ledger_db):
    """The fixture is only useful while it still reproduces the mismatch."""
    from utils.analytics import reliable_income_summary, typical_monthly_income

    _mismatched_income(ledger_db)

    typical = round(float(typical_monthly_income(conn=ledger_db)["amount"]), 2)
    reliable = reliable_income_summary(conn=ledger_db)

    assert reliable["confirmed"] is True
    assert abs(typical - round(float(reliable["monthly_amount"]), 2)) >= 0.01


# ── one authoritative figure ──────────────────────────────────────────────

def test_save_succeeds_on_the_income_the_screen_showed(ledger_db):
    """The 2.7.1 failure: this raised the reliable-income-floor error."""
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)
    assert payload["saved"] is None

    _save_what_the_screen_shows(ledger_db, payload)

    assert _plan_payload(ledger_db, today=TODAY)["saved"] is not None


def test_displayed_income_is_the_completed_month_average(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload
    from utils.analytics import typical_monthly_income

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)

    expected = round(float(typical_monthly_income(conn=ledger_db)["amount"]), 2)
    assert payload["working_plan"]["income_target"] == pytest.approx(expected)
    assert payload["equation"]["income"] == pytest.approx(expected)
    assert payload["income_basis"]["amount"] == pytest.approx(expected)


def test_preview_prices_the_month_with_that_same_income(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload, preview_plan_action

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)
    form = payload["working_plan"]

    preview = preview_plan_action({
        "mode": "normal",
        "fixed_obligations": form["fixed_obligations"],
        "savings_target": form["savings_target"],
        "safety_buffer": form["safety_buffer"],
        "apply_preset": False,
    })

    assert preview["equation"]["income"] == pytest.approx(
        payload["equation"]["income"]
    )
    assert preview["equation"]["flexible"] == pytest.approx(
        payload["equation"]["flexible"]
    )


def test_the_saved_row_records_the_engine_income(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)
    _save_what_the_screen_shows(ledger_db, payload)

    saved = _plan_payload(ledger_db, today=TODAY)["saved"]
    assert saved["income_target"] == pytest.approx(payload["equation"]["income"])


# ── the client cannot supply a derived figure ─────────────────────────────

def test_a_client_supplied_income_is_ignored(ledger_db):
    """Even a hostile or stale caller cannot move the planning income."""
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)
    engine_income = payload["equation"]["income"]

    _save_what_the_screen_shows(ledger_db, payload, income_target=99_000.0)

    saved = _plan_payload(ledger_db, today=TODAY)["saved"]
    assert saved["income_target"] == pytest.approx(engine_income)


def test_a_client_supplied_flexible_allowance_is_ignored(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)

    _save_what_the_screen_shows(ledger_db, payload, flexible_allowance=99_000.0)

    saved = _plan_payload(ledger_db, today=TODAY)["saved"]
    assert saved["flexible_allowance"] == pytest.approx(
        payload["equation"]["flexible"]
    )


def test_preview_ignores_a_client_supplied_income(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload, preview_plan_action

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)
    form = payload["working_plan"]

    preview = preview_plan_action({
        "mode": "normal", "income_target": 99_000.0,
        "fixed_obligations": form["fixed_obligations"],
        "savings_target": form["savings_target"],
        "safety_buffer": form["safety_buffer"],
        "apply_preset": False,
    })

    assert preview["equation"]["income"] == pytest.approx(
        payload["equation"]["income"]
    )


# ── legitimate rejections still happen ────────────────────────────────────

def test_an_incoherent_plan_is_still_refused(ledger_db):
    """Allocating more than comes in must fail, on the engine's own income."""
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)

    with pytest.raises(ValueError):
        _save_what_the_screen_shows(
            ledger_db, payload, savings_target=payload["equation"]["income"],
        )
    assert _plan_payload(ledger_db, today=TODAY)["saved"] is None


def test_a_negative_amount_is_still_refused(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    payload = _plan_payload(ledger_db, today=TODAY)

    with pytest.raises(ValueError):
        _save_what_the_screen_shows(ledger_db, payload, savings_target=-5.0)


def test_no_history_refuses_with_a_reason_the_user_can_act_on(ledger_db):
    """No complete month means no income basis, so there is nothing to save."""
    from desktop.engine.ledger_engine import save_plan_action

    with pytest.raises(ValueError, match="complete month"):
        save_plan_action({
            "month": "2026-08", "mode": "normal",
            "fixed_obligations": 0, "savings_target": 0, "safety_buffer": 0,
            "notes": "", "fixed_override_reason": "",
        })


def test_the_reliable_floor_error_is_gone(ledger_db):
    """The message that made the screen unusable must not be reachable."""
    import pathlib

    engine = (pathlib.Path(__file__).resolve().parents[1]
              / "desktop/engine/ledger_engine.py").read_text(encoding="utf-8")

    assert "anchored to the reliable" not in engine


# ── persistence, and editing again ────────────────────────────────────────

def test_editing_the_amount_to_keep_persists(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    _mismatched_income(ledger_db)
    _save_what_the_screen_shows(
        ledger_db, _plan_payload(ledger_db, today=TODAY), savings_target=400,
    )
    assert _plan_payload(ledger_db, today=TODAY)["saved"][
        "savings_target"] == pytest.approx(400)

    _save_what_the_screen_shows(
        ledger_db, _plan_payload(ledger_db, today=TODAY), savings_target=925,
    )
    after = _plan_payload(ledger_db, today=TODAY)
    assert after["saved"]["savings_target"] == pytest.approx(925)
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM monthly_plans WHERE month=?", (after["month"],),
    ).fetchone()[0] == 1


# ── why the two figures differ, pinned ────────────────────────────────────

def test_reliable_income_overstates_an_intermittent_source(ledger_db):
    """The mechanism behind the shipped failure, stated as behaviour.

    `reliable_income_summary` medians each source across only the months it
    appeared in, so a source arriving in three months out of six contributes
    its full monthly amount as though it arrived in all six. That is why it
    can sit above the average month, and why it must never price a plan. Its
    docstring once called this conservative; it is the opposite.
    """
    from utils.analytics import reliable_income_summary, typical_monthly_income
    from utils.database import set_income_source_preference

    _mismatched_income(ledger_db)
    set_income_source_preference(
        "CONTRACT CLIENT PAYOUT", "confirmed", conn=ledger_db,
    )
    ledger_db.commit()

    typical = round(float(typical_monthly_income(conn=ledger_db)["amount"]), 2)
    reliable = reliable_income_summary(conn=ledger_db)
    contract = next(
        source for source in reliable["sources"]
        if source["source_normalized"] == "CONTRACT CLIENT PAYOUT"
    )

    # Counted at its full per-arrival size, despite landing every other month.
    assert contract["monthly_amount"] == pytest.approx(1500.0)
    # And it lands in only three of the six months that were averaged.
    assert typical_monthly_income(conn=ledger_db)["months_used"] == 6
    # So the "floor" sits above the honest monthly average.
    assert round(float(reliable["monthly_amount"]), 2) > typical


def test_confirming_a_source_does_not_move_the_planning_amount(ledger_db):
    """Confirming a source must not move the planning figure.

    `reliable_income_summary` owns review state. If confirmation could also
    move the amount, the two would be coupled again by a different route: a
    whole-month average has no eligibility gate for a confirmation to open,
    so the deposits are already in it.

    Exclusion is the deliberate exception, covered in
    `test_income_source_exclusion.py`. It does not recouple the two figures —
    it removes deposits from the one figure's input, and
    `reliable_income_summary` still prices nothing.
    """
    from desktop.engine.ledger_engine import _plan_payload
    from utils.database import set_income_source_preference

    _mismatched_income(ledger_db)
    before = _plan_payload(ledger_db, today=TODAY)["equation"]["income"]

    set_income_source_preference(
        "CONTRACT CLIENT PAYOUT", "confirmed", conn=ledger_db,
    )
    ledger_db.commit()
    after = _plan_payload(ledger_db, today=TODAY)["equation"]["income"]

    assert after == pytest.approx(before)


def test_excluding_a_source_still_leaves_one_planning_figure(ledger_db):
    """The exclusion moves the amount without reviving the second opinion.

    2.7.1 failed because `save_plan` priced the month with
    `reliable_income_summary` while the screen showed
    `typical_monthly_income`. An exclusion must not reopen that gap: after
    it, display, preview and save must still agree, and they must agree on
    the complete-month average rather than on the reliable figure.
    """
    from desktop.engine.ledger_engine import _plan_payload
    from utils.analytics import reliable_income_summary, typical_monthly_income
    from utils.database import set_income_source_preference

    _mismatched_income(ledger_db)
    set_income_source_preference(
        "CONTRACT CLIENT PAYOUT", "excluded", conn=ledger_db,
    )
    ledger_db.commit()

    payload = _plan_payload(ledger_db, today=TODAY)
    typical = round(float(typical_monthly_income(conn=ledger_db)["amount"]), 2)
    reliable = reliable_income_summary(conn=ledger_db)

    assert payload["equation"]["income"] == pytest.approx(typical)
    assert payload["income_basis"]["amount"] == pytest.approx(typical)
    # The excluded source is gone from both, and the plan still prices the
    # month from the average rather than from the source-median figure.
    assert all(
        source["source_normalized"] != "CONTRACT CLIENT PAYOUT"
        for source in reliable["sources"]
    )
    _save_what_the_screen_shows(ledger_db, payload)
    assert _plan_payload(ledger_db, today=TODAY)["saved"] is not None

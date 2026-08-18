"""Excluding an income source removes it from the plan.

All transactions and plans are invented; nothing here reads real data.

Insights and Settings both offer one choice per income source: use it as
stable income, or exclude it. Through 2.8.0 the exclusion wrote its row and
no planning number read it. `reliable_income_summary` honoured it, but 2.7.2
made `typical_monthly_income` the sole planning income and left
`reliable_income_summary` pricing nothing, so telling Northstar a deposit was
not income changed no figure anywhere. The control was decoration.

The exclusion is now applied once, in `_planning_income_deposits`, which is
the shared definition of a planning-income deposit. That keeps one planning
figure: the exclusion changes what goes into `typical_monthly_income`, it
does not introduce a second opinion about the amount.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import add_tx, seed_month

MONTHS = ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07")
TODAY = "2026-08-17"
# `seed_month` pays twice a month under two descriptors.
PAYROLL_SOURCES = ("EMPLOYER PAYROLL", "EMPLOYER PAYROLL 2")
SIDE_SOURCE = "SIDE STUDIO PAYROLL"


def _payroll_plus_side_income(ledger_db) -> None:
    """Regular payroll every month, plus a second real income source.

    The side source is `employment_income` and lands in every month, so
    nothing about it is marginal: it is genuine planning income until the
    user says otherwise.
    """
    for month in MONTHS:
        seed_month(ledger_db, month)
        add_tx(ledger_db, day=f"{month}-16", desc="SIDE STUDIO PAYROLL",
               amount=800, direction="credit", category="Payroll Income",
               merchant="Side Studio")
    ledger_db.commit()


def _exclude(ledger_db, source_normalized: str) -> None:
    from utils.database import set_income_source_preference

    set_income_source_preference(source_normalized, "excluded", conn=ledger_db)
    ledger_db.commit()


# ── the control moves the number it claims to ─────────────────────────────

def test_excluding_a_source_lowers_the_planning_baseline(ledger_db):
    """The defect: a genuine source stayed in the average after exclusion."""
    from utils.analytics import typical_monthly_income

    _payroll_plus_side_income(ledger_db)
    before = typical_monthly_income(conn=ledger_db)["amount"]

    _exclude(ledger_db, SIDE_SOURCE)
    after = typical_monthly_income(conn=ledger_db)["amount"]

    # $800 every month, so the monthly average drops by exactly that.
    assert before - after == pytest.approx(800.0)


def test_exclusion_reaches_safe_to_spend(ledger_db):
    """Safe to Spend reads the same baseline, so it moves too.

    This is the point of the change rather than a side effect: a user who
    says a deposit is not income should not be told they can spend it.
    """
    from utils.checkin import flow_safe_to_spend

    _payroll_plus_side_income(ledger_db)
    before = flow_safe_to_spend(ledger_db, today=TODAY)

    _exclude(ledger_db, SIDE_SOURCE)
    after = flow_safe_to_spend(ledger_db, today=TODAY)

    assert before is not None and after is not None
    assert after["remaining"] < before["remaining"]


def test_the_plan_screen_offers_the_lower_income(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload
    from utils.analytics import typical_monthly_income

    _payroll_plus_side_income(ledger_db)
    _exclude(ledger_db, SIDE_SOURCE)

    payload = _plan_payload(ledger_db, today=TODAY)
    expected = round(
        float(typical_monthly_income(conn=ledger_db)["amount"]), 2
    )

    assert payload["working_plan"]["income_target"] == pytest.approx(expected)
    assert payload["equation"]["income"] == pytest.approx(expected)
    assert payload["income_basis"]["amount"] == pytest.approx(expected)


def test_the_button_itself_moves_the_plan(ledger_db):
    """End to end through the action the Insights control actually calls.

    The preference write and the plan read happen on different connections
    in the running app, so exercising the helper alone would not prove the
    screen sees the change.
    """
    from desktop.engine.ledger_engine import (
        _plan_payload, set_income_source_preference_action,
    )

    _payroll_plus_side_income(ledger_db)
    before = _plan_payload(ledger_db, today=TODAY)["equation"]["income"]

    settings = set_income_source_preference_action({
        "source_normalized": SIDE_SOURCE, "status": "excluded",
    })
    after = _plan_payload(ledger_db, today=TODAY)["equation"]["income"]

    assert before - after == pytest.approx(800.0)
    # And the screen that owns the control reports the new state back.
    assert next(
        row for row in settings["income_sources"]
        if row["source_normalized"] == SIDE_SOURCE
    )["status"] == "excluded"


def test_an_excluded_source_is_not_expected_to_arrive(ledger_db):
    """The arrival outlook reads the same deposits, so it agrees.

    Otherwise Plan would drop the money from the baseline and still report
    it as income that has not turned up yet.
    """
    from utils.analytics import scheduled_income_outlook

    _payroll_plus_side_income(ledger_db)
    _exclude(ledger_db, SIDE_SOURCE)

    outlook = scheduled_income_outlook(
        "2026-07", anchor=date(2026, 7, 31), conn=ledger_db,
    )

    assert all(
        "SIDE STUDIO" not in source["source"].upper()
        for source in outlook["sources"]
    )


def test_undoing_the_exclusion_restores_the_baseline(ledger_db):
    """Nothing is destroyed, so the choice is reversible from the screen."""
    from utils.analytics import typical_monthly_income
    from utils.database import set_income_source_preference

    _payroll_plus_side_income(ledger_db)
    before = typical_monthly_income(conn=ledger_db)["amount"]

    _exclude(ledger_db, SIDE_SOURCE)
    set_income_source_preference(
        SIDE_SOURCE, "confirmed", conn=ledger_db,
    )
    ledger_db.commit()

    assert typical_monthly_income(
        conn=ledger_db
    )["amount"] == pytest.approx(before)


# ── what exclusion must NOT touch ─────────────────────────────────────────

def test_confirming_a_source_still_moves_nothing(ledger_db):
    """Only exclusion removes money. Confirming stays inert.

    A whole-month average has no eligibility gate for confirmation to open,
    and coupling it to review state is exactly the 2.7.1 defect.
    """
    from utils.analytics import typical_monthly_income
    from utils.database import set_income_source_preference

    _payroll_plus_side_income(ledger_db)
    before = typical_monthly_income(conn=ledger_db)["amount"]

    set_income_source_preference(
        SIDE_SOURCE, "confirmed", conn=ledger_db,
    )
    ledger_db.commit()

    assert typical_monthly_income(
        conn=ledger_db
    )["amount"] == pytest.approx(before)


def test_a_finished_months_money_in_is_still_what_arrived(ledger_db):
    """Excluding a source must not rewrite what a past month actually earned.

    Last Plan Result compares a saved intention against the money that
    actually landed. That figure comes from the cash-flow aggregate, not from
    the planning baseline, so it is deliberately untouched here — otherwise
    an exclusion made today would quietly restate finished months.
    """
    from utils.insights import monthly_aggregates

    _payroll_plus_side_income(ledger_db)
    before = {
        row["month"]: row["income"] for row in monthly_aggregates(conn=ledger_db)
    }

    _exclude(ledger_db, SIDE_SOURCE)
    after = {
        row["month"]: row["income"] for row in monthly_aggregates(conn=ledger_db)
    }

    assert after == before


def test_excluding_every_source_reports_no_income_rather_than_guessing(
    ledger_db,
):
    """The honest end of the control, not a crash and not a stale number."""
    from utils.analytics import typical_monthly_income
    from utils.checkin import flow_safe_to_spend

    _payroll_plus_side_income(ledger_db)
    for source in (SIDE_SOURCE, *PAYROLL_SOURCES):
        _exclude(ledger_db, source)

    baseline = typical_monthly_income(conn=ledger_db)

    assert baseline["amount"] == pytest.approx(0.0)
    assert flow_safe_to_spend(ledger_db, today=TODAY) is None

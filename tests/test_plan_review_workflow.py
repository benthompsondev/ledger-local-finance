"""The commitment review has to be reachable, and saving has to clear it.

"Review changes" pointed at ``screen: "plan"`` while Plan was already the
open screen, so clicking it did nothing: no scroll, no focus, no state
change. Nothing covered it, which is how it shipped.

These tests hold the engine side of that loop: the action targets an anchor,
drift is reported before acceptance, accepting and saving persists, and the
readiness item clears on reload. The scroll and focus themselves are DOM
behaviour and were exercised by hand in the built app.
"""
import pytest

from tests.conftest import add_tx, seed_month
from utils import database as db
from utils.planner import bills_and_commitments


@pytest.fixture
def drifted_db(ledger_db, monkeypatch):
    """Three complete months of ordinary bills, plus a partial July.

    Uses the repository's own month seeder, whose rent, hydro and
    subscription are the shape the commitment detector recognizes, so the
    drift under test is the realistic one: a saved plan that understates
    what is actually committed.
    """
    # This is a July scenario. Freeze only the month a new plan targets so the
    # test does not silently become an August scenario at midnight.
    monkeypatch.setattr(
        "utils.planner.plan_target_month", lambda today=None: "2026-07",
    )
    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(ledger_db, day="2026-07-03", desc="EMPLOYER PAYROLL",
           amount=2500.0, direction="credit", category="Payroll Income")
    for day in (2, 3, 4, 5):
        add_tx(ledger_db, day=f"2026-07-{day:02d}", desc=f"GROCER J{day}",
               amount=30.0, direction="debit", category="Groceries")
    ledger_db.commit()
    return ledger_db


def _detected(conn) -> float:
    return round(float(
        bills_and_commitments(conn=conn).get(
            "commitment_monthly_estimate") or 0), 2)


def _save(conn, fixed: float, reason: str = ""):
    """What the Save button does, at the storage layer."""
    db.upsert_monthly_plan({
        "month": "2026-07", "mode": "normal", "income_target": 2900.0,
        "spending_target": 2400.0,
        "fixed_obligations": fixed, "flexible_allowance": 500.0,
        "savings_target": 300.0, "safety_buffer": 100.0, "notes": "",
        "detected_commitments_at_save": fixed,
        "fixed_override_reason": reason,
    }, conn=conn)
    conn.commit()


def _stored(conn) -> dict:
    row = conn.execute(
        "SELECT fixed_obligations, fixed_override_reason FROM monthly_plans "
        "WHERE month = ?", ("2026-07",),
    ).fetchone()
    return {"fixed_obligations": float(row[0] or 0),
            "fixed_override_reason": str(row[1] or "")}


def test_the_review_action_targets_an_anchor_not_the_open_screen(drifted_db):
    """The whole defect: it sent you to the screen you were already on."""
    from desktop.engine.ledger_engine import _plan_payload

    _save(drifted_db, fixed=200.0)
    payload = _plan_payload(drifted_db)
    drift = next(item for item in payload["readiness"]["missing"]
                 if item["id"] == "plan_drift")

    assert drift["screen"] == "plan#commitment-review", (
        "navigating to a bare 'plan' is what made this button do nothing")
    assert drift["action"] == "Review changes"


def test_drift_is_reported_with_both_figures(drifted_db):
    from desktop.engine.ledger_engine import _plan_payload

    _save(drifted_db, fixed=200.0)
    agreement = _plan_payload(drifted_db)["readiness"]["plan_agreement"]

    assert agreement["needs_review"] is True
    assert agreement["saved"] == pytest.approx(200.0, abs=0.01)
    assert agreement["detected"] == pytest.approx(_detected(drifted_db), abs=0.01)
    assert agreement["detected"] > agreement["saved"]


def test_accepting_the_reviewed_amount_and_saving_clears_the_warning(drifted_db):
    from desktop.engine.ledger_engine import _plan_payload

    _save(drifted_db, fixed=200.0)
    before = _plan_payload(drifted_db)
    assert any(i["id"] == "plan_drift" for i in before["readiness"]["missing"])

    # What "Use reviewed commitments" then Save does.
    _save(drifted_db, fixed=before["fixed_obligations_suggestion"])

    after = _plan_payload(drifted_db)
    assert after["readiness"]["plan_agreement"]["needs_review"] is False
    assert not any(i["id"] == "plan_drift" for i in after["readiness"]["missing"])
    assert _stored(drifted_db)["fixed_obligations"] == pytest.approx(
        before["fixed_obligations_suggestion"], abs=0.01)


def test_a_deliberate_override_is_preserved_with_its_reason(drifted_db):
    """Someone who means to differ should not be nagged into agreeing."""
    from desktop.engine.ledger_engine import _plan_payload

    _save(drifted_db, fixed=200.0, reason="rent changes next month")
    stored = _stored(drifted_db)

    assert stored["fixed_override_reason"] == "rent changes next month"
    assert stored["fixed_obligations"] == pytest.approx(200.0, abs=0.01)
    # The figures are still reported, so the choice stays visible.
    agreement = _plan_payload(drifted_db)["readiness"]["plan_agreement"]
    assert agreement["saved"] == pytest.approx(200.0, abs=0.01)


def test_reading_the_plan_never_changes_it(drifted_db):
    """Navigating to the review must not accept anything on its own."""
    from desktop.engine.ledger_engine import _plan_payload

    _save(drifted_db, fixed=200.0)
    for _ in range(3):
        _plan_payload(drifted_db)

    assert _stored(drifted_db)["fixed_obligations"] == pytest.approx(
        200.0, abs=0.01)


def test_an_estimate_is_never_called_reliable_income(drifted_db):
    """Until the sources are confirmed this figure is a historical average,
    and "reliable" is a promise the data has not made."""
    from desktop.engine.ledger_engine import _plan_payload

    payload = _plan_payload(drifted_db)
    wording = " ".join([
        payload["equation"]["explanation"],
        *(f"{item['label']} {item['detail']}"
          for item in payload["readiness"]["missing"]),
    ]).lower()

    assert "reliable income" not in wording
    assert "typical monthly income" in payload["equation"]["explanation"].lower()


def test_the_plan_payload_names_the_date_its_figures_describe(drifted_db):
    from desktop.engine.ledger_engine import _plan_payload

    analysis = _plan_payload(drifted_db)["analysis"]
    assert analysis["data_through"] == "2026-07-05"
    assert "2026-07-05" in analysis["label"]

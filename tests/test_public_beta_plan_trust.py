"""Public-beta trust regressions for account activity and legacy plans.

All rows are invented. These cases mirror ordinary multi-account behaviour:
one account can be active while a savings account is quiet, and an older saved
plan can outlive the income baseline that originally created it.
"""

import pytest

from tests.conftest import add_tx, save_plan, seed_month
from utils.checkin import data_freshness, weekly_checkin
from utils.planner import forecast_month, generate_starter_plan


def test_quiet_account_does_not_replace_a_useful_checkin(steady_db):
    """A card with no July purchase is quiet, not proven out of date."""
    freshness = data_freshness(conn=steady_db, today="2026-07-06")

    assert freshness["cutoff_gap_days"] >= 7
    assert freshness["account_activity_note"]
    assert "last-transaction dates" in freshness["account_activity_note"]
    assert freshness["coverage_warning"] == ""

    packet = weekly_checkin(conn=steady_db, today="2026-07-06")
    assert packet["verdict"]["state"] != "insufficient"
    assert packet["verdict"]["headline"] != "Forecast confidence is limited"
    assert packet["safe_to_spend"]["confidence"] != "low"


def test_quiet_account_does_not_lower_forecast_or_proposal_confidence(steady_db):
    forecast = forecast_month(
        plan_month="2026-07", conn=steady_db, today="2026-07-06"
    )
    proposal = generate_starter_plan(plan_month="2026-07", conn=steady_db)

    assert forecast["risk_level"] != "low_confidence"
    assert forecast["confidence"] == "normal"
    assert forecast["coverage_warning"] == ""
    assert proposal["basis"]["coverage_mismatch"] is False


@pytest.fixture
def legacy_plan_db(ledger_db, monkeypatch):
    monkeypatch.setattr(
        "utils.planner.plan_target_month", lambda today=None: "2026-07",
    )
    for month in (
        "2026-01", "2026-02", "2026-03",
        "2026-04", "2026-05", "2026-06",
    ):
        seed_month(ledger_db, month)

    # Current invented activity across three accounts. Savings is deliberately
    # quiet after the middle of the month.
    add_tx(
        ledger_db, day="2026-07-15", desc="INVENTED SAVINGS INTEREST",
        amount=2.0, direction="credit", category="Interest Income",
        account_type="savings",
    )
    add_tx(
        ledger_db, day="2026-07-28", desc="INVENTED CARD PURCHASE",
        amount=40.0, direction="debit", category="Shopping",
        account_type="mastercard",
    )
    add_tx(
        ledger_db, day="2026-07-30", desc="INVENTED EMPLOYER PAYROLL",
        amount=2500.0, direction="credit", category="Payroll Income",
    )
    add_tx(
        ledger_db, day="2026-07-30", desc="INVENTED MARKET",
        amount=75.0, direction="debit", category="Groceries",
    )
    add_tx(
        ledger_db, day="2026-07-30", desc="INVENTED COFFEE",
        amount=5.0, direction="debit", category="Food & Convenience",
    )
    ledger_db.commit()

    # Simulates a plan saved before the six-complete-month baseline was made
    # canonical across Home and Plan.
    save_plan(
        ledger_db, "2026-07", income=3800.0, spending=2600.0, savings=500.0
    )
    return ledger_db


def test_legacy_saved_income_cannot_make_plan_disagree_with_home(legacy_plan_db):
    """A stale saved income target must not outrank the canonical baseline.

    The planning surfaces below all read the baseline, which is the point of
    this test and is unchanged.

    The forecast's expected income is a different question and its assertion
    changed in 2.5.5. It asserted ``expected_income_forecast == baseline``,
    which was the defect: the whole historical baseline was counted as still
    coming however late in the month it was. This invented month is the
    reason why. It is the 30th, both usual paydays are long past, and only
    $2,502 of a usual $5,000 has been imported. Reporting $5,000 of expected
    income on the second-to-last day claims $2,498 that has no date and no
    evidence behind it.
    """
    from desktop.engine.ledger_engine import _plan_payload
    from utils.analytics import typical_monthly_income

    baseline = typical_monthly_income(conn=legacy_plan_db)["amount"]
    payload = _plan_payload(legacy_plan_db, today="2026-07-30")
    forecast = payload["forecast"]

    assert baseline == pytest.approx(5000.0)
    assert payload["saved"]["income_target"] == pytest.approx(3800.0)
    assert payload["working_plan"]["income_target"] == pytest.approx(baseline)
    assert payload["equation"]["income"] == pytest.approx(baseline)
    assert payload["safe_to_spend"]["reserved"]["reliable_income"] == pytest.approx(
        baseline
    )

    # The forecast reads neither the stale saved target nor a baseline the
    # month did not deliver. Expected income equals what actually arrived,
    # because nothing is still scheduled to.
    assert forecast["expected_income_forecast"] == pytest.approx(2502.0)
    assert forecast["expected_income_forecast"] != pytest.approx(3800.0)
    assert forecast["expected_income_remaining"] == pytest.approx(0.0)
    assert forecast["mtd_income"] == pytest.approx(2502.0)
    assert forecast["missed_income_total"] > 0
    assert forecast["risk_level"] != "on_track"


def test_balance_checks_are_optional_and_activity_gap_is_not_a_task(legacy_plan_db):
    from desktop.engine.ledger_engine import _plan_payload

    payload = _plan_payload(legacy_plan_db, today="2026-07-30")
    required = {item["id"] for item in payload["readiness"]["missing"]}
    optional = {item["id"] for item in payload["readiness"]["advisories"]}

    assert "coverage" not in required
    assert not any(item.startswith("balance:") for item in required)
    assert any(item.startswith("balance:") for item in optional)
    assert payload["readiness"]["cash_checked"] is False

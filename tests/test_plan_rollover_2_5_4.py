"""Plan targets the month you are in, not the last one you imported.

All figures are invented.

When the newest statement was June and the calendar said July, Plan led with
"This planning period has ended". That is true about June and useless about
July: the screen is called Plan, the month it should be planning had just
started, and the headline described a month the user could no longer change.

The closed month is worth showing. It is context, not the headline.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import add_tx
from utils.analysis_period import reset_supported_bounds_cache
from utils.checkin import weekly_checkin


# The check-in changes verdict once the gap since the newest transaction
# passes FRESH_STALE_DAYS (21). This fixture's newest row is dated the 27th
# of last month, so that gap is 21 days when the suite runs on the 17th and
# 22 days when it runs on the 18th. The tests below passed locally on the
# 17th and failed in CI forty minutes later, because CI runs in UTC and the
# date had rolled over. Nothing about the product was wrong; the test was
# measuring the calendar. Pin the day the check-in is asked about so these
# tests keep measuring the month rollover they are named for.
REFERENCE_DAY = 10


def _reference() -> date:
    """Today, as these tests ask about it: a fixed day of the real month."""
    return date.today().replace(day=REFERENCE_DAY)


def _previous_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


@pytest.fixture
def imports_end_last_month(ledger_db):
    """Four complete months, the newest of which ended before this one."""
    today = date.today()
    month = _previous_month(today)
    months = []
    for _ in range(4):
        months.append(month)
        month = (month - timedelta(days=1)).replace(day=1)
    for month in reversed(months):
        ym = month.strftime("%Y-%m")
        last_day = ((month.replace(day=28) + timedelta(days=4)).replace(day=1)
                    - timedelta(days=1)).day
        add_tx(ledger_db, day=f"{ym}-01", desc="LANDLORD RENT", amount=1800.0,
               direction="debit", category="Housing / Mortgage")
        add_tx(ledger_db, day=f"{ym}-02", desc="EMPLOYER PAYROLL",
               amount=2500.0, direction="credit", category="Payroll Income")
        add_tx(ledger_db, day=f"{ym}-16", desc="EMPLOYER PAYROLL 2",
               amount=2500.0, direction="credit", category="Payroll Income")
        for day in (3, 8, 13, 18, 23, min(27, last_day)):
            add_tx(ledger_db, day=f"{ym}-{day:02d}", desc=f"GROCER {day}",
                   amount=84.20, direction="debit", category="Groceries")
    ledger_db.commit()
    reset_supported_bounds_cache()
    return ledger_db


def test_plan_targets_the_current_month_not_the_last_imported_one(
    imports_end_last_month,
) -> None:
    packet = weekly_checkin(conn=imports_end_last_month,
                            today=_reference().isoformat())
    verdict = packet["verdict"]

    assert verdict["state"] != "period_ended", (
        "the current month was reported as a period that had ended"
    )
    assert "has ended" not in verdict["headline"].lower(), verdict["headline"]


def test_the_headline_names_the_month_being_planned(
    imports_end_last_month,
) -> None:
    packet = weekly_checkin(conn=imports_end_last_month,
                            today=_reference().isoformat())
    headline = packet["verdict"]["headline"]
    this_month = _reference().strftime("%B")

    assert this_month in headline, (
        f"{headline!r} does not name {this_month}"
    )
    assert "ready" in headline.lower() or "plan" in headline.lower()


def test_the_closed_month_is_context_not_the_headline(
    imports_end_last_month,
) -> None:
    packet = weekly_checkin(conn=imports_end_last_month,
                            today=_reference().isoformat())
    verdict = packet["verdict"]
    closed = _previous_month(_reference()).strftime("%B")

    assert closed not in verdict["headline"], (
        "the finished month took over the headline"
    )
    # It still gets said, in the sentence underneath.
    assert closed in verdict["sentence"] or "kept" in verdict["sentence"]


def test_the_sentence_says_no_current_data_has_been_imported(
    imports_end_last_month,
) -> None:
    sentence = weekly_checkin(
        conn=imports_end_last_month,
        today=_reference().isoformat())["verdict"]["sentence"]
    this_month = _reference().strftime("%B")

    assert this_month in sentence
    assert "import" in sentence.lower() or "no " in sentence.lower()


def test_planning_the_current_month_is_still_possible(
    imports_end_last_month,
) -> None:
    """Safe to Spend may be unavailable. The plan itself must not be blocked."""
    from utils.planner import generate_starter_plan, plan_target_month

    target = plan_target_month()
    assert target == date.today().strftime("%Y-%m")

    proposal = generate_starter_plan(
        plan_month=target, conn=imports_end_last_month)
    assert proposal, "no plan proposal for the current month"
    assert float(proposal.get("income") or proposal.get("income_target") or 0) > 0


def test_a_genuinely_finished_period_still_says_so(ledger_db) -> None:
    """The old state was not wrong, only misapplied. It must still exist.

    A month with data through its final day, viewed after it ended, has
    genuinely finished.
    """
    today = date.today()
    month = _previous_month(today)
    ym = month.strftime("%Y-%m")
    last_day = ((month.replace(day=28) + timedelta(days=4)).replace(day=1)
                - timedelta(days=1)).day
    add_tx(ledger_db, day=f"{ym}-01", desc="LANDLORD RENT", amount=1800.0,
           direction="debit", category="Housing / Mortgage")
    for day in (5, 12, 19, last_day):
        add_tx(ledger_db, day=f"{ym}-{day:02d}", desc=f"GROCER {day}",
               amount=84.20, direction="debit", category="Groceries")
    ledger_db.commit()
    reset_supported_bounds_cache()

    packet = weekly_checkin(conn=ledger_db, today=f"{ym}-{last_day:02d}")

    # Nothing newer is missing: the month we are in has run out of days.
    assert packet["verdict"]["state"] == "period_ended"
    assert "ready to review" not in packet["verdict"]["headline"].lower()

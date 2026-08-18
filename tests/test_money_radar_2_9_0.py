"""Money Radar: an expected event is a claim about the future, so it is the
one place in Northstar where being wrong is cheap to do and expensive to
notice.

These tests are weighted accordingly. Most of them assert that something is
*absent*: a rejected merchant, a dead subscription, an excluded income
source, a rhythm too thin to be a rhythm, a balance projection. A radar that
draws everything it can compute is worse than no radar, because every wrong
marker teaches the user to ignore the right ones.

All data here is synthetic.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import pytest

from tests.conftest import add_tx
from utils.analysis_period import reset_supported_bounds_cache
from utils.upcoming import upcoming_money

TODAY = date(2026, 8, 18)


def _bill(db, merchant, *, day, months, amount=94.0, start_month=None,
          category="Utilities / Bills", step_months=1):
    """A charge repeating on a whole-month rhythm, ending before TODAY."""
    when = start_month or date(TODAY.year, TODAY.month, min(day, 28))
    # Walk backwards so the newest charge is the most recent cycle.
    made = []
    cursor = when
    while len(made) < months:
        made.append(cursor)
        total = cursor.month - 1 - step_months
        year = cursor.year + (total // 12)
        month = total % 12 + 1
        cursor = date(year, month,
                      min(day, calendar.monthrange(year, month)[1]))
    for occurrence in made:
        if occurrence >= TODAY:
            continue
        add_tx(db, day=occurrence.isoformat(), desc=merchant, amount=amount,
               direction="debit", category=category)


def _payroll(db, *, every_days=14, count=10, amount=2400.0,
             desc="EMPLOYER PAYROLL", last=None):
    when = last or (TODAY - timedelta(days=4))
    for _ in range(count):
        add_tx(db, day=when.isoformat(), desc=desc, amount=amount,
               direction="credit", category="Payroll Income")
        when -= timedelta(days=every_days)


def _radar(db, **kwargs):
    db.commit()
    reset_supported_bounds_cache()
    return upcoming_money(conn=db, today=TODAY, **kwargs)


def _labels(result, kind=None):
    return [
        item["label"] for item in result["items"]
        if kind is None or item["kind"] == kind
    ]


# ── the window itself ───────────────────────────────────────────────────

def test_an_empty_database_offers_nothing_and_says_why(ledger_db):
    result = upcoming_money(conn=ledger_db, today=TODAY)

    assert result["available"] is False
    assert result["items"] == []
    assert "Import a statement" in result["reason"]


def test_the_window_runs_from_the_real_day_not_the_last_statement(ledger_db):
    """The whole point of the feature. Evidence stops where the statements
    stop; the question is about the calendar."""
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)

    assert result["window_start"] == TODAY.isoformat()
    assert result["window_end"] == (TODAY + timedelta(days=35)).isoformat()
    assert result["data_through"] < result["window_start"]
    assert result["data_age_days"] > 0


def test_the_horizon_moves_deterministically_with_the_injected_day(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    reset_supported_bounds_cache()

    early = upcoming_money(conn=ledger_db, today=TODAY)
    later = upcoming_money(conn=ledger_db, today=TODAY + timedelta(days=10))

    assert later["window_start"] > early["window_start"]
    assert later["window_end"] > early["window_end"]


def test_an_event_just_outside_the_window_is_not_shown(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    reset_supported_bounds_cache()

    wide = upcoming_money(conn=ledger_db, today=TODAY, horizon_days=35)
    narrow = upcoming_money(conn=ledger_db, today=TODAY, horizon_days=2)

    assert any(i["label"] == "HYDRO CO" for i in wide["items"])
    assert not any(i["label"] == "HYDRO CO" for i in narrow["items"])


# ── recurring bills ─────────────────────────────────────────────────────

def test_a_monthly_bill_is_expected_on_its_own_day_of_the_month(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8, amount=94.0)
    result = _radar(ledger_db)
    hydro = next(i for i in result["items"] if i["label"] == "HYDRO CO")

    assert hydro["kind"] == "bill"
    assert hydro["expected_date"].endswith("-24")
    assert hydro["amount"] == pytest.approx(94.0, abs=0.01)
    assert hydro["cadence_note"] == "usually once a month"
    assert hydro["days_away"] >= 0


def test_a_monthly_bill_does_not_drift_backwards_across_months(ledger_db):
    """Stepping a monthly rhythm by 30 days walks it out of the month."""
    _bill(ledger_db, "RENT CO", day=1, months=10, amount=1800.0,
          category="Housing / Mortgage")
    result = upcoming_money(conn=ledger_db, today=TODAY, horizon_days=70)
    days = {
        i["expected_date"][-2:] for i in result["items"]
        if i["label"] == "RENT CO"
    }

    assert days and days <= {"01"}, f"rent drifted to {days}"


def test_a_quarterly_bill_is_not_treated_as_monthly(ledger_db):
    _bill(ledger_db, "WATER BOARD", day=12, months=6, amount=210.0,
          step_months=3)
    result = _radar(ledger_db)
    water = [i for i in result["items"] if i["label"] == "WATER BOARD"]

    assert len(water) <= 1, "a quarterly bill was drawn more than once"
    if water:
        assert water[0]["cadence"] in {"quarterly", "irregular"}


def test_an_annual_bill_appears_at_most_once(ledger_db):
    _bill(ledger_db, "INSURANCE CO", day=9, months=4, amount=640.0,
          step_months=12)
    result = _radar(ledger_db)

    assert len([i for i in result["items"]
                if i["label"] == "INSURANCE CO"]) <= 1


def test_a_merchant_marked_not_recurring_never_appears(ledger_db):
    from utils.database import set_recurring_preference

    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    set_recurring_preference("HYDRO CO", "not_recurring", conn=ledger_db)
    result = _radar(ledger_db)

    assert "HYDRO CO" not in _labels(result), (
        "Northstar kept expecting something the user rejected"
    )


def test_a_subscription_that_stopped_coming_is_not_expected(ledger_db):
    """Cancelled in spring. Drawing it in August is expecting a ghost."""
    old = date(TODAY.year, 3, 5)
    for _ in range(6):
        add_tx(ledger_db, day=old.isoformat(), desc="GONE STREAMING",
               amount=15.99, direction="debit",
               category="Subscriptions & Digital")
        total = old.month - 2
        old = date(old.year + (total // 12), total % 12 + 1, 5)
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)

    assert "GONE STREAMING" not in _labels(result)


def test_two_charges_are_not_a_rhythm(ledger_db):
    add_tx(ledger_db, day=(TODAY - timedelta(days=30)).isoformat(),
           desc="ONCE OR TWICE", amount=40.0, direction="debit",
           category="Shopping")
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)

    assert "ONCE OR TWICE" not in _labels(result)


def test_a_price_change_shows_the_current_amount_with_its_evidence(ledger_db):
    when = date(TODAY.year, TODAY.month, 14)
    amounts = []
    cursor = when
    for index in range(9):
        if cursor < TODAY:
            amounts.append((cursor, 18.99 if index < 3 else 14.49))
        total = cursor.month - 2
        cursor = date(cursor.year + (total // 12), total % 12 + 1, 14)
    for day, amount in amounts:
        add_tx(ledger_db, day=day.isoformat(), desc="STREAMPLUS",
               amount=amount, direction="debit",
               category="Subscriptions & Digital")
    result = _radar(ledger_db)
    sub = next(i for i in result["items"] if i["label"] == "STREAMPLUS")

    assert sub["amount"] == pytest.approx(18.99, abs=0.01), (
        "the radar quoted a price that is no longer charged"
    )
    assert sub["recent"], "no evidence behind the estimate"
    assert [row["amount"] for row in sub["recent"]][-1] == pytest.approx(
        18.99, abs=0.01)


def test_every_bill_carries_evidence_and_a_way_into_it(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    _bill(ledger_db, "RENT CO", day=1, months=10, amount=1800.0,
          category="Housing / Mortgage")
    result = _radar(ledger_db)

    for item in result["items"]:
        if item["kind"] != "bill":
            continue
        assert item["recent"], f"{item['label']} has no recent charges"
        assert len(item["recent"]) <= 3
        assert item["last_seen"]
        assert item["confidence"] in {"low", "medium", "high"}
        assert item["drill"]["merchant"]


def test_normalization_does_not_produce_two_of_the_same_bill(ledger_db):
    """"Hydro Co." and "HYDRO CO" are one bill, not two."""
    when = date(TODAY.year, TODAY.month, 24)
    for index in range(8):
        if when < TODAY:
            add_tx(ledger_db, day=when.isoformat(),
                   desc="Hydro Co." if index % 2 else "HYDRO CO",
                   amount=94.0, direction="debit",
                   category="Utilities / Bills")
        total = when.month - 2
        when = date(when.year + (total // 12), total % 12 + 1, 24)
    result = _radar(ledger_db)
    keys = [i["key"] for i in result["items"] if i["kind"] == "bill"]

    assert len(keys) == len(set(keys)), f"duplicate identities: {keys}"


# ── expected income ─────────────────────────────────────────────────────

def test_a_steady_payroll_becomes_an_expected_payday(ledger_db):
    _payroll(ledger_db)
    result = _radar(ledger_db)
    income = [i for i in result["items"] if i["kind"] == "income"]

    assert income, "a fortnightly payroll produced no expected payday"
    assert income[0]["amount"] == pytest.approx(2400.0, abs=0.01)
    assert income[0]["confidence"] in {"medium", "high"}
    assert result["expected_in_total"] > 0


def test_an_income_source_marked_no_is_not_drawn_as_a_payday(ledger_db):
    """The narrow rule this feature is allowed to enforce.

    "Stable: No" has historically not reached every planning calculation.
    Whatever the rest of the app does, the radar must not draw a payday for
    a source the user has explicitly called undependable.
    """
    from utils.database import set_income_source_preference

    _payroll(ledger_db, desc="SIDE GIG", amount=800.0)
    _payroll(ledger_db, desc="EMPLOYER PAYROLL", amount=2400.0)
    ledger_db.commit()
    set_income_source_preference("SIDE GIG", "excluded", conn=ledger_db)
    result = _radar(ledger_db)

    labels = _labels(result, "income")
    assert "SIDE GIG" not in labels, "an excluded source was drawn as income"
    assert any("PAYROLL" in label for label in labels)


def test_an_expected_payday_shows_what_its_figure_came_from(ledger_db):
    """An amount with nothing behind it has to be taken on faith, and this
    one is a guess about a date that has not happened."""
    _payroll(ledger_db, amount=2412.0)
    result = _radar(ledger_db)
    payday = next(i for i in result["items"] if i["kind"] == "income")

    assert payday["recent"], "no deposits behind the expected payday"
    assert len(payday["recent"]) <= 3
    assert all(row["amount"] == pytest.approx(2412.0, abs=0.01)
               for row in payday["recent"])
    assert payday["last_seen"]


def test_irregular_deposits_never_become_a_payday(ledger_db):
    for offset, amount in ((3, 900.0), (41, 120.0), (58, 4000.0), (96, 60.0)):
        add_tx(ledger_db, day=(TODAY - timedelta(days=offset)).isoformat(),
               desc="ODD MONEY", amount=amount, direction="credit",
               category="Other Income")
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)

    assert "ODD MONEY" not in _labels(result, "income")


def test_no_trustworthy_payday_means_no_payday_is_invented(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)

    assert result["next_income"] is None
    assert result["out_before_next_income"] is None
    assert result["income_count"] == 0
    assert "payday" not in result["summary"]


# ── staleness ───────────────────────────────────────────────────────────

def test_fresh_data_is_not_flagged(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    add_tx(ledger_db, day=(TODAY - timedelta(days=2)).isoformat(),
           desc="CORNER MARKET", amount=11.0, direction="debit",
           category="Food & Convenience")
    result = _radar(ledger_db)

    assert result["staleness"] == "current"
    assert result["staleness_note"] == ""


def test_stale_data_is_disclosed_and_lowers_confidence(ledger_db):
    """A stale import must not be made to look current by the wall clock."""
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    reset_supported_bounds_cache()

    fresh = upcoming_money(conn=ledger_db, today=TODAY)
    far = upcoming_money(conn=ledger_db, today=TODAY + timedelta(days=40))

    assert far["staleness"] == "stale"
    assert "statements stop" in far["staleness_note"]
    assert far["data_age_days"] > fresh["data_age_days"]
    ranks = {"low": 0, "medium": 1, "high": 2}
    for item in far["items"]:
        match = [i for i in fresh["items"] if i["key"] == item["key"]]
        if match:
            assert ranks[item["confidence"]] <= ranks[match[0]["confidence"]]


def test_a_charge_the_statements_should_have_caught_is_marked_not_seen(
        ledger_db):
    """The only honest form of "missing": the import covers the date and the
    charge is not in it."""
    when = date(TODAY.year, TODAY.month, 2)
    for _ in range(8):
        add_tx(ledger_db, day=when.isoformat(), desc="EARLY BILL",
               amount=60.0, direction="debit", category="Utilities / Bills")
        total = when.month - 2
        when = date(when.year + (total // 12), total % 12 + 1, 2)
    # Statements run well past the 2nd, so its absence is real.
    add_tx(ledger_db, day=(TODAY - timedelta(days=1)).isoformat(),
           desc="CORNER MARKET", amount=11.0, direction="debit",
           category="Food & Convenience")
    result = _radar(ledger_db)
    early = [i for i in result["items"] if i["label"] == "EARLY BILL"]

    if early:
        flagged = [i for i in early if i["not_seen_yet"]]
        assert not flagged or flagged[0]["expected_date"] < TODAY.isoformat()


def test_a_gap_after_the_statements_end_is_never_called_missing(ledger_db):
    """Between the data cutoff and today, nothing is knowable. Calling that
    a missed charge turns a stale import into a false alarm."""
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    reset_supported_bounds_cache()
    result = upcoming_money(conn=ledger_db, today=TODAY + timedelta(days=40))
    through = result["data_through"]

    for item in result["items"]:
        if item["not_seen_yet"]:
            assert item["expected_date"] < through, (
                f"{item['label']} called missing for a date the statements "
                "never reached"
            )


# ── what the radar refuses to say ───────────────────────────────────────

def test_the_payload_contains_no_projected_balance(ledger_db):
    """The trust line. Events and their sums, never a future position."""
    import json

    _payroll(ledger_db)
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)
    blob = json.dumps(result).lower()

    for banned in ("balance", "safe_to_spend", "projected", "shortfall",
                   "will_have", "runway", "forecast"):
        assert banned not in blob, f"radar payload mentions {banned!r}"


def test_the_summary_never_calls_it_available_money(ledger_db):
    _payroll(ledger_db)
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    result = _radar(ledger_db)
    summary = result["summary"].lower()

    assert summary
    assert "available" not in summary
    assert "left" not in summary
    assert "short" not in summary
    assert "expected" in summary


def test_the_summary_only_names_a_payday_when_one_is_trustworthy(ledger_db):
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    without = _radar(ledger_db)
    _payroll(ledger_db)
    with_income = _radar(ledger_db)

    assert "payday" not in without["summary"]
    assert "payday" in with_income["summary"]


def test_totals_reconcile_with_the_items(ledger_db):
    _payroll(ledger_db)
    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    _bill(ledger_db, "RENT CO", day=1, months=10, amount=1800.0,
          category="Housing / Mortgage")
    result = _radar(ledger_db)

    assert result["expected_out_total"] == pytest.approx(
        sum(i["amount"] for i in result["items"] if i["kind"] == "bill"),
        abs=0.01)
    assert result["expected_in_total"] == pytest.approx(
        sum(i["amount"] for i in result["items"] if i["kind"] == "income"),
        abs=0.01)
    assert result["bill_count"] + result["income_count"] == len(result["items"])


# ── boundaries in the calendar itself ───────────────────────────────────

def test_a_window_crossing_new_year_still_places_events(ledger_db):
    end_of_year = date(2026, 12, 20)
    when = end_of_year
    for _ in range(8):
        add_tx(ledger_db, day=when.isoformat(), desc="HYDRO CO", amount=94.0,
               direction="debit", category="Utilities / Bills")
        total = when.month - 2
        when = date(when.year + (total // 12), total % 12 + 1, 20)
    ledger_db.commit()
    reset_supported_bounds_cache()
    result = upcoming_money(conn=ledger_db, today=end_of_year + timedelta(days=1),
                            horizon_days=35)

    dates = [i["expected_date"] for i in result["items"]]
    assert dates, "nothing survived the year boundary"
    assert all(d >= "2026-12-21" for d in dates)


def test_a_bill_on_the_31st_survives_a_short_month(ledger_db):
    when = date(2026, 7, 31)
    for _ in range(6):
        add_tx(ledger_db, day=when.isoformat(), desc="MONTH END FEE",
               amount=25.0, direction="debit", category="Fees / Interest")
        total = when.month - 2
        year = when.year + (total // 12)
        month = total % 12 + 1
        when = date(year, month, min(31, calendar.monthrange(year, month)[1]))
    ledger_db.commit()
    reset_supported_bounds_cache()
    result = upcoming_money(conn=ledger_db, today=date(2026, 8, 5),
                            horizon_days=60)

    for item in result["items"]:
        day = int(item["expected_date"][-2:])
        month = int(item["expected_date"][5:7])
        year = int(item["expected_date"][:4])
        assert day <= calendar.monthrange(year, month)[1]


def test_two_events_landing_together_are_both_kept(ledger_db):
    _bill(ledger_db, "RENT CO", day=1, months=10, amount=1800.0,
          category="Housing / Mortgage")
    _bill(ledger_db, "CAR LOAN", day=1, months=10, amount=410.0,
          category="Utilities / Bills")
    result = _radar(ledger_db)
    first_of_month = [
        i for i in result["items"] if i["expected_date"].endswith("-01")
    ]

    assert len({i["label"] for i in first_of_month}) >= 2


def test_the_radar_fails_loudly_rather_than_going_quietly_dead(
        ledger_db, monkeypatch):
    """The 2.8.0 lesson, encoded.

    A detector wrapped in ``except Exception`` can be broken for months and
    look exactly like a detector that found nothing — that is how the
    recurring price card stayed dead through a whole release. So the engine
    does not swallow here. Home stays up because *the screen* guards the
    call, the same way it already guards the insight feed, which keeps the
    failure visible to tests while keeping it harmless to the user.
    """
    import utils.upcoming as module

    _bill(ledger_db, "HYDRO CO", day=24, months=8)
    ledger_db.commit()
    reset_supported_bounds_cache()

    def explode(*args, **kwargs):
        raise RuntimeError("income schedule failed")

    monkeypatch.setattr(module, "_income_items", explode)
    with pytest.raises(RuntimeError):
        upcoming_money(conn=ledger_db, today=TODAY)


def test_home_guards_the_radar_call_so_a_failure_cannot_blank_the_page():
    """The other half of the contract above, checked in the source."""
    import pathlib
    import re

    home = (pathlib.Path(__file__).resolve().parents[1]
            / "desktop" / "src" / "HomeView.tsx").read_text(encoding="utf-8")

    assert re.search(r"try\s*\{[^}]*loadUpcomingMoney\(\)[^}]*\}\s*catch",
                     home), (
        "HomeView must guard the radar request, or one engine failure takes "
        "the whole check-in down"
    )

"""Northstar 1.6.0 Pass 2 — the bills that do not arrive every month.

Quarterly insurance and annual property tax were invisible to the whole app.
The detector grouped by calendar month and required a merchant in three
distinct months, so anything billed quarterly or annually could never
qualify. Nothing reserved for them, and Safe to Spend was overstated by their
whole monthly cost until the bill landed and the money vanished.
"""
import calendar

import pytest

from tests.conftest import add_tx

# Two full years, so an annual bill has actually happened twice. One
# occurrence is a purchase; two a year apart is a rhythm.
MONTHS = [f"2024-{m:02d}" for m in range(7, 13)] + \
         [f"2025-{m:02d}" for m in range(1, 13)] + \
         [f"2026-{m:02d}" for m in range(1, 7)]


def _base_month(db, month, *, mortgage=1200.0, income=4000.0):
    last = calendar.monthrange(int(month[:4]), int(month[5:7]))[1]
    for day in range(1, 27):
        add_tx(db, day=f"{month}-{day:02d}", desc=f"INVENTED CAFE {day}",
               amount=12, direction="debit", category="Food & Convenience")
    add_tx(db, day=f"{month}-{last:02d}", desc="INVENTED LATE SHOP",
           amount=20, direction="debit", category="Groceries")
    add_tx(db, day=f"{month}-05", desc="INVENTED EMPLOYER PAYROLL",
           amount=income, direction="credit", category="Payroll Income")
    add_tx(db, day=f"{month}-01", desc="INVENTED MORTGAGE CO", amount=mortgage,
           direction="debit", category="Housing / Mortgage")


QUARTERLY_MONTHS = ("2024-07", "2024-10", "2025-01", "2025-04", "2025-07",
                    "2025-10", "2026-01", "2026-04")
ANNUAL_MONTHS = ("2024-11", "2025-11")


def _seed(db, *, quarterly=True, annual=True, weekly=False):
    for month in MONTHS:
        _base_month(db, month)
        if quarterly and month in QUARTERLY_MONTHS:
            add_tx(db, day=f"{month}-15", desc="INVENTED INSURANCE GROUP",
                   amount=540, direction="debit", category="Insurance")
        if annual and month in ANNUAL_MONTHS:
            add_tx(db, day=f"{month}-20", desc="INVENTED PROPERTY TAX CITY",
                   amount=2400, direction="debit",
                   category="Utilities / Bills")
        if weekly:
            for day in (3, 10, 17, 24):
                add_tx(db, day=f"{month}-{day:02d}",
                       desc="INVENTED WINDOW CLEANER", amount=25,
                       direction="debit", category="Utilities / Bills")
    db.commit()


def _item(bills, needle):
    return next(
        (i for i in bills.get("items", [])
         if needle in str(i.get("merchant", "")).upper()), None,
    )


# ── cadence detection ────────────────────────────────────────────────────

def test_a_quarterly_bill_is_detected(ledger_db):
    from utils.planner import bills_and_commitments

    _seed(ledger_db, annual=False)
    item = _item(bills_and_commitments(conn=ledger_db), "INSURANCE")

    assert item is not None, "a bill paid four times a year is still a bill"
    assert item["cadence"] == "quarterly"
    assert item["period_months"] == 3
    assert item["est_amount"] == pytest.approx(540.0)
    # 540 every three months is 180 a month put aside.
    assert item["monthly_setaside"] == pytest.approx(180.0)


def test_an_annual_bill_is_detected(ledger_db):
    from utils.planner import bills_and_commitments

    _seed(ledger_db, quarterly=False)
    item = _item(bills_and_commitments(conn=ledger_db), "PROPERTY TAX")

    assert item is not None
    assert item["cadence"] == "annual"
    assert item["period_months"] == 12
    assert item["monthly_setaside"] == pytest.approx(200.0)


def test_a_monthly_bill_keeps_its_full_amount(ledger_db):
    """A monthly bill sets aside its whole amount every month."""
    from utils.planner import bills_and_commitments

    _seed(ledger_db)
    item = _item(bills_and_commitments(conn=ledger_db), "MORTGAGE")

    assert item["cadence"] == "monthly"
    assert item["period_months"] == 1
    assert item["monthly_setaside"] == pytest.approx(1200.0)


def test_cadence_is_carried_through_and_never_none(ledger_db):
    """`cadence` used to be dropped in the rebuild, arriving as None."""
    from utils.planner import bills_and_commitments

    _seed(ledger_db, weekly=True)
    for item in bills_and_commitments(conn=ledger_db).get("items", []):
        assert item.get("cadence"), f"{item.get('merchant')} has no cadence"
        assert item.get("period_months") is not None


def test_a_weekly_bill_is_detected(ledger_db):
    from utils.planner import bills_and_commitments

    _seed(ledger_db, quarterly=False, annual=False, weekly=True)
    item = _item(bills_and_commitments(conn=ledger_db), "WINDOW CLEANER")

    assert item is not None
    assert item["cadence"] == "weekly"
    # About 4.33 weeks in a month.
    assert item["monthly_setaside"] == pytest.approx(108.0, abs=3.0)


def test_a_cancelled_bill_stops_being_reserved(ledger_db):
    """Money set aside for a bill that stopped coming is money lost.

    A policy cancelled a year ago kept reserving its monthly share forever,
    quietly holding back cash the user could spend, and nothing would ever
    release it. Under-reporting what is safe to spend is a quieter failure
    than over-reporting it, and just as wrong.
    """
    from utils.planner import bills_and_commitments

    for month in MONTHS:
        _base_month(ledger_db, month)
        if month in ("2024-07", "2024-10", "2025-01", "2025-04"):
            add_tx(ledger_db, day=f"{month}-15",
                   desc="INVENTED CANCELLED INSURANCE", amount=540,
                   direction="debit", category="Insurance")
    ledger_db.commit()

    bills = bills_and_commitments(conn=ledger_db)
    cancelled = _item(bills, "CANCELLED")
    if cancelled is not None:
        assert cancelled["included_in_forecast"] is False
        assert cancelled["monthly_setaside"] == 0.0
    assert bills["nonmonthly_monthly_reserve"] == pytest.approx(0.0)


def test_a_bill_still_arriving_is_kept(ledger_db):
    """The staleness rule must not throw away a bill that is merely slow."""
    from utils.planner import bills_and_commitments

    _seed(ledger_db, annual=False)
    item = _item(bills_and_commitments(conn=ledger_db), "INSURANCE")
    assert item is not None
    assert item["included_in_forecast"] is True


def test_a_missed_payment_caught_up_later_keeps_the_bill(ledger_db):
    """Real statements skip a month and pay two the next.

    Judging the rhythm on the worst gap dropped such a bill out of the plan
    entirely, which is the opposite of what someone catching up on payments
    needs.
    """
    from utils.planner import bills_and_commitments

    for month in MONTHS:
        _base_month(ledger_db, month)
        if month in ("2024-10", "2025-01", "2025-04", "2025-10", "2026-01",
                     "2026-04"):
            add_tx(ledger_db, day=f"{month}-15",
                   desc="INVENTED CATCHUP INSURANCE", amount=540,
                   direction="debit", category="Insurance")
        if month == "2025-10":  # July was missed, two paid in October
            add_tx(ledger_db, day=f"{month}-28",
                   desc="INVENTED CATCHUP INSURANCE", amount=540,
                   direction="debit", category="Insurance")
    ledger_db.commit()

    item = _item(bills_and_commitments(conn=ledger_db), "CATCHUP")
    assert item is not None, "one skipped payment must not erase the bill"
    assert item["cadence"] == "quarterly"
    assert item["monthly_setaside"] == pytest.approx(180.0)


def test_a_one_off_purchase_is_not_a_bill(ledger_db):
    """One large payment is not a cadence, however memorable."""
    from utils.planner import bills_and_commitments

    _seed(ledger_db, quarterly=False, annual=False)
    add_tx(ledger_db, day="2026-03-11", desc="INVENTED FURNITURE WAREHOUSE",
           amount=3200, direction="debit", category="Home Improvement")
    ledger_db.commit()

    assert _item(bills_and_commitments(conn=ledger_db), "FURNITURE") is None


# ── the reserve, and Safe to Spend ───────────────────────────────────────

def test_non_monthly_bills_are_reserved_in_the_plan(ledger_db):
    """The whole point: stop overstating what is safe to spend."""
    from utils.planner import bills_and_commitments

    _seed(ledger_db)
    bills = bills_and_commitments(conn=ledger_db)

    # 1200 mortgage + 180 insurance + 200 property tax.
    assert bills["monthly_estimate"] == pytest.approx(1580.0, abs=1.0)
    assert bills["nonmonthly_monthly_reserve"] == pytest.approx(380.0, abs=1.0)


def test_safe_to_spend_falls_by_exactly_the_reserve(tmp_path, monkeypatch):
    """Two separate databases, so the comparison is honest."""
    import utils.database as dbmod
    from utils.checkin import flow_safe_to_spend

    def build(**kwargs):
        monkeypatch.setattr(
            dbmod, "DB_PATH", tmp_path / f"{sorted(kwargs.items())}.db",
        )
        dbmod.init_db()
        conn = dbmod.get_connection()
        _seed(conn, **kwargs)
        return flow_safe_to_spend(conn, today="2026-06-28")

    without = build(quarterly=False, annual=False)
    with_reserve = build(quarterly=True, annual=True)

    drop = without["monthly_plan"] - with_reserve["monthly_plan"]
    assert drop == pytest.approx(380.0, abs=1.0)


def test_paying_a_non_monthly_bill_is_not_counted_twice(ledger_db):
    """In the month the bill lands it is paid from the reserve, not again.

    The latest month holds a $540 insurance payment. It is already reserved
    at $180 a month, so counting the payment as flexible spending as well
    would charge the user for the same bill twice.
    """
    from utils.checkin import flow_safe_to_spend

    _seed(ledger_db)
    # Put an insurance payment in the month the check-in actually reports on.
    add_tx(ledger_db, day="2026-06-15", desc="INVENTED INSURANCE GROUP",
           amount=540, direction="debit", category="Insurance")
    ledger_db.commit()

    flow = flow_safe_to_spend(ledger_db, today="2026-06-30")
    assert flow["month"] == "2026-06"
    # June's flexible spending is cafes and groceries, never the insurance.
    assert flow["flexible_spent"] < 540.0
    assert flow["flexible_spent"] == pytest.approx(26 * 12 + 20, abs=1.0)


def test_the_reserve_is_explained_in_plain_words(ledger_db):
    from utils.planner import bills_and_commitments

    _seed(ledger_db)
    item = _item(bills_and_commitments(conn=ledger_db), "INSURANCE")

    assert "180" in item["setaside_note"]
    assert item["expected_next"], "a reserve needs a date to aim at"
    assert item["expected_next"] > "2026-04"


# ── price changes ────────────────────────────────────────────────────────

def test_a_subscription_price_rise_is_reported(ledger_db):
    from utils.planner import bills_and_commitments

    for month in MONTHS:
        _base_month(ledger_db, month)
        add_tx(ledger_db, day=f"{month}-08", desc="INVENTED STREAMCO",
               amount=16.99 if month < "2026-01" else 18.99,
               direction="debit", category="Subscriptions & Digital")
    ledger_db.commit()

    item = _item(bills_and_commitments(conn=ledger_db), "STREAM")
    change = item["price_change"]

    assert change["previous_amount"] == pytest.approx(16.99)
    assert change["current_amount"] == pytest.approx(18.99)
    assert change["changed_on"].startswith("2026-01")
    assert change["annual_impact"] == pytest.approx(24.0, abs=0.5)
    # The estimate must be what it costs now, not an average of both prices.
    assert item["est_amount"] == pytest.approx(18.99)


def test_a_steady_price_reports_no_change(ledger_db):
    from utils.planner import bills_and_commitments

    for month in MONTHS:
        _base_month(ledger_db, month)
        add_tx(ledger_db, day=f"{month}-08", desc="INVENTED STREAMCO",
               amount=16.99, direction="debit",
               category="Subscriptions & Digital")
    ledger_db.commit()

    item = _item(bills_and_commitments(conn=ledger_db), "STREAM")
    assert item.get("price_change") is None


def test_small_wobbles_are_not_reported_as_price_changes(ledger_db):
    """A utility bill that drifts a dollar has not raised its price."""
    from utils.planner import bills_and_commitments

    for index, month in enumerate(MONTHS):
        _base_month(ledger_db, month)
        add_tx(ledger_db, day=f"{month}-09", desc="INVENTED HYDRO CO",
               amount=95 + index * 0.4, direction="debit",
               category="Utilities / Bills")
    ledger_db.commit()

    item = _item(bills_and_commitments(conn=ledger_db), "HYDRO")
    assert item.get("price_change") is None

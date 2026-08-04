"""Northstar 1.6.0 — one money model for every screen.

Three defects, one root cause: the same concept was computed in more than one
place. Home read `typical_monthly_income` while Plan still read
`reliable_income_summary`, so the two screens disagreed by thousands on
identical data. Months with no income at all were dropped from the baseline
rather than counted as zero, inflating it for seasonal and contract earners.
And a month that had already ended was still offered as money available now.
"""
import pytest

from tests.conftest import add_tx


def _month(db, month, *, income=3000.0, days=26, spend=12.0, mortgage=1200.0):
    for day in range(1, days + 1):
        add_tx(db, day=f"{month}-{day:02d}", desc=f"INVENTED CAFE {day}",
               amount=spend, direction="debit", category="Food & Convenience")
    if income:
        add_tx(db, day=f"{month}-05", desc="INVENTED EMPLOYER PAYROLL",
               amount=income, direction="credit", category="Payroll Income")
    if mortgage:
        add_tx(db, day=f"{month}-01", desc="INVENTED MORTGAGE CO",
               amount=mortgage, direction="debit",
               category="Housing / Mortgage")


SIX = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")


# ── the baseline itself ──────────────────────────────────────────────────

def test_months_with_no_income_count_as_zero(ledger_db):
    """A seasonal earner's quiet months must not vanish from the average.

    Income in three of six months means a typical month is $1,500, not the
    $3,000 you get by pretending the empty months were never imported.
    """
    from utils.analytics import typical_monthly_income

    for index, month in enumerate(SIX):
        _month(ledger_db, month, income=3000.0 if index % 2 == 0 else 0.0)
    ledger_db.commit()

    result = typical_monthly_income(conn=ledger_db)
    assert result["amount"] == pytest.approx(1500.0)
    assert result["months_used"] == 6


def test_the_baseline_is_an_average_of_the_months_it_names(ledger_db):
    """Whatever months the label claims must be exactly what was averaged."""
    from utils.analytics import typical_monthly_income

    incomes = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    for month, income in zip(SIX, incomes):
        _month(ledger_db, month, income=income)
    ledger_db.commit()

    result = typical_monthly_income(conn=ledger_db)
    assert result["amount"] == pytest.approx(sum(incomes) / 6)
    assert result["months_used"] == 6
    assert [m["month"] for m in result["months"]] == list(SIX)
    assert [m["income"] for m in result["months"]] == pytest.approx(incomes)


def test_only_the_latest_six_complete_months_are_used(ledger_db):
    from utils.analytics import typical_monthly_income

    for month in ("2025-09", "2025-10", "2025-11", "2025-12"):
        _month(ledger_db, month, income=9000.0)
    for month in SIX:
        _month(ledger_db, month, income=3000.0)
    ledger_db.commit()

    result = typical_monthly_income(conn=ledger_db)
    assert result["months_used"] == 6
    assert result["amount"] == pytest.approx(3000.0)
    assert result["first_month"] == "2026-01"


# ── every screen agrees ──────────────────────────────────────────────────

def test_home_and_plan_report_the_same_income(ledger_db):
    """The 1.5.0 miss: Home said $3,450 and Plan said $900 on this data."""
    from desktop.engine.ledger_engine import _plan_payload
    from utils.analytics import typical_monthly_income
    from utils.checkin import safe_to_spend_summary

    for index, month in enumerate(SIX):
        _month(ledger_db, month, income=3000.0)
        if index % 2 == 0:  # lumpy e-transfer, three months of six
            add_tx(ledger_db, day=f"{month}-12",
                   desc="INTERAC E-TRANSFER FROM INVENTED ROOMMATE",
                   amount=900, direction="credit", category="Other Income")
    ledger_db.commit()

    baseline = typical_monthly_income(conn=ledger_db)["amount"]
    home = safe_to_spend_summary(conn=ledger_db, today="2026-06-28")
    plan = _plan_payload(conn=ledger_db)

    assert home["reserved"]["reliable_income"] == pytest.approx(baseline)
    assert plan["proposal"]["income_target"] == pytest.approx(baseline)
    assert plan["income_basis"]["amount"] == pytest.approx(baseline)
    assert plan["income_basis"]["months_used"] == baseline_months(ledger_db)


def baseline_months(conn):
    from utils.analytics import typical_monthly_income
    return typical_monthly_income(conn=conn)["months_used"]


def test_plan_states_which_months_its_income_came_from(ledger_db):
    from desktop.engine.ledger_engine import _plan_payload

    for month in SIX:
        _month(ledger_db, month)
    ledger_db.commit()

    plan = _plan_payload(conn=ledger_db)
    label = plan["income_basis"]["basis_label"]
    assert "6 complete months" in label
    assert "January" in label and "June" in label


# ── an ended month is not money you have now ─────────────────────────────

def test_a_finished_month_is_not_offered_as_current_money(ledger_db):
    """Data through June, opened in late July: June is history, not a budget."""
    from utils.checkin import safe_to_spend_summary

    for month in SIX:
        _month(ledger_db, month)
    ledger_db.commit()

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-24")
    assert sts["available"] is False
    assert sts["is_stale"] is True
    assert "2026-06" in sts["reason"] or "June" in sts["reason"]


def test_the_current_month_still_produces_a_number(ledger_db):
    """The guard must not fire while the user is inside the latest month."""
    from utils.checkin import safe_to_spend_summary

    for month in SIX:
        _month(ledger_db, month)
    for day in range(1, 13):
        add_tx(ledger_db, day=f"2026-07-{day:02d}", desc=f"INVENTED CAFE {day}",
               amount=12, direction="debit", category="Food & Convenience")
    ledger_db.commit()

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-12")
    assert sts["available"] is True
    assert sts["is_stale"] is False


def test_a_few_days_of_lag_is_not_treated_as_stale(ledger_db):
    """Statements always arrive a little late; that is normal, not stale."""
    from utils.checkin import safe_to_spend_summary

    for month in SIX:
        _month(ledger_db, month)
    for day in range(1, 20):
        add_tx(ledger_db, day=f"2026-07-{day:02d}", desc=f"INVENTED CAFE {day}",
               amount=12, direction="debit", category="Food & Convenience")
    ledger_db.commit()

    sts = safe_to_spend_summary(conn=ledger_db, today="2026-07-24")
    assert sts["available"] is True


# ── completeness is decided once, by the engine ──────────────────────────

def test_a_sparse_but_fully_covered_month_is_complete(ledger_db):
    """Frugal households, cash users and single-account imports are not partial.

    Seven transaction days spread across a month, with activity through the
    end of it, describes a complete month. Counting transaction days punished
    people for spending little.
    """
    from utils.insights import statement_coverage

    for day in (2, 5, 9, 14, 19, 24, 29):
        add_tx(ledger_db, day=f"2026-05-{day:02d}",
               desc=f"INVENTED SHOP {day}", amount=40, direction="debit",
               category="Groceries")
    add_tx(ledger_db, day="2026-05-05", desc="INVENTED EMPLOYER PAYROLL",
           amount=3000, direction="credit", category="Payroll Income")
    ledger_db.commit()

    info = statement_coverage(
        conn=ledger_db,
    )["statement_coverage_by_month"]["2026-05"]
    assert info["complete"] is True


def test_a_month_abandoned_mid_way_is_still_partial(ledger_db):
    """The rule must still catch a genuinely half-imported month."""
    from utils.insights import statement_coverage

    for day in (2, 5, 9, 12):
        add_tx(ledger_db, day=f"2026-05-{day:02d}",
               desc=f"INVENTED SHOP {day}", amount=40, direction="debit",
               category="Groceries")
    ledger_db.commit()

    info = statement_coverage(
        conn=ledger_db,
    )["statement_coverage_by_month"]["2026-05"]
    assert info["complete"] is False


def test_the_engine_labels_every_cashflow_month_complete_or_not(ledger_db):
    """Insights recreated this in React with `m.month < data.month`.

    Financial logic in the view is how Home and Insights ended up disagreeing
    about which months were complete, so the engine has to be the one to say.
    """
    from utils.insights import monthly_aggregates, statement_coverage

    for month in SIX:
        _month(ledger_db, month)
    for day in range(1, 8):
        add_tx(ledger_db, day=f"2026-07-{day:02d}", desc=f"INVENTED CAFE {day}",
               amount=12, direction="debit", category="Food & Convenience")
    ledger_db.commit()

    coverage = statement_coverage(conn=ledger_db)["statement_coverage_by_month"]
    for row in monthly_aggregates(conn=ledger_db):
        assert "complete" in row, "every month must carry its own verdict"
        assert row["complete"] == coverage[row["month"]]["complete"]

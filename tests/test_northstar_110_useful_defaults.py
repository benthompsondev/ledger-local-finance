"""Northstar 1.1 useful-defaults regressions.

All transactions and account names are invented. The tests exercise the
month-to-month comparison and remaining-bill failures without statement data.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import add_tx, save_plan, seed_month


def test_insights_and_home_use_real_same_day_category_comparisons(
    ledger_db, monkeypatch,
):
    import utils.analysis_period as analysis_period
    from desktop.engine.ledger_engine import (
        home_dashboard_action, insights_summary_action,
    )

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 8)

    # This is a July same-day comparison fixture. Freeze only the canonical
    # analysis clock so it does not become "stale" as the real calendar moves.
    monkeypatch.setattr(analysis_period, "date", FrozenDate)

    seed_month(ledger_db, "2026-06", grocery_days=5, grocery_amount=20)
    for day in (22, 23, 24, 25):
        add_tx(
            ledger_db, day=f"2026-06-{day:02d}",
            desc=f"INVENTED COVERAGE TRANSFER {day}", amount=1,
            direction="debit", category="Internal Transfer", is_transfer=1,
        )
    add_tx(
        ledger_db, day="2026-06-08", desc="INVENTED CLOTHING",
        amount=40, direction="debit", category="Shopping",
    )
    add_tx(
        ledger_db, day="2026-07-02", desc="INVENTED GROCER JULY",
        amount=150, direction="debit", category="Groceries",
    )
    add_tx(
        ledger_db, day="2026-07-08", desc="INVENTED CLOTHING JULY",
        amount=10, direction="debit", category="Shopping",
    )
    ledger_db.commit()

    insights = insights_summary_action({"period_days": 30})
    dashboard = home_dashboard_action({"period_days": 30})

    by_category = {
        item["category"]: item for item in insights["category_pace"]["items"]
    }
    assert insights["category_pace"]["month"] == "2026-07"
    assert insights["category_pace"]["previous_month"] == "2026-06"
    assert by_category["Groceries"]["amount"] == pytest.approx(150)
    assert by_category["Groceries"]["previous"] == pytest.approx(100)
    assert by_category["Groceries"]["delta"] == pytest.approx(50)
    assert by_category["Shopping"]["previous"] == pytest.approx(40)
    assert by_category["Shopping"]["delta"] == pytest.approx(-30)
    assert insights["category_movers"]["increase"]["category"] == "Groceries"
    assert insights["category_movers"]["decrease"]["category"] == "Shopping"
    assert insights["pace"]["previous_total_same_day"] is not None
    assert dashboard["category_pace"] == insights["category_pace"]


def test_amount_kept_falls_back_to_partial_month_same_day(ledger_db):
    from desktop.engine.ledger_engine import home_summary

    add_tx(
        ledger_db, day="2026-06-01", desc="INVENTED JUNE PAY",
        amount=100, direction="credit", category="Payroll Income",
    )
    add_tx(
        ledger_db, day="2026-06-15", desc="INVENTED JUNE BILL",
        amount=75, direction="debit", category="Utilities / Bills",
    )
    add_tx(
        ledger_db, day="2026-07-01", desc="INVENTED JULY PAY",
        amount=200, direction="credit", category="Payroll Income",
    )
    add_tx(
        ledger_db, day="2026-07-15", desc="INVENTED JULY BILL",
        amount=50, direction="debit", category="Utilities / Bills",
    )
    ledger_db.commit()

    result = home_summary({"period_days": 90})["period_result"]

    assert result["comparison_available"] is True
    assert result["comparison_basis"] == "month_to_date"
    assert result["prior_start"] == "2026-06-01"
    assert result["prior_end"] == "2026-06-15"
    assert result["comparison_delta"] == pytest.approx(125)
    assert "through day 15" in result["comparison_label"]


def test_safe_to_spend_reserves_only_commitments_not_seen_this_month(ledger_db):
    from utils.checkin import safe_to_spend_summary
    from utils.database import create_account, insert_account_balance

    for month in ("2026-04", "2026-05", "2026-06"):
        seed_month(ledger_db, month)
    add_tx(
        ledger_db, day="2026-07-01", desc="LANDLORD RENT",
        amount=1800, direction="debit", category="Housing / Mortgage",
    )
    add_tx(
        ledger_db, day="2026-07-10", desc="STREAMCO",
        amount=15, direction="debit", category="Subscriptions & Digital",
        account_type="mastercard",
    )
    add_tx(
        ledger_db, day="2026-07-15", desc="INVENTED GROCER JULY",
        amount=50, direction="debit", category="Groceries",
    )
    save_plan(
        ledger_db, "2026-07", income=5000, spending=3400, savings=0,
    )
    account_id = create_account({
        "name": "Invented Everyday Chequing", "type": "chequing",
    }, conn=ledger_db)
    insert_account_balance({
        "account_ref": account_id,
        "account_name": "Invented Everyday Chequing",
        "account_kind": "chequing",
        "balance": 4300,
        "currency": "CAD",
        "as_of_date": "2026-07-15",
    }, conn=ledger_db)
    ledger_db.commit()

    # 1.4.1: a missing card balance falls back to the flow budget number
    # instead of blocking; the cash-reconciled path (below) still needs it.
    missing_card = safe_to_spend_summary(conn=ledger_db, today="2026-07-16")
    assert missing_card["available"] is True
    assert missing_card["reserved"].get("basis") == "flow"

    card_id = create_account({
        "name": "Invented Credit Card", "type": "credit_card",
    }, conn=ledger_db)
    insert_account_balance({
        "account_ref": card_id,
        "account_name": "Invented Credit Card",
        "account_kind": "credit_card",
        "balance": 0,
        "currency": "CAD",
        "as_of_date": "2026-07-15",
    }, conn=ledger_db)
    ledger_db.commit()
    safe = safe_to_spend_summary(conn=ledger_db, today="2026-07-16")

    assert safe["available"] is True
    assert safe["reserved"]["bills_remaining"] == pytest.approx(90)
    assert safe["reserved"]["subscriptions_remaining"] == pytest.approx(0)
    assert safe["reserved"]["buffer"] == pytest.approx(250)
    assert safe["amount"] == pytest.approx(
        min(
            safe["reserved"]["flexible_remaining"],
            safe["reserved"]["cash_cushion_after_bills"],
        )
    )

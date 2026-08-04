"""Internal transfers and credit-card payments must never count as
ordinary income or spending — the double-counting guard."""
import pytest

from utils.analytics import compute_cashflow


def test_cc_payments_and_internal_transfers_excluded(steady_db):
    cf = compute_cashflow("2026-06-01", "2026-06-30", conn=steady_db)
    # Income: 2 × 2500 payroll only — the $500 Mastercard "PAYMENT
    # RECEIVED" credit must not inflate it.
    assert cf["income"] == pytest.approx(5000.0)
    # Spending: rent 1800 + hydro 90 + sub 15 + groceries 16×30 (480)
    # + coffee 6 + June extras (Shopping 200 + Pets 10) = 2601.
    # The $500 CC payment debit and $400 internal transfer are excluded.
    assert cf["spending"] == pytest.approx(2601.0)
    assert cf["net"] == pytest.approx(5000.0 - 2601.0)


def test_savings_rate_derives_from_true_flows(steady_db):
    cf = compute_cashflow("2026-06-01", "2026-06-30", conn=steady_db)
    assert cf["savings_rate"] == pytest.approx(
        (cf["income"] - cf["spending"]) / cf["income"] * 100, abs=0.1)

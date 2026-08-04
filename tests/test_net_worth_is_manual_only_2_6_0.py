"""One net worth: the monthly reading the user typed.

All figures are invented.

Northstar shipped two things called Net worth. One was the deliberate monthly
tracker — cash, investments, other assets, debts, entered by hand, every chart
point a real reading. The other was derived from whatever account balance
snapshots happened to exist, and it appeared right beside the first one with
its own Assets and Liabilities cards and a "quick estimate" form.

Two numbers answering one question is the failure this repo has a rule about.
Worse, the derived one is wrong far more often: it only ever sees the accounts
someone remembered to record a balance for, so it silently omits the pension,
the car and the mortgage.

2.6.0 keeps the monthly reading and removes the duplicate presentation. The
underlying balances stay — Safe to Spend and Plan need them — but they are
planning inputs shown in Settings, never a second measurement of net worth.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from desktop.engine import ledger_engine

REPO = pathlib.Path(__file__).resolve().parents[1]
INSIGHTS = REPO / "desktop" / "src" / "InsightsView.tsx"
SETTINGS = REPO / "desktop" / "src" / "SettingsView.tsx"

MANUAL_NET_WORTH = 4000.0
CHEQUING_BALANCE = 5400.0


def _request(action: str, **params):
    import io
    out = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params}) + "\n"), out,
    )
    return code, json.loads(out.getvalue())


def _ok(action: str, **params):
    code, response = _request(action, **params)
    assert code == 0, response
    return response["data"]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    ledger_engine._bind_database()
    return tmp_path


@pytest.fixture
def reading_and_balance(engine):
    """A $4,000 monthly reading and a $5,400 chequing snapshot.

    The exact conflict from the brief: a saved reading and a larger balance
    that used to be presented as a competing net worth.
    """
    _ok("create_account", name="Invented Chequing", type="chequing")
    account = _ok("list_accounts")["accounts"][0]
    _ok("save_net_worth_entry", month="2026-06", cash=1500.0,
        investments=3000.0, other_assets=0.0, liabilities=500.0, note="")
    _ok("add_account_balance", account_ref=account["id"],
        balance=CHEQUING_BALANCE, as_of_date="2026-06-30", note="")
    return account


# ── the saved reading wins ───────────────────────────────────────────────

def test_the_typed_reading_is_the_net_worth(reading_and_balance):
    trend = _ok("net_worth_trend")

    assert trend["current"]["net"] == pytest.approx(MANUAL_NET_WORTH)
    assert trend["months_recorded"] == 1


def test_a_larger_balance_never_becomes_the_net_worth(reading_and_balance):
    """$5,400 sitting in chequing is not what this household is worth."""
    trend = _ok("net_worth_trend")

    assert trend["current"]["net"] != pytest.approx(CHEQUING_BALANCE)
    assert trend["current"]["net"] == pytest.approx(MANUAL_NET_WORTH)


def test_planning_balances_report_no_net_worth_at_all(reading_and_balance):
    """The Settings payload carries balances and nothing that sums them."""
    planning = _ok("get_planning_balances")

    assert [b["balance"] for b in planning["balances"]] == [CHEQUING_BALANCE]
    for banned in ("net_worth", "total_assets", "total_liabilities", "net"):
        assert banned not in planning, (
            f"the planning payload grew a {banned} total"
        )


def test_prefill_offers_the_balance_but_the_saved_month_still_wins(
    reading_and_balance,
):
    """A balance may start an empty form. It may not rewrite June."""
    trend = _ok("net_worth_trend")
    prefill = trend["prefill"]

    assert prefill["has_balances"] is True, "the balance should be offered"
    june = next(e for e in trend["entries"] if e["month"] == "2026-06")
    assert june["cash"] == pytest.approx(1500.0), (
        "a balance snapshot overwrote a reading the user typed"
    )
    assert june["net"] == pytest.approx(MANUAL_NET_WORTH)


def test_recording_another_balance_leaves_every_reading_alone(
    reading_and_balance,
):
    account = reading_and_balance
    before = _ok("net_worth_trend")["entries"]

    _ok("add_account_balance", account_ref=account["id"],
        balance=9999.0, as_of_date="2026-07-01", note="")

    after = _ok("net_worth_trend")["entries"]
    assert after == before, "adding a balance changed a saved reading"


def test_the_monthly_tracker_still_edits_and_deletes(reading_and_balance):
    """Removing the duplicate must not damage the feature being kept."""
    _ok("save_net_worth_entry", month="2026-07", cash=1800.0,
        investments=3100.0, other_assets=0.0, liabilities=400.0, note="")
    assert _ok("net_worth_trend")["months_recorded"] == 2

    # Saving the same month again corrects it rather than duplicating.
    corrected = _ok("save_net_worth_entry", month="2026-07", cash=1900.0,
                    investments=3100.0, other_assets=0.0, liabilities=400.0,
                    note="")
    assert corrected["months_recorded"] == 2

    after_delete = _ok("delete_net_worth_entry", month="2026-07")
    assert after_delete["months_recorded"] == 1
    assert _ok("net_worth_trend")["current"]["net"] == pytest.approx(
        MANUAL_NET_WORTH
    )


# ── the duplicate interface is gone from the source ──────────────────────

def test_insights_no_longer_renders_a_balance_derived_net_worth() -> None:
    source = INSIGHTS.read_text(encoding="utf-8")

    for gone in (
        "quickForm",                 # the quick-estimate form state
        "saveQuickNetWorth",         # its save call
        "Quick estimate",            # its summary label
        "net_worth.calculated",      # the balance-derived figure
        "total_liabilities",         # the Liabilities card
        "net-worth-section",         # the duplicate disclosure
    ):
        assert gone not in source, f"InsightsView still contains {gone!r}"


def test_insights_keeps_the_monthly_tracker() -> None:
    source = INSIGHTS.read_text(encoding="utf-8")

    for kept in (
        "What you are worth, month by month",
        "NetWorthEntryForm",
        "NetWorthTrendChart",
        "Every reading",
        "Record or correct a month",
    ):
        assert kept in source, f"InsightsView lost {kept!r}"


def test_only_one_thing_is_called_net_worth_in_settings() -> None:
    """Settings holds planning balances. It must not total them up."""
    source = SETTINGS.read_text(encoding="utf-8")

    assert "Planning balances" in source
    assert "Accounts available for spending" in source
    # No aggregate presented in the planning section.
    for banned in ("Net worth", "Total assets", "Total debts", "Liabilities"):
        assert banned not in source, (
            f"Settings presents an aggregate {banned!r}"
        )

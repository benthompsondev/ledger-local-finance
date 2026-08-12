"""Net worth is measured monthly, and every figure in it was entered.

All figures are invented.

What this replaces matters, because it is the reason the feature was worth
rebuilding rather than polishing. Net worth used to be a *projection*: one
starting figure typed once, plus each complete month's kept amount from the
imported statements. It drew a confident line that could not see a brokerage
account move, a mortgage come down, or a car lose value, and the only table
that could have held real readings was never written by the desktop app, so
the "measured" series drawn beside it was empty on every install.

So the properties locked here are about honesty first and arithmetic second:

  * every point comes from a reading someone entered;
  * the month is the key, so recording August twice corrects August;
  * a component cannot be negative, because a negative debt is an asset and
    a negative cash balance is a debt, and silently accepting either flips a
    sign somewhere downstream;
  * net worth itself *can* be negative, and must be;
  * a percentage change is absent rather than wrong when the previous month
    was zero or negative.
"""
from __future__ import annotations

import io
from pathlib import Path
import json

import pytest

from utils.net_worth import (
    delete_entry, list_entries, net_worth_overview, save_entry,
    suggested_entry,
)


def _engine(action: str, **params) -> dict:
    from desktop.engine import ledger_engine

    output = io.StringIO()
    ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params}) + "\n"),
        output,
    )
    return json.loads(output.getvalue())


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    import utils.database as db

    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _account(name: str, kind: str) -> int:
    assert _engine("create_account", name=name, type=kind)["ok"]
    return next(
        int(a["id"]) for a in _engine("list_accounts")["data"]["accounts"]
        if a["name"] == name
    )


def _record(month: str, cash=0, investments=0, other_assets=0, liabilities=0):
    return save_entry(
        month, cash=cash, investments=investments,
        other_assets=other_assets, liabilities=liabilities,
        today="2099-12-31",
    )


# ── the arithmetic ───────────────────────────────────────────────────────

def test_net_worth_is_assets_minus_debts(ledger) -> None:
    entry = _record("2026-01", cash=5000, investments=30000,
                    other_assets=12000, liabilities=4000)

    assert entry["net"] == 43000.0


def test_net_worth_can_be_negative(ledger) -> None:
    """Someone with a mortgage and no equity is the normal case, not an error."""
    entry = _record("2026-01", cash=800, liabilities=42000)

    assert entry["net"] == -41200.0


def test_cents_survive_a_year_of_readings(ledger) -> None:
    for index in range(12):
        _record(f"2026-{index + 1:02d}", cash=1000.01 + index * 0.01)

    entries = list_entries()
    assert entries[-1]["cash"] == 1000.12
    assert entries[-1]["net"] == 1000.12
    # 0.01 a month for eleven months, with no float drift accumulating.
    assert entries[-1]["net"] - entries[0]["net"] == pytest.approx(0.11, abs=1e-9)


# ── the month is the key ─────────────────────────────────────────────────

def test_recording_a_month_twice_corrects_it(ledger) -> None:
    _record("2026-03", cash=1000)
    _record("2026-03", cash=2500)

    entries = list_entries()
    assert len(entries) == 1
    assert entries[0]["net"] == 2500.0


def test_entries_come_back_oldest_first_whatever_order_they_arrived(
    ledger,
) -> None:
    for month in ("2026-05", "2026-01", "2026-03", "2026-02"):
        _record(month, cash=100)

    assert [e["month"] for e in list_entries()] == [
        "2026-01", "2026-02", "2026-03", "2026-05",
    ]


def test_a_month_can_be_removed(ledger) -> None:
    _record("2026-01", cash=100)
    _record("2026-02", cash=200)

    assert delete_entry("2026-02") is True
    assert [e["month"] for e in list_entries()] == ["2026-01"]
    assert delete_entry("2026-02") is False


@pytest.mark.parametrize("month", [
    "", "2026", "2026-13", "2026-00", "26-01", "2026-1", "not-a-month",
])
def test_a_bad_month_is_refused_rather_than_stored(ledger, month) -> None:
    with pytest.raises(ValueError):
        _record(month, cash=100)
    assert list_entries() == []


def test_a_full_date_is_refused_rather_than_truncated(ledger) -> None:
    """2.5.2 read "2026-01-15" as January, by slicing the first 7 characters.

    That was documented as deliberate, and it was the wrong call: the same
    truncation read "2026-01-garbage" as January too, so a malformed month
    could silently overwrite or delete a real reading instead of being
    rejected. The month picker sends YYYY-MM, and the carry-forward from the
    retired tables does its own date handling, so nothing needs the leniency.
    """
    _record("2026-01", cash=1000)

    with pytest.raises(ValueError):
        _record("2026-01-15", cash=9999)

    assert list_entries()[0]["net"] == 1000.0


# ── components cannot be negative, because the sign would go somewhere ───

@pytest.mark.parametrize("field", [
    "cash", "investments", "other_assets", "liabilities",
])
def test_a_negative_component_is_refused(ledger, field) -> None:
    with pytest.raises(ValueError) as raised:
        save_entry("2026-01", **{field: -100})

    assert "negative" in str(raised.value).lower()
    assert list_entries() == []


def test_an_implausible_figure_is_refused(ledger) -> None:
    """A typo of a few extra zeros is the likeliest bad input here."""
    with pytest.raises(ValueError) as raised:
        _record("2026-01", cash=50_000_000_000)

    assert "typo" in str(raised.value).lower()


@pytest.mark.parametrize("value", ["abc", None, float("inf"), float("nan")])
def test_a_figure_that_is_not_a_number_is_refused(ledger, value) -> None:
    if value is None:
        # None is a blank field, which is a genuine zero.
        assert save_entry("2026-01", cash=None)["cash"] == 0.0
        return
    with pytest.raises(ValueError):
        _record("2026-01", cash=value)


# ── movement ─────────────────────────────────────────────────────────────

def test_the_first_reading_has_no_change(ledger) -> None:
    _record("2026-01", cash=1000)

    entry = list_entries()[0]
    assert entry["change"] is None
    assert entry["change_pct"] is None


def test_change_is_measured_against_the_previous_reading(ledger) -> None:
    _record("2026-01", cash=1000)
    _record("2026-02", cash=1250)

    entries = list_entries()
    assert entries[1]["change"] == 250.0
    assert entries[1]["change_pct"] == 25.0


def test_a_percentage_is_absent_rather_than_wrong_from_a_negative_base(
    ledger,
) -> None:
    """Percent change from -$500 to -$250 is not a number worth printing."""
    _record("2026-01", liabilities=500)
    _record("2026-02", liabilities=250)

    entries = list_entries()
    assert entries[1]["change"] == 250.0
    assert entries[1]["change_pct"] is None


def test_a_percentage_is_absent_from_a_zero_base(ledger) -> None:
    _record("2026-01")
    _record("2026-02", cash=100)

    assert list_entries()[1]["change_pct"] is None


def test_a_gap_in_months_is_reported_as_a_gap(ledger) -> None:
    """Skipping a month is normal, and it is not a month-over-month move.

    2.5.2 asserted the opposite here: it read January to June as
    "month over month" because January was one row back. Positions are not
    months, and a label that names a calendar period has to mean it, so the
    movement is now reported as since_last_reading with both months on it.
    """
    _record("2026-01", cash=1000)
    _record("2026-06", cash=1600)

    overview = net_worth_overview()
    assert overview["months_recorded"] == 2
    assert overview["month_over_month"] is None
    assert overview["three_month"] is None
    assert overview["since_last_reading"]["from_month"] == "2026-01"
    assert overview["since_last_reading"]["to_month"] == "2026-06"
    assert overview["since_last_reading"]["amount"] == 600.0
    assert overview["since_last_reading"]["months_apart"] == 5


def test_the_overview_reports_windows_best_and_worst(ledger) -> None:
    for index, cash in enumerate([1000, 1200, 900, 1500, 1700]):
        _record(f"2026-{index + 1:02d}", cash=cash)

    overview = net_worth_overview()
    assert overview["current"]["net"] == 1700.0
    assert overview["month_over_month"]["amount"] == 200.0
    assert overview["three_month"]["amount"] == 500.0
    assert overview["twelve_month"] is None
    assert overview["since_first"]["amount"] == 700.0
    assert overview["best_month"]["month"] == "2026-04"
    assert overview["worst_month"]["month"] == "2026-03"
    assert overview["average_move"] == pytest.approx(175.0)


def test_component_moves_describe_the_latest_month(ledger) -> None:
    _record("2026-01", cash=1000, investments=5000, liabilities=2000)
    _record("2026-02", cash=1100, investments=5400, liabilities=1800)

    moves = {m["field"]: m for m in net_worth_overview()["component_moves"]}
    assert moves["cash"]["change"] == 100.0
    assert moves["investments"]["change"] == 400.0
    assert moves["liabilities"]["change"] == -200.0
    assert moves["other_assets"]["change"] == 0.0


def test_an_empty_ledger_asks_for_a_reading(ledger) -> None:
    overview = net_worth_overview()

    assert overview["has_entries"] is False
    assert overview["entries"] == []
    assert overview["reason"]


# ── prefill from account balances ────────────────────────────────────────

def test_prefill_groups_accounts_into_the_four_figures(ledger) -> None:
    chequing = _account("Invented Chequing", "chequing")
    savings = _account("Invented Savings", "savings")
    brokerage = _account("Invented Brokerage", "investment")
    card = _account("Invented Card", "credit_card")
    for ref, balance in (
        (chequing, 5400.0), (savings, 12000.0),
        (brokerage, 48250.0), (card, 1430.0),
    ):
        assert _engine("add_account_balance", account_ref=ref,
                       balance=balance, as_of_date="2026-07-24")["ok"]

    prefill = suggested_entry("2026-07")

    assert prefill["cash"] == 17400.0, "chequing and savings are both cash"
    assert prefill["investments"] == 48250.0
    assert prefill["other_assets"] == 0.0
    assert prefill["liabilities"] == 1430.0
    assert prefill["net"] == 64220.0
    assert prefill["has_balances"] is True


def test_prefill_names_the_account_behind_every_figure(ledger) -> None:
    """A total that looks wrong has to be traceable without guessing."""
    chequing = _account("Invented Chequing", "chequing")
    _engine("add_account_balance", account_ref=chequing, balance=5400.0,
            as_of_date="2026-07-24")

    sources = suggested_entry("2026-07")["sources"]

    assert [s["account"] for s in sources] == ["Invented Chequing"]
    assert sources[0]["field"] == "cash"
    assert sources[0]["amount"] == 5400.0


def test_prefill_is_empty_and_says_so_without_balances(ledger) -> None:
    prefill = suggested_entry("2026-07")

    assert prefill["has_balances"] is False
    assert prefill["net"] == 0.0


def test_prefill_never_writes_an_entry(ledger) -> None:
    """It is a suggestion. Nothing is recorded until the user saves."""
    chequing = _account("Invented Chequing", "chequing")
    _engine("add_account_balance", account_ref=chequing, balance=5400.0,
            as_of_date="2026-07-24")

    suggested_entry("2026-07")

    assert list_entries() == []


# ── statements as explanation, never as the measurement ──────────────────

def test_the_statement_comparison_only_covers_months_with_both_ends(
    ledger,
) -> None:
    _record("2026-01", cash=1000)
    _record("2026-02", cash=1500)

    overview = net_worth_overview()
    for row in overview["explained"]:
        assert row["month"] != "2026-01", (
            "the first reading has no previous month to have moved from"
        )
        assert row["measured"] == row["kept"] + row["unexplained"]


# ── through the engine, as the app calls it ──────────────────────────────

def test_the_engine_records_and_returns_the_overview(ledger) -> None:
    body = _engine("save_net_worth_entry", month="2026-01", cash=1000,
                   investments=2000, other_assets=0, liabilities=500)

    assert body["ok"], body
    assert body["data"]["current"]["net"] == 2500.0
    assert body["data"]["months_recorded"] == 1


def test_the_engine_refuses_a_negative_component_clearly(ledger) -> None:
    body = _engine("save_net_worth_entry", month="2026-01", cash=-5,
                   investments=0, other_assets=0, liabilities=0)

    assert body["ok"] is False
    assert "negative" in str(body["error"]).lower()


def test_the_engine_refuses_to_delete_a_month_that_is_not_there(
    ledger,
) -> None:
    body = _engine("delete_net_worth_entry", month="2019-01")

    assert body["ok"] is False
    assert "2019-01" in str(body["error"])


def test_readings_survive_a_new_engine_process(ledger) -> None:
    _engine("save_net_worth_entry", month="2026-01", cash=1000,
            investments=0, other_assets=0, liabilities=0)

    body = _engine("net_worth_trend")

    assert body["ok"], body
    assert body["data"]["months_recorded"] == 1
    assert body["data"]["current"]["net"] == 1000.0


# ── the readings are financial data, and behave like it ──────────────────

def test_a_factory_reset_removes_every_reading(ledger) -> None:
    """Found in stress testing: the table was not in RESET_TABLES, so
    "remove my financial data" quietly kept the most personal figures in
    the app."""
    for index in range(4):
        _record(f"2026-{index + 1:02d}", cash=1000, investments=5000)

    body = _engine("reset_financial_data", confirmation="RESET")

    assert body["ok"], body
    assert list_entries() == []


def test_the_reset_confirmation_counts_the_readings(ledger) -> None:
    """A destructive confirmation has to say what it destroys, and these
    cannot be recovered by importing a statement again."""
    for index in range(3):
        _record(f"2026-{index + 1:02d}", cash=1000)

    body = _engine("data_safety_status")

    assert body["ok"], body
    assert body["data"]["reset"]["net_worth_reading_count"] == 3


def test_readings_survive_a_backup_and_restore(ledger, tmp_path) -> None:
    import utils.database as db
    from utils.backups import create_backup, restore_database

    for index in range(6):
        _record(f"2026-{index + 1:02d}", cash=1000 * (index + 1))
    before = [(e["month"], e["net"]) for e in list_entries()]

    backup = create_backup(reason="test", db_path=db.DB_PATH)
    delete_entry("2026-03")
    delete_entry("2026-04")
    assert len(list_entries()) == 4

    restore_database(Path(backup), db_path=db.DB_PATH)

    assert [(e["month"], e["net"]) for e in list_entries()] == before


# ── nothing typed earlier is lost ────────────────────────────────────────

def test_an_earlier_quick_estimate_is_carried_forward(ledger) -> None:
    """The retired quick estimate held a real figure someone typed."""
    import utils.database as db

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO net_worth_estimates(total_assets, total_liabilities, "
            "as_of_date) VALUES(?, ?, ?)", (26000.0, 4000.0, "2026-02-14"),
        )
        conn.commit()
    finally:
        conn.close()

    entries = list_entries()

    assert len(entries) == 1
    assert entries[0]["month"] == "2026-02"
    assert entries[0]["net"] == 22000.0, "the total has to survive exactly"
    # The old estimate recorded no breakdown, so the label says so rather
    # than pretending the money was cash.
    assert entries[0]["other_assets"] == 26000.0
    assert "breakdown" in entries[0]["note"]


def test_carrying_forward_never_overwrites_a_real_reading(ledger) -> None:
    import utils.database as db

    _record("2026-02", cash=999)
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO net_worth_estimates(total_assets, total_liabilities, "
            "as_of_date) VALUES(?, ?, ?)", (26000.0, 4000.0, "2026-02-14"),
        )
        conn.commit()
    finally:
        conn.close()

    entries = list_entries()

    assert len(entries) == 1
    assert entries[0]["net"] == 999.0

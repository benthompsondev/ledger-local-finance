"""A comparison labelled "Three months" has to span three calendar months.

All figures are invented.

2.5.2 counted list positions, not months. With readings for January,
February, June and July, "Three months" compared January with July, because
January is three recorded rows back. That is a six-month span reported under
a three-month label, and it gets worse the sparser the readings are: two
readings a year apart were labelled "This month".

The rule here is that a labelled calendar period means that calendar period
or nothing. A missing comparison month produces null, never zero, because
zero reads as "you did not move" and that is a claim about the data rather
than an admission that it is absent.

Movement between whatever two readings do exist is still worth seeing. It is
reported separately as `since_last_reading`, carrying both months, so the
screen can say what it is actually comparing.
"""
from __future__ import annotations

import pytest

import utils.database as db
from utils.net_worth import net_worth_overview, save_entry


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    return tmp_path


def _readings(pairs: list[tuple[str, float]]) -> None:
    for month, cash in pairs:
        save_entry(month, cash=cash, today="2099-12-31")


SPARSE = [("2026-01", 100.0), ("2026-02", 200.0),
          ("2026-06", 300.0), ("2026-07", 400.0)]


# ── the reproduction ─────────────────────────────────────────────────────

def test_three_months_means_three_calendar_months(ledger) -> None:
    """The exact case from the review: Jan, Feb, Jun, Jul."""
    _readings(SPARSE)

    overview = net_worth_overview()

    assert overview["three_month"] is None, (
        "January is three rows back but six calendar months back, so there "
        "is no three-month comparison to make"
    )


def test_month_over_month_needs_the_previous_calendar_month(ledger) -> None:
    _readings(SPARSE)

    over = net_worth_overview()["month_over_month"]

    assert over is not None, "June to July is a real month-over-month"
    assert over["from_month"] == "2026-06"
    assert over["to_month"] == "2026-07"
    assert over["amount"] == 100.0


def test_a_gap_leaves_month_over_month_unavailable(ledger) -> None:
    """Two readings five months apart are not a month-over-month move."""
    _readings([("2026-01", 1000.0), ("2026-06", 1600.0)])

    overview = net_worth_overview()

    assert overview["month_over_month"] is None
    assert overview["three_month"] is None
    assert overview["twelve_month"] is None


def test_a_missing_period_is_null_and_never_zero(ledger) -> None:
    """Zero is a claim about the data. Absent is the truth here."""
    _readings(SPARSE)

    overview = net_worth_overview()

    for window in ("three_month", "twelve_month"):
        assert overview[window] is None, f"{window} should be unavailable"


# ── the windows that do exist ────────────────────────────────────────────

def test_consecutive_readings_fill_every_window(ledger) -> None:
    _readings([(f"2026-{month:02d}", 1000.0 + month * 10) for month in
               range(1, 13)]
              + [("2027-01", 1200.0)])

    overview = net_worth_overview()

    assert overview["month_over_month"]["from_month"] == "2026-12"
    assert overview["three_month"]["from_month"] == "2026-10"
    assert overview["twelve_month"]["from_month"] == "2026-01"
    assert overview["twelve_month"]["to_month"] == "2027-01"


def test_windows_cross_a_year_boundary(ledger) -> None:
    _readings([("2025-11", 100.0), ("2025-12", 200.0),
               ("2026-01", 300.0), ("2026-02", 400.0)])

    overview = net_worth_overview()

    assert overview["month_over_month"]["from_month"] == "2026-01"
    assert overview["three_month"]["from_month"] == "2025-11"
    assert overview["three_month"]["amount"] == 300.0


def test_a_window_needs_that_exact_month_not_a_nearby_one(ledger) -> None:
    """October is present, November is not; three months back is November."""
    _readings([("2026-09", 100.0), ("2026-10", 200.0), ("2026-12", 300.0)])

    overview = net_worth_overview()

    assert overview["month_over_month"] is None, "November is missing"
    assert overview["three_month"]["from_month"] == "2026-09"


# ── what replaced the misleading labels ──────────────────────────────────

def test_movement_between_readings_is_reported_with_both_months(
    ledger,
) -> None:
    _readings([("2026-01", 1000.0), ("2026-06", 1600.0)])

    since = net_worth_overview()["since_last_reading"]

    assert since["from_month"] == "2026-01"
    assert since["to_month"] == "2026-06"
    assert since["amount"] == 600.0
    assert since["months_apart"] == 5


def test_since_last_reading_is_absent_with_one_reading(ledger) -> None:
    _readings([("2026-01", 1000.0)])

    overview = net_worth_overview()

    assert overview["since_last_reading"] is None
    assert overview["month_over_month"] is None
    assert overview["three_month"] is None


def test_all_recorded_states_the_span_not_the_count(ledger) -> None:
    """Four sparse readings are not "across 4 months"."""
    _readings(SPARSE)

    since_first = net_worth_overview()["since_first"]

    assert since_first["from_month"] == "2026-01"
    assert since_first["to_month"] == "2026-07"
    assert since_first["reading_count"] == 4
    assert since_first["months_apart"] == 6, (
        "January to July is a six-month span, whatever the row count"
    )


def test_component_moves_name_the_months_they_compare(ledger) -> None:
    save_entry("2026-01", cash=1000.0, investments=5000.0, liabilities=2000.0)
    save_entry("2026-06", cash=1100.0, investments=5400.0, liabilities=1800.0)

    overview = net_worth_overview()

    assert overview["component_from_month"] == "2026-01"
    assert overview["component_to_month"] == "2026-06"
    assert overview["component_months_apart"] == 5
    moves = {m["field"]: m for m in overview["component_moves"]}
    assert moves["investments"]["change"] == 400.0
    assert moves["liabilities"]["change"] == -200.0


# ── the statement comparison must stay month-to-month ────────────────────

def test_kept_is_only_compared_with_adjacent_readings(ledger) -> None:
    """A one-month kept figure cannot explain a six-month measured change."""
    _readings(SPARSE)

    for row in net_worth_overview()["explained"]:
        assert row["months_apart"] == 1, (
            f"{row['month']} compared a one-month kept amount against a "
            f"{row['months_apart']}-month measured change"
        )


def test_a_sparse_ledger_explains_nothing_rather_than_guessing(
    ledger,
) -> None:
    _readings([("2026-01", 1000.0), ("2026-06", 1600.0)])

    assert net_worth_overview()["explained"] == []


# ── negative net worth behaves in every window ───────────────────────────

def test_windows_work_while_net_worth_is_negative(ledger) -> None:
    save_entry("2026-01", cash=100.0, liabilities=50000.0)
    save_entry("2026-02", cash=100.0, liabilities=48000.0)
    save_entry("2026-03", cash=100.0, liabilities=46000.0)
    save_entry("2026-04", cash=100.0, liabilities=44000.0)

    overview = net_worth_overview()

    assert overview["current"]["net"] == -43900.0
    assert overview["month_over_month"]["amount"] == 2000.0
    assert overview["three_month"]["amount"] == 6000.0
    assert overview["three_month"]["from_net"] == -49900.0
    assert overview["since_first"]["amount"] == 6000.0


def test_crossing_zero_reports_the_real_movement(ledger) -> None:
    save_entry("2026-01", cash=100.0, liabilities=5000.0)
    save_entry("2026-02", cash=9000.0, liabilities=1000.0)

    over = net_worth_overview()["month_over_month"]

    assert over["from_net"] == -4900.0
    assert over["to_net"] == 8000.0
    assert over["amount"] == 12900.0

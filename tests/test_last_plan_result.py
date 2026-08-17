"""Last plan result — eligibility and comparison behaviour.

All transactions, plans and account names are invented. These assert what the
card must be true about money: that it only ever compares a month against an
intention that provably existed at the time, that its figures are the same
canonical personal-view figures the rest of the app reports, and that it
refuses rather than guesses.
"""
from __future__ import annotations

import pytest
from zoneinfo import ZoneInfo

from tests.conftest import add_tx, save_plan, seed_month


def _stamp(conn, month: str, created: str, updated: str) -> None:
    """Set a plan row's persisted timing.

    `upsert_monthly_plan` always stamps `updated_at` with the current clock,
    so a historical intention cannot be expressed through the normal helper.
    """
    conn.execute(
        "UPDATE monthly_plans SET created_at=?, updated_at=? WHERE month=?",
        (created, updated, month),
    )
    conn.commit()


def _contemporaneous_plan(conn, month: str, *, savings: float,
                          day: str = "03") -> None:
    """A plan saved early in the month it targets."""
    save_plan(conn, month, savings=savings)
    stamp = f"{month}-{day} 09:15:00"
    _stamp(conn, month, stamp, stamp)


def _result(conn, today="2026-08-15"):
    from utils.plan_result import last_plan_result

    return last_plan_result(conn=conn, today=today)


# ── the comparison ────────────────────────────────────────────────────────

def test_a_met_plan_reports_the_surplus(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    result = _result(ledger_db)

    assert result["available"], result.get("reason")
    assert result["month"] == "2026-06"
    assert result["month_label"] == "June 2026"
    assert result["intended_kept"] == pytest.approx(800.0)
    # seed_month: 2 × 2500 in, 2391 spent.
    assert result["money_in"] == pytest.approx(5000.0)
    assert result["spending"] == pytest.approx(2391.0)
    assert result["actual_kept"] == pytest.approx(2609.0)
    assert result["difference"] == pytest.approx(1809.0)
    assert result["met"] is True
    assert result["kept_is_negative"] is False


def test_a_missed_plan_reports_the_shortfall_without_apology(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=3000)

    result = _result(ledger_db)

    assert result["intended_kept"] == pytest.approx(3000.0)
    assert result["actual_kept"] == pytest.approx(2609.0)
    assert result["difference"] == pytest.approx(-391.0)
    assert result["difference_abs"] == pytest.approx(391.0)
    assert result["met"] is False


def test_a_month_that_spent_more_than_it_earned_reads_honestly(ledger_db):
    seed_month(ledger_db, "2026-06", payroll=900, grocery_days=20,
               grocery_amount=95)
    _contemporaneous_plan(ledger_db, "2026-06", savings=200)

    result = _result(ledger_db)

    assert result["actual_kept"] < 0
    assert result["kept_is_negative"] is True
    assert result["met"] is False
    # The shortfall is the whole distance from target to reality, not just
    # the part below zero.
    assert result["difference"] == pytest.approx(
        result["actual_kept"] - 200.0
    )
    assert result["actual_kept_abs"] == pytest.approx(
        abs(result["actual_kept"])
    )


def test_a_zero_savings_target_is_a_real_intention(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=0)

    result = _result(ledger_db)

    assert result["intended_kept"] == pytest.approx(0.0)
    assert result["target_is_zero"] is True
    assert result["met"] is True
    assert result["difference"] == pytest.approx(2609.0)


def test_the_figures_match_canonical_monthly_aggregates(ledger_db):
    """The card must not be able to quote a number Insights disagrees with."""
    from utils.insights import monthly_aggregates

    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    result = _result(ledger_db)
    aggregate = next(row for row in monthly_aggregates(conn=ledger_db)
                     if row["month"] == "2026-06")

    assert result["money_in"] == pytest.approx(aggregate["income"])
    assert result["spending"] == pytest.approx(aggregate["spending"])
    assert result["actual_kept"] == pytest.approx(aggregate["net"])


def test_a_shared_expense_only_counts_the_user_share(ledger_db):
    seed_month(ledger_db, "2026-06")
    add_tx(ledger_db, day="2026-06-12", desc="SHARED HYDRO BILL", amount=400,
           direction="debit", category="Utilities / Bills",
           merchant="Shared Hydro")
    ledger_db.execute(
        "UPDATE transactions SET shared_expense_override=1, "
        "shared_user_share_pct=50 WHERE raw_description='SHARED HYDRO BILL'"
    )
    ledger_db.commit()
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    result = _result(ledger_db)

    # Only the user's $200 half lands in spending, so kept falls by 200.
    assert result["spending"] == pytest.approx(2591.0)
    assert result["actual_kept"] == pytest.approx(2409.0)


# ── refusing what cannot be proven ────────────────────────────────────────

def test_a_plan_written_after_the_month_ended_is_refused(ledger_db):
    """A target set once the result was knowable proves no intention."""
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", savings=800)
    _stamp(ledger_db, "2026-06", "2026-07-18 11:26:27", "2026-07-18 07:26:27")

    result = _result(ledger_db)

    assert result["available"] is False
    assert "complete statement coverage" in result["reason"]


def test_a_plan_edited_after_the_month_ended_is_refused(ledger_db):
    """Created in time, but its amounts moved once the month was over."""
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", savings=800)
    _stamp(ledger_db, "2026-06", "2026-06-02 09:00:00", "2026-07-09 20:00:00")

    assert _result(ledger_db)["available"] is False


def test_an_exact_plan_may_be_saved_before_its_target_month(ledger_db):
    """The month key identifies the intent; early planning is still planning."""
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", savings=800)
    _stamp(ledger_db, "2026-06", "2026-05-30 09:00:00",
           "2026-05-30 09:00:00")

    assert _result(ledger_db)["month"] == "2026-06"


def test_new_utc_stamp_in_the_last_local_hours_is_contemporaneous():
    """June 30 at 23:30 Toronto is July 1 in UTC, but still a June plan."""
    from utils.plan_result import intention_is_contemporaneous

    plan = {
        "created_at": "2026-07-01T03:30:00Z",
        "updated_at": "2026-07-01T03:30:00Z",
    }

    assert intention_is_contemporaneous(
        plan, "2026-06", local_timezone=ZoneInfo("America/Toronto")
    ) is True


def test_new_utc_stamp_after_local_month_end_is_refused():
    from utils.plan_result import intention_is_contemporaneous

    plan = {
        # Exactly local midnight belongs to July, not the June plan window.
        "created_at": "2026-07-01T04:00:00Z",
        "updated_at": "2026-07-01T04:00:00Z",
    }

    assert intention_is_contemporaneous(
        plan, "2026-06", local_timezone=ZoneInfo("America/Toronto")
    ) is False


def test_new_utc_month_end_uses_the_historical_dst_offset():
    """Toronto month-end is UTC-4 in June and UTC-5 in November."""
    from utils.plan_result import intention_is_contemporaneous

    toronto = ZoneInfo("America/Toronto")
    assert intention_is_contemporaneous({
        "created_at": "2026-12-01T04:30:00Z",
        "updated_at": "2026-12-01T04:30:00Z",
    }, "2026-11", local_timezone=toronto) is True
    assert intention_is_contemporaneous({
        "created_at": "2026-12-01T05:00:00Z",
        "updated_at": "2026-12-01T05:00:00Z",
    }, "2026-11", local_timezone=toronto) is False


def test_legacy_mixed_boundary_stamps_remain_fail_closed():
    """Unmarked historical UTC/local values cannot be reconstructed safely."""
    from utils.plan_result import intention_is_contemporaneous

    assert intention_is_contemporaneous({
        "created_at": "2026-07-01 03:30:00",  # old SQLite UTC
        "updated_at": "2026-06-30 23:30:00",  # old Python local time
    }, "2026-06", local_timezone=ZoneInfo("America/Toronto")) is False


def test_an_unparseable_or_missing_stamp_proves_nothing(ledger_db):
    seed_month(ledger_db, "2026-06")
    save_plan(ledger_db, "2026-06", savings=800)
    _stamp(ledger_db, "2026-06", "", "2026-06-02 09:00:00")

    assert _result(ledger_db)["available"] is False


def test_a_month_without_its_own_plan_is_never_compared(ledger_db):
    """get_applicable_plan would fall back to the newest saved plan here.

    That fallback is right for planning and wrong for judging a month, so a
    month with no plan row of its own must simply not appear.
    """
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    # Only May owns a plan; June must not borrow it.
    _contemporaneous_plan(ledger_db, "2026-05", savings=800)

    result = _result(ledger_db)

    assert result["available"] is True
    assert result["month"] == "2026-05"


def test_a_partial_month_is_refused_even_with_its_own_plan(ledger_db):
    seed_month(ledger_db, "2026-05")
    _contemporaneous_plan(ledger_db, "2026-05", savings=800)
    # June has a plan and a little data, but nowhere near full coverage.
    add_tx(ledger_db, day="2026-06-02", desc="GROCER PARTIAL", amount=30,
           direction="debit", category="Groceries")
    ledger_db.commit()
    _contemporaneous_plan(ledger_db, "2026-06", savings=900)

    result = _result(ledger_db)

    # Falls back past the partial month to the last one that qualifies.
    assert result["month"] == "2026-05"


def test_the_month_being_lived_is_never_judged(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    # Standing inside June, June has no result yet.
    assert _result(ledger_db, today="2026-06-20")["available"] is False


def test_no_saved_plan_at_all_is_a_clean_absence(ledger_db):
    seed_month(ledger_db, "2026-06")

    result = _result(ledger_db)

    assert result["available"] is False
    assert result["reason"] == "No saved plan yet."


def test_the_most_recent_eligible_month_wins(ledger_db):
    seed_month(ledger_db, "2026-05")
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-05", savings=500)
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    assert _result(ledger_db)["month"] == "2026-06"


# ── it is a read ──────────────────────────────────────────────────────────

def test_reading_the_result_writes_nothing(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    before = ledger_db.total_changes
    plans_before = [dict(r) for r in ledger_db.execute(
        "SELECT * FROM monthly_plans").fetchall()]

    assert _result(ledger_db)["available"] is True

    assert ledger_db.total_changes == before
    assert [dict(r) for r in ledger_db.execute(
        "SELECT * FROM monthly_plans").fetchall()] == plans_before


def test_coverage_language_names_the_real_evidence(ledger_db):
    seed_month(ledger_db, "2026-06")
    _contemporaneous_plan(ledger_db, "2026-06", savings=800)

    result = _result(ledger_db)

    assert "Complete statement coverage" in result["coverage_note"]
    assert "2026-06" in result["coverage_note"]
    assert result["planned_on"] == "2026-06-03"

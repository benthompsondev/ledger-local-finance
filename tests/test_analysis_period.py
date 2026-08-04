"""One analysis date, shared by every screen.

Each screen used to ask the database ``MAX(transaction_date)`` for itself.
One row dated 2035 was enough to move Home, Insights and Plan five hundred
weeks into the future, where almost nothing had happened, and take visible
spending from $416 to $42. These tests hold the anchor to the date the data
genuinely reaches, and hold the screens to agreeing on it.

The outlier is never deleted. A guard that quietly destroys evidence is worse
than the bug it fixes, so the row stays queryable and is reported.
"""
import calendar
from datetime import date, timedelta

import pytest

from tests.conftest import add_tx
from utils.analysis_period import (
    analysis_context, ignored_future_dates, supported_date_bounds,
    supported_latest_date,
)

END = date(2026, 7, 20)


def _seed(db, months=4):
    """Ordinary months of activity ending 2026-07-20."""
    start = (END.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    cursor = start
    while cursor <= END:
        last = calendar.monthrange(cursor.year, cursor.month)[1]
        if cursor.day == 5:
            add_tx(db, day=cursor.isoformat(), desc="INVENTED PAYROLL",
                   amount=2600, direction="credit", category="Payroll Income")
        if cursor.day <= last:
            add_tx(db, day=cursor.isoformat(),
                   desc=f"INVENTED GROCER {cursor.day}", amount=42.0,
                   direction="debit", category="Groceries")
        cursor += timedelta(days=1)
    db.commit()


def _stray_future_row(db, day="2035-01-01"):
    add_tx(db, day=day, desc="INVENTED LEGACY ROW", amount=17.0,
           direction="debit", category="Shopping")
    db.commit()


# ── the anchor itself ───────────────────────────────────────────────────

def test_the_anchor_is_where_the_data_reaches(ledger_db):
    _seed(ledger_db)
    assert supported_latest_date(conn=ledger_db) == END


def test_one_far_future_row_does_not_move_the_anchor(ledger_db):
    _seed(ledger_db)
    _stray_future_row(ledger_db)

    assert supported_latest_date(conn=ledger_db) == END
    raw = ledger_db.execute(
        "SELECT MAX(transaction_date) FROM transactions").fetchone()[0]
    assert raw.startswith("2035"), "the row itself must still be the raw maximum"


def test_the_ignored_row_is_still_stored_and_reported(ledger_db):
    _seed(ledger_db)
    _stray_future_row(ledger_db)

    ignored = ignored_future_dates(conn=ledger_db)
    assert ignored == [{"date": "2035-01-01", "transactions": 1}]
    # Findable, not quietly deleted.
    found = ledger_db.execute(
        "SELECT COUNT(*) FROM transactions WHERE transaction_date = ?",
        ("2035-01-01",)).fetchone()[0]
    assert found == 1
    assert "2035-01-01" in analysis_context(conn=ledger_db, today=END)["label"]


def test_a_real_cluster_of_newer_activity_does_move_the_anchor(ledger_db):
    """The guard must not freeze the app in the past when data genuinely
    continues. Three ordinary days is activity, not an outlier."""
    _seed(ledger_db)
    for offset in (1, 2, 3, 4):
        add_tx(ledger_db, day=(END + timedelta(days=offset)).isoformat(),
               desc="INVENTED GROCER LATER", amount=31.0,
               direction="debit", category="Groceries")
    ledger_db.commit()

    assert supported_latest_date(conn=ledger_db) == END + timedelta(days=4)


def test_three_rows_on_one_bad_date_do_not_support_each_other(ledger_db):
    _seed(ledger_db)
    for suffix in range(3):
        add_tx(ledger_db, day="2035-01-01",
               desc=f"INVENTED LEGACY ROW {suffix}", amount=17.0,
               direction="debit", category="Shopping")
    ledger_db.commit()

    assert supported_latest_date(
        conn=ledger_db, today=date(2035, 1, 1)
    ) == END


def test_future_dates_are_never_eligible_for_the_anchor(ledger_db):
    _seed(ledger_db)
    for offset in range(1, 5):
        add_tx(ledger_db, day=f"2035-01-0{offset}",
               desc="INVENTED FUTURE CLUSTER", amount=17.0,
               direction="debit", category="Shopping")
    ledger_db.commit()

    context = analysis_context(conn=ledger_db, today=END)
    assert context["data_through"] == END.isoformat()
    assert context["raw_latest"] == "2035-01-04"
    assert context["ignored_count"] == 4


def test_sparse_dates_do_not_hide_an_older_supported_cluster(ledger_db):
    _seed(ledger_db)
    first_sparse = date(2030, 1, 1)
    for index in range(401):
        add_tx(
            ledger_db,
            day=(first_sparse + timedelta(days=index * 40)).isoformat(),
            desc=f"INVENTED SPARSE ROW {index}",
            amount=17.0,
            direction="debit",
            category="Shopping",
        )
    ledger_db.commit()

    assert supported_latest_date(
        conn=ledger_db, today=date(2080, 1, 1)
    ) == END


def test_supported_bounds_ignore_one_far_past_row(ledger_db):
    _seed(ledger_db)
    add_tx(ledger_db, day="1900-01-01", desc="INVENTED OLD ROW",
           amount=17.0, direction="debit", category="Shopping")
    ledger_db.commit()

    first, last = supported_date_bounds(conn=ledger_db, today=END)
    assert first is not None and first.year == 2026
    assert last == END


def test_an_empty_database_degrades_without_claiming_anything(ledger_db):
    context = analysis_context(conn=ledger_db, today=END)

    assert context["has_data"] is False
    assert context["state"] == "empty"
    assert context["data_through"] == ""
    assert context["analysis_month"] == "2026-07"
    assert supported_latest_date(conn=ledger_db) is None


def test_a_single_transaction_does_not_crash_or_overclaim(ledger_db):
    add_tx(ledger_db, day="2026-07-11", desc="INVENTED SHOP", amount=20,
           direction="debit", category="Groceries")
    ledger_db.commit()

    context = analysis_context(conn=ledger_db, today=END)
    assert context["has_data"] is True
    assert context["data_through"] == "2026-07-11"


def test_stale_data_names_its_real_date_and_uses_the_calendar_month(ledger_db):
    _seed(ledger_db)
    much_later = date(2026, 11, 3)

    context = analysis_context(conn=ledger_db, today=much_later)
    assert context["state"] == "stale"
    assert context["data_through"] == "2026-07-20"
    assert context["month_source"] == "calendar"
    assert context["analysis_month"] == "2026-11"
    assert context["analysis_month_label"] == "November 2026"
    assert context["data_month_label"] == "July 2026"
    assert "2026-07-20" in context["label"]
    assert "this month" not in context["label"].lower()


def test_current_data_keeps_the_data_month(ledger_db):
    _seed(ledger_db)
    context = analysis_context(conn=ledger_db, today=END + timedelta(days=2))

    assert context["state"] == "current"
    assert context["month_source"] == "data"
    assert context["analysis_month"] == "2026-07"


# ── the screens agreeing ────────────────────────────────────────────────

def _engine_periods(tmp_path, monkeypatch, seed_extra=None):
    """Drive the real engine actions against a disposable directory."""
    import io
    import json

    from desktop.engine import ledger_engine

    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))

    def call(action, params=None):
        out = io.StringIO()
        body = {"action": action}
        if params:
            body["params"] = params
        code = ledger_engine.run(io.StringIO(json.dumps(body) + "\n"), out)
        payload = json.loads(out.getvalue())
        assert code == 0 and payload["ok"], payload.get("error")
        return payload["data"]

    call("home_summary")  # initializes the schema at the disposable path
    from utils import database
    conn = database.get_connection()
    _seed(conn)
    if seed_extra:
        before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        seed_extra(conn)
        # Prove the fixture actually created the condition under test. A
        # rejected insert here would make every assertion below pass while
        # exercising nothing.
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] > before
    from utils.ai_assist import build_payload
    from utils.insight_feed import insight_feed

    coach = build_payload(conn=conn, scope="totals", months=3)
    feed = insight_feed(conn=conn)
    conn.close()

    return {
        "home": call("home_summary", {"period_days": 30}),
        "dashboard": call("home_dashboard", {"period_days": 30}),
        "insights": call("insights_summary", {"period_days": 30}),
        "plan": call("plan_summary", {"as_of_date": END.isoformat()}),
        "coach": coach,
        "feed": feed,
    }


def test_every_screen_reports_the_same_data_context(tmp_path, monkeypatch):
    screens = _engine_periods(tmp_path, monkeypatch)

    assert screens["home"]["period_end"] == "2026-07-20"
    assert screens["insights"]["period_end"] == "2026-07-20"
    # The injected clock keeps this cross-screen integration test independent
    # of the day pytest happens to run.
    assert screens["plan"]["month"] == "2026-07"
    assert screens["coach"]["data_coverage"]["latest_transaction_date"] == END.isoformat()
    assert screens["coach"]["data_coverage"]["latest_data_month"] == "2026-07"
    assert screens["coach"]["months"][-1]["month"] == "2026-07"
    for name in ("home", "dashboard", "insights", "plan", "coach", "feed"):
        assert screens[name]["analysis"]["data_through"] == END.isoformat()
        assert screens[name]["analysis"]["data_month_label"] == "July 2026"


def test_a_stray_future_row_moves_no_screen(tmp_path, monkeypatch):
    """The whole point. Before the shared context this put Home, Insights and
    Plan in 2035 while the pattern detectors correctly stayed in 2026."""
    screens = _engine_periods(tmp_path, monkeypatch,
                              seed_extra=_stray_future_row)

    assert screens["home"]["period_end"] == "2026-07-20"
    assert screens["insights"]["period_end"] == "2026-07-20"
    assert screens["plan"]["month"] == "2026-07"
    assert screens["coach"]["data_coverage"]["latest_transaction_date"] == END.isoformat()
    assert screens["coach"]["data_coverage"]["latest_data_month"] == "2026-07"
    assert screens["coach"]["months"][-1]["month"] == "2026-07"
    for name in ("home", "dashboard", "insights", "plan", "coach", "feed"):
        assert screens[name]["analysis"]["data_through"] == END.isoformat()
        assert screens[name]["analysis"]["data_month_label"] == "July 2026"
    # And the period still contains the real spending rather than the one
    # stray row, which is what the corrupted window actually cost.
    assert screens["insights"]["cash_flow_summary"]["spending"] > 100


def test_plan_history_is_measured_to_the_supported_date(tmp_path, monkeypatch):
    """A 2035 row turned 111 days of statements into 3,198."""
    clean = _engine_periods(tmp_path, monkeypatch)["plan"]
    assert clean["readiness"]["history_days"] < 400

    import shutil
    second = tmp_path.parent / (tmp_path.name + "-stray")
    if second.exists():
        shutil.rmtree(second)
    second.mkdir()
    strayed = _engine_periods(second, monkeypatch,
                              seed_extra=_stray_future_row)["plan"]
    assert strayed["readiness"]["history_days"] == pytest.approx(
        clean["readiness"]["history_days"], abs=1)


def test_plan_history_ignores_one_far_past_row(tmp_path, monkeypatch):
    clean = _engine_periods(tmp_path, monkeypatch)["plan"]

    import shutil
    second = tmp_path.parent / (tmp_path.name + "-old")
    if second.exists():
        shutil.rmtree(second)
    second.mkdir()

    def seed_old(conn):
        add_tx(conn, day="1900-01-01", desc="INVENTED OLD ROW", amount=17.0,
               direction="debit", category="Shopping")
        conn.commit()

    old = _engine_periods(second, monkeypatch, seed_extra=seed_old)["plan"]
    assert old["readiness"]["history_days"] == clean["readiness"]["history_days"]

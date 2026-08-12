"""Month completeness needs one account's credible coverage, not a household pile.

All rows are invented. `statement_coverage` aggregated every account
together, so it read the earliest and latest imported day across the whole
household. A chequing statement covering the 1st to the 9th and a card
statement covering the 22nd to the 30th therefore produced a first day of
the 1st and a last day of the 30th, and June was declared complete with
three of its four weeks never imported by anything. Complete months are what
the typical-income baseline reads, so the mosaic did not stay a display
problem.

The other half of the requirement pulls the opposite way: a savings account
with one transaction all month is quiet, not missing, and must not be able
to make the month partial. So the rule asks that *some* account has credible
coverage, never that every account did.
"""
from __future__ import annotations

import io
import json

import pytest


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
    body = _engine("create_account", name=name, type=kind)
    assert body["ok"], body
    return next(
        int(a["id"]) for a in _engine("list_accounts")["data"]["accounts"]
        if a["name"] == name
    )


def _import(tmp_path, name: str, account_id: int, rows) -> None:
    path = tmp_path / name
    path.write_text(
        "Date,Description,Amount\n"
        + "".join(f"{d},{desc},{amount}\n" for d, desc, amount in rows),
        encoding="utf-8",
    )
    body = _engine("confirm_import", files=[
        {"path": str(path), "account_id": account_id},
    ])
    assert body["ok"], body


def _full_month(prefix: str) -> list[tuple[str, str, str]]:
    """A statement that genuinely covers 2026-06, day 1 to day 30."""
    rows = [("2026-06-01", f"{prefix} PAYROLL", "1500.00")]
    for day in (3, 6, 9, 13, 17, 21, 24, 27, 30):
        rows.append((f"2026-06-{day:02d}", f"{prefix} MERCHANT {day}", "-20.00"))
    return rows


def _coverage(month: str = "2026-06") -> dict:
    from utils.insights import statement_coverage

    return statement_coverage()["statement_coverage_by_month"].get(month, {})


# ── the mosaic ───────────────────────────────────────────────────────────

def test_two_half_covered_accounts_do_not_make_a_complete_month(
    ledger,
) -> None:
    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"), [
        ("2026-06-01", "INVENTED PAYROLL", "1500.00"),
        ("2026-06-03", "INVENTED GROCER", "-84.20"),
        ("2026-06-06", "INVENTED CAFE", "-6.75"),
        ("2026-06-09", "INVENTED FUEL", "-52.10"),
    ])
    _import(ledger, "card.csv", _account("Invented Card", "credit_card"), [
        ("2026-06-22", "INVENTED PHARMACY", "-31.40"),
        ("2026-06-25", "INVENTED RESTAURANT", "-64.00"),
        ("2026-06-28", "INVENTED STREAMING", "-16.99"),
        ("2026-06-30", "INVENTED HARDWARE", "-22.50"),
    ])

    june = _coverage()

    assert june["complete"] is False
    assert "no single imported statement covers" in june["reason"]
    # The combined view still spans the month; that was the whole trap.
    assert june["first_day"] == "2026-06-01"
    assert june["last_day"] == "2026-06-30"


def test_two_edge_rows_cannot_anchor_another_accounts_sparse_days(
    ledger,
) -> None:
    """The edge account must meet the activity threshold on its own."""
    _import(ledger, "edge.csv", _account("Invented Chequing", "chequing"), [
        ("2026-06-01", "INVENTED PAYROLL", "1500.00"),
        ("2026-06-30", "INVENTED RENT", "-900.00"),
    ])
    _import(ledger, "middle.csv", _account("Invented Card", "credit_card"), [
        (f"2026-06-{day:02d}", f"INVENTED MERCHANT {day}", "-20.00")
        for day in (12, 13, 14, 15, 16, 17, 18, 19)
    ])

    june = _coverage()

    assert june["distinct_days"] == 10
    assert june["first_day"] == "2026-06-01"
    assert june["last_day"] == "2026-06-30"
    assert june["complete"] is False
    assert "no single imported statement covers" in june["reason"]


def test_same_account_fragments_do_not_form_one_complete_statement(
    ledger,
) -> None:
    """Coverage cannot be assembled from several partial import batches."""
    account_id = _account("Invented Chequing", "chequing")
    _import(ledger, "start.csv", account_id, [
        ("2026-06-01", "INVENTED PAYROLL", "1500.00"),
    ])
    _import(ledger, "middle.csv", account_id, [
        ("2026-06-15", "INVENTED GROCER", "-84.20"),
    ])
    _import(ledger, "end.csv", account_id, [
        ("2026-06-30", "INVENTED RENT", "-900.00"),
    ])

    june = _coverage()

    assert june["first_day"] == "2026-06-01"
    assert june["last_day"] == "2026-06-30"
    assert june["distinct_days"] == 3
    assert june["complete"] is False
    assert "no single imported statement covers" in june["reason"]


def test_a_mosaic_month_is_not_a_typical_income_baseline(ledger) -> None:
    """Completeness is not cosmetic: it decides what income is read from."""
    from utils.analytics import complete_month_income
    from utils.insights import statement_coverage

    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"), [
        ("2026-06-01", "INVENTED PAYROLL", "1500.00"),
        ("2026-06-04", "INVENTED GROCER", "-84.20"),
        ("2026-06-08", "INVENTED CAFE", "-6.75"),
    ])
    _import(ledger, "card.csv", _account("Invented Card", "credit_card"), [
        ("2026-06-23", "INVENTED PHARMACY", "-31.40"),
        ("2026-06-27", "INVENTED RESTAURANT", "-64.00"),
        ("2026-06-30", "INVENTED HARDWARE", "-22.50"),
    ])

    assert "2026-06" not in statement_coverage()["complete_months"]
    assert [m for m, _total in complete_month_income()] == []


# ── the quiet account, which must not be punished ────────────────────────

def test_a_quiet_savings_account_does_not_make_the_month_partial(
    ledger,
) -> None:
    """One transfer all month is a quiet account, not a missing statement."""
    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"),
            _full_month("INVENTED"))
    _import(ledger, "savings.csv", _account("Invented Savings", "savings"), [
        ("2026-06-15", "INVENTED TRANSFER IN", "200.00"),
    ])

    june = _coverage()

    assert june["complete"] is True, june["reason"]


def test_a_silent_savings_account_with_no_rows_changes_nothing(
    ledger,
) -> None:
    """An account with no June activity at all cannot veto June."""
    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"),
            _full_month("INVENTED"))
    _account("Invented Savings", "savings")

    assert _coverage()["complete"] is True


def test_one_account_covering_the_month_is_still_complete(ledger) -> None:
    """The single-account case must behave exactly as it did before."""
    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"),
            _full_month("INVENTED"))

    june = _coverage()

    assert june["complete"] is True
    assert june["reason"] == "complete"


def test_a_single_account_that_stops_early_is_still_partial(ledger) -> None:
    _import(ledger, "chequing.csv", _account("Invented Chequing", "chequing"), [
        ("2026-06-01", "INVENTED PAYROLL", "1500.00"),
        ("2026-06-04", "INVENTED GROCER", "-84.20"),
        ("2026-06-08", "INVENTED CAFE", "-6.75"),
    ])

    june = _coverage()

    assert june["complete"] is False
    assert "before month end" in june["reason"]

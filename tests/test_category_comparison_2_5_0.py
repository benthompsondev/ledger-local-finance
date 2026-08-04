"""A month that was never imported is not a month in which nothing was spent.

All rows are invented. `category_pace_breakdown` compares the current month
against the previous one, and returns an empty previous map when the previous
month was not imported far enough to compare against. Every item then read
`prev.get(category, 0.0)`, so an absent month and a month of genuine zeros
became the same thing: previous $0.00 and a delta equal to the category's
whole current amount. The installed app rendered that as every category being
up by everything it had spent.

This is the installed-demo scenario: import one month, open Insights, and the
comparison is against a month that does not exist.
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


def _account(name: str = "Invented Chequing", kind: str = "chequing") -> int:
    assert _engine("create_account", name=name, type=kind)["ok"]
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
    assert _engine("confirm_import", files=[
        {"path": str(path), "account_id": account_id},
    ])["ok"]


def _month(month: str, prefix: str = "INVENTED") -> list[tuple[str, str, str]]:
    """A statement covering one whole month, with two spending categories."""
    rows = [(f"{month}-01", f"{prefix} EMPLOYER PAYROLL", "1500.00")]
    for day in (3, 6, 9, 13, 17, 21, 24, 27, 28):
        rows.append((f"{month}-{day:02d}", f"{prefix} GROCER", "-20.00"))
    for day in (5, 12, 19, 26):
        rows.append((f"{month}-{day:02d}", f"{prefix} RESTAURANT", "-30.00"))
    return rows


def _pace(tmp_path, months: list[str]) -> dict:
    from utils.insights import category_pace_breakdown

    account = _account()
    for month in months:
        _import(tmp_path, f"{month}.csv", account, _month(month))
    return category_pace_breakdown()


# ── the invented baseline ────────────────────────────────────────────────

def test_a_single_imported_month_reports_no_comparison(ledger) -> None:
    pace = _pace(ledger, ["2026-06"])

    assert pace["comparison_available"] is False
    assert pace["items"], "the breakdown should still show the month itself"


def test_an_uncomparable_month_carries_no_previous_or_delta(ledger) -> None:
    """The specific values that used to be invented."""
    pace = _pace(ledger, ["2026-06"])

    for item in pace["items"]:
        assert item["previous"] is None, item
        assert item["delta"] is None, item
        # What is real is still reported.
        assert item["amount"] > 0
        assert item["share"] > 0


def test_an_uncomparable_month_reports_no_movers(ledger) -> None:
    """Biggest movers is a statement about change, so it needs a before."""
    account = _account()
    _import(ledger, "june.csv", account, _month("2026-06"))

    body = _engine("insights_summary")
    assert body["ok"], body
    movers = body["data"]["category_movers"]

    assert movers["increase"] is None
    assert movers["decrease"] is None


def test_the_explainer_does_not_claim_a_comparison(ledger) -> None:
    from desktop.engine import ledger_engine  # noqa: F401  (engine import)

    pace = _pace(ledger, ["2026-06"])

    assert pace["comparison_available"] is False
    # The note the screen prints above the bars must not promise a baseline
    # it does not have; the wording itself lives in charts.tsx.
    assert pace["previous_month"] == "2026-05"


# ── the comparison that must keep working ────────────────────────────────

def test_two_imported_months_still_compare(ledger) -> None:
    pace = _pace(ledger, ["2026-05", "2026-06"])

    assert pace["comparison_available"] is True
    assert any(item["previous"] is not None for item in pace["items"])
    assert any(item["delta"] is not None for item in pace["items"])


def test_a_category_absent_last_month_still_compares_against_zero(
    ledger,
) -> None:
    """Zero and null are different answers and must stay different."""
    account = _account()
    _import(ledger, "may.csv", account, _month("2026-05"))
    june = _month("2026-06") + [("2026-06-14", "INVENTED HARDWARE", "-45.00")]
    _import(ledger, "june.csv", account, june)

    from utils.insights import category_pace_breakdown

    pace = category_pace_breakdown()
    hardware = next(
        (i for i in pace["items"] if "Hardware" in i["category"]
         or i["amount"] == 45.0), None,
    )

    assert pace["comparison_available"] is True
    if hardware is not None:
        assert hardware["previous"] is not None, (
            "a category with no spending last month compares against zero, "
            "not against nothing"
        )

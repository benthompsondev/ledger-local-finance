"""Which file a row came from decides whether an identical row is a duplicate.

Every fixture is invented. The three behaviours that have to hold together,
and which pull against each other:

  * two genuine identical purchases on separate statements both survive;
  * re-importing the same file changes nothing;
  * an overlapping re-export adds only what is new, never a second copy of
    activity already recorded.

The middle and last of these are what stopped 2.4.0 from simply numbering
duplicates against stored rows. See utils/import_provenance.py.
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
def account(monkeypatch, tmp_path):
    import utils.database as db

    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    body = _engine("create_account", name="Invented Chequing", type="chequing")
    assert body["ok"], body
    accounts = _engine("list_accounts")["data"]["accounts"]
    return int(accounts[0]["id"])


def _write(tmp_path, name: str, rows: list[tuple[str, str, str]]) -> str:
    body = "Date,Description,Amount\n" + "".join(
        f"{d},{desc},{amount}\n" for d, desc, amount in rows
    )
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _import(path: str, account_id: int, import_mode: str = "") -> dict:
    spec = {"path": path, "account_id": account_id}
    if import_mode:
        spec["import_mode"] = import_mode
    body = _engine("confirm_import", files=[
        spec,
    ])
    assert body["ok"], body
    return body["data"]["totals"]


def _rows() -> list[tuple]:
    import utils.database as db

    conn = db.get_connection()
    try:
        return [
            (r["transaction_date"], r["raw_description"], round(r["amount"], 2))
            for r in conn.execute(
                "SELECT transaction_date, raw_description, amount "
                "FROM transactions ORDER BY transaction_date, raw_description"
            ).fetchall()
        ]
    finally:
        conn.close()


def _count(needle: str) -> int:
    return sum(1 for _d, desc, _a in _rows() if needle in desc)


# ── the row that used to disappear ───────────────────────────────────────

def test_identical_purchases_on_separate_statements_both_survive(
    tmp_path, account,
) -> None:
    first = _write(tmp_path, "one.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-02", "INVENTED UNIQUE A", "-11.11"),
    ])
    second = _write(tmp_path, "two.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-02", "INVENTED UNIQUE B", "-22.22"),
    ])

    _import(first, account)
    _import(second, account, "new")

    assert _count("TRANSIT FARE") == 2
    assert len(_rows()) == 4


def test_three_statements_each_carrying_one_identical_fare(
    tmp_path, account,
) -> None:
    """The numbering has to keep working past the second file."""
    for index, unique in enumerate(["A", "B", "C"]):
        path = _write(tmp_path, f"week-{index}.csv", [
            ("2026-05-04", "INVENTED TRANSIT FARE", "-3.35"),
            ("2026-05-04", f"INVENTED UNIQUE {unique}", f"-{index + 1}0.00"),
        ])
        _import(path, account, "new" if index else "")

    assert _count("TRANSIT FARE") == 3


# ── the protections that must not be lost ────────────────────────────────

def test_reimporting_the_same_file_changes_nothing(tmp_path, account) -> None:
    path = _write(tmp_path, "statement.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-03", "INVENTED GROCER", "-84.20"),
    ])

    first = _import(path, account)
    before = _rows()
    second = _import(path, account)

    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["skipped"] == 3
    assert _rows() == before


def test_an_overlapping_reexport_adds_only_what_is_new(
    tmp_path, account,
) -> None:
    """The case that makes numbering against stored rows unsafe.

    The second export covers the first fortnight again and carries the rest
    of the month as well. Every shared row must be recognised, including the
    two identical fares.
    """
    first_half = [
        ("2026-06-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-06-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-06-05", "INVENTED GROCER", "-84.20"),
        ("2026-06-11", "INVENTED CAFE", "-6.75"),
    ]
    whole_month = first_half + [
        ("2026-06-18", "INVENTED PHARMACY", "-31.40"),
        ("2026-06-25", "INVENTED EMPLOYER PAYROLL", "1500.00"),
    ]
    _import(_write(tmp_path, "june-first-half.csv", first_half), account)
    _import(
        _write(tmp_path, "june-full.csv", whole_month),
        account,
        "reexport",
    )

    assert _count("TRANSIT FARE") == 2, "the shared fares were duplicated"
    assert _count("GROCER") == 1
    assert _count("PHARMACY") == 1
    assert len(_rows()) == 6


def test_a_reexport_of_exactly_the_same_period_adds_nothing(
    tmp_path, account,
) -> None:
    """A second download of the same statement, saved under another name."""
    rows = [
        ("2026-07-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-07-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-07-09", "INVENTED GROCER", "-84.20"),
    ]
    _import(_write(tmp_path, "july.csv", rows), account)
    _import(_write(tmp_path, "july (1).csv", list(rows)), account)

    assert len(_rows()) == 3


def test_a_one_row_exact_file_reimport_is_still_automatic(
    tmp_path, account,
) -> None:
    path = _write(tmp_path, "one-row.csv", [
        ("2026-07-02", "INVENTED TRANSIT FARE", "-3.35"),
    ])

    assert _import(path, account)["inserted"] == 1
    again = _import(path, account)

    assert again["inserted"] == 0
    assert again["skipped"] == 1


def test_partial_overlap_requires_a_user_decision(tmp_path, account) -> None:
    first = _write(tmp_path, "first.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-03", "INVENTED GROCER", "-84.20"),
        ("2026-04-04", "INVENTED CAFE", "-6.75"),
    ])
    revised = _write(tmp_path, "revised.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-04", "INVENTED CAFE", "-6.75"),
        ("2026-04-08", "INVENTED PHARMACY", "-31.40"),
    ])
    _import(first, account)

    preview = _engine("preview_import", files=[{
        "path": revised, "account_id": account,
    }])
    file = preview["data"]["files"][0]
    assert file["provenance_status"] == "ambiguous"
    assert file["dedup_choice_required"] is True

    refused = _engine("confirm_import", files=[{
        "path": revised, "account_id": account,
    }])
    assert refused["ok"] is False
    assert "updated export or separate activity" in refused["error"]
    assert len(_rows()) == 3


def test_full_row_containment_still_requires_a_user_decision(
    tmp_path, account,
) -> None:
    """Two shared purchases are evidence of overlap, not proof of identity.

    A later statement can contain the same two real purchases plus unrelated
    activity. Silently treating containment as a re-export would discard those
    purchases, so only an exact file hash may bypass the choice.
    """
    first = _write(tmp_path, "first.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-03", "INVENTED GROCER", "-84.20"),
    ])
    second = _write(tmp_path, "second.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-03", "INVENTED GROCER", "-84.20"),
        ("2026-04-10", "INVENTED BOOKSTORE", "-18.25"),
    ])
    _import(first, account)

    preview = _engine("preview_import", files=[{
        "path": second, "account_id": account,
    }])["data"]["files"][0]

    assert preview["provenance_status"] == "ambiguous"
    assert preview["dedup_choice_required"] is True

    saved = _import(second, account, "new")
    assert saved["inserted"] == 3
    assert _count("TRANSIT FARE") == 2
    assert _count("GROCER") == 2


def test_revised_export_skips_shared_rows_after_confirmation(
    tmp_path, account,
) -> None:
    first = _write(tmp_path, "first.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-03", "INVENTED GROCER", "-84.20"),
        ("2026-04-04", "INVENTED CAFE", "-6.75"),
    ])
    revised = _write(tmp_path, "revised.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-04", "INVENTED CAFE", "-6.75"),
        ("2026-04-08", "INVENTED PHARMACY", "-31.40"),
    ])
    _import(first, account)

    preview = _engine("preview_import", files=[{
        "path": revised, "account_id": account, "import_mode": "reexport",
    }])["data"]["files"][0]
    saved = _import(revised, account, "reexport")

    assert preview["new_transaction_count"] == saved["inserted"] == 1
    assert preview["duplicate_count"] == saved["skipped"] == 2
    assert _count("TRANSIT FARE") == 1
    assert _count("CAFE") == 1
    assert _count("PHARMACY") == 1


def test_separate_activity_preview_and_confirm_agree(
    tmp_path, account,
) -> None:
    first = _write(tmp_path, "one.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-02", "INVENTED UNIQUE A", "-11.11"),
    ])
    second = _write(tmp_path, "two.csv", [
        ("2026-04-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-02", "INVENTED UNIQUE B", "-22.22"),
    ])
    _import(first, account)

    preview = _engine("preview_import", files=[{
        "path": second, "account_id": account, "import_mode": "new",
    }])["data"]["files"][0]
    saved = _import(second, account, "new")

    assert preview["new_transaction_count"] == saved["inserted"] == 2
    assert preview["duplicate_count"] == saved["skipped"] == 0


def test_multi_file_preview_uses_selected_file_order_without_saving(
    tmp_path, account,
) -> None:
    first_rows = [
        ("2026-06-02", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-06-05", "INVENTED GROCER", "-84.20"),
        ("2026-06-11", "INVENTED CAFE", "-6.75"),
    ]
    whole_rows = first_rows + [
        ("2026-06-18", "INVENTED PHARMACY", "-31.40"),
        ("2026-06-25", "INVENTED EMPLOYER PAYROLL", "1500.00"),
    ]
    first = _write(tmp_path, "june-first.csv", first_rows)
    whole = _write(tmp_path, "june-whole.csv", whole_rows)

    preview = _engine("preview_import", files=[
        {"path": first, "account_id": account},
        {"path": whole, "account_id": account, "import_mode": "reexport"},
    ])["data"]["files"]

    assert (preview[0]["new_transaction_count"], preview[0]["duplicate_count"]) == (3, 0)
    assert (preview[1]["new_transaction_count"], preview[1]["duplicate_count"]) == (2, 3)
    assert _rows() == [], "preview leaked simulated transactions"

    first_saved = _import(first, account)
    whole_saved = _import(whole, account, "reexport")
    assert (first_saved["inserted"], first_saved["skipped"]) == (3, 0)
    assert (whole_saved["inserted"], whole_saved["skipped"]) == (2, 3)


def test_non_overlapping_statements_both_import_in_full(
    tmp_path, account,
) -> None:
    march = _write(tmp_path, "march.csv", [
        ("2026-03-04", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-03-20", "INVENTED GROCER", "-84.20"),
    ])
    april = _write(tmp_path, "april.csv", [
        ("2026-04-04", "INVENTED TRANSIT FARE", "-3.35"),
        ("2026-04-20", "INVENTED GROCER", "-84.20"),
    ])

    _import(march, account)
    _import(april, account)

    assert len(_rows()) == 4
    assert _count("TRANSIT FARE") == 2


def test_identical_activity_on_two_accounts_is_not_a_duplicate(
    tmp_path, account,
) -> None:
    """Provenance must not undo the account-aware fingerprint."""
    body = _engine("create_account", name="Invented Card", type="credit_card")
    assert body["ok"], body
    other = next(
        int(a["id"]) for a in _engine("list_accounts")["data"]["accounts"]
        if a["name"] == "Invented Card"
    )
    # The two files must differ in content: importing byte-identical files
    # into two accounts is refused earlier, by the file-hash guard, which is
    # a different protection and already covered.
    _import(_write(tmp_path, "chequing.csv", [
        ("2026-04-02", "INVENTED STREAMING", "-16.99"),
        ("2026-04-03", "INVENTED PAYROLL", "1500.00"),
    ]), account)
    _import(_write(tmp_path, "card.csv", [
        ("2026-04-02", "INVENTED STREAMING", "-16.99"),
        ("2026-04-04", "INVENTED FUEL", "-52.10"),
    ]), other)

    assert _count("STREAMING") == 2

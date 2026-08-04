"""The real two-file preview and confirm workflow, through the engine.

All rows are invented.

The pieces of this were covered separately — the provenance classifier had
unit tests, and the persistence layer had its own — but the workflow a person
actually performs was not: select two files, one of which partly overlaps the
other, see what the preview says, answer the question it asks, and confirm.
That is where a wrong answer costs real money, in either direction. Discarding
a genuinely repeated purchase understates spending; keeping a re-exported row
twice overstates it.

**Separate files are separate atomic batches.** `confirm_import_action`
commits each file as it finishes, so a file that fails leaves the files before
it imported and itself entirely absent. There is no all-or-nothing transaction
across a selection, and there deliberately is not: refusing four good
statements because the fifth is unreadable helps nobody.
"""
from __future__ import annotations

import io
import json

import pytest

from desktop.engine import ledger_engine

SHARED_ROW = ("2026-05-08", "INVENTED HARDWARE STORE", "-40.00")
NEW_ROW = ("2026-05-09", "INVENTED BOOKSHOP", "-25.00")
REPEATED = ("2026-05-08", "INVENTED TRANSIT FARE", "-3.35")


def _request(action: str, **params) -> tuple[int, dict]:
    out = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps({"action": action, "params": params}) + "\n"),
        out,
    )
    return code, json.loads(out.getvalue())


def _ok(action: str, **params) -> dict:
    code, response = _request(action, **params)
    assert code == 0, response
    return response["data"]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    ledger_engine._bind_database()
    return tmp_path


def _account(name: str, kind: str = "chequing") -> int:
    _ok("create_account", name=name, type=kind)
    return next(
        account["id"] for account in _ok("list_accounts")["accounts"]
        if account["name"] == name
    )


def _csv(directory, name: str, rows) -> str:
    path = directory / name
    path.write_text(
        "Date,Description,Amount\n"
        + "".join(",".join(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return str(path)


def _descriptions(account_id: int | None = None) -> list[str]:
    rows = _ok("list_transactions", limit=1000)["transactions"]
    return [
        str(row.get("description") or "").upper() for row in rows
        if account_id is None or row.get("account_ref") == account_id
    ]


@pytest.fixture
def two_files(engine):
    """File A imports cleanly. File B shares one of its rows and adds one."""
    account = _account("Invented Chequing")
    file_a = _csv(engine, "statement-a.csv", [
        ("2026-05-01", "INVENTED LANDLORD RENT", "-1800.00"),
        ("2026-05-04", "INVENTED GROCER", "-64.00"),
        SHARED_ROW,
    ])
    file_b = _csv(engine, "statement-b.csv", [SHARED_ROW, NEW_ROW])
    return engine, account, file_a, file_b


# ── file A on its own ────────────────────────────────────────────────────

def test_the_first_file_imports_normally(two_files):
    _, account, file_a, _ = two_files

    result = _ok("confirm_import",
                 files=[{"path": file_a, "account_id": account}])

    assert result["totals"]["inserted"] == 3
    assert result["totals"]["skipped"] == 0
    assert result["results"][0]["provenance"] == "new"
    assert "INVENTED HARDWARE STORE" in _descriptions()


# ── file B asks the question ─────────────────────────────────────────────

def test_a_partial_overlap_is_reported_as_needing_a_choice(two_files):
    _, account, file_a, file_b = two_files
    _ok("confirm_import", files=[{"path": file_a, "account_id": account}])

    preview = _ok("preview_import",
                  files=[{"path": file_b, "account_id": account}])

    body = json.dumps(preview).lower()
    assert "ambiguous" in body, (
        f"a partially overlapping file did not ask anything: {preview}"
    )


def test_confirming_without_answering_refuses_and_imports_nothing(two_files):
    _, account, file_a, file_b = two_files
    _ok("confirm_import", files=[{"path": file_a, "account_id": account}])
    before = _descriptions()

    code, response = _request(
        "confirm_import", files=[{"path": file_b, "account_id": account}],
    )

    assert code != 0 or response.get("error"), (
        "an unresolved overlap was imported on a guess"
    )
    message = json.dumps(response).lower()
    assert "some, but not all" in message
    # Nothing moved, in either direction.
    assert _descriptions() == before
    assert "INVENTED BOOKSHOP" not in _descriptions()


def test_choosing_re_export_skips_the_shared_row_and_takes_the_new_one(
    two_files,
):
    _, account, file_a, file_b = two_files
    _ok("confirm_import", files=[{"path": file_a, "account_id": account}])

    result = _ok("confirm_import", files=[{
        "path": file_b, "account_id": account, "import_mode": "reexport",
    }])

    assert result["totals"]["inserted"] == 1, "the shared row was taken twice"
    assert result["totals"]["skipped"] == 1
    descriptions = _descriptions()
    assert descriptions.count("INVENTED HARDWARE STORE") == 1
    assert descriptions.count("INVENTED BOOKSHOP") == 1


def test_choosing_new_activity_keeps_a_genuinely_repeated_purchase(engine):
    """Two identical fares on one day are two fares, not one recorded twice."""
    account = _account("Invented Transit")
    first = _csv(engine, "fares-1.csv", [REPEATED, ("2026-05-08",
                                                    "INVENTED KIOSK", "-2.00")])
    second = _csv(engine, "fares-2.csv", [REPEATED, ("2026-05-08",
                                                     "INVENTED SNACK", "-4.00")])
    _ok("confirm_import", files=[{"path": first, "account_id": account}])

    result = _ok("confirm_import", files=[{
        "path": second, "account_id": account, "import_mode": "new",
    }])

    assert result["totals"]["inserted"] == 2
    assert _descriptions().count("INVENTED TRANSIT FARE") == 2, (
        "a real second fare was discarded as a duplicate"
    )


def test_the_two_answers_reach_different_outcomes(engine):
    """If both choices did the same thing the question would be theatre.

    Two accounts rather than one reset, because the same overlap has to be
    posed twice and the engine refuses to import one file's bytes into a
    second account. Each account gets its own pair, differing only in a
    marker row so the file hashes differ the way two real exports would.
    """
    inserted = {}
    for mode in ("reexport", "new"):
        account = _account(f"Invented Chequing {mode}")
        file_a = _csv(engine, f"a-{mode}.csv", [
            ("2026-05-01", f"INVENTED MARKER {mode}", "-1.00"),
            ("2026-05-04", "INVENTED GROCER", "-64.00"),
            SHARED_ROW,
        ])
        file_b = _csv(engine, f"b-{mode}.csv", [
            ("2026-05-02", f"INVENTED MARKER B {mode}", "-2.00"),
            SHARED_ROW, NEW_ROW,
        ])
        _ok("confirm_import", files=[{"path": file_a, "account_id": account}])
        inserted[mode] = _ok("confirm_import", files=[{
            "path": file_b, "account_id": account, "import_mode": mode,
        }])["totals"]["inserted"]

    assert inserted["new"] > inserted["reexport"], (
        f"both answers inserted the same rows: {inserted}"
    )


# ── accounts stay separate ───────────────────────────────────────────────

def test_identical_rows_on_different_accounts_both_survive(engine):
    """The same subscription charged to two accounts is two charges.

    The statements carry a distinguishing row each, as two real exports from
    two real accounts would. Byte-identical files are a different case and
    are refused outright, deliberately: importing one account's statement
    into another would double-count every row in it.
    """
    chequing = _account("Invented Chequing")
    card = _account("Invented Card", "credit_card")
    shared = ("2026-05-12", "INVENTED STREAMING", "-16.99")

    _ok("confirm_import", files=[{
        "path": _csv(engine, "chq.csv",
                     [shared, ("2026-05-13", "INVENTED PAYCHEQUE", "1200.00")]),
        "account_id": chequing,
    }])
    _ok("confirm_import", files=[{
        "path": _csv(engine, "card.csv",
                     [shared, ("2026-05-14", "INVENTED FUEL", "-52.00")]),
        "account_id": card,
    }])

    assert _descriptions().count("INVENTED STREAMING") == 2, (
        "one account's subscription charge cancelled out another's"
    )


def test_the_same_file_cannot_be_imported_into_a_second_account(engine):
    """The other half of that rule, stated so the fixture above is not read
    as the whole of it."""
    chequing = _account("Invented Chequing")
    card = _account("Invented Card", "credit_card")
    same = _csv(engine, "same.csv", [
        ("2026-05-12", "INVENTED STREAMING", "-16.99"),
        ("2026-05-13", "INVENTED OTHER", "-9.00"),
    ])
    _ok("confirm_import", files=[{"path": same, "account_id": chequing}])

    code, response = _request(
        "confirm_import", files=[{"path": same, "account_id": card}],
    )

    assert code != 0 or response.get("error")
    assert "double-count" in json.dumps(response)
    assert _descriptions().count("INVENTED STREAMING") == 1


def test_an_overlap_on_one_account_does_not_question_another(engine):
    """Provenance is per account. A card statement is not evidence about
    the chequing account."""
    chequing = _account("Invented Chequing")
    card = _account("Invented Card", "credit_card")
    _ok("confirm_import", files=[{
        "path": _csv(engine, "one.csv",
                     [SHARED_ROW, ("2026-05-02", "INVENTED OTHER", "-9.00")]),
        "account_id": chequing,
    }])

    result = _ok("confirm_import", files=[{
        "path": _csv(engine, "two.csv",
                     [SHARED_ROW, ("2026-05-03", "INVENTED ANOTHER", "-7.00")]),
        "account_id": card,
    }])

    assert result["results"][0]["provenance"] == "new", (
        "an unrelated account's history was treated as evidence"
    )
    assert result["totals"]["inserted"] == 2


# ── one file failing does not damage another ─────────────────────────────

def test_a_failed_second_file_leaves_no_partial_batch_of_its_own(two_files):
    """File B is unreadable. Nothing of B may be stored, and A stays."""
    directory, account, file_a, _ = two_files
    broken = directory / "unreadable.csv"
    broken.write_text("Col1|Col2|Col3\nfoo|bar|baz\nqux|quux|corge\n",
                      encoding="utf-8")

    code, response = _request("confirm_import", files=[
        {"path": file_a, "account_id": account},
        {"path": str(broken), "account_id": account},
    ])

    assert code != 0 or response.get("error"), "an unreadable file imported"
    descriptions = _descriptions()
    # File A committed as its own batch and survived the later failure.
    assert "INVENTED LANDLORD RENT" in descriptions
    assert descriptions.count("INVENTED HARDWARE STORE") == 1
    # Nothing from the broken file, whole or partial.
    for junk in ("FOO", "QUX", "BAR", "CORGE"):
        assert junk not in descriptions


def test_a_failed_file_writes_no_half_batch_of_its_own_rows(engine):
    """The failure is mid-file: good rows first, then a row that stops it."""
    account = _account("Invented Chequing")
    good = _csv(engine, "good.csv", [
        ("2026-05-01", "INVENTED FIRST FILE ROW", "-10.00"),
    ])
    # Ambiguous punctuation: the parser cannot tell 1.234 from 1,234, and
    # refuses the file rather than importing an amount off by a thousand.
    bad = _csv(engine, "bad.csv", [
        ("2026-06-01", "INVENTED SECOND FILE ROW", "-1.234"),
        ("2026-06-02", "INVENTED SECOND FILE OTHER", "-2.345"),
    ])

    code, response = _request("confirm_import", files=[
        {"path": good, "account_id": account},
        {"path": bad, "account_id": account},
    ])

    assert code != 0 or response.get("error")
    descriptions = _descriptions()
    assert "INVENTED FIRST FILE ROW" in descriptions
    assert not any("SECOND FILE" in row for row in descriptions), (
        "a refused file left rows behind"
    )


def test_each_file_becomes_its_own_batch(engine):
    """The documented contract: one atomic batch per file, not per selection."""
    account = _account("Invented Chequing")
    first = _csv(engine, "one.csv", [("2026-05-01", "INVENTED ONE", "-10.00")])
    second = _csv(engine, "two.csv", [("2026-06-01", "INVENTED TWO", "-20.00")])

    result = _ok("confirm_import", files=[
        {"path": first, "account_id": account},
        {"path": second, "account_id": account},
    ])

    batches = [row["batch_id"] for row in result["results"]]
    assert len(result["results"]) == 2
    assert len(set(batches)) == 2, "two files shared one batch"
    assert all(batch for batch in batches)

"""Giving the data back.

A local-first app that will not hand the ledger over is only a different kind
of lock-in, so the export has to be complete and readable by something other
than Northstar. These tests read the file back with the standard csv module,
which is the whole point of the feature.
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from desktop.engine import ledger_engine


def _request(payload: dict) -> tuple[int, dict]:
    output = io.StringIO()
    code = ledger_engine.run(io.StringIO(json.dumps(payload) + "\n"), output)
    return code, json.loads(output.getvalue())


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed(engine_env):
    """Seed the same database the engine will export from.

    Calling ``init_db`` directly would build the schema against the module
    default path, not the disposable directory: the engine repoints
    ``database.DB_PATH`` when it handles a request. So make a request first,
    then write into whatever it prepared.
    """
    from utils import database
    from utils.database import insert_transaction

    _request({"action": "home_summary"})
    assert database.DB_PATH.parent == engine_env, (
        "the engine should be pointed at the disposable directory")
    conn = database.get_connection()
    try:
        for day, desc, amount, direction, category in [
            ("2026-05-02", "INVENTED GROCER", -84.20, "debit", "Groceries"),
            ("2026-05-05", "INVENTED EMPLOYER PAYROLL", 2400.00, "credit",
             "Payroll Income"),
            ("2026-05-11", "INVENTED CAFE, WITH A COMMA", -6.75, "debit",
             "Food & Convenience"),
        ]:
            ok, reason = insert_transaction({
                "transaction_date": day,
                "raw_description": desc,
                "merchant": desc,
                "amount": amount,
                "direction": direction,
                "category": category,
                "transaction_type": (
                    "income" if direction == "credit" else "spending"),
                "account_type": "chequing",
            }, conn)
            assert ok, reason
        conn.commit()
    finally:
        conn.close()


def test_every_row_comes_out_and_reads_back(engine_env):
    _seed(engine_env)
    destination = engine_env / "out.csv"

    code, payload = _request({
        "action": "export_transactions",
        "params": {"path": str(destination)},
    })

    assert code == 0, payload
    assert payload["ok"] is True
    assert payload["data"]["rows"] == 3
    assert destination.is_file()

    with open(destination, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert {"transaction_date", "amount", "category"} <= set(rows[0])
    # Oldest first, and a description containing a comma survives the trip.
    assert rows[0]["transaction_date"] == "2026-05-02"
    assert "INVENTED CAFE, WITH A COMMA" in {r["raw_description"] for r in rows}
    assert sum(float(r["amount"]) for r in rows) == pytest.approx(2309.05, abs=0.01)


def test_an_empty_ledger_still_writes_a_usable_file(engine_env):
    destination = engine_env / "empty.csv"

    code, payload = _request({
        "action": "export_transactions",
        "params": {"path": str(destination)},
    })

    assert code == 0
    assert payload["data"]["rows"] == 0
    with open(destination, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1, "the header should still be there"
    assert "transaction_date" in rows[0]


def test_no_destination_is_refused(engine_env):
    code, payload = _request({
        "action": "export_transactions", "params": {"path": "  "},
    })

    assert code == 1
    assert payload["ok"] is False
    assert "Choose where to save" in payload["error"]

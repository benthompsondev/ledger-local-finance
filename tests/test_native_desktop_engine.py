from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from desktop.engine import ledger_engine
from scripts.make_share_zip import is_excluded


def _request(payload: dict) -> tuple[int, dict]:
    """Run one one-shot engine request exactly like the Tauri shell does."""
    output = io.StringIO()
    code = ledger_engine.run(
        io.StringIO(json.dumps(payload) + "\n"), output
    )
    return code, json.loads(output.getvalue())


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_engine_requires_desktop_data_directory(monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_DATA_DIR", raising=False)

    with pytest.raises(RuntimeError, match="LEDGER_DATA_DIR"):
        ledger_engine._data_root()


def test_missing_data_directory_still_returns_json(monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_DATA_DIR", raising=False)
    output = io.StringIO()

    code = ledger_engine.run(io.StringIO('{"action":"home_summary"}\n'), output)

    payload = json.loads(output.getvalue())
    assert code == 1
    assert payload["ok"] is False
    assert "LEDGER_DATA_DIR" in payload["error"]


def test_home_request_uses_existing_finance_packet(engine_env) -> None:
    code, payload = _request({"action": "home_summary"})

    assert code == 0
    assert payload["ok"] is True
    assert payload["engine"] == "ledger-python"
    assert payload["data"]["freshness"]["state"] == "no_data"
    assert payload["data"]["verdict"]["headline"]
    assert (engine_env / "finance.db").is_file()
    assert (engine_env / "logs" / "native-engine.log").is_file()


def test_ai_coaching_summary_is_local_and_available_while_ai_is_off(engine_env) -> None:
    code, payload = _request({"action": "ai_coaching_summary"})

    assert code == 0
    assert payload["ok"] is True
    assert payload["data"]["profile"]["focus"] == "Save more consistently"
    assert payload["data"]["snapshot"]["comparison_available"] is False
    _, settings = _request({"action": "ai_settings"})
    assert settings["data"]["ai"]["enabled"] is False
    assert settings["data"]["ai"]["api_key_set"] is False


def test_balances_stay_unconfigured_until_one_is_entered(engine_env) -> None:
    """A balance is only ever an explicit snapshot, never inferred.

    In 2.6.0 `add_account_balance` answers with the planning payload the
    Settings section reads — accounts and their latest snapshot — instead of
    rebuilding the whole Insights view it no longer belongs to. The derived
    net-worth figure it used to return is not shown anywhere now, so this
    checks the same behaviour where that figure still lives.
    """
    _, account = _request({
        "action": "create_account",
        "params": {"name": "Test Card", "type": "credit_card"},
    })
    code, insights = _request({"action": "insights_summary"})
    assert code == 0
    assert insights["data"]["net_worth"]["status"] == "not_configured"
    assert insights["data"]["net_worth"]["calculated"] is False

    code, saved = _request({
        "action": "add_account_balance",
        "params": {
            "account_ref": account["data"]["created_id"],
            "balance": 425.50, "as_of_date": "2026-07-18",
        },
    })
    assert code == 0
    # The planning payload: the snapshot, and nothing that totals it up.
    assert saved["data"]["balances"][0]["balance"] == pytest.approx(425.50)
    assert "net_worth" not in saved["data"]

    _, after = _request({"action": "insights_summary"})
    assert after["data"]["net_worth"]["status"] == "configured"
    assert after["data"]["net_worth"]["net_worth"] == pytest.approx(-425.50)


def test_plan_rejects_outflow_above_income(engine_env) -> None:
    code, payload = _request({
        "action": "save_plan",
        "params": {
            "month": "2026-07", "mode": "normal", "income_target": 3000,
            "fixed_obligations": 1800, "flexible_allowance": 800,
            "savings_target": 400, "safety_buffer": 100,
        },
    })
    assert code == 1
    assert "cannot exceed expected income" in payload["error"]


def test_one_recurring_confirmation_updates_matching_history(engine_env) -> None:
    csv_path = engine_env / "recurring-obligation.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-01-05,OTHER,HOME LOAN SERVICER,Withdrawal,-900.00\n"
        "2026-02-05,OTHER,HOME LOAN SERVICER,Withdrawal,-900.00\n"
        "2026-03-05,OTHER,HOME LOAN SERVICER,Withdrawal,-900.00\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Chequing", "type": "chequing"},
    })
    code, imported = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": created["data"]["created_id"],
        }]},
    })
    assert code == 0 and imported["data"]["totals"]["flagged"] == 1
    _, review = _request({
        "action": "list_transactions", "params": {"flagged_only": True},
    })
    assert len(review["data"]["transactions"]) == 1
    tx_id = review["data"]["transactions"][0]["id"]
    code, _ = _request({
        "action": "correct_transaction",
        "params": {"id": tx_id, "category": "Housing / Mortgage", "note": "",
                   "apply_to_matching": True, "remember_rule": True},
    })
    assert code == 0
    _, all_rows = _request({"action": "list_transactions", "params": {}})
    matching = [r for r in all_rows["data"]["transactions"]
                if r["description"] == "HOME LOAN SERVICER"]
    assert len(matching) == 3
    assert all(r["category"] == "Housing / Mortgage" for r in matching)
    assert all(r["transaction_type"] == "mortgage_payment" for r in matching)
    assert all(not r["is_flagged"] for r in matching)


def test_unsupported_action_returns_one_json_error(engine_env) -> None:
    code, payload = _request({"action": "delete_everything"})

    assert code == 1
    assert payload == {"ok": False, "error": "Unsupported Ledger desktop action."}


def test_account_lifecycle_and_types(engine_env) -> None:
    code, payload = _request({"action": "list_accounts"})
    assert code == 0
    type_values = {t["value"] for t in payload["data"]["account_types"]}
    assert {"chequing", "savings", "credit_card"} <= type_values

    code, payload = _request(
        {
            "action": "create_account",
            "params": {
                "name": "Test Chequing",
                "type": "chequing",
                "institution": "Test Bank",
                "opening_balance": 100.0,
            },
        }
    )
    assert code == 0
    accounts = payload["data"]["accounts"]
    assert any(a["name"] == "Test Chequing" for a in accounts)
    assert payload["data"]["created_id"] > 0


def test_spending_availability_preference_is_typed_and_persistent(engine_env) -> None:
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Protected Savings", "type": "savings"},
    })
    account_id = created["data"]["created_id"]
    savings = next(
        row for row in created["data"]["accounts"] if row["id"] == account_id
    )
    assert savings["available_for_spending"] is False

    code, updated = _request({
        "action": "set_account_spending_availability",
        "params": {"account_id": account_id, "available": True},
    })
    assert code == 0
    savings = next(
        row for row in updated["data"]["accounts"] if row["id"] == account_id
    )
    assert savings["available_for_spending"] is True

    _, relaunched = _request({"action": "list_accounts"})
    savings = next(
        row for row in relaunched["data"]["accounts"] if row["id"] == account_id
    )
    assert savings["available_for_spending"] is True


def test_liability_cannot_be_marked_available_for_spending(engine_env) -> None:
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Test Card", "type": "credit_card"},
    })
    code, payload = _request({
        "action": "set_account_spending_availability",
        "params": {"account_id": created["data"]["created_id"], "available": True},
    })
    assert code == 1
    assert "cannot be available" in payload["error"]


def _write_csv(tmp_path, name="statement one.csv"):
    """Synthetic bank CSV in a filename containing a space.

    Uses the canonical signed-export convention: positive amounts are money
    in (credits), negative amounts are money out (debits).
    """
    csv_path = tmp_path / name
    csv_path.write_text(
        "Date,Description,Amount\n"
        "2026-07-02,COFFEE SHOP,-4.50\n"
        "2026-07-03,PAYROLL DEPOSIT,1500.00\n"
        "2026-07-05,GROCERY MART,-82.10\n",
        encoding="utf-8",
    )
    return csv_path


def test_preview_import_parses_synthetic_csv(engine_env) -> None:
    csv_path = _write_csv(engine_env)

    code, payload = _request(
        {"action": "preview_import", "params": {"paths": [str(csv_path)]}}
    )

    assert code == 0
    entry = payload["data"]["files"][0]
    assert entry["detected"] == "csv"
    assert entry["already_imported"] is False
    assert entry["tx_count"] == 3
    assert entry["new_transaction_count"] == 3
    assert entry["duplicate_count"] == 0
    assert entry["date_start"] == "2026-07-02"
    assert entry["date_end"] == "2026-07-05"
    assert entry["income"] == pytest.approx(1500)
    assert entry["spending"] == pytest.approx(86.60)
    assert entry["error"] == ""
    assert len(entry["sample"]) == 3
    by_desc = {r["description"]: r["direction"] for r in entry["sample"]}
    assert by_desc["PAYROLL DEPOSIT"] == "credit"
    assert by_desc["COFFEE SHOP"] == "debit"
    amounts = {r["description"]: r["amount"] for r in entry["sample"]}
    assert amounts["PAYROLL DEPOSIT"] == 1500.00
    assert amounts["COFFEE SHOP"] == -4.50


def test_preview_rejects_missing_file(engine_env) -> None:
    code, payload = _request(
        {
            "action": "preview_import",
            "params": {"paths": [str(engine_env / "nope.csv")]},
        }
    )
    assert code == 1
    assert "not found" in payload["error"].lower()


def test_confirm_import_requires_account(engine_env) -> None:
    csv_path = _write_csv(engine_env)

    code, payload = _request(
        {
            "action": "confirm_import",
            "params": {"files": [{"path": str(csv_path)}]},
        }
    )

    assert code == 1
    assert "destination account" in payload["error"]


def test_full_workflow_import_correct_and_duplicates(engine_env) -> None:
    csv_path = _write_csv(engine_env)

    _, created = _request(
        {
            "action": "create_account",
            "params": {"name": "Everyday Chequing", "type": "chequing"},
        }
    )
    account_id = created["data"]["created_id"]

    code, payload = _request(
        {
            "action": "confirm_import",
            "params": {
                "files": [{"path": str(csv_path), "account_id": account_id}]
            },
        }
    )
    assert code == 0
    assert payload["data"]["totals"]["inserted"] == 3
    assert payload["data"]["results"][0]["account_name"] == "Everyday Chequing"

    # Duplicate protection: importing the same file again inserts nothing.
    code, payload = _request(
        {
            "action": "confirm_import",
            "params": {
                "files": [{"path": str(csv_path), "account_id": account_id}]
            },
        }
    )
    assert code == 0
    assert payload["data"]["totals"]["inserted"] == 0
    assert payload["data"]["totals"]["skipped"] == 3

    # Preview now flags the file as already imported.
    _, preview = _request({
        "action": "preview_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": account_id,
        }]},
    })
    assert preview["data"]["files"][0]["already_imported"] is True
    assert preview["data"]["files"][0]["new_transaction_count"] == 0
    assert preview["data"]["files"][0]["duplicate_count"] == 3

    # The imported rows are listed with their account name.
    code, listing = _request({"action": "list_transactions", "params": {}})
    assert code == 0
    txs = listing["data"]["transactions"]
    assert len(txs) == 3
    assert txs[0]["account_name"] == "Everyday Chequing"
    assert listing["data"]["categories"]

    # Correct one visible transaction and confirm the edit sticks.
    target = next(t for t in txs if "COFFEE" in t["description"].upper())
    category = next(
        c for c in listing["data"]["categories"] if c not in ("", target["category"])
    )
    code, corrected = _request(
        {
            "action": "correct_transaction",
            "params": {"id": target["id"], "category": category, "note": "fixed"},
        }
    )
    assert code == 0
    updated = corrected["data"]["transaction"]
    assert updated["category"] == category
    assert updated["category_source"] == "user_edit"
    assert updated["notes"] == "fixed"

    # Home reflects the imported data afterwards.
    code, home = _request({"action": "home_summary"})
    assert code == 0
    assert home["data"]["freshness"]["state"] != "no_data"


def test_preview_and_confirm_share_account_aware_overlap_counts(engine_env) -> None:
    first = engine_env / "first.csv"
    second = engine_env / "second.csv"
    first.write_text(
        "Date,Description,Amount\n"
        "2026-07-01,LOCAL MARKET,-20.00\n"
        "2026-07-02,LOCAL CAFE,-5.00\n",
        encoding="utf-8",
    )
    second.write_text(
        "Date,Description,Amount\n"
        "2026-07-02,LOCAL CAFE,-5.00\n"
        "2026-07-03,LOCAL BOOKSHOP,-15.00\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Everyday Account", "type": "chequing"},
    })
    account_id = created["data"]["created_id"]
    specs = [
        {"path": str(first), "account_id": account_id},
        {
            "path": str(second), "account_id": account_id,
            "import_mode": "reexport",
        },
    ]
    code, preview = _request({
        "action": "preview_import", "params": {"files": specs},
    })
    assert code == 0
    assert [row["new_transaction_count"] for row in preview["data"]["files"]] == [2, 1]
    assert [row["duplicate_count"] for row in preview["data"]["files"]] == [0, 1]

    code, confirmed = _request({
        "action": "confirm_import", "params": {"files": specs},
    })
    assert code == 0
    assert confirmed["data"]["totals"]["inserted"] == 3
    assert confirmed["data"]["totals"]["skipped"] == 1

    _, repeat = _request({
        "action": "preview_import", "params": {"files": specs},
    })
    assert sum(row["new_transaction_count"] for row in repeat["data"]["files"]) == 0
    assert sum(row["duplicate_count"] for row in repeat["data"]["files"]) == 4


def test_native_unknown_csv_mapping_profile_and_import(engine_env) -> None:
    path = engine_env / "invented bank.csv"
    path.write_text(
        "When;Who;Outflow;Inflow\n"
        "2026-07-02;NEIGHBOURHOOD CAFE;7.25;\n"
        "2026-07-03;INVENTED EMPLOYER;;1800.00\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Mapped Chequing", "type": "chequing"},
    })
    account_id = created["data"]["created_id"]
    _, initial = _request({
        "action": "preview_import", "params": {"paths": [str(path)]},
    })
    entry = initial["data"]["files"][0]
    assert entry["needs_mapping"] is True
    assert entry["mapping_info"]["headers"] == ["When", "Who", "Outflow", "Inflow"]

    mapping = {
        "date_col": "When", "desc_col": "Who", "amount_mode": "split",
        "amount_col": "", "debit_col": "Outflow", "credit_col": "Inflow",
        "positive_is": "credit",
    }
    code, mapped = _request({
        "action": "apply_csv_mapping",
        "params": {
            "path": str(path), "account_id": account_id, "mapping": mapping,
            "save_profile": True, "profile_name": "Invented Bank",
        },
    })
    assert code == 0
    mapped_entry = mapped["data"]["files"][0]
    assert mapped_entry["tx_count"] == 2
    assert mapped_entry["new_transaction_count"] == 2
    assert [row["amount"] for row in mapped_entry["sample"]] == [-7.25, 1800.0]

    code, confirmed = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(path), "account_id": account_id, "mapping": mapping,
        }]},
    })
    assert code == 0 and confirmed["data"]["totals"]["inserted"] == 2

    _, remembered = _request({
        "action": "preview_import", "params": {"paths": [str(path)]},
    })
    remembered_entry = remembered["data"]["files"][0]
    assert remembered_entry["csv_profile_id"]
    assert remembered_entry["suggested_account_ref"] == account_id
    assert remembered_entry["needs_mapping"] is False


def test_correct_transaction_rejects_unknown_category(engine_env) -> None:
    csv_path = _write_csv(engine_env)
    _, created = _request(
        {
            "action": "create_account",
            "params": {"name": "Card", "type": "credit_card"},
        }
    )
    _request(
        {
            "action": "confirm_import",
            "params": {
                "files": [
                    {"path": str(csv_path),
                     "account_id": created["data"]["created_id"]}
                ]
            },
        }
    )
    _, listing = _request({"action": "list_transactions", "params": {}})
    tx_id = listing["data"]["transactions"][0]["id"]

    code, payload = _request(
        {
            "action": "correct_transaction",
            "params": {"id": tx_id, "category": "Not A Real Category"},
        }
    )
    assert code == 1
    assert "not a Ledger category" in payload["error"]


def test_native_plan_goal_insights_and_manual_entry(engine_env) -> None:
    from datetime import date

    _, created = _request({
        "action": "create_account",
        "params": {"name": "Northstar Chequing", "type": "chequing"},
    })
    account_id = created["data"]["created_id"]

    code, manual = _request({
        "action": "add_manual_transaction",
        "params": {
            "account_id": account_id,
            "date": date.today().isoformat(),
            "description": "SYNTHETIC GROCERIES",
            "amount": 42.50,
            "direction": "debit",
            "category": "Groceries",
            "note": "manual test",
        },
    })
    assert code == 0
    assert manual["data"]["transaction"]["account_name"] == "Northstar Chequing"

    month = date.today().strftime("%Y-%m")
    code, plan = _request({
        "action": "save_plan",
        "params": {
            "month": month,
            "mode": "normal",
            "income_target": 5000,
                "fixed_obligations": 2200,
                "flexible_allowance": 1200,
                "savings_target": 1000,
                "safety_buffer": 200,
                "fixed_override_reason": "Synthetic manual commitments",
            },
    })
    assert code == 0
    # 0.7 makes flexible spending the exact remainder in the authoritative
    # engine equation: 5000 - 2200 fixed - 1000 savings - 200 buffer = 1600.
    assert plan["data"]["saved"]["spending_target"] == 3800
    assert plan["data"]["equation"]["flexible"] == 1600
    assert plan["data"]["saved"]["safety_buffer"] == 200

    code, goals = _request({
        "action": "create_goal",
        "params": {
            "name": "Synthetic reserve",
            "type": "emergency_fund",
            "target_amount": 2000,
            "current_amount": 250,
            "monthly_contribution": 100,
        },
    })
    assert code == 0
    goal_id = goals["data"]["created_id"]
    created_goal = next(
        g for g in goals["data"]["goals"] if g["id"] == goal_id
    )
    assert created_goal["include_in_plan"] == 0
    code, goals = _request({
        "action": "contribute_goal",
        "params": {"goal_id": goal_id, "amount": 75},
    })
    assert code == 0
    goal = next(g for g in goals["data"]["goals"] if g["id"] == goal_id)
    assert goal["current_amount"] == pytest.approx(325)

    code, insights = _request({"action": "insights_summary"})
    assert code == 0
    assert insights["data"]["categories"][0]["category"] == "Groceries"


def test_native_transaction_filters_pagination_and_backup_restore(engine_env) -> None:
    from datetime import date

    _, created = _request({
        "action": "create_account",
        "params": {"name": "Filter Account", "type": "chequing"},
    })
    account_id = created["data"]["created_id"]
    for description, amount, category in (
        ("SYNTHETIC COFFEE", 5.0, "Food & Convenience"),
        ("SYNTHETIC BOOK", 20.0, "Shopping"),
        ("SYNTHETIC GROCER", 80.0, "Groceries"),
    ):
        code, _ = _request({
            "action": "add_manual_transaction",
            "params": {
                "account_id": account_id,
                "date": date.today().isoformat(),
                "description": description,
                "amount": amount,
                "direction": "debit",
                "category": category,
            },
        })
        assert code == 0

    code, listing = _request({
        "action": "list_transactions",
        "params": {
            "search": "SYNTHETIC",
            "direction": "debit",
            "sort": "amount",
            "descending": True,
            "limit": 2,
            "offset": 0,
        },
    })
    assert code == 0
    assert listing["data"]["total"] == 3
    assert [row["amount"] for row in listing["data"]["transactions"]] == [-80, -20]

    code, backup = _request({"action": "create_backup"})
    assert code == 0
    backup_path = backup["data"]["backups"][0]["path"]

    _request({
        "action": "add_manual_transaction",
        "params": {
            "account_id": account_id,
            "date": date.today().isoformat(),
            "description": "AFTER BACKUP",
            "amount": 10,
            "direction": "debit",
            "category": "Shopping",
        },
    })
    code, restored = _request({
        "action": "restore_backup", "params": {"path": backup_path}
    })
    assert code == 0
    assert restored["data"]["restored"]
    _, listing = _request({"action": "list_transactions", "params": {}})
    assert all(row["description"] != "AFTER BACKUP"
               for row in listing["data"]["transactions"])


def _write_tangerine_card_csv(tmp_path):
    """Sanitized fixture matching the structure of a real Tangerine credit
    card CSV export: Transaction date,Transaction,Name,Memo,Amount with an
    explicit DEBIT/CREDIT indicator and purchases exported as negatives.
    All institutions, merchants, dates, and amounts are invented."""
    p = tmp_path / "card 1234 5678.CSV"
    p.write_text(
        "Transaction date,Transaction,Name,Memo,Amount\n"
        "7/2/2026,DEBIT,COFFEE HOUSE,Point of sale purchase,-4.50\n"
        "7/3/2026,DEBIT,BOOK STORE,Point of sale purchase,-25.00\n"
        "7/5/2026,CREDIT,PAYMENT THANK YOU,Bill payment,150.00\n",
        encoding="utf-8",
    )
    return p


def _write_tangerine_chequing_csv(tmp_path):
    """Sanitized fixture matching the structure of a real Tangerine chequing
    CSV export: Date,Transaction,Name,Memo,Amount. One row has an empty Memo
    and one row a Memo that used to be mistaken for the description."""
    p = tmp_path / "chq 9999.CSV"
    p.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "7/2/2026,OTHER,EMPLOYER PAYROLL,,1200.00\n"
        "7/4/2026,OTHER,HYDRO UTILITY,Bill payment to provider,-96.40\n"
        "7/6/2026,ATM,GROCERY MART,,-82.10\n",
        encoding="utf-8",
    )
    return p


def test_tangerine_card_structure_preserves_source_sign(engine_env) -> None:
    card = _write_tangerine_card_csv(engine_env)

    code, payload = _request(
        {"action": "preview_import", "params": {"paths": [str(card)]}}
    )

    assert code == 0
    entry = payload["data"]["files"][0]
    assert entry["tx_count"] == 3
    by_desc = {r["description"]: r["direction"] for r in entry["sample"]}
    # Purchases remain negative and the explicit flow agrees with that sign.
    # Name (the merchant) must be the description.
    assert by_desc["COFFEE HOUSE"] == "debit"
    assert by_desc["BOOK STORE"] == "debit"
    assert by_desc["PAYMENT THANK YOU"] == "credit"


def test_tangerine_two_structure_import_end_to_end(engine_env) -> None:
    card = _write_tangerine_card_csv(engine_env)
    chq = _write_tangerine_chequing_csv(engine_env)

    _, created_card = _request(
        {"action": "create_account",
         "params": {"name": "Card", "type": "credit_card"}}
    )
    _, created_chq = _request(
        {"action": "create_account",
         "params": {"name": "Chequing", "type": "chequing"}}
    )

    code, payload = _request(
        {
            "action": "confirm_import",
            "params": {
                "files": [
                    {"path": str(card),
                     "account_id": created_card["data"]["created_id"]},
                    {"path": str(chq),
                     "account_id": created_chq["data"]["created_id"]},
                ]
            },
        }
    )
    assert code == 0
    assert payload["data"]["totals"]["inserted"] == 6

    _, listing = _request({"action": "list_transactions", "params": {}})
    txs = listing["data"]["transactions"]
    by_desc = {t["description"]: t for t in txs}
    assert by_desc["EMPLOYER PAYROLL"]["direction"] == "credit"
    assert by_desc["COFFEE HOUSE"]["direction"] == "debit"
    assert by_desc["PAYMENT THANK YOU"]["direction"] == "credit"
    assert by_desc["EMPLOYER PAYROLL"]["amount"] == 1200.00
    assert by_desc["COFFEE HOUSE"]["amount"] == -4.50
    assert by_desc["PAYMENT THANK YOU"]["amount"] == 150.00
    # Memo text survives as a note instead of masquerading as the merchant.
    assert by_desc["HYDRO UTILITY"]["notes"] == "Bill payment to provider"
    assert by_desc["HYDRO UTILITY"]["date"] == "2026-07-04"


def test_uncategorized_correction_refreshes_category_datasets(engine_env) -> None:
    from datetime import date

    today = date.today().isoformat()
    csv_path = engine_env / "invented uncategorized.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        f"{today},DEBIT,NOVELTY EMPORIUM,Purchase,-12.00\n"
        f"{today},DEBIT,NOVELTY EMPORIUM,Purchase,-18.00\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Test Chequing", "type": "chequing"},
    })
    code, imported = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": created["data"]["created_id"],
        }]},
    })
    assert code == 0 and imported["data"]["totals"]["inserted"] == 2

    _, uncategorized = _request({
        "action": "list_transactions", "params": {"category": "Uncategorized"},
    })
    assert uncategorized["data"]["total"] == 2
    _, before = _request({"action": "home_dashboard"})
    before_total = before["data"]["categories"]["total"]
    before_items = {item["category"]: item["amount"]
                    for item in before["data"]["categories"]["items"]}
    assert before_items["Uncategorized"] == pytest.approx(30.00)

    code, corrected = _request({
        "action": "correct_transaction",
        "params": {
            "id": uncategorized["data"]["transactions"][0]["id"],
            "category": "Shopping", "note": "",
            "apply_to_matching": True, "remember_rule": True,
        },
    })
    assert code == 0
    assert corrected["data"]["matching_updated"] == 1
    assert corrected["data"]["rule_saved"] is True

    _, after = _request({"action": "home_dashboard"})
    after_items = {item["category"]: item["amount"]
                   for item in after["data"]["categories"]["items"]}
    assert after["data"]["categories"]["total"] == pytest.approx(before_total)
    assert after_items["Shopping"] == pytest.approx(30.00)
    assert "Uncategorized" not in after_items
    _, insights = _request({"action": "insights_summary"})
    assert insights["data"]["categories"][0]["category"] == "Shopping"

    _, undone = _request({"action": "undo_last_category_change"})
    assert undone["data"]["restored"] == 2
    _, restored = _request({"action": "home_dashboard"})
    restored_items = {item["category"]: item["amount"]
                      for item in restored["data"]["categories"]["items"]}
    assert restored_items["Uncategorized"] == pytest.approx(30.00)

    _, settings = _request({"action": "category_settings"})
    rule = settings["data"]["rules"][0]
    assert rule["example_description"] == "NOVELTY EMPORIUM"
    _, changed = _request({
        "action": "update_category_rule",
        "params": {"id": rule["id"], "category": "Groceries", "enabled": False},
    })
    updated = changed["data"]["rules"][0]
    assert updated["category"] == "Groceries"
    assert updated["enabled"] == 0
    assert updated["example_description"] == "NOVELTY EMPORIUM"
    _, deleted = _request({
        "action": "delete_category_rule", "params": {"id": rule["id"]},
    })
    assert deleted["data"]["deleted"] is True


def test_recurring_override_stays_manageable_after_exclusion(engine_env) -> None:
    csv_path = engine_env / "invented recurring service.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-01-10,DEBIT,RELIABLE HOSTING,, -20.00\n"
        "2026-02-10,DEBIT,RELIABLE HOSTING,, -20.00\n"
        "2026-03-10,DEBIT,RELIABLE HOSTING,, -20.00\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Test Card", "type": "credit_card"},
    })
    code, imported = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": created["data"]["created_id"],
        }]},
    })
    assert code == 0 and imported["data"]["totals"]["inserted"] == 3

    _, automatic = _request({"action": "category_settings"})
    recurring = automatic["data"]["recurring"][0]
    merchant_key = recurring["merchant_normalized"]
    assert recurring["recurring_status"] == "automatic"

    _, excluded = _request({
        "action": "set_recurring_preference",
        "params": {"merchant_normalized": merchant_key, "status": "not_recurring"},
    })
    assert excluded["data"]["status"] == "not_recurring"
    _, settings = _request({"action": "category_settings"})
    visible = next(item for item in settings["data"]["recurring"]
                   if item["merchant_normalized"] == merchant_key)
    assert visible["recurring_status"] == "excluded"
    _, insights = _request({"action": "insights_summary"})
    assert all(item["merchant_normalized"] != merchant_key
               for item in insights["data"]["recurring"])

    _request({
        "action": "set_recurring_preference",
        "params": {"merchant_normalized": merchant_key, "status": "automatic"},
    })
    _, restored = _request({"action": "category_settings"})
    assert any(item["merchant_normalized"] == merchant_key
               for item in restored["data"]["recurring"])


def test_recognized_subscription_can_be_recurring_after_two_stable_months(engine_env) -> None:
    csv_path = engine_env / "invented two month subscription.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-05-20,DEBIT,OPENAI SUBSCRIPTION,Category: Other,-28.25\n"
        "2026-06-20,DEBIT,OPENAI SUBSCRIPTION,Category: Other,-28.25\n",
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Test Card", "type": "credit_card"},
    })
    code, imported = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": created["data"]["created_id"],
        }]},
    })
    assert code == 0 and imported["data"]["totals"]["inserted"] == 2

    _, insights = _request({"action": "insights_summary"})
    item = next(row for row in insights["data"]["recurring"]
                if "OPENAI" in row["merchant_normalized"])
    assert item["category"] == "Subscriptions & Digital"
    assert item["cadence"] == "monthly"
    assert item["recurring_status"] == "automatic"


def test_insights_returns_every_spending_category_for_reconciliation(engine_env) -> None:
    from datetime import date

    today = date.today().isoformat()
    rows = [
        ("INVENTED CAFE", "Category: Restaurant"),
        ("INVENTED GAME SHOP", "Category: E-Games"),
        ("INVENTED FUEL", "Category: Gas"),
        ("INVENTED MARKET", "Category: Groceries"),
        ("INVENTED UTILITY", "Category: Bill Payment"),
        ("INVENTED HARDWARE", "Category: Home Improvement"),
        ("INVENTED PHARMACY", "Category: Drug Store"),
        ("NESTO HOME LOAN", "Category: Other"),
        ("AMAZON MARKETPLACE", "Category: Other"),
        ("PETSMART", "Category: Other"),
        ("OPENAI SUBSCRIPTION", "Category: Other"),
        ("YMCA MEMBERSHIP", "Category: Other"),
        ("ECONOMICAL INSURANCE", "Category: Other"),
    ]
    csv_path = engine_env / "invented many categories.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n" +
        "".join(f"{today},DEBIT,{merchant},{memo},-{10 + index}.00\n"
                for index, (merchant, memo) in enumerate(rows)),
        encoding="utf-8",
    )
    _, created = _request({
        "action": "create_account",
        "params": {"name": "Test Card", "type": "credit_card"},
    })
    code, imported = _request({
        "action": "confirm_import",
        "params": {"files": [{
            "path": str(csv_path), "account_id": created["data"]["created_id"],
        }]},
    })
    assert code == 0 and imported["data"]["totals"]["inserted"] == len(rows)

    _, insights = _request({"action": "insights_summary", "params": {"period_days": 90}})
    categories = insights["data"]["categories"]
    pace = insights["data"]["category_pace"]

    assert len(categories) > 10
    assert sum(item["total"] for item in categories) == pytest.approx(pace["total"])
    assert sum(item["amount"] for item in pace["items"]) == pytest.approx(pace["total"])


def _seed_two_months(engine_env) -> None:
    """Seed one complete previous month and a partial current month through
    the engine's own import path, using invented data."""
    from datetime import date, timedelta

    today = date.today()
    prev_last = date(today.year, today.month, 1) - timedelta(days=1)
    prev = prev_last.strftime("%Y-%m")
    cur = today.strftime("%Y-%m")
    rows = [
        f"{prev}-02,CREDIT,EMPLOYER PAYROLL,,2000.00",
        f"{prev}-03,DEBIT,GROCERY MART,,-150.00",
        f"{prev}-28,DEBIT,COFFEE HOUSE,,-40.00",
        f"{prev}-{prev_last.day:02d},DEBIT,BOOK STORE,,-60.00",
        f"{cur}-01,CREDIT,EMPLOYER PAYROLL,,2000.00",
        f"{cur}-02,DEBIT,GROCERY MART,,-260.00",
    ]
    rows.extend(
        f"{prev}-{day:02d},DEBIT,TRANSFER TO SAVINGS {day},,-1.00"
        for day in range(4, 16)
    )
    csv_path = engine_env / "seed months.CSV"
    csv_path.write_text(
        "Date,Transaction,Name,Memo,Amount\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    _, created = _request(
        {"action": "create_account",
         "params": {"name": "Chequing", "type": "chequing"}}
    )
    code, payload = _request(
        {
            "action": "confirm_import",
            "params": {
                "files": [{"path": str(csv_path),
                           "account_id": created["data"]["created_id"]}]
            },
        }
    )
    assert code == 0 and payload["data"]["totals"]["inserted"] == len(rows)


def test_same_file_reimport_blocked_into_other_account(engine_env) -> None:
    """The exact same file re-imports calmly into its own account (all
    duplicates) but is rejected for a different account, where account-aware
    dedup would otherwise double-count every row."""
    csv_path = _write_tangerine_card_csv(engine_env)
    _, a1 = _request(
        {"action": "create_account", "params": {"name": "Card A", "type": "credit_card"}}
    )
    _, a2 = _request(
        {"action": "create_account", "params": {"name": "Card B", "type": "credit_card"}}
    )
    id1 = a1["data"]["created_id"]
    id2 = a2["data"]["created_id"]

    code, payload = _request(
        {"action": "confirm_import",
         "params": {"files": [{"path": str(csv_path), "account_id": id1}]}}
    )
    assert code == 0 and payload["data"]["totals"]["inserted"] == 3

    # Same account: calm duplicate-only outcome.
    code, payload = _request(
        {"action": "confirm_import",
         "params": {"files": [{"path": str(csv_path), "account_id": id1}]}}
    )
    assert code == 0
    assert payload["data"]["totals"]["inserted"] == 0
    assert payload["data"]["totals"]["skipped"] == 3

    _, preview = _request({
        "action": "preview_import",
        "params": {"files": [{"path": str(csv_path), "account_id": id2}]},
    })
    assert "already imported into" in preview["data"]["files"][0]["error"]
    assert "Card A" in preview["data"]["files"][0]["error"]

    # Different account: rejected with a plain-language explanation.
    code, payload = _request(
        {"action": "confirm_import",
         "params": {"files": [{"path": str(csv_path), "account_id": id2}]}}
    )
    assert code == 1
    assert "double-count" in payload["error"]
    assert "Card A" in payload["error"]


def test_native_connection_waits_for_a_short_database_lock(
    engine_env, monkeypatch
) -> None:
    """A second sidecar should wait for first-run schema work, not fail."""
    import sqlite3
    import time
    from concurrent.futures import ThreadPoolExecutor

    from utils import database

    monkeypatch.setattr(database, "DB_PATH", engine_env / "locked.db")
    blocker = sqlite3.connect(database.DB_PATH)
    blocker.execute("CREATE TABLE lock_probe (id INTEGER PRIMARY KEY)")
    blocker.commit()
    blocker.execute("PRAGMA journal_mode=DELETE")
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        def connect_and_close() -> bool:
            connection = database.get_connection()
            connection.close()
            return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(connect_and_close)
            time.sleep(0.1)
            assert not pending.done()
            blocker.rollback()
            assert pending.result(timeout=2) is True
    finally:
        blocker.close()


def test_home_dashboard_datasets(engine_env) -> None:
    from datetime import date

    _seed_two_months(engine_env)
    code, payload = _request({"action": "home_dashboard", "params": {}})

    assert code == 0
    data = payload["data"]
    assert data["available"] is True

    cur = date.today().strftime("%Y-%m")
    months = {m["month"]: m for m in data["cashflow"]}
    assert months[cur]["complete"] is False
    prev = data["pace"]["previous_month"]
    assert months[prev]["complete"] is True
    assert months[prev]["income"] == 2000.0
    assert months[prev]["spending"] == 250.0
    assert months[prev]["net"] == 1750.0

    # Pace compares through the latest imported day against the previous
    # month through that SAME day, never against unimported calendar days.
    pace = data["pace"]
    assert pace["current_complete"] is False
    assert pace["current_total"] == 260.0
    assert pace["previous_total_same_day"] == 0.0
    assert pace["current"][-1]["day"] == 2

    cats = data["categories"]
    assert cats["month"] == "Last 30 days"
    assert cats["total"] == pytest.approx(
        sum(item["amount"] for item in cats["items"])
    )
    assert sum(item["total"] for item in data["income_sources"]) == pytest.approx(
        months[cur]["income"]
    )
    assert data["income_sources"][0]["source"] == "Employer Payroll"
    from datetime import timedelta
    assert data["period_start"] == (
        date.fromisoformat(data["period_end"]) - timedelta(days=29)
    ).isoformat()


def test_home_and_insights_share_30_and_90_day_periods(engine_env) -> None:
    from datetime import date, timedelta

    _, created = _request({
        "action": "create_account",
        "params": {"name": "Period account", "type": "chequing"},
    })
    account_id = created["data"]["created_id"]
    latest = date.today()
    for age, description, amount, direction, category in (
        (0, "LATEST PAYROLL", 2000, "credit", "Payroll Income"),
        (2, "LATEST GROCER", 125, "debit", "Groceries"),
        (40, "OLDER PAYROLL", 1500, "credit", "Payroll Income"),
        (41, "OLDER SHOP", 80, "debit", "Shopping"),
    ):
        code, _ = _request({
            "action": "add_manual_transaction",
            "params": {
                "account_id": account_id,
                "date": (latest - timedelta(days=age)).isoformat(),
                "description": description,
                "amount": amount,
                "direction": direction,
                "category": category,
            },
        })
        assert code == 0

    for days, expected_income, expected_spending in (
        (30, 2000, 125), (90, 3500, 205),
    ):
        _, home = _request({"action": "home_summary", "params": {"period_days": days}})
        _, dashboard = _request({"action": "home_dashboard", "params": {"period_days": days}})
        _, insights = _request({"action": "insights_summary", "params": {"period_days": days}})
        assert home["data"]["progress"]["mtd_income"] == pytest.approx(expected_income)
        assert home["data"]["progress"]["mtd_spending"] == pytest.approx(expected_spending)
        assert dashboard["data"]["income_total"] == pytest.approx(expected_income)
        assert dashboard["data"]["categories"]["total"] == pytest.approx(expected_spending)
        assert insights["data"]["income_total"] == pytest.approx(expected_income)
        assert sum(
            row["total"] for row in insights["data"]["categories"]
        ) == pytest.approx(expected_spending)
        assert insights["data"]["period_days"] == days


def test_transaction_rows_expose_signed_etransfer_labels(engine_env) -> None:
    from datetime import date

    _, created = _request({
        "action": "create_account",
        "params": {"name": "Flow account", "type": "chequing"},
    })
    for description, direction, amount in (
        ("INTERAC E-TRANSFER FROM: SAMPLE", "credit", 75),
        ("INTERAC E-TRANSFER TO: SAMPLE", "debit", 25),
    ):
        code, _ = _request({
            "action": "add_manual_transaction",
            "params": {
                "account_id": created["data"]["created_id"],
                "date": date.today().isoformat(),
                "description": description,
                "amount": amount,
                "direction": direction,
                "category": "Income" if direction == "credit" else "Uncategorized",
            },
        })
        assert code == 0
    _, listing = _request({"action": "list_transactions", "params": {}})
    by_description = {row["description"]: row for row in listing["data"]["transactions"]}
    assert by_description["INTERAC E-TRANSFER FROM: SAMPLE"]["amount"] == 75
    assert by_description["INTERAC E-TRANSFER FROM: SAMPLE"]["flow_label"] == "E-transfer in"
    assert by_description["INTERAC E-TRANSFER TO: SAMPLE"]["amount"] == -25
    assert by_description["INTERAC E-TRANSFER TO: SAMPLE"]["flow_label"] == "E-transfer out"


def test_data_safety_reset_requires_confirmation_and_restores_backup(
    engine_env,
) -> None:
    from datetime import date

    _, created = _request({
        "action": "create_account",
        "params": {"name": "Reset account", "type": "chequing"},
    })
    _, added = _request({
        "action": "add_manual_transaction",
        "params": {
            "account_id": created["data"]["created_id"],
            "date": date.today().isoformat(),
            "description": "RESET RECOVERY PROBE",
            "amount": 42,
            "direction": "debit",
            "category": "Shopping",
        },
    })
    assert added["ok"] is True

    code, rejected = _request({
        "action": "reset_financial_data",
        "params": {"confirmation": "reset", "complete_reset": False},
    })
    assert code == 1
    assert "RESET" in rejected["error"]

    code, reset = _request({
        "action": "reset_financial_data",
        "params": {"confirmation": "RESET", "complete_reset": False},
    })
    assert code == 0
    assert reset["data"]["removed"]["transaction_count"] == 1
    backup_path = reset["data"]["backup_path"]
    _, empty = _request({"action": "list_transactions", "params": {}})
    assert empty["data"]["transactions"] == []

    code, restored = _request({
        "action": "restore_backup",
        "params": {"path": backup_path},
    })
    assert code == 0
    assert restored["data"]["restored"] == Path(backup_path).name
    _, listing = _request({"action": "list_transactions", "params": {}})
    assert [row["description"] for row in listing["data"]["transactions"]] == [
        "RESET RECOVERY PROBE"
    ]


def test_home_dashboard_empty_database(engine_env) -> None:
    code, payload = _request({"action": "home_dashboard", "params": {}})
    assert code == 0
    assert payload["data"]["available"] is False
    assert payload["data"]["pace"]["available"] is False
    assert payload["data"]["categories"]["available"] is False


@pytest.mark.parametrize(
    "path",
    [
        "build/native-sidecar/ledger-engine.exe",
        "build/native-sidecar-dir/ledger-engine/ledger-engine.exe",
        "dist-desktop/assets/index.js",
        "src-tauri/target/release/ledger-native.exe",
        "src-tauri/binaries/ledger-engine-x86_64-pc-windows-msvc.exe",
    ],
)
def test_share_zip_excludes_native_build_outputs(path: str) -> None:
    from pathlib import Path

    assert is_excluded(Path(path))

"""Merchant normalization, saved-rule precedence, and audit provenance."""
from __future__ import annotations

from pathlib import Path

import utils.database as db
from parsers.csv_import import parse_csv
from utils.categorizer import enrich_transaction, normalize_merchant


FIXTURES = Path(__file__).parent / "fixtures"


def _tx(desc, *, amount=12.0, direction="debit", account="mastercard"):
    return {
        "account_type": account,
        "transaction_date": "2026-07-17",
        "raw_description": desc,
        "amount": amount,
        "direction": direction,
    }


def test_normalize_capitalization_whitespace_and_repeatability():
    values = [
        "  Costco   Wholesale  ",
        "COSTCO WHOLESALE",
        "costco wholesale",
    ]
    assert {normalize_merchant(v) for v in values} == {"COSTCO WHOLESALE"}
    first = normalize_merchant(values[0])
    assert normalize_merchant(values[0]) == first


def test_normalize_reference_terminal_processor_and_date_noise():
    variants = [
        "SQ *TIM HORTONS STORE #1234",
        "TIM HORTONS TERMINAL 9876",
        "TIM HORTONS REF A19BC8821",
        "TIM HORTONS 2026-07-17",
    ]
    assert {normalize_merchant(v) for v in variants} == {"TIM HORTONS"}


def test_normalize_explicit_location_suffix():
    assert normalize_merchant("DEMO MARKET, TORONTO ON") == "DEMO MARKET"


def test_normalize_keeps_meaningful_identity_distinct():
    assert normalize_merchant("7-ELEVEN") == "7-ELEVEN"
    assert normalize_merchant("SHOPPERS DRUG MART") != normalize_merchant("SHOPPERS HOME HEALTH")
    assert normalize_merchant("STUDIO 54") == "STUDIO 54"


def test_normalize_empty_and_malformed():
    assert normalize_merchant("") == ""
    assert normalize_merchant("   ") == ""
    assert normalize_merchant(None) == ""
    out = enrich_transaction(_tx(None))
    assert out["merchant_normalized"] == ""
    assert out["category"] == "Uncategorized"


def test_enabled_user_rule_applies_with_explanation(ledger_db):
    rule_id = db.upsert_learned_rule(
        "SQ *COSTCO STORE 1234", "Groceries",
        example_description="SQ *COSTCO STORE 1234", conn=ledger_db,
    )
    out = enrich_transaction(_tx("COSTCO TERMINAL 9988"), conn=ledger_db)
    assert out["category"] == "Groceries"
    assert out["category_source"] == "user_rule"
    assert out["category_rule_id"] == rule_id
    assert "saved rule for COSTCO" in out["category_explanation"]


def test_disabled_rule_does_not_apply(ledger_db):
    db.upsert_learned_rule("COFFEE PLACE", "Groceries", conn=ledger_db)
    db.set_learned_rule_enabled("COFFEE PLACE", False, conn=ledger_db)
    out = enrich_transaction(_tx("COFFEE PLACE"), conn=ledger_db)
    assert out["category"] != "Groceries"
    assert out["category_source"] == "import_rule"


def test_saved_rule_then_statement_memo_then_known_merchant_precedence(ledger_db):
    statement = enrich_transaction({
        **_tx("AMAZON MARKETPLACE"),
        "statement_category": "Groceries",
        "statement_subcategory": "Groceries",
    }, conn=ledger_db)
    assert statement["category"] == "Groceries"
    assert statement["category_source"] == "statement_memo"

    db.upsert_learned_rule("AMAZON MARKETPLACE", "Pets", conn=ledger_db)
    learned = enrich_transaction({
        **_tx("AMAZON MARKETPLACE"),
        "statement_category": "Groceries",
        "statement_subcategory": "Groceries",
    }, conn=ledger_db)
    assert learned["category"] == "Pets"
    assert learned["category_source"] == "user_rule"

    known = enrich_transaction(_tx("AMAZON MARKETPLACE"), conn=ledger_db)
    assert known["category"] == "Pets"


def test_tangerine_statement_memo_is_preserved_and_used(tmp_path, ledger_db):
    path = tmp_path / "invented-bank-export.csv"
    path.write_text(
        "Date,Transaction,Name,Memo,Amount\n"
        "2026-07-02,DEBIT,INVENTED CORNER STORE,Category: Groceries,-24.50\n",
        encoding="utf-8",
    )

    tx = parse_csv(path)["transactions"][0]

    assert tx["amount"] == -24.50
    assert tx["statement_memo"] == "Category: Groceries"
    assert tx["statement_category"] == "Groceries"
    assert tx["category"] == "Groceries"
    assert tx["category_source"] == "statement_memo"


def test_requested_known_merchants_have_specific_categories(ledger_db):
    expected = {
        "NESTO HOME LOAN": "Housing / Mortgage",
        "MICHAELS STORE": "Shopping",
        "LIFELABS CLINIC": "Health / Care",
        "NBX*YMCA MEMBERSHIP": "Fitness",
        "ECONOMICAL INSURANCE": "Insurance",
        "OPENAI SUBSCRIPTION": "Subscriptions & Digital",
        "ANTHROPIC SUBSCRIPTION": "Subscriptions & Digital",
    }
    for description, category in expected.items():
        assert enrich_transaction(_tx(description), conn=ledger_db)["category"] == category


def test_protected_transfer_and_card_payment_beat_conflicting_rule(ledger_db):
    db.upsert_learned_rule("TANGERINE CREDIT CARD PAYMENT", "Groceries", conn=ledger_db)
    payment = enrich_transaction(_tx(
        "TANGERINE CREDIT CARD PAYMENT", direction="payment", account="chequing"
    ), conn=ledger_db)
    assert payment["category"] == "Credit Card Payment"
    assert payment["transaction_type"] == "credit_card_payment"
    assert payment["category_source"] == "protected"

    db.upsert_learned_rule("OWN ACCOUNT MOVE", "Shopping", conn=ledger_db)
    transfer = enrich_transaction(_tx(
        "OWN ACCOUNT MOVE", direction="transfer", account="savings"
    ), conn=ledger_db)
    assert transfer["category"] == "Internal Transfer"
    assert transfer["transaction_type"] == "internal_transfer"
    assert transfer["category_source"] == "protected"


def test_protected_cash_advance_and_refund_beat_rule(ledger_db):
    db.upsert_learned_rule("ATM CASH ADVANCE", "Groceries", conn=ledger_db)
    advance = enrich_transaction(_tx("ATM CASH ADVANCE"), conn=ledger_db)
    assert advance["category"] == "Cash Advance"
    assert advance["category_source"] == "protected"

    db.upsert_learned_rule("MERCHANT REFUND", "Shopping", conn=ledger_db)
    refund = enrich_transaction(_tx(
        "MERCHANT REFUND", amount=-25.0, direction="credit"
    ), conn=ledger_db)
    assert refund["category"] == "Refund / Credit"
    assert refund["category_source"] == "protected"


def test_rule_creation_is_not_retroactive(ledger_db):
    ok, _ = db.insert_transaction({
        **_tx("LATER RULE MERCHANT"),
        "merchant": "Later Rule Merchant",
        "category": "Shopping",
        "is_transfer": 0,
    }, ledger_db)
    assert ok
    ledger_db.commit()
    db.upsert_learned_rule("LATER RULE MERCHANT", "Groceries", conn=ledger_db)
    row = ledger_db.execute(
        "SELECT category FROM transactions WHERE raw_description='LATER RULE MERCHANT'"
    ).fetchone()
    assert row["category"] == "Shopping"


def test_canonical_rule_overlap_resolves_to_one_stable_row(ledger_db):
    first = db.upsert_learned_rule("SQ *DEMO CAFE STORE 123", "Groceries", conn=ledger_db)
    second = db.upsert_learned_rule("DEMO CAFE TERMINAL 987", "Food & Convenience", conn=ledger_db)
    assert first == second
    rows = db.list_learned_rules(conn=ledger_db)
    assert len(rows) == 1
    assert rows[0]["category"] == "Food & Convenience"


def test_noisy_csv_descriptions_share_one_matcher(ledger_db):
    parsed = parse_csv(FIXTURES / "categorization_first_import.csv")
    cafe_rows = [
        tx for tx in parsed["transactions"]
        if "DEMO CAFE" in tx["raw_description"]
    ]
    assert len(cafe_rows) == 3
    assert {tx["merchant_normalized"] for tx in cafe_rows} == {"DEMO CAFE"}


def test_saved_and_disabled_rule_flow_through_actual_csv_import(ledger_db):
    db.upsert_learned_rule("DEMO CAFE", "Food & Convenience", conn=ledger_db)
    ledger_db.commit()
    parsed = parse_csv(
        FIXTURES / "categorization_second_import.csv", conn=ledger_db,
    )
    cafe = next(tx for tx in parsed["transactions"]
                if "DEMO CAFE" in tx["raw_description"])
    assert cafe["category"] == "Food & Convenience"
    assert cafe["category_source"] == "user_rule"
    assert "saved rule for DEMO CAFE" in cafe["category_explanation"]

    db.set_learned_rule_enabled("DEMO CAFE", False, conn=ledger_db)
    ledger_db.commit()
    parsed_disabled = parse_csv(
        FIXTURES / "categorization_second_import.csv", conn=ledger_db,
    )
    disabled_cafe = next(tx for tx in parsed_disabled["transactions"]
                         if "DEMO CAFE" in tx["raw_description"])
    assert disabled_cafe["category_source"] == "import_rule"


def test_rule_hits_count_only_successful_inserts(ledger_db):
    rule_id = db.upsert_learned_rule("DEMO CAFE", "Groceries", conn=ledger_db)
    ledger_db.commit()

    first = enrich_transaction(_tx("DEMO CAFE STORE 123"), conn=ledger_db)
    enrich_transaction(_tx("DEMO CAFE TERMINAL 456"), conn=ledger_db)
    assert db.get_learned_rule("DEMO CAFE", conn=ledger_db)["hit_count"] == 0

    ok, _ = db.insert_transaction(first, ledger_db)
    assert ok
    duplicate = dict(first)
    ok, reason = db.insert_transaction(duplicate, ledger_db)
    assert not ok and reason == "duplicate"
    assert db.get_learned_rule("DEMO CAFE", conn=ledger_db)["hit_count"] == 1
    assert first["category_rule_id"] == rule_id


def test_rule_disable_reenable_delete_does_not_rewrite_history(ledger_db):
    rule_id = db.upsert_learned_rule("LIFECYCLE SHOP", "Shopping", conn=ledger_db)
    db.set_learned_rule_enabled("LIFECYCLE SHOP", False, conn=ledger_db)
    assert enrich_transaction(_tx("LIFECYCLE SHOP"), conn=ledger_db)[
        "category_source"
    ] == "import_rule"

    assert db.update_learned_rule_by_id(
        rule_id, category="Groceries", enabled=True, conn=ledger_db
    )
    tx = enrich_transaction(_tx("LIFECYCLE SHOP"), conn=ledger_db)
    assert tx["category"] == "Groceries"
    ok, _ = db.insert_transaction(tx, ledger_db)
    assert ok
    ledger_db.commit()

    assert db.delete_learned_rule_by_id(rule_id, conn=ledger_db)
    historical = ledger_db.execute(
        "SELECT category, category_source FROM transactions"
    ).fetchone()
    assert dict(historical) == {
        "category": "Groceries", "category_source": "user_rule"
    }
    assert enrich_transaction(_tx("LIFECYCLE SHOP"), conn=ledger_db)[
        "category_source"
    ] == "import_rule"


def test_split_credit_csv_uses_positive_income_not_refund(tmp_path, ledger_db):
    path = tmp_path / "income.csv"
    path.write_text(
        "Date,Description,Debit,Credit\n"
        "2026-07-15,EMPLOYER PAYROLL,,2500.00\n",
        encoding="utf-8",
    )
    tx = parse_csv(path)["transactions"][0]
    assert tx["amount"] == 2500.0
    assert tx["direction"] == "credit"
    assert tx["category"] != "Refund / Credit"


def test_rerun_categorization_preserves_reviewed_history(ledger_db):
    for desc, category, source, edited in (
        ("MANUAL SHOP", "Shopping", "user_bulk", "2026-07-17 10:00:00"),
        ("AUTO SHOP", "Uncategorized", "import_rule", None),
    ):
        ok, _ = db.insert_transaction({
            **_tx(desc), "merchant": desc.title(), "category": category,
            "category_source": source, "manually_edited_at": edited,
            "is_transfer": 0,
        }, ledger_db)
        assert ok
    db.upsert_learned_rule("MANUAL SHOP", "Groceries", conn=ledger_db)
    db.upsert_learned_rule("AUTO SHOP", "Groceries", conn=ledger_db)
    ledger_db.commit()

    assert db.rerun_categorization(conn=ledger_db, preserve_manual=True) == 1
    rows = ledger_db.execute(
        "SELECT raw_description, category FROM transactions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("MANUAL SHOP", "Shopping"), ("AUTO SHOP", "Groceries")
    ]

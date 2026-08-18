"""Authoritative financial meaning for SignalSpace transactions.

Storage contract
----------------
``amount`` is the signed account-relative movement: positive means money
entered the account and negative means money left it. ``direction`` mirrors
that source sign for ordinary CSV rows (``credit`` for positive and ``debit``
for negative) while retaining explicit plumbing labels used by older PDF
parsers. Categorization never changes the amount sign.
``transaction_type`` records why the movement happened. Cash-flow reports use
the type, never the amount sign alone.
"""
from __future__ import annotations

from collections import defaultdict
import re
from statistics import median

SPENDING_TYPES = frozenset({
    "spending", "mortgage_payment", "fee_interest", "cash_advance",
})
REFUND_INCOME_TYPES = frozenset({"refund", "reimbursement", "rewards_credit"})
PLANNING_INCOME_TYPES = frozenset({
    "employment_income", "interest_income", "other_income",
})
INCOME_TYPES = frozenset({
    "employment_income", "interest_income", "other_income",
    "shared_reimbursement",
}) | REFUND_INCOME_TYPES
# Compatibility export for older callers. Refund-like credits are income now,
# so there are no transaction types that reduce an unrelated spending bucket.
OFFSET_TYPES = frozenset()
MATERIAL_OFFSET_REVIEW_TYPES = frozenset({"refund", "reimbursement"})
EXCLUDED_TYPES = frozenset({
    "internal_transfer", "credit_card_payment", "investment_transfer",
    "personal_transfer", "cancelled",
})
NET_TYPES = frozenset(INCOME_TYPES | SPENDING_TYPES | OFFSET_TYPES)
MANUAL_TRANSACTION_TYPES = frozenset(
    INCOME_TYPES | SPENDING_TYPES | OFFSET_TYPES | EXCLUDED_TYPES
)
# Bulk merchant corrections intentionally stay narrow. They can safely update
# ordinary cash flow and unresolved positive deposits, but must never sweep a
# payment or transfer into a merchant category just because the text matches.
BULK_CATEGORY_TYPES = frozenset(
    INCOME_TYPES | SPENDING_TYPES | {"ambiguous_inflow"}
)


def transaction_types_for_role(role: str) -> tuple[str, ...]:
    """Return one deterministic type tuple for SQL and API cash-flow filters."""
    groups = {
        "income": INCOME_TYPES,
        "spending": SPENDING_TYPES,
        "spending_offset": OFFSET_TYPES,
        "refund_income": REFUND_INCOME_TYPES,
        "planning_income": PLANNING_INCOME_TYPES,
        "excluded": EXCLUDED_TYPES,
        "net": NET_TYPES,
    }
    return tuple(sorted(groups.get(str(role or "").lower(), frozenset())))


def sql_type_placeholders(types: tuple[str, ...] | frozenset[str]) -> str:
    """Return placeholders for a trusted semantic type collection."""
    return ",".join("?" for _ in types)


def classify_transaction_type(raw_description: str, account_type: str,
                              direction: str, category: str = "") -> str:
    """Return a deterministic type before user merchant rules can override it."""
    desc = (raw_description or "").upper()
    account = (account_type or "").lower()
    cat = category or ""

    if direction == "cancelled" or cat == "Cancelled" or desc.startswith("CANCELLED"):
        return "cancelled"
    if (direction == "payment" or cat in {"Credit Card Payment", "Payment"}
            or (account in {"credit_card", "mastercard"}
                and direction == "credit" and "PAYMENT" in desc
                and "THANK" in desc)
            or "CREDIT CARD PAYMENT" in desc):
        return "credit_card_payment"
    if direction == "transfer" or cat in {"Transfer", "Internal Transfer", "Savings"}:
        return "internal_transfer"
    if any(k in desc for k in (
        "TRANSFER TO SAVINGS", "TRANSFER FROM SAVINGS",
        "DEPOSIT FROM TANGERINE SAVINGS", "WITHDRAWAL TO TANGERINE",
    )):
        return "internal_transfer"
    if cat == "Investments" or any(k in desc for k in (
        "INVESTMENT", "BROKERAGE", "TFSA", "RRSP", "WEALTHSIMPLE",
        "QUESTRADE",
    )):
        return "investment_transfer"
    if "MORTGAGE" in desc or cat == "Housing / Mortgage":
        return "mortgage_payment"
    if "CASH ADVANCE" in desc or "ATM ADVANCE" in desc or cat == "Cash Advance":
        return "cash_advance"
    if cat == "Fees / Interest" or any(k in desc for k in (
        "SERVICE CHARGE", "ANNUAL FEE", "LATE PAYMENT FEE",
        "CASH INTEREST",
    )) or re.search(r"\bNSF\b", desc):
        return "fee_interest"
    if "REIMBURSE" in desc or cat == "Reimbursement / Insurance Reimbursement":
        return "reimbursement"
    if cat == "Refund / Credit":
        return "refund"
    if cat == "Rewards / Cashback" and direction == "credit":
        return "rewards_credit"
    # An Interac e-transfer is ordinary cash flow unless the user explicitly
    # identifies it as movement between their own accounts. The internal
    # categories above deliberately run first so a manual correction remains
    # authoritative.
    if "INTERAC E-TRANSFER" in desc or cat in {"Transfer In", "Transfer Out"}:
        return "other_income" if direction == "credit" else "spending"
    if cat == "Interest Income" and direction == "credit":
        return "interest_income"
    if direction == "credit" and (cat in {"Payroll Income", "Income"} or any(
        k in desc for k in ("PAYROLL", "PAY DEPOSIT", "DIRECT DEPOSIT PAY")
    )):
        return "employment_income"

    # A positive card entry is either a payment or merchant credit. Payment
    # descriptions are protected first; everything else reduces spending.
    if account in {"credit_card", "mastercard"} and direction == "credit":
        return "refund"
    if direction == "debit":
        return "spending"
    # A credit to an asset account is not automatically earned income.
    return "ambiguous_inflow"


def canonicalize_transaction(tx: dict) -> dict:
    """Preserve the signed amount and attach the authoritative type."""
    amount = float(tx.get("amount") or 0.0)
    tx["amount"] = 0.0 if amount == 0 else amount
    tx["transaction_type"] = classify_transaction_type(
        str(tx.get("raw_description") or ""),
        str(tx.get("account_type") or ""),
        str(tx.get("direction") or ""),
        str(tx.get("category") or ""),
    )
    return tx


def cashflow_role(tx: dict) -> str:
    """Return income, spending, excluded, or review."""
    tx_type = str(tx.get("transaction_type") or "")
    if not tx_type:
        tx_type = classify_transaction_type(
            str(tx.get("raw_description") or ""),
            str(tx.get("account_type") or ""),
            str(tx.get("direction") or ""),
            str(tx.get("category") or ""),
        )
    if tx_type in INCOME_TYPES:
        return "income"
    if tx_type in SPENDING_TYPES:
        return "spending"
    if tx_type in EXCLUDED_TYPES:
        return "excluded"
    return "review"


def cashflow_totals(rows: list[dict], *, view: str = "personal") -> dict:
    """Return cash flow while keeping refunds and reimbursements inspectable.

    Refunds, reimbursements, and rewards credits are Money In. They never
    reduce or redistribute spending categories. Partner reimbursements remain
    visible in Money In, while My Share excludes them from net because the
    paired shared spending has already been reduced to the user's portion.
    """
    income = spending_gross = refund_income = reimbursement_total = 0.0
    for tx in rows:
        amount = abs(float(
            tx.get("_cashflow_amount")
            if tx.get("_cashflow_amount") is not None
            else tx.get("amount") or 0.0
        ))
        role = cashflow_role(tx)
        if role == "income":
            income += amount
            if tx.get("transaction_type") in REFUND_INCOME_TYPES:
                refund_income += amount
            if tx.get("transaction_type") == "shared_reimbursement":
                reimbursement_total += amount
        elif role == "spending":
            spending_gross += amount
    spending = spending_gross
    personal_income = income - reimbursement_total if view == "personal" else income
    return {
        "income": round(income, 2),
        "money_in": round(income, 2),
        "income_for_net": round(personal_income, 2),
        "shared_reimbursements": round(reimbursement_total, 2),
        "spending": round(spending, 2),
        "spending_gross": round(spending_gross, 2),
        "refund_income": round(refund_income, 2),
        # Retained so older API consumers do not fail on a missing field.
        "refund_offset": 0.0,
        "net": round(personal_income - spending, 2),
        "savings_rate": round((personal_income - spending) / personal_income * 100, 1)
        if personal_income else 0.0,
    }


def promote_recurring_income(transactions: list[dict]) -> None:
    """Promote stable multi-month asset-account credits to payroll income.

    This is intentionally batch-level evidence. One unexplained deposit is
    never called salary, and transfer/investment/refund patterns are excluded.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for tx in transactions:
        if tx.get("transaction_type") != "ambiguous_inflow":
            continue
        if tx.get("account_type") not in {"chequing", "savings", "cash"}:
            continue
        merchant = str(tx.get("merchant_normalized") or "").strip()
        if merchant:
            groups[merchant].append(tx)
    for rows in groups.values():
        months = {str(r.get("transaction_date") or "")[:7] for r in rows}
        amounts = [abs(float(r.get("amount") or 0)) for r in rows]
        if len(months) < 3 or len(amounts) < 3:
            continue
        midpoint = median(amounts)
        if midpoint <= 0 or max(abs(a - midpoint) / midpoint for a in amounts) > 0.35:
            continue
        for tx in rows:
            tx.update({
                "transaction_type": "employment_income",
                "category": "Payroll Income",
                "subcategory": "Recurring deposit",
                "category_source": "protected",
                "category_explanation": "Recognized from a stable multi-month deposit pattern.",
                "parse_confidence": "high",
                "is_flagged": 0,
                "flag_reason": "",
            })


def consolidate_material_review(transactions: list[dict]) -> None:
    """Keep a batch review queue material and avoid repeated copies.

    Low-value unmatched purchases stay visible as deferred uncertainty. For a
    repeated large uncategorized outflow, only the newest occurrence needs one
    confirmation; that category correction can then become a merchant rule.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for tx in transactions:
        if (tx.get("transaction_type") == "spending"
                and tx.get("category") == "Uncategorized"):
            groups[str(tx.get("merchant_normalized") or "")].append(tx)
    for rows in groups.values():
        material = [r for r in rows if abs(float(r.get("amount") or 0)) >= 500]
        for tx in rows:
            tx["is_flagged"] = 0
            tx["flag_reason"] = ""
        if not material:
            continue
        months = {str(r.get("transaction_date") or "")[:7] for r in material}
        if len(months) >= 3:
            target = max(material, key=lambda r: str(r.get("transaction_date") or ""))
            target["is_flagged"] = 1
            target["flag_reason"] = (
                "confirm this recurring material outflow and choose its "
                "fixed-obligation category"
            )
        else:
            for tx in material:
                tx["is_flagged"] = 1
                tx["flag_reason"] = "large uncategorized purchase"

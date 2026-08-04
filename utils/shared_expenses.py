"""Explicit per-transaction allocation without changing source amounts.

Transactions always retain the exact signed amount imported from the statement.
Automatic merchant, category, and global-default rules are retained in the
schema for compatibility but are not applied to financial totals. A row only
uses a reduced amount when the user explicitly saved a split on that row.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from utils.categorizer import normalize_merchant


SHARE_VIEWS = {"personal", "household"}


def clamp_share(value, default: float = 50.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(min(100.0, max(0.0, number)), 2)


def get_shared_settings(conn) -> dict:
    row = conn.execute(
        "SELECT * FROM shared_expense_settings WHERE id=1"
    ).fetchone()
    if not row:
        return {
            "shared_with_name": "",
            "default_user_share_pct": 50.0,
            "partner_matcher": "",
        }
    result = dict(row)
    result["default_user_share_pct"] = clamp_share(
        result.get("default_user_share_pct"), 50.0
    )
    return result


def get_shared_rules(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM shared_expense_rules WHERE enabled=1 ORDER BY scope_type,scope_value"
    ).fetchall()]


def save_shared_settings(conn, *, shared_with_name: str,
                         default_user_share_pct: float) -> dict:
    name = str(shared_with_name or "").strip()
    pct = clamp_share(default_user_share_pct, 50.0)
    matcher = normalize_merchant(name)
    conn.execute(
        "INSERT INTO shared_expense_settings "
        "(id,shared_with_name,default_user_share_pct,partner_matcher,updated_at) "
        "VALUES (1,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(id) DO UPDATE SET shared_with_name=excluded.shared_with_name,"
        "default_user_share_pct=excluded.default_user_share_pct,"
        "partner_matcher=excluded.partner_matcher,updated_at=excluded.updated_at",
        (name, pct, matcher),
    )
    return get_shared_settings(conn)


def set_shared_rule(conn, *, scope_type: str, scope_value: str,
                    user_share_pct: float, enabled: bool = True) -> None:
    scope = str(scope_type or "").strip().lower()
    if scope not in {"merchant", "recurring", "category"}:
        raise ValueError("Choose a merchant, recurring commitment, or category rule.")
    value = str(scope_value or "").strip()
    if scope in {"merchant", "recurring"}:
        value = normalize_merchant(value)
    if not value:
        raise ValueError("Choose what should be shared.")
    pct = clamp_share(user_share_pct, 50.0)
    conn.execute(
        "INSERT INTO shared_expense_rules "
        "(scope_type,scope_value,user_share_pct,enabled,updated_at) "
        "VALUES (?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(scope_type,scope_value) DO UPDATE SET "
        "user_share_pct=excluded.user_share_pct,enabled=excluded.enabled,"
        "updated_at=excluded.updated_at",
        (scope, value, pct, 1 if enabled else 0),
    )


def delete_shared_rule(conn, *, scope_type: str, scope_value: str) -> bool:
    scope = str(scope_type or "").strip().lower()
    value = str(scope_value or "").strip()
    if scope in {"merchant", "recurring"}:
        value = normalize_merchant(value)
    return bool(conn.execute(
        "DELETE FROM shared_expense_rules WHERE scope_type=? AND scope_value=?",
        (scope, value),
    ).rowcount)


def _rule_maps(rules: Iterable[dict]) -> dict[str, dict[str, dict]]:
    maps = {"merchant": {}, "recurring": {}, "category": {}}
    for raw in rules:
        rule = dict(raw)
        scope = str(rule.get("scope_type") or "")
        if scope in maps:
            maps[scope][str(rule.get("scope_value") or "")] = rule
    return maps


def shared_allocation(tx: dict, conn, *, settings: dict | None = None,
                      rules: list[dict] | None = None) -> dict:
    """Return posted/user/excluded amounts for one transaction.

    ``settings`` and ``rules`` remain accepted so older callers do not break,
    but automatic sharing is deliberately inert. Only transaction-level
    ``shared_expense_override`` plus ``shared_user_share_pct`` changes the
    amount that counts toward the user.
    """
    from utils.financial_semantics import SPENDING_TYPES

    amount = abs(float(tx.get("amount") or tx.get("est_amount") or 0))
    tx_type = str(tx.get("transaction_type") or "")
    eligible = bool(
        tx.get("est_amount") is not None
        or tx_type in SPENDING_TYPES
    )

    override = tx.get("shared_expense_override")
    share_value = tx.get("shared_user_share_pct")
    is_shared = bool(
        eligible and override is not None and bool(int(override))
        and share_value is not None
    )
    source = "transaction" if is_shared else ""
    pct = clamp_share(share_value, 100.0) if is_shared else 100.0
    cents = Decimal("0.01")
    household_decimal = Decimal(str(amount)).quantize(cents, rounding=ROUND_HALF_UP)
    user_decimal = (
        household_decimal * Decimal(str(pct)) / Decimal("100")
    ).quantize(cents, rounding=ROUND_HALF_UP)
    other_decimal = household_decimal - user_decimal
    user = float(user_decimal)
    other = float(other_decimal)
    return {
        "is_shared": is_shared,
        "shared_source": source,
        "user_share_pct": pct,
        "household_cost": float(household_decimal),
        "user_share": user,
        "other_share": other,
    }


def apply_shared_view(rows: Iterable[dict], conn, view: str = "personal") -> list[dict]:
    """Decorate row copies with the effective cash-flow amount for a view."""
    view = view if view in SHARE_VIEWS else "personal"
    output = []
    for raw in rows:
        tx = dict(raw)
        allocation = shared_allocation(tx, conn)
        tx.update({f"_shared_{key}": value for key, value in allocation.items()})
        tx["_cashflow_amount"] = (
            allocation["user_share"] if view == "personal"
            else allocation["household_cost"]
        )
        tx["_share_view"] = view
        output.append(tx)
    return output


def partner_reimbursement_suggestion(tx: dict, conn) -> dict | None:
    """Suggest, but never silently apply, a partner e-transfer reimbursement."""
    if float(tx.get("amount") or 0) <= 0:
        return None
    description = str(tx.get("raw_description") or "")
    if "INTERAC E-TRANSFER" not in description.upper():
        return None
    settings = get_shared_settings(conn)
    partner = str(settings.get("partner_matcher") or "").strip()
    if not partner:
        return None
    merchant = str(
        tx.get("merchant_normalized")
        or normalize_merchant(str(tx.get("merchant") or description))
    )
    if partner not in merchant and partner not in normalize_merchant(description):
        return None
    return {
        "transaction_type": "shared_reimbursement",
        "reason": (
            f"Incoming e-transfer matches {settings.get('shared_with_name') or 'the configured person'}; "
            "confirm only when it repays a shared cost."
        ),
    }


def apply_commitment_share(item: dict, conn) -> dict:
    """Attach personal and household amounts to a recurring commitment."""
    result = dict(item)
    allocation = shared_allocation({
        "amount": result.get("est_amount") or 0,
        "est_amount": result.get("est_amount") or 0,
        "merchant": result.get("merchant") or "",
        "merchant_normalized": (
            result.get("merchant_normalized")
            or normalize_merchant(str(result.get("merchant") or ""))
        ),
        "category": result.get("category") or "Uncategorized",
    }, conn)
    result["household_amount"] = allocation["household_cost"]
    result["est_amount"] = allocation["user_share"]
    result["is_shared"] = allocation["is_shared"]
    result["user_share_pct"] = allocation["user_share_pct"]
    result["other_share"] = allocation["other_share"]
    return result

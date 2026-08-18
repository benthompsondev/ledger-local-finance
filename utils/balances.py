"""
Deterministic account balances and net-worth contributions.

One shared service — pages must not duplicate these formulas.

SignalSpace stores transaction amounts as positive magnitudes with a
`direction` of 'credit' (money in) or 'debit' (money out); the known
exception is Mastercard refunds, historically stored as negative
credits — ABS() normalizes both. 'cancelled' rows never count.

Definitions (per account, per its own currency — no FX invented):

  asset accounts   (chequing, savings, cash, investment, other_asset):
      balance = opening_balance + Σ|credits| − Σ|debits|
      net-worth contribution = +balance

  liability accounts (credit_card, loan, mortgage, other_liability):
      balance owed = opening_balance + Σ|debits| − Σ|credits|
      (purchases/draws increase what you owe; payments/refunds reduce it)
      net-worth contribution = −balance owed

  opening_balance_date: transactions BEFORE the date are excluded from
  the sum (the opening balance already includes them). Without a date,
  every transaction counts.

Only rows in the account's own currency are summed; rows in any other
currency are counted and surfaced so the UI can say "N foreign-currency
transactions excluded" instead of silently mixing currencies.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from utils.database import LIABILITY_ACCOUNT_TYPES


def _conn(conn):
    from utils.database import get_connection
    if conn is not None:
        return conn, False
    return get_connection(), True


def _close(conn, opened):
    if opened:
        try:
            conn.close()
        except Exception:
            pass


def account_balance(account: dict,
                    conn: Optional[sqlite3.Connection] = None) -> dict:
    """Deterministic balance packet for one account (dict from
    list_accounts/get_account)."""
    c, opened = _conn(conn)
    try:
        acct_id = int(account["id"])
        currency = (account.get("currency") or "CAD").upper()
        opening = float(account.get("opening_balance") or 0.0)
        ob_date = account.get("opening_balance_date") or ""

        date_filter = ""
        params: list = [acct_id, currency]
        if ob_date:
            date_filter = " AND transaction_date >= ?"
            params.append(ob_date)

        row = c.execute(
            f"""
            SELECT
              COALESCE(SUM(CASE WHEN direction='credit'
                           THEN ABS(amount) END), 0) AS credits,
              COALESCE(SUM(CASE WHEN direction='debit'
                           THEN ABS(amount) END), 0) AS debits,
              COUNT(*) AS tx_count
            FROM transactions
            WHERE account_ref = ?
              AND direction != 'cancelled'
              AND UPPER(COALESCE(currency,'CAD')) = ?
              {date_filter}
            """,
            params,
        ).fetchone()
        foreign = c.execute(
            """
            SELECT COUNT(*) FROM transactions
            WHERE account_ref = ? AND direction != 'cancelled'
              AND UPPER(COALESCE(currency,'CAD')) != ?
            """,
            (acct_id, currency),
        ).fetchone()[0]

        credits = float(row["credits"] or 0)
        debits = float(row["debits"] or 0)
        is_liability = account.get("type") in LIABILITY_ACCOUNT_TYPES
        if is_liability:
            balance = opening + debits - credits
        else:
            balance = opening + credits - debits

        return {
            "account_id": acct_id,
            "name": account.get("name") or "",
            "type": account.get("type") or "",
            "currency": currency,
            "is_liability": is_liability,
            "balance": round(balance, 2),
            "opening_balance": round(opening, 2),
            "credits": round(credits, 2),
            "debits": round(debits, 2),
            "tx_count": int(row["tx_count"] or 0),
            "foreign_currency_tx": int(foreign or 0),
            "networth_contribution": round(-balance if is_liability
                                           else balance, 2),
        }
    finally:
        _close(c, opened)


def all_account_balances(conn: Optional[sqlite3.Connection] = None,
                         include_archived: bool = False,
                         base_currency: str = "CAD") -> dict:
    """Balances for every account plus totals in the base currency.

    Non-base-currency accounts are never merged into the totals; they
    are returned separately so the UI can present them honestly.
    """
    from utils.database import list_accounts

    c, opened = _conn(conn)
    try:
        accounts = list_accounts(conn=c,
                                 include_archived=include_archived)
        balances = [account_balance(a, conn=c) for a in accounts]
        base = base_currency.upper()
        in_base = [b for b in balances if b["currency"] == base]
        foreign = [b for b in balances if b["currency"] != base]
        assets = sum(b["balance"] for b in in_base
                     if not b["is_liability"])
        liabilities = sum(b["balance"] for b in in_base
                          if b["is_liability"])
        return {
            "balances": balances,
            "base_currency": base,
            "assets_total": round(assets, 2),
            "liabilities_total": round(liabilities, 2),
            "networth_contribution": round(assets - liabilities, 2),
            "foreign_accounts": foreign,
        }
    finally:
        _close(c, opened)

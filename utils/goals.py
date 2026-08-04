"""Deterministic goal, savings, and true-expense calculations.

This module keeps goal progress separate from plan math and then exposes one
small bridge: ``goal_plan_summary``. A saved monthly savings target and the
sum of included goal allocations are never added together. Ledger reserves
the larger value, so named goals can explain the target without double-counting
the same savings intention.
"""
from __future__ import annotations

import calendar
from datetime import date
import sqlite3
from typing import Optional


GOAL_TYPES = {
    "emergency_fund": "Emergency fund",
    "planned_purchase": "Planned purchase",
    "investing": "Investing",
    "true_expense": "Annual or irregular expense",
    "general_savings": "General savings",
    "debt_payoff": "Debt payoff",
    "long_term": "Long-term goal",
}

LEGACY_GOAL_TYPE_MAP = {
    "cash_buffer": "emergency_fund",
    "debt_reduction": "debt_payoff",
    "investment_contribution": "investing",
    "net_worth": "long_term",
    "savings_rate": "general_savings",
    "sub_reduction": "general_savings",
    "custom": "general_savings",
}

PROGRESS_METHODS = {
    "manual": "Manual progress",
    "linked_account": "Linked account balance",
    "legacy_metric": "Legacy computed total",
}

ACTIVE_STATUS = "active"
COMPLETED_STATUSES = {"completed", "done"}
PAUSED_STATUSES = {"paused", "abandoned"}
ARCHIVED_STATUSES = {"archived"}


def _today(value=None) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return date.today()


def canonical_goal_type(value: Optional[str]) -> str:
    raw = (value or "general_savings").strip().lower()
    if raw in GOAL_TYPES:
        return raw
    return LEGACY_GOAL_TYPE_MAP.get(raw, "general_savings")


def goal_type_label(value: Optional[str]) -> str:
    return GOAL_TYPES[canonical_goal_type(value)]


def canonical_status(value: Optional[str]) -> str:
    raw = (value or ACTIVE_STATUS).strip().lower()
    if raw in COMPLETED_STATUSES:
        return "completed"
    if raw in PAUSED_STATUSES:
        return "paused"
    if raw in ARCHIVED_STATUSES:
        return "archived"
    return ACTIVE_STATUS


def _months_including_current(today: date, target: date) -> int:
    if target < today:
        return 0
    return max(1, (target.year - today.year) * 12
               + target.month - today.month + 1)


def _legacy_metric_amount(goal: dict, conn: sqlite3.Connection) -> float:
    link = (goal.get("linked_metric") or "").strip().lower()
    if link == "net_worth":
        from utils.database import compute_net_worth_now
        return float(compute_net_worth_now(conn=conn).get("net_worth") or 0)
    if link == "investments":
        from utils.database import get_latest_investment_snapshot
        snap = get_latest_investment_snapshot(conn=conn)
        return float((snap or {}).get("total_market_value_native") or 0)
    if link == "cash_balance":
        from utils.database import get_account_balances
        rows = get_account_balances(conn=conn, latest_only=True)
        return float(sum(
            float(row.get("balance") or 0)
            for row in rows
            if (row.get("account_kind") or "")
            in {"cash", "chequing", "savings"}
        ))
    return float(goal.get("current_amount") or 0)


def goal_progress(goals: Optional[list[dict]] = None,
                  conn: Optional[sqlite3.Connection] = None,
                  today=None) -> list[dict]:
    """Return inspectable progress packets for goals.

    Linked accounts use only the computed account balance. Manual goals use
    only ``current_amount`` (which contribution recording advances). Liability
    goals show amount paid down as ``target - balance owed`` so users never
    have to reason about reversed signs.
    """
    from utils.database import get_connection, get_goals, list_accounts
    from utils.balances import account_balance

    opened = conn is None
    c = conn or get_connection()
    t = _today(today)
    try:
        rows = list(goals if goals is not None
                    else get_goals(conn=c, status=None))
        accounts = {
            int(a["id"]): a
            for a in list_accounts(conn=c, include_archived=True)
        }
        active_link_counts: dict[int, int] = {}
        for goal in rows:
            if canonical_status(goal.get("status")) != ACTIVE_STATUS:
                continue
            ref = goal.get("linked_account_ref")
            if ref is not None:
                active_link_counts[int(ref)] = (
                    active_link_counts.get(int(ref), 0) + 1
                )

        month_start_date = t.replace(day=1)
        month_start = month_start_date.isoformat()
        month_end = t.replace(
            day=calendar.monthrange(t.year, t.month)[1]
        ).isoformat()
        quarter_start = date(t.year, ((t.month - 1) // 3) * 3 + 1, 1).isoformat()
        contributed = {
            int(r["goal_id"]): float(r["total"] or 0)
            for r in c.execute(
                "SELECT goal_id, SUM(amount) AS total "
                "FROM goal_contributions "
                "WHERE contributed_on BETWEEN ? AND ? GROUP BY goal_id",
                (month_start, month_end),
            ).fetchall()
        }
        contributed_quarter = {
            int(r["goal_id"]): float(r["total"] or 0)
            for r in c.execute(
                "SELECT goal_id, SUM(amount) AS total FROM goal_contributions "
                "WHERE contributed_on BETWEEN ? AND ? GROUP BY goal_id",
                (quarter_start, t.isoformat()),
            ).fetchall()
        }
        contribution_months: dict[int, set[str]] = {}
        for row in c.execute(
            "SELECT goal_id, substr(contributed_on,1,7) AS month "
            "FROM goal_contributions WHERE amount>0 GROUP BY goal_id,month"
        ).fetchall():
            contribution_months.setdefault(int(row["goal_id"]), set()).add(
                str(row["month"])
            )

        output: list[dict] = []
        for goal in rows:
            result = dict(goal)
            kind = canonical_goal_type(goal.get("type"))
            status = canonical_status(goal.get("status"))
            method = (goal.get("progress_method") or "manual").strip().lower()
            if method not in PROGRESS_METHODS:
                method = "manual"

            target = max(0.0, float(goal.get("target_amount") or 0))
            current = max(0.0, float(goal.get("current_amount") or 0))
            currency = (goal.get("currency") or "CAD").strip().upper() or "CAD"
            source_label = "Manual progress"
            source_available = True
            linked_balance = None
            account = None
            source_warning = ""
            data_stale = False

            if method == "linked_account":
                ref = goal.get("linked_account_ref")
                account = accounts.get(int(ref)) if ref is not None else None
                if not account:
                    current = 0.0
                    source_available = False
                    source_label = "Linked account unavailable"
                    source_warning = (
                        "Choose an available account or switch to manual progress."
                    )
                else:
                    balance = account_balance(account, conn=c)
                    linked_balance = float(balance.get("balance") or 0)
                    currency = (balance.get("currency") or currency).upper()
                    source_label = f"{account.get('name') or 'Linked account'} balance"
                    if kind == "debt_payoff":
                        if not balance.get("is_liability"):
                            current = 0.0
                            source_available = False
                            source_warning = (
                                "Debt payoff goals must link a debt account."
                            )
                        else:
                            current = max(0.0, target - max(0.0, linked_balance))
                    elif balance.get("is_liability"):
                        current = 0.0
                        source_available = False
                        source_warning = (
                            "Savings goals must link an asset account, not a debt account."
                        )
                    else:
                        current = max(0.0, linked_balance)

                    # list_accounts has already capped this at Northstar's
                    # supported data date.
                    latest_value = account.get("last_activity")
                    if latest_value:
                        data_stale = (
                            t - date.fromisoformat(str(latest_value)[:10])
                        ).days > 14
                    if int(account.get("is_archived") or 0):
                        source_warning = (
                            "The linked account is archived. Progress is shown, "
                            "but restore or relink it before relying on future updates."
                        )
            elif method == "legacy_metric":
                current = max(0.0, _legacy_metric_amount(goal, c))
                source_label = "Legacy computed total"

            # Completing a goal is an explicit user decision. Present that
            # lifecycle state consistently as 100% even when the last manual
            # contribution was not entered separately.
            if status == "completed" and target > 0:
                current = max(current, target)
            gap = max(0.0, target - current)
            pct = min(1.0, current / target) if target > 0 else 0.0
            target_day = None
            invalid_target_date = False
            if goal.get("target_date"):
                try:
                    target_day = date.fromisoformat(str(goal["target_date"])[:10])
                except ValueError:
                    invalid_target_date = True
            months_remaining = (
                _months_including_current(t, target_day)
                if target_day else None
            )
            required_pace = (
                gap / max(1, months_remaining)
                if target_day and months_remaining is not None else None
            )
            planned = max(
                0.0, float(goal.get("planned_monthly_contribution") or 0)
            )
            frequency = str(goal.get("contribution_frequency") or "monthly").lower()
            if frequency not in {"monthly", "quarterly"}:
                frequency = "monthly"
            if status != ACTIVE_STATUS:
                next_contribution = 0.0
            else:
                next_contribution = min(
                    gap,
                    planned if planned > 0
                    else (required_pace if required_pace is not None else gap),
                )

            if gap <= 0 and target > 0:
                pace_state = "complete"
            elif data_stale or not source_available:
                pace_state = "unknown"
            elif target_day and months_remaining == 0:
                pace_state = "overdue"
            elif required_pace is None:
                pace_state = "no_date"
            elif planned >= required_pace:
                pace_state = "on_track"
            else:
                pace_state = "behind"

            actual_this_month = contributed.get(int(goal["id"]), 0.0)
            actual_this_period = (
                contributed_quarter.get(int(goal["id"]), 0.0)
                if frequency == "quarterly" else actual_this_month
            )
            if status != ACTIVE_STATUS or planned <= 0:
                contribution_state = "not_planned"
            elif method != "manual":
                contribution_state = "unverified"
            elif actual_this_period >= planned:
                contribution_state = "met"
            elif actual_this_period > 0:
                contribution_state = "partial"
            elif t.day > 7:
                contribution_state = "missed"
            else:
                contribution_state = "pending"

            duplicate_link = bool(
                account and active_link_counts.get(int(account["id"]), 0) > 1
            )
            streak = 0
            cursor_year, cursor_month = t.year, t.month
            months_with_real_contributions = contribution_months.get(
                int(goal["id"]), set()
            )
            while f"{cursor_year:04d}-{cursor_month:02d}" in months_with_real_contributions:
                streak += 1
                cursor_month -= 1
                if cursor_month == 0:
                    cursor_year -= 1
                    cursor_month = 12
            result.update({
                "type": kind,
                "type_label": GOAL_TYPES[kind],
                "status": status,
                "progress_method": method,
                "progress_method_label": PROGRESS_METHODS[method],
                "current_amount": round(current, 2),
                "target_amount": round(target, 2),
                "progress_pct": round(pct, 4),
                "gap": round(gap, 2),
                "currency": currency,
                "linked_account": account,
                "linked_balance": (round(linked_balance, 2)
                                   if linked_balance is not None else None),
                "source_label": source_label,
                "source_available": source_available,
                "source_warning": source_warning,
                "data_stale": data_stale,
                "foreign_currency": currency != "CAD",
                "duplicate_link_warning": duplicate_link,
                "months_remaining": months_remaining,
                "required_monthly_pace": (round(required_pace, 2)
                                           if required_pace is not None else None),
                "planned_monthly_contribution": round(planned, 2),
                "contribution_frequency": frequency,
                "contribution_target": round(planned, 2),
                "next_contribution": round(next_contribution, 2),
                "pace_state": pace_state,
                "contributed_this_month": round(actual_this_month, 2),
                "contributed_this_period": round(actual_this_period, 2),
                "contribution_period_label": (
                    "this quarter" if frequency == "quarterly" else "this month"
                ),
                "contribution_state": contribution_state,
                "invalid_target_date": invalid_target_date,
                "milestone": (
                    "Complete" if pct >= 1
                    else "Three quarters there" if pct >= 0.75
                    else "Halfway there" if pct >= 0.50
                    else "First quarter reached" if pct >= 0.25
                    else "Getting started"
                ),
                "show_milestones": bool(goal.get("show_milestones", 1)),
                "contribution_streak_months": streak,
            })
            output.append(result)
        return output
    finally:
        if opened:
            c.close()


def goal_plan_summary(plan_savings_target: float = 0,
                      conn: Optional[sqlite3.Connection] = None,
                      today=None) -> dict:
    """Reconcile named goal allocations with the headline savings target.

    ``effective_savings_target`` is the only value Safe to Spend should
    subtract. It is ``max(plan target, included goal allocations)`` rather
    than their sum, which avoids double-counting one savings intention.
    """
    from utils.database import get_connection, get_goals

    opened = conn is None
    c = conn or get_connection()
    try:
        goals = get_goals(conn=c, status=None)
        progress_by_id = {
            int(row["id"]): row
            for row in goal_progress(goals, conn=c, today=today)
        }
        allocations = []
        foreign_allocations = []
        for goal in goals:
            if canonical_status(goal.get("status")) != ACTIVE_STATUS:
                continue
            if not int(goal.get("include_in_plan")
                       if goal.get("include_in_plan") is not None else 1):
                continue
            amount = max(
                0.0, float(goal.get("planned_monthly_contribution") or 0)
            )
            if str(goal.get("contribution_frequency") or "monthly") == "quarterly":
                amount = round(amount / 3.0, 2)
            if amount <= 0:
                continue
            allocation = {
                "goal_id": int(goal["id"]),
                "name": goal.get("name") or "Goal",
                "amount": round(amount, 2),
                "contributed_this_month": round(float(
                    progress_by_id.get(int(goal["id"]), {}).get(
                        "contributed_this_month"
                    ) or 0
                ), 2),
                "currency": (goal.get("currency") or "CAD").upper(),
            }
            allocation["remaining"] = round(max(
                0.0,
                allocation["amount"] - allocation["contributed_this_month"],
            ), 2)
            if allocation["currency"] != "CAD":
                foreign_allocations.append(allocation)
            else:
                allocations.append(allocation)
        allocated = round(sum(row["amount"] for row in allocations), 2)
        headline = max(0.0, float(plan_savings_target or 0))
        effective = max(headline, allocated)
        contributed = round(sum(
            min(float(row["amount"]), float(row["contributed_this_month"]))
            for row in allocations
        ), 2)
        remaining = round(max(0.0, effective - contributed), 2)
        return {
            "headline_savings_target": round(headline, 2),
            "goal_allocations": allocations,
            "goal_allocations_total": allocated,
            "foreign_allocations": foreign_allocations,
            "unallocated_savings": round(max(0.0, headline - allocated), 2),
            "allocation_above_plan": round(max(0.0, allocated - headline), 2),
            "effective_savings_target": round(effective, 2),
            "contributed_this_month": contributed,
            "remaining_savings_target": remaining,
        }
    finally:
        if opened:
            c.close()


def home_goal_summary(conn: Optional[sqlite3.Connection] = None,
                      today=None) -> Optional[dict]:
    """Choose one useful active goal for Home without building a card wall."""
    from utils.database import get_connection, get_goals

    opened = conn is None
    c = conn or get_connection()
    t = _today(today)
    try:
        active = goal_progress(
            get_goals(conn=c, status="active"), conn=c, today=t
        )
        if not active:
            return None

        def rank(goal: dict):
            target = goal.get("target_date") or "9999-12-31"
            return (
                0 if goal.get("pace_state") == "overdue" else 1,
                0 if goal.get("type") == "emergency_fund" else 1,
                target,
                -float(goal.get("gap") or 0),
            )

        chosen = sorted(active, key=rank)[0]
        summary = dict(chosen)
        summary["stale_warning"] = (
            "Import or refresh this account before treating the pace as current."
            if chosen.get("data_stale") else ""
        )
        return summary
    finally:
        if opened:
            c.close()

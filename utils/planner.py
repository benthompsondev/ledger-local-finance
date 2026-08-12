"""
utils/planner.py — Pass 21 monthly operating loop (deterministic).

What this is
────────────
A pure-Python module that turns Ledger's existing analytics into the
inputs a user needs to plan a month, forecast it, watch their bills,
and track goals. Every function here is deterministic — no AI, no
internet calls, no live price lookup, no DB mutation.

What this is NOT
────────────────
- It is not a write surface. The Month Plan page does the writes;
  this module only reads + computes.
- It does not invent numbers. When data is missing or thin, helpers
  return shapes that say so (e.g. `mode='insufficient_data'`,
  `risk_level='insufficient_data'`).
- It does not call MiniMax. The page may layer an explanation on top,
  but every number on screen comes from here.

Public surface
──────────────
- analysis_anchor(conn) → str (YYYY-MM)
        The month we treat as "current" for analysis. Latest imported
        month if it's recent enough; otherwise today's calendar month.
- recent_category_averages(months=3, anchor_month=None, conn=None)
        Average monthly spending per category over a lookback window
        anchored to the latest imported month (NOT today). Filters out
        non-consumption categories.
- generate_starter_plan(mode, conn=None)
        Returns a complete plan proposal for the analysis-anchor month
        based on `mode`. Includes targets, focus categories, top moves,
        risk warning, win condition. Never persists.
- bills_and_commitments(conn=None)
        Combines subscription_detective + recurring_merchants into a
        single list with frequency, last_seen, expected_next, included
        flag, and confidence.
- forecast_month(plan_month=None, conn=None)
        Projects month-end spending/income/net based on MTD pace +
        recurring commitments yet to hit.
- safe_to_spend(plan, conn=None)
        Given a saved plan + forecast, returns the dollars the user
        can still spend this month without breaking the plan.
- goal_progress(goals, conn=None)
        For each goal, computes current_amount (auto-derived where
        linked_metric is set) and a percent + next milestone hint.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

# Local imports kept inside functions where practical to avoid
# import cycles (analytics imports from database, insights imports
# from both; planner sits "above" them all).


# ── Mode catalog ────────────────────────────────────────────────────
# Each mode tells generate_starter_plan how to bend its targets. Cuts
# are expressed as a fraction of the controllable-category average.

PLAN_MODES = {
    "normal": {
        "label":           "Normal Month",
        "controllable_cut": 0.05,
        "target_savings_rate": 0.15,
        "win_condition":   "End the month with savings_rate ≥ 15%.",
        "risk":             "Comfortable mode — drifts if subscriptions creep.",
    },
    "tight": {
        "label":           "Tight Month",
        "controllable_cut": 0.20,
        "target_savings_rate": 0.20,
        "win_condition":   "Cut controllables by 20% AND keep savings rate ≥ 20%.",
        "risk":             "Cuts feel fast; one impulse buy can break the plan.",
    },
    "reset": {
        "label":           "Reset Month",
        "controllable_cut": 0.10,
        "target_savings_rate": 0.10,
        "win_condition":   "No new subscriptions. Clear the Review queue.",
        "risk":             "Easy to coast and skip the cleanup work.",
    },
    "aggressive_save": {
        "label":           "Aggressive Save Month",
        "controllable_cut": 0.30,
        "target_savings_rate": 0.30,
        "win_condition":   "Hit 30% savings rate. Move the surplus to investments.",
        "risk":             "Burnout risk — pair with a Normal Month next.",
    },
    "sub_cleanup": {
        "label":           "Subscription Cleanup Month",
        "controllable_cut": 0.10,
        "target_savings_rate": 0.15,
        "win_condition":   "Cancel ≥ 2 active subscription candidates.",
        "risk":             "Subs that auto-renew mid-month leak money anyway.",
    },
    "debt_recovery": {
        "label":           "Debt / Fee Recovery Month",
        "controllable_cut": 0.15,
        "target_savings_rate": 0.05,
        "win_condition":   "Zero cash advances and zero new fees.",
        "risk":             "If income missed last month, plan stays defensive.",
    },
    "stabilize": {
        "label":           "Stabilize Month",
        "controllable_cut": 0.00,
        "target_savings_rate": 0.10,
        "win_condition":   "Hold spending at recent average. No new commitments.",
        "risk":             "Holding pattern — only useful for one month.",
    },
}

# Categories that are essentially fixed costs. We don't propose
# aggressive cuts for these in the wizard; we recommend reviewing them.
FIXED_CATEGORIES = {
    "Housing / Mortgage", "Utilities / Bills",
    "Health / Care", "Pets",
}

# Categories that vary heavily month-to-month — propose a "watch"
# target instead of a hard cut.
VOLATILE_CATEGORIES = {
    "Home Improvement", "Misc",
}

# ── Pass 23: commitment classifier ──────────────────────────────────
# Bills/forecast truth-layer fix. The previous version treated every
# merchant that recurred ≥3 months as a "commitment" and rolled it
# into upcoming_bills_total. That over-counted Groceries / Shopping /
# Home Improvement / Gas / person-to-person transfers and inflated
# forecast risk + safe_to_spend.
#
# Classification rules:
#   FIXED_COMMITMENT_CATEGORIES    → fixed_commitments (in forecast)
#   active subscription_detective  → active_subscriptions (in forecast)
#   recurring + variable retail    → recurring_variable_merchants (watch only)
#   subscription_detective stale   → stale_or_inactive (excluded)
#   non-cashflow / transfer / etc. → never counted

# True fixed bills. Detected primarily by category — categorization is
# already the truth layer for all other Ledger math, so we lean on it
# here too. A merchant repeating in one of these categories represents
# a real recurring obligation.
_FIXED_COMMITMENT_CATEGORIES = {
    "Housing / Mortgage",
    "Utilities / Bills",
    # Insurance is its own category in config/categories.py, and leaving it
    # out meant a quarterly premium was excluded from the plan even after
    # the user confirmed it as recurring.
    "Insurance",
    "Debt / Loan Payment",
    "Childcare",
    "Education",
}

# Variable retail / consumption — these can recur monthly without
# being bills. They get *watched* but never locked into the forecast.
_VARIABLE_RETAIL_CATEGORIES = {
    "Groceries",
    "Food & Convenience",
    "Shopping",
    "Home Improvement",
    "Gas / Transport",
    "Entertainment",
    "Health / Care",        # variable until user reclassifies as Bill
    "Pets",                 # variable: vet bills aren't recurring
    "Cash Advance",
    "Fees / Interest",
    "Misc",
    "Uncategorized",
    None,                   # treat NULL category as variable-watch
    "",
}

# Categories that must NEVER be classified as a commitment. These are
# accounting plumbing — including them would double-count cashflow.
_NEVER_COMMITMENT_CATEGORIES = {
    "Transfer", "Transfer In", "Transfer Out", "Internal Transfer",
    "Credit Card Payment", "Cancelled", "Refund / Credit",
    "Savings", "Investments",
    "Income", "Payroll Income", "Interest Income",
    "Rewards / Cashback",
    "Reimbursement / Insurance Reimbursement",
}


def _classify_commitment(category: Optional[str]) -> str:
    """Return one of: 'fixed', 'variable', 'never'.

    Subscriptions are handled separately by subscription_detective and
    don't pass through here.
    """
    cat = category if category else None
    if cat in _NEVER_COMMITMENT_CATEGORIES:
        return "never"
    if cat in _FIXED_COMMITMENT_CATEGORIES:
        return "fixed"
    # Anything not explicitly fixed is treated as variable retail. This
    # is intentionally conservative — better to under-count commitments
    # than to inflate them.
    return "variable"


# ── Anchor / window helpers ────────────────────────────────────────

def analysis_anchor(conn: Optional[sqlite3.Connection] = None,
                    today=None) -> str:
    """Return 'YYYY-MM' to treat as the current analysis month.

    The month of the last date the data genuinely reaches, falling back to
    the calendar month when that is more than five weeks behind, so a stale
    import is never described as "this month".

    Reads the shared context rather than ``MAX(transaction_date)``: one
    legacy row dated 2035 used to move Plan five hundred weeks into the
    future while the pattern detectors, which already had this guard,
    correctly stayed in the present.
    """
    from utils.analysis_period import analysis_month

    return analysis_month(conn=conn, today=today)


def _month_bounds(month: str) -> tuple[str, str, int]:
    """Return (start_date, end_date, days_in_month)."""
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{month}-01", f"{month}-{last:02d}", last


# ── Recent averages (per category) ──────────────────────────────────

def recent_category_averages(months: int = 3, anchor_month: Optional[str] = None,
                             conn: Optional[sqlite3.Connection] = None
                             ) -> list[dict]:
    """Average monthly spending per non-consumption-excluded category
    over the `months` complete months ENDING WITH (but not including)
    the anchor month. So if anchor is 2026-05, this returns the avg
    over 2026-02, 2026-03, 2026-04 by default.

    Returns: [{category, monthly_avg, total, months_with_data}, ...]
    sorted by monthly_avg desc.
    """
    from utils.database import get_connection
    from config.categories import NON_CONSUMPTION_CATEGORIES

    close = conn is None
    if close:
        conn = get_connection()

    if not anchor_month:
        anchor_month = analysis_anchor(conn=conn)

    y, m = map(int, anchor_month.split("-"))
    # End is the month BEFORE the anchor.
    em_y, em_m = (y, m - 1) if m > 1 else (y - 1, 12)
    end_first = f"{em_y:04d}-{em_m:02d}-01"
    end_last_day = calendar.monthrange(em_y, em_m)[1]
    end_iso = f"{em_y:04d}-{em_m:02d}-{end_last_day:02d}"

    # Start is N-1 months before that.
    sm_y, sm_m = em_y, em_m - (months - 1)
    while sm_m <= 0:
        sm_m += 12
        sm_y -= 1
    start_iso = f"{sm_y:04d}-{sm_m:02d}-01"

    from collections import defaultdict
    from utils.database import get_category_totals

    totals: dict[str, float] = defaultdict(float)
    months_seen: dict[str, int] = defaultdict(int)
    cursor_y, cursor_m = sm_y, sm_m
    for _ in range(months):
        month_label = f"{cursor_y:04d}-{cursor_m:02d}"
        month_end = calendar.monthrange(cursor_y, cursor_m)[1]
        for row in get_category_totals(
            f"{month_label}-01", f"{month_label}-{month_end:02d}", conn=conn
        ):
            category = row["category"]
            if category in NON_CONSUMPTION_CATEGORIES:
                continue
            totals[category] += float(row["total"] or 0)
            months_seen[category] += 1
        cursor_m += 1
        if cursor_m > 12:
            cursor_m = 1
            cursor_y += 1

    if close:
        conn.close()

    out = []
    for category, total in sorted(totals.items(), key=lambda item: -item[1]):
        m_with = max(months_seen[category], 1)
        out.append({
            "category":         category,
            "total":            total,
            "months_with_data": months_seen[category],
            "monthly_avg":      total / m_with,
        })
    return out


# ── Bills & commitments ─────────────────────────────────────────────

def bills_and_commitments(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Pass 23 — split bills/commitments into four groups.

    Groups:
      fixed_commitments         — Housing/Mortgage, Utilities/Bills
                                  recurring merchants. Locked into
                                  forecast.
      active_subscriptions      — subscription_detective active
                                  candidates. Locked into forecast.
      recurring_variable_merchants
                                — Groceries / Shopping / Gas / etc.
                                  that recur but aren't bills. Watched
                                  separately, NOT in forecast.
      stale_or_inactive         — subscription_detective stale items.
                                  Excluded entirely.

    Forecast totals:
      commitment_monthly_estimate = fixed + active subs (locked).
      variable_monthly_watch      = recurring variable retail (watched).

    Backward-compatible top-level keys (`items`, `monthly_estimate`,
    `count`, `anchor_month`) are preserved. `monthly_estimate` now
    equals `commitment_monthly_estimate` (NOT the old "every recurring
    merchant" sum) so callers that already used it as the forecast
    bills figure get the corrected number automatically. Each item in
    `items` carries a `group` field and an `included_in_forecast`
    flag.
    """
    from utils.insights import subscription_detective, recurring_merchants
    from utils.database import get_connection

    close = conn is None
    if close:
        conn = get_connection()

    anchor = analysis_anchor(conn=conn)
    today = date.today()

    sub = subscription_detective(conn=conn) or {}
    sub_active   = list(sub.get("active_candidates")  or [])
    sub_stale    = list(sub.get("stale_candidates")   or [])
    rec          = recurring_merchants(min_months=3, conn=conn) or []

    seen: dict[str, dict] = {}

    def _add(item: dict) -> None:
        key = (item.get("merchant") or "").upper()
        if not key:
            return
        if key in seen:
            return
        seen[key] = item

    # 1. Active subscriptions — always commitments.
    for c in sub_active:
        last_seen = c.get("last_seen") or ""
        try:
            last_dt = date.fromisoformat(last_seen) if last_seen else None
        except Exception:
            last_dt = None
        expected_next = None
        if last_dt:
            nxt = last_dt + timedelta(days=30)
            if nxt >= today.replace(day=1):
                expected_next = nxt.isoformat()
        _add({
            "merchant":      c.get("merchant"),
            "category":      c.get("category") or "Subscriptions & Digital",
            "est_amount":    float(c.get("avg_amount") or 0),
            "frequency":     "monthly",
            "last_seen":     last_seen,
            "expected_next": expected_next,
            "confidence":    "high",
            "active":        True,
            "included_in_forecast": True,
            "group":         "active_subscriptions",
            "reason":        "Active subscription (subscription_detective).",
            "source":        "subscription_active",
            "recurring_status": "automatic",
        })

    # 2. Stale subscriptions — never in forecast.
    for c in sub_stale:
        _add({
            "merchant":      c.get("merchant"),
            "category":      c.get("category") or "Subscriptions & Digital",
            "est_amount":    float(c.get("avg_amount") or 0),
            "frequency":     "monthly",
            "last_seen":     c.get("last_seen") or "",
            "expected_next": None,
            "confidence":    "low",
            "active":        False,
            "included_in_forecast": False,
            "group":         "stale_or_inactive",
            "reason":        "Stale subscription (no recent charge).",
            "source":        "subscription_stale",
            "recurring_status": "excluded",
        })

    # 3. Recurring merchants that aren't subscriptions. Classified by
    #    category into fixed vs variable.
    for r in rec:
        if (r.get("merchant") or "").upper() in seen:
            continue
        ms = int(r.get("months_seen") or 0)
        conf = "high" if ms >= 4 else ("medium" if ms >= 3 else "low")
        cls = _classify_commitment(r.get("category"))
        if cls == "never":
            # Transfer / Income / etc. — not a bill. Skip entirely
            # so it doesn't pollute either group.
            continue
        if cls == "fixed":
            group = "fixed_commitments"
            included = True
            reason = (
                f"Fixed commitment by category "
                f"({r.get('category')}); recurs {ms} mo."
            )
        else:
            group = "recurring_variable_merchants"
            included = False
            reason = (
                f"Variable retail ({r.get('category') or 'uncategorized'}); "
                f"recurs {ms} mo — watched, not locked."
            )
        _add({
            "merchant":      r.get("merchant"),
            "merchant_normalized": r.get("merchant_normalized"),
            "category":      r.get("category"),
            "est_amount":    float(r.get("avg_amount") or 0),
            "frequency":     "monthly" if ms >= 3 else "irregular",
            # Preserve the detector's latest observed charge. Safe to Spend
            # uses this to avoid reserving a fixed bill a second time after it
            # has already cleared in the current month.
            "last_seen":     r.get("last_seen") or "",
            "expected_next": None,
            "confidence":    conf,
            "active":        True,
            "included_in_forecast": included,
            "group":         group,
            "reason":        reason,
            "source":        "recurring_merchant",
            # Authoritative user-review state from find_recurring(). Keep an
            # explicit automatic value so missing data is never itself used
            # as the definition of "unreviewed" downstream.
            "recurring_status": r.get("recurring_status") or "automatic",
        })

    # 4. Cadence. The monthly detector above groups by calendar month, so it
    #    can only ever see monthly rhythms; anything billed quarterly or
    #    annually was invisible to the whole app and nothing was reserved
    #    for it. Reading the gaps between charges finds those, and gives
    #    every commitment a real cadence instead of the None this used to
    #    hand downstream.
    from utils.recurring_cadence import merchant_cadences

    for row in merchant_cadences(conn=conn):
        key = (row.get("merchant") or "").upper()
        cadence = row["cadence"]
        existing = seen.get(key)
        if not row.get("is_active", True):
            # Cancelled, paid off, or moved elsewhere. Reserving for a bill
            # that has stopped coming quietly locks away money the user
            # could be spending, and nothing would ever release it.
            if existing is not None:
                existing["included_in_forecast"] = False
                existing["group"] = "stale_or_inactive"
                existing["monthly_setaside"] = 0.0
                existing["cadence"] = cadence
                existing["period_months"] = row["period_months"]
                existing["reason"] = (
                    f"No charge for {row['overdue_days']} days; not reserved."
                )
            continue
        if existing is not None:
            existing["cadence"] = cadence
            existing["period_months"] = row["period_months"]
            existing["monthly_setaside"] = row["monthly_setaside"]
            existing["setaside_note"] = row["setaside_note"]
            existing["price_change"] = row["price_change"]
            existing["occurrences"] = row["occurrences"]
            if not existing.get("expected_next"):
                existing["expected_next"] = row["expected_next"]
            # A price that has risen makes the running average an amount
            # that was never actually charged. Plan for what it costs now.
            if row["price_change"] or cadence != "monthly":
                existing["est_amount"] = row["est_amount"]
            continue

        if cadence == "monthly":
            continue  # the monthly detector already had its say
        cls = _classify_commitment(row.get("category"))
        if cls != "fixed":
            continue  # a weekly coffee habit is not a bill
        _add({
            "merchant":      row["merchant"],
            "merchant_normalized": row["merchant_normalized"],
            "category":      row["category"],
            "est_amount":    row["est_amount"],
            "frequency":     cadence,
            "cadence":       cadence,
            "period_months": row["period_months"],
            "monthly_setaside": row["monthly_setaside"],
            "setaside_note": row["setaside_note"],
            "price_change":  row["price_change"],
            "occurrences":   row["occurrences"],
            "last_seen":     row["last_seen"],
            "expected_next": row["expected_next"],
            "confidence":    row["confidence"],
            "active":        True,
            "included_in_forecast": True,
            "group":         "nonmonthly_commitments",
            "reason":        row["setaside_note"],
            "source":        "cadence",
            "recurring_status": "automatic",
        })

    # Anything the cadence pass did not reach still needs the fields its
    # callers now read, rather than a None that quietly breaks arithmetic.
    for item in seen.values():
        item.setdefault("cadence", "monthly")
        item.setdefault("period_months", 1)
        item.setdefault(
            "monthly_setaside", float(item.get("est_amount") or 0),
        )
        item.setdefault("price_change", None)
        item.setdefault("setaside_note", "")

    from utils.shared_expenses import apply_commitment_share
    items = [apply_commitment_share(item, conn) for item in seen.values()]
    items.sort(key=lambda x: -float(x.get("monthly_setaside") or 0))

    # Group splits.
    fixed_commitments = [i for i in items
                         if i["group"] == "fixed_commitments"]
    active_subs       = [i for i in items
                         if i["group"] == "active_subscriptions"]
    nonmonthly        = [i for i in items
                         if i["group"] == "nonmonthly_commitments"]
    variable_watch    = [i for i in items
                         if i["group"] == "recurring_variable_merchants"]
    stale_inactive    = [i for i in items
                         if i["group"] == "stale_or_inactive"]

    # Reserve the monthly share, not the amount on the invoice. A $540 bill
    # every three months costs $180 a month; charging the plan $540 every
    # month would be as wrong as charging it nothing.
    commitment_total = sum(
        float(i.get("monthly_setaside") or i.get("est_amount") or 0)
        for i in fixed_commitments + active_subs + nonmonthly
    )
    nonmonthly_total = sum(
        float(i.get("monthly_setaside") or 0) for i in nonmonthly
    )
    variable_total   = sum(float(i.get("est_amount") or 0)
                           for i in variable_watch)

    if close:
        conn.close()

    return {
        # Backward-compatible keys (now corrected — only commitments).
        "items":            items,
        "monthly_estimate": float(commitment_total),
        "count":            len(items),
        "anchor_month":     anchor,
        # Pass 23 grouped fields.
        "fixed_commitments":            fixed_commitments,
        "active_subscriptions":         active_subs,
        "nonmonthly_commitments":       nonmonthly,
        "recurring_variable_merchants": variable_watch,
        "stale_or_inactive":            stale_inactive,
        "commitment_monthly_estimate":  float(commitment_total),
        "nonmonthly_monthly_reserve":   round(float(nonmonthly_total), 2),
        "nonmonthly_annual_total":      round(sum(
            float(i.get("monthly_setaside") or 0) * 12 for i in nonmonthly
        ), 2),
        "variable_monthly_watch":       float(variable_total),
        "commitment_count":             (
            len(fixed_commitments) + len(active_subs) + len(nonmonthly)
        ),
        "nonmonthly_count":             len(nonmonthly),
        "variable_count":               len(variable_watch),
        "price_changes": [
            {"merchant": i.get("merchant"), **(i.get("price_change") or {})}
            for i in items if i.get("price_change")
        ],
    }


def regular_monthly_fixed_total(bills: dict) -> float:
    """Monthly fixed costs that recur every month, excluding nonmonthly bills.

    Plan subtracts nonmonthly commitments through `nonmonthly_monthly_reserve`,
    which spreads a quarterly or annual invoice across the months it covers.
    Summing every commitment's `est_amount` into fixed costs and then also
    subtracting the reserve charged the plan twice for the same bill: a $300
    quarterly invoice with a $100 reserve removed $400 a month instead of $100.

    This is the one basis for "regular monthly fixed costs". The reserve stays
    separate and is added by the equation.
    """
    return round(sum(
        float(item.get("monthly_setaside") or item.get("est_amount") or 0)
        for item in bills.get("items", [])
        if item.get("included_in_forecast")
        and item.get("group") != "nonmonthly_commitments"
    ), 2)


# Cadences that bill at least once a month. If one of these has not been seen
# yet this month it is probably still coming, which is all the monthly rule
# ever needed to know.
_MONTHLY_CADENCES = {"weekly", "biweekly", "monthly"}


def commitment_placement(item: dict, plan_month: str) -> str:
    """Where a reviewed commitment sits relative to ``plan_month``.

    One of 'paid', 'due', 'overdue' or 'other_month'.

    The forecast used to ask only whether a merchant had been seen since the
    1st. That reads a monthly bill correctly and reads every slower bill
    wrongly: an annual $2,400 property-tax invoice due in November had not
    been seen this month in January either, so it was charged in full against
    January, and February, and every other month of the year. A quarterly
    premium did the same thing nine months out of twelve. Both inflated
    projected spending badly enough to turn an ordinary month into a danger
    verdict.

    A slow bill is therefore placed by the occurrence the cadence detector
    expects, not by silence.
    """
    month_start = f"{plan_month}-01"
    last_seen = str(item.get("last_seen") or "")[:10]
    if last_seen and last_seen >= month_start:
        # Already cleared inside the forecast month. Counting it again would
        # charge the same rent twice.
        return "paid"

    cadence = str(item.get("cadence") or "monthly")
    try:
        period_months = float(item.get("period_months") or 1)
    except (TypeError, ValueError):
        period_months = 1.0
    if cadence in _MONTHLY_CADENCES and period_months <= 1:
        return "due"

    expected_month = str(item.get("expected_next") or "")[:7]
    if not expected_month:
        # A slow bill nobody can place in time cannot be charged to this
        # month. Guessing costs the user real money in eleven months out
        # of twelve.
        return "other_month"
    if expected_month == plan_month:
        return "due"
    if expected_month < plan_month:
        return "overdue"
    return "other_month"


def plan_commitment_agreement(
    plan: Optional[dict],
    *,
    bills: Optional[dict] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Compare saved fixed costs with the currently reviewed commitments.

    A deliberate override is durable only while the detected commitment
    baseline has not changed. New or removed recurring costs therefore reopen
    review without silently erasing the user's chosen amount or explanation.
    """
    bills = bills or bills_and_commitments(conn=conn)
    # Same basis the plan equation uses, so agreement and drift are measured
    # against the number actually shown as fixed costs.
    detected = regular_monthly_fixed_total(bills)
    if not plan:
        return {
            "agreed": False,
            "needs_review": True,
            "detected": detected,
            "saved": 0.0,
            "difference": detected,
            "override_active": False,
            "override_reason": "",
            "detected_at_save": None,
            "reason": "Save a monthly plan after reviewing commitments.",
        }
    saved = round(float(plan.get("fixed_obligations") or 0), 2)
    difference = round(saved - detected, 2)
    override_reason = str(plan.get("fixed_override_reason") or "").strip()
    raw_baseline = plan.get("detected_commitments_at_save")
    baseline = (
        round(float(raw_baseline), 2) if raw_baseline is not None else None
    )
    matches = abs(difference) < 0.01
    override_active = bool(
        not matches
        and override_reason
        and baseline is not None
        and abs(baseline - detected) < 0.01
    )
    agreed = matches or override_active
    if matches:
        reason = "Saved fixed costs match reviewed commitments."
    elif override_active:
        reason = f"Deliberate override: {override_reason}"
    elif baseline is None:
        reason = (
            "This plan predates commitment reconciliation. Review the "
            "detected fixed costs once."
        )
    else:
        reason = "Detected commitments changed since this plan was reviewed."
    return {
        "agreed": agreed,
        "needs_review": not agreed,
        "detected": detected,
        "saved": saved,
        "difference": difference,
        "override_active": override_active,
        "override_reason": override_reason,
        "detected_at_save": baseline,
        "reason": reason,
    }


# ── Starter plan ────────────────────────────────────────────────────

def generate_starter_plan(mode: str = "normal",
                          conn: Optional[sqlite3.Connection] = None,
                          plan_month: Optional[str] = None) -> dict:
    """Build a deterministic plan proposal. Never persists.

    `plan_month` is the month the plan TARGETS (defaults to the analysis
    anchor for backwards compatibility). Baselines always come from the
    imported data regardless of the target month — statements lag the
    calendar, and a July plan built from April-June data is exactly what
    a real user needs.
    """
    from utils.database import get_connection
    from utils.analytics import compute_cashflow
    from utils.financial_semantics import (
        sql_type_placeholders, transaction_types_for_role,
    )

    close = conn is None
    if close:
        conn = get_connection()

    spec = PLAN_MODES.get(mode, PLAN_MODES["normal"])
    # Planning baselines follow the latest supported DATA month, even when
    # the import is old enough that user-facing screens correctly switch to
    # the calendar month and call the data stale. Using that calendar fallback
    # here shifted the three-month window into empty months and understated a
    # steady income history (for example, $2,000 became $1,333.33).
    from utils.analysis_period import analysis_context

    context = analysis_context(conn=conn)
    anchor = context.get("data_month") or context["analysis_month"]
    target_month = plan_month or anchor

    # 3-month average for income + spending baseline (preferring data,
    # gracefully degrading to whatever exists).
    avgs = recent_category_averages(months=3, anchor_month=anchor, conn=conn)
    cat_avgs = {c["category"]: c["monthly_avg"] for c in avgs}
    months_used = max((c["months_with_data"] for c in avgs), default=0)

    # Recent income avg (over the same lookback window).
    y, m = map(int, anchor.split("-"))
    em_y, em_m = (y, m - 1) if m > 1 else (y - 1, 12)
    end_last_day = calendar.monthrange(em_y, em_m)[1]
    sm_y, sm_m = em_y, em_m - 2
    while sm_m <= 0:
        sm_m += 12
        sm_y -= 1
    cf = compute_cashflow(
        f"{sm_y:04d}-{sm_m:02d}-01",
        f"{em_y:04d}-{em_m:02d}-{end_last_day:02d}",
        conn=conn,
    )
    active_months = conn.execute("""
        SELECT COUNT(DISTINCT substr(transaction_date,1,7))
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
    """, (f"{sm_y:04d}-{sm_m:02d}-01",
          f"{em_y:04d}-{em_m:02d}-{end_last_day:02d}")).fetchone()[0]
    divisor = max(1, min(3, int(active_months or 0)))
    # Refunds belong in Money In for cash-flow reporting, but a one-off return
    # is not dependable future income. Plan proposals remain based on earned
    # and ordinary confirmed income only.
    planning_types = transaction_types_for_role("planning_income")
    planning_income = float(conn.execute(
        "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions "
        "WHERE transaction_type IN ("
        + sql_type_placeholders(planning_types) + ") "
        "AND transaction_date BETWEEN ? AND ?",
        (*planning_types, f"{sm_y:04d}-{sm_m:02d}-01",
         f"{em_y:04d}-{em_m:02d}-{end_last_day:02d}"),
    ).fetchone()[0] or 0)
    income_avg   = planning_income / divisor
    spending_avg = float(cf.get("spending", 0)) / divisor if cf else 0

    # A last transaction date is account activity, not statement coverage.
    # Quiet savings and cash accounts often have no recent rows even when the
    # user's imports are current, so they cannot make a proposal insufficient.
    insufficient = months_used < 1 or income_avg <= 0

    # Category targets — apply the mode's controllable cut, except for
    # fixed/volatile buckets.
    cut_frac = float(spec["controllable_cut"])
    targets: list[dict] = []
    for c in avgs:
        cat = c["category"]
        avg = c["monthly_avg"]
        if cat in FIXED_CATEGORIES:
            target = avg
            difficulty, basis = "conservative", "fixed"
        elif cat in VOLATILE_CATEGORIES:
            target = avg
            difficulty, basis = "watch", "volatile_watch"
        else:
            target = max(0.0, avg * (1 - cut_frac))
            difficulty = "tight" if cut_frac >= 0.20 else (
                "normal" if cut_frac >= 0.05 else "conservative")
            basis = f"recent_avg_minus_{int(cut_frac * 100)}pct"
        targets.append({
            "category":      cat,
            "monthly_avg":   round(avg, 2),
            "target_amount": round(target, 2),
            "basis":         basis,
            "difficulty":    difficulty,
        })

    historical_spending_target = (
        sum(t["target_amount"] for t in targets) or spending_avg
    )
    target_rate     = float(spec["target_savings_rate"])
    income_target   = round(income_avg, 2)
    safety_buffer = round(
        max(25.0, min(250.0, income_target * 0.05)) if income_target else 0.0,
        2,
    )
    spending_capacity = max(0.0, income_target - safety_buffer)
    spending_target = round(
        min(historical_spending_target, spending_capacity), 2
    )
    if historical_spending_target > spending_capacity:
        insufficient = True
    savings_target  = round(
        max(0.0, income_target - spending_target - safety_buffer), 2
    )
    if income_target > 0:
        proposed_rate = savings_target / income_target
    else:
        proposed_rate = 0.0

    # Top focus = top 3 controllable categories with biggest absolute
    # cut size (current avg − target).
    focus = sorted(
        [t for t in targets if t["basis"].startswith("recent_avg_minus_")],
        key=lambda t: -(t["monthly_avg"] - t["target_amount"]),
    )[:3]

    next_moves = []
    if focus:
        # Pass 25: humanized — currency-formatted with comma separators
        # and explicit per-month suffix. Was: "Cut Shopping from 1261 to 1198"
        next_moves.append(
            f"Cut {focus[0]['category']} from "
            f"${focus[0]['monthly_avg']:,.0f}/mo to "
            f"${focus[0]['target_amount']:,.0f}/mo."
        )
    bills = bills_and_commitments(conn=conn)
    # Pass 25: prefer the Pass 23 grouped commitment counts so this
    # phrase reflects actual locked-in bills only — Groceries/Shopping/etc.
    # in variable_count are watched, not bills, so they no longer alarm.
    _commit_count = int(bills.get("commitment_count") or 0)
    _commit_amt   = float(bills.get("commitment_monthly_estimate")
                          or bills.get("monthly_estimate") or 0)
    if _commit_count:
        next_moves.append(
            f"Review {_commit_count} locked commitment(s) — "
            f"~${_commit_amt:,.0f}/mo (Housing, Utilities, active subs)."
        )
    elif bills.get("variable_count"):
        next_moves.append(
            f"{int(bills.get('variable_count') or 0)} recurring variable "
            f"merchant(s) on the watch list — review on the Reduce page."
        )
    if mode == "sub_cleanup":
        next_moves.append("Cancel at least 2 active subscriptions.")
    if mode == "debt_recovery":
        next_moves.append("Pause non-essential spending until fees clear.")

    if close:
        conn.close()

    return {
        "month":             target_month,
        "mode":              mode,
        "mode_label":        spec["label"],
        "income_target":     round(income_target, 2),
        "spending_target":   round(spending_target, 2),
        "savings_target":    round(savings_target, 2),
        "safety_buffer":     round(safety_buffer, 2),
        "proposed_savings_rate": round(proposed_rate, 4),
        "target_savings_rate":  target_rate,
        "category_targets":  targets,
        "focus_categories":  [t["category"] for t in focus],
        "next_moves":        next_moves,
        "win_condition":     spec["win_condition"],
        "risk_warning":      spec["risk"],
        "basis": {
            "lookback_months_used": months_used,
            "income_avg":           round(income_avg, 2),
            "spending_avg":         round(spending_avg, 2),
            "source": "canonical transaction types",
            # Compatibility field for older desktop clients. Real coverage
            # needs explicit statement-period metadata, not MAX(tx date).
            "coverage_mismatch": False,
            "history_spending_above_income": (
                historical_spending_target > spending_capacity
            ),
        },
        "insufficient_data": insufficient,
    }


# ── One-minute plan confirm (rollover) ──────────────────────────────

# Plain-language names for the three stances that cover most months.
# The other PLAN_MODES stay reachable under Advanced; these are the
# primary-path vocabulary.
STANCE_LABELS = {
    "normal": "Steady",
    "tight":  "Tighter",
    "reset":  "Recovery",
}


def plan_target_month(today=None) -> str:
    """The month a NEW plan should target: the current calendar month.

    Planning is deliberately decoupled from analysis_anchor() — the
    anchor follows the imported data, which usually lags the calendar,
    and 'update your plan' must always be able to mean *this* month.
    """
    if isinstance(today, str) and today:
        today = date.fromisoformat(today)
    if not isinstance(today, date):
        today = date.today()
    return today.strftime("%Y-%m")


def plan_confirm_proposal(plan_month: Optional[str] = None,
                          conn: Optional[sqlite3.Connection] = None,
                          today=None,
                          stance: Optional[str] = None) -> dict:
    """The one-decision proposal for confirming `plan_month`'s plan.

    Rollover-first (the pattern that makes monthly budgeting low-
    maintenance in Monarch/YNAB): if a previous saved plan exists and
    no stance override is given, carry its numbers forward. Otherwise
    generate a steady starter plan from recent data.

    Returns the same shape as generate_starter_plan plus:
      source        'rollover' | 'generated'
      source_label  plain-language provenance for the UI
    """
    from utils.database import get_connection, get_applicable_plan

    close = conn is None
    if close:
        conn = get_connection()
    try:
        month = plan_month or plan_target_month(today)
        previous = get_applicable_plan(month, conn=conn)
        rollover_ok = (
            previous is not None
            and previous.get("month") != month   # not already confirmed
            and stance is None
        )
        if rollover_ok:
            proposal = generate_starter_plan(
                mode=previous.get("mode") or "normal",
                conn=conn, plan_month=month,
            )
            # Carry the agreed headline numbers forward verbatim; the
            # regenerated category targets stay as fresh suggestions.
            for k in ("income_target", "spending_target", "savings_target"):
                if previous.get(k) is not None:
                    proposal[k] = float(previous[k])
            proposal["source"] = "rollover"
            proposal["source_label"] = (
                f"Carried forward from your {previous.get('month')} plan "
                f"({STANCE_LABELS.get(previous.get('mode'), previous.get('mode'))})."
            )
            return proposal

        proposal = generate_starter_plan(
            mode=stance or "normal", conn=conn, plan_month=month,
        )
        proposal["source"] = "generated"
        proposal["source_label"] = (
            "Suggested from your recent months of imported data."
        )
        return proposal
    finally:
        if close:
            conn.close()


# ── One category-target truth ───────────────────────────────────────

def resolve_category_targets(conn: Optional[sqlite3.Connection] = None,
                             plan_month: Optional[str] = None,
                             today=None) -> dict:
    """The single source for 'what should category X be per month?'.

    Priority per category:
      1. the applicable saved plan's target      (source='plan')
      2. 90-day average minus a 20% cut          (source='suggested')

    Reduce, the recommendation engine, and runway watchlists must all
    read from here — this is what ended the era of three different
    Groceries targets on three pages.

    Returns {category: {target, source, monthly_avg}}.
    """
    from utils.database import get_connection, get_applicable_plan

    close = conn is None
    if close:
        conn = get_connection()
    try:
        month = plan_month or plan_target_month(today)
        plan = get_applicable_plan(month, conn=conn) or {}
        plan_targets = {
            t.get("category"): float(t.get("target_amount") or 0)
            for t in (plan.get("category_targets") or [])
            if t.get("category") and float(t.get("target_amount") or 0) > 0
        }

        # 90-day per-category monthly average (same frame Reduce used).
        from utils.financial_semantics import (
            sql_type_placeholders, transaction_types_for_role,
        )
        spending_types = transaction_types_for_role("spending")
        # Ninety days back from where the data genuinely reaches, not from
        # MAX(transaction_date): a single row dated years ahead would slide
        # this window past everything real and report near-zero averages.
        from utils.analysis_period import supported_latest_date
        anchor = supported_latest_date(conn=conn)
        rows = conn.execute(
            f"""
            SELECT category, SUM(ABS(amount)) / 3.0 AS monthly_avg
            FROM transactions
            WHERE transaction_date >= date(?, '-90 days')
              AND transaction_date <= ?
              AND transaction_type IN ({sql_type_placeholders(spending_types)})
            GROUP BY category
            """,
            [anchor.isoformat() if anchor else date.today().isoformat(),
             anchor.isoformat() if anchor else date.today().isoformat(),
             *spending_types],
        ).fetchall()

        out: dict = {}
        for r in rows:
            cat = r["category"] or "Uncategorized"
            avg = float(r["monthly_avg"] or 0)
            if cat in plan_targets:
                out[cat] = {
                    "target": round(plan_targets[cat], 2),
                    "source": "plan",
                    "monthly_avg": round(avg, 2),
                }
            else:
                out[cat] = {
                    "target": round(avg * 0.80, 2),
                    "source": "suggested",
                    "monthly_avg": round(avg, 2),
                }
        # Plan targets for categories with no recent spending still count.
        for cat, tgt in plan_targets.items():
            out.setdefault(cat, {
                "target": round(tgt, 2), "source": "plan",
                "monthly_avg": 0.0,
            })
        return out
    finally:
        if close:
            conn.close()


# ── Forecast ────────────────────────────────────────────────────────

def forecast_month(plan_month: Optional[str] = None,
                   conn: Optional[sqlite3.Connection] = None,
                   today=None) -> dict:
    """Project month-end income/spending/net for `plan_month`.

    Strategy:
      - MTD figures from compute_cashflow on (month_start, anchor_date).
      - "Anchor date" = min(today, last imported tx date in this month).
      - Spending is counted once per kind: fixed already paid, commitments
        due this month, flexible so far, and the only projected term,
        remaining flexible spending.
      - Commitments are placed by the occurrence their cadence expects, so a
        quarterly or annual invoice is charged to the month it falls due and
        to no other.
      - Expected income covers deposits whose date is still ahead. Money that
        was due and did not arrive is dropped rather than assumed.
      - Risk levels by margin between projected_net and savings_target
        if a plan exists, else by absolute projected_net.
    """
    from utils.database import get_connection, get_applicable_plan
    from utils.analytics import (
        compute_cashflow, scheduled_income_outlook, typical_monthly_income,
    )

    close = conn is None
    if close:
        conn = get_connection()

    if not plan_month:
        plan_month = analysis_anchor(conn=conn)
    start_iso, end_iso, days_in_month = _month_bounds(plan_month)

    # Anchor for "how much of the month have we seen". Latest tx date
    # within the month if it lags today; otherwise today. `today` is
    # injectable so the forecast and weekly check-in share one clock.
    if isinstance(today, str) and today:
        today_dt = date.fromisoformat(today)
    elif isinstance(today, date):
        today_dt = today
    else:
        today_dt = date.today()
    last_in_month_row = conn.execute(
        "SELECT MAX(transaction_date) FROM transactions "
        "WHERE transaction_date BETWEEN ? AND ?",
        (start_iso, end_iso),
    ).fetchone()
    last_in_month = last_in_month_row[0] if last_in_month_row else None
    if last_in_month:
        try:
            anchor_dt = date.fromisoformat(last_in_month)
        except Exception:
            anchor_dt = today_dt
    else:
        anchor_dt = today_dt

    month_start = date.fromisoformat(start_iso)
    month_end = date.fromisoformat(end_iso)
    if month_start <= today_dt <= month_end:
        anchor_dt = min(anchor_dt, today_dt)

    # Clamp anchor inside the plan month.
    if anchor_dt < month_start:
        anchor_dt = month_start
    elif anchor_dt > month_end:
        anchor_dt = month_end

    days_elapsed = (anchor_dt - month_start).days + 1
    days_remaining = days_in_month - days_elapsed

    cf = compute_cashflow(start_iso, anchor_dt.isoformat(), conn=conn) or {}
    mtd_income   = float(cf.get("income",   0))
    mtd_spending = float(cf.get("spending", 0))

    # Reviewed commitments still to land this month, placed by the occurrence
    # the cadence detector expects rather than by mere silence. A quarterly or
    # annual invoice belongs to one month a year, not to every month.
    #
    # The full invoice is charged in the month it falls due. The monthly
    # set-aside Plan keeps for the same bill is a different question — how to
    # save up for it — and lives in the plan equation, so neither figure is
    # counted twice inside the other.
    bills = bills_and_commitments(conn=conn)
    upcoming_bills_total = 0.0
    upcoming_bills_count = 0
    overdue_bills_total = 0.0
    overdue_bills_count = 0
    upcoming_bills: list[dict] = []
    for item in bills["items"]:
        if not item.get("included_in_forecast"):
            continue
        placement = commitment_placement(item, plan_month)
        if placement in ("paid", "other_month"):
            continue
        amount = float(item.get("est_amount") or 0)
        if placement == "overdue":
            overdue_bills_total += amount
            overdue_bills_count += 1
        else:
            upcoming_bills_total += amount
            upcoming_bills_count += 1
        upcoming_bills.append({
            "merchant": item.get("merchant"),
            "amount": round(amount, 2),
            "cadence": item.get("cadence"),
            "expected_next": item.get("expected_next"),
            "status": placement,
        })

    # An occurrence whose date has passed unseen is still likely to be paid,
    # so the money stays in projected spending. It is reported apart from the
    # ordinary upcoming bills, because calling it "expected on the 16th" three
    # weeks after the 16th is not a description of anything.
    committed_bills_total = upcoming_bills_total + overdue_bills_total
    overdue_bills_note = ""
    if overdue_bills_count:
        overdue_bills_note = (
            f"{overdue_bills_count} commitment(s) worth "
            f"${overdue_bills_total:,.0f} were expected before this month and "
            f"have not been imported. Still allowed for, not confirmed."
        )

    # Project only the spending that behaves like a rate.
    #
    # This used to be mtd_spending * days_in_month / days_elapsed. Rent lands
    # on the 1st, so on day one that multiplied the month's largest
    # commitment by the number of days in the month: $1,800 of rent became a
    # projected $55,800 and a "danger" verdict, and the alarm persisted for
    # about three weeks every month until the arithmetic caught up. A bill is
    # an event, not a rate.
    #
    # Each kind of money is now counted exactly once:
    #
    #     fixed already paid
    #   + reviewed fixed commitments due this month (committed_bills_total)
    #   + flexible spending so far
    #   + projected remaining flexible spending     (the only projected term)
    from utils.insights import _flexible_spending_so_far, statement_coverage

    flexible_mtd = _flexible_spending_so_far(
        start_iso, anchor_dt.isoformat(), bills["items"], conn,
    )
    fixed_mtd = max(0.0, mtd_spending - flexible_mtd)

    # What this household actually spent over the same stretch of days in
    # comparable complete months. Real history beats extrapolating a handful
    # of early days, and it carries the shape of a month with it.
    remainders: list[float] = []
    if days_remaining > 0:
        coverage = statement_coverage(conn=conn) or {}
        for past_month in (coverage.get("complete_months") or [])[-6:]:
            if past_month >= plan_month:
                continue
            past_year, past_number = int(past_month[:4]), int(past_month[5:7])
            past_days = calendar.monthrange(past_year, past_number)[1]
            if days_elapsed >= past_days:
                continue
            remainders.append(_flexible_spending_so_far(
                f"{past_month}-{days_elapsed + 1:02d}",
                f"{past_month}-{past_days:02d}",
                bills["items"], conn,
            ))

    if days_remaining <= 0:
        projected_remaining_flexible = 0.0
        forecast_confidence = "normal"
    elif len(remainders) >= 2:
        projected_remaining_flexible = sum(remainders) / len(remainders)
        forecast_confidence = "normal"
    else:
        # No comparable history, so the only option is the daily rate of the
        # flexible spending seen so far. Early in a month that is a handful
        # of days deciding a whole month, which is worth saying rather than
        # dressing up as a verdict.
        daily_flexible = flexible_mtd / max(days_elapsed, 1)
        projected_remaining_flexible = daily_flexible * days_remaining
        forecast_confidence = "low" if days_elapsed >= 5 else "insufficient"

    projected_spending = (
        fixed_mtd + committed_bills_total
        + flexible_mtd + projected_remaining_flexible
    )
    plan = get_applicable_plan(plan_month, conn=conn)
    current_income_baseline = typical_monthly_income(conn=conn)
    planned_income = float(
        current_income_baseline.get("amount")
        or (plan or {}).get("income_target")
        or 0
    )
    # The authoritative risk result uses money received, never planned or
    # pace-inferred income. Expected future payroll remains useful context, but
    # it is a separate forecast and cannot make a danger result look on track.
    projected_income = mtd_income
    projected_income_source = "received_only"

    # Only income with a date still ahead of it can support the outlook.
    #
    # This used to be max(mtd_income, planned_income): the whole historical
    # baseline, counted as though it were still coming however late in the
    # month it was. A household whose second payroll never arrived was told it
    # was on track on the 28th, because the missing $2,500 was folded in as if
    # received. Money that was due and did not arrive is now dropped, and the
    # verdict it was propping up is dropped with it.
    income_outlook = scheduled_income_outlook(
        plan_month, anchor=anchor_dt, conn=conn,
    )
    missed_income = float(income_outlook["missed"])
    if income_outlook["scheduled_detected"]:
        expected_income_remaining = float(income_outlook["expected_remaining"])
        expected_income_basis = "scheduled_arrivals"
    else:
        # No detectable rhythm, so there is no payday to point at. The
        # baseline may still be broadly right, but its claim on the month
        # decays as the month runs out: by the last week, income that has
        # not arrived and cannot be dated is not a forecast, it is a hope.
        remaining_share = max(0, days_remaining) / float(days_in_month or 1)
        expected_income_remaining = round(max(
            0.0, (planned_income - mtd_income) * remaining_share,
        ), 2)
        expected_income_basis = (
            "prorated_baseline" if planned_income > 0 else "received_only"
        )
    expected_income_forecast = round(mtd_income + expected_income_remaining, 2)
    projected_net = projected_income - projected_spending
    expected_projected_net = expected_income_forecast - projected_spending
    projected_rate = (
        projected_net / projected_income if projected_income > 0 else 0.0
    )

    # Risk classification. A verdict is only as good as the projection it
    # rests on, so an unsupported early month says so rather than declaring
    # a disaster on the strength of three days.
    if mtd_income == 0 and mtd_spending == 0:
        risk = "insufficient_data"
    elif forecast_confidence == "insufficient":
        risk = "insufficient_data"
    else:
        # The verdict is about how the month is likely to END, so it reads
        # the outlook figure, which counts income the baseline says is still
        # coming. Reading received-only income made every month before the
        # second payroll look like a disaster: on day two the household had
        # paid a full month of rent and received half a month of pay, so the
        # net was negative and the screen said danger about a month that was
        # entirely on course.
        #
        # projected_net stays received-only and stays authoritative for cash
        # questions. Safe to Spend never reads this branch.
        outlook_net = expected_projected_net
        outlook_income = max(expected_income_forecast, 1.0)
        if plan and plan.get("savings_target"):
            margin = outlook_net - float(plan["savings_target"])
            if margin >= 0:
                risk = "on_track"
            elif margin >= -0.10 * float(plan["savings_target"] or 1):
                risk = "watch"
            else:
                risk = "danger"
        else:
            if outlook_net >= 0.10 * outlook_income:
                risk = "on_track"
            elif outlook_net >= 0:
                risk = "watch"
            else:
                risk = "danger"

    # A payday that came and went without the money is the one thing that
    # must never read as on track. The arithmetic above no longer counts the
    # missing deposit, so it usually lands on watch by itself; this makes it
    # a rule rather than a coincidence, and lowers the confidence with it so
    # nothing about the month is stated more firmly than the data supports.
    if missed_income > 0:
        if risk == "on_track":
            risk = "watch"
        if forecast_confidence == "normal":
            forecast_confidence = "low"

    # Top forecast drivers — three biggest spending categories MTD.
    from config.categories import SPENDING_CATEGORIES
    from utils.database import get_category_totals
    drivers = [
        {"category": row["category"], "total": float(row["total"] or 0)}
        for row in get_category_totals(
            start_iso, anchor_dt.isoformat(), share_view="personal", conn=conn
        )
        if row["category"] in SPENDING_CATEGORIES
    ][:3]

    safe_to_spend_val = None
    if plan and plan.get("spending_target"):
        # spending_target − projected_already_committed.
        already = mtd_spending + committed_bills_total
        safe_to_spend_val = max(0.0, float(plan["spending_target"]) - already)

    if close:
        conn.close()

    return {
        "month":              plan_month,
        "anchor_date":        anchor_dt.isoformat(),
        "days_elapsed":       days_elapsed,
        "days_remaining":     days_remaining,
        "days_in_month":      days_in_month,
        "mtd_income":         round(mtd_income, 2),
        "mtd_spending":       round(mtd_spending, 2),
        "mtd_net":            round(mtd_income - mtd_spending, 2),
        "upcoming_bills_total": round(upcoming_bills_total, 2),
        "upcoming_bills_count": upcoming_bills_count,
        # An occurrence whose expected date has already passed unseen. Kept
        # apart from the ordinary upcoming bills so no screen can describe it
        # as merely "expected" long after the date it was expected on.
        "overdue_bills_total": round(overdue_bills_total, 2),
        "overdue_bills_count": overdue_bills_count,
        "overdue_bills_note": overdue_bills_note,
        "committed_bills_total": round(committed_bills_total, 2),
        "upcoming_bills": upcoming_bills,
        # Pass 23: variable retail watched separately. NOT added to
        # projected_spending or safe_to_spend math — surfaced so the
        # UI can show "you ALSO spend ~$X/mo on recurring variable
        # merchants" without locking it into the forecast.
        "recurring_variable_watch_total":
            round(float(bills.get("variable_monthly_watch") or 0), 2),
        "recurring_variable_watch_count":
            int(bills.get("variable_count") or 0),
        "projected_income":   round(projected_income, 2),
        "projected_income_source": projected_income_source,
        "expected_income_forecast": round(expected_income_forecast, 2),
        "expected_income_remaining": round(expected_income_remaining, 2),
        "expected_projected_net": round(expected_projected_net, 2),
        # How the expectation was arrived at, and what was due and did not
        # turn up. Received and expected stay visibly separate figures.
        "expected_income_basis": expected_income_basis,
        "missed_income_total": round(missed_income, 2),
        "income_note": str(income_outlook.get("note") or ""),
        "projected_spending": round(projected_spending, 2),
        "projected_net":      round(projected_net, 2),
        "projected_savings_rate": round(projected_rate, 4),
        "drivers":            drivers,
        "risk_level":         risk,
        # remaining_spending_cap used to sit here: a second Safe to Spend,
        # computed differently from safe_to_spend_summary and read by
        # nothing. Two ways to answer one question is how they drift apart.
        "has_plan":           bool(plan),
        # Compatibility fields. Different last-transaction dates cannot prove
        # that any statement is missing, so they do not lower confidence.
        "cutoff_gap_days":    0,
        "coverage_warning":   "",
        "confidence":         forecast_confidence,
        # The components, so a screen can show its working and nobody has to
        # reverse-engineer which part was projected.
        "fixed_paid_so_far":  round(fixed_mtd, 2),
        "flexible_so_far":    round(flexible_mtd, 2),
        "projected_remaining_flexible":
            round(projected_remaining_flexible, 2),
        "flexible_basis": (
            "comparable_months" if len(remainders) >= 2 else "recent_days"
        ),
    }


def safe_to_spend(plan: dict, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return the canonical current-balance Safe to Spend result.

    ``plan`` remains accepted for compatibility, but the result is never
    derived from a planned spending cap or unreceived income.
    """
    from utils.checkin import safe_to_spend_summary

    result = safe_to_spend_summary(conn=conn)
    return {
        **result,
        "anchor_date": result.get("period_end") or "",
        "basis": "available balances minus confirmed reservations",
    }


# ── Goals ───────────────────────────────────────────────────────────

def _next_milestone(value: float) -> float:
    """Snap to next round milestone. Mirrors Home card logic."""
    if value < 1_000:        step = 500
    elif value < 10_000:     step = 1_000
    elif value < 50_000:     step = 5_000
    elif value < 250_000:    step = 25_000
    elif value < 1_000_000:  step = 50_000
    else:                    step = 100_000
    import math
    return float(math.floor(value / step + 1) * step)


def goal_progress(goals: list[dict],
                  conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Backward-compatible wrapper for the unified goals service."""
    from utils.goals import goal_progress as _goal_progress
    return _goal_progress(goals, conn=conn)

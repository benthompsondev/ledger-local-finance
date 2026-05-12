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
    # Insurance / loan / phone / internet typically land in Utilities/
    # Bills today. If the user later splits these out, add them here.
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

def analysis_anchor(conn: Optional[sqlite3.Connection] = None) -> str:
    """Return 'YYYY-MM' to treat as the current analysis month.

    Picks the LATEST month that has any imported transactions. If that
    month is older than ~5 weeks behind today, falls back to the
    calendar month so the forecast UI doesn't claim to be "current"
    when it isn't. Returns today's month string when DB is empty.
    """
    from utils.database import get_connection
    close = conn is None
    if close:
        conn = get_connection()
    row = conn.execute(
        "SELECT MAX(transaction_date) AS last_d FROM transactions"
    ).fetchone()
    if close:
        conn.close()
    today = date.today()
    if not row or not row["last_d"]:
        return today.strftime("%Y-%m")
    try:
        last = date.fromisoformat(row["last_d"])
    except Exception:
        return today.strftime("%Y-%m")
    if (today - last).days > 35:
        return today.strftime("%Y-%m")
    return last.strftime("%Y-%m")


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

    placeholders = ",".join("?" * len(NON_CONSUMPTION_CATEGORIES))
    rows = conn.execute(f"""
        SELECT
            category,
            COUNT(DISTINCT strftime('%Y-%m', transaction_date)) AS months_with_data,
            SUM(ABS(amount)) AS total
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND transaction_date BETWEEN ? AND ?
          AND (category NOT IN ({placeholders}) OR category IS NULL)
          AND category NOT IN ('Credit Card Payment','Cancelled')
        GROUP BY category
        HAVING category IS NOT NULL AND category != ''
        ORDER BY total DESC
    """, [start_iso, end_iso, *NON_CONSUMPTION_CATEGORIES]).fetchall()

    if close:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        m_with = max(int(d["months_with_data"] or 0), 1)
        out.append({
            "category":         d["category"],
            "total":            float(d["total"] or 0),
            "months_with_data": int(d["months_with_data"] or 0),
            "monthly_avg":      float((d["total"] or 0) / m_with),
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
            "category":      r.get("category"),
            "est_amount":    float(r.get("avg_amount") or 0),
            "frequency":     "monthly" if ms >= 3 else "irregular",
            "last_seen":     "",
            "expected_next": None,
            "confidence":    conf,
            "active":        True,
            "included_in_forecast": included,
            "group":         group,
            "reason":        reason,
            "source":        "recurring_merchant",
        })

    items = sorted(seen.values(),
                   key=lambda x: -float(x.get("est_amount") or 0))

    # Group splits.
    fixed_commitments = [i for i in items
                         if i["group"] == "fixed_commitments"]
    active_subs       = [i for i in items
                         if i["group"] == "active_subscriptions"]
    variable_watch    = [i for i in items
                         if i["group"] == "recurring_variable_merchants"]
    stale_inactive    = [i for i in items
                         if i["group"] == "stale_or_inactive"]

    commitment_total = sum(float(i.get("est_amount") or 0)
                           for i in fixed_commitments + active_subs)
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
        "recurring_variable_merchants": variable_watch,
        "stale_or_inactive":            stale_inactive,
        "commitment_monthly_estimate":  float(commitment_total),
        "variable_monthly_watch":       float(variable_total),
        "commitment_count":             len(fixed_commitments) + len(active_subs),
        "variable_count":               len(variable_watch),
    }


# ── Starter plan ────────────────────────────────────────────────────

def generate_starter_plan(mode: str = "normal",
                          conn: Optional[sqlite3.Connection] = None) -> dict:
    """Build a deterministic plan proposal. Never persists."""
    from utils.database import get_connection
    from utils.analytics import compute_cashflow

    close = conn is None
    if close:
        conn = get_connection()

    spec = PLAN_MODES.get(mode, PLAN_MODES["normal"])
    anchor = analysis_anchor(conn=conn)

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
    income_avg   = float(cf.get("income", 0)) / 3 if cf else 0
    spending_avg = float(cf.get("spending", 0)) / 3 if cf else 0

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

    spending_target = sum(t["target_amount"] for t in targets) or spending_avg
    target_rate     = float(spec["target_savings_rate"])
    income_target   = income_avg
    savings_target  = max(0.0, income_target - spending_target)
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
        "month":             anchor,
        "mode":              mode,
        "mode_label":        spec["label"],
        "income_target":     round(income_target, 2),
        "spending_target":   round(spending_target, 2),
        "savings_target":    round(savings_target, 2),
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
        },
        "insufficient_data": insufficient,
    }


# ── Forecast ────────────────────────────────────────────────────────

def forecast_month(plan_month: Optional[str] = None,
                   conn: Optional[sqlite3.Connection] = None) -> dict:
    """Project month-end income/spending/net for `plan_month`.

    Strategy:
      - MTD figures from compute_cashflow on (month_start, anchor_date).
      - "Anchor date" = min(today, last imported tx date in this month).
      - Pace projection: spending_pace = mtd_spending * days_in_month
        / max(days_elapsed, 1). Same for income.
      - Add upcoming-bill estimate: bills not yet seen this month based
        on last_seen prior to anchor.
      - Risk levels by margin between projected_net and savings_target
        if a plan exists, else by absolute projected_net.
    """
    from utils.database import get_connection, get_monthly_plan
    from utils.analytics import compute_cashflow

    close = conn is None
    if close:
        conn = get_connection()

    if not plan_month:
        plan_month = analysis_anchor(conn=conn)
    start_iso, end_iso, days_in_month = _month_bounds(plan_month)

    # Anchor for "how much of the month have we seen". Latest tx date
    # within the month if it lags today; otherwise today.
    today = date.today()
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
            anchor_dt = today
    else:
        anchor_dt = today

    # Clamp anchor inside the plan month.
    month_start = date.fromisoformat(start_iso)
    month_end   = date.fromisoformat(end_iso)
    if anchor_dt < month_start:
        anchor_dt = month_start
    elif anchor_dt > month_end:
        anchor_dt = month_end

    days_elapsed = (anchor_dt - month_start).days + 1
    days_remaining = days_in_month - days_elapsed

    cf = compute_cashflow(start_iso, anchor_dt.isoformat(), conn=conn) or {}
    mtd_income   = float(cf.get("income",   0))
    mtd_spending = float(cf.get("spending", 0))

    # Upcoming bills not yet hit this month.
    bills = bills_and_commitments(conn=conn)
    upcoming_bills_total = 0.0
    upcoming_bills_count = 0
    for item in bills["items"]:
        if not item.get("included_in_forecast"):
            continue
        ls = item.get("last_seen") or ""
        try:
            last_dt = date.fromisoformat(ls) if ls else None
        except Exception:
            last_dt = None
        # If we haven't seen this merchant this month yet, treat as
        # likely-upcoming.
        if not last_dt or last_dt < month_start:
            upcoming_bills_total += float(item.get("est_amount") or 0)
            upcoming_bills_count += 1

    # Pace projection from month-to-date numbers.
    pace_factor = days_in_month / max(days_elapsed, 1)
    projected_spending_pace = mtd_spending * pace_factor
    # Take whichever is larger: pure pace OR mtd + remaining bills.
    projected_spending = max(projected_spending_pace,
                             mtd_spending + upcoming_bills_total)
    projected_income = mtd_income * pace_factor
    projected_net = projected_income - projected_spending
    projected_rate = (
        projected_net / projected_income if projected_income > 0 else 0.0
    )

    # Risk classification.
    plan = get_monthly_plan(plan_month, conn=conn)
    if mtd_income == 0 and mtd_spending == 0:
        risk = "insufficient_data"
    else:
        if plan and plan.get("savings_target"):
            margin = projected_net - float(plan["savings_target"])
            if margin >= 0:
                risk = "on_track"
            elif margin >= -0.10 * float(plan["savings_target"] or 1):
                risk = "watch"
            else:
                risk = "danger"
        else:
            if projected_net >= 0.10 * max(projected_income, 1):
                risk = "on_track"
            elif projected_net >= 0:
                risk = "watch"
            else:
                risk = "danger"

    # Top forecast drivers — three biggest spending categories MTD.
    driver_rows = conn.execute("""
        SELECT category, SUM(ABS(amount)) AS total
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND transaction_date BETWEEN ? AND ?
          AND category NOT IN ('Credit Card Payment','Cancelled')
        GROUP BY category
        ORDER BY total DESC
        LIMIT 3
    """, (start_iso, anchor_dt.isoformat())).fetchall()
    drivers = [{"category": r[0], "total": float(r[1] or 0)} for r in driver_rows]

    safe_to_spend_val = None
    if plan and plan.get("spending_target"):
        # spending_target − projected_already_committed.
        already = mtd_spending + upcoming_bills_total
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
        # Pass 23: variable retail watched separately. NOT added to
        # projected_spending or safe_to_spend math — surfaced so the
        # UI can show "you ALSO spend ~$X/mo on recurring variable
        # merchants" without locking it into the forecast.
        "recurring_variable_watch_total":
            round(float(bills.get("variable_monthly_watch") or 0), 2),
        "recurring_variable_watch_count":
            int(bills.get("variable_count") or 0),
        "projected_income":   round(projected_income, 2),
        "projected_spending": round(projected_spending, 2),
        "projected_net":      round(projected_net, 2),
        "projected_savings_rate": round(projected_rate, 4),
        "drivers":            drivers,
        "risk_level":         risk,
        "safe_to_spend":      (round(safe_to_spend_val, 2)
                               if safe_to_spend_val is not None else None),
        "has_plan":           bool(plan),
    }


def safe_to_spend(plan: dict, conn: Optional[sqlite3.Connection] = None) -> dict:
    """Wrap forecast_month with the plan's spending_target.

    Returns {amount, basis, anchor_date}. Amount may be None if the
    plan has no spending_target.
    """
    fc = forecast_month(plan_month=plan.get("month"), conn=conn)
    return {
        "amount":      fc.get("safe_to_spend"),
        "anchor_date": fc.get("anchor_date"),
        "basis":       "spending_target − (MTD spending + upcoming bills)",
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
    """Compute progress for each goal. Auto-derives current_amount when
    `linked_metric` is set.

    Linked metrics:
      'net_worth'      → compute_net_worth_now().net_worth
      'investments'    → latest investment snapshot total_market_value
      'cash_balance'   → sum of latest balances where account_kind in
                         {cash, chequing, savings}
    """
    from utils.database import (
        get_connection, compute_net_worth_now,
        get_latest_investment_snapshot, get_account_balances,
        ASSET_KINDS,
    )
    close = conn is None
    if close:
        conn = get_connection()

    nw = compute_net_worth_now(conn=conn)
    snap = get_latest_investment_snapshot(conn=conn)
    bals = get_account_balances(conn=conn, latest_only=True)
    cash_total = sum(
        float(b.get("balance") or 0)
        for b in bals
        if (b.get("account_kind") or "") in {"cash", "chequing", "savings"}
    )

    out = []
    for g in goals:
        g_out = dict(g)
        link = (g.get("linked_metric") or "").lower()
        if link == "net_worth":
            g_out["current_amount"] = float(nw.get("net_worth") or 0)
        elif link == "investments":
            g_out["current_amount"] = float(
                snap.get("total_market_value_native") if snap else 0
            )
        elif link == "cash_balance":
            g_out["current_amount"] = float(cash_total)
        else:
            g_out["current_amount"] = float(g.get("current_amount") or 0)

        target = float(g.get("target_amount") or 0)
        cur    = float(g_out["current_amount"] or 0)
        if target > 0:
            pct = max(0.0, min(1.0, cur / target))
        else:
            pct = 0.0
        g_out["progress_pct"] = round(pct, 4)
        g_out["gap"] = round(max(0.0, target - cur), 2)
        g_out["next_milestone"] = (
            round(_next_milestone(cur), 2) if cur >= 0 else None
        )
        out.append(g_out)

    if close:
        conn.close()
    return out

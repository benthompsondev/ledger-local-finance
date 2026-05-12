"""
Read-only Ledger context export for future OpenClaw Finance Seraphine.

What this is
────────────
A tiny, defensive helper that returns a single dict snapshot of Ledger's
useful state — KPIs, top categories, top merchants, subscriptions, recs,
review queue summary, money progress, generated_at — so an external
agent can read the user's finances without touching the SQLite DB
directly and without any write surface.

What this is NOT
────────────────
• Not a server. There's no HTTP / RPC layer here.
• Not a writer. There are no mutators in this module — every helper it
  calls is read-only by construction.
• Not an authentication boundary. A caller that can import this module
  already has full Python access to the same DB; the seam exists for
  ergonomics, not security.
• Not a transaction dump. The default packet excludes raw rows; pass
  `include_recent_transactions=True` to opt in for a small slice.

Pass 17: scaffolded so OpenClaw can ship before Pass 18 has to ship a
write API. The shape is intentionally similar to the explainer packets
already used by `dashboard_copilot` / `weekly_review` so existing AI
plumbing can grow into it without re-shaping data again.

Usage
─────
    from utils.agent_context import build_agent_context
    ctx = build_agent_context()        # default: last 90 days
    # ctx is a dict; safe to json.dumps. No secrets, no API keys.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional


# Cap raw-tx slice so this never accidentally exports thousands of rows.
_MAX_RECENT_TX = 50


def _period_window(period: str) -> tuple[str, str]:
    today = date.today()
    if period == "last_30_days":
        start = today - timedelta(days=30)
    elif period == "last_180_days":
        start = today - timedelta(days=180)
    elif period == "last_365_days":
        start = today - timedelta(days=365)
    else:  # default
        start = today - timedelta(days=90)
    return start.isoformat(), today.isoformat()


def build_agent_context(
    *,
    period: str = "last_90_days",
    include_recent_transactions: bool = False,
    conn=None,
) -> dict:
    """Return a JSON-serializable dict describing the current Ledger state.

    Args:
        period: 'last_30_days' | 'last_90_days' (default) | 'last_180_days'
                | 'last_365_days'
        include_recent_transactions: if True, attaches up to 50 most-recent
                                     transactions (id/date/merchant/category/
                                     amount/direction). Off by default.

    Returns dict with keys:
        generated_at, period, window
        coverage:        first_month, last_month, total_months, gap_months
        kpis:            income, spending, net, savings_rate
        top_categories:  [{category, total, tx_count, pct}]
        top_merchants:   [{merchant, total, tx_count, category}]
        subscriptions:   {monthly_estimate, annual_total, count, candidates[]}
        reduce_summary:  {controllable_categories[]}  (top 5)
        recommendations: {total, by_priority, top[]}
        review_queue:    {flagged_count, by_reason}
        money_progress:  level / xp / momentum_label / wins / risks
        risks:           cash_advance_count_90d, cash_advance_total_90d,
                         fees_total_90d
        income_summary:  by_source[] (tag/total) using analytics helper
        recent_transactions: optional, see flag

    Never raises. Falls back to empty sections if a helper fails so a
    partial export still has whatever it could compute. No secrets / API
    keys are ever included; this packet is safe to log.
    """
    from utils.database import get_connection

    close = False
    if conn is None:
        conn = get_connection()
        close = True

    start, end = _period_window(period)
    out: dict = {
        "generated_at": date.today().isoformat(),
        "period":       period,
        "window":       {"start": start, "end": end},
    }

    # Pass 28: demo-mode disclosure. OpenClaw and any reviewer can
    # immediately tell whether this context describes a real ledger or
    # the synthetic demo DB. Never mutates anything.
    try:
        from utils.database import is_demo_mode, DB_PATH as _DB_PATH
        out["demo_mode"] = bool(is_demo_mode())
        out["demo_warning"] = (
            "DEMO MODE — all transactions, balances, and snapshots are "
            "synthetic placeholders (every merchant prefixed 'DEMO '). "
            "Do not treat any number here as Ben's real finances."
            if out["demo_mode"] else ""
        )
        out["db_path_basename"] = _DB_PATH.name if hasattr(_DB_PATH, "name") else str(_DB_PATH)
    except Exception:
        out["demo_mode"] = False
        out["demo_warning"] = ""
        out["db_path_basename"] = ""

    # Coverage
    try:
        from utils.insights import coverage_summary
        out["coverage"] = coverage_summary(conn=conn)
        out["latest_imported_month"] = (
            (out["coverage"] or {}).get("last_month") or ""
        )
    except Exception:
        out["coverage"] = {}
        out["latest_imported_month"] = ""

    # Ask Ledger supported skills — flat list of skill ids the agent can
    # request. Tiny and safe; helps OpenClaw know what to route to.
    try:
        from utils.ai_explainer import ASK_PRESETS
        out["ask_ledger_supported_skills"] = [s for s, _ in ASK_PRESETS]
    except Exception:
        out["ask_ledger_supported_skills"] = []

    # KPIs (cashflow)
    try:
        from utils.analytics import compute_cashflow
        cf = compute_cashflow(start, end, conn=conn)
        out["kpis"] = {
            "income":       float(cf.get("income", 0)),
            "spending":     float(cf.get("spending", 0)),
            "net":          float(cf.get("net", 0)),
            "savings_rate": float(cf.get("savings_rate", 0)),
        }
    except Exception:
        out["kpis"] = {}

    # Top categories
    try:
        from utils.analytics import spending_by_category
        cats = spending_by_category(start, end, conn=conn) or []
        # Drop system / non-consumption noise from the agent context.
        from config.categories import NON_CONSUMPTION_CATEGORIES
        cats = [c for c in cats if c["category"] not in NON_CONSUMPTION_CATEGORIES]
        out["top_categories"] = [
            {
                "category": c["category"],
                "total":    float(c["total"]),
                "tx_count": int(c.get("tx_count", 0)),
                "pct":      float(c.get("pct", 0)) if "pct" in c else 0.0,
            }
            for c in cats[:10]
        ]
    except Exception:
        out["top_categories"] = []

    # Top merchants
    try:
        from utils.analytics import top_merchants
        ms = top_merchants(start, end, limit=10, conn=conn) or []
        out["top_merchants"] = [
            {
                "merchant": m.get("merchant", ""),
                "total":    float(m.get("total", 0)),
                "tx_count": int(m.get("tx_count", 0)),
                "category": m.get("category", ""),
            }
            for m in ms
        ]
    except Exception:
        out["top_merchants"] = []

    # Subscriptions (Pass 18: active/stale split exposed for OpenClaw)
    try:
        from utils.insights import subscription_detective
        det = subscription_detective(conn=conn)
        out["subscriptions"] = {
            "monthly_estimate":    float(det.get("monthly_estimate", 0)),
            "annual_total":        float(det.get("annual_total", 0)),
            "count":               int(det.get("count", 0)),
            "candidates":          [
                {
                    "merchant":     c["merchant"],
                    "monthly":      float(c.get("avg_amount", 0)),
                    "annual":       float(c.get("annual", 0)),
                    "months_seen":  int(c.get("months_seen", 0)),
                    "flags":        list(c.get("flags") or []),
                }
                for c in (det.get("candidates") or [])[:8]
            ],
            # Pass 18 additions — keep compact, no raw rows.
            "active_monthly_estimate":      float(det.get("active_monthly_estimate", 0)),
            "active_annual_total":          float(det.get("active_annual_total", 0)),
            "active_candidate_annual_total": float(det.get("active_candidate_annual_total", 0)),
            "stale_annual_total":           float(det.get("stale_annual_total", 0)),
            "anchor_date":                  det.get("anchor_date") or "",
            "active_window_days":           int(det.get("active_window_days") or 60),
        }
        out["active_reduce_candidates"] = [
            {
                "merchant":  c["merchant"],
                "monthly":   float(c.get("avg_amount", 0)),
                "annual":    float(c.get("annual", 0)),
                "flags":     list(c.get("flags") or []),
                "last_seen": c.get("last_seen", ""),
            }
            for c in (det.get("active_candidates") or [])[:6]
        ]
        out["stale_subscriptions"] = [
            {
                "merchant":  s["merchant"],
                "monthly":   float(s.get("avg_amount", 0)),
                "annual":    float(s.get("annual", 0)),
                "last_seen": s.get("last_seen", ""),
            }
            for s in (det.get("stale_candidates") or [])[:6]
        ]
    except Exception:
        out["subscriptions"] = {}
        out["active_reduce_candidates"] = []
        out["stale_subscriptions"] = []

    # Reduce — controllable categories
    try:
        from utils.insights import top_controllable_categories
        ctl = top_controllable_categories(conn=conn, limit=5) or []
        out["reduce_summary"] = {
            "controllable_categories": [
                {
                    "category":    c["category"],
                    "monthly_avg": float(c.get("monthly_avg", 0)),
                    "total_90d":   float(c.get("total_90d", 0)),
                    "tx_count":    int(c.get("tx_count", 0)),
                }
                for c in ctl
            ],
        }
    except Exception:
        out["reduce_summary"] = {}

    # Recommendations summary
    try:
        from utils.insights import compute_recommendations
        recs = compute_recommendations(conn=conn) or []
        priority = {"high": 0, "medium": 0, "low": 0}
        for r in recs:
            priority[r.get("priority", "low")] = priority.get(r.get("priority", "low"), 0) + 1
        out["recommendations"] = {
            "total":       len(recs),
            "by_priority": priority,
            "top": [
                {
                    "key":            r.get("key"),
                    "title":          r.get("title"),
                    "category":       r.get("category"),
                    "annual_impact":  float(r.get("annual_impact", 0) or 0),
                    "priority":       r.get("priority"),
                    "type":           r.get("type"),
                }
                for r in recs[:5]
            ],
        }
    except Exception:
        out["recommendations"] = {}

    # Review queue
    try:
        flagged_rows = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
        ).fetchone()
        flagged_total = int(flagged_rows[0]) if flagged_rows else 0
        by_reason_rows = conn.execute(
            "SELECT flag_reason, COUNT(*) AS n FROM transactions "
            "WHERE is_flagged=1 GROUP BY flag_reason ORDER BY n DESC"
        ).fetchall()
        by_reason = {(r[0] or "unknown"): int(r[1]) for r in by_reason_rows}
        out["review_queue"] = {
            "flagged_count": flagged_total,
            "by_reason":     by_reason,
        }
    except Exception:
        out["review_queue"] = {}

    # Money progress
    try:
        from utils.momentum import money_progress
        mp = money_progress(conn=conn)
        out["money_progress"] = {
            "level":          mp.get("level"),
            "xp":             mp.get("xp"),
            "xp_total":       mp.get("xp_total"),
            "xp_to_next":     mp.get("xp_to_next"),
            "progress_pct":   mp.get("progress_pct"),
            "momentum_label": mp.get("momentum_label"),
            "wins":           [w.get("label") for w in (mp.get("wins") or [])],
            "risks":          [r.get("label") for r in (mp.get("risks") or [])],
        }
    except Exception:
        out["money_progress"] = {}

    # Risk signals — cash advance / fees in last 90 days
    try:
        cash_row = conn.execute("""
            SELECT COUNT(*) AS n, SUM(ABS(amount)) AS total
            FROM transactions
            WHERE category='Cash Advance' AND direction='debit'
              AND transaction_date >= date('now', '-90 days')
        """).fetchone()
        fees_row = conn.execute("""
            SELECT SUM(ABS(amount)) AS total
            FROM transactions
            WHERE category='Fees / Interest' AND direction='debit'
              AND transaction_date >= date('now', '-90 days')
        """).fetchone()
        out["risks"] = {
            "cash_advance_count_90d": int(cash_row[0] or 0) if cash_row else 0,
            "cash_advance_total_90d": float(cash_row[1] or 0) if cash_row else 0.0,
            "fees_total_90d":         float(fees_row[0] or 0) if fees_row else 0.0,
        }
    except Exception:
        out["risks"] = {}

    # Income summary
    try:
        from utils.analytics import income_summary, income_by_source
        inc = income_summary(start, end, conn=conn) or {}
        sources = income_by_source(start, end, conn=conn) or []
        out["income_summary"] = {
            "total":   float(inc.get("total", 0)),
            "tx_count": int(inc.get("tx_count", 0)),
            "by_source": [
                {
                    "source": s.get("category") or s.get("subcategory") or "",
                    "total":  float(s.get("total", 0)),
                    "tx_count": int(s.get("tx_count", 0)),
                }
                for s in sources[:10]
            ],
        }
    except Exception:
        out["income_summary"] = {}

    # ── Pass 19: investment + net worth summaries ──────────────────
    # All read-only. No raw account numbers, no full position dumps by
    # default. Shapes are deliberately small so they fit in an agent
    # context window without crowding KPIs/recommendations.
    try:
        from utils.database import (
            get_latest_investment_snapshot, compute_net_worth_now,
            get_net_worth_snapshots,
        )
        snap = get_latest_investment_snapshot(conn=conn)
        if snap:
            positions = snap.get("positions") or []
            # Top 5 holdings by market value (positions already sorted).
            top_positions = [
                {
                    "ticker":       p.get("ticker"),
                    "security":     p.get("security_name"),
                    "account_type": p.get("account_type"),
                    "market_value": float(p.get("market_value") or 0),
                    "currency":     p.get("market_value_currency") or "CAD",
                }
                for p in positions[:5]
            ]
            by_account: dict[str, float] = {}
            for p in positions:
                k = p.get("account_type") or "Other"
                by_account[k] = by_account.get(k, 0) + float(
                    p.get("market_value") or 0
                )
            out["investments_summary"] = {
                "as_of_date":         snap.get("as_of_date"),
                "total_market_value": float(snap.get("total_market_value_native") or 0),
                "position_count":     len(positions),
                "currencies":         (snap.get("currencies_seen") or "").split(",") if snap.get("currencies_seen") else [],
                "mixed_currency":     bool(snap.get("mixed_currency")),
                "by_account_type":    [
                    {"account_type": k, "total": v}
                    for k, v in sorted(by_account.items(), key=lambda x: -x[1])
                ],
                "top_positions":      top_positions,
            }
        else:
            out["investments_summary"] = {}
    except Exception:
        out["investments_summary"] = {}

    try:
        from utils.database import compute_net_worth_now, get_net_worth_snapshots
        nw = compute_net_worth_now(conn=conn)
        history = get_net_worth_snapshots(conn=conn, limit=24)
        out["net_worth_summary"] = {
            "as_of_date":        nw.get("as_of_date") or "",
            "total_assets":      float(nw.get("total_assets") or 0),
            "total_liabilities": float(nw.get("total_liabilities") or 0),
            "net_worth":         float(nw.get("net_worth") or 0),
            "mixed_currency":    bool(nw.get("mixed_currency")),
            "currencies":        list(nw.get("currencies") or []),
            "missing_inputs":    list(nw.get("missing") or []),
            "history": [
                {
                    "as_of_date":        h.get("as_of_date"),
                    "total_assets":      float(h.get("total_assets") or 0),
                    "total_liabilities": float(h.get("total_liabilities") or 0),
                    "net_worth":         float(h.get("net_worth") or 0),
                }
                for h in history
            ],
        }
    except Exception:
        out["net_worth_summary"] = {}

    # ── Pass 21: month plan / forecast / goals / bills ─────────────
    # All deterministic. Each section is built defensively — partial
    # state still produces a usable shape for the agent.
    try:
        from utils.planner import (
            analysis_anchor, forecast_month, bills_and_commitments,
            goal_progress,
        )
        from utils.database import get_monthly_plan, get_goals
        anchor = analysis_anchor(conn=conn)
        plan = get_monthly_plan(anchor, conn=conn)
        if plan:
            out["month_plan"] = {
                "month":            plan.get("month"),
                "mode":             plan.get("mode"),
                "income_target":    float(plan.get("income_target") or 0),
                "spending_target":  float(plan.get("spending_target") or 0),
                "savings_target":   float(plan.get("savings_target") or 0),
                "category_targets": [
                    {
                        "category":      t.get("category"),
                        "target_amount": float(t.get("target_amount") or 0),
                        "difficulty":    t.get("difficulty"),
                        "basis":         t.get("basis"),
                    }
                    for t in plan.get("category_targets") or []
                ],
            }
        else:
            out["month_plan"] = {"month": anchor, "saved": False}
    except Exception:
        out["month_plan"] = {}

    try:
        fc = forecast_month(plan_month=anchor, conn=conn)
        out["forecast"] = {
            "month":              fc.get("month"),
            "anchor_date":        fc.get("anchor_date"),
            "days_elapsed":       fc.get("days_elapsed"),
            "days_in_month":      fc.get("days_in_month"),
            "mtd_income":         fc.get("mtd_income"),
            "mtd_spending":       fc.get("mtd_spending"),
            "projected_income":   fc.get("projected_income"),
            "projected_spending": fc.get("projected_spending"),
            "projected_net":      fc.get("projected_net"),
            "projected_savings_rate": fc.get("projected_savings_rate"),
            "upcoming_bills_total": fc.get("upcoming_bills_total"),
            "risk_level":         fc.get("risk_level"),
            "drivers":            fc.get("drivers") or [],
            "safe_to_spend":      fc.get("safe_to_spend"),
            "has_plan":           fc.get("has_plan"),
        }
    except Exception:
        out["forecast"] = {}

    try:
        goals = get_goals(conn=conn, status="active") or []
        gp = goal_progress(goals, conn=conn)
        out["goals"] = [
            {
                "name":           g.get("name"),
                "type":           g.get("type"),
                "target_amount":  float(g.get("target_amount") or 0),
                "current_amount": float(g.get("current_amount") or 0),
                "progress_pct":   float(g.get("progress_pct") or 0),
                "next_milestone": float(g.get("next_milestone") or 0),
                "linked_metric":  g.get("linked_metric"),
                "target_date":    g.get("target_date"),
            }
            for g in gp
        ]
    except Exception:
        out["goals"] = []

    try:
        bills = bills_and_commitments(conn=conn)

        def _shrink(items: list, limit: int = 6) -> list:
            return [
                {
                    "merchant":      i.get("merchant"),
                    "category":      i.get("category"),
                    "est_amount":    float(i.get("est_amount") or 0),
                    "frequency":     i.get("frequency"),
                    "active":        bool(i.get("active")),
                    "included":      bool(i.get("included_in_forecast")),
                    "expected_next": i.get("expected_next"),
                    "group":         i.get("group"),
                    "reason":        i.get("reason"),
                }
                for i in (items or [])[:limit]
            ]

        out["bills_summary"] = {
            "count":                       int(bills.get("count") or 0),
            "anchor_month":                bills.get("anchor_month"),
            # Pass 23: grouped totals replace the flat "monthly_estimate"
            # as the source of truth for the agent. Backward-compat field
            # `monthly_estimate` mirrors `commitment_monthly_estimate`.
            "monthly_estimate":            float(bills.get("monthly_estimate") or 0),
            "commitment_monthly_estimate": float(bills.get("commitment_monthly_estimate") or 0),
            "variable_monthly_watch":      float(bills.get("variable_monthly_watch") or 0),
            "commitment_count":            int(bills.get("commitment_count") or 0),
            "variable_count":              int(bills.get("variable_count") or 0),
            "fixed_commitments":           _shrink(bills.get("fixed_commitments")),
            "active_subscriptions":        _shrink(bills.get("active_subscriptions")),
            "recurring_variable_merchants":
                _shrink(bills.get("recurring_variable_merchants")),
            "stale_or_inactive":           _shrink(bills.get("stale_or_inactive")),
            # Top items kept for backward compatibility — same data,
            # ordered by est_amount desc.
            "top_items":                   _shrink(bills.get("items"), 8),
        }
    except Exception:
        out["bills_summary"] = {}

    # ── Pass 22: reminder_suggestions ──────────────────────────────
    # A short, concrete list of reminders OpenClaw can surface to the
    # user weekly/monthly. Built from forecast risk, plan presence,
    # bills count, and goals progress — never invented. Each entry is
    # a self-contained string so the agent can render them as-is.
    try:
        _reminders: list[str] = []
        _fc_local = out.get("forecast") or {}
        _plan_local = out.get("month_plan") or {}
        _bills_local = out.get("bills_summary") or {}
        _goals_local = out.get("goals") or []
        _risk = (_fc_local.get("risk_level") or "").replace("_", " ")
        if _risk in ("danger", "watch"):
            _reminders.append(
                f"Weekly: review forecast risk ({_risk}). "
                f"Projected net ${(_fc_local.get('projected_net') or 0):,.0f}."
            )
        if _plan_local and not _plan_local.get("mode"):
            _reminders.append(
                "Open Month Plan and save a plan for this month."
            )
        elif _plan_local.get("mode"):
            _reminders.append(
                f"Mid-month: check safe-to-spend before the weekend "
                f"(plan mode: {_plan_local.get('mode')})."
            )
        if _bills_local.get("count"):
            _reminders.append(
                f"Audit {_bills_local.get('count', 0)} commitment(s) "
                f"(~${(_bills_local.get('monthly_estimate') or 0):,.0f}/mo)."
            )
        for _g in _goals_local[:2]:
            _reminders.append(
                f"Track goal '{_g.get('name')}' "
                f"({(_g.get('progress_pct') or 0) * 100:.0f}%)."
            )
        _reminders.append(
            "Monthly: snapshot net worth on the Investments page."
        )
        _cov = out.get("coverage") or {}
        if _cov.get("last_month"):
            _reminders.append(
                f"Refresh data: latest imported month is "
                f"{_cov.get('last_month')}."
            )
        out["reminder_suggestions"] = _reminders[:6]
    except Exception:
        out["reminder_suggestions"] = []

    # Suggested reminders / next actions — distilled from plan +
    # forecast + recommendations. Compact, agent-friendly strings.
    try:
        next_actions: list[str] = []
        fc_local = out.get("forecast") or {}
        plan_local = out.get("month_plan") or {}
        if fc_local.get("risk_level") == "danger":
            next_actions.append(
                "Forecast risk is DANGER — review the Reduce page and "
                "cut at least one active subscription this week."
            )
        elif fc_local.get("risk_level") == "watch":
            next_actions.append(
                "Forecast risk is WATCH — keep discretionary spend "
                "below your safe-to-spend amount."
            )
        if plan_local and not plan_local.get("mode"):
            next_actions.append(
                "No plan saved for the analysis month yet — open Month "
                "Plan and pick a mode."
            )
        rec_top = (out.get("recommendations") or {}).get("top") or []
        if rec_top:
            next_actions.append(
                f"Top recommendation: {rec_top[0].get('title')}"
            )
        out["next_actions"] = next_actions[:5]
    except Exception:
        out["next_actions"] = []

    # ── Pass 25: preferred everyday-use summary keys ────────────────
    # These are additive; nothing reads or removes the existing fields
    # above. OpenClaw can rely on these for the most common questions:
    # what changed, what to cut, what's the next move, what to watch.
    # All five are deterministic and safe — never include keys, prompts,
    # or raw transactions.
    try:
        kp = out.get("kpis") or {}
        cov2 = out.get("coverage") or {}
        fc2 = out.get("forecast") or {}
        plan2 = out.get("month_plan") or {}
        bills2 = out.get("bills_summary") or {}
        recs_block = out.get("recommendations") or {}
        red_cands = out.get("active_reduce_candidates") or []
        red_cats = (out.get("reduce_summary") or {}).get(
            "controllable_categories") or []
        rec_top = recs_block.get("top") or []

        # 1. everyday_summary — one-paragraph "where am I" snapshot.
        _income = float(kp.get("income") or 0)
        _spend = float(kp.get("spending") or 0)
        _net = float(kp.get("net") or 0)
        _sr = float(kp.get("savings_rate") or 0)
        _last_month = cov2.get("last_month") or "(no data)"
        _risk = (fc2.get("risk_level") or "unknown").replace("_", " ")
        _safe = fc2.get("safe_to_spend")
        _safe_str = (f"safe-to-spend ${_safe:,.0f}"
                     if isinstance(_safe, (int, float))
                     else "no safe-to-spend (no plan saved)")
        out["everyday_summary"] = (
            f"Latest imported month: {_last_month}. "
            f"Period income ${_income:,.0f}, spending ${_spend:,.0f}, "
            f"net ${_net:,.0f} ({_sr:.0f}% savings rate). "
            f"Forecast risk: {_risk}; {_safe_str}."
        )

        # 2. next_best_move — single concrete action string.
        _next = ""
        if (fc2.get("risk_level") == "danger"):
            _next = ("Forecast risk is DANGER — open Reduce and cancel one "
                     "active subscription this week.")
        elif red_cands:
            _c = red_cands[0]
            _next = (f"Cancel or downgrade {_c.get('merchant')} — "
                     f"~${float(_c.get('annual') or 0):,.0f}/yr.")
        elif red_cats:
            _c = red_cats[0]
            _avg = float(_c.get("monthly_avg") or 0)
            _next = (f"Trim {_c.get('category')} by ~20% "
                     f"(${_avg:,.0f}/mo → ~${_avg*0.80:,.0f}/mo).")
        elif plan2 and not plan2.get("mode"):
            _next = ("No plan saved for the analysis month — open Month "
                     "Plan and pick a mode.")
        elif rec_top:
            _next = f"Top recommendation: {rec_top[0].get('title')}."
        else:
            _next = ("No urgent move detected. Snapshot net worth on the "
                     "Investments page if you haven't this month.")
        out["next_best_move"] = _next

        # 3. reduce_plan — top cut + recurring watch + estimated impact.
        _top_cat = red_cats[0] if red_cats else None
        _top_cand = red_cands[0] if red_cands else None
        out["reduce_plan"] = {
            "top_cut_category": ({
                "category":     _top_cat.get("category"),
                "monthly_avg":  float(_top_cat.get("monthly_avg") or 0),
                "suggested_target": round(
                    float(_top_cat.get("monthly_avg") or 0) * 0.80, 2),
                "save_per_month": round(
                    float(_top_cat.get("monthly_avg") or 0) * 0.20, 2),
                "save_per_year":  round(
                    float(_top_cat.get("monthly_avg") or 0) * 0.20 * 12, 2),
            } if _top_cat else None),
            "top_cancellation_candidate": ({
                "merchant":       _top_cand.get("merchant"),
                "monthly":        float(_top_cand.get("monthly") or 0),
                "annual":         float(_top_cand.get("annual") or 0),
                "flags":          list(_top_cand.get("flags") or []),
                "last_seen":      _top_cand.get("last_seen") or "",
            } if _top_cand else None),
            "weekly_action": (
                f"Cancel or downgrade {_top_cand.get('merchant')}"
                if _top_cand else
                (f"Cut {_top_cat.get('category')} by ~20% this week"
                 if _top_cat else
                 "No urgent reduce action — review subs monthly.")
            ),
            "estimated_annual_impact": round(
                float((_top_cand.get("annual") if _top_cand else 0)
                      or (float(_top_cat.get("monthly_avg") or 0)
                          * 0.20 * 12 if _top_cat else 0)), 2),
            "evidence_grounded_from": [
                "active_reduce_candidates", "reduce_summary",
            ],
        }

        # Pass 35 Phase 1+3: additive trust-layer keys.
        # statement_coverage classifies imported months as complete vs
        # partial so OpenClaw / external agents can avoid treating a
        # half-imported May as if it were a full month. cash_advance_status
        # tells consumers whether later credit-card payments plausibly
        # cover any cash advance — so external coaches never say
        # "pay off $X" without evidence.
        try:
            from utils.insights import statement_coverage as _sc_fn
            out["statement_coverage"] = _sc_fn(conn=conn) or {}
        except Exception:
            out["statement_coverage"] = {}
        try:
            from utils.insights import cash_advance_status as _ca_fn
            out["cash_advance_status"] = _ca_fn(conn=conn) or {}
        except Exception:
            out["cash_advance_status"] = {}

        # Pass 35c: expose Mastercard statement summaries (latest 6)
        # as a read-only list. Carries authoritative interest_charges /
        # fees / cash_advances / new_balance / payment_due_date. NO
        # account numbers, NO file paths, NO raw PDF text — only the
        # bank-provided summary fields. External coaches can use this
        # to ground "your card statement said $X interest" claims.
        try:
            _conn_obj = conn  # may be None
            from utils.database import get_connection as _gc
            _own_conn = False
            if _conn_obj is None:
                _conn_obj = _gc()
                _own_conn = True
            rows = _conn_obj.execute(
                "SELECT account_type, statement_period_label, "
                "       statement_start_date, statement_end_date, "
                "       previous_balance, payments_and_credits, "
                "       transactions_total, cash_advances_total, "
                "       adjustments_total, interest_charges, fees, "
                "       new_balance, minimum_payment_due, "
                "       payment_due_date "
                "FROM statement_summaries "
                "ORDER BY statement_end_date DESC LIMIT 6"
            ).fetchall()
            if _own_conn:
                _conn_obj.close()
            out["statement_summaries"] = [dict(r) for r in rows]
        except Exception:
            out["statement_summaries"] = []

        # Pass 33: monthly_review — full deterministic packet for the
        # "Am I better/worse, what changed, why, what to inspect next?"
        # loop. This is additive — trend_summary and what_changed (below)
        # remain populated for backward-compatible OpenClaw consumers,
        # but new consumers should prefer monthly_review.
        try:
            from utils.insights import monthly_review as _mr_fn
            out["monthly_review"] = _mr_fn(conn=conn) or {}
        except Exception:
            out["monthly_review"] = {
                "available": False, "reason": "build_error",
                "month": "", "prev_month": "",
                "income": 0.0, "spending": 0.0, "net": 0.0,
                "savings_rate": 0.0,
                "prev_income": 0.0, "prev_spending": 0.0,
                "prev_net": 0.0, "prev_savings_rate": 0.0,
                "income_delta": 0.0, "spending_delta": 0.0,
                "net_delta": 0.0,
                "top_increases": [], "top_decreases": [],
                "biggest_mover": None,
                "data_caveats": [],
                "suggested_action": None,
            }

        # 4. trend_summary — what changed since prior month.
        _trend = {"month": None, "spending_delta": None,
                  "income_delta": None, "net_delta": None,
                  "top_increase": None, "top_decrease": None,
                  "note": ""}
        try:
            from utils.insights import monthly_aggregates, category_drift
            aggs = monthly_aggregates(conn=conn) or []
            if len(aggs) >= 2:
                lat = aggs[-1]
                prv = aggs[-2]
                _trend["month"] = lat.get("month")
                _trend["spending_delta"] = round(
                    float(lat.get("spending") or 0)
                    - float(prv.get("spending") or 0), 2)
                _trend["income_delta"] = round(
                    float(lat.get("income") or 0)
                    - float(prv.get("income") or 0), 2)
                _trend["net_delta"] = round(
                    float(lat.get("net") or 0)
                    - float(prv.get("net") or 0), 2)
            drift = category_drift(lookback_months=1, conn=conn) or []
            _NON = {"Transfer", "Transfer Out", "Transfer In",
                    "Credit Card Payment", "Payment", "Savings",
                    "Cancelled", "Income"}
            drift = [d for d in drift if d.get("category") not in _NON]
            ups = sorted(
                (d for d in drift if (d.get("abs_change") or 0) > 0),
                key=lambda d: -float(d.get("abs_change") or 0))
            downs = sorted(
                (d for d in drift if (d.get("abs_change") or 0) < 0),
                key=lambda d: float(d.get("abs_change") or 0))
            if ups:
                _trend["top_increase"] = {
                    "category": ups[0].get("category"),
                    "abs_change": round(float(ups[0].get("abs_change") or 0), 2),
                    "pct_change": round(float(ups[0].get("pct_change") or 0), 1),
                }
            if downs:
                _trend["top_decrease"] = {
                    "category": downs[0].get("category"),
                    "abs_change": round(float(downs[0].get("abs_change") or 0), 2),
                    "pct_change": round(float(downs[0].get("pct_change") or 0), 1),
                }
            if _trend["spending_delta"] is not None:
                _dir = ("more" if _trend["spending_delta"] > 0 else "less")
                _trend["note"] = (
                    f"You spent ${abs(_trend['spending_delta']):,.0f} "
                    f"{_dir} this month than last."
                )
        except Exception:
            pass
        out["trend_summary"] = _trend

        # 5. money_moves_summary — Do Now / Review This Week / Watch
        # grouped from compute_recommendations + the routing rules used
        # by the page. Concise — top 3 items per group.
        try:
            from utils.insights import compute_recommendations
            recs_local = compute_recommendations(conn=conn) or []

            def _route(r: dict) -> str:
                t = r.get("type", "investigate")
                pri = r.get("priority", "low")
                urg = float(r.get("urgency", 0.4))
                impact = float(r.get("annual_impact") or 0)
                if t == "fix" or (pri == "high" and urg >= 0.7):
                    return "do_now"
                if pri == "high" or (pri == "medium" and urg >= 0.4):
                    return "review_week"
                if t in ("cut", "optimize") and impact > 0:
                    return "review_week"  # collapse "save_money"→ review
                if t == "watch":
                    return "watch"
                return "watch"

            buckets = {"do_now": [], "review_week": [], "watch": []}
            for r in recs_local:
                buckets[_route(r)].append({
                    "title":         r.get("title"),
                    "category":      r.get("category"),
                    "annual_impact": float(r.get("annual_impact") or 0),
                    "priority":      r.get("priority"),
                })
            out["money_moves_summary"] = {
                "do_now":       buckets["do_now"][:3],
                "review_week":  buckets["review_week"][:3],
                "watch":        buckets["watch"][:3],
                "total":        len(recs_local),
            }
        except Exception:
            out["money_moves_summary"] = {
                "do_now": [], "review_week": [], "watch": [], "total": 0,
            }
    except Exception:
        # Defensive: never let the new fields crash the export. Each
        # caller (smoke test, OpenClaw) can read them as optional.
        out.setdefault("everyday_summary", "")
        out.setdefault("next_best_move", "")
        out.setdefault("reduce_plan", {})
        out.setdefault("trend_summary", {})
        out.setdefault("monthly_review", {"available": False})
        out.setdefault("money_moves_summary",
                       {"do_now": [], "review_week": [], "watch": [],
                        "total": 0})

    # ── Pass 26: daily-use spine keys ──────────────────────────────
    # Additive aliases / refinements built on the Pass 25 keys so
    # OpenClaw can answer "what matters today?" in one shape. Nothing
    # below removes existing fields.
    try:
        cov3 = out.get("coverage") or {}
        kp3 = out.get("kpis") or {}
        fc3 = out.get("forecast") or {}
        nw3 = out.get("net_worth_summary") or {}
        recs3 = out.get("recommendations") or {}
        rev3 = out.get("review_queue") or {}

        # Data freshness — based on latest_imported_month vs today.
        _stale = False
        _stale_reason = ""
        try:
            last_mo = (cov3.get("last_month") or "")
            if last_mo:
                from datetime import date as _d
                _y, _m = int(last_mo[:4]), int(last_mo[5:7])
                _today = _d.today()
                _months_behind = (_today.year - _y) * 12 + (_today.month - _m)
                if _months_behind >= 2:
                    _stale = True
                    _stale_reason = (
                        f"Latest imported month is {last_mo} "
                        f"(~{_months_behind} months behind today). "
                        f"Import latest statements before acting."
                    )
        except Exception:
            pass

        # 1. today_summary — what matters today, single object.
        out["today_summary"] = {
            "anchor_month":     cov3.get("last_month") or "",
            "anchor_date":      fc3.get("anchor_date") or "",
            "month_net":        float(kp3.get("net") or 0),
            "savings_rate":     float(kp3.get("savings_rate") or 0),
            "forecast_risk":    fc3.get("risk_level") or "unknown",
            "safe_to_spend":    fc3.get("safe_to_spend"),
            "data_stale":       bool(_stale),
            "data_stale_note":  _stale_reason,
            "review_queue":     int(rev3.get("flagged_count") or 0),
        }

        # 2. top_money_move — single concrete action, surfaced here as
        # a structured object (next_best_move stays as the bare string
        # for callers that prefer prose).
        # Pass 29: target_page values use the user-facing sidebar labels
        # introduced by st.navigation (Plan / Net Worth / Reports). Old
        # callers that switched on "Month Plan"/"Investments" will see
        # the new strings — search-and-replace if you previously matched.
        _move_text = out.get("next_best_move") or ""
        _move_target = ""
        _l = _move_text.lower()
        if "cancel" in _l or "downgrade" in _l or "trim" in _l or "reduce" in _l:
            _move_target = "Reduce"
        elif "month plan" in _l or "plan" in _l:
            _move_target = "Plan"
        elif "snapshot" in _l or "investment" in _l or "net worth" in _l:
            _move_target = "Net Worth"
        elif "review" in _l:
            _move_target = "Review queue"
        else:
            _move_target = "Reports"
        out["top_money_move"] = {
            "action":  _move_text,
            "target_page": _move_target,
            "evidence_grounded_from": [
                "next_best_move", "forecast", "active_reduce_candidates",
            ],
        }

        # 3. reduce_plan_v2 — alias of reduce_plan (Pass 25). New name
        # signals the v2 daily-use shape is stable; the older key stays
        # for backward compatibility with Pass 25 consumers.
        out["reduce_plan_v2"] = dict(out.get("reduce_plan") or {})

        # 4. what_changed — alias / superset of trend_summary.
        out["what_changed"] = dict(out.get("trend_summary") or {})

        # 5. net_worth_builder — what move grows net worth next.
        _has_nw = bool((nw3 or {}).get("as_of_date"))
        _missing = list((nw3 or {}).get("missing_inputs") or [])
        if _has_nw:
            _builder_state = "tracking"
            _builder_action = (
                "Move surplus toward your top goal — see Month Plan / "
                "Goals tab. Trim one controllable category to free cash."
            )
        elif _missing:
            _builder_state = "needs_inputs"
            _builder_action = (
                "Add cash + credit-card balances on the Investments → "
                "Cash / debts tab to start net-worth tracking."
            )
        else:
            _builder_state = "empty"
            _builder_action = (
                "Import a holdings CSV on the Investments page, or add "
                "manual cash/debt balances, to take your first net-worth "
                "snapshot."
            )
        out["net_worth_builder"] = {
            "state":          _builder_state,
            "current":        float(nw3.get("net_worth") or 0)
                              if _has_nw else None,
            "as_of":          nw3.get("as_of_date") or "",
            "missing_inputs": _missing,
            "next_action":    _builder_action,
            "evidence_grounded_from": [
                "net_worth_summary", "investments_summary",
            ],
        }

        # 6. open_loops — short list of items the user should review
        # this week (stale data, unsaved plan, flagged queue, missing
        # inputs). Each entry is a self-contained string so the agent
        # can render them as-is.
        _loops: list[str] = []
        if _stale:
            _loops.append(_stale_reason)
        if rev3.get("flagged_count"):
            _loops.append(
                f"{int(rev3['flagged_count'])} flagged transaction(s) "
                f"in the Review queue — bulk-categorise to keep numbers "
                f"accurate."
            )
        _plan_local2 = out.get("month_plan") or {}
        if _plan_local2 and not _plan_local2.get("mode"):
            _loops.append(
                "No plan saved for the analysis month — open Month Plan "
                "and pick a mode."
            )
        if (fc3.get("risk_level") or "") in ("danger", "watch"):
            _loops.append(
                f"Forecast risk is {fc3.get('risk_level')} — review "
                f"safe-to-spend before discretionary purchases."
            )
        if not _has_nw:
            _loops.append(
                "Net worth not tracked yet — add cash/debt balances or "
                "a holdings CSV."
            )
        out["open_loops"] = _loops[:6]
    except Exception:
        out.setdefault("today_summary", {})
        out.setdefault("top_money_move", {})
        out.setdefault("reduce_plan_v2", {})
        out.setdefault("what_changed", {})
        out.setdefault("net_worth_builder", {})
        out.setdefault("open_loops", [])

    # ── Pass 27: weekly money loop + redirect options ─────────────
    # Additive keys that complete the "cut waste → redirect savings →
    # build net worth → review next week" loop. All deterministic, no
    # writes, no secrets.
    try:
        _rp27 = out.get("reduce_plan") or {}
        _top_cand27 = _rp27.get("top_cancellation_candidate") or {}
        _top_cat27 = _rp27.get("top_cut_category") or {}
        _est_save_mo = float(
            (_top_cand27.get("monthly") if _top_cand27 else 0)
            or (_top_cat27.get("save_per_month") if _top_cat27 else 0)
            or 0
        )
        _est_save_yr = float(
            (_top_cand27.get("annual") if _top_cand27 else 0)
            or (_top_cat27.get("save_per_year") if _top_cat27 else 0)
            or _est_save_mo * 12
        )

        # 1. top_reduce_target — single concrete target with savings.
        # Pass 28: first_action and difficulty come from the shared
        # utils.reduce_actions catalog so the Reduce page UI and the
        # OpenClaw context never disagree.
        try:
            from utils.reduce_actions import (
                first_action_for as _first_action_for,
                difficulty_for   as _difficulty_for,
            )
        except Exception:
            _first_action_for = lambda c: ""
            _difficulty_for   = lambda c: "moderate"

        if _top_cand27 and _top_cand27.get("merchant"):
            out["top_reduce_target"] = {
                "kind":            "subscription",
                "label":           f"Cancel or downgrade {_top_cand27['merchant']}",
                "merchant":        _top_cand27.get("merchant"),
                "monthly_savings": _est_save_mo,
                "annual_savings":  _est_save_yr,
                "first_action":    ("Open the merchant's account, "
                                    "cancel or downgrade, then verify on "
                                    "the next statement."),
                "difficulty":      "easy",
                "evidence_grounded_from": ["active_reduce_candidates"],
            }
        elif _top_cat27 and _top_cat27.get("category"):
            _cat_label = _top_cat27.get("category") or ""
            out["top_reduce_target"] = {
                "kind":            "category",
                "label":           f"Trim {_cat_label} by ~20%",
                "category":        _cat_label,
                "monthly_savings": _est_save_mo,
                "annual_savings":  _est_save_yr,
                "first_action":    _first_action_for(_cat_label),
                "difficulty":      _difficulty_for(_cat_label),
                "evidence_grounded_from": ["reduce_summary"],
            }
        else:
            out["top_reduce_target"] = {
                "kind": None, "label": "No urgent reduce target",
                "monthly_savings": 0.0, "annual_savings": 0.0,
                "first_action": "Review subscriptions monthly.",
                "difficulty":   "easy",
                "evidence_grounded_from": [],
            }

        # 2. savings_redirect_options — destinations for the freed cash.
        # We tie the recommended_priority to deterministic state:
        # - DANGER risk + no NW snapshots → cash buffer
        # - high credit card / liability → debt reduction
        # - else investment contribution
        _fc27 = out.get("forecast") or {}
        _nw27 = out.get("net_worth_summary") or {}
        _liab = float(_nw27.get("total_liabilities") or 0)
        _has_nw27 = bool((_nw27 or {}).get("as_of_date"))
        _risk27 = (_fc27.get("risk_level") or "")
        if _risk27 in ("danger", "watch") or not _has_nw27:
            _priority = "cash_buffer"
        elif _liab > 1000:
            _priority = "debt_reduction"
        else:
            _priority = "investment"
        out["savings_redirect_options"] = {
            "monthly_savings_estimate": _est_save_mo,
            "annual_savings_estimate":  _est_save_yr,
            "recommended_priority":     _priority,
            "options": [
                {
                    "key":          "cash_buffer",
                    "label":        "Cash buffer",
                    "rationale":    "Protect 1–2 months of breathing "
                                    "room before optimising elsewhere.",
                    "target_page":  "Plan",
                },
                {
                    "key":          "debt_reduction",
                    "label":        "Debt reduction",
                    "rationale":    "Highest-rate balance first; visible "
                                    "as falling liabilities on Net Worth.",
                    "target_page":  "Net Worth",
                },
                {
                    "key":          "investment",
                    "label":        "Investment contribution",
                    "rationale":    "Move surplus to brokerage; capture "
                                    "the change with a fresh holdings "
                                    "CSV snapshot.",
                    "target_page":  "Net Worth",
                },
                {
                    "key":          "custom_goal",
                    "label":        "Custom goal",
                    "rationale":    "Emergency fund, sub-reduction, "
                                    "etc. Linked metrics auto-track.",
                    "target_page":  "Plan",
                },
            ],
        }

        # 3. reduce_scenario_presets — the 6 canonical what-if presets
        # that the Reduce page surfaces as buttons. Listed here so
        # OpenClaw can answer "what scenarios can I run?" without
        # reading the Streamlit source.
        out["reduce_scenario_presets"] = [
            {"key": "shopping_10",   "label": "Cut Shopping 10%",
             "scenario": {"category_cuts": {"Shopping": 0.10}}},
            {"key": "groceries_10",  "label": "Cut Groceries 10%",
             "scenario": {"category_cuts": {"Groceries": 0.10}}},
            {"key": "food_10",       "label": "Cut Food & Convenience 10%",
             "scenario": {"category_cuts": {"Food & Convenience": 0.10}}},
            {"key": "cancel_one",    "label": "Cancel one subscription",
             "scenario": {"subscription_cancels": [
                 (_top_cand27.get("merchant") or "")
             ]}},
            {"key": "save_100",      "label": "Save $100/month",
             "scenario": {"target_monthly_savings": 100.0}},
            {"key": "tight_week",    "label": "Tight week mode",
             "scenario": {"category_cuts": {
                 "Shopping": 0.20, "Food & Convenience": 0.20,
                 "Groceries": 0.10,
             }}},
        ]

        # 4. weekly_money_loop — the ordered checklist that drives the
        # Reduce / Plan / Net Worth / Review weekly cadence.
        # Pass 29: copy refers to the Pass 29 sidebar labels (Dashboard /
        # Reduce / Plan / Net Worth / Reports).
        out["weekly_money_loop"] = {
            "monday":    "Open Dashboard and read the next-action banner.",
            "tuesday":   ("Open Reduce. Pick This Week's Reduce Plan; "
                          "do the first action."),
            "wednesday": ("Open Reduce → Quick Scenarios; pick a preset "
                          "that matches your spend mood for the week."),
            "thursday":  ("Pick a Savings Redirect destination "
                          "(cash buffer / debt / investment / goal)."),
            "friday":    ("Capture a net-worth snapshot if balances "
                          "changed (Net Worth → Cash / debts)."),
            "weekend":   ("Open Reports → Trends and read the "
                          "'What changed?' section for the 5-minute "
                          "Sunday review."),
        }

        # 5. openclaw_reminder_suggestions — alias of the Pass 22
        # reminder_suggestions list, plus a couple of Pass 27 weekly
        # reminders. Stable Pass 27 name; old key kept.
        _existing_rems = list(out.get("reminder_suggestions") or [])
        _new_rems: list[str] = []
        if _est_save_mo > 0:
            _new_rems.append(
                f"This week: capture ~${_est_save_mo:,.0f}/mo savings "
                f"from the top reduce target."
            )
        if (_today_summary := out.get("today_summary")) and \
           (_today_summary.get("forecast_risk") in ("watch", "danger")):
            _new_rems.append(
                "Mid-week: re-check safe-to-spend before discretionary "
                "purchases."
            )
        if not _has_nw27:
            _new_rems.append(
                "First-time: add a cash + credit card balance to start "
                "net-worth tracking."
            )
        out["openclaw_reminder_suggestions"] = (
            _new_rems + _existing_rems
        )[:8]
    except Exception:
        out.setdefault("top_reduce_target", {})
        out.setdefault("savings_redirect_options", {})
        out.setdefault("reduce_scenario_presets", [])
        out.setdefault("weekly_money_loop", {})
        out.setdefault("openclaw_reminder_suggestions", [])

    # Optional recent transactions slice (off by default).
    if include_recent_transactions:
        try:
            tx_rows = conn.execute(
                "SELECT id, transaction_date, merchant, category, amount, "
                "direction FROM transactions ORDER BY transaction_date DESC "
                "LIMIT ?",
                (_MAX_RECENT_TX,),
            ).fetchall()
            out["recent_transactions"] = [
                {
                    "id":               int(r[0]),
                    "date":             r[1],
                    "merchant":         r[2],
                    "category":         r[3],
                    "amount":           float(r[4] or 0),
                    "direction":        r[5],
                }
                for r in tx_rows
            ]
        except Exception:
            out["recent_transactions"] = []

    if close:
        try:
            conn.close()
        except Exception:
            pass
    return out

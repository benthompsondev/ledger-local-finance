"""
Monthly Plan page.

Four tabs:
  Plan      — choose a mode, generate starter targets, save the plan.
  Forecast  — projected month-end income/spending/net + safe-to-spend.
  Goals     — long-running goals with progress bars.
  Bills     — subscriptions + recurring merchants in one list.

Every number on this page is deterministic (utils/planner.py). No AI
calls; saving requires explicit user click.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from datetime import date

from utils.database import (
    init_db, get_connection,
    upsert_monthly_plan, get_monthly_plan, list_monthly_plans,
    replace_category_targets,
    insert_goal, get_goals, update_goal, delete_goal,
)
from utils.planner import (
    PLAN_MODES, analysis_anchor, generate_starter_plan,
    forecast_month, bills_and_commitments, goal_progress,
)
from utils.ai_explainer import (
    explain_month_plan, explain_forecast, coach_goals,
)
from utils.ai_cache import evidence_hash, get_cached, get_or_compute
from utils.styles import inject_styles
from utils.insights import money_runway, mission_deck

st.set_page_config(page_title="Month Plan · Ledger", page_icon="🗓",
                   layout="wide")
init_db()
inject_styles()

st.title("🗓 Monthly Plan")
st.caption(
    "Set this month's spending target, bills, and savings goal."
)

# Plain-English explanation so first-time users understand the job of the page.
st.markdown(
    "<div style='background:rgba(79,134,198,0.05);"
    "border:1px solid rgba(79,134,198,0.2);"
    "border-left:3px solid #4f86c6;border-radius:8px;"
    "padding:10px 14px;margin-bottom:14px;font-size:0.88rem'>"
    "<b>What this page does.</b> Turns your recent spending into a "
    "monthly plan. It helps answer: how much can I spend, what bills "
    "are coming, and what savings target am I aiming for?"
    "</div>",
    unsafe_allow_html=True,
)


# ── Month Plan copy helpers ───────────────────────────────────────────
def _humanize_basis(basis: str | None) -> str:
    """Map raw basis tokens (recent_avg_minus_5pct, fixed, ...) to plain
    English column copy. Falls through to the original string if unknown."""
    if not basis:
        return "—"
    s = str(basis)
    if s == "fixed":
        return "Fixed / keep stable"
    if s == "volatile_watch":
        return "Volatile — watch only"
    if s == "conservative":
        return "Conservative target"
    if s.startswith("recent_avg_minus_"):
        try:
            pct = int(s.replace("recent_avg_minus_", "").replace("pct", ""))
        except ValueError:
            return "Cut from recent average"
        if pct <= 5:
            return "Slight cut from recent average"
        if pct <= 10:
            return "Moderate cut from recent average"
        if pct <= 20:
            return "Tight cut from recent average"
        return "Aggressive cut from recent average"
    return s.replace("_", " ").capitalize()


def _format_targets_for_display(rows: list[dict]) -> "pd.DataFrame":
    """Return a presentation-friendly DataFrame: dollar formatting,
    plain-English basis, friendly column names."""
    if not rows:
        return pd.DataFrame()
    out = []
    for r in rows:
        avg = float(r.get("monthly_avg") or 0)
        tgt = float(r.get("target_amount") or 0)
        out.append({
            "Category":         r.get("category") or "—",
            "Current avg":      f"${avg:,.0f}/mo" if avg else "—",
            "Target":           f"${tgt:,.0f}/mo" if tgt else "—",
            "Difficulty":       (r.get("difficulty") or "—").title(),
            "Why this target":  _humanize_basis(r.get("basis")),
        })
    return pd.DataFrame(out)

conn = get_connection()
anchor = analysis_anchor(conn=conn)
existing = get_monthly_plan(anchor, conn=conn)
fc = forecast_month(plan_month=anchor, conn=conn)

# Surface statement completeness so partial current months do not silently
# distort plan guidance.
try:
    from utils.insights import statement_coverage as _stmt_cov_fn
    _stmt_cov = _stmt_cov_fn(conn=conn) or {}
except Exception:
    _stmt_cov = {}
_latest_complete = _stmt_cov.get("latest_complete_month") or ""
_partial_now = anchor in (_stmt_cov.get("partial_months") or [])
if _partial_now and _latest_complete:
    st.info(
        f"**Analysis month {anchor} is partial** — "
        f"{_stmt_cov.get('incomplete_reason') or 'not enough data'}. "
        f"For Money Pulse and 'what changed?' comparisons, Ledger "
        f"uses the latest complete month ({_latest_complete}). The "
        f"plan you generate below still targets {anchor}, but the "
        f"numbers you compare it against come from complete months.",
        icon="🗓",
    )

# ── Status strip ────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Analysis month", anchor)
m2.metric("Plan saved",     "Yes" if existing else "No",
          delta=existing.get("mode") if existing else None)
m3.metric("Forecast risk",  fc["risk_level"].replace("_", " ").title())
m4.metric("Anchor date",    fc["anchor_date"])

if fc["risk_level"] == "insufficient_data":
    st.info(
        "Not enough current-month data to forecast yet. Import your "
        "latest statements, then come back."
    )

# A practical planning card inspired by the best "spending plan" apps:
# show the money left after bills and turn it into a daily guardrail.
if existing and fc.get("safe_to_spend") is not None:
    _safe_left = float(fc.get("safe_to_spend") or 0)
    _days_left = max(1, int(fc.get("days_remaining") or 1))
    _safe_day = _safe_left / _days_left
    _bill_hold = float(fc.get("upcoming_bills_total") or 0)
    _goal = float(existing.get("savings_target") or 0)
    _risk = (fc.get("risk_level") or "unknown").replace("_", " ").title()
    st.markdown(
        f"<div style='background:rgba(63,185,80,0.055);"
        f"border:1px solid rgba(63,185,80,0.20);"
        f"border-left:3px solid #3fb950;border-radius:8px;"
        f"padding:12px 16px;margin:12px 0 16px 0'>"
        f"<div style='font-size:0.72rem;color:#8b949e;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px'>"
        f"This month's job</div>"
        f"<div style='font-size:1.05rem;color:#e6edf3;font-weight:700;"
        f"margin-bottom:5px'>You can spend ~${_safe_day:,.0f}/day and keep "
        f"the plan intact.</div>"
        f"<div style='font-size:0.86rem;color:#c9d1d9;line-height:1.5'>"
        f"Safe-to-spend left: ${_safe_left:,.0f}. Bills reserved: "
        f"${_bill_hold:,.0f}. Savings goal: ${_goal:,.0f}. "
        f"Forecast risk: {_risk}.</div>"
        f"</div>".replace("$", r"\$"),
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        "<div style='background:rgba(79,134,198,0.05);"
        "border:1px solid rgba(79,134,198,0.18);"
        "border-left:3px solid #4f86c6;border-radius:8px;"
        "padding:12px 16px;margin:12px 0 16px 0'>"
        "<div style='font-size:0.72rem;color:#8b949e;font-weight:700;"
        "text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px'>"
        "This month's job</div>"
        "<div style='font-size:1.02rem;color:#e6edf3;font-weight:700;"
        "margin-bottom:5px'>Save a plan to unlock safe-to-spend.</div>"
        "<div style='font-size:0.86rem;color:#c9d1d9;line-height:1.5'>"
        "Ledger will reserve expected bills, compare spending against your "
        "target, and turn the rest into a daily guardrail.</div>"
        "</div>",
        unsafe_allow_html=True,
    )

try:
    _runway = money_runway(conn=conn) or {}
    _missions = mission_deck(conn=conn, limit=1) or []
except Exception:
    _runway, _missions = {}, []

if _runway.get("available"):
    _safe = _runway.get("safe_to_spend") or {}
    _top_mission = _missions[0] if _missions else {}
    st.markdown("#### This week inside the plan")
    p1, p2, p3 = st.columns([1, 2, 1])
    with p1:
        st.metric(
            "Runway",
            f"${float(_safe.get('amount') or 0):,.0f}",
            delta=f"{_runway.get('runway_status','watch').title()}",
        )
    with p2:
        if _top_mission:
            st.markdown(f"**{_top_mission.get('title', 'Next money move')}**")
            st.caption(str(_top_mission.get("if_then_plan") or "").replace("$", r"\$"))
        elif _runway.get("partial_month_note"):
            st.caption(str(_runway["partial_month_note"]).replace("$", r"\$"))
        else:
            st.caption("No urgent mission detected. Keep the plan steady.")
    with p3:
        _target = (_top_mission.get("target_page") if _top_mission else "Dashboard")
        _page = {
            "Dashboard": "pages/1_Dashboard.py",
            "Reduce": "pages/11_Reduce.py",
            "Review queue": "pages/8_Review.py",
            "Spending": "pages/5_Spending.py",
            "Plan": "pages/12_Month_Plan.py",
        }.get(_target, "pages/1_Dashboard.py")
        if st.button(_top_mission.get("action_label") or "Open Dashboard",
                     key="plan_weekly_mission_jump",
                     use_container_width=True):
            st.switch_page(_page)

# ── Pass 22: data-caveat banner ────────────────────────────────────
# Surfaces the analysis anchor, missing-income state, and stale-data
# warnings so the user understands what the numbers are based on.
_caveats: list[str] = []
if fc.get("anchor_date") and fc["anchor_date"] != date.today().isoformat():
    _caveats.append(
        f"Analysis anchor is **{fc['anchor_date']}** — Ledger uses your "
        f"latest imported transaction within the month, not today. "
        f"Import newer statements to refresh."
    )
if (fc.get("mtd_income") or 0) <= 0 and fc.get("days_elapsed", 0) > 7:
    _caveats.append(
        "No income recorded month-to-date — forecast assumes the "
        "remaining month follows the recent average."
    )
if (fc.get("days_elapsed") or 0) <= 3 and fc["risk_level"] != "insufficient_data":
    _caveats.append(
        "Only the first few days of the month are observed — early-"
        "month projections are noisy. Re-check after week one."
    )
if _caveats:
    with st.expander("Data caveats", expanded=False):
        for c in _caveats:
            st.caption(f"• {c}")

tab_plan, tab_forecast, tab_goals, tab_bills = st.tabs(
    ["Plan", "Forecast", "Goals", "Bills"]
)

# ══════════════════════════════════════════════════════════════════
# Plan tab
# ══════════════════════════════════════════════════════════════════
with tab_plan:
    st.subheader("Choose a mode and generate a starter plan")
    st.caption(
        "Targets are pulled from your last 3 months of imported data, "
        "then bent according to the mode. You always confirm before "
        "anything is saved."
    )

    # Pass 31: split modes into Basic vs Advanced. The default selector
    # only shows Normal / Tight / Reset — three clearly-distinct stances
    # most users actually pick between. Aggressive Save / Subscription
    # Cleanup / Debt Recovery / Stabilize live behind an Advanced
    # expander so the page reads as a planner, not a config menu.
    _BASIC_MODES = ["normal", "tight", "reset"]
    _MODE_BLURB = {
        "normal":          "Use recent averages and keep things steady.",
        "tight":           "Cut controllable categories harder this month.",
        "reset":           "Recover after a messy month and reduce pressure.",
        "aggressive_save": "Push savings rate to 30% — pair with a Normal next month.",
        "sub_cleanup":     "Cancel ≥ 2 active subscriptions, hold spending steady.",
        "debt_recovery":   "Zero cash advances and zero new fees this month.",
        "stabilize":       "Hold spending at recent average; no new commitments.",
    }
    mode_keys = list(PLAN_MODES.keys())
    mode_labels = {k: v["label"] for k, v in PLAN_MODES.items()}
    default_mode = (existing or {}).get("mode") or "normal"
    if default_mode not in mode_keys:
        default_mode = "normal"

    # If the saved plan uses an advanced mode, surface the picker
    # pre-expanded so the user sees what's selected.
    _advanced_pre_open = default_mode not in _BASIC_MODES

    _basic_choices = [k for k in _BASIC_MODES if k in mode_keys]
    _basic_idx = (_basic_choices.index(default_mode)
                  if default_mode in _basic_choices else 0)
    mode = st.selectbox(
        "Mode",
        _basic_choices,
        index=_basic_idx,
        format_func=lambda k: f"{mode_labels.get(k, k)} — {_MODE_BLURB.get(k, '')}",
        key="plan_mode_basic",
    )
    with st.expander("Advanced modes", expanded=_advanced_pre_open):
        st.caption(
            "Specialised stances for specific situations — use only when "
            "Normal / Tight / Reset don't fit."
        )
        _advanced_choices = [k for k in mode_keys if k not in _BASIC_MODES]
        if _advanced_choices:
            _adv_idx = (_advanced_choices.index(default_mode)
                        if default_mode in _advanced_choices else 0)
            _adv_mode = st.selectbox(
                "Advanced mode",
                _advanced_choices,
                index=_adv_idx,
                format_func=lambda k: (
                    f"{mode_labels.get(k, k)} — {_MODE_BLURB.get(k, '')}"
                ),
                key="plan_mode_advanced",
            )
            if st.button("Use this advanced mode",
                         key="plan_mode_advanced_apply"):
                mode = _adv_mode
                st.session_state["_plan_mode_override"] = _adv_mode
        # If the user previously applied an advanced mode this session,
        # honour it so refreshes don't snap back to Normal.
        _override = st.session_state.get("_plan_mode_override")
        if _override and _override in mode_keys:
            mode = _override

    proposal = generate_starter_plan(mode=mode, conn=conn)

    if proposal["insufficient_data"]:
        st.warning(
            "Limited data for this analysis window. Targets are "
            "best-effort. Import more statements for sharper numbers."
        )

    p1, p2, p3 = st.columns(3)
    p1.metric("Income target",   f"${proposal['income_target']:,.0f}")
    p2.metric("Spending target", f"${proposal['spending_target']:,.0f}")
    p3.metric("Savings target",  f"${proposal['savings_target']:,.0f}",
              delta=f"{proposal['proposed_savings_rate'] * 100:.0f}% rate")

    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);"
        f"border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid #4f86c6;border-radius:8px;"
        f"padding:12px 14px;margin-bottom:12px'>"
        f"<div style='font-weight:700;color:#e6edf3;margin-bottom:4px'>"
        f"Win condition</div>"
        f"<div style='color:#c9d1d9'>{proposal['win_condition']}</div>"
        f"<div style='color:#8b949e;font-size:0.85rem;margin-top:6px'>"
        f"Risk: {proposal['risk_warning']}</div></div>",
        unsafe_allow_html=True,
    )

    # Pass 32: replace flat "Top 3 next moves" bullets with compact
    # action cards. Each card carries a clear button that routes to
    # the page that actually does the work — Reduce for cuts, Review
    # for the queue, Bills tab here for commitments. Keeps Plan focused
    # on the plan itself; the cards become a wayfinder, not another
    # text wall.
    if proposal["next_moves"]:
        st.markdown("**Top 3 next moves**")

        def _route_for_move(text: str) -> tuple[str, str, str]:
            """Return (button label, page path, accent color hex) for a move."""
            t = (text or "").lower()
            if "cancel" in t or "subscription" in t:
                return ("Open Reduce →", "pages/11_Reduce.py", "#3fb950")
            if t.startswith("cut ") or "trim" in t or "controllable" in t:
                return ("Open Reduce →", "pages/11_Reduce.py", "#3fb950")
            if "watch list" in t or "recurring variable" in t:
                return ("Open Reduce →", "pages/11_Reduce.py", "#3fb950")
            if "review" in t and "commitment" in t:
                # Bills tab is on this same page; tell the user.
                return ("See Bills tab below", "", "#4f86c6")
            if "review" in t or "flagged" in t:
                return ("Open Review →", "pages/8_Review.py", "#e3b341")
            if "pause" in t or "fees" in t:
                return ("Open Reduce →", "pages/11_Reduce.py", "#f59e0b")
            return ("Open Reduce →", "pages/11_Reduce.py", "#4f86c6")

        _moves_top3 = proposal["next_moves"][:3]
        _ncols = st.columns(len(_moves_top3))
        for _i, _nm in enumerate(_moves_top3):
            _btn, _target, _color = _route_for_move(_nm)
            with _ncols[_i]:
                st.markdown(
                    f"<div style='background:rgba(255,255,255,0.02);"
                    f"border:1px solid rgba(255,255,255,0.08);"
                    f"border-left:3px solid {_color};border-radius:8px;"
                    f"padding:12px 14px;margin-bottom:8px;height:100%'>"
                    f"<div style='font-size:0.86rem;color:#e6edf3;"
                    f"line-height:1.45'>{_nm}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if _target:
                    if st.button(_btn, key=f"plan_move_btn_{_i}",
                                 use_container_width=True):
                        st.switch_page(_target)
                else:
                    st.caption(_btn)

    st.markdown("**Category targets**")
    # Pass 25: human-friendly columns + currency formatting + plain-language
    # basis. Raw fields stay available behind the expander below.
    df_t_disp = _format_targets_for_display(proposal["category_targets"])
    if not df_t_disp.empty:
        st.dataframe(df_t_disp, use_container_width=True, hide_index=True)
        # Pass 30: "Raw target fields (debug)" only renders when
        # LEDGER_DEV_MODE=1. User feedback: hide debug fields from
        # normal users.
        import os as _os_mp
        if _os_mp.environ.get("LEDGER_DEV_MODE", "").strip().lower() in {
            "1", "true", "yes", "on",
        }:
            with st.expander("Raw target fields (developer)", expanded=False):
                st.dataframe(
                    pd.DataFrame(proposal["category_targets"])[
                        ["category", "monthly_avg", "target_amount",
                         "difficulty", "basis"]
                    ],
                    use_container_width=True, hide_index=True,
                )

    st.divider()
    sc1, sc2 = st.columns([1, 5])
    with sc1:
        if st.button("💾 Save plan", type="primary", key="save_plan_btn"):
            plan_id = upsert_monthly_plan({
                "month":            proposal["month"],
                "mode":             proposal["mode"],
                "income_target":    proposal["income_target"],
                "spending_target": proposal["spending_target"],
                "savings_target":  proposal["savings_target"],
                "notes":           proposal["win_condition"],
            }, conn=conn)
            replace_category_targets(plan_id,
                                     proposal["category_targets"],
                                     conn=conn)
            conn.commit()
            st.success(f"Plan saved for {proposal['month']}.")
            st.rerun()
    with sc2:
        st.caption(
            "Saving overwrites any existing plan for the same month. "
            "Use a different mode anytime — re-running this generator "
            "is non-destructive until you click Save."
        )

    # ── Pass 22: Plan Coach ────────────────────────────────────────
    # Renders deterministic immediately. If MiniMax is configured,
    # there's a Generate button that does ONE cached AI call. This
    # mirrors the Reduce page's lazy pattern so the page never blocks
    # on the network.
    st.markdown('<p class="ledger-section-header">Plan coach</p>',
                unsafe_allow_html=True)

    _plan_for_coach = existing or {
        "month": proposal["month"], "mode": proposal["mode"],
        "income_target": proposal["income_target"],
        "spending_target": proposal["spending_target"],
        "savings_target": proposal["savings_target"],
        "category_targets": proposal["category_targets"],
        "notes": proposal["win_condition"],
    }
    _plan_ev = evidence_hash(_plan_for_coach, fc.get("risk_level"),
                              fc.get("projected_net"))
    _plan_cached, _plan_hash = get_cached("explain_month_plan")
    _plan_coach = (_plan_cached
                   if _plan_hash == _plan_ev else
                   explain_month_plan(_plan_for_coach, fc))
    # Always render the deterministic / cached fields. The Generate
    # button only matters when AI is configured AND not cached for
    # this evidence hash.
    st.markdown(f"**{_plan_coach.get('headline','')}**")
    st.caption(_plan_coach.get("summary", ""))
    for a in (_plan_coach.get("actions") or [])[:3]:
        st.markdown(f"- {a}")
    if _plan_coach.get("risk_note"):
        st.caption(f"Risk: {_plan_coach['risk_note']}")

    if not _plan_coach.get("ai_active") and _plan_hash != _plan_ev:
        if st.button("✨ Generate AI plan summary", key="ai_plan_btn"):
            get_or_compute(
                "explain_month_plan", _plan_ev,
                lambda: explain_month_plan(_plan_for_coach, fc),
                force=True,
            )
            st.rerun()
    st.caption(
        "Grounded in saved plan + forecast. Ledger never invents "
        "numbers; AI only narrates the deterministic packet."
    )

    # Show the currently-saved plan if any.
    if existing:
        with st.expander(f"Currently saved: {existing.get('mode')} "
                         f"({existing['month']})", expanded=False):
            # Pass 25: friendly labels + dollar formatting on the saved plan.
            _LABEL = {
                "income_target":   "Income target",
                "spending_target": "Spending target",
                "savings_target":  "Savings target",
            }
            for k in ("income_target", "spending_target", "savings_target"):
                st.caption(f"{_LABEL[k]}: ${(existing.get(k) or 0):,.0f}/mo")
            if existing.get("category_targets"):
                df_e_disp = _format_targets_for_display(
                    existing["category_targets"]
                )
                if not df_e_disp.empty:
                    st.dataframe(df_e_disp, use_container_width=True,
                                 hide_index=True)
            if st.button("🗑 Delete this plan", key="delete_plan_btn"):
                conn.execute("DELETE FROM category_budget_targets "
                             "WHERE monthly_plan_id=?", (existing["id"],))
                conn.execute("DELETE FROM monthly_plans WHERE id=?",
                             (existing["id"],))
                conn.commit()
                st.success("Plan deleted.")
                st.rerun()

# ══════════════════════════════════════════════════════════════════
# Forecast tab
# ══════════════════════════════════════════════════════════════════
with tab_forecast:
    st.subheader(f"Forecast — {anchor}")

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("MTD spending",        f"${fc['mtd_spending']:,.0f}")
    f2.metric("MTD income",          f"${fc['mtd_income']:,.0f}")
    f3.metric("Projected net",       f"${fc['projected_net']:,.0f}",
              delta=f"{fc['projected_savings_rate']*100:.0f}% rate")
    if fc["safe_to_spend"] is not None:
        f4.metric("Safe to spend",   f"${fc['safe_to_spend']:,.0f}",
                  help="spending_target − (MTD spending + upcoming bills)")
    else:
        f4.metric("Days remaining",  f"{fc['days_remaining']}")

    risk_color = {
        "on_track":          "#34d058",
        "watch":             "#f59e0b",
        "danger":            "#ef4444",
        "insufficient_data": "#8b949e",
    }.get(fc["risk_level"], "#8b949e")

    st.markdown(
        f"<div style='border:1px solid {risk_color}55;"
        f"border-left:3px solid {risk_color};border-radius:6px;"
        f"padding:10px 14px;margin:6px 0'>"
        f"<b style='color:{risk_color}'>"
        f"Risk: {fc['risk_level'].replace('_',' ')}</b><br>"
        f"<span style='color:#c9d1d9;font-size:0.9rem'>"
        f"Day {fc['days_elapsed']} of {fc['days_in_month']} · "
        f"projected income ${fc['projected_income']:,.0f}, "
        f"projected spending ${fc['projected_spending']:,.0f}, "
        f"upcoming-bills ${fc['upcoming_bills_total']:,.0f} "
        f"({fc['upcoming_bills_count']})"
        f"</span></div>",
        unsafe_allow_html=True,
    )

    # Pass 23: variable watch is shown for awareness but NOT included
    # in upcoming_bills_total or safe_to_spend math.
    _vw = fc.get("recurring_variable_watch_total") or 0
    _vc = fc.get("recurring_variable_watch_count") or 0
    if _vw > 0:
        st.caption(
            f"Plus ~${_vw:,.0f}/mo across {_vc} recurring variable "
            "merchant(s) (Groceries, Shopping, Gas, etc.) — watched "
            "separately, not locked into the forecast."
        )

    st.markdown("**Top 3 spending drivers (MTD)**")
    if fc["drivers"]:
        st.dataframe(pd.DataFrame(fc["drivers"]),
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No spending recorded yet this month.")

    if not fc["has_plan"]:
        st.caption(
            "Save a plan on the **Plan** tab to enable safe-to-spend."
        )

    # ── Pass 22: Forecast coach ────────────────────────────────────
    st.markdown('<p class="ledger-section-header">Forecast coach</p>',
                unsafe_allow_html=True)
    _fc_ev = evidence_hash(
        fc.get("risk_level"), fc.get("projected_net"),
        fc.get("safe_to_spend"), fc.get("upcoming_bills_total"),
        (existing or {}).get("savings_target"),
    )
    _fc_cached, _fc_hash = get_cached("explain_forecast")
    _fc_coach = (_fc_cached if _fc_hash == _fc_ev
                  else explain_forecast(fc, existing))
    st.markdown(f"**{_fc_coach.get('risk_explanation','')}**")
    if _fc_coach.get("what_matters_most"):
        st.caption(f"What matters most: {_fc_coach['what_matters_most']}")
    if _fc_coach.get("watch_this_week"):
        st.caption(f"Watch this week: {_fc_coach['watch_this_week']}")
    if _fc_coach.get("next_action"):
        st.markdown(f"**Next action:** {_fc_coach['next_action']}")
    if not _fc_coach.get("ai_active") and _fc_hash != _fc_ev:
        if st.button("✨ Generate AI forecast summary",
                     key="ai_forecast_btn"):
            get_or_compute(
                "explain_forecast", _fc_ev,
                lambda: explain_forecast(fc, existing),
                force=True,
            )
            st.rerun()
    st.caption(
        "Safe-to-spend = spending_target − (MTD spending + upcoming "
        "bills). Tracks what you can spend without breaking the plan."
    )

# ══════════════════════════════════════════════════════════════════
# Goals tab
# ══════════════════════════════════════════════════════════════════
with tab_goals:
    st.subheader("Goals & milestones")
    goals = get_goals(conn=conn, status="active") or []
    if goals:
        progressed = goal_progress(goals, conn=conn)
        for g in progressed:
            with st.container(border=True):
                gc1, gc2 = st.columns([4, 1])
                with gc1:
                    st.markdown(
                        f"**{g['name']}** "
                        f"<span style='color:#8b949e;font-size:0.85rem'>"
                        f"({g.get('type') or 'custom'})</span>",
                        unsafe_allow_html=True,
                    )
                    pct = g.get("progress_pct") or 0
                    cur = g.get("current_amount") or 0
                    tgt = g.get("target_amount") or 0
                    st.progress(min(1.0, max(0.0, pct)))
                    st.caption(
                        f"${cur:,.0f} / ${tgt:,.0f} "
                        f"({pct*100:.0f}%) — "
                        f"next milestone ${g.get('next_milestone',0):,.0f}"
                    )
                    if g.get("linked_metric"):
                        st.caption(
                            f"Auto-tracking from "
                            f"`{g['linked_metric']}`"
                        )
                with gc2:
                    if st.button("Done", key=f"goal_done_{g['id']}"):
                        update_goal(g["id"], {"status": "done"},
                                    conn=conn)
                        conn.commit()
                        st.rerun()
                    if st.button("🗑", key=f"goal_del_{g['id']}"):
                        delete_goal(g["id"], conn=conn)
                        conn.commit()
                        st.rerun()
    else:
        st.info("No active goals yet. Add one below.")

    # ── Pass 22: Goal Progress Coach ───────────────────────────────
    st.markdown('<p class="ledger-section-header">Goal progress coach</p>',
                unsafe_allow_html=True)
    _gp_for_coach = goal_progress(goals, conn=conn) if goals else []
    _gp_ev = evidence_hash([
        {"name": g.get("name"), "pct": g.get("progress_pct"),
         "cur": g.get("current_amount"), "tgt": g.get("target_amount")}
        for g in _gp_for_coach
    ])
    _gp_cached, _gp_hash = get_cached("coach_goals")
    _gp_coach = (_gp_cached if _gp_hash == _gp_ev
                  else coach_goals(_gp_for_coach))
    st.markdown(f"**{_gp_coach.get('progress_summary','')}**")
    if _gp_coach.get("next_milestone"):
        st.caption(_gp_coach["next_milestone"])
    if _gp_coach.get("suggested_action"):
        st.markdown(f"**Suggested:** {_gp_coach['suggested_action']}")
    if _gp_coach.get("caution"):
        st.warning(_gp_coach["caution"])
    if not _gp_coach.get("ai_active") and _gp_hash != _gp_ev and goals:
        if st.button("✨ Generate AI goal summary", key="ai_goal_btn"):
            get_or_compute(
                "coach_goals", _gp_ev,
                lambda: coach_goals(_gp_for_coach), force=True)
            st.rerun()

    st.markdown("---")
    with st.form("add_goal"):
        st.markdown("**New goal**")
        gn1, gn2 = st.columns(2)
        g_name = gn1.text_input("Name*",
                                placeholder="e.g. 6-month emergency fund")
        g_type = gn2.selectbox("Type", [
            "emergency_fund", "cash_buffer", "net_worth",
            "debt_reduction", "investment_contribution",
            "savings_rate", "sub_reduction", "custom",
        ])
        gn3, gn4, gn5 = st.columns(3)
        g_target = gn3.number_input("Target amount*", min_value=0.0,
                                    step=100.0)
        g_current = gn4.number_input("Current amount (manual)",
                                     min_value=0.0, step=100.0)
        g_link = gn5.selectbox(
            "Auto-track from",
            ["", "net_worth", "investments", "cash_balance"],
            help=("If set, current amount is read from Ledger's "
                  "computed value instead of the manual number."),
        )
        g_date = st.date_input("Target date (optional)",
                               value=None, format="YYYY-MM-DD")
        g_notes = st.text_input("Notes")
        if st.form_submit_button("Create goal"):
            if not g_name or g_target <= 0:
                st.error("Name and target amount are required.")
            else:
                insert_goal({
                    "name": g_name, "type": g_type,
                    "target_amount": float(g_target),
                    "current_amount": float(g_current),
                    "target_date": g_date.isoformat() if g_date else None,
                    "linked_metric": g_link or None,
                    "status": "active",
                    "notes": g_notes or None,
                }, conn=conn)
                conn.commit()
                st.success("Goal created.")
                st.rerun()

# ══════════════════════════════════════════════════════════════════
# Bills tab
# ══════════════════════════════════════════════════════════════════
with tab_bills:
    bills = bills_and_commitments(conn=conn)
    fixed     = bills.get("fixed_commitments")            or []
    subs      = bills.get("active_subscriptions")         or []
    variable  = bills.get("recurring_variable_merchants") or []
    stale     = bills.get("stale_or_inactive")            or []

    st.subheader(f"Bills & commitments — {bills['count']} item(s)")

    # Pass 23: header KPIs split into LOCKED vs WATCHED so the user
    # immediately understands what's actually included in forecast
    # math vs what's just a recurring variable expense.
    bk1, bk2, bk3 = st.columns(3)
    bk1.metric(
        "Locked commitments / mo",
        f"${bills.get('commitment_monthly_estimate', 0):,.0f}",
        delta=f"{bills.get('commitment_count', 0)} item(s)",
        help=("Fixed bills (Housing, Utilities) plus active "
              "subscriptions. THIS is what feeds forecast risk and "
              "safe-to-spend."),
    )
    bk2.metric(
        "Variable watch / mo",
        f"${bills.get('variable_monthly_watch', 0):,.0f}",
        delta=f"{bills.get('variable_count', 0)} item(s)",
        help=("Recurring merchants that aren't bills (Groceries, "
              "Shopping, etc.). Watched separately — NOT included in "
              "forecast lock-in."),
    )
    bk3.metric("Stale / inactive", len(stale))

    st.info(
        "**Truth-layer note.** Only fixed bills + active "
        "subscriptions are treated as locked-in commitments. "
        "Recurring grocery/shopping/transfer merchants are watched "
        "separately so forecast risk and safe-to-spend reflect real "
        "obligations only."
    )

    def _show_group(label: str, group_items: list, *,
                    note: str, included: bool) -> None:
        if not group_items:
            return
        st.markdown(f"#### {label} — {len(group_items)} item(s)")
        st.caption(note)
        df = pd.DataFrame(group_items)
        cols = [c for c in [
            "merchant", "category", "est_amount", "frequency",
            "last_seen", "expected_next", "confidence", "reason",
        ] if c in df.columns]
        st.dataframe(df[cols], use_container_width=True,
                     hide_index=True)

    _show_group(
        "Fixed commitments (in forecast)", fixed,
        note=("Recurring obligations in Housing / Utilities "
              "categories. These count toward upcoming_bills_total."),
        included=True,
    )
    _show_group(
        "Active subscriptions (in forecast)", subs,
        note=("Detected by subscription_detective with active "
              "candidates only. Cancelling these is the fastest "
              "controllable savings."),
        included=True,
    )
    _show_group(
        "Recurring variable merchants (watch only)", variable,
        note=("These merchants repeat monthly but aren't bills — "
              "Groceries, Shopping, Gas, etc. Included for awareness; "
              "NEVER added to forecast lock-in. If one of these is "
              "actually a fixed cost, recategorise the underlying "
              "transactions."),
        included=False,
    )
    if stale:
        with st.expander(f"Stale / inactive ({len(stale)})",
                         expanded=False):
            df_s = pd.DataFrame(stale)
            cols = [c for c in [
                "merchant", "category", "est_amount",
                "last_seen", "reason",
            ] if c in df_s.columns]
            st.dataframe(df_s[cols], use_container_width=True,
                         hide_index=True)

    if not bills["items"]:
        st.info("No recurring bills detected yet.")
    else:
        st.caption(
            "`expected_next` is a rough +30-day projection from "
            "`last_seen` — never treat it as certain. Recurring-merchant "
            "detection is not the same as a confirmed bill."
        )

conn.close()

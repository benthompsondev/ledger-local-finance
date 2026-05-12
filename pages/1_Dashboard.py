"""
Dashboard — KPIs, Money Pulse, budget warnings, insights, top merchants.
"""
import streamlit as st
from datetime import date, timedelta
import html
import pandas as pd

from utils.database import (
    init_db, get_connection, get_budgets, has_data, get_active_profile,
)
from utils.analytics import compute_cashflow, period_cashflow, spending_by_category, top_merchants, compute_score, score_label
from utils.insights import (
    generate_insights, budget_vs_actuals, coverage_summary, compute_recommendations,
    subscription_detective, money_runway, mission_deck, found_money,
)
from utils.styles import inject_styles
from utils.ai_explainer import (
    dashboard_copilot, mission_framing, ask_ledger, ASK_PRESETS,
    weekly_review,
)
from utils.ai_cache import evidence_hash, get_or_compute, clear as clear_ai_cache
from utils.ai_config import ai_is_ready
# Pass 30: money_progress / coach_money_progress imports dropped along
# with the Money Progress XP block. choose_mission still used in
# Mission Options panel, money_progress is no longer surfaced.
from utils.momentum import choose_mission, mission_options
from utils.navigation import set_transaction_search
from components.charts import spending_donut, spending_bar, top_merchants_bar, score_gauge
from config.constants import PRIORITY_COLOR, PRIORITY_BG, ALERT_COLOR_OK, BUDGET_NEAR_PCT

st.set_page_config(page_title="Dashboard · Ledger", page_icon="🏠", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Dashboard")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()

# ══════════════════════════════════════════════════════════════════
# FIRST-RUN ONBOARDING
# ══════════════════════════════════════════════════════════════════
if not has_data(conn=conn):
    st.info("👋 Welcome to **Ledger** — your local-first personal finance dashboard.")

    with st.container():
        st.markdown("### Get started in 3 steps")
        s1, s2, s3 = st.columns(3)

        with s1:
            st.markdown("#### 1. Import statements")
            st.markdown(
                "Download your Tangerine Chequing and Mastercard PDFs, "
                "then import them on the **Import** page. "
                "All data stays on your computer — nothing is sent anywhere."
            )
            if st.button("→ Go to Import", type="primary"):
                st.switch_page("pages/3_Import.py")

        with s2:
            st.markdown("#### 2. Set budgets")
            st.markdown(
                "After importing, set monthly budget targets per category in "
                "**Settings → Budgets**. Budget warnings will appear here on the Dashboard."
            )
            if st.button("→ Set Budgets"):
                st.switch_page("pages/9_Settings.py")

        with s3:
            st.markdown("#### 3. Review & customize")
            st.markdown(
                "Check the **Review** page for flagged transactions. "
                "Visit **Settings → Profiles** to switch between budget modes. "
                "Use **Recommendations** to find savings opportunities."
            )
            if st.button("→ Review Flagged"):
                st.switch_page("pages/8_Review.py")

    st.divider()
    st.markdown("**Supported formats:** Tangerine Chequing PDF · Tangerine Mastercard PDF · CSV")
    st.markdown("**Data stays local.** SQLite database at `data/finance.db`.")
    conn.close()
    st.stop()

# ══════════════════════════════════════════════════════════════════
# NORMAL DASHBOARD (data exists)
# ══════════════════════════════════════════════════════════════════

# ── Active profile banner ──────────────────────────────────────────
active_profile = get_active_profile(conn=conn)
if active_profile:
    st.markdown(
        f"<div style='display:inline-flex;align-items:center;gap:8px;"
        f"background:rgba(52,208,88,0.08);border:1px solid rgba(52,208,88,0.2);"
        f"border-radius:8px;padding:6px 14px;margin-bottom:8px;font-size:0.82rem'>"
        f"<span style='color:#34d058;font-weight:600'>● {active_profile['name']}</span>"
        f"<span style='color:#8b949e'>— {active_profile.get('description','')}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

# ── Coverage notice ────────────────────────────────────────────────
cov = coverage_summary(conn=conn)
if cov["gap_months"]:
    st.warning(
        f"Missing data for: **{', '.join(cov['gap_months'])}** — "
        f"import older PDFs on the Import page to fill gaps.",
        icon="📂",
    )

# ── Period selector ────────────────────────────────────────────────
available_months = cov["months"]
today = date.today()

period_options = ["Last 30 days", "Last 90 days", "Last 6 months", "This year", "All time"]
if available_months:
    period_options += [f"Month: {m}" for m in sorted(set(available_months), reverse=True)[:6]]

left_col, acct_col, right_col = st.columns([2, 1, 1])
with acct_col:
    acct_sel = st.selectbox(
        "Account", ["All Accounts", "Chequing", "Savings", "Mastercard"],
        index=0, label_visibility="collapsed",
        help="Filter KPIs by account type.",
    )
    dash_acct_filter = None if acct_sel == "All Accounts" else acct_sel.lower()
with right_col:
    period = st.selectbox("Period", period_options, index=1, label_visibility="collapsed")

if period == "Last 30 days":
    start, end = (today - timedelta(days=30)).isoformat(), today.isoformat()
elif period == "Last 90 days":
    start, end = (today - timedelta(days=90)).isoformat(), today.isoformat()
elif period == "Last 6 months":
    start, end = (today - timedelta(days=180)).isoformat(), today.isoformat()
elif period == "This year":
    start, end = date(today.year, 1, 1).isoformat(), today.isoformat()
elif period == "All time":
    start = cov["first_month"] + "-01" if cov["first_month"] else "2000-01-01"
    end = today.isoformat()
else:
    mo = period.split(": ")[1]
    y, m = int(mo[:4]), int(mo[5:7])
    import calendar
    last_day = calendar.monthrange(y, m)[1]
    start, end = f"{mo}-01", f"{mo}-{last_day:02d}"

# ── KPI strip ──────────────────────────────────────────────────────
exclude_xfers = st.session_state.get("exclude_transfers", False)
cf = compute_cashflow(start, end, exclude_transfers=exclude_xfers, account_type=dash_acct_filter, conn=conn)
score_data = compute_score(conn=conn)
score = score_data["total"]
flagged_count = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_flagged=1").fetchone()[0]
budgets = get_budgets(conn=conn)
recs = compute_recommendations(conn=conn)
high_recs = sum(1 for r in recs if r["priority"] == "high")

# ── Pass 21: tiny plan status banner ────────────────────────────────
# One-line nudge that points at the dedicated Month Plan page. Keeps
# Dashboard focused on analysis without recreating Plan controls here.
try:
    from utils.planner import analysis_anchor as _pa, forecast_month as _pf
    from utils.database import get_monthly_plan as _gmp
    _p_anchor = _pa(conn=conn)
    _p_fc = _pf(plan_month=_p_anchor, conn=conn)
    _p_saved = _gmp(_p_anchor, conn=conn)
except Exception:
    _p_anchor, _p_fc, _p_saved = "", None, None

if _p_fc:
    _r = (_p_fc.get("risk_level") or "").replace("_", " ")
    _rc = {"on track": "#34d058", "watch": "#f59e0b",
           "danger": "#ef4444"}.get(_r, "#8b949e")
    _pst = (
        f"{_p_saved['mode']} plan saved" if _p_saved else "no plan saved"
    )
    # Pass 33: split the right side into two compact buttons — Plan
    # and "What changed?" — so the daily flow has a one-click route to
    # the new Trends "What changed?" lead. No new card, just one extra
    # button in the existing row.
    _pc1, _pc2, _pc3 = st.columns([4, 1, 1])
    with _pc1:
        st.markdown(
            f"<div style='font-size:0.85rem;color:#c9d1d9;"
            f"padding:4px 0;margin-bottom:4px'>"
            f"<b>Month {_p_anchor}</b> · {_pst} · "
            f"projected net <b>${_p_fc.get('projected_net',0):,.0f}</b> · "
            f"<span style='color:{_rc}'>risk: {_r or 'unknown'}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with _pc2:
        if st.button("Month Plan →", key="dash_plan_jump",
                     use_container_width=True):
            st.switch_page("pages/12_Month_Plan.py")
    with _pc3:
        if st.button("What changed? →", key="dash_whatchanged_jump",
                     use_container_width=True,
                     help=("Open Trends — month-over-month deltas, "
                           "biggest movers, and what likely caused them.")):
            st.switch_page("pages/4_Trends.py")

st.markdown('<p class="ledger-section-header">Overview</p>', unsafe_allow_html=True)
_acct_note = "" if not dash_acct_filter else f" · {dash_acct_filter} only"
st.caption(
    f"Period: **{start}** → **{end}**{_acct_note}. "
    "Income = true deposits (payroll, e-Transfers in, interest). "
    "Spending = debits net of refunds. "
    "Internal transfers (CC payments, savings ↔ chequing) are always excluded."
)
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Income",              f"${cf['income']:,.2f}",
          help="All deposits (direction=credit, amount>0). Excludes CC payments, cancelled, and savings pullbacks.")
k2.metric("Spending",            f"${cf['spending']:,.2f}",
          help="All debits net of refund credits. Excludes CC payments (chequing→MC) and cancelled.")
k3.metric("Net",                  f"${cf['net']:,.2f}",    delta=f"{cf['savings_rate']:.1f}% saved",
          help="Income − Spending. Savings rate = Net / Income.")
k4.metric("Money Pulse",    f"{score}/100",          delta=score_label(score))
k5.metric("Flagged",        flagged_count,
          delta="needs review" if flagged_count else "all clear",
          delta_color="inverse" if flagged_count else "normal")
k6.metric("High-Priority Recs", high_recs,
          delta="action needed" if high_recs else "none",
          delta_color="inverse" if high_recs else "normal")

# ── Money Runway + Mission Deck ──────────────────────────────────────
try:
    _runway = money_runway(conn=conn) or {}
except Exception as _e:
    _runway = {"available": False, "reason": f"Runway unavailable: {_e}"}
try:
    _missions = mission_deck(conn=conn, limit=3) or []
except Exception:
    _missions = []
try:
    _wins_packet = found_money(conn=conn) or {}
except Exception:
    _wins_packet = {"wins": []}


def _route_to_page(target: str | None) -> str | None:
    mapping = {
        "Dashboard": "pages/1_Dashboard.py",
        "Spending": "pages/5_Spending.py",
        "Reduce": "pages/11_Reduce.py",
        "Plan": "pages/12_Month_Plan.py",
        "Review queue": "pages/8_Review.py",
        "Transactions": "pages/2_Transactions.py",
    }
    return mapping.get(target or "")


if _runway.get("available"):
    _safe = _runway.get("safe_to_spend") or {}
    _status = str(_runway.get("runway_status") or "watch")
    _status_color = {
        "clear": "#3fb950",
        "watch": "#e3b341",
        "tight": "#f59e0b",
        "danger": "#f85149",
    }.get(_status, "#8b949e")
    st.markdown(
        '<p class="ledger-section-header">Money Runway</p>',
        unsafe_allow_html=True,
    )
    r1, r2, r3, r4 = st.columns([1.2, 1, 1, 2.2])
    with r1:
        st.metric(
            "Safe to spend",
            f"${float(_safe.get('amount') or 0):,.0f}",
            help="Expected income minus spending so far, remaining bills/subscriptions, savings goal, exact debt/fee reserve, and a small buffer.",
        )
    with r2:
        st.metric("Daily pace", f"${float(_safe.get('daily_amount') or 0):,.0f}/day")
    with r3:
        st.markdown(
            f"<div style='font-size:0.82rem;color:#8b949e'>Runway status</div>"
            f"<div style='font-size:1.8rem;color:{_status_color};font-weight:700'>"
            f"{html.escape(_status.title())}</div>",
            unsafe_allow_html=True,
        )
    with r4:
        for why in (_runway.get("why") or [])[:3]:
            st.caption("• " + str(why).replace("$", r"\$"))
        if _runway.get("partial_month_note"):
            st.info(str(_runway["partial_month_note"]).replace("$", r"\$"))

    _upcoming = _runway.get("upcoming") or []
    _watch = _runway.get("watchlists") or []
    if _upcoming or _watch:
        ucol, wcol = st.columns(2)
        with ucol:
            st.markdown("**Upcoming / Reserved**")
            if _upcoming:
                for item in _upcoming[:3]:
                    st.caption(
                        f"{item.get('merchant','Upcoming')} · "
                        f"${float(item.get('amount') or 0):,.0f} · "
                        f"{item.get('confidence','medium')}"
                    )
            else:
                st.caption("No remaining fixed bills detected for this period.")
        with wcol:
            st.markdown("**Watchlists**")
            if _watch:
                for item in _watch[:3]:
                    st.caption(
                        f"{item.get('label','Watch')} · "
                        f"{item.get('pace_status','watch').replace('_',' ')} · "
                        f"${float(item.get('current_amount') or 0):,.0f}"
                    )
            else:
                st.caption("No risky watchlist items detected.")
else:
    st.info(_runway.get("reason") or "Import transactions to unlock Money Runway.")

if _missions:
    st.markdown(
        '<p class="ledger-section-header">Mission Deck</p>',
        unsafe_allow_html=True,
    )
    mcols = st.columns(len(_missions))
    for i, mission in enumerate(_missions):
        with mcols[i]:
            st.markdown(f"**{mission.get('title','Mission')}**")
            st.caption(str(mission.get("why_it_matters") or "").replace("$", r"\$"))
            st.caption(
                f"Effort: {mission.get('effort','5 min')} · "
                f"Impact: {mission.get('impact_label','practical')}"
            )
            st.info(str(mission.get("if_then_plan") or "").replace("$", r"\$"))
            _target = _route_to_page(mission.get("target_page"))
            if _target and st.button(
                mission.get("action_label") or "Open",
                key=f"mission_deck_{i}_{mission.get('id','mission')}",
                use_container_width=True,
            ):
                st.switch_page(_target)

_wins = (_wins_packet.get("wins") or [])[:4]
if _wins:
    st.markdown(
        '<p class="ledger-section-header">Tiny Wins</p>',
        unsafe_allow_html=True,
    )
    wcols = st.columns(min(4, len(_wins)))
    for i, win in enumerate(_wins[:4]):
        with wcols[i]:
            st.markdown(f"**{win.get('title','Win')}**")
            st.caption(str(win.get("detail") or "").replace("$", r"\$"))
    st.divider()

# ══════════════════════════════════════════════════════════════════
# WHERE THE MONEY WENT — top categories teaser (Pass 15)
# Pass 15 manual-test feedback: Breakdown was too far down the page.
# Quick deterministic teaser shows top consumption categories right after
# the KPI strip so the "control center" feel kicks in before the AI loads.
# Full Breakdown (donut + score gauge) still lives further down.
# ══════════════════════════════════════════════════════════════════
_dash_cats = spending_by_category(start, end, account_type=dash_acct_filter, conn=conn)
_dash_consumption = [c for c in _dash_cats
                     if c["category"] not in (
                         "Transfer", "Transfer Out", "Transfer In",
                         "Credit Card Payment", "Payment", "Cancelled")]
if _dash_consumption:
    st.markdown(
        '<p class="ledger-section-header">Where the Money Went</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Top consumption categories for the period above. Click any to "
        "filter Transactions. Open **Reports → Spending** for the full "
        "breakdown, or **Trends** for the month-over-month deltas."
    )
    top5 = sorted(_dash_consumption, key=lambda c: -c["total"])[:5]
    top_total = sum(c["total"] for c in _dash_consumption)
    cols = st.columns(len(top5))
    for i, c in enumerate(top5):
        share = (c["total"] / top_total * 100) if top_total > 0 else 0
        with cols[i]:
            st.metric(
                c["category"],
                f"${c['total']:,.0f}",
                delta=f"{share:.0f}% of spending",
                delta_color="off",
            )
            if st.button(f"Filter →", key=f"top5_filter_{c['category']}",
                         use_container_width=True):
                set_transaction_search(category=c["category"], all_time=True)
                st.switch_page("pages/2_Transactions.py")
    st.divider()

# ══════════════════════════════════════════════════════════════════
# BREAKDOWN — charts are visible by default because this is one of the
# fastest ways to understand where money went. The donut uses its own row so
# the right side never gets clipped.
# ══════════════════════════════════════════════════════════════════
_brk_cats = spending_by_category(start, end, account_type=dash_acct_filter, conn=conn)
_xfer_out_dash = sum(c["total"] for c in _brk_cats if c["category"] == "Transfer Out")
_chart_cats = [c for c in _brk_cats if c["category"] != "Transfer Out"]

st.markdown('<p class="ledger-section-header">Breakdown</p>',
            unsafe_allow_html=True)
if _chart_cats:
    # Keep the bar + donut side-by-side on a balanced split so labels and the
    # legend have enough horizontal room.
    _bk1, _bk2 = st.columns([1, 1], gap="medium")
    with _bk1:
        st.plotly_chart(spending_bar(_chart_cats,
                                     title="Spending by Category"),
                        use_container_width=True,
                        key="dash_breakdown_bar")
    with _bk2:
        st.plotly_chart(spending_donut(_chart_cats),
                        use_container_width=True,
                        key="dash_breakdown_donut")
    if _xfer_out_dash > 0:
        st.caption(
            f"Consumption categories only. "
            f"Outgoing e-Transfers (${_xfer_out_dash:,.2f}) excluded "
            f"from chart, included in Spending total.".replace(
                "$", r"\$")
        )
else:
    st.info("No spending data yet — import statements to get started.")

_top_merchants = top_merchants(start, end, limit=10,
                               account_type=dash_acct_filter, conn=conn)
_tm_col, _hs_col = st.columns([7, 3])
with _tm_col:
    st.markdown(
        '<p class="ledger-section-header">Top Merchants</p>',
        unsafe_allow_html=True,
    )
    if _top_merchants:
        st.plotly_chart(top_merchants_bar(_top_merchants),
                        use_container_width=True,
                        key="dash_topmerch_top")
    else:
        st.info("No merchant data yet for the selected period.")
with _hs_col:
    st.markdown(
        '<p class="ledger-section-header">Money Pulse</p>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(score_gauge(score, score_label(score), title="Money Pulse"),
                    use_container_width=True,
                    key="dash_score_gauge_top")
    _conf_top = score_data.get("data_confidence") or {}
    _conf_top_score = _conf_top.get("score", 0)
    _conf_top_level = _conf_top.get("level", "insufficient")
    _LEVEL_COLOR_TOP = {
        "high":         "#3fb950",
        "medium":       "#e3b341",
        "low":          "#f59e0b",
        "insufficient": "#f85149",
    }
    _LEVEL_LABEL_TOP = {
        "high":         "High", "medium": "Medium",
        "low":          "Low",  "insufficient": "Insufficient",
    }
    _badge_color_top = _LEVEL_COLOR_TOP.get(_conf_top_level, "#8b949e")
    _badge_label_top = _LEVEL_LABEL_TOP.get(_conf_top_level,
                                            _conf_top_level.title())
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:8px;"
        f"margin-top:-4px;margin-bottom:4px'>"
        f"<span style='background:{_badge_color_top};color:#fff;"
        f"padding:2px 8px;border-radius:4px;font-size:11px;"
        f"font-weight:600'>Data confidence: {_badge_label_top} "
        f"({_conf_top_score}/100)</span></div>",
        unsafe_allow_html=True,
    )

# Pass 35b: "Why this score?" panel rewritten so dollar amounts render
# correctly (Streamlit's markdown engine treats `$...$` as LaTeX math
# mode and collapses whitespace inside — that's what produced the
# `feesduringmonthof2026...` mess). We now build plain-text strings
# with every `$` escaped and render each dimension as its own block
# via st.write so no markdown ambiguity remains.
_score_window = (score_data.get("score_window_label")
                 or "the last 90 days")
_partial_list = (score_data.get("statement_coverage") or {}
                 ).get("partial_months") or []
_dims_sorted = sorted(
    score_data.get("dimensions") or [],
    key=lambda d: (d.get("score") or 0) / max(float(d.get("max") or 1), 1),
)
_dims_all = list(score_data.get("dimensions") or [])
_weak_dims = [d for d in _dims_sorted if d.get("sufficient")][:2]


def _improve_hint(dim_key: str, dim: dict) -> str:
    """Deterministic next-month suggestion per dimension."""
    if dim_key == "savings":
        return ("Hold spending flat next month and bank the difference; "
                "target a 20% savings rate for full credit.")
    if dim_key == "diversity":
        return ("Trim the top spending category - open Reduce and pick "
                "one cut to bring it under 25% of total spend.")
    if dim_key == "debt":
        return ("Avoid new interest/fee charges next month - bill "
                "payments on time, no new cash advances, no NSF/"
                "overlimit fees.")
    if dim_key == "consistency":
        return ("Aim for a positive-net month - import a full statement "
                "so Ledger can confirm net >= 0.")
    return "Open Reports -> Trends for the month-over-month detail."


def _safe(s: str) -> str:
    """Escape literal '$' so st.markdown doesn't interpret '$...$' as LaTeX."""
    return str(s or "").replace("$", r"\$")


def _html_safe(s: str) -> str:
    return html.escape(str(s or ""))


with st.expander(
    f"Why this score? - {score}/100 scored on {_safe(_score_window)}",
    expanded=False,
):
    st.metric("Money Pulse", f"{score}/100", delta=score_label(score))
    if _dims_all:
        for d in _dims_all:
            # Render each dimension as its own block. Title line uses
            # markdown (bold), reason+hint use st.caption (no math
            # mode), and finance-charge rows render via st.write so
            # dollar amounts stay literal.
            st.markdown(
                f"**{_safe(d['label'])}** - "
                f"{d['score']:.0f}/{d['max']:.0f}"
            )
            st.caption(_safe(d.get("reason", "")))
            st.caption(_safe(_improve_hint(d["key"], d)))
            # For the Debt dimension specifically, show the exact source
            # of the number — either the Mastercard statement summary
            # (preferred, when present) or the transaction-row fallback
            # — so the user can verify (and so a bogus $577 explanation
            # can't sneak by silently again).
            if d.get("key") == "debt":
                _fc = score_data.get("finance_charges") or {}
                _fc_source = _fc.get("source") or "fallback"
                if _fc_source == "summary":
                    _sums = _fc.get("summary") or []
                    _totals = _fc.get("summary_totals") or {}
                    _int_str  = _safe(
                        f"${float(_totals.get('interest_charges') or 0):,.2f}"
                    )
                    _fee_str  = _safe(
                        f"${float(_totals.get('fees') or 0):,.2f}"
                    )
                    _ca_str   = _safe(
                        f"${float(_totals.get('cash_advances_total') or 0):,.2f}"
                    )
                    st.caption(
                        f"From Mastercard statement summary: "
                        f"{_int_str} interest, {_fee_str} fees, "
                        f"{_ca_str} cash advances."
                    )
                    for _s in _sums[:3]:
                        _lbl = _safe(_s.get("period_label") or "")
                        _i = _safe(f"${float(_s.get('interest_charges') or 0):,.2f}")
                        _f = _safe(f"${float(_s.get('fees') or 0):,.2f}")
                        _c = _safe(f"${float(_s.get('cash_advances_total') or 0):,.2f}")
                        st.write(
                            f"- {_lbl} - interest {_i} - fees {_f}"
                            f" - cash advances {_c}"
                        )
                    if len(_sums) > 3:
                        st.caption(
                            f"...and {len(_sums) - 3} more statement(s) "
                            "in this window."
                        )
                else:
                    st.caption(
                        "Debt & fees is exact-statement only. Import or "
                        "backfill the Mastercard PDF statement summary so "
                        "Ledger can read Interest charges and Fees from the "
                        "bank statement instead of guessing from transactions."
                    )
    else:
        st.caption(
            "Not enough complete data to single out a weakest dimension yet."
        )

    # "Data used" line so the user knows the scoring frame.
    st.caption(
        f"Data used: scored on {_safe(_score_window)}."
        + (" Partial month(s) ignored: "
           + ", ".join(_safe(m) for m in _partial_list) + "."
           if _partial_list else "")
    )
    st.caption(
        "Score uses the latest complete statement month when available. "
        "Partial months remain visible on Transactions but are not "
        "counted toward Money Pulse until they complete."
    )

st.divider()

# ══════════════════════════════════════════════════════════════════
# LEDGER COPILOT — grounded AI summary + This Month's Mission
# ══════════════════════════════════════════════════════════════════
ai_ready, ai_reason = ai_is_ready()

st.markdown('<p class="ledger-section-header">Ledger Copilot</p>', unsafe_allow_html=True)
cop_col, mis_col = st.columns([3, 2])

with cop_col:
    # Cache the copilot output per-session; refreshes on button click
    cop_key = "ai_copilot_cache"
    cop_refresh_col1, cop_refresh_col2 = st.columns([4, 1])
    with cop_refresh_col1:
        if ai_ready:
            st.caption("🧠 **MiniMax Copilot** — grounded in your local data.")
        else:
            st.caption("🧠 **Copilot (deterministic fallback)** — enable AI in Settings for plain-English summaries.")
    with cop_refresh_col2:
        if st.button("↻ Refresh", key="copilot_refresh", use_container_width=True,
                     help="Regenerate the copilot summary from the latest evidence."):
            clear_ai_cache("dashboard_copilot")
            st.session_state["_cop_force"] = True

    # Evidence-hash cache: regenerate only when the underlying data changes
    # (period KPIs, score, flagged count, top recs). Survives navigation.
    _cop_force = st.session_state.pop("_cop_force", False)
    _cop_evidence = (
        round(cf.get("income", 0), 2),
        round(cf.get("spending", 0), 2),
        round(cf.get("savings_rate", 0), 1),
        score,
        flagged_count,
        high_recs,
        start, end, dash_acct_filter,
    )
    _cop_hash = evidence_hash(_cop_evidence)
    with st.spinner("Building summary from your local evidence…"):
        cop = get_or_compute(
            "dashboard_copilot",
            _cop_hash,
            lambda: dashboard_copilot(conn),
            force=_cop_force,
        )

    _border = "#4f86c6" if cop.get("ok") else "#8b949e"
    _badge  = "AI · grounded" if cop.get("ok") else "Deterministic fallback"
    _badge_color = "#4f86c6" if cop.get("ok") else "#8b949e"
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid {_border};border-radius:8px;padding:12px 16px;margin-bottom:4px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin-bottom:6px'>"
        f"<span style='font-size:1.02rem;font-weight:700;color:#e6edf3'>{_html_safe(cop.get('headline',''))}</span>"
        f"<span style='background:{_badge_color};color:#fff;padding:2px 8px;"
        f"border-radius:4px;font-size:10px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:0.06em'>{_badge}</span>"
        f"</div>"
        f"<div style='font-size:0.88rem;color:#c9d1d9;line-height:1.5'>{_html_safe(cop.get('summary',''))}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    moves = cop.get("moves") or []
    if moves:
        st.markdown("**Top 3 next moves:**")
        for m in moves:
            st.markdown(f"- {_safe(m)}")
    grounded = cop.get("grounded_from") or []
    if grounded:
        st.caption("Grounded in: " + " · ".join(grounded)
                   + f"  ·  {cop.get('provider','')}/{cop.get('model','')}")
    if cop.get("error"):
        st.caption(f"⚠ {cop['error']}")

with mis_col:
    # Mission Options v2 — up to 3 ranked options, user selects the active one
    mis_opt_key = "ai_mission_options_cache"
    if mis_opt_key not in st.session_state:
        st.session_state[mis_opt_key] = mission_options(conn=conn, limit=3)
    options = st.session_state[mis_opt_key]
    streaks = (options[0].get("streaks") if options else {}) or {}

    # Active selection (session-only — persistence deferred to Pass 11)
    active_id = st.session_state.get("active_mission_id") or (options[0]["id"] if options else None)

    st.markdown(
        f"<div style='font-size:10px;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:0.08em;color:#3fb950;margin-bottom:6px'>Mission Options ({len(options)})</div>",
        unsafe_allow_html=True,
    )

    _DIFF_COLOR = {"easy": "#3fb950", "moderate": "#e3b341", "hard": "#f85149"}
    for m in options:
        is_active = (m["id"] == active_id)
        border = "#3fb950" if is_active else "rgba(255,255,255,0.08)"
        bg     = "rgba(52,208,88,0.06)" if is_active else "rgba(255,255,255,0.02)"
        current = m.get("current") or 0
        target  = m.get("target")
        progress = m.get("progress_pct") or 0
        diff = m.get("difficulty", "easy")
        diff_col = _DIFF_COLOR.get(diff, "#8b949e")

        with st.container():
            st.markdown(
                f"<div style='background:{bg};border:1px solid {border};"
                f"border-left:3px solid #3fb950;border-radius:8px;padding:10px 12px;margin-bottom:6px'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;"
                f"margin-bottom:4px'>"
                f"<span style='font-size:0.92rem;font-weight:700;color:#e6edf3'>"
                f"{'● ' if is_active else ''}{m.get('title','')}</span>"
                f"<span style='background:{diff_col};color:#fff;padding:1px 7px;"
                f"border-radius:3px;font-size:9px;font-weight:700;text-transform:uppercase'>{diff}</span>"
                f"</div>"
                f"<div style='font-size:0.78rem;color:#8b949e;margin-bottom:4px'>"
                f"{m.get('description','')}</div>"
                f"<div style='font-size:0.75rem;color:#c9d1d9;margin-bottom:4px'>"
                f"<b>Next:</b> {m.get('next_action','')}</div>"
                f"<div style='font-size:0.72rem;color:#8b949e;margin-bottom:4px'>"
                f"<b>Impact:</b> {m.get('expected_impact','')}</div>"
                f"<div style='background:rgba(255,255,255,0.05);border-radius:3px;height:4px;overflow:hidden'>"
                f"<div style='background:#3fb950;height:100%;width:{max(0,min(100,progress))}%'></div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if not is_active:
                if st.button(f"Activate: {m.get('title','')[:28]}", key=f"mis_act_{m['id']}", use_container_width=True):
                    st.session_state["active_mission_id"] = m["id"]
                    st.rerun()

    # Streak chips under the mission stack
    chip_bits = []
    days_ca = streaks.get("days_since_cash_advance")
    if days_ca is not None:
        chip_bits.append(f"🛡 {days_ca}d since last cash advance")
    else:
        chip_bits.append("🛡 No cash advances in imported data")
    if streaks.get("positive_net_streak", 0) > 0:
        chip_bits.append(f"📈 {streaks['positive_net_streak']} positive-net month(s)")
    if streaks.get("flagged_count", 0) == 0:
        chip_bits.append("✅ Review queue clear")
    if chip_bits:
        st.caption(" · ".join(chip_bits))

st.divider()

# ══════════════════════════════════════════════════════════════════
# Pass 30: Money Progress / XP / Level-up Coach REMOVED.
# User feedback: "Money Progress XP / level / level-up coach feels
# pointless and should be removed completely." The XP and momentum
# math still exists in utils/momentum.py for any future re-add, but
# the Dashboard no longer surfaces it. ~155 lines deleted.
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
# WEEKLY REVIEW — Pass 25: collapsed by default. Most users don't need
# the 5-minute check-in every dashboard load; opening the expander
# triggers the AI call lazily.
# ══════════════════════════════════════════════════════════════════
_weekly_expander = st.expander(
    "Weekly Review · 5-minute check-in", expanded=False
)
with _weekly_expander:
    wr_key = "ai_weekly_cache"
    wr_c1, wr_c2 = st.columns([4, 1])
    with wr_c1:
        if ai_ready:
            st.caption("🧠 Grounded in your last 7 days, flagged items, top rec, and subscriptions.")
        else:
            st.caption("5-minute money check-in — running in deterministic mode. Enable AI in Settings.")
    with wr_c2:
        _wr_force = st.button("↻ Refresh review", key="weekly_refresh",
                              use_container_width=True)
        if _wr_force:
            clear_ai_cache("weekly_review")

    # Evidence: flagged count, top rec, current month aggregates.
    _wr_evidence = (
        flagged_count,
        cov.get("last_month"),
        round(cf.get("spending", 0), 2),
        round(cf.get("income", 0), 2),
    )
    _wr_hash = evidence_hash(_wr_evidence)
    with st.spinner("Building this week's check-in…"):
        wr = get_or_compute(
            "weekly_review",
            _wr_hash,
            lambda: weekly_review(conn),
            force=_wr_force,
        )

    _ok = wr.get("ok")
    _border = "#4f86c6" if _ok else "#8b949e"
    _badge = "AI · grounded" if _ok else "Deterministic fallback"
    _badge_color = "#4f86c6" if _ok else "#8b949e"
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid {_border};border-radius:8px;padding:12px 16px;margin-bottom:8px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
        f"<span style='font-size:1rem;font-weight:700;color:#e6edf3'>{wr.get('headline','')}</span>"
        f"<span style='background:{_badge_color};color:#fff;padding:2px 8px;"
        f"border-radius:4px;font-size:10px;font-weight:600'>{_badge}</span>"
        f"</div>"
        f"<div style='font-size:0.88rem;color:#c9d1d9;line-height:1.5'>{wr.get('focus','')}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    checklist = wr.get("checklist") or []
    if checklist:
        st.markdown("**5-minute checklist:**")
        for item in checklist:
            st.markdown(f"- {item}")
    if wr.get("error"):
        st.caption(f"⚠ {wr['error']}")
    st.caption("Grounded in: " + ", ".join(wr.get("grounded_from") or [])
               + f"  ·  {wr.get('provider','—')}/{wr.get('model','—')}")

st.divider()

# ── Budget warnings ────────────────────────────────────────────────
if budgets and available_months:
    latest_month = available_months[-1]
    bva = budget_vs_actuals(latest_month, conn=conn)
    over = [b for b in bva if b["over_budget"]]
    near = [b for b in bva if b["budget"] and not b["over_budget"]
            and b["pct_used"] is not None and b["pct_used"] >= BUDGET_NEAR_PCT]
    if over or near:
        st.markdown('<p class="ledger-section-header">Budget Status — ' + latest_month + '</p>', unsafe_allow_html=True)
        cols = st.columns(min(4, len(over) + len(near)))
        for i, b in enumerate((over + near)[:4]):
            delta_str = f"${abs(b['remaining']):,.2f} {'over' if b['over_budget'] else 'left'}"
            cols[i].metric(
                b["category"],
                f"${b['actual']:,.2f} / ${b['budget']:,.2f}",
                delta=delta_str,
                delta_color="inverse" if b["over_budget"] else "normal",
            )

# ── Score breakdown details ────────────────────────────────────────
# Pass 16: the donut + score gauge moved up to the top "Breakdown" section
# right after the KPI strip (so the user sees it before any AI loads).
# Only the per-dimension score breakdown lives here, in an expander.
conf = score_data.get("data_confidence") or {}
with st.expander("Score breakdown details"):
    st.metric("Money Pulse", f"{score}/100", delta=score_label(score))
    dims = score_data.get("dimensions") or []
    if dims:
        for d in dims:
            _color = "#8b949e" if not d["sufficient"] else "#e6edf3"
            _suffix = " · _insufficient data_" if not d["sufficient"] else ""
            st.markdown(
                f"<div style='padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05)'>"
                f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
                f"<span style='font-weight:600;color:#e6edf3'>{_html_safe(d['label'])}</span>"
                f"<span style='color:{_color};font-variant-numeric:tabular-nums'>"
                f"{d['score']:.0f} / {d['max']:.0f}</span>"
                f"</div>"
                f"<div style='font-size:0.78rem;color:#8b949e;margin-top:2px'>{_html_safe(d['reason'])}{_suffix}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        s1, s2, s3, s4 = st.columns(4)
        w = score_data.get("weights", {})
        s1.metric("Savings",     f"{score_data['savings_score']:.0f}/{w.get('savings',30):.0f}")
        s2.metric("Diversity",   f"{score_data['diversity_score']:.0f}/{w.get('diversity',20):.0f}")
        s3.metric("Debt & fees", f"{score_data['debt_score']:.0f}/{w.get('debt',15):.0f}")
        s4.metric("Consistency", f"{score_data['consistency_score']:.0f}/{w.get('consistency',25):.0f}")

    reasons = conf.get("reasons") or []
    if reasons:
        st.divider()
        st.caption("**Data confidence factors:**")
        for reason in reasons:
            st.caption(f"• {reason}")

# ── Recommendations nudge ───────────────────────────────────────────
# Pass 25: full rec cards live on the Recommendations page. Dashboard
# shows a one-line summary with a jump button so the page stays focused
# on analysis instead of restating the action queue.
if recs:
    top_recs = [r for r in recs if r["priority"] == "high"][:3] or recs[:3]
    annual = sum(r.get("annual_impact", 0) for r in recs
                 if r.get("annual_impact", 0) > 0)
    _rc1, _rc2 = st.columns([5, 1])
    with _rc1:
        _impact_chip = (f" · up to ~${annual:,.0f}/yr potential"
                        if annual > 0 else "")
        st.caption(
            f"💡 **{high_recs} high-priority** · "
            f"{len(recs)} total recommendations{_impact_chip}. "
            f"Top: {top_recs[0]['title'] if top_recs else '—'}"
        )
    with _rc2:
        if st.button("Money Moves →", key="dash_recs_jump",
                     use_container_width=True):
            st.switch_page("pages/10_Recommendations.py")

# ══════════════════════════════════════════════════════════════════
# Pass 25: Subscription Detective moved to Reduce. Dashboard shows a
# one-line nudge so the page stays focused on analysis, not a queue.
# ══════════════════════════════════════════════════════════════════
det = subscription_detective(conn=conn)
if det["count"] > 0:
    st.divider()
    _sd_c1, _sd_c2 = st.columns([5, 1])
    with _sd_c1:
        st.caption(
            f"🔁 **{det['count']} recurring services** detected · "
            f"~${det.get('active_monthly_estimate', det['monthly_estimate']):,.0f}/mo · "
            f"~${det.get('active_annual_total', det['annual_total']):,.0f}/yr. "
            "Cancellation candidates and stale subs live on the Reduce page."
        )
    with _sd_c2:
        if st.button("Reduce →", key="dash_reduce_jump",
                     use_container_width=True):
            st.switch_page("pages/11_Reduce.py")

st.divider()

# ── Insights ───────────────────────────────────────────────────────
insights = generate_insights(conn=conn)
if insights:
    st.markdown('<p class="ledger-section-header">Insights</p>', unsafe_allow_html=True)
    cols = st.columns(min(3, len(insights)))
    icons = {"warning": "⚠️", "good": "✅", "info": "ℹ️", "drift": "📈",
             "subscriptions": "🔁", "price_increase": "💸", "cash_advance": "🚨"}
    for i, ins in enumerate(insights[:3]):
        icon = icons.get(ins["severity"], icons.get(ins["type"], "ℹ️"))
        cols[i % 3].info(f"**{icon} {ins['title']}**\n\n{ins['body']}")

# ══════════════════════════════════════════════════════════════════
# ASK LEDGER — Pass 25: collapsed by default. Free-form question stays
# inside; preset grid moved into the same expander so Dashboard reads
# as analysis-first.
# ══════════════════════════════════════════════════════════════════
ask_key_result = "ai_ask_result"
with st.expander("Ask Ledger · grounded Q&A", expanded=False):
    if ai_ready:
        st.caption("🧠 Presets route to deterministic evidence packets, "
                   "then one MiniMax call writes the answer. Local-only.")
    else:
        st.caption("🧠 Works in deterministic mode when AI is off. "
                   "Enable in Settings for plain-English answers.")

    # Two rows of preset buttons so 9 skills fit cleanly
    _N = len(ASK_PRESETS)
    _row1 = ASK_PRESETS[: (_N + 1) // 2]
    _row2 = ASK_PRESETS[(_N + 1) // 2 :]
    for row in (_row1, _row2):
        if not row:
            continue
        cols = st.columns(len(row))
        for i, (skill, label) in enumerate(row):
            if cols[i].button(label, key=f"ask_btn_{skill}", use_container_width=True):
                with st.spinner("Looking at your data…"):
                    st.session_state[ask_key_result] = ask_ledger(label, conn, skill_override=skill)

    free_q = st.text_input("Or type a free-form question", key="ask_free_q",
                           placeholder="e.g. What changed in my spending this month?")
    if st.button("Ask", key="ask_free_btn", disabled=not free_q.strip()):
        with st.spinner("Looking at your data…"):
            st.session_state[ask_key_result] = ask_ledger(free_q, conn)

ask_res = st.session_state.get(ask_key_result)
if ask_res:
    _ok = ask_res.get("ok")
    _refused = ask_res.get("refused", False)
    if _refused:
        _border = "#f59e0b"
        _badge = "Out of scope — refused"
        _badge_color = "#f59e0b"
    else:
        _border = "#4f86c6" if _ok else "#8b949e"
        _badge  = "AI · grounded" if _ok else "Deterministic answer"
        _badge_color = "#4f86c6" if _ok else "#8b949e"
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid {_border};border-radius:8px;padding:12px 16px;margin-top:6px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
        f"<span style='font-size:11px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:0.06em'>"
        f"Skill: {ask_res.get('skill','?')}</span>"
        f"<span style='background:{_badge_color};color:#fff;padding:2px 8px;border-radius:4px;"
        f"font-size:10px;font-weight:600'>{_badge}</span>"
        f"</div>"
        f"<div style='font-size:0.92rem;color:#e6edf3;line-height:1.5;margin-bottom:6px'>"
        f"{ask_res.get('answer','')}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    bullets = ask_res.get("bullets") or []
    if bullets:
        for b in bullets:
            st.markdown(f"- {b}")
    if ask_res.get("error"):
        st.caption(f"⚠ {ask_res['error']}")
    st.caption(f"Grounded in: {', '.join(ask_res.get('grounded_from') or [])}  ·  "
               f"{ask_res.get('provider','')}/{ask_res.get('model','')}")

st.divider()

# Pass 17: Top Merchants chart was moved up to the Breakdown section. We
# keep ONLY the recent-transactions block here at the bottom so power users
# can still scroll for raw rows. The duplicate chart was removed to avoid
# a second 12-merchant render on the same page.

# Pass 30: Recent Transactions block removed from Dashboard.
# User feedback: "Dashboard recent transactions are redundant because
# Transactions exists." The full Transactions page already provides
# this view with search, filtering, and bulk edits.

conn.close()

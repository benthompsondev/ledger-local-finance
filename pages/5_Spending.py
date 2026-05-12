"""
Spending — category breakdown, budget targets, over-budget warnings.
v2.1: polished budget bars with category colours, stacked area trend, CSS injection.
"""
import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar

from utils.database import init_db, get_connection, get_budgets, upsert_budget, delete_budget
from utils.analytics import compute_cashflow, spending_by_category, top_merchants
from utils.insights import (
    budget_vs_actuals, coverage_summary, all_categories_monthly,
    top_controllable_categories, subscription_detective,
)
# Pass 32: explain_scenario / reduce_workspace_summary / scenario_simulate are
# still used on the Reduce page; Spending no longer renders them.
from utils.styles import inject_styles
from config.categories import CATEGORIES
from components.charts import (
    spending_donut, spending_bar, top_merchants_bar,
)
from config.theme import CATEGORY_COLORS, cat_color, hex_to_rgba
from config.constants import ALERT_COLOR_OVER, ALERT_COLOR_WARNING, BUDGET_NEAR_PCT

st.set_page_config(page_title="Spending · Ledger", page_icon="💸", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Spending")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()
cov = coverage_summary(conn=conn)

# ── Period picker ──────────────────────────────────────────────────────
today = date.today()
period_options = ["Last 30 days", "Last 90 days", "Last 6 months", "This year"]
if cov["months"]:
    period_options += [f"Month: {m}" for m in sorted(cov["months"], reverse=True)[:8]]

filter_col, period_col = st.columns([1, 1])
with filter_col:
    acct_options = ["All Accounts", "Chequing", "Savings", "Mastercard"]
    acct_sel = st.selectbox(
        "Account", acct_options, index=0,
        help="Filter spending by account type. Savings withdrawals appear as internal transfers.",
    )
    acct_filter = None if acct_sel == "All Accounts" else acct_sel.lower()
with period_col:
    period = st.selectbox("Period", period_options, index=1)

if period == "Last 30 days":
    start, end = (today - timedelta(days=30)).isoformat(), today.isoformat()
elif period == "Last 90 days":
    start, end = (today - timedelta(days=90)).isoformat(), today.isoformat()
elif period == "Last 6 months":
    start, end = (today - timedelta(days=180)).isoformat(), today.isoformat()
elif period == "This year":
    start, end = date(today.year, 1, 1).isoformat(), today.isoformat()
else:
    mo = period.split(": ")[1]
    y, m = int(mo[:4]), int(mo[5:7])
    start = f"{mo}-01"
    end   = f"{mo}-{calendar.monthrange(y,m)[1]:02d}"

# ── Toggle: exclude outgoing INTERAC e-Transfers from spending totals ───
_, toggle_col = st.columns([4, 1])
with toggle_col:
    exclude_xfers = st.toggle(
        "Exclude outgoing e-Transfers",
        value=st.session_state.get("exclude_transfers", False),
        help=(
            "When ON, outgoing INTERAC e-Transfers (money sent to people) are removed "
            "from the Spending total. Received e-Transfers (Transfer In) and your own "
            "savings↔chequing moves are always excluded from spending regardless. "
            "OFF by default — sent e-Transfers are real cash outflows."
        ),
        key="exclude_transfers",
    )

# Cashflow KPIs for this period (matches Dashboard exactly)
cf = compute_cashflow(start, end, exclude_transfers=exclude_xfers, account_type=acct_filter, conn=conn)

# Category breakdown — single call, account filter applied at query level
cats = spending_by_category(start, end, account_type=acct_filter, conn=conn)
if exclude_xfers:
    cats = [c for c in cats if c["category"] not in ("Transfer", "Transfer In", "Transfer Out")]
    xfer_out_chart_total = 0.0
else:
    # Donut/merchant charts default to consumption-only; Transfer Out stays in table + KPI
    xfer_out_chart_total = sum(c["total"] for c in cats if c["category"] == "Transfer Out")
chart_cats = cats if exclude_xfers else [c for c in cats if c["category"] != "Transfer Out"]
total_spend = cf["spending"]  # authoritative number — includes Transfer Out

# ── Budget vs actuals for the selected period (if single month) ────────
budgets = get_budgets(conn=conn)
bva = []
if period.startswith("Month: ") and budgets:
    mo = period.split(": ")[1]
    bva = budget_vs_actuals(mo, conn=conn)

# ── KPI strip ──────────────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Summary</p>', unsafe_allow_html=True)
_acct_note = "" if not acct_filter else f" · {acct_sel} only"
_xfer_note = (
    "Outgoing e-Transfers excluded from total." if exclude_xfers
    else "Outgoing e-Transfers included in Total; excluded from charts."
)
st.caption(
    f"Period: **{start}** → **{end}**{_acct_note}. "
    f"Spending = debits net of refund credits. "
    f"CC payments and savings ↔ chequing moves are always excluded. {_xfer_note}"
)
k1, k2, k3 = st.columns(3)
k1.metric("Total Spending",        f"${cf['spending']:,.2f}",
          help="Net of refund credits. Matches the Dashboard for the same period.")
k2.metric("Gross (before refunds)", f"${cf['spending_gross']:,.2f}")
k3.metric("Refund Offsets",         f"${cf['refund_offset']:,.2f}",
          help="MC credits with negative amounts (returns, etc.) applied against spending.")

# ── Budget warnings ────────────────────────────────────────────────────
over = [b for b in bva if b["over_budget"]]
near = [b for b in bva if b["budget"] and not b["over_budget"]
        and b["pct_used"] is not None and b["pct_used"] >= BUDGET_NEAR_PCT]
if over:
    for b in over:
        st.error(f"**{b['category']}** over budget: ${b['actual']:,.2f} / ${b['budget']:,.2f} "
                 f"(${abs(b['remaining']):,.2f} over)")
if near:
    for b in near:
        st.warning(f"**{b['category']}** at {b['pct_used']:.0f}% of budget "
                   f"(${b['actual']:,.2f} / ${b['budget']:,.2f})")

# ── Charts row (Pass 17: bar chart added alongside donut) ──────────────
# Manual-test feedback: a bar chart is easier to compare than a donut. We
# keep the donut for share-of-spend at-a-glance and add a horizontal bar
# chart for direct dollar comparison side-by-side.
c1, c2 = st.columns(2)
with c1:
    if chart_cats:
        st.plotly_chart(spending_bar(chart_cats, title="Spending by Category"),
                        use_container_width=True, key="spend_bar_top")
    else:
        st.info("No spending data for this period.")
with c2:
    if chart_cats:
        st.plotly_chart(spending_donut(chart_cats),
                        use_container_width=True, key="spend_donut_top")

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
merchants = top_merchants(start, end, limit=10, account_type=acct_filter, conn=conn)
if merchants:
    st.plotly_chart(top_merchants_bar(merchants), use_container_width=True,
                    key="spend_topmerch_top")

if xfer_out_chart_total > 0:
    st.caption(
        f"Charts show consumption categories only. "
        f"Outgoing e-Transfers (${xfer_out_chart_total:,.2f}) are in the table below "
        f"and included in Total Spending above."
    )

# ── Category trend table (Pass 31 replaces grouped bars) ──────────────
# User feedback: "Replace with category trend table — Category, This
# month, Last month, Change $, Change %, Status chip." Cleaner than
# any chart for the "what's moving where?" question.
if not period.startswith("Month: "):
    cat_data = all_categories_monthly(conn=conn)
    if cat_data:
        from_month = start[:7]
        to_month   = end[:7]
        filtered_cat: dict[str, list] = {}
        for cat, rows in cat_data.items():
            if cat in {"Transfer", "Transfer Out", "Transfer In",
                       "Credit Card Payment", "Payment", "Cancelled",
                       "Income"}:
                continue
            r = [d for d in rows if from_month <= d["month"] <= to_month]
            if r:
                filtered_cat[cat] = r
        # Two most-recent months in the window (descending order)
        all_months_in_window = sorted({d["month"] for rows in filtered_cat.values()
                                        for d in rows})
        if len(all_months_in_window) >= 2:
            this_m = all_months_in_window[-1]
            last_m = all_months_in_window[-2]

            rows_out: list[dict] = []
            for cat, rs in filtered_cat.items():
                m_to_total = {d["month"]: float(d.get("total") or 0)
                              for d in rs}
                this_v = m_to_total.get(this_m, 0)
                last_v = m_to_total.get(last_m, 0)
                if this_v == 0 and last_v == 0:
                    continue
                delta_d = this_v - last_v
                delta_p = ((delta_d / last_v * 100) if last_v > 0
                           else (100.0 if this_v > 0 else 0.0))
                if abs(delta_d) < 5:
                    status = "→ Stable"
                elif delta_d > 0:
                    status = "▲ Up"
                else:
                    status = "▼ Down"
                rows_out.append({
                    "Category":     cat,
                    "This month":   f"${this_v:,.0f}",
                    "Last month":   f"${last_v:,.0f}",
                    "Change $":     (f"+${delta_d:,.0f}" if delta_d >= 0
                                     else f"-${abs(delta_d):,.0f}"),
                    "Change %":     f"{delta_p:+.0f}%",
                    "Status":       status,
                    "_sort":        abs(delta_d),
                })
            # Sort by biggest absolute movement first
            rows_out.sort(key=lambda r: -r["_sort"])
            for r in rows_out:
                r.pop("_sort", None)

            if rows_out:
                st.markdown(
                    f"<p class='ledger-section-header'>"
                    f"Category trend · {this_m} vs {last_m}</p>",
                    unsafe_allow_html=True,
                )
                import pandas as _pd
                _df_trend = _pd.DataFrame(rows_out)
                st.dataframe(_df_trend, use_container_width=True,
                              hide_index=True)
                st.caption(
                    "Biggest movers first. ▲ Up means spending grew; "
                    "▼ Down means spending fell; → Stable is within $5 "
                    "of last month."
                )

# ── Category breakdown table with budget bars ──────────────────────────
if cats:
    st.markdown('<p class="ledger-section-header">Category Breakdown</p>', unsafe_allow_html=True)
    budget_map = {b["category"]: b for b in bva} if bva else {}

    for cat in cats:
        name      = cat["category"]
        actual    = cat["total"]
        binfo     = budget_map.get(name, {})
        budget_amt = binfo.get("budget")
        color      = cat_color(name)

        # Compute rgba fill for budget bar track
        hx = color.lstrip("#")
        r, g, b_ch = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

        bar_cols = st.columns([0.3, 2.5, 3, 1.2, 1.0])

        # Colour dot
        bar_cols[0].markdown(
            f"<div style='width:10px;height:10px;border-radius:50%;"
            f"background:{color};margin-top:10px'></div>",
            unsafe_allow_html=True,
        )
        bar_cols[1].write(f"**{name}**")
        bar_cols[3].write(f"${actual:,.2f}")
        bar_cols[4].write(f"{cat['pct']:.1f}%")

        if budget_amt:
            pct_used  = actual / budget_amt
            fill_pct  = min(pct_used * 100, 100)
            if actual > budget_amt:
                fill_color, track_color = ALERT_COLOR_OVER, "rgba(239,68,68,0.15)"
            elif pct_used >= BUDGET_NEAR_PCT / 100:
                fill_color, track_color = ALERT_COLOR_WARNING, "rgba(245,158,11,0.15)"
            else:
                fill_color, track_color = color, f"rgba({r},{g},{b_ch},0.15)"

            bar_cols[2].markdown(
                f"<div style='background:{track_color};border-radius:4px;height:8px;margin-top:9px'>"
                f"<div style='background:{fill_color};width:{fill_pct:.0f}%;height:8px;border-radius:4px'></div>"
                f"</div>"
                f"<div style='font-size:10px;color:#8b949e;margin-top:2px'>"
                f"${actual:,.0f} / ${budget_amt:,.0f}</div>",
                unsafe_allow_html=True,
            )
        else:
            fill_pct = min(cat["pct"], 100)
            bar_cols[2].markdown(
                f"<div style='background:rgba({r},{g},{b_ch},0.12);border-radius:4px;height:8px;margin-top:9px'>"
                f"<div style='background:{color};opacity:0.75;width:{fill_pct:.0f}%;height:8px;border-radius:4px'></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

# ── Total row ──────────────────────────────────────────────────────────
if cats:
    st.markdown(
        f"<div style='text-align:right;color:#8b949e;font-size:0.80rem;"
        f"margin-top:4px'>Total: <b style='color:#e6edf3'>${total_spend:,.2f}</b></div>",
        unsafe_allow_html=True,
    )

# ── Pass 32: Reduce / Subscriptions / Simulator surfaces removed ──────
# Spending now answers a single question: "where did money go?"
# Cuts, subscription cancellations, and what-if simulator all live on
# the Reduce page so we don't carry three near-duplicate UIs. A single
# nudge below routes users to Reduce when they want to act.
_red_subs_count = subscription_detective(conn=conn)["count"]
_red_ctrl_count = len(top_controllable_categories(conn=conn, limit=5))
if _red_subs_count > 0 or _red_ctrl_count > 0:
    st.divider()
    _rcol1, _rcol2 = st.columns([5, 1])
    with _rcol1:
        st.caption(
            f"💡 {_red_subs_count} recurring service(s) and "
            f"{_red_ctrl_count} controllable categor"
            f"{'y' if _red_ctrl_count == 1 else 'ies'} detected. "
            "Trim them on the Reduce page — Spending stays focused on "
            "where money went."
        )
    with _rcol2:
        if st.button("Open Reduce →", key="spending_open_reduce_v32",
                     use_container_width=True):
            st.switch_page("pages/11_Reduce.py")

# ── Budget Management ──────────────────────────────────────────────────
st.divider()
with st.expander("Manage Budget Targets"):
    st.caption("Set monthly budget targets per category. "
               "Over-budget warnings appear on Dashboard and Spending pages.")

    if budgets:
        df = pd.DataFrame([
            {"Category": k, "Monthly Budget": f"${v:,.2f}"}
            for k, v in sorted(budgets.items())
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

    with st.form("budget_form"):
        bc1, bc2, bc3 = st.columns(3)
        cat_sel   = bc1.selectbox("Category", sorted(CATEGORIES))
        amt_input = bc2.number_input("Monthly budget ($)", min_value=0.0, value=0.0, step=10.0)
        action    = bc3.radio("Action", ["Set", "Delete"], horizontal=True)
        if st.form_submit_button("Apply"):
            if action == "Set" and amt_input > 0:
                upsert_budget(cat_sel, amt_input, conn=conn)
                st.success(f"Budget set: {cat_sel} = ${amt_input:,.2f}/month")
                st.rerun()
            elif action == "Delete":
                delete_budget(cat_sel, conn=conn)
                st.success(f"Budget removed for {cat_sel}")
                st.rerun()

conn.close()

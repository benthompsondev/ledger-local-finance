"""
Trends & Insights — rolling history, category drift, recurring, YoY, plain-English summaries.
Updates automatically whenever new or backfilled PDFs are imported.
"""
import streamlit as st
import pandas as pd
from datetime import date

from utils.database import init_db, get_connection
from utils.insights import (
    monthly_aggregates, category_drift, recurring_merchants,
    yoy_comparison, generate_insights, all_categories_monthly,
    coverage_summary, monthly_review,
)
from utils.styles import inject_styles
from components.charts import cashflow_bar, spending_donut, category_trend, recurring_table

st.set_page_config(page_title="Trends · Ledger", page_icon="📊", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Trends & Insights")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()
cov = coverage_summary(conn=conn)
aggs = monthly_aggregates(conn=conn)

if len(aggs) < 2:
    st.info("Import at least 2 months of statements to see trends. Go to **Import** to get started.")
    conn.close()
    st.stop()

# ── Pass 33: "What changed?" lead — deterministic monthly_review ──────
# Text-first answer to "what's different vs last month?" pulled from the
# single-source-of-truth helper utils.insights.monthly_review. The same
# packet feeds Reports (summary card) and OpenClaw (monthly_review key).
_mr = monthly_review(conn=conn)

if _mr.get("available"):
    _spend_d  = float(_mr["spending_delta"])
    _income_d = float(_mr["income_delta"])
    _net_d    = float(_mr["net_delta"])

    _spend_dir   = "more"   if _spend_d  > 0 else "less"
    _spend_color = "#f85149" if _spend_d  > 0 else "#3fb950"
    _income_dir  = "more"   if _income_d > 0 else "less"
    _income_color = "#3fb950" if _income_d > 0 else "#f85149"
    _net_dir   = "better" if _net_d   >= 0 else "worse"
    _net_color = "#3fb950" if _net_d   >= 0 else "#f85149"

    # Pass 35b: header reflects the *complete* months compared. When
    # monthly_review ignored a partial month (e.g. May 2026), surface
    # that explicitly so the user knows why the header doesn't show
    # the absolute latest month they imported.
    _ignored = _mr.get("ignored_partial_months") or []
    _ignored_suffix = (
        f" · partial {', '.join(_ignored)} ignored"
        if _ignored else ""
    )
    st.markdown(
        f"<p class='ledger-section-header'>What changed · "
        f"{_mr['month']} vs {_mr['prev_month']}"
        f"{_ignored_suffix}</p>",
        unsafe_allow_html=True,
    )

    _wc1, _wc2, _wc3 = st.columns(3)
    _wc1.markdown(
        f"<div style='font-size:0.85rem;color:#8b949e'>"
        f"Net vs prior month</div>"
        f"<div style='font-size:1.2rem;color:{_net_color};"
        f"font-weight:700'>${abs(_net_d):,.0f} {_net_dir}</div>",
        unsafe_allow_html=True,
    )
    _wc2.markdown(
        f"<div style='font-size:0.85rem;color:#8b949e'>"
        f"Spending vs prior month</div>"
        f"<div style='font-size:1.2rem;color:{_spend_color};"
        f"font-weight:700'>${abs(_spend_d):,.0f} {_spend_dir}</div>",
        unsafe_allow_html=True,
    )
    _wc3.markdown(
        f"<div style='font-size:0.85rem;color:#8b949e'>"
        f"Income vs prior month</div>"
        f"<div style='font-size:1.2rem;color:{_income_color};"
        f"font-weight:700'>${abs(_income_d):,.0f} {_income_dir}</div>",
        unsafe_allow_html=True,
    )

    for _cv in _mr.get("data_caveats") or []:
        st.info(f"⚠ {_cv}", icon="📂")

    # ── Biggest movers table (replaces the prior driver bullets) ────
    _ti = _mr.get("top_increases") or []
    _td = _mr.get("top_decreases") or []
    _ALL_MOVERS = list(_ti) + list(_td)
    if _ALL_MOVERS:
        st.markdown("**Biggest movers**")
        _KIND_LABEL = {
            "fixed":         "Fixed bill",
            "subscription":  "Subscription",
            "controllable":  "Controllable",
            "variable":      "Variable",
        }
        _KIND_INSPECT = {
            "fixed":        "Plan → Bills tab",
            "subscription": "Reduce → Subscriptions",
            "controllable": "Reduce → 3 cuts",
            "variable":     "Spending breakdown",
        }
        _rows = []
        for m in sorted(_ALL_MOVERS,
                        key=lambda r: -abs(float(r["abs_change"]))):
            _rows.append({
                "Category":   m["category"],
                "This month": f"${m['current']:,.0f}",
                "Last month": f"${m['previous']:,.0f}",
                "Δ $":        (f"+${m['abs_change']:,.0f}"
                                 if m["abs_change"] > 0
                                 else f"-${abs(m['abs_change']):,.0f}"),
                "Δ %":        f"{m['pct_change']:+.0f}%",
                "Kind":       _KIND_LABEL.get(m["kind"], m["kind"]),
                "Inspect":    (_KIND_INSPECT.get(m["kind"], "")
                               if m["abs_change"] > 0 else ""),
            })
        st.dataframe(pd.DataFrame(_rows),
                      use_container_width=True, hide_index=True)
        st.caption(
            "Sorted by biggest dollar movement. **Kind** classifies the "
            "category as a fixed bill, subscription, controllable "
            "discretionary, or variable. **Inspect** points at the page "
            "best suited to act on an increase."
        )

    # ── What likely caused this? — top merchants in biggest mover ──
    _bm = _mr.get("biggest_mover")
    if _bm and _bm.get("direction") == "up" and _bm.get("top_merchants"):
        st.markdown("**What likely caused this?**")
        _merchant_lines = []
        for tm in _bm["top_merchants"]:
            _merchant_lines.append(
                f"- **{tm['merchant']}** — ${tm['total']:,.0f} this month "
                f"({tm['tx_count']} transactions)"
            )
        st.markdown(
            f"`{_bm['category']}` jumped ${abs(_bm['abs_change']):,.0f} "
            f"({_bm['pct_change']:+.0f}%). Top merchants in that "
            f"category for {_mr['month']}:".replace("$", r"\$")
        )
        for line in _merchant_lines:
            st.markdown(line.replace("$", r"\$"))
        # Single deterministic suggested action button.
        if _bm.get("inspect_target"):
            if st.button(_bm.get("inspect_label", "Open"),
                         key="trends_what_changed_action"):
                st.switch_page(_bm["inspect_target"])
    elif _bm and _bm.get("direction") == "down":
        st.caption(
            f"📉 Biggest mover is **{_bm['category']}** down "
            f"${abs(_bm['abs_change']):,.0f} — keep doing whatever "
            f"changed.".replace("$", r"\$")
        )

    st.divider()

# ── Tab layout ─────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Cash Flow", "Category Comparison", "Recurring", "Year-over-Year", "Insights"
])

# ─── Cash Flow ──────────────────────────────────────────────────────────
with tab1:
    st.caption(f"Data: {cov['first_month']} → {cov['last_month']} · {cov['total_months']} months")

    if cov["gap_months"]:
        st.warning(f"Gaps in history: **{', '.join(cov['gap_months'])}**. "
                   f"Import missing PDFs to complete the timeline.")

    _has_xfer = any(a.get("transfer_out", 0) > 0 for a in aggs)
    if _has_xfer:
        _, _toggle_col = st.columns([4, 1.2])
        with _toggle_col:
            consumption_only = st.toggle(
                "Consumption only",
                value=False,
                help=(
                    "When ON, outgoing e-Transfers are subtracted from Spending so the bars "
                    "show pure consumption. OFF matches the Dashboard total (e-Transfers "
                    "included as real outflows)."
                ),
                key="trends_consumption_only",
            )
    else:
        consumption_only = False

    if consumption_only:
        plot_aggs = []
        for a in aggs:
            _sp = max(0.0, a["spending"] - a.get("transfer_out", 0))
            _net = a["income"] - _sp
            _sr = round(_net / a["income"] * 100, 1) if a["income"] > 0 else 0.0
            plot_aggs.append({**a, "spending": _sp, "net": _net, "savings_rate": _sr})
    else:
        plot_aggs = aggs

    st.plotly_chart(cashflow_bar(plot_aggs), use_container_width=True)

    # ── Month-over-month narrative ──────────────────────────────────
    # NOTE: every literal '$' is escaped as '\$' because Streamlit treats
    # paired '$...$' tokens as LaTeX math mode — math mode collapses
    # whitespace, which is what produced bug-text like "morecentlyvs" in
    # earlier versions of this caption.
    if len(plot_aggs) >= 2:
        _lat = plot_aggs[-1]
        _prv = plot_aggs[-2]
        _spend_delta  = _lat["spending"] - _prv["spending"]
        _income_delta = _lat["income"]   - _prv["income"]
        _net_word     = "surplus" if _lat["net"] >= 0 else "deficit"
        _spend_dir    = "▲" if _spend_delta  >= 0 else "▼"
        _income_dir   = "▲" if _income_delta >= 0 else "▼"

        _xfer_delta = _lat.get("transfer_out", 0) - _prv.get("transfer_out", 0)
        _xfer_clause = ""
        if (not consumption_only
                and abs(_xfer_delta) >= 100 and _spend_delta != 0
                and abs(_xfer_delta) / abs(_spend_delta) >= 0.4):
            _xfer_clause = (
                f", of which \\${abs(_xfer_delta):,.0f} from "
                f"{'more' if _xfer_delta > 0 else 'fewer'} outgoing e-Transfers"
            )

        st.caption(
            f"**{_lat['month']}:** net **\\${abs(_lat['net']):,.0f} {_net_word}**, "
            f"savings rate {_lat['savings_rate']:.0f}%. "
            f"vs {_prv['month']}: spending {_spend_dir}\\${abs(_spend_delta):,.0f}{_xfer_clause}, "
            f"income {_income_dir}\\${abs(_income_delta):,.0f}."
        )

    # Summary table — always shows canonical totals (matches Dashboard).
    # Pass 17: sorted newest-first so the user's eye lands on the latest
    # month immediately. Net column gets an inline explanation.
    if consumption_only:
        st.caption("Table below shows canonical totals (e-Transfers included) for reference.")
    st.caption("**Net = income − spending.** Positive net = surplus; "
               "negative = deficit (you spent more than you earned that month).")
    df = pd.DataFrame(aggs)[["month","income","spending","net","savings_rate","tx_count"]]
    df = df.sort_values("month", ascending=False)
    df["income"]    = df["income"].map("${:,.2f}".format)
    df["spending"]  = df["spending"].map("${:,.2f}".format)
    df["net"]       = df["net"].map("${:,.2f}".format)
    df["savings_rate"] = df["savings_rate"].map("{:.1f}%".format)
    df.columns = ["Month","Income","Spending","Net","Savings Rate","Transactions"]
    st.dataframe(df, use_container_width=True, hide_index=True)

# ─── Category Comparison ────────────────────────────────────────────────
# Pass 16: this tab now opens with a compact 4-month matrix table — the
# user explicitly asked for "a table showing the last 3-4 months by category,
# like the Cash Flow columns but for category spending". Newest month is the
# leftmost data column so the eye lands on it first. Up/down comparison
# cards live below; full sortable table is in an expander.
with tab2:
    _NON_CONSUMPTION = frozenset({
        "Transfer", "Transfer Out", "Transfer In",
        "Credit Card Payment", "Payment", "Savings", "Cancelled", "Income",
    })

    cc_h1, cc_h2 = st.columns([2.5, 1])
    with cc_h1:
        st.caption(
            "Consumption-only month-over-month comparison. Income, "
            "Transfer Out / Transfer In, Credit Card Payments, and savings "
            "moves are always excluded — this view answers *'what did I spend "
            "more or less on?'*"
        )
    with cc_h2:
        n_months_table = st.selectbox(
            "Last N months in the matrix",
            [3, 4, 5, 6], index=1, key="cat_matrix_n",
            help="Compact column view of the latest N months. Newest is leftmost.",
        )

    # ── 4-month category matrix with change columns (Pass 17) ───────────
    # Pass 17 adds Change $ / Change % / direction arrow columns derived
    # from the latest two months in the window. The user explicitly asked
    # for this so they can see "Subscriptions ▲ +$45 / +12% mo-over-mo"
    # at a glance instead of mentally diffing two columns.
    #
    # Pass 17 taxonomy: explicitly include Utilities / Bills, Entertainment,
    # Home Improvement (newly split categories) so the matrix reflects the
    # statement-aware buckets.
    cat_data = all_categories_monthly(conn=conn)
    if cat_data:
        consumption_cats = [c for c in cat_data.keys()
                            if c not in _NON_CONSUMPTION]
        if consumption_cats:
            all_months = sorted({m["month"] for cat in consumption_cats
                                 for m in cat_data.get(cat, [])})
            recent_months = all_months[-int(n_months_table):]
            recent_months_desc = list(reversed(recent_months))  # newest first

            matrix_rows = []
            for cat in consumption_cats:
                series = {m["month"]: m["total"] for m in cat_data.get(cat, [])}
                row = {"Category": cat}
                for mo in recent_months_desc:
                    row[mo] = float(series.get(mo, 0) or 0)
                row["__total"] = sum(row[mo] for mo in recent_months_desc)
                # Change $ / % between latest and previous month within the window.
                if len(recent_months_desc) >= 2:
                    latest = row[recent_months_desc[0]]
                    prev_  = row[recent_months_desc[1]]
                    row["__change_abs"] = latest - prev_
                    if prev_ > 0:
                        row["__change_pct"] = (latest - prev_) / prev_ * 100
                    elif latest > 0:
                        row["__change_pct"] = 100.0  # new spend
                    else:
                        row["__change_pct"] = 0.0
                else:
                    row["__change_abs"] = 0.0
                    row["__change_pct"] = 0.0
                matrix_rows.append(row)
            matrix_rows.sort(key=lambda r: -r["__total"])
            matrix_rows = [r for r in matrix_rows if r["__total"] > 0]

            if matrix_rows:
                st.markdown(
                    f'<p class="ledger-section-header">'
                    f'Last {len(recent_months_desc)} months by category</p>',
                    unsafe_allow_html=True,
                )
                _newest = recent_months_desc[0] if recent_months_desc else "?"
                _prev = recent_months_desc[1] if len(recent_months_desc) >= 2 else "—"
                st.caption(
                    f"Newest month (**{_newest}**) is the leftmost data column. "
                    f"Change columns compare {_newest} vs {_prev}. "
                    "Sorted by total spend across the window (biggest first)."
                )
                # Build the display DataFrame with formatted dollar/pct/arrow strings.
                disp_rows = []
                for r in matrix_rows:
                    drow = {"Category": r["Category"]}
                    for mo in recent_months_desc:
                        drow[mo] = f"${r[mo]:,.0f}" if r[mo] else "—"
                    if len(recent_months_desc) >= 2:
                        ca = r["__change_abs"]
                        cp = r["__change_pct"]
                        if abs(ca) < 1:
                            drow["Δ $"] = "—"
                            drow["Δ %"] = "—"
                            drow["Dir"] = "•"
                        else:
                            sign = "+" if ca >= 0 else "-"
                            drow["Δ $"] = f"{sign}${abs(ca):,.0f}"
                            drow["Δ %"] = f"{cp:+.0f}%"
                            drow["Dir"] = "▲" if ca > 0 else "▼"
                    disp_rows.append(drow)
                df_matrix = pd.DataFrame(disp_rows)
                st.dataframe(df_matrix, use_container_width=True, hide_index=True)

            # ── Monthly trend chart — moved up (Pass 16) ────────────────
            st.markdown(
                '<p class="ledger-section-header">Category Monthly Trend</p>',
                unsafe_allow_html=True,
            )
            selected = st.selectbox("Select category", sorted(consumption_cats),
                                    key="cat_compare_drill")
            if selected and cat_data.get(selected):
                st.plotly_chart(
                    category_trend(cat_data[selected], selected),
                    use_container_width=True,
                )
        else:
            st.info("No consumption categories with monthly data yet.")
    st.markdown("---")

    # ── Up/Down comparison cards (kept from Pass 15, now below the matrix) ──
    cc_lb1, cc_lb2 = st.columns([3, 1])
    with cc_lb1:
        st.markdown(
            '<p class="ledger-section-header">Movement vs prior period</p>',
            unsafe_allow_html=True,
        )
    with cc_lb2:
        lookback = st.slider("Compare last N months vs prior N months",
                             1, 4, 2, key="cat_compare_lookback")

    drift = category_drift(lookback_months=lookback, conn=conn)
    drift = [d for d in drift if d["category"] not in _NON_CONSUMPTION]

    if not drift:
        st.info(f"Need at least {lookback*2} months of consumption data.")
    else:
        drift_sorted = sorted(drift, key=lambda d: -abs(d["abs_change"]))
        rising  = [d for d in drift_sorted if d["abs_change"] > 0][:6]
        falling = [d for d in drift_sorted if d["abs_change"] < 0][:6]

        rcol, fcol = st.columns(2)

        def _pct_str(v: float) -> str:
            if abs(v) >= 200:
                return f"{v:+.0f}%"
            return f"{v:+.1f}%"

        def _render_card(d: dict, color: str, badge: str) -> str:
            """Single comparison card. Dollar literals escaped (\\$) to avoid
            Streamlit's LaTeX-mode whitespace collapse."""
            arrow = "▲" if d["abs_change"] >= 0 else "▼"
            return (
                f"<div style='background:rgba(255,255,255,0.02);"
                f"border:1px solid rgba(255,255,255,0.07);"
                f"border-left:3px solid {color};border-radius:6px;"
                f"padding:8px 12px;margin-bottom:6px'>"
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;margin-bottom:4px'>"
                f"<span style='font-weight:600;color:#e6edf3;font-size:0.95rem'>"
                f"{d['category']}</span>"
                f"<span style='background:{color};color:#fff;padding:1px 7px;"
                f"border-radius:3px;font-size:10px;font-weight:700'>"
                f"{arrow} {_pct_str(d['pct_change'])}</span>"
                f"</div>"
                f"<div style='font-size:0.78rem;color:#8b949e;"
                f"font-variant-numeric:tabular-nums'>"
                f"${d['recent_avg']:,.0f}/mo recently vs "
                f"${d['prior_avg']:,.0f}/mo before "
                f"(<b style='color:#c9d1d9'>"
                f"{'+' if d['abs_change']>=0 else '-'}${abs(d['abs_change']):,.0f}/mo</b>)"
                f"</div>"
                f"</div>"
            ).replace("$", r"\$")

        with rcol:
            st.markdown(f"**Spending up** ({len(rising)} categories)")
            if not rising:
                st.caption("No categories rose meaningfully.")
            for d in rising:
                st.markdown(_render_card(d, "#f85149", "up"),
                            unsafe_allow_html=True)
        with fcol:
            st.markdown(f"**Spending down** ({len(falling)} categories)")
            if not falling:
                st.caption("No categories fell meaningfully.")
            for d in falling:
                st.markdown(_render_card(d, "#3fb950", "down"),
                            unsafe_allow_html=True)

        with st.expander(f"All {len(drift_sorted)} consumption categories — sortable table"):
            df = pd.DataFrame(drift_sorted)[
                ["category", "recent_avg", "prior_avg", "abs_change", "pct_change"]
            ]
            df["recent_avg"] = df["recent_avg"].map("${:,.2f}".format)
            df["prior_avg"]  = df["prior_avg"].map("${:,.2f}".format)
            df["abs_change"] = df["abs_change"].apply(
                lambda x: f"+${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"
            )
            df["pct_change"] = df["pct_change"].apply(_pct_str)
            df.columns = ["Category", "Recent Avg", "Prior Avg", "Change", "Change %"]
            st.dataframe(df, use_container_width=True, hide_index=True)

# ─── Recurring ─────────────────────────────────────────────────────────
with tab3:
    min_months = st.slider("Minimum months seen", 2, 6, 3)
    rec = recurring_merchants(min_months=min_months, conn=conn)

    if rec:
        total_monthly  = sum(r["avg_amount"] for r in rec)
        total_annual   = total_monthly * 12
        r1, r2, r3 = st.columns(3)
        r1.metric("Recurring merchants", len(rec))
        r2.metric("Est. monthly total",  f"${total_monthly:,.2f}")
        r3.metric("Est. annual total",   f"${total_annual:,.2f}")

        st.plotly_chart(recurring_table(rec), use_container_width=True)

        # Pass 17: dropped the "Possible Price Increases" warnings block.
        # Manual testing: it fired on too many variable merchants and
        # confused real subscription audits. The same signal lives — softer
        # — in Recommendations as `variable_review_*`. A single low-key
        # caption stays so power users know where the signal went.
        narrow_movers = [r for r in rec
                          if r["min_amount"] > 0
                          and 1.10 <= r["max_amount"] / r["min_amount"] <= 2.0
                          and r["max_amount"] > 15]
        if narrow_movers:
            st.caption(
                f"📊 **{len(narrow_movers)}** recurring merchant(s) have 10–100% "
                "month-to-month variation. See **Recommendations → Watch** for "
                "*'Review variable charges from …'* cards before treating any as "
                "real price increases."
            )
    else:
        st.info(f"No merchants appear in {min_months}+ months yet. Import more history.")

# ─── Year-over-Year ──────────────────────────────────────────────────────
with tab4:
    months_list = sorted(set(int(m[5:7]) for m in cov["months"])) if cov["months"] else []
    years_list  = sorted(set(m[:4] for m in cov["months"])) if cov["months"] else []

    if len(years_list) < 2:
        st.info(
            f"Year-over-year comparison needs data from at least 2 calendar years. "
            f"Currently have: {', '.join(years_list) or 'none'}."
        )
    else:
        mo_names = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
                    7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
        selected_month = st.selectbox(
            "Compare calendar month",
            options=months_list,
            format_func=lambda m: mo_names.get(m, str(m)),
        )
        yoy = yoy_comparison(selected_month, conn=conn)
        if yoy:
            df = pd.DataFrame(yoy)
            pivot = df.pivot_table(index="category", columns="year", values="total", aggfunc="sum").fillna(0)
            if not pivot.empty and len(pivot.columns) >= 2:
                pivot = pivot.sort_values(pivot.columns[-1], ascending=False)
                st.dataframe(
                    pivot.style.format("${:,.2f}"),
                    use_container_width=True,
                )
            else:
                st.info(
                    f"No overlapping spending data for {mo_names.get(selected_month, selected_month)} "
                    f"across multiple years yet."
                )
        else:
            st.info(
                f"No spending recorded in {mo_names.get(selected_month, selected_month)} "
                f"for any imported year."
            )

# ─── Insights ──────────────────────────────────────────────────────────
with tab5:
    st.caption("All insights are grounded in your actual imported data — no generic advice.")
    insights = generate_insights(conn=conn)
    if not insights:
        st.info("No notable patterns found yet. Import more months for richer insights.")
    for ins in insights:
        icons = {"warning":"⚠️","good":"✅","info":"ℹ️","drift":"📈",
                 "subscriptions":"🔁","price_increase":"💸","cash_advance":"🚨"}
        icon = icons.get(ins["severity"], icons.get(ins["type"], "ℹ️"))
        if ins["severity"] == "good":
            st.success(f"**{icon} {ins['title']}**\n\n{ins['body']}")
        elif ins["severity"] == "warning":
            st.warning(f"**{icon} {ins['title']}**\n\n{ins['body']}")
        else:
            st.info(f"**{icon} {ins['title']}**\n\n{ins['body']}")

conn.close()

"""
Income — true inflow analysis: sources, trends, stability, concentration risk.

True income (v8) = all credit transactions with amount > 0:
  Includes: payroll (EFT), INTERAC e-Transfers IN (category='Transfer In'),
            bank interest, and any other deposits.
  Excludes: direction='transfer' (savings pullbacks), CC payment credits,
            cancelled transactions, MC refunds (amount < 0).

v1.1: broadened income definition — INTERAC e-Transfers IN now show as income.
"""
import streamlit as st
import pandas as pd
from datetime import date, timedelta
import calendar

from utils.database import init_db, get_connection
from utils.analytics import income_summary, income_by_source, income_monthly_by_source
from utils.insights import coverage_summary
from utils.styles import inject_styles
from config.theme import ACCENT, ACCENT2, TEXT_MUTED, TEXT_BASE, BG_CARD, BORDER, cat_color, hex_to_rgba
from config.constants import ALERT_COLOR_OK, ALERT_COLOR_WARNING, ALERT_COLOR_OVER
import plotly.graph_objects as go
from components.charts import cashflow_bar, _base_layout, _axis_style, _FONT_FAMILY, _FONT_COLOR

st.set_page_config(page_title="Income · Ledger", page_icon="💰", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Income")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()
cov = coverage_summary(conn=conn)

# ── Period picker ───────────────────────────────────────────────────────
today = date.today()
period_options = ["Last 30 days", "Last 90 days", "Last 6 months", "This year"]
if cov["months"]:
    period_options += [f"Month: {m}" for m in sorted(cov["months"], reverse=True)[:8]]

filter_col, period_col, etx_col = st.columns([1, 1, 1.4])
with filter_col:
    acct_options = ["All Accounts", "Chequing", "Savings", "Mastercard"]
    acct_sel = st.selectbox(
        "Account", acct_options, index=0,
        help="Filter income by account type. 'All' shows combined income across all accounts.",
    )
    acct_filter = None if acct_sel == "All Accounts" else acct_sel.lower()
with period_col:
    period = st.selectbox("Period", period_options, index=1)
with etx_col:
    # Pass 14: explicit toggle for received e-Transfers, which can be real
    # income (payments from clients, splits, gifts) OR personal/internal
    # movement (own e-Transfers from one account to another). Default
    # remains "include" for backward-compat with the v8 broad definition.
    exclude_etxfer_in = st.toggle(
        "Exclude received e-Transfers",
        value=False,
        key="income_exclude_etxfer",
        help=("Received e-Transfers (category 'Transfer In') can be real income "
              "or personal/internal movement. Toggle ON to drop them from total, "
              "average, stability, source breakdown, and the trend chart. "
              "OFF (default) matches the v8 broad income definition."),
    )

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

# ── Data ────────────────────────────────────────────────────────────────
summary = income_summary(start, end, account_type=acct_filter,
                         exclude_etransfer_in=exclude_etxfer_in, conn=conn)
sources = income_by_source(start, end, account_type=acct_filter,
                           exclude_etransfer_in=exclude_etxfer_in, conn=conn)
monthly_by_src = income_monthly_by_source(
    start, end, account_type=acct_filter,
    exclude_etransfer_in=exclude_etxfer_in, conn=conn,
)
# Compute the dropped Transfer In total so we can surface what the toggle hid.
_etxfer_in_total = 0.0
if exclude_etxfer_in:
    _etx_row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE transaction_date BETWEEN ? AND ?
          AND direction='credit' AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category = 'Transfer In'
        """,
        (start, end),
    ).fetchone()
    _etxfer_in_total = float(_etx_row["total"] or 0)

total_income     = summary["total"]
avg_monthly      = summary["avg_monthly"]
consistency_pct  = summary["consistency_pct"]
months_active    = summary["months_active"]
monthly_trend    = summary["monthly_trend"]

if total_income == 0:
    st.info(
        "No income found for this period. "
        "Import Tangerine Chequing PDFs — payroll deposits, INTERAC e-Transfers received, "
        "and direct deposits are automatically included as income."
    )
    conn.close()
    st.stop()

# ── KPI strip ────────────────────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Overview</p>', unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Income",     f"${total_income:,.2f}")
k2.metric("Avg / Month",      f"${avg_monthly:,.2f}")
if consistency_pct is None:
    k3.metric("Income Stability", "—",
              delta=f"{months_active} month — need 2+",
              delta_color="off",
              help="Stability is measured across months. Import a second month to see this.")
else:
    k3.metric("Income Stability", f"{consistency_pct:.0f}%",
              delta="consistent" if consistency_pct >= 80 else "variable",
              delta_color="normal" if consistency_pct >= 80 else "inverse")
k4.metric("Months Active",    months_active)

if exclude_etxfer_in:
    st.caption(
        f"Mode: **Excluding received e-Transfers** — \\${_etxfer_in_total:,.0f} of "
        f"`Transfer In` rows are dropped from total, avg/month, stability, sources, "
        f"and the monthly trend. Internal savings transfers and CC payment credits "
        f"are always excluded. Toggle OFF in the header to include received "
        f"e-Transfers as income."
    )
else:
    st.caption(
        "Mode: **Including received e-Transfers** — payroll deposits, received "
        "e-Transfers, insurance/reimbursements, bank interest, and rewards all count. "
        "Internal savings transfers and CC payment credits are always excluded. "
        "Use the **Exclude received e-Transfers** toggle in the header if those "
        "rows are personal pass-throughs rather than real income."
    )

st.divider()

# ── Source breakdown + trend ─────────────────────────────────────────────
col_left, col_right = st.columns([1, 1])

with col_left:
    st.markdown('<p class="ledger-section-header">Income by Source</p>', unsafe_allow_html=True)

    # Donut chart for source breakdown
    if sources:
        src_labels  = [s["source"]  for s in sources]
        src_values  = [s["total"]   for s in sources]
        src_pcts    = [s["pct"]     for s in sources]

        # Color palette for income sources (distinct from spending categories)
        INCOME_SOURCE_COLORS = [
            "#34d058",   # green — primary payroll
            "#60a5fa",   # blue
            "#a78bfa",   # violet
            "#fbbf24",   # amber
            "#22d3ee",   # cyan
            "#f472b6",   # pink
            "#86efac",   # pale green
        ]
        src_colors = [INCOME_SOURCE_COLORS[i % len(INCOME_SOURCE_COLORS)] for i in range(len(sources))]

        # Text labels for top slices
        text_labels = []
        for i, (lbl, val) in enumerate(zip(src_labels, src_values)):
            if i < 4 and val / total_income >= 0.04:
                text_labels.append(f"{lbl}<br><b>{val/total_income*100:.1f}%</b>")
            else:
                text_labels.append("")

        fig_donut = go.Figure()

        # Pie trace (no legend — driven by scatter below)
        fig_donut.add_trace(go.Pie(
            labels=src_labels, values=src_values, text=text_labels,
            hole=0.52,
            marker=dict(colors=src_colors, line=dict(color="#0d1117", width=2)),
            textinfo="text",
            textposition="outside",
            textfont=dict(size=11, color=_FONT_COLOR),
            hovertemplate="<b>%{label}</b><br>$%{value:,.2f} (%{percent})<extra></extra>",
            pull=[0.03 if i == 0 else 0 for i in range(len(src_labels))],
            sort=False,
            direction="clockwise",
            showlegend=False,
        ))

        # Invisible scatter traces for circle legend markers
        for lbl, color in zip(src_labels, src_colors):
            fig_donut.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers", name=lbl,
                marker=dict(symbol="circle", size=10, color=color, line=dict(width=0)),
                showlegend=True, hoverinfo="skip",
            ))

        fig_donut.add_annotation(
            text=f"<b>${total_income:,.0f}</b><br><span style='font-size:10px'>total</span>",
            x=0.5, y=0.5,
            font=dict(size=15, color=_FONT_COLOR, family=_FONT_FAMILY),
            showarrow=False, align="center",
        )
        fig_donut.update_layout(
            **_base_layout(
                title=dict(text="Income Sources", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
                showlegend=True,
                legend=dict(
                    orientation="v", x=1.02, y=0.5, xanchor="left", yanchor="middle",
                    font=dict(size=11), bgcolor="rgba(0,0,0,0)", itemsizing="constant",
                ),
                margin=dict(l=0, r=160, t=52, b=20),
            )
        )
        st.plotly_chart(fig_donut, use_container_width=True)

with col_right:
    st.markdown('<p class="ledger-section-header">Monthly Trend</p>', unsafe_allow_html=True)

    if monthly_trend:
        months_x = [m["month"] for m in monthly_trend]
        totals_y = [m["total"] for m in monthly_trend]

        fig_trend = go.Figure()
        fig_trend.add_trace(go.Bar(
            x=months_x, y=totals_y,
            name="Income",
            marker=dict(color=ACCENT, opacity=0.85, line=dict(width=0)),
            hovertemplate="<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
        ))
        # Avg line
        if len(months_x) >= 2:
            fig_trend.add_trace(go.Scatter(
                x=months_x, y=[avg_monthly] * len(months_x),
                name=f"Avg ${avg_monthly:,.0f}",
                mode="lines",
                line=dict(color=ACCENT2, width=1.5, dash="dot"),
                hoverinfo="skip",
            ))
        fig_trend.update_layout(
            **_base_layout(
                title=dict(text="Monthly Income", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
                barmode="overlay",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11), bgcolor="rgba(0,0,0,0)",
                ),
            )
        )
        fig_trend.update_xaxes(**_axis_style(show_grid=False, tickangle=-30))
        fig_trend.update_yaxes(**_axis_style(tickprefix="$", tickformat=",.0f"))
        st.plotly_chart(fig_trend, use_container_width=True)

st.divider()

# ── Source breakdown table ────────────────────────────────────────────────
if sources:
    st.markdown('<p class="ledger-section-header">Source Breakdown</p>', unsafe_allow_html=True)

    # Data-driven income type summary
    _type_totals: dict[str, float] = {}
    for s in sources:
        _cat = s.get("category") or "Other"
        _type_totals[_cat] = _type_totals.get(_cat, 0.0) + s["total"]
    _type_parts = [
        f"**{cat}** ${amt:,.0f} ({amt / total_income * 100:.0f}%)"
        for cat, amt in sorted(_type_totals.items(), key=lambda x: -x[1])
    ]
    st.caption("By type: " + "  ·  ".join(_type_parts))

    INCOME_SOURCE_COLORS = [
        "#34d058", "#60a5fa", "#a78bfa", "#fbbf24", "#22d3ee", "#f472b6", "#86efac",
    ]

    for i, src in enumerate(sources):
        color    = INCOME_SOURCE_COLORS[i % len(INCOME_SOURCE_COLORS)]
        hx       = color.lstrip("#")
        r, g, b  = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        fill_pct = src["pct"]

        bar_cols = st.columns([0.3, 2.5, 3, 1.2, 1.0])
        bar_cols[0].markdown(
            f"<div style='width:10px;height:10px;border-radius:50%;"
            f"background:{color};margin-top:10px'></div>",
            unsafe_allow_html=True,
        )
        bar_cols[1].write(f"**{src['source']}**  ·  *{src.get('category', '')}*")
        bar_cols[2].markdown(
            f"<div style='background:rgba({r},{g},{b},0.12);border-radius:4px;height:8px;margin-top:9px'>"
            f"<div style='background:{color};opacity:0.85;width:{fill_pct:.0f}%;height:8px;border-radius:4px'></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        bar_cols[3].write(f"${src['total']:,.2f}")
        bar_cols[4].write(f"{src['pct']:.1f}%")

    # Total row
    st.markdown(
        f"<div style='text-align:right;color:#8b949e;font-size:0.80rem;margin-top:4px'>"
        f"Total: <b style='color:#e6edf3'>${total_income:,.2f}</b></div>",
        unsafe_allow_html=True,
    )

st.divider()

# ── Stability + concentration risk ───────────────────────────────────────
st.markdown('<p class="ledger-section-header">Income Health</p>', unsafe_allow_html=True)
h1, h2 = st.columns(2)

with h1:
    # Stability card
    if consistency_pct is None:
        stab_color = "#8b949e"
        stab_value = "—"
        stab_label = "Insufficient data"
        stab_detail = f"Only {months_active} month imported — stability is a multi-month measure."
    else:
        if consistency_pct >= 80:
            stab_color, stab_label = ALERT_COLOR_OK, "Stable"
        elif consistency_pct >= 50:
            stab_color, stab_label = ALERT_COLOR_WARNING, "Moderate variation"
        else:
            stab_color, stab_label = ALERT_COLOR_OVER, "High variation"
        stab_value = f"{consistency_pct:.0f}%"
        stab_detail = "Based on month-to-month variance over the selected period."

    st.markdown(
        f"<div class='ledger-card'>"
        f"<div style='font-size:0.68rem;font-weight:700;text-transform:uppercase;"
        f"letter-spacing:0.07em;color:#8b949e;margin-bottom:6px'>Income Stability</div>"
        f"<div style='font-size:1.8rem;font-weight:700;color:{stab_color}'>{stab_value}</div>"
        f"<div style='font-size:0.82rem;color:#8b949e;margin-top:4px'>{stab_label} — {stab_detail}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

with h2:
    # Pass 30: softened "concentration risk" copy. User feedback:
    # "Do not treat employer concentration as a scary 'risk' in a
    # normal personal finance app. Most people have one employer."
    # Renamed to "Income source concentration" with neutral copy.
    if sources:
        top_src = sources[0]
        top_pct = top_src["pct"]
        if top_pct > 90:
            conc_color = "#8b949e"  # neutral grey, not red
            conc_label = "Single source"
            conc_detail = (f"{top_pct:.0f}% comes from {top_src['source']}. "
                           "That's normal for many people.")
        elif top_pct > 70:
            conc_color = "#8b949e"
            conc_label = "Mostly one source"
            conc_detail = (f"{top_pct:.0f}% from {top_src['source']} "
                           "with smaller other sources.")
        else:
            conc_color = ALERT_COLOR_OK
            conc_label = "Multiple sources"
            conc_detail = (f"Largest source is {top_src['source']} at "
                           f"{top_pct:.0f}%.")

        st.markdown(
            f"<div class='ledger-card'>"
            f"<div style='font-size:0.68rem;font-weight:700;text-transform:uppercase;"
            f"letter-spacing:0.07em;color:#8b949e;margin-bottom:6px'>"
            f"Income source concentration</div>"
            f"<div style='font-size:1.8rem;font-weight:700;color:{conc_color}'>{top_pct:.0f}%</div>"
            f"<div style='font-size:0.82rem;color:#8b949e;margin-top:4px'>"
            f"<b style='color:{conc_color}'>{conc_label}</b><br>{conc_detail}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ── Stacked area: income sources over time (multi-month only) ────────────
if monthly_by_src and len(monthly_trend) >= 2:
    st.divider()
    st.markdown('<p class="ledger-section-header">Sources Over Time</p>', unsafe_allow_html=True)

    all_months  = sorted({d["month"] for rows in monthly_by_src.values() for d in rows})
    INCOME_SOURCE_COLORS = [
        "#34d058", "#60a5fa", "#a78bfa", "#fbbf24", "#22d3ee", "#f472b6", "#86efac",
    ]

    fig_area = go.Figure()
    for idx, (src_name, rows) in enumerate(monthly_by_src.items()):
        row_map = {d["month"]: d["total"] for d in rows}
        vals    = [row_map.get(m, 0) for m in all_months]
        color   = INCOME_SOURCE_COLORS[idx % len(INCOME_SOURCE_COLORS)]
        hx = color.lstrip("#")
        r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

        fig_area.add_trace(go.Scatter(
            x=all_months, y=vals,
            name=src_name,
            mode="lines",
            stackgroup="one",
            line=dict(color=color, width=1),
            fillcolor=f"rgba({r},{g},{b},0.5)",
            hovertemplate=f"<b>{src_name}</b> at %{{x}}<br>$%{{y:,.2f}}<extra></extra>",
        ))

    # Pass 30: render as grouped bars instead of a stacked area.
    # User feedback: "Sources Over Time stacked chart is also bad.
    # Replace with a clearer monthly/source comparison." Keeping the
    # data shape (monthly_by_src) but using `barmode='group'` makes
    # each month read as a row of side-by-side source bars.
    fig_area.update_layout(
        **_base_layout(
            title=dict(text="Monthly income by source",
                       font=dict(size=14, color=_FONT_COLOR),
                       x=0, xanchor="left"),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1,
                font=dict(size=11), bgcolor="rgba(0,0,0,0)",
            ),
        )
    )
    fig_area.update_layout(barmode="group", bargap=0.18,
                            bargroupgap=0.05)
    # Strip the stack-fill from the existing Scatter traces and
    # re-add as Bar traces so we don't duplicate the chart code.
    fig_area.data = ()
    for idx, (src_name, rows) in enumerate(monthly_by_src.items()):
        row_map = {d["month"]: d["total"] for d in rows}
        vals    = [row_map.get(m, 0) for m in all_months]
        color   = INCOME_SOURCE_COLORS[idx % len(INCOME_SOURCE_COLORS)]
        fig_area.add_bar(
            name=src_name, x=all_months, y=vals,
            marker_color=color,
            hovertemplate=(
                f"<b>{src_name}</b><br>%{{x}}: $%{{y:,.0f}}"
                "<extra></extra>"
            ),
        )
    fig_area.update_xaxes(**_axis_style(show_grid=False, tickangle=-30))
    fig_area.update_yaxes(**_axis_style(tickprefix="$", tickformat=",.0f"))
    st.plotly_chart(fig_area, use_container_width=True,
                    key="income_monthly_grouped_bars")
    st.caption(
        "Side-by-side monthly comparison. Toggle a source in the "
        "legend to focus on one at a time."
    )

    # ── Source breakdown table (Pass 31) ──────────────────────────────
    # User feedback: "Source breakdown table: Source, total, % of
    # income, months seen, last seen. This is more useful than a
    # giant green blob." Builds from monthly_by_src so we don't make
    # an extra DB call.
    st.markdown(
        '<p class="ledger-section-header">Source breakdown</p>',
        unsafe_allow_html=True,
    )
    _src_rows = []
    _grand_total = sum(
        sum(d.get("total", 0) for d in rows)
        for rows in monthly_by_src.values()
    ) or 1.0
    for src_name, rows in monthly_by_src.items():
        total = sum(d.get("total", 0) for d in rows)
        months_seen = len([d for d in rows if d.get("total", 0) > 0])
        last_seen = max((d["month"] for d in rows
                          if d.get("total", 0) > 0), default="—")
        _src_rows.append({
            "Source":      src_name,
            "Total":       f"${total:,.0f}",
            "% of income": f"{(total/_grand_total*100):.0f}%",
            "Months seen": months_seen,
            "Last seen":   last_seen,
            "_sort":       total,
        })
    _src_rows.sort(key=lambda r: -r["_sort"])
    for r in _src_rows:
        r.pop("_sort", None)
    if _src_rows:
        import pandas as _pd_in
        st.dataframe(_pd_in.DataFrame(_src_rows),
                      use_container_width=True, hide_index=True)

# ── Raw income transactions ──────────────────────────────────────────────
with st.expander("Income Transactions", expanded=False):
    _raw_acct = f" AND account_type = '{acct_filter}'" if acct_filter else ""
    rows = conn.execute(f"""
        SELECT transaction_date, merchant, category, subcategory, amount, raw_description, account_type
        FROM transactions
        WHERE direction = 'credit'
          AND amount > 0
          AND direction NOT IN ('payment','cancelled','transfer')
          AND category NOT IN ('Credit Card Payment','Cancelled')
          AND transaction_date BETWEEN ? AND ?
          {_raw_acct}
        ORDER BY transaction_date DESC
    """, (start, end)).fetchall()

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df["amount"] = df["amount"].apply(lambda x: f"${abs(x):,.2f}")
        df.columns = ["Date", "Merchant", "Category", "Source", "Amount", "Description", "Account"]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(rows)} income transactions in this period")
    else:
        st.info("No income transactions found.")

conn.close()

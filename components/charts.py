"""
Reusable Plotly chart builders for Finance Dashboard pages.
All return plotly.graph_objects.Figure objects ready for st.plotly_chart().

Design principles:
  - Transparent backgrounds (dark theme aware)
  - All colors/tokens imported from config.theme — single source of truth
  - Donut: collapses tiny slices into "Other"; legend uses coloured circles
  - All charts share the same font / axis grid treatment
  - Net line on cashflow uses area fill for depth
  - automargin=True on axes prevents label clipping at smaller widths
"""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config.theme import (
    CATEGORY_COLORS, INVESTMENT_COLORS,
    ACCENT, ACCENT2, SPEND_COLOR, INCOME_COLOR, NET_COLOR,
    BG_CHART, TEXT_BASE, TEXT_MUTED, FONT_FAMILY, FONT_SIZE, GRID_COLOR,
    cat_color, hex_to_rgba,
)

# ── Re-export for pages that import directly ──────────────────────────────────
# (5_Spending.py imports CATEGORY_COLORS from here — keep that working)

_FONT_FAMILY = FONT_FAMILY
_FONT_SIZE   = FONT_SIZE
_FONT_COLOR  = TEXT_BASE
_GRID_COLOR  = GRID_COLOR
_BG          = BG_CHART

# ── Compat alias (pages import _cat_color via charts) ─────────────────────────
def _cat_color(cat: str) -> str:
    return cat_color(cat)


def _base_layout(**kwargs) -> dict:
    """Return a base layout dict that all charts extend."""
    base = dict(
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(family=_FONT_FAMILY, size=_FONT_SIZE, color=_FONT_COLOR),
        margin=dict(l=40, r=24, t=52, b=40),
        hoverlabel=dict(
            bgcolor="#1e2433",
            font_size=12,
            font_family=_FONT_FAMILY,
            bordercolor="rgba(255,255,255,0.15)",
        ),
    )
    base.update(kwargs)
    return base


def _axis_style(show_grid=True, **kwargs) -> dict:
    return dict(
        showgrid=show_grid,
        gridcolor=_GRID_COLOR,
        gridwidth=1,
        zeroline=False,
        automargin=True,
        tickfont=dict(size=11, color=TEXT_MUTED),
        **kwargs,
    )


# ── Cash flow bar chart ───────────────────────────────────────────────────────

def cashflow_bar(monthly_data: list[dict]) -> go.Figure:
    """
    Grouped bar: Income vs Spending per month + net line with area fill.
    monthly_data: [{month, income, spending, net, savings_rate?, tx_count?}]
    """
    months  = [d["month"] for d in monthly_data]
    incomes = [d.get("income",   0) for d in monthly_data]
    spends  = [d.get("spending", 0) for d in monthly_data]
    nets    = [d.get("net",      0) for d in monthly_data]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Income", x=months, y=incomes,
        marker=dict(color=INCOME_COLOR, opacity=0.9, line=dict(width=0)),
        hovertemplate="<b>%{x}</b><br>Income: $%{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Spending", x=months, y=spends,
        marker=dict(color=SPEND_COLOR, opacity=0.9, line=dict(width=0)),
        hovertemplate="<b>%{x}</b><br>Spending: $%{y:,.2f}<extra></extra>",
    ))

    # Net as filled area line for depth
    fig.add_trace(go.Scatter(
        name="Net", x=months, y=nets,
        mode="lines+markers",
        line=dict(color=NET_COLOR, width=2.5, dash="solid"),
        marker=dict(size=7, color=NET_COLOR, line=dict(width=1.5, color="#0f172a")),
        fill="tozeroy",
        fillcolor=hex_to_rgba(NET_COLOR, 0.10),
        hovertemplate="<b>%{x}</b><br>Net: $%{y:,.2f}<extra></extra>",
    ))

    fig.update_layout(
        **_base_layout(
            title=dict(text="Monthly Cash Flow", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
            barmode="group",
            bargap=0.25,
            bargroupgap=0.05,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1,
                font=dict(size=11),
                bgcolor="rgba(0,0,0,0)",
            ),
        )
    )
    fig.update_xaxes(**_axis_style(show_grid=False, title_text=None, tickangle=-30))
    fig.update_yaxes(**_axis_style(title_text="CAD ($)", tickprefix="$", tickformat=",.0f"))
    return fig


# ── Spending bar chart (Pass 17) ───────────────────────────────────────
# Manual-test feedback: the donut alone is hard to read for category
# comparison. A horizontal bar chart sorted by total puts the largest
# categories at the top and makes the comparison legible at a glance.
def spending_bar(category_totals: list[dict],
                 *, top_n: int = 12,
                 title: str = "Spending by Category") -> go.Figure:
    """Horizontal bar chart of spending by category.

    Args:
        category_totals: [{category, total, tx_count?, pct?}]
        top_n: keep the largest N categories; the rest fold into "Other".

    Sorted ascending in the figure (so largest is at the top of the page).
    Colours come from the same per-category palette as the donut so the
    user's mental model stays consistent across charts.
    """
    if not category_totals:
        # Render an empty placeholder figure rather than raising.
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text=title, font=dict(size=14, color=_FONT_COLOR),
                       x=0, xanchor="left"),
            height=240,
        ))
        return fig

    sorted_cats = sorted(category_totals, key=lambda c: c["total"], reverse=True)
    main = sorted_cats[:top_n]
    rest = sorted_cats[top_n:]
    if rest:
        other_total = sum(c["total"] for c in rest)
        main.append({"category": f"Other ({len(rest)})", "total": other_total})

    # Plotly bar charts render bottom-up; reverse so the biggest category
    # appears at the top.
    main_rev = list(reversed(main))
    cats   = [c["category"] for c in main_rev]
    vals   = [c["total"]    for c in main_rev]
    colors = [cat_color(c["category"]) for c in main_rev]

    fig = go.Figure(go.Bar(
        x=vals,
        y=cats,
        orientation="h",
        marker=dict(color=colors, line=dict(width=0)),
        text=[f"${v:,.0f}" for v in vals],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>$%{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_base_layout(
        title=dict(text=title, font=dict(size=14, color=_FONT_COLOR),
                   x=0, xanchor="left"),
        height=max(240, 32 * len(cats) + 80),
        showlegend=False,
        margin=dict(l=140, r=60, t=52, b=40),
    ))
    fig.update_xaxes(**_axis_style(show_grid=True, tickprefix="$",
                                   tickformat=",.0f", title_text=None))
    fig.update_yaxes(**_axis_style(show_grid=False, title_text=None))
    return fig


# ── Spending donut ────────────────────────────────────────────────────────────

def spending_donut(category_totals: list[dict], other_threshold: float = 0.025) -> go.Figure:
    """
    Donut chart of spending by category.
    - Collapses slices below `other_threshold` (2.5%) into "Other"
    - Labels only top 6 slices directly; rest in legend
    - Legend uses coloured CIRCLE markers (not Plotly's default grey squares)
    - category_totals: [{category, total, tx_count, pct}]
    """
    # Sort descending
    sorted_cats = sorted(category_totals, key=lambda c: c["total"], reverse=True)
    grand_total = sum(c["total"] for c in sorted_cats) or 1

    # Collapse tiny slices
    main, other_sum, other_count = [], 0.0, 0
    for c in sorted_cats:
        if c["total"] / grand_total < other_threshold and len(main) >= 5:
            other_sum   += c["total"]
            other_count += 1
        else:
            main.append(c)

    if other_sum > 0:
        main.append({
            "category": f"Other ({other_count})",
            "total": other_sum,
            "pct": other_sum / grand_total * 100,
        })

    labels = [c["category"] for c in main]
    values = [c["total"]    for c in main]
    colors = [cat_color(c["category"]) for c in main]

    # Big slices get name + amount + percent so the donut is useful
    # without hover. Smaller slices rely on the legend below.
    text_labels = []
    for i, val in enumerate(values):
        pct = (val / grand_total * 100) if grand_total else 0
        if i < 4 and pct >= 7.0:
            text_labels.append(f"{labels[i]}<br>${val:,.0f} · {pct:.0f}%")
        elif i < 7 and pct >= 4.0:
            text_labels.append(f"{labels[i]}<br>{pct:.0f}%")
        else:
            text_labels.append("")

    fig = go.Figure()

    # ── Main Pie trace — showlegend=False so we control legend via scatter ──
    fig.add_trace(go.Pie(
        labels=labels,
        values=values,
        text=text_labels,
        hole=0.50,
        marker=dict(
            colors=colors,
            line=dict(color="#0d1117", width=2),
        ),
        textinfo="text",
        textposition="inside",
        insidetextorientation="horizontal",
        textfont=dict(size=13, color=_FONT_COLOR),
        hovertemplate="<b>%{label}</b><br>$%{value:,.2f}  (%{percent})<extra></extra>",
        pull=[0.0] * len(labels),
        sort=False,
        direction="clockwise",
        showlegend=False,   # we'll drive legend with scatter traces below
    ))

    # ── Invisible scatter traces — one per slice — give us circle markers ──
    # These render in the legend only (mode="markers", x/y off-screen).
    for lbl, color in zip(labels, colors):
        fig.add_trace(go.Scatter(
            x=[None], y=[None],
            mode="markers",
            name=lbl,
            marker=dict(
                symbol="circle",
                size=10,
                color=color,
                line=dict(width=0),
            ),
            showlegend=True,
            hoverinfo="skip",
        ))

    # Centre annotation showing total
    fig.add_annotation(
        text=f"<b>${grand_total:,.0f}</b><br><span style='font-size:10px'>total</span>",
        x=0.5, y=0.5,
        font=dict(size=15, color=_FONT_COLOR, family=_FONT_FAMILY),
        showarrow=False,
        align="center",
    )

    # Pass 35d: bottom legend kept; chart height bumped so big slices
    # have room for their category-name labels. Pass 35b's clipping
    # fix (no outside labels) still holds.
    fig.update_layout(
        **_base_layout(
            title=dict(
                text="Spending by Category",
                font=dict(size=14, color=_FONT_COLOR),
                x=0, xanchor="left",
            ),
            showlegend=True,
            legend=dict(
                orientation="h",
                x=0.5, y=-0.05,
                xanchor="center",
                yanchor="top",
                font=dict(size=11),
                bgcolor="rgba(0,0,0,0)",
                traceorder="normal",
                itemsizing="constant",
            ),
            height=480,
            margin=dict(l=16, r=16, t=52, b=108),
        )
    )
    return fig


# ── Top merchants horizontal bar ──────────────────────────────────────────────

def top_merchants_bar(merchant_data: list[dict], top_n: int = 10) -> go.Figure:
    data   = merchant_data[:top_n]
    names  = [d["merchant"]          for d in reversed(data)]
    totals = [d["total"]             for d in reversed(data)]
    cats   = [d.get("category", "")  for d in reversed(data)]
    colors = [cat_color(c)           for c in cats]

    fig = go.Figure(go.Bar(
        x=totals, y=names,
        orientation="h",
        marker=dict(
            color=colors,
            opacity=0.90,
            line=dict(width=0),
        ),
        text=[f"  ${v:,.0f}" for v in totals],
        textposition="outside",
        textfont=dict(size=11, color=_FONT_COLOR),
        hovertemplate="<b>%{y}</b><br>$%{x:,.2f}<extra></extra>",
        cliponaxis=False,
    ))

    max_val = max(totals, default=1)
    fig.update_layout(
        **_base_layout(
            title=dict(text=f"Top {len(data)} Merchants", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
            xaxis=dict(range=[0, max_val * 1.28], **_axis_style(tickprefix="$", tickformat=",.0f")),
            yaxis=dict(**_axis_style(show_grid=False)),
            margin=dict(l=8, r=90, t=52, b=40),
        )
    )
    return fig


# ── Category trend line ───────────────────────────────────────────────────────

def category_trend(trend_data: list[dict], category: str) -> go.Figure:
    """trend_data: [{month, total}]"""
    months = [d["month"] for d in trend_data]
    totals = [d["total"] for d in trend_data]
    color  = cat_color(category)
    fill_c = hex_to_rgba(color, 0.12)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=months, y=totals,
        mode="lines+markers",
        name=category,
        line=dict(color=color, width=2.5),
        marker=dict(size=8, color=color, line=dict(width=1.5, color="#0f172a")),
        fill="tozeroy",
        fillcolor=fill_c,
        hovertemplate="<b>%{x}</b><br>$%{y:,.2f}<extra></extra>",
    ))

    fig.update_layout(
        **_base_layout(
            title=dict(text=f"{category} — Monthly Trend", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
        )
    )
    fig.update_xaxes(**_axis_style(show_grid=False, tickangle=-30))
    fig.update_yaxes(**_axis_style(tickprefix="$", tickformat=",.0f"))
    return fig


# ── Score gauge ───────────────────────────────────────────────────────────────

def score_gauge(score: int, label: str, title: str = "Money Pulse") -> go.Figure:
    if score >= 80:
        color = "#34d058"
    elif score >= 65:
        color = "#86efac"
    elif score >= 50:
        color = "#fbbf24"
    elif score >= 35:
        color = "#f97316"
    else:
        color = "#ef4444"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        title=dict(
            text=f"{title}<br><span style='font-size:13px;color:{color}'>{label}</span>",
            font=dict(size=14, color=_FONT_COLOR, family=_FONT_FAMILY),
        ),
        gauge=dict(
            axis=dict(
                range=[0, 100],
                tickwidth=1,
                tickcolor=TEXT_MUTED,
                tickfont=dict(size=10, color=TEXT_MUTED),
            ),
            bar=dict(color=color, thickness=0.65),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            steps=[
                dict(range=[0, 35],   color="rgba(239,68,68,0.10)"),
                dict(range=[35, 50],  color="rgba(249,115,22,0.10)"),
                dict(range=[50, 65],  color="rgba(251,191,36,0.10)"),
                dict(range=[65, 80],  color="rgba(134,239,172,0.10)"),
                dict(range=[80, 100], color="rgba(52,208,88,0.10)"),
            ],
            threshold=dict(
                line=dict(color=color, width=3),
                thickness=0.85,
                value=score,
            ),
        ),
        number=dict(
            suffix="/100",
            font=dict(size=40, color=_FONT_COLOR, family=_FONT_FAMILY),
        ),
    ))
    fig.update_layout(
        **_base_layout(margin=dict(l=24, r=24, t=60, b=16))
    )
    return fig


# ── Investment allocation pie ─────────────────────────────────────────────────

def investment_allocation(inv_data: list[dict]) -> go.Figure:
    from collections import defaultdict

    by_account = defaultdict(float)
    for row in inv_data:
        key = f"{row['account_name']} ({row.get('account_type', '')})"
        by_account[key] += row.get("market_value", 0)

    labels = list(by_account.keys())
    values = list(by_account.values())
    colors = [INVESTMENT_COLORS[i % len(INVESTMENT_COLORS)] for i in range(len(labels))]

    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.5,
        marker=dict(colors=colors, line=dict(color="#0d1117", width=2)),
        textinfo="percent+label",
        textfont=dict(size=11, color=_FONT_COLOR),
        hovertemplate="<b>%{label}</b><br>$%{value:,.2f}  (%{percent})<extra></extra>",
    ))
    total = sum(values)
    fig.add_annotation(
        text=f"<b>${total:,.0f}</b><br><span style='font-size:10px'>total</span>",
        x=0.5, y=0.5,
        font=dict(size=14, color=_FONT_COLOR, family=_FONT_FAMILY),
        showarrow=False, align="center",
    )
    fig.update_layout(
        **_base_layout(
            title=dict(text="Portfolio Allocation", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
            legend=dict(font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=0, r=120, t=52, b=20),
        )
    )
    return fig


# ── Recurring subscriptions table ─────────────────────────────────────────────

def recurring_table(recurring_data: list[dict]) -> go.Figure:
    merchants = [d["merchant"]              for d in recurring_data]
    avgs      = [round(d["avg_amount"], 2)  for d in recurring_data]
    months    = [d["months_seen"]           for d in recurring_data]
    cats      = [d.get("category", "")      for d in recurring_data]
    annual    = [round(d["avg_amount"] * 12, 2) for d in recurring_data]

    row_fill = [["#1a2133" if i % 2 == 0 else "#151d2e" for i in range(len(merchants))]]

    fig = go.Figure(go.Table(
        columnwidth=[160, 120, 100, 90, 110],
        header=dict(
            values=["<b>Merchant</b>", "<b>Category</b>", "<b>Avg/Month</b>",
                    "<b>Months</b>", "<b>Est. Annual</b>"],
            fill_color="#1e2d3d",
            font=dict(color=_FONT_COLOR, size=12, family=_FONT_FAMILY),
            align="left",
            height=32,
            line=dict(color="rgba(255,255,255,0.06)", width=1),
        ),
        cells=dict(
            values=[
                merchants, cats,
                [f"${v:,.2f}" for v in avgs],
                months,
                [f"${v:,.2f}" for v in annual],
            ],
            fill_color=row_fill,
            font=dict(color=_FONT_COLOR, size=12, family=_FONT_FAMILY),
            align="left",
            height=28,
            line=dict(color="rgba(255,255,255,0.04)", width=1),
        ),
    ))
    fig.update_layout(
        **_base_layout(
            title=dict(text="Recurring Charges", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
            margin=dict(l=0, r=0, t=52, b=0),
        )
    )
    return fig


# ── Category stacked area (for multi-category trends) ────────────────────────

def category_stacked_area(cat_monthly: dict[str, list[dict]], top_n: int = 6) -> go.Figure:
    """
    cat_monthly: {category: [{month, total}]}
    Renders top N categories as a stacked area chart.
    """
    totals   = {cat: sum(d["total"] for d in rows) for cat, rows in cat_monthly.items()}
    top_cats = sorted(totals, key=totals.get, reverse=True)[:top_n]

    all_months = sorted({d["month"] for rows in cat_monthly.values() for d in rows})
    if not all_months:
        return go.Figure()

    fig = go.Figure()
    for cat in reversed(top_cats):   # reversed so highest is on top visually
        rows   = {d["month"]: d["total"] for d in cat_monthly.get(cat, [])}
        vals   = [rows.get(m, 0) for m in all_months]
        color  = cat_color(cat)
        fill_c = hex_to_rgba(color, 0.5)

        fig.add_trace(go.Scatter(
            x=all_months, y=vals,
            name=cat,
            mode="lines",
            stackgroup="one",
            line=dict(color=color, width=1),
            fillcolor=fill_c,
            hovertemplate=f"<b>{cat}</b> — %{{x}}<br>${{y:,.2f}}<extra></extra>",
        ))

    fig.update_layout(
        **_base_layout(
            title=dict(text=f"Top {top_n} Categories Over Time", font=dict(size=14, color=_FONT_COLOR), x=0, xanchor="left"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
        )
    )
    fig.update_xaxes(**_axis_style(show_grid=False, tickangle=-30))
    fig.update_yaxes(**_axis_style(tickprefix="$", tickformat=",.0f"))
    return fig

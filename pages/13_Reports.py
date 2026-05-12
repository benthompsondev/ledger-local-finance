"""
Reports — analytics hub.

This is intentionally a hub, not another giant dashboard. It links to deeper
inspection surfaces while the Dashboard stays focused on daily decisions.
"""
from __future__ import annotations

import streamlit as st

from utils.database import init_db, get_connection
from utils.insights import monthly_review
from utils.styles import inject_styles

st.set_page_config(page_title="Reports · Ledger",
                   page_icon="📊", layout="wide")
inject_styles()
init_db()

st.title("Reports")
st.caption(
    "Use Reports when you want to explore details. "
    "Dashboard is the daily view."
)

# Light coverage hint up top so the user knows whether the data is
# rich enough for the deep reports to be useful.
conn = get_connection()
try:
    n_tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    flagged = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
    ).fetchone()[0]
    # Pass 33: deterministic monthly review packet — same data feeds
    # Reports (here), Trends (lead block), and OpenClaw context.
    _mr = monthly_review(conn=conn)
finally:
    conn.close()

if n_tx == 0:
    st.info(
        "No transactions yet — Reports populate after your first "
        "import. Open **Import** to get started.",
        icon="📥",
    )

# ── Pass 33: Monthly Review summary card ─────────────────────────────
# A compact, deterministic answer to "am I better or worse than last
# month, what changed, what should I look at?" Renders only when there
# are at least 2 imported months. Otherwise the existing Reports cards
# below are still useful for one-month users.
if _mr.get("available"):
    _spend_d  = float(_mr["spending_delta"])
    _income_d = float(_mr["income_delta"])
    _net_d    = float(_mr["net_delta"])

    _spend_color  = "#f85149" if _spend_d > 0 else "#3fb950"
    _income_color = "#3fb950" if _income_d > 0 else "#f85149"
    _net_color    = "#3fb950" if _net_d   >= 0 else "#f85149"
    _net_word     = "better" if _net_d   >= 0 else "worse"
    _spend_word   = "more"   if _spend_d  > 0 else "less"
    _income_word  = "more"   if _income_d > 0 else "less"

    st.divider()
    # Pass 35b: explicit partial-month note when ignored months exist.
    _ignored = _mr.get("ignored_partial_months") or []
    _ignored_suffix = (
        f" <span style='color:#8b949e;font-weight:500'>"
        f"&nbsp;·&nbsp;partial {', '.join(_ignored)} ignored</span>"
        if _ignored else ""
    )
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);"
        f"border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid #4f86c6;border-radius:8px;"
        f"padding:14px 16px;margin-bottom:8px'>"
        f"<div style='font-size:0.78rem;color:#8b949e;"
        f"text-transform:uppercase;letter-spacing:0.06em;"
        f"margin-bottom:4px'>Monthly review</div>"
        f"<div style='font-size:1.05rem;font-weight:700;"
        f"color:#e6edf3;margin-bottom:6px'>{_mr['month']} vs "
        f"{_mr['prev_month']}{_ignored_suffix}</div>"
        f"<div style='font-size:0.92rem;color:#c9d1d9;line-height:1.55'>"
        f"Net is "
        f"<span style='color:{_net_color};font-weight:700'>"
        f"${abs(_net_d):,.0f} {_net_word}</span>"
        f". Spending "
        f"<span style='color:{_spend_color};font-weight:600'>"
        f"${abs(_spend_d):,.0f} {_spend_word}</span>; "
        f"income "
        f"<span style='color:{_income_color};font-weight:600'>"
        f"${abs(_income_d):,.0f} {_income_word}</span>."
        f"</div></div>",
        unsafe_allow_html=True,
    )

    _mr_cols = st.columns(4)
    _mr_cols[0].metric(
        "Net this month", f"${_mr['net']:,.0f}",
        delta=f"${_net_d:+,.0f} vs {_mr['prev_month']}",
    )
    _mr_cols[1].metric(
        "Spending", f"${_mr['spending']:,.0f}",
        delta=f"${_spend_d:+,.0f}",
        delta_color="inverse",
    )
    _mr_cols[2].metric(
        "Income", f"${_mr['income']:,.0f}",
        delta=f"${_income_d:+,.0f}",
    )
    _mr_cols[3].metric(
        "Savings rate", f"{_mr['savings_rate']:.0f}%",
        delta=f"{_mr['savings_rate'] - _mr['prev_savings_rate']:+.0f} pp",
    )

    _bm = _mr.get("biggest_mover")
    _ti = _mr.get("top_increases") or []
    _td = _mr.get("top_decreases") or []
    _bm_bits: list[str] = []
    if _ti:
        _bm_bits.append(
            f"📈 **{_ti[0]['category']}** up "
            f"${abs(_ti[0]['abs_change']):,.0f} "
            f"({_ti[0]['pct_change']:+.0f}%)"
        )
    if _td:
        _bm_bits.append(
            f"📉 **{_td[0]['category']}** down "
            f"${abs(_td[0]['abs_change']):,.0f} "
            f"({_td[0]['pct_change']:+.0f}%)"
        )
    if _bm_bits:
        st.caption(" · ".join(_bm_bits).replace("$", r"\$"))

    for _cv in _mr.get("data_caveats") or []:
        st.caption(f"⚠ {_cv}")

    _sa = _mr.get("suggested_action")
    if _sa:
        _ac1, _ac2 = st.columns([5, 1])
        with _ac1:
            st.caption(f"➡ **Suggested next:** {_sa.get('reason','')}")
        with _ac2:
            if _sa.get("target_page"):
                if st.button(_sa.get("label", "Open"),
                             key="reports_mr_action",
                             use_container_width=True):
                    st.switch_page(_sa["target_page"])

    # Quick-jump row to the deeper reports for the same headline.
    _qj1, _qj2 = st.columns([1, 1])
    with _qj1:
        if st.button("📉 Open Trends — full What changed?",
                     key="reports_mr_open_trends",
                     use_container_width=True):
            st.switch_page("pages/4_Trends.py")
    with _qj2:
        if st.button("💸 Open Spending — category breakdown",
                     key="reports_mr_open_spending",
                     use_container_width=True):
            st.switch_page("pages/5_Spending.py")
elif n_tx > 0 and not _mr.get("available"):
    # 1 month of data only — explain how to unlock the review card.
    st.divider()
    st.info(
        f"**Monthly Review unlocks at 2 months.** {_mr.get('reason') or ''} "
        "Once you do, this page leads with how the latest month "
        "compares to the previous one.",
        icon="🗓",
    )

# ── Cards ─────────────────────────────────────────────────────────
# Each card: title, blurb, target page path, button label. We render
# in a 2-column grid so every card is the same width regardless of
# blurb length.
_CARDS: list[dict] = [
    {
        "title":  "💸 Spending",
        "blurb":  ("Where did money go? Category breakdowns, top "
                   "merchants, MoM deltas, and consumption-only "
                   "totals. Skip transfers and CC payments by "
                   "default."),
        "target": "pages/5_Spending.py",
        "button": "Open Spending",
    },
    {
        "title":  "💵 Income",
        "blurb":  ("Where did money come from? Payroll, e-Transfers "
                   "in, interest, refunds. Income vs spending side-"
                   "by-side, source breakdown."),
        "target": "pages/6_Income.py",
        "button": "Open Income",
    },
    {
        "title":  "📉 Trends",
        "blurb":  ("What changed? Cashflow over time, Top movers "
                   "matrix, recurring merchants, year-over-year. "
                   "Lead block answers the 'what changed?' question "
                   "without scrolling."),
        "target": "pages/4_Trends.py",
        "button": "Open Trends",
    },
    {
        "title":  "💡 Money Moves",
        "blurb":  ("What opportunities exist? Ranked, actionable "
                   "recommendations grouped Do Today / Review This "
                   "Week / Watch. Each card has an action button "
                   "and an evidence trail."),
        "target": "pages/10_Recommendations.py",
        "button": "Open Money Moves",
    },
    {
        "title":  "🏠 Detailed Overview",
        "blurb":  ("The full analytics view of your imported data — "
                   "Ledger Copilot summary, breakdown charts, "
                   "category and merchant tables, and the Mission / "
                   "Weekly Review surfaces. Use this when you want "
                   "everything on one long page."),
        "target": "pages/1_Dashboard.py",
        "button": "Open detailed overview",
    },
    {
        "title":  ("🔍 Review queue"
                   + (f" · {flagged} flagged" if flagged else "")),
        "blurb":  ("Flagged transactions awaiting categorization or "
                   "decision. Cleared rows improve every other "
                   "report's accuracy."),
        "target": "pages/8_Review.py",
        "button": "Open Review",
    },
]

st.divider()

_left, _right = st.columns(2, gap="medium")
for i, card in enumerate(_CARDS):
    col = _left if i % 2 == 0 else _right
    with col:
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.02);"
            f"border:1px solid rgba(255,255,255,0.08);"
            f"border-left:3px solid #4f86c6;border-radius:8px;"
            f"padding:14px 16px;margin-bottom:10px;height:100%'>"
            f"<div style='font-size:1.0rem;font-weight:700;"
            f"color:#e6edf3;margin-bottom:6px'>{card['title']}</div>"
            f"<div style='font-size:0.86rem;color:#c9d1d9;"
            f"line-height:1.5;margin-bottom:10px'>{card['blurb']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button(card["button"],
                     key=f"reports_card_{i}",
                     use_container_width=True):
            st.switch_page(card["target"])

"""
Recommendations — ranked, actionable, grounded in your imported data.
Each recommendation has: priority, annual impact, context, and action path.
States: active | snoozed | done | ignored
"""
import streamlit as st
from datetime import date, timedelta

from utils.database import (
    init_db, get_connection, get_rec_states, set_rec_state, clear_rec_state,
)
from utils.insights import compute_recommendations
from utils.ai_explainer import explain_recommendation
from utils.ai_config import ai_is_ready
from utils.styles import inject_styles
from utils.navigation import set_transaction_search
from config.constants import PRIORITY_COLOR, PRIORITY_BG

st.set_page_config(page_title="Money Moves · Ledger", page_icon="💡", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    # Pass 25: page title rebranded to "Money Moves" (filename kept for
    # routing stability). Cards still come from compute_recommendations.
    st.title("Money Moves")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()

# Pass 25: dropped the 4-metric score strip (Health/Savings/Debt/Consistency)
# from the top of this page — it duplicates the Dashboard health gauge
# and pushes the actual recommendations below the fold. Health Score
# already lives on the Dashboard. The AI availability banner is also
# trimmed to one line; per-card "Explain this" still works.
_ai_ready_local, _ai_reason_local = ai_is_ready()
st.caption(
    "Money Moves — fewer, better actions, grounded in your imported data. "
    + ("🧠 AI explanations are on per-card." if _ai_ready_local else
       "🧠 Enable AI in Settings for per-card plain-English explanations.")
)

# ── Load recommendations + saved states ────────────────────────────────
recs = compute_recommendations(conn=conn)
states = get_rec_states(conn=conn)

# Filter out done/ignored based on user preference
show_filter = st.radio(
    "Show",
    ["Active only", "All (including snoozed)", "Everything"],
    horizontal=True,
    label_visibility="collapsed",
)

today_str = date.today().isoformat()

def is_visible(rec, state_map, show) -> bool:
    s = state_map.get(rec["key"], {}).get("state", "active")
    if show == "Active only":
        if s == "done" or s == "ignored":
            return False
        if s == "snoozed":
            snoozed_until = state_map.get(rec["key"], {}).get("snoozed_until") or ""
            if snoozed_until > today_str:
                return False
    elif show == "All (including snoozed)":
        if s in ("done", "ignored"):
            return False
    return True

visible = [r for r in recs if is_visible(r, states, show_filter)]

if not recs:
    st.info("No recommendations yet — import at least 2 months of statements to generate insights.")
    conn.close()
    st.stop()

if not visible:
    st.success("All recommendations are resolved. Change the filter above to see done/snoozed items.")
    conn.close()
    st.stop()

# Pass 25: removed the 4-metric priority count strip. The grouped
# section headers below already say e.g. "Do Today (3) · ~$420/yr" so
# the priority counts were just noise above them. We keep ONE compact
# annual-impact line because it's a real motivator.
annual_total = sum(r.get("annual_impact", 0) for r in visible if r.get("annual_impact", 0) > 0)
if annual_total > 0:
    st.caption(
        f"📊 {len(visible)} active · addressing them all could save up to "
        f"**${annual_total:,.0f}/year** based on your data."
    )

# ── Action-plan groups ──────────────────────────────────────────────────
# Each recommendation lands in exactly ONE bucket so the page reads as a
# clear "what to do, in what order" rather than a flat ranked list. Routing
# is deterministic from the rec's existing fields:
#   • Do Today      = type='fix' OR (priority='high' AND urgency >= 0.7)
#   • Review Week   = priority='high' OR priority='medium' AND urgency >= 0.4
#   • Save Money    = type in ('cut','optimize') AND annual_impact > 0
#   • Data Cleanup  = type='review'
#   • Watch         = type='watch' or anything still uncategorized
#
# The order above is the routing order — first match wins. This matches the
# user's mental model: urgent fires first, then near-term review, then
# money-saving moves, then bookkeeping, then passive watching.
def _route_rec(rec: dict) -> str:
    t = rec.get("type", "investigate")
    pri = rec.get("priority", "low")
    urg = float(rec.get("urgency", 0.4))
    impact = float(rec.get("annual_impact") or 0)
    if t == "fix" or (pri == "high" and urg >= 0.7):
        return "do_today"
    if pri == "high" or (pri == "medium" and urg >= 0.4):
        return "review_week"
    if t in ("cut", "optimize") and impact > 0:
        return "save_money"
    if t == "review":
        return "data_cleanup"
    if t == "watch":
        return "watch"
    # Fallback — investigate / unknown types end up under Watch so they never
    # disappear silently.
    return "watch"

GROUP_META = [
    ("do_today",     "🔴 Do Today",
        "Urgent fixes — time-sensitive or high-priority items that materially affect cashflow."),
    ("review_week",  "🟡 Review This Week",
        "Items worth a closer look in the next few days. Not on fire, but don't drift."),
    ("save_money",   "💰 Save Money",
        "Concrete cuts and optimisations with a projected dollar impact."),
    ("data_cleanup", "🧹 Data Cleanup",
        "Bookkeeping — clearing the review queue, fixing categorization. Improves score accuracy."),
    ("watch",        "👀 Watch",
        "Trends to keep an eye on. No action required yet."),
]

groups: dict[str, list[dict]] = {gid: [] for gid, _, _ in GROUP_META}
for rec in visible:
    groups[_route_rec(rec)].append(rec)

# Pass 25: dropped the 5-column group-counter strip. Each group's
# section header below already shows its count + dollar impact, e.g.
# "Do Today (3) · ~$420/yr", so the counter strip duplicated that info.

st.divider()

# ── Recommendation cards (grouped) ──────────────────────────────────────
PRIORITY_ICON   = {"high": "🔴", "medium": "🟡", "low": "🟢"}
STATE_ICON      = {"active": "", "done": "✅", "snoozed": "😴", "ignored": "🚫"}
TYPE_ICON = {
    "cut":         "✂️",
    "review":      "🔍",
    "watch":       "👀",
    "fix":         "🛠",
    "investigate": "🧭",
    "optimize":    "⚙️",
}


def _render_rec_card(rec: dict) -> None:
    key         = rec["key"]
    priority    = rec["priority"]
    state_info  = states.get(key, {})
    state       = state_info.get("state", "active")

    color  = PRIORITY_COLOR.get(priority, "#8b949e")
    bg     = PRIORITY_BG.get(priority, "rgba(255,255,255,0.03)")
    picon  = PRIORITY_ICON.get(priority, "⚪")
    sicon  = STATE_ICON.get(state, "")
    rtype  = rec.get("type", "investigate")
    ticon  = TYPE_ICON.get(rtype, "•")
    conf   = rec.get("confidence", 0.6)

    with st.container():
        st.markdown(
            f"<div style='background:{bg};border:1px solid rgba(255,255,255,0.07);"
            f"border-left:3px solid {color};border-radius:8px;"
            f"padding:4px 12px 4px 14px;margin-bottom:2px;"
            f"display:flex;align-items:center;gap:10px;flex-wrap:wrap'>"
            f"<span style='font-size:10px;font-weight:700;color:{color};"
            f"text-transform:uppercase;letter-spacing:0.07em'>"
            f"{picon} {priority} priority</span>"
            f"<span style='font-size:10px;color:#8b949e;"
            f"text-transform:uppercase;letter-spacing:0.07em;font-weight:600'>"
            f"{ticon} {rtype}</span>"
            f"<span style='font-size:10px;color:#8b949e'>"
            f"confidence {conf*100:.0f}%</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        with st.expander(f"{sicon} {rec['title']}", expanded=(priority == "high" and state == "active")):
            col_body, col_meta = st.columns([3, 1])

            with col_body:
                st.markdown(rec["body"])
                st.caption(f"**Evidence:** {rec.get('evidence','')}")

                # ── AI "Explain this" ─────────────────────────────────
                explain_cache_key = f"ai_explain_{key}"
                explain_col1, explain_col2 = st.columns([1, 3])
                with explain_col1:
                    if st.button("🧠 Explain this", key=f"explain_btn_{key}",
                                 help=("Uses MiniMax (or deterministic fallback) to summarize "
                                       "why this recommendation matters and what to do, "
                                       "grounded in its drivers.")):
                        with st.spinner("Explaining…"):
                            st.session_state[explain_cache_key] = explain_recommendation(rec)
                exp = st.session_state.get(explain_cache_key)
                if exp:
                    _nature = exp.get("nature") or "money"
                    _chip_bg = "#4f86c6" if _nature == "money" else "#8b949e"
                    _chip_lbl = "MONEY" if _nature == "money" else "CLEANUP"
                    _ok = exp.get("ok")
                    _src_label = "AI · grounded" if _ok else "Deterministic"
                    _src_color = "#4f86c6" if _ok else "#8b949e"
                    st.markdown(
                        f"<div style='background:rgba(255,255,255,0.03);border-left:2px solid {_chip_bg};"
                        f"padding:8px 12px;margin-top:6px;border-radius:4px'>"
                        f"<div style='display:flex;gap:6px;margin-bottom:4px'>"
                        f"<span style='background:{_chip_bg};color:#fff;padding:1px 7px;"
                        f"border-radius:3px;font-size:9px;font-weight:700;letter-spacing:0.05em'>{_chip_lbl}</span>"
                        f"<span style='background:{_src_color};color:#fff;padding:1px 7px;"
                        f"border-radius:3px;font-size:9px;font-weight:700'>{_src_label}</span>"
                        f"</div>"
                        f"<div style='font-size:0.85rem;color:#e6edf3;margin-bottom:4px'>"
                        f"<b>Why it matters:</b> {exp.get('why_it_matters','')}</div>"
                        f"<div style='font-size:0.85rem;color:#e6edf3'>"
                        f"<b>Action:</b> {exp.get('action','')}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    if exp.get("error"):
                        st.caption(f"⚠ {exp['error']}")

                # Action button
                action_type  = rec.get("action_type", "none")
                action_label = rec.get("action_label", "")
                action_value = rec.get("action_value", "")

                if action_type == "category_filter":
                    if st.button(f"→ {action_label}", key=f"act_{key}"):
                        set_transaction_search(category=action_value, all_time=True)
                        st.switch_page("pages/2_Transactions.py")
                elif action_type == "spending_page":
                    if st.button(f"→ {action_label}", key=f"act_{key}"):
                        st.switch_page("pages/5_Spending.py")
                elif action_type == "review_page":
                    # Pass 18: subscription-related recs route to Reduce
                    # (the canonical Reduce / cancel-candidate workspace)
                    # rather than the legacy Investments page.
                    if action_value == "review":
                        if st.button(f"→ {action_label}", key=f"act_{key}"):
                            st.switch_page("pages/8_Review.py")
                    elif action_value == "subscriptions":
                        if st.button(f"→ {action_label}", key=f"act_{key}"):
                            st.switch_page("pages/11_Reduce.py")
                elif action_type == "merchant_filter":
                    if st.button(f"→ {action_label}", key=f"act_{key}"):
                        set_transaction_search(merchant=action_value, all_time=True)
                        st.switch_page("pages/2_Transactions.py")

            with col_meta:
                if rec.get("annual_impact", 0) > 0:
                    st.metric("Est. annual impact", f"${rec['annual_impact']:,.0f}")
                st.caption(f"Category: **{rec.get('category','—')}**")
                st.caption(
                    f"Controllability: {int(rec.get('controllability', 0.5) * 100)}%  ·  "
                    f"Urgency: {int(rec.get('urgency', 0.4) * 100)}%"
                )
                if state != "active":
                    st.caption(f"Status: {sicon} {state}")

            # ── Action buttons (snooze / done / ignore / reset) ─────────
            st.divider()
            btn_cols = st.columns(4)

            if state != "done":
                if btn_cols[0].button("✅ Mark done", key=f"done_{key}"):
                    set_rec_state(key, "done", title=rec["title"],
                                  annual_impact=rec.get("annual_impact", 0), conn=conn)
                    conn.commit()
                    st.rerun()

            if state not in ("snoozed",):
                if btn_cols[1].button("😴 Snooze 30d", key=f"snooze_{key}"):
                    until = (date.today() + timedelta(days=30)).isoformat()
                    set_rec_state(key, "snoozed", title=rec["title"],
                                  annual_impact=rec.get("annual_impact", 0),
                                  snoozed_until=until, conn=conn)
                    conn.commit()
                    st.rerun()

            if state != "ignored":
                if btn_cols[2].button("🚫 Ignore", key=f"ignore_{key}"):
                    set_rec_state(key, "ignored", title=rec["title"],
                                  annual_impact=rec.get("annual_impact", 0), conn=conn)
                    conn.commit()
                    st.rerun()

            if state != "active":
                if btn_cols[3].button("↩ Reset", key=f"reset_{key}"):
                    clear_rec_state(key, conn=conn)
                    conn.commit()
                    st.rerun()

    st.markdown("---")


# ── Render each group ───────────────────────────────────────────────────
GROUP_HELP = dict((gid, h) for gid, _, h in GROUP_META)
GROUP_LABEL = dict((gid, lbl) for gid, lbl, _ in GROUP_META)

for gid, label, help_text in GROUP_META:
    bucket = groups[gid]
    if not bucket:
        continue
    annual = sum(r.get("annual_impact", 0) for r in bucket if r.get("annual_impact", 0) > 0)
    suffix = f" · ~${annual:,.0f}/yr" if annual > 0 else ""
    # Pass 25: Watch and Data Cleanup are collapsed by default. They're
    # informational, not urgent, and they pad the page when "Active only"
    # filter shows everything stacked.
    _collapse_default = gid in ("watch", "data_cleanup")
    if _collapse_default:
        with st.expander(f"{label} ({len(bucket)}){suffix}",
                         expanded=False):
            st.caption(help_text)
            for rec in bucket:
                _render_rec_card(rec)
    else:
        st.markdown(
            f'<p class="ledger-section-header">{label} ({len(bucket)}){suffix}</p>',
            unsafe_allow_html=True,
        )
        st.caption(help_text)
        for rec in bucket:
            _render_rec_card(rec)

conn.close()

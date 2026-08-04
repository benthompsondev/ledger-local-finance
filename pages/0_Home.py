"""
Home — the weekly check-in.

One page, one story, top to bottom:
  1. How fresh is the data?
  2. One verdict.
  3. One Safe to Spend number (canonical — shared with Dashboard/Plan).
  4. Monthly Progress (savings target vs projection).
  5. What changed (max 3 material findings).
  6. This week (1 primary action, at most 1 secondary).
  7. Finish check-in.

Everything rendered here comes from utils.checkin.weekly_checkin() —
deterministic, local, no AI. Deep analysis lives on the other pages;
this page is allowed to end.
"""
import html

import streamlit as st

from utils.database import init_db, get_connection, insert_checkin
from utils.checkin import weekly_checkin
from utils.navigation import set_transaction_search
from utils.styles import inject_styles

st.set_page_config(page_title="Home · Ledger", page_icon="✅",
                   layout="wide")
inject_styles()
init_db()

conn = get_connection()
packet = weekly_checkin(conn=conn)

freshness = packet["freshness"]
sts       = packet["safe_to_spend"]
progress  = packet["progress"]
goal      = packet.get("goal")
changes   = packet["changes"]
actions   = packet["actions"]
verdict   = packet["verdict"]
last      = packet["last_checkin"]


def _h(s) -> str:
    return html.escape(str(s or ""))


def _route(page: str, *, category: str = "", merchant: str = "") -> None:
    if category:
        set_transaction_search(category=category, all_time=True)
    elif merchant:
        set_transaction_search(merchant=merchant, all_time=True)
    st.switch_page(page)


# ══════════════════════════════════════════════════════════════════
# Section 1 — Freshness
# ══════════════════════════════════════════════════════════════════
_FRESH_STYLE = {
    "current":       ("#3fb950", "rgba(63,185,80,0.07)"),
    "getting_stale": ("#e3b341", "rgba(227,179,65,0.07)"),
    "stale":         ("#f59e0b", "rgba(245,158,11,0.09)"),
    "no_data":       ("#8b949e", "rgba(139,148,158,0.07)"),
}
_f_color, _f_bg = _FRESH_STYLE.get(freshness["state"],
                                   _FRESH_STYLE["no_data"])
_last_bit = ""
if last:
    _last_bit = (f"Last check-in {(last.get('completed_at') or '')[:10]}"
                 f" · data through {last.get('data_through') or '—'}")

fcol1, fcol2 = st.columns([5, 1.4])
with fcol1:
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"background:{_f_bg};border:1px solid {_f_color}44;"
        f"border-left:3px solid {_f_color};border-radius:8px;"
        f"padding:8px 14px'>"
        f"<span style='color:{_f_color};font-weight:700'>"
        f"{_h(freshness['headline'])}</span>"
        f"<span style='color:#8b949e;font-size:0.85rem'>"
        f"{_h(freshness['detail'])}</span>"
        f"<span style='color:#8b949e;font-size:0.78rem;margin-left:auto'>"
        f"{_h(_last_bit)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
with fcol2:
    _import_label = ("Import statements"
                     if freshness["state"] in ("stale", "no_data",
                                               "getting_stale")
                     else "Add data")
    if st.button(f"📥 {_import_label}", use_container_width=True,
                 type=("primary" if freshness["state"] in
                       ("stale", "no_data") else "secondary")):
        st.switch_page("pages/3_Import.py")

if freshness.get("update_action"):
    st.caption("⚠ " + freshness["update_action"])
if freshness.get("incomplete_note"):
    st.caption("🗓 " + freshness["incomplete_note"])

# ══════════════════════════════════════════════════════════════════
# Section 2 — Verdict
# ══════════════════════════════════════════════════════════════════
_VERDICT_COLOR = {
    "on_track":     "#3fb950",
    "tight":        "#e3b341",
    "behind":       "#f85149",
    "period_ended": "#4f86c6",
    "stale_data":   "#f59e0b",
    "insufficient": "#8b949e",
    "no_data":      "#8b949e",
}
_v_color = _VERDICT_COLOR.get(verdict["state"], "#8b949e")
st.markdown(
    f"<div style='margin:14px 0 4px 0'>"
    f"<div style='display:flex;align-items:center;gap:10px'>"
    f"<span style='width:12px;height:12px;border-radius:50%;"
    f"background:{_v_color};display:inline-block'></span>"
    f"<span style='font-size:1.5rem;font-weight:800;color:#e6edf3'>"
    f"{_h(verdict['headline'])}</span>"
    f"</div>"
    f"<div style='font-size:0.95rem;color:#c9d1d9;margin-top:4px;"
    f"line-height:1.5'>{_h(verdict['sentence'])}</div>"
    f"</div>",
    unsafe_allow_html=True,
)

st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 3 — Safe to Spend
# ══════════════════════════════════════════════════════════════════
if sts.get("available"):
    r = sts["reserved"]
    _reserved_total = (r["bills_remaining"] + r["subscriptions_remaining"]
                      + r["savings_target"] + r["debt_or_fee_reserve"]
                      + r["buffer"])
    _amt = float(sts["amount"])
    _amt_color = "#3fb950" if _amt >= 0 else "#f85149"

    # "Ended" can come from the data (0 days left in the data month) or
    # from the calendar (the newest data month is already behind today).
    _ended = sts.get("period_ended") or verdict["state"] == "period_ended"
    if _ended:
        if progress.get("has_current_plan"):
            _sub_line = (f"This period has ended and "
                         f"{progress.get('calendar_month', 'next month')} "
                         f"is already planned — import fresh data to "
                         f"track it.")
        else:
            _sub_line = ("This planning period has ended. "
                         "Start or update your next plan.")
    elif _amt < 0:
        _sub_line = (f"You are projected to exceed the current plan by "
                     f"${abs(_amt):,.0f}.")
    else:
        _bits = [f"${sts['weekly_amount']:,.0f} this week"]
        if (sts.get("daily_amount") is not None and sts["days_left"] > 1
                and not _ended):
            _bits.append(f"≈ ${float(sts['daily_amount']):,.0f}/day")
        _bits.append(f"{sts['days_left']} day"
                     f"{'s' if sts['days_left'] != 1 else ''} left")
        _sub_line = " · ".join(_bits)

    st.markdown(
        f"<div style='margin:4px 0'>"
        f"<div style='font-size:0.75rem;color:#8b949e;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.08em'>"
        f"Safe to Spend · {_h(sts['month'])}</div>"
        f"<div style='font-size:3rem;font-weight:800;color:{_amt_color};"
        f"line-height:1.15'>${_amt:,.0f}</div>"
        f"<div style='font-size:0.95rem;color:#e6edf3'>{_h(_sub_line)}</div>"
        f"<div style='font-size:0.85rem;color:#8b949e;margin-top:4px'>"
        f"After bills, planned savings, and your safety buffer "
        f"(${_reserved_total:,.0f} already reserved).</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Why this number?"):
        _goal_allocation_line = (
            f"&nbsp;&nbsp;Named goal allocations inside that target: "
            f"<b>${r.get('goal_allocations_total', 0):,.0f}</b><br>"
            if r.get("goal_allocations_total") else ""
        )
        st.markdown(
            f"<div style='font-size:0.9rem;color:#c9d1d9;line-height:1.7'>"
            f"Expected income: <b>${r['income_expected']:,.0f}</b><br>"
            f"− Spent so far: <b>${r['spending_so_far']:,.0f}</b><br>"
            f"− Bills still to come: <b>${r['bills_remaining']:,.0f}</b><br>"
            f"− Active subscriptions: "
            f"<b>${r['subscriptions_remaining']:,.0f}</b><br>"
            f"− Savings target: <b>${r['savings_target']:,.0f}</b><br>"
            f"{_goal_allocation_line}"
            f"− Interest &amp; fees reserve: "
            f"<b>${r['debt_or_fee_reserve']:,.0f}</b><br>"
            f"− Safety buffer: <b>${r['buffer']:,.0f}</b><br>"
            f"= Safe to Spend: <b style='color:{_amt_color}'>"
            f"${_amt:,.0f}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )
        for why in (sts.get("why") or [])[:4]:
            st.caption("• " + str(why).replace("$", r"\$"))
        st.caption(f"Confidence: {sts.get('confidence', 'medium')}."
                   + ("  " + sts["partial_month_note"].replace("$", r"\$")
                      if sts.get("partial_month_note") else ""))
else:
    st.info(sts.get("reason")
            or "Import transactions to unlock Safe to Spend.")

st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 4 — Monthly Progress
# ══════════════════════════════════════════════════════════════════
st.markdown('<p class="ledger-section-header">Monthly Progress</p>',
            unsafe_allow_html=True)
if progress.get("has_plan"):
    mp1, mp2, mp3, mp4 = st.columns(4)
    mp1.metric("Savings target", f"${progress['savings_target']:,.0f}",
               help="Fixed dollar target from this month's saved plan.")
    _rate_bit = None
    if progress.get("target_rate") is not None:
        _rate_bit = f"{progress['target_rate'] * 100:.0f}% of income"
    mp1.caption(_rate_bit or "")
    if progress.get("goal_allocations_total"):
        mp1.caption(
            f"${progress['goal_allocations_total']:,.0f} assigned to named goals"
        )
    mp2.metric(
        "Projected savings", f"${progress['projected_net']:,.0f}",
        delta=(f"{progress['projected_rate'] * 100:.0f}% rate"
               if progress.get("projected_rate") else None),
        help="Projected month-end income minus projected spending.",
    )
    _gap = float(progress["savings_gap"])
    mp3.metric(
        "Gap to target", f"${abs(_gap):,.0f}",
        delta=("on target" if abs(_gap) < 1
               else ("ahead" if _gap > 0 else "short")),
        delta_color=("normal" if _gap >= 0 else "inverse"),
        help="Projected savings minus the savings target.",
    )
    pv = progress.get("pace_vs_prev") or {}
    _pd = float(pv.get("delta") or 0)
    mp4.metric(
        f"Spending vs {pv.get('prev_month', 'last month')}",
        f"${pv.get('spend_now', 0):,.0f}",
        delta=f"${abs(_pd):,.0f} {'more' if _pd > 0 else 'less'}",
        delta_color=("inverse" if _pd > 0 else "normal"),
        help=(f"Both months measured through day "
              f"{pv.get('through_day', '?')} — a same-point comparison, "
              f"not a full-month one."),
    )
else:
    st.caption(
        f"No plan saved for {progress.get('month') or 'this month'} yet — "
        "savings progress unlocks once a plan exists. It takes about a "
        "minute on the Plan page."
    )
    if st.button("Open Plan →", key="home_plan_cta"):
        st.switch_page("pages/12_Month_Plan.py")

mr = packet["monthly_review"]
if mr.get("available"):
    st.caption(
        (f"{mr['month']} closed with \\${mr['net']:,.0f} kept "
         f"({mr['savings_rate']:.0f}% savings rate) — "
         f"\\${abs(mr['net_delta']):,.0f} "
         f"{'better' if mr['net_delta'] >= 0 else 'worse'} than "
         f"{mr['prev_month']}.")
    )

with st.expander("Monthly score (secondary indicator)"):
    st.caption(
        "Money Pulse is a composite 0-100 score of the latest complete "
        "statement month. It is a report card, not a forecast — the "
        "numbers above are what to act on. Full breakdown lives on the "
        "Detailed overview page."
    )
    try:
        from utils.analytics import compute_score, score_label
        _score = compute_score(conn=conn)
        st.metric("Money Pulse", f"{_score['total']}/100",
                  delta=score_label(_score["total"]))
    except Exception:
        st.caption("Score unavailable.")
    if st.button("Open Detailed overview →", key="home_dash_link"):
        st.switch_page("pages/1_Dashboard.py")

st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 5 — Goal focus
# ══════════════════════════════════════════════════════════════════
if goal:
    st.markdown('<p class="ledger-section-header">Goal focus</p>',
                unsafe_allow_html=True)
    _goal_currency = goal.get("currency") or "CAD"
    _goal_pct = float(goal.get("progress_pct") or 0)
    g1, g2 = st.columns([4, 1])
    with g1:
        st.markdown(
            f"**{_h(goal.get('name'))}** · {_h(goal.get('type_label'))}"
        )
        st.progress(_goal_pct)
        st.caption(
            f"{_goal_currency} ${goal.get('current_amount', 0):,.0f} of "
            f"${goal.get('target_amount', 0):,.0f} · "
            f"{_goal_pct * 100:.0f}% complete"
        )
        if goal.get("stale_warning"):
            st.warning(goal["stale_warning"])
        elif goal.get("pace_state") == "overdue":
            st.caption(
                f"Target date passed · ${goal.get('gap', 0):,.0f} remaining."
            )
        elif goal.get("next_contribution"):
            st.caption(
                f"Next useful contribution: "
                f"${goal['next_contribution']:,.0f}. "
                f"Source: {goal.get('source_label') or 'manual progress'}."
            )
    with g2:
        if st.button("Open Goals →", key="home_goals_link",
                     use_container_width=True):
            st.switch_page("pages/15_Goals.py")
    st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 6 — What changed
# ══════════════════════════════════════════════════════════════════
st.markdown('<p class="ledger-section-header">What changed</p>',
            unsafe_allow_html=True)
if changes:
    ccols = st.columns(len(changes))
    _DIR_COLOR = {"up": "#f85149", "down": "#3fb950"}
    for i, ch in enumerate(changes):
        good = ch["kind"] in ("reduced_spending", "improved_savings")
        color = "#3fb950" if good else _DIR_COLOR.get(ch.get("direction"),
                                                      "#e3b341")
        with ccols[i]:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.02);"
                f"border:1px solid rgba(255,255,255,0.08);"
                f"border-left:3px solid {color};border-radius:8px;"
                f"padding:12px 14px;min-height:120px'>"
                f"<div style='font-weight:700;color:#e6edf3;"
                f"font-size:0.92rem;margin-bottom:4px'>"
                f"{_h(ch['title'])}</div>"
                f"<div style='font-size:0.8rem;color:#c9d1d9;"
                f"line-height:1.5'>{_h(ch['why_it_matters'])}</div>"
                f"<div style='font-size:0.72rem;color:#8b949e;"
                f"margin-top:6px'>{_h(ch['period'])} · confidence "
                f"{_h(ch.get('confidence', 'medium'))}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            drill = ch.get("drill") or {}
            if drill.get("page"):
                if st.button("Inspect →", key=f"home_drill_{i}",
                             use_container_width=True):
                    _route(drill["page"],
                           category=drill.get("category", ""),
                           merchant=drill.get("merchant", ""))
else:
    st.caption(
        f"No changes above ${packet['materiality_threshold']:,.0f} to "
        "report for the latest complete month. Quiet is good."
    )

st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 6 — This week
# ══════════════════════════════════════════════════════════════════
st.markdown('<p class="ledger-section-header">This week</p>',
            unsafe_allow_html=True)
if actions:
    for idx, act in enumerate(actions):
        is_primary = idx == 0
        border = "#3fb950" if is_primary else "rgba(255,255,255,0.15)"
        label = "Do this" if is_primary else "Also worth doing"
        a1, a2 = st.columns([4.2, 1.2])
        with a1:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.02);"
                f"border:1px solid rgba(255,255,255,0.08);"
                f"border-left:3px solid {border};border-radius:8px;"
                f"padding:10px 14px;margin-bottom:6px'>"
                f"<div style='font-size:0.7rem;color:#8b949e;"
                f"font-weight:700;text-transform:uppercase;"
                f"letter-spacing:0.08em'>{label}</div>"
                f"<div style='font-weight:700;color:#e6edf3;"
                f"font-size:0.95rem'>{_h(act['title'])}</div>"
                f"<div style='font-size:0.8rem;color:#8b949e;"
                f"line-height:1.45'>{_h(act.get('why', ''))}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        with a2:
            if act.get("page") and act.get("label"):
                if st.button(act["label"], key=f"home_act_{act['id']}",
                             use_container_width=True,
                             type=("primary" if is_primary
                                   else "secondary")):
                    _route(act["page"],
                           category=act.get("category", ""))
else:
    st.caption("Nothing urgent this week.")

st.divider()

# ══════════════════════════════════════════════════════════════════
# Section 7 — Finish check-in
# ══════════════════════════════════════════════════════════════════
_done_key = "home_checkin_done"
if st.session_state.get(_done_key):
    done = st.session_state[_done_key]
    st.success(
        f"✅ Weekly check-in complete — {done['when']}.  \n"
        f"Data through {done['data_through'] or '—'} · "
        f"acknowledged: {done['action'] or 'no action needed'}."
    )
    _next_hint = ("after your next statement import"
                  if freshness["state"] != "current"
                  else "in about a week")
    st.caption(f"Nothing else needed today. The next review will be "
               f"useful {_next_hint}.")
else:
    fin1, fin2 = st.columns([1.6, 4])
    with fin1:
        if st.button("✓ Finish weekly check-in", type="primary",
                     use_container_width=True):
            from datetime import datetime
            _primary = actions[0]["title"] if actions else ""
            insert_checkin({
                "planning_period": progress.get("month") or "",
                "data_through": freshness.get("latest_date") or "",
                "safe_to_spend": (float(sts["amount"])
                                  if sts.get("available") else None),
                "primary_action": _primary,
            }, conn=conn)
            st.session_state[_done_key] = {
                "when": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "data_through": freshness.get("latest_date") or "",
                "action": _primary,
            }
            st.rerun()
    with fin2:
        _pending_note = ""
        try:
            _small_flags = conn.execute(
                "SELECT COUNT(*) FROM transactions "
                "WHERE is_flagged=1 AND ABS(amount) < ?",
                (packet["materiality_threshold"],),
            ).fetchone()[0]
            if _small_flags:
                _pending_note = (
                    f"{_small_flags} small uncategorized item"
                    f"{'s' if _small_flags != 1 else ''} can wait — "
                    "finishing now is fine."
                )
        except Exception:
            pass
        st.caption(
            "Marks this week's review done and records the numbers you "
            "saw. " + _pending_note
        )

conn.close()

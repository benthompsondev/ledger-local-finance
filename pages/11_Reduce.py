"""Reduce — focused workspace for trimming spend."""
import streamlit as st

from utils.database import init_db, get_connection
from utils.insights import (
    subscription_detective, top_controllable_categories, recurring_merchants,
    coverage_summary,
)
from utils.styles import inject_styles
from utils.ai_config import ai_is_ready
from utils.ai_cache import evidence_hash, get_cached, get_or_compute, clear as clear_ai_cache
from utils.navigation import set_transaction_search
from utils.reduce_actions import (
    CATEGORY_FIRST_ACTION as _FIRST_ACTION,
    CATEGORY_DIFFICULTY as _DIFFICULTY_BY_CAT,
)

st.set_page_config(page_title="Reduce · Ledger", page_icon="✂️", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Reduce  ·  Trim Spend Workspace")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()
cov = coverage_summary(conn=conn)

if cov["total_months"] == 0:
    st.info("No data yet. Import statements first to find recurring charges and "
            "controllable categories.")
    conn.close()
    st.stop()

# ── Deterministic evidence ─────────────────────────────────────────────
det          = subscription_detective(conn=conn)
controllable = top_controllable_categories(conn=conn, limit=5)
recurring    = recurring_merchants(min_months=3, conn=conn)

active_candidates = det.get("active_candidates") or []
stale_candidates  = det.get("stale_candidates") or []
active_subs       = det.get("active_subs") or []
stale_subs        = det.get("stale_subs") or []
active_window     = int(det.get("active_window_days") or 60)
anchor_date       = det.get("anchor_date") or ""

# Active recurring merchants (debit, ≥3 months) restricted to last_seen
# within the active window of the anchor. recurring_merchants() doesn't
# carry last_seen, so we re-pull from the active_subs set when present
# AND fall back to recurring_merchants for non-Subscription categories.
# This keeps the "Active recurring" section honest about what's
# actually still charging.

# Build the slim packet for AI summary + fallback. Numbers are
# authoritative — use ACTIVE candidates only so AI doesn't hallucinate
# savings from things that already stopped.
packet = {
    "active_subscription_candidates": [
        {
            "merchant":     s["merchant"],
            "monthly":      float(s["avg_amount"]),
            "annual":       float(s["annual"]),
            "months_seen":  int(s["months_seen"]),
            "flags":        list(s.get("flags") or []),
            "last_seen":    s.get("last_seen", ""),
        }
        for s in active_candidates
    ],
    "active_subscriptions_top": [
        {
            "merchant": s["merchant"],
            "monthly":  float(s["avg_amount"]),
            "annual":   float(s["annual"]),
        }
        for s in active_subs[:8]
    ],
    "stale_subscriptions": [
        {
            "merchant":  s["merchant"],
            "monthly":   float(s["avg_amount"]),
            "annual":    float(s["annual"]),
            "last_seen": s.get("last_seen", ""),
        }
        for s in stale_candidates[:6]
    ],
    "controllable_categories": [
        {
            "category":    c["category"],
            "monthly_avg": float(c["monthly_avg"]),
            "total_90d":   float(c["total_90d"]),
            "tx_count":    int(c["tx_count"]),
        }
        for c in controllable
    ],
    "active_monthly_estimate":         float(det.get("active_monthly_estimate") or 0),
    "active_annual_total":             float(det.get("active_annual_total") or 0),
    "active_candidate_annual_total":   float(det.get("active_candidate_annual_total") or 0),
    "stale_annual_total":              float(det.get("stale_annual_total") or 0),
    "anchor_date":                     anchor_date,
}

# ── Pass 25: This Week's Reduce Plan ───────────────────────────────────
# A compact, deterministic "what should I do FIRST" card. Picks the top
# active cancel candidate if present, else the top controllable category.
# All numbers are deterministic — no AI in this card. Designed to be the
# practical starting point of the page.
def _this_weeks_plan():
    if active_candidates:
        c = active_candidates[0]
        merchant = c["merchant"]
        return {
            "kind":       "subscription",
            "title":      f"Cancel or downgrade {merchant}",
            "current":    f"${c['avg_amount']:,.0f}/mo",
            "target":     "$0/mo",
            "save_mo":    f"${c['avg_amount']:,.0f}/mo",
            "save_yr":    f"${c['annual']:,.0f}/yr",
            "first":      ("Open the merchant's account, cancel or "
                           "downgrade, then keep an eye on your next "
                           "statement to confirm."),
            "link_kind":  "merchant",
            "link_value": merchant,
            "effort":     "Low — usually one form / one email.",
        }
    if controllable:
        c = controllable[0]
        cat = c["category"]
        avg = float(c["monthly_avg"])
        target = round(avg * 0.80, 0)
        save_mo = max(0.0, avg - target)
        return {
            "kind":       "category",
            "title":      f"Trim {cat} by ~20%",
            "current":    f"${avg:,.0f}/mo",
            "target":     f"${target:,.0f}/mo",
            "save_mo":    f"${save_mo:,.0f}/mo",
            "save_yr":    f"${save_mo*12:,.0f}/yr",
            "first":      _FIRST_ACTION.get(
                cat, f"Open Transactions filtered to {cat} and review the "
                     "5 largest charges first."),
            "link_kind":  "category",
            "link_value": cat,
            "effort":     "Moderate — one habit change for the week.",
        }
    return None


_plan = _this_weeks_plan()
if _plan:
    st.markdown(
        '<p class="ledger-section-header">This Week\'s Reduce Plan</p>',
        unsafe_allow_html=True,
    )
    _safe_first = (_plan["first"] or "").replace("$", r"\$")
    st.markdown(
        f"<div style='background:rgba(63,185,80,0.06);"
        f"border:1px solid rgba(63,185,80,0.25);"
        f"border-left:3px solid #3fb950;border-radius:8px;"
        f"padding:14px 16px;margin-bottom:8px'>"
        f"<div style='font-size:1.05rem;font-weight:700;color:#e6edf3;"
        f"margin-bottom:6px'>{_plan['title']}</div>"
        f"<div style='font-size:0.85rem;color:#c9d1d9;"
        f"line-height:1.55;margin-bottom:6px'>"
        f"<b>Current:</b> {_plan['current']}  ·  "
        f"<b>Target:</b> {_plan['target']}  ·  "
        f"<b>Save:</b> <span style='color:#3fb950'>{_plan['save_mo']}</span> "
        f"(<span style='color:#3fb950'>{_plan['save_yr']}</span>)  ·  "
        f"<b>Effort:</b> {_plan['effort']}"
        f"</div>"
        f"<div style='font-size:0.85rem;color:#c9d1d9;line-height:1.55'>"
        f"<b>First action:</b> {_safe_first}</div>"
        f"</div>".replace("$", r"\$"),
        unsafe_allow_html=True,
    )
    if st.button(f"See {_plan['link_value']} transactions",
                 key="this_week_link"):
        if _plan["link_kind"] == "merchant":
            set_transaction_search(merchant=_plan["link_value"],
                                   all_time=True)
        else:
            set_transaction_search(category=_plan["link_value"],
                                   all_time=True)
        st.switch_page("pages/2_Transactions.py")

    _challenge_mo = float(str(_plan.get("save_mo", "$0")).replace("$", "").replace(",", "").replace("/mo", "") or 0)
    _challenge_week = max(1.0, _challenge_mo / 4.0)
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);"
        f"border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid #4f86c6;border-radius:8px;"
        f"padding:12px 14px;margin:8px 0 4px 0'>"
        f"<div style='font-size:0.72rem;color:#8b949e;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px'>"
        f"This week's challenge</div>"
        f"<div style='font-size:1rem;color:#e6edf3;font-weight:700;"
        f"margin-bottom:4px'>Bank ~${_challenge_week:,.0f} this week</div>"
        f"<div style='font-size:0.86rem;color:#c9d1d9;line-height:1.5'>"
        f"Treat this like a score streak: do the first action, then keep "
        f"the saved money pointed at your Plan goal instead of letting it "
        f"leak into another category.</div>"
        f"</div>".replace("$", r"\$"),
        unsafe_allow_html=True,
    )
    st.divider()

# ── KPI strip ──────────────────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">At a Glance</p>', unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Active subscriptions", len(active_subs))
k2.metric("Active monthly recurring",
          f"${det.get('active_monthly_estimate', 0):,.0f}",
          help="Sum of avg monthly amounts for subscriptions still seen "
               f"within {active_window} days of latest imported tx.")
k3.metric("Active annualised",    f"${det.get('active_annual_total', 0):,.0f}")
k4.metric(
    "Cancel-candidate annual",
    f"${det.get('active_candidate_annual_total', 0):,.0f}",
    help="Annualised cost of ACTIVE subscriptions flagged for review "
         "(price-increase / low-usage / duplicate). Stale / inactive "
         "subscriptions are excluded — they may already be cancelled.",
)
if anchor_date:
    st.caption(f"Active = last seen within {active_window} days of "
               f"latest imported transaction ({anchor_date}). "
               f"Stale subs are listed separately below.")

st.divider()

# ── B. Active cancellation candidates (deterministic detail) ───────────
# Pass 18: renamed and gated to ACTIVE candidates only. Stale subs get
# their own section further down so the user can review them without
# them counting as fresh savings.
st.markdown('<p class="ledger-section-header">Active Cancellation Candidates</p>',
            unsafe_allow_html=True)
st.caption("Subscriptions still charging within the active window — "
           "ranked by review priority (price-increase / low-usage / duplicate).")

_FLAG_LABEL = {
    "stale":               ("😴 Stale",          "#8b949e"),
    "price_increase":      ("📈 Price increase", "#e3b341"),
    "variable_amount":     ("〰️ Variable amount", "#8b949e"),
    "duplicate_candidate": ("⚠ Duplicate?",     "#f59e0b"),
    "low_usage":           ("🔎 Low usage",      "#4f86c6"),
}


def _flag_chips(flags: list[str]) -> str:
    chips = ""
    for f in flags:
        # Don't show the bare "stale" chip in the active section — by
        # construction these are not stale. Don't show price_increase
        # chip on stale-section rows either (gated below).
        if f == "stale":
            continue
        lbl, col = _FLAG_LABEL.get(f, (f.replace("_", " ").title(), "#8b949e"))
        chips += (f"<span style='background:{col};color:#fff;padding:1px 7px;"
                  f"border-radius:3px;font-size:10px;font-weight:600;margin-right:4px'>"
                  f"{lbl}</span>")
    return chips


if not active_candidates:
    st.info("No active cancellation candidates right now. The active "
            "subscriptions list further down is sorted by annual cost so "
            "you can review the largest ones.")
else:
    for c in active_candidates:
        chips = _flag_chips(list(c["flags"]))
        saving_chip = (
            f"<span style='background:#3fb950;color:#fff;padding:1px 7px;"
            f"border-radius:3px;font-size:10px;font-weight:700;margin-right:4px'>"
            f"~${c['annual']:,.0f}/yr if cancelled</span>"
        )
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.07);"
            f"border-left:3px solid #e3b341;border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:4px'>"
            f"<div>"
            f"<div style='font-weight:600;color:#e6edf3;font-size:0.92rem'>{c['merchant']}</div>"
            f"<div style='color:#8b949e;font-size:0.78rem'>"
            f"~${c['avg_amount']:,.0f}/mo · seen {c['months_seen']} month(s)"
            + (f" · last {c['last_seen']}" if c.get("last_seen") else "") + "</div>"
            f"</div>"
            f"<div>{saving_chip}{chips}</div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        # Each candidate gets a "See in Transactions" deep link via the
        # central navigation helper so OpenAI / Patreon / etc. all use
        # the same alias rules.
        link_col, _ = st.columns([1.5, 4])
        if link_col.button(f"See {c['merchant']} transactions",
                           key=f"reduce_see_{c['merchant']}"):
            set_transaction_search(merchant=c["merchant"], all_time=True)
            st.switch_page("pages/2_Transactions.py")

st.divider()

# ── C. Controllable spending — explicit targets + savings ─────────────
st.markdown('<p class="ledger-section-header">Controllable Spending — Cut Targets</p>',
            unsafe_allow_html=True)
st.caption(
    "Top consumption categories from the last 90 days. Each row shows the "
    "current monthly average, a suggested target (20% cut by default — easy "
    "first move), monthly + annual savings if you hit it, and a concrete "
    "first action."
)

_DIFF_COLOR = {"easy": "#3fb950", "moderate": "#e3b341", "harder": "#f85149"}

if not controllable:
    st.info("Need 90 days of consumption data to rank controllable categories.")
else:
    # Pass 27: render the top 3 by default; the rest live behind a
    # "Show more cut targets" expander so the page stays focused on
    # the most actionable wins.
    def _render_target_row(c: dict, key_prefix: str = "main") -> None:
        cat = c["category"]
        m_avg = float(c["monthly_avg"])
        target = round(m_avg * 0.80, 0)
        save_mo = max(0.0, m_avg - target)
        save_yr = save_mo * 12
        difficulty = _DIFFICULTY_BY_CAT.get(cat, "moderate")
        diff_color = _DIFF_COLOR[difficulty]
        first_action = _FIRST_ACTION.get(
            cat,
            f"Open Transactions filtered to {cat} and review the largest 5 charges.",
        )
        with st.container():
            r1, r2, r3, r4, r5, r6 = st.columns([2.0, 1.1, 1.1, 1.1, 1.1, 1.4])
            r1.markdown(f"**{cat}**")
            r2.markdown(
                f"<span style='font-variant-numeric:tabular-nums;color:#e6edf3'>"
                f"${m_avg:,.0f}</span>",
                unsafe_allow_html=True,
            )
            r3.markdown(
                f"<span style='font-variant-numeric:tabular-nums;color:#c9d1d9'>"
                f"${target:,.0f}</span>",
                unsafe_allow_html=True,
            )
            r4.markdown(
                f"<span style='color:#3fb950;font-variant-numeric:tabular-nums'>"
                f"+${save_mo:,.0f}</span>",
                unsafe_allow_html=True,
            )
            r5.markdown(
                f"<span style='color:#3fb950;font-variant-numeric:tabular-nums;"
                f"font-weight:600'>+${save_yr:,.0f}</span>",
                unsafe_allow_html=True,
            )
            r6.markdown(
                f"<span style='background:{diff_color};color:#fff;"
                f"padding:1px 7px;border-radius:3px;font-size:10px;"
                f"font-weight:700;text-transform:uppercase'>{difficulty}</span>",
                unsafe_allow_html=True,
            )
            st.caption(f"➤ **First action:** {first_action}")
            link_col, _, _ = st.columns([2, 1, 4])
            if link_col.button(
                f"See {cat} transactions",
                key=f"reduce_filter_{key_prefix}_{cat}",
                use_container_width=True,
            ):
                set_transaction_search(category=cat, all_time=True)
                st.switch_page("pages/2_Transactions.py")
            st.markdown(
                "<div style='border-bottom:1px solid rgba(255,255,255,0.05);"
                "margin:6px 0 8px 0'></div>",
                unsafe_allow_html=True,
            )

    h1, h2, h3, h4, h5, h6 = st.columns([2.0, 1.1, 1.1, 1.1, 1.1, 1.4])
    for col, lbl in zip(
        (h1, h2, h3, h4, h5, h6),
        ("Category", "Current /mo", "Target /mo", "Save /mo", "Save /yr", "Difficulty"),
    ):
        col.markdown(
            f"<div style='font-size:10px;font-weight:700;color:#8b949e;"
            f"text-transform:uppercase;letter-spacing:0.06em'>{lbl}</div>",
            unsafe_allow_html=True,
        )

    _top3 = controllable[:3]
    _rest = controllable[3:]
    for c in _top3:
        _render_target_row(c, key_prefix="top3")
    if _rest:
        with st.expander(f"Show more cut targets ({len(_rest)})",
                         expanded=False):
            for c in _rest:
                _render_target_row(c, key_prefix="more")

    total_save_yr = sum(max(0.0, c["monthly_avg"] * 0.20 * 12)
                        for c in controllable)
    st.markdown(
        f"<div style='background:rgba(63,185,80,0.06);border:1px solid rgba(63,185,80,0.2);"
        f"border-radius:6px;padding:10px 14px;margin-top:6px;text-align:center'>"
        f"<span style='font-size:0.95rem;color:#e6edf3'>"
        f"Hit every 20% target → <b style='color:#3fb950'>"
        f"~${total_save_yr:,.0f}/year</b> in additional savings.</span>"
        f"</div>".replace("$", r"\$"),
        unsafe_allow_html=True,
    )

st.divider()

# ── D. Inactive recurring services (audit only) ────────────────────────
# Pass 35 Phase 6: stronger wording — these have not charged in 60+ days
# from the latest imported transaction, so they are treated as inactive
# and are NOT presented as active savings opportunities. The list is
# kept for audit/history, not for active reduce decisions.
st.markdown(
    '<p class="ledger-section-header">'
    'Inactive recurring services (audit only)</p>',
    unsafe_allow_html=True,
)
if not stale_candidates:
    st.caption(
        f"No recurring services have gone {active_window}+ days "
        f"without a charge (anchor: {anchor_date or 'latest tx'}). "
        "Every recurring service you've imported still looks active."
    )
else:
    st.caption(
        f"**Inactive — no charge seen in {active_window}+ days "
        f"(anchor: {anchor_date or 'latest tx'}).** These are NOT "
        "active savings opportunities. They are kept here for audit "
        "in case one was cancelled / paused and you want to verify."
    )
    for s in stale_candidates:
        st.markdown(
            f"<div style='background:rgba(139,148,158,0.04);border:1px solid rgba(139,148,158,0.18);"
            f"border-left:3px solid #8b949e;border-radius:6px;padding:8px 12px;margin-bottom:5px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap'>"
            f"<div>"
            f"<div style='font-weight:600;color:#c9d1d9;font-size:0.9rem'>{s['merchant']}</div>"
            f"<div style='color:#8b949e;font-size:0.78rem'>"
            f"Was ~${s['avg_amount']:,.0f}/mo when active · last seen {s.get('last_seen', '?')}"
            + (f" · {s.get('days_since_last_seen')}d ago"
               if s.get("days_since_last_seen", -1) >= 0 else "")
            + " · not counted as a savings opportunity"
            + "</div></div>"
            f"<span style='background:#8b949e;color:#fff;padding:1px 7px;"
            f"border-radius:3px;font-size:10px;font-weight:600'>"
            f"Inactive {active_window}+ days</span>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        if st.button(f"See {s['merchant']} transactions",
                     key=f"reduce_stale_{s['merchant']}"):
            set_transaction_search(merchant=s["merchant"], all_time=True)
            st.switch_page("pages/2_Transactions.py")

st.divider()

# ── E. AI cut summary — lazy, never blocks the page ─────────────────────
# Pass 18: only render when a cached result exists for the current
# evidence, OR the user clicks "Generate AI cut summary". The
# deterministic content above is fully usable without AI; this is a
# bonus surface, not the load-blocking one it used to be.
ai_ready, _ai_reason = ai_is_ready()
ev_hash = evidence_hash(packet)

# Peek the cache without computing — tells us whether to auto-show.
try:
    _cached_val, _cached_hash = get_cached("reduce_workspace_ai_summary")
    cached_summary = _cached_val if _cached_hash == ev_hash else None
except Exception:
    cached_summary = None

st.markdown('<p class="ledger-section-header">What to cut first — AI summary</p>',
            unsafe_allow_html=True)
sum_h1, sum_h2 = st.columns([4, 1])
with sum_h1:
    if ai_ready:
        st.caption("🧠 Optional MiniMax summary grounded in the active "
                   "candidates / controllable categories above. Cached by "
                   "evidence hash — only regenerates when your data changes.")
    else:
        st.caption("🧠 AI is off — the deterministic sections above already "
                   "tell you what to cut. Enable MiniMax in Settings → AI "
                   "Categorization for a plain-English summary on top.")
with sum_h2:
    force_refresh = st.button(
        "↻ Refresh", key="reduce_refresh_ai",
        use_container_width=True,
        help="Regenerate the AI summary even if data hasn't changed.",
    )
    if force_refresh:
        clear_ai_cache("reduce_workspace_ai_summary")

# Decide whether to render: cached available OR user clicked refresh OR
# user clicked "generate". Otherwise show a minimal generate button.
generate_clicked = False
if cached_summary is None and not force_refresh:
    if st.button("Generate AI cut summary", key="reduce_generate_ai",
                 disabled=not ai_ready,
                 help=("Calls MiniMax once to write a short summary "
                       "grounded in the deterministic sections above.")
                 if ai_ready else "Enable AI in Settings to generate."):
        generate_clicked = True

should_render = (cached_summary is not None) or force_refresh or generate_clicked

if should_render:
    from utils.ai_explainer import reduce_workspace_summary
    with st.spinner("Building the cut plan…"):
        summary = get_or_compute(
            "reduce_workspace_ai_summary",
            ev_hash,
            lambda: reduce_workspace_summary(packet),
            force=force_refresh or generate_clicked,
        )

    _ok = summary.get("ok")
    _border = "#3fb950" if _ok else "#8b949e"
    _badge_color = "#3fb950" if _ok else "#8b949e"
    _badge = "AI · grounded" if _ok else "Deterministic fallback"
    st.markdown(
        f"<div style='background:rgba(63,185,80,0.05);border:1px solid rgba(63,185,80,0.2);"
        f"border-left:3px solid {_border};border-radius:8px;padding:14px 16px;margin-bottom:12px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
        f"<span style='font-size:1rem;font-weight:700;color:#e6edf3'>{summary.get('headline','')}</span>"
        f"<span style='background:{_badge_color};color:#fff;padding:2px 8px;"
        f"border-radius:4px;font-size:10px;font-weight:600'>{_badge}</span>"
        f"</div>"
        f"<div style='font-size:0.92rem;color:#c9d1d9;line-height:1.5'>"
        f"<b>First move:</b> {summary.get('first_move','')}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    cands = summary.get("candidates") or []
    cats  = summary.get("categories") or []
    if cands:
        st.markdown("**Cancel candidates:**")
        for c in cands:
            st.markdown(f"- {c}")
    if cats:
        st.markdown("**Controllable categories worth trimming:**")
        for c in cats:
            st.markdown(f"- {c}")
    if summary.get("error"):
        st.caption(f"⚠ {summary['error']}")
    st.caption(f"Grounded in: {' · '.join(summary.get('grounded_from') or [])}  ·  "
               f"{summary.get('provider', '—')}/{summary.get('model', '—')}")

st.divider()

# ══════════════════════════════════════════════════════════════════════
# Pass 30 — "3 practical cuts" replaces the Savings Scenario Simulator.
# User feedback: "Remove the Savings Scenario Simulator. It is bloat
# and not useful." We swap the 6-preset what-if grid + advanced
# slider expander for 3 deterministic cards: Small / Medium / Big.
# Each card is a concrete, named action with a real $/mo number from
# the same inputs the simulator used. No sliders, no scenario picker,
# no projected-savings-rate chart — just three actions ranked by
# effort.
# ══════════════════════════════════════════════════════════════════════
st.markdown('<p class="ledger-section-header">3 practical cuts</p>',
            unsafe_allow_html=True)
st.caption(
    "Three cuts ranked by effort. Pick whichever fits your week — "
    "Small if you want a quick win, Big if you're ready to tighten."
)

# Compute deterministic dollar impacts directly from the same evidence
# the Pass 28 simulator used. No live simulator call required.
_cancel_one_merchant_p30 = (
    (active_candidates[0]["merchant"] if active_candidates else None)
    or (active_subs[0]["merchant"] if active_subs else None)
)
_cancel_one_amt = (
    float(active_candidates[0].get("avg_amount") or 0)
    if active_candidates else
    (float(active_subs[0].get("avg_amount") or 0) if active_subs else 0)
)

# Top controllable category for medium / big cuts. controllable is
# already sorted by 90-day total descending.
_top_cat_row = controllable[0] if controllable else None
_top_cat_name = (_top_cat_row.get("category") if _top_cat_row else None) or "Shopping"
_top_cat_avg  = float(_top_cat_row.get("monthly_avg") or 0) if _top_cat_row else 0
_second_cat_row = controllable[1] if len(controllable) >= 2 else None
_second_cat_name = (_second_cat_row.get("category") if _second_cat_row else None) or "Food & Convenience"

_practical_cuts: list[dict] = [
    {
        "size":    "Small",
        "color":   "#3fb950",
        "label":   (f"Cancel {_cancel_one_merchant_p30}"
                    if _cancel_one_merchant_p30 else
                    f"Trim {_top_cat_name} 5%"),
        "save_mo": (_cancel_one_amt if _cancel_one_merchant_p30
                    else round(_top_cat_avg * 0.05, 0)),
        "first_action": (
            f"Open {_cancel_one_merchant_p30}'s account, cancel or "
            f"downgrade, then verify it's gone on the next statement."
            if _cancel_one_merchant_p30 else
            f"Pause one {_top_cat_name} purchase this week. "
            "Audit your cart before checkout."
        ),
        "effort":  "Low — 10 minutes once.",
    },
    {
        "size":    "Medium",
        "color":   "#e3b341",
        "label":   f"Reduce {_top_cat_name} 10%",
        "save_mo": round(_top_cat_avg * 0.10, 0),
        "first_action": (
            f"Set a {_top_cat_name} weekly limit at "
            f"${(_top_cat_avg * 0.90 / 4):,.0f}/week and stick to it. "
            "Track on the Transactions page."
        ),
        "effort":  "Moderate — daily attention for one month.",
    },
    {
        "size":    "Big",
        "color":   "#f85149",
        "label":   (f"Reduce {_top_cat_name} 20%"
                    + (f" + cancel {_cancel_one_merchant_p30}"
                       if _cancel_one_merchant_p30 else "")),
        "save_mo": (round(_top_cat_avg * 0.20, 0)
                    + (_cancel_one_amt if _cancel_one_merchant_p30 else 0)),
        "first_action": (
            f"Both moves at once: cancel "
            f"{_cancel_one_merchant_p30 or 'one subscription'} "
            f"and shift {_top_cat_name} spending to a $"
            f"{(_top_cat_avg * 0.80):,.0f}/mo cap."
            if _cancel_one_merchant_p30 else
            f"Cap {_top_cat_name} at ${(_top_cat_avg * 0.80):,.0f}/mo "
            f"and review {_second_cat_name} as well."
        ),
        "effort":  "High — a full month of discipline.",
    },
]

_pc_cols = st.columns(3)
for i, cut in enumerate(_practical_cuts):
    save_mo = float(cut["save_mo"] or 0)
    save_yr = save_mo * 12
    with _pc_cols[i]:
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.02);"
            f"border:1px solid rgba(255,255,255,0.07);"
            f"border-left:3px solid {cut['color']};border-radius:8px;"
            f"padding:14px 16px;margin-bottom:6px;height:100%'>"
            f"<div style='font-size:10px;font-weight:700;"
            f"text-transform:uppercase;letter-spacing:0.06em;"
            f"color:{cut['color']};margin-bottom:6px'>{cut['size']}</div>"
            f"<div style='font-size:0.95rem;font-weight:700;"
            f"color:#e6edf3;margin-bottom:8px;line-height:1.35'>"
            f"{cut['label']}</div>"
            f"<div style='font-size:1.1rem;font-weight:700;"
            f"color:#3fb950;margin-bottom:4px'>"
            f"+${save_mo:,.0f}/mo</div>"
            f"<div style='font-size:0.8rem;color:#8b949e;"
            f"margin-bottom:8px'>≈ +${save_yr:,.0f}/yr</div>"
            f"<div style='font-size:0.8rem;color:#c9d1d9;"
            f"line-height:1.45;margin-bottom:6px'>"
            f"<b>First action.</b> {cut['first_action']}</div>"
            f"<div style='font-size:0.75rem;color:#8b949e'>"
            f"<b>Effort.</b> {cut['effort']}</div>"
            f"</div>".replace("$", r"\$"),
            unsafe_allow_html=True,
        )
        if st.button(f"Pick {cut['size']}",
                     key=f"pc_pick_{cut['size'].lower()}",
                     use_container_width=True):
            # Stash the selected savings amount so the Savings Redirect
            # card downstream picks it up.
            st.session_state["reduce_savings_mo"] = save_mo
            # Also stash a synthetic "scenario picked" flag so the
            # Pass 28 redirect-gating logic still works.
            st.session_state["reduce_qs_pick"] = {
                "key":   f"practical_{cut['size'].lower()}",
                "label": cut["label"],
            }

st.divider()

# ══════════════════════════════════════════════════════════════════════
# Pass 27/28 — Savings Redirect / Net Worth Builder
# Pass 28 fix: only render the 4-destination redirect cards once the
# user has actually picked a scenario above. Without a pick, show a
# small empty-state hint so the page reads as guided, not assumptive.
# We DO NOT move money, DO NOT create transfers, DO NOT give
# investment advice — read-only guidance that links to existing pages.
# ══════════════════════════════════════════════════════════════════════
_scenario_picked = st.session_state.get("reduce_qs_pick") is not None
_redirect_amount = float(st.session_state.get("reduce_savings_mo") or 0)

st.markdown(
    '<p class="ledger-section-header">Redirect this savings toward…</p>',
    unsafe_allow_html=True,
)

if not _scenario_picked:
    st.caption(
        "Pick a Quick Scenario above to see where the savings could go. "
        "Once you choose one, four destinations appear here: cash buffer, "
        "debt reduction, investment contribution, or a custom goal."
    )

if _scenario_picked and _redirect_amount > 0:
    st.caption(
        f"You've identified ~${_redirect_amount:,.0f}/mo "
        f"(~${_redirect_amount * 12:,.0f}/yr) of potential savings. "
        "Pick a destination — Ledger does not move money for you. Each "
        "option opens the right page to set it up manually."
    )

    _dest_cols = st.columns(4)
    _DESTINATIONS = [
        ("cash_buffer",   "💧 Cash buffer",
         "Build 1–2 months of breathing room. Best first move when "
         "forecast risk is WATCH or DANGER.",
         "pages/12_Month_Plan.py"),
        ("debt_reduction","💳 Debt reduction",
         "Paydown highest-rate balance first. Open Investments → Cash "
         "/ debts to track current liability balances.",
         "pages/7_Investments.py"),
        ("investment",    "📈 Investment contribution",
         "Move surplus into your brokerage. Import a holdings CSV for "
         "your post-contribution snapshot to capture the change.",
         "pages/7_Investments.py"),
        ("custom_goal",   "🎯 Custom goal",
         "Open Month Plan → Goals tab and create a goal "
         "(emergency fund, sub-reduction, etc.). Linked metrics "
         "auto-track from net_worth / cash / investments.",
         "pages/12_Month_Plan.py"),
    ]
    for i, (k, label, desc, target) in enumerate(_DESTINATIONS):
        with _dest_cols[i]:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.02);"
                f"border:1px solid rgba(255,255,255,0.07);"
                f"border-left:3px solid #4f86c6;border-radius:8px;"
                f"padding:10px 12px;margin-bottom:6px;height:100%'>"
                f"<div style='font-weight:700;color:#e6edf3;"
                f"margin-bottom:4px'>{label}</div>"
                f"<div style='font-size:0.78rem;color:#8b949e;"
                f"line-height:1.45;margin-bottom:6px'>{desc}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if st.button(f"Open →", key=f"redirect_{k}",
                         use_container_width=True):
                st.switch_page(target)

    st.divider()

    # Net Worth Builder mini-card on Reduce.
    try:
        from utils.database import (
            compute_net_worth_now, get_net_worth_snapshots,
        )
        _nw_now = compute_net_worth_now(conn=conn)
        _nw_hist = get_net_worth_snapshots(conn=conn, limit=2) or []
    except Exception:
        _nw_now = None
        _nw_hist = []

    st.markdown(
        '<p class="ledger-section-header">Net Worth Builder</p>',
        unsafe_allow_html=True,
    )

    if _nw_now and _nw_now.get("breakdown"):
        _current = float(_nw_now.get("net_worth") or 0)

        def _next_milestone_red(v: float) -> float:
            import math
            if v < 1_000:        step = 500
            elif v < 10_000:     step = 1_000
            elif v < 50_000:     step = 5_000
            elif v < 250_000:    step = 25_000
            elif v < 1_000_000:  step = 50_000
            else:                step = 100_000
            return float(math.floor(v / step + 1) * step)

        _ms = _next_milestone_red(_current)
        _gap = _ms - _current
        _months_to_ms = (
            (_gap / _redirect_amount) if _redirect_amount > 0 else None
        )
        _months_str = (f"~{_months_to_ms:,.0f} months at this savings pace"
                       if _months_to_ms is not None and
                          0 < _months_to_ms < 1200 else
                       "")
        st.markdown(
            f"<div style='background:rgba(52,208,88,0.05);"
            f"border:1px solid rgba(52,208,88,0.2);"
            f"border-left:3px solid #34d058;border-radius:8px;"
            f"padding:12px 14px;margin-bottom:6px'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"margin-bottom:6px'>"
            f"<div style='font-size:1rem;font-weight:700;color:#e6edf3'>"
            f"Net worth: ${_current:,.0f}</div>"
            f"<div style='font-size:0.85rem;color:#8b949e'>"
            f"Next milestone: ${_ms:,.0f}</div></div>"
            f"<div style='font-size:0.85rem;color:#c9d1d9;line-height:1.5'>"
            f"${_gap:,.0f} to next milestone. {_months_str}.</div></div>"
            .replace("$", r"\$"),
            unsafe_allow_html=True,
        )
    else:
        # Empty state — guide first inputs
        _missing = list((_nw_now or {}).get("missing") or [])
        st.info(
            "🛠 Net worth not tracked yet. To start: add cash + credit "
            "card balances on **Investments → Cash / debts**, or import "
            "a holdings CSV on the **Investments** page. Snapshots build "
            "automatically as you add inputs."
            + (f" Missing: {', '.join(_missing)}." if _missing else ""),
        )
    if st.button("→ Investments / Net Worth",
                 key="reduce_nw_jump"):
        st.switch_page("pages/7_Investments.py")

    st.divider()

# ── F. Full active subscriptions list ──────────────────────────────────
with st.expander(f"All {len(active_subs)} active recurring merchant(s) — "
                 f"sorted by annual cost"):
    rows = sorted(active_subs, key=lambda s: -s["annual"])
    for s in rows:
        chip_html = _flag_chips(list(s["flags"]))
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;padding:4px 0;"
            f"border-bottom:1px solid rgba(255,255,255,0.04)'>"
            f"<span style='color:#e6edf3;font-size:0.86rem'>{s['merchant']}{chip_html}</span>"
            f"<span style='color:#8b949e;font-size:0.82rem;font-variant-numeric:tabular-nums'>"
            f"${s['avg_amount']:,.0f}/mo · ${s['annual']:,.0f}/yr</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ── G. Search hint for non-listed merchants ────────────────────────────
st.caption(
    "Looking for a specific charge? Open **Transactions** and use the search "
    "box at the top — it covers merchant, description, category, notes, and "
    "amount text. Quick chips for **Subscriptions / Large debits / Cash advance** "
    "help narrow further."
)

conn.close()

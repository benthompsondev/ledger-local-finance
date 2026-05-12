"""
Settings / Control Center — v2.0
Sections: Profiles · Budgets · Scoring · Rules · Watch List · Data · About
"""
import streamlit as st
import json
from pathlib import Path
from datetime import datetime

from utils.database import (
    init_db, get_connection, delete_all_transactions, rerun_categorization,
    get_budgets, upsert_budget, delete_budget,
    get_profiles, get_active_profile, upsert_profile, set_active_profile, delete_profile,
    get_score_weights, save_score_weights,
    get_watch_list, add_to_watch_list, remove_from_watch_list,
    list_learned_rules, delete_learned_rule,
)
from utils.watcher import get_watch_folder, set_watch_folder
from utils.platform_utils import watch_folder_placeholder, open_folder_in_explorer, is_windows
from config.categories import CATEGORIES, BUDGETABLE_CATEGORIES, SYSTEM_CATEGORIES
from utils.ai_config import (
    get_ai_settings, update_ai_settings, clear_api_key, redacted_settings,
    SUPPORTED_PROVIDERS, DEFAULT_MODELS, DEFAULT_BASE_URLS,
)
from utils.ai_categorizer import provider_status, suggest_for_transaction
from utils.ai_explainer import (
    ai_features_status, dashboard_copilot, review_triage_summary,
    explain_recommendation, ask_ledger, weekly_review, explain_scenario,
    ai_health_check, last_ai_call_status,
)


def _render_diag(feature_id: str, res: dict) -> None:
    """Render a sanitized AI-call diagnostic block under a feature test result.

    Shows: provider, model, attempted, latency, response chars, parse/validation
    errors, and a clear state line. Never shows API key or raw payloads."""
    diag = (res or {}).get("diagnostic") or last_ai_call_status(feature_id) or {}
    last_snap = last_ai_call_status(feature_id) or {}
    # Retry fields may live in either `diag` (from _call_and_parse) or the
    # in-memory snapshot (carried through by _record_from_diag).
    retry_attempted = bool(diag.get("retry_attempted") or last_snap.get("retry_attempted"))
    retry_result    = diag.get("retry_result") or last_snap.get("retry_result") or ""
    final_result    = diag.get("final_result") or last_snap.get("final_result") or ""
    ok = bool((res or {}).get("ok"))
    fallback = bool((res or {}).get("fallback")) or not ok

    # State line
    if ok:
        if retry_attempted and retry_result == "success":
            state = "Retry succeeded after parse failure — MiniMax response active"
        else:
            state = "MiniMax response active"
    else:
        reason = diag.get("reason") or (res or {}).get("error") or "unknown"
        if retry_attempted and retry_result and retry_result != "success":
            state = "Retry failed after parse failure; deterministic fallback used"
        elif "disabled" in reason.lower():
            state = "Fallback used: AI disabled"
        elif "key" in reason.lower():
            state = "Fallback used: missing API key"
        elif "timeout" in reason.lower():
            state = "Fallback used: provider timeout"
        elif "http" in reason.lower():
            state = f"Fallback used: provider error ({reason})"
        elif "parse" in reason.lower() or "json" in reason.lower():
            state = "Fallback used: JSON parse failed"
        elif "schema" in reason.lower() or "missing" in reason.lower() or "incomplete" in reason.lower():
            state = "Fallback used: schema validation failed"
        elif "empty" in reason.lower():
            state = "Fallback used: empty provider response"
        else:
            state = f"Fallback used: {reason}"
    (st.success if ok else st.info)(state)

    # Compact diagnostic table
    rows = []
    rows.append(("Provider", diag.get("provider") or last_snap.get("provider") or "—"))
    rows.append(("Model",    diag.get("model") or last_snap.get("model") or "—"))
    rows.append(("Attempted", "yes" if diag.get("attempted") else "no"))
    rows.append(("Result",    "success" if ok else ("fallback" if fallback else "failed")))
    if diag.get("latency_ms"):
        rows.append(("Latency", f"{diag['latency_ms']} ms"))
    if diag.get("response_chars"):
        rows.append(("Response chars", str(diag["response_chars"])))
    if diag.get("http_status"):
        rows.append(("HTTP status", str(diag["http_status"])))
    if diag.get("parsed_keys"):
        rows.append(("Parsed keys", ", ".join(diag["parsed_keys"])))
    if diag.get("parse_error"):
        rows.append(("Parse error", str(diag["parse_error"])[:160]))
    if diag.get("validation_error"):
        rows.append(("Validation error", str(diag["validation_error"])[:160]))
    if retry_attempted:
        rows.append(("Retry attempted", "yes"))
        if retry_result:
            rows.append(("Retry result", retry_result))
        rl = diag.get("retry_latency_ms") or last_snap.get("retry_latency_ms")
        if rl:
            rows.append(("Retry latency", f"{rl} ms"))
        rc = diag.get("retry_response_chars") or last_snap.get("retry_response_chars")
        if rc:
            rows.append(("Retry response chars", str(rc)))
        rpe = diag.get("retry_parse_error") or last_snap.get("retry_parse_error")
        if rpe:
            rows.append(("Retry parse error", str(rpe)[:160]))
    if final_result:
        rows.append(("Final result", final_result))
    if diag.get("reason") and not ok:
        rows.append(("Reason", str(diag["reason"])[:200]))

    md = "\n".join(f"- **{k}:** {v}" for k, v in rows)
    st.caption(md)

from utils.styles import inject_styles
st.set_page_config(page_title="Settings · Ledger", page_icon="⚙️", layout="wide")
init_db()
inject_styles()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Settings")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

st.caption(
    "Pick a card below to set up the part of Ledger you need. "
    "Everything else - profiles, budgets, score weights, AI feature "
    "tests - lives behind the Advanced card."
)

# ══════════════════════════════════════════════════════════════════════
# Pass 31 — 4-card Settings landing.
# Replaces the 9-radio "Profiles · Budgets · Scoring · Rules · AI
# Categorization · AI Features · Watch List · Data & Export · About"
# row that opened on Profiles. The 9 content blocks below are unchanged
# — we just gate WHICH section renders via session_state, and default
# to a 4-card landing instead of "Profiles first."
# ══════════════════════════════════════════════════════════════════════
SECTIONS = [
    "Profiles", "Budgets", "Scoring", "Rules",
    "AI Categorization", "AI Features", "Watch List",
    "Data & Export", "About",
]

# Map landing-card clicks → underlying section key.
# Pass 35 Phase 7: friendlier user-facing wording on the landing cards.
# Same destinations and same gating; just less developer-speak.
_LANDING_CARDS = [
    {
        "title":   "🧠 AI Suggestions",
        "purpose": ("Let Ledger suggest categories for transactions it "
                    "isn't sure about. Fully optional - Ledger works "
                    "offline without it."),
        "button":  "Set up AI suggestions",
        "section": "AI Categorization",
    },
    {
        "title":   "📋 Categories & Merchants",
        "purpose": ("Teach Ledger how to categorize specific merchants, "
                    "manage your watch list, and review the rules it "
                    "has learned."),
        "button":  "Manage categories",
        "section": "Rules",
    },
    {
        "title":   "💾 Import & Backup",
        "purpose": ("Set the folder Ledger watches for new statements, "
                    "make a clean share bundle, or send a sanitized "
                    "bug report."),
        "button":  "Open data tools",
        "section": "Data & Export",
    },
    {
        "title":   "⚙️ Advanced",
        "purpose": ("Spending profiles, monthly budget targets, score "
                    "weights, and built-in AI feature tests."),
        "button":  "Show advanced settings",
        "section": "__ADVANCED__",
    },
]

# session_state holds the current selection (None = landing).
_settings_section = st.session_state.get("settings_section")
_settings_mode    = st.session_state.get("settings_mode", "basic")

# Top toolbar: only render when a section is open.
if _settings_section is not None:
    _bk_col, _adv_col, _dev_col = st.columns([1, 1, 6])
    with _bk_col:
        if st.button("← Back to Settings",
                     key="settings_back",
                     use_container_width=True):
            st.session_state["settings_section"] = None
            st.rerun()
    with _adv_col:
        if _settings_section == "__ADVANCED__" or _settings_mode != "basic":
            # Show developer-mode toggle when in Advanced view.
            _new_mode = st.selectbox(
                "Mode",
                ["basic", "advanced", "developer"],
                index=["basic","advanced","developer"].index(_settings_mode),
                label_visibility="collapsed",
                key="settings_mode_picker",
                help=("Basic: only the 4 landing cards. "
                      "Advanced: also Profiles / Budgets / Scoring. "
                      "Developer: also AI Features / About."),
            )
            if _new_mode != _settings_mode:
                st.session_state["settings_mode"] = _new_mode
                st.rerun()

# ── Render the LANDING (4 cards) when no section is selected ─────────
if _settings_section is None:
    st.divider()
    _l, _r = st.columns(2, gap="medium")
    for i, card in enumerate(_LANDING_CARDS):
        col = _l if i % 2 == 0 else _r
        with col:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.02);"
                f"border:1px solid rgba(255,255,255,0.08);"
                f"border-left:3px solid #4f86c6;border-radius:8px;"
                f"padding:14px 16px;margin-bottom:10px;height:100%'>"
                f"<div style='font-size:1.0rem;font-weight:700;"
                f"color:#e6edf3;margin-bottom:6px'>{card['title']}</div>"
                f"<div style='font-size:0.86rem;color:#c9d1d9;"
                f"line-height:1.5;margin-bottom:10px'>"
                f"{card['purpose']}</div></div>",
                unsafe_allow_html=True,
            )
            if st.button(card["button"], key=f"settings_card_{i}",
                         use_container_width=True):
                if card["section"] == "__ADVANCED__":
                    st.session_state["settings_section"] = "Profiles"
                    st.session_state["settings_mode"] = "advanced"
                else:
                    st.session_state["settings_section"] = card["section"]
                    st.session_state["settings_mode"] = "basic"
                st.rerun()
    st.divider()
    st.caption(
        "About this app · localhost-only · data stays on this "
        "computer · no cloud sync. See the Diagnostics page (dev "
        "mode) for environment health."
    )
    conn = get_connection()
    conn.close()
    st.stop()

# ── A section is selected. Render the legacy section radio limited
#    to the chosen mode's allowed sections, with the current section
#    pre-selected. The 9 if/elif content blocks below operate on
#    `section` exactly as before.
_BASIC_SECTIONS    = ["AI Categorization", "Rules", "Watch List", "Data & Export"]
_ADVANCED_SECTIONS = _BASIC_SECTIONS + ["Profiles", "Budgets", "Scoring"]
_DEVELOPER_SECTIONS = SECTIONS  # All

if _settings_mode == "developer":
    _visible_sections = _DEVELOPER_SECTIONS
elif _settings_mode == "advanced":
    _visible_sections = _ADVANCED_SECTIONS
else:
    _visible_sections = _BASIC_SECTIONS

if _settings_section not in _visible_sections:
    # User came from a card straight into a single-section view; render
    # only that section without the radio chooser.
    section = _settings_section
else:
    _idx = (_visible_sections.index(_settings_section)
            if _settings_section in _visible_sections else 0)
    section = st.radio(
        "Section",
        _visible_sections,
        index=_idx,
        horizontal=True,
        label_visibility="collapsed",
        key="settings_section_radio_p31",
    )
    if section != _settings_section:
        st.session_state["settings_section"] = section
        st.rerun()

conn = get_connection()

# ═══════════════════════════════════════════════════════════════════════
# 1. PROFILES
# ═══════════════════════════════════════════════════════════════════════
if section == "Profiles":
    st.subheader("Spending Profiles")
    st.markdown(
        "Profiles let you switch between different budget sets without losing data. "
        "Common presets: **Normal Month**, **Tight Month**, **Vacation**, **No-Spend**."
    )

    profiles = get_profiles(conn=conn)
    active   = get_active_profile(conn=conn)

    # ── Active profile banner ──────────────────────────────────────────
    if active:
        st.success(f"Active profile: **{active['name']}** — {active.get('description','')}")
    else:
        st.info("No profile active — using base budgets from the Budgets section.")

    # ── Existing profiles ─────────────────────────────────────────────
    if profiles:
        st.markdown("#### Your Profiles")
        for p in profiles:
            is_active = bool(p.get("is_active"))
            tag = " ✓ Active" if is_active else ""
            with st.expander(f"{p['name']}{tag}"):
                st.caption(p.get("description") or "No description")
                if p.get("notes"):
                    st.caption(f"Notes: {p['notes']}")

                # Show profile budgets
                try:
                    pbud = json.loads(p.get("budgets_json") or "{}")
                except Exception:
                    pbud = {}
                if pbud:
                    st.markdown("**Budget overrides in this profile:**")
                    for cat, amt in sorted(pbud.items()):
                        st.write(f"- {cat}: ${amt:,.2f}/month")

                pc1, pc2, pc3 = st.columns(3)
                if not is_active:
                    if pc1.button("Activate", key=f"activate_{p['name']}"):
                        set_active_profile(p["name"], conn=conn)
                        conn.commit()
                        st.success(f"Profile '{p['name']}' activated.")
                        st.rerun()

                if pc2.button("Clone", key=f"clone_{p['name']}"):
                    new_name = p["name"] + " (copy)"
                    upsert_profile(new_name, p.get("description","") + " (clone)",
                                   p.get("budgets_json","{}"), conn=conn)
                    conn.commit()
                    st.success(f"Cloned as '{new_name}'")
                    st.rerun()

                if pc3.button("Delete", key=f"del_profile_{p['name']}", type="secondary"):
                    if not is_active:
                        delete_profile(p["name"], conn=conn)
                        conn.commit()
                        st.success(f"Deleted '{p['name']}'")
                        st.rerun()
                    else:
                        st.warning("Deactivate before deleting.")

        if active:
            if st.button("Deactivate current profile (use base budgets)"):
                conn.execute("UPDATE profiles SET is_active=0")
                conn.commit()
                st.success("Profile deactivated.")
                st.rerun()

    # ── Create new profile ────────────────────────────────────────────
    st.markdown("#### Create New Profile")
    with st.form("new_profile_form"):
        pf1, pf2 = st.columns(2)
        p_name = pf1.text_input("Profile name", placeholder="e.g. Tight Month")
        p_desc = pf2.text_input("Description", placeholder="e.g. Strict spending month")
        p_notes = st.text_area("Notes (optional)", height=60)

        st.markdown("**Budget overrides** (leave blank to inherit base budgets)")
        budget_override_cats = st.multiselect("Categories to override", sorted(CATEGORIES))
        override_vals = {}
        if budget_override_cats:
            for cat in budget_override_cats:
                val = st.number_input(f"{cat} budget", min_value=0.0, step=10.0, key=f"pov_{cat}")
                if val > 0:
                    override_vals[cat] = val

        if st.form_submit_button("Create Profile"):
            if p_name:
                upsert_profile(p_name, p_desc, json.dumps(override_vals), p_notes, conn=conn)
                conn.commit()
                st.success(f"Profile '{p_name}' created.")
                st.rerun()
            else:
                st.error("Profile name is required.")

    # ── Preset loader ─────────────────────────────────────────────────
    st.markdown("#### Load Preset")
    PRESETS = {
        "Normal Month":  {"description": "Standard monthly budget", "budgets_json": "{}"},
        "Tight Month":   {"description": "Trim Food & Convenience, Shopping, Subscriptions",
                          "budgets_json": json.dumps({
                              "Food & Convenience": 150,
                              "Shopping": 100,
                              "Subscriptions & Digital": 40,
                          })},
        "No-Spend Month":{"description": "Essentials only — cut discretionary to zero",
                          "budgets_json": json.dumps({
                              "Food & Convenience": 0,
                              "Shopping": 0,
                              "Subscriptions & Digital": 0,
                          })},
        "Travel Month":  {"description": "Higher food/shopping allowance while travelling",
                          "budgets_json": json.dumps({
                              "Food & Convenience": 500,
                              "Gas / Transport": 250,
                              "Shopping": 200,
                          })},
    }
    existing_names = {p["name"] for p in profiles}
    preset_choice = st.selectbox("Preset", list(PRESETS.keys()))
    if st.button("Load Preset"):
        pdata = PRESETS[preset_choice]
        name = preset_choice
        if name in existing_names:
            name = preset_choice + " (preset)"
        upsert_profile(name, pdata["description"], pdata["budgets_json"], conn=conn)
        conn.commit()
        st.success(f"Preset '{name}' added.")
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# 2. BUDGETS
# ═══════════════════════════════════════════════════════════════════════
elif section == "Budgets":
    st.subheader("Monthly Budget Targets")
    st.caption(
        "Budgets apply to **spending categories only**. Income, transfers, "
        "rewards, refunds, savings, investments, and credit-card payments "
        "are excluded — those aren't consumption."
    )
    st.info(
        "💡 **Two surfaces, two purposes.** These flat budget targets "
        "are simple recurring per-category limits and feed the "
        "Recommendations engine. The **Month Plan** page (🗓 in the "
        "sidebar) generates plan-specific category targets bent by a "
        "mode (Tight, Aggressive Save, Sub Cleanup…) and tracks "
        "forecast risk, safe-to-spend, and goals. Pick whichever fits — "
        "you don't need both."
    )

    budgets = get_budgets(conn=conn)

    # Pass 17: detect any pre-existing budget rows that landed in system
    # categories (e.g. someone mistakenly budgeted "Income" before the
    # taxonomy split) and show a one-click cleanup so the budget UI matches
    # the new taxonomy without silently deleting user data.
    legacy_system_budgets = [c for c in budgets.keys() if c in SYSTEM_CATEGORIES]
    if legacy_system_budgets:
        st.warning(
            "Found budgets on system / accounting categories that shouldn't "
            "be budgeted: **" + ", ".join(legacy_system_budgets) + "**. "
            "These are cashflow plumbing (income, transfers, CC payments) "
            "and never count as consumption."
        )
        if st.button("🗑 Remove these from budgets",
                     type="secondary",
                     key="cleanup_legacy_budgets"):
            for c in legacy_system_budgets:
                delete_budget(c, conn=conn)
            conn.commit()
            st.success(f"Removed {len(legacy_system_budgets)} non-spending budget(s).")
            st.rerun()

    if budgets:
        st.markdown("#### Current Budgets")
        for cat, amt in sorted(budgets.items()):
            bc1, bc2, bc3 = st.columns([3, 2, 1])
            bc1.write(f"**{cat}**")
            new_val = bc2.number_input(
                "", min_value=0.0, value=float(amt), step=10.0,
                key=f"budg_{cat}", label_visibility="collapsed"
            )
            if bc3.button("Save", key=f"savebud_{cat}"):
                upsert_budget(cat, new_val, conn=conn)
                conn.commit()
                st.success(f"Updated {cat}: ${new_val:,.2f}")
                st.rerun()
    else:
        st.info("No budget targets set yet.")

    st.divider()
    st.markdown("#### Add / Update Budget")
    # Pass 17: dropdown is restricted to BUDGETABLE_CATEGORIES — system
    # categories (Income, Transfers, CC Payment, Rewards, etc.) are
    # excluded so the user can't accidentally set a budget on a category
    # that doesn't represent consumption.
    with st.form("budget_form"):
        bc1, bc2, bc3 = st.columns(3)
        cat_sel   = bc1.selectbox(
            "Category",
            sorted(BUDGETABLE_CATEGORIES),
            help="Only spending categories are listed. System categories "
                 "(Income, Transfers, CC Payment, etc.) are intentionally "
                 "hidden — budgeting against them would be meaningless.",
        )
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

    st.divider()
    st.markdown("#### Delete a Budget")
    if budgets:
        del_cat = st.selectbox("Select to delete", sorted(budgets.keys()), key="del_bud_sel")
        if st.button("Delete budget", type="secondary"):
            delete_budget(del_cat, conn=conn)
            conn.commit()
            st.success(f"Removed budget for {del_cat}")
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# 3. SCORING
# ═══════════════════════════════════════════════════════════════════════
elif section == "Scoring":
    st.subheader("Score Weights")
    st.markdown(
        "Customize how the 0–100 Money Pulse score is calculated. "
        "Weights must sum to 100. Adjust to reflect what matters most to you."
    )

    sw = get_score_weights(conn=conn)
    savings_w     = float(sw.get("savings_weight", 40))
    diversity_w   = float(sw.get("diversity_weight", 30))
    debt_w        = float(sw.get("debt_weight", 15))
    consistency_w = float(sw.get("consistency_weight", 15))

    sc1, sc2, sc3, sc4 = st.columns(4)
    new_savings     = sc1.slider(
        "Savings rate", 0, 60, int(savings_w), 5,
        help="Net cashflow / savings rate. Higher = full credit at >= 20%.",
    )
    new_diversity   = sc2.slider(
        "Spending control", 0, 40, int(diversity_w), 5,
        help=("Concentration of CONTROLLABLE spend (excludes fixed "
              "Housing/Mortgage, Utilities/Bills, Insurance, etc.). "
              "Lower concentration = full credit."),
    )
    new_debt        = sc3.slider(
        "Debt / fees", 0, 50, int(debt_w), 5,
        help=("Exact interest + fees from saved Mastercard statement "
              "summaries only. Transaction rows are not used for this "
              "score. Cash-advance principal is not counted."),
    )
    new_consistency = sc4.slider(
        "Month consistency", 0, 50, int(consistency_w), 5,
        help="Positive cashflow in recent complete months.",
    )

    total_w = new_savings + new_diversity + new_debt + new_consistency
    if total_w != 100:
        st.warning(f"Weights sum to {total_w} — should equal 100. Adjust sliders before saving.")
    else:
        st.success("Weights sum to 100 ✓")

    if st.button("Save Score Weights", type="primary", disabled=(total_w != 100)):
        save_score_weights(new_savings, new_diversity, new_debt, new_consistency, conn=conn)
        conn.commit()
        st.success("Score weights saved. Dashboard will reflect on next load.")
        st.rerun()

    if st.button("Reset to defaults"):
        save_score_weights(40, 30, 15, 15, conn=conn)
        conn.commit()
        st.success("Reset to 40 / 30 / 15 / 15 (Money Pulse defaults).")
        st.rerun()

    st.divider()
    st.markdown("#### Score Dimension Guide")
    st.markdown("""
| Dimension | What it measures | Max (default) |
|-----------|-----------------|---------------|
| Savings rate     | % of income kept each month. Full credit at >= 20%. | 40 |
| Spending control | Controllable spending concentration plus trend vs the prior complete month. Housing/Mortgage, Utilities/Bills, Insurance, transfers, card payments, and finance charges are excluded. | 30 |
| Debt / fees      | Exact interest + fees from saved Mastercard statement summaries only. Cash-advance principal is NOT scored. | 15 |
| Month consistency| # of recent COMPLETE months with positive net cashflow. Partial months are excluded. | 15 |
    """)

# ═══════════════════════════════════════════════════════════════════════
# 4. RULES
# ═══════════════════════════════════════════════════════════════════════
elif section == "Rules":
    st.subheader("Merchant → Category Rules")
    st.caption(
        "Use the Rule Tester below to check how a description gets "
        "categorized, and the Learned Rules list to see (or remove) "
        "the merchant→category pairs you've taught Ledger from the "
        "Review page. The raw `config/rules.py` editor lives behind "
        "Developer mode."
    )

    # Pass 32: the giant rules.py text editor is a power-user tool.
    # Hide it under developer mode so the Basic Rules section stays
    # focused on Rule Tester + Learned Rules.
    if _settings_mode == "developer":
        st.markdown("""
        Edit `config/rules.py` to add or change merchant-to-category mappings.
        After saving, click **Re-run Categorization** to apply to all existing transactions.

        **Rule format:**
        ```python
        RULES = [
            # (pattern, category, subcategory, is_recurring)
            ("NESTO", "Housing / Mortgage", "Mortgage", True),
            ("NETFLIX", "Subscriptions & Digital", "Streaming", True),
            ("AMAZON", "Shopping", "Online", False),
        ]
        ```
        `pattern` is matched case-insensitively as a substring of the raw description.
        Use canonical category names from `config/categories.py` — mismatched names
        will be silently ignored by insights and recommendations.
        """)

        rules_path = Path(__file__).parent.parent / "config" / "rules.py"
        try:
            rules_text = rules_path.read_text()
            edited = st.text_area("rules.py", value=rules_text, height=400)

            st.caption(
                "**Self-transfer names:** The rules near the top contain "
                "`INTERAC e-Transfer To/From: Benjamin Thompson`. "
                "Update these two lines to match the account holder's name exactly "
                "as it appears in bank statements, so own-name transfers are excluded from spending."
            )

            if st.button("Save rules.py"):
                # ── Syntax check ────────────────────────────────────────────────
                try:
                    code = compile(edited, "rules.py", "exec")
                except SyntaxError as e:
                    st.error(f"Syntax error — file NOT saved: {e}")
                    st.stop()

                # ── Structural + category validation ────────────────────────────
                try:
                    ns: dict = {}
                    exec(code, ns)  # noqa: S102
                    rules_list = ns.get("RULES")
                    if not isinstance(rules_list, (list, tuple)):
                        st.error("Validation failed: `RULES` must be a list. File NOT saved.")
                        st.stop()

                    valid_cats = set(CATEGORIES) | {
                        "Transfer", "Transfer In", "Transfer Out",
                        "Credit Card Payment", "Payment", "Cancelled",
                        "Savings", "Cash Advance", "Fees / Interest",
                        "Housing / Mortgage", "Income", "Uncategorized", None,
                    }
                    bad_cats = []
                    for rule in rules_list:
                        cat = rule[1] if isinstance(rule, (list, tuple)) and len(rule) >= 2 else rule.get("category") if isinstance(rule, dict) else None
                        if cat is not None and cat not in valid_cats:
                            bad_cats.append(cat)
                    if bad_cats:
                        st.warning(
                            f"Unknown category name(s) — file saved anyway, but these won't match "
                            f"the spending charts: **{', '.join(sorted(set(bad_cats)))}**"
                        )
                except Exception as e:
                    st.error(f"Rule validation error — file NOT saved: {e}")
                    st.stop()

                rules_path.write_text(edited)
                st.success("Saved. Click Re-run Categorization to apply.")
        except Exception as e:
            st.warning(f"Could not read rules.py: {e}")

    st.divider()
    if st.button("Re-run Categorization on All Transactions", type="primary"):
        conn2 = get_connection()
        n = rerun_categorization(conn=conn2)
        conn2.commit()
        conn2.close()
        st.success(f"Updated categories on {n} transactions.")

    st.divider()
    st.subheader("Rule Tester")
    st.caption("Test how a raw description would be categorized with current rules.")
    test_desc = st.text_input("Raw description to test", placeholder="e.g. NETFLIX.COM 12.99")
    if test_desc:
        try:
            from utils.categorizer import categorize, normalize_merchant
            cat, sub, conf = categorize(test_desc, 0.0, "debit")
            merchant = normalize_merchant(test_desc)
            st.success(f"**Merchant:** {merchant}  |  **Category:** {cat}  |  **Subcategory:** {sub or '—'}  |  **Confidence:** {conf:.0%}")
        except Exception as e:
            st.error(f"Error: {e}")

    # ── Learned rules (user corrections promoted from Review) ──────────
    st.divider()
    st.subheader("Learned Rules")
    st.caption(
        "Merchant → category mappings you've taught Ledger from the Review page. "
        "These take priority over the static `rules.py`."
    )
    learned = list_learned_rules(conn=conn)
    if learned:
        for lr in learned:
            lc1, lc2, lc3, lc4, lc5 = st.columns([3, 2, 1.2, 1.2, 1])
            lc1.write(f"**{lr['merchant_normalized']}**")
            lc2.write(lr["category"])
            lc3.caption(f"hits: {lr.get('hit_count') or 0}")
            lc4.caption(lr.get("source") or "user")
            if lc5.button("Remove", key=f"rm_learned_{lr['id']}"):
                delete_learned_rule(lr["merchant_normalized"], conn=conn)
                conn.commit()
                st.rerun()
    else:
        st.info("No learned rules yet. When you correct a flagged transaction in Review, you'll see an option to teach Ledger that merchant→category pair.")

# ═══════════════════════════════════════════════════════════════════════
# 5. AI CATEGORIZATION
# ═══════════════════════════════════════════════════════════════════════
elif section == "AI Categorization":
    st.subheader("AI Categorization")
    ai_settings = get_ai_settings()
    status = provider_status()

    pill_col1, pill_col2 = st.columns([4, 1])
    with pill_col1:
        st.markdown(
            "Uses an LLM to **suggest** a category for transactions the rule-based "
            "categorizer couldn't confidently label. Suggestions are never applied "
            "automatically — you accept or reject each one from the Review page. "
            "Keyword rules and your learned rules always run first."
        )
    with pill_col2:
        if status["ready"]:
            st.success("AI ready")
        elif status["enabled"]:
            st.warning("AI enabled · not ready")
        else:
            st.info("AI disabled")

    if not status["ready"] and status["enabled"] and status.get("reason"):
        st.caption(f"⚠ {status['reason']}")

    st.divider()

    # ── Provider / model / key form ────────────────────────────────────
    provider_labels = {
        "minimax":   "MiniMax (default · M2.7)",
        "anthropic": "Anthropic (Claude)",
        "openai":    "OpenAI (GPT)",
    }
    current_provider = ai_settings.get("provider", "minimax")

    with st.form("ai_config_form"):
        c1, c2 = st.columns([1, 1])
        with c1:
            provider_sel = st.selectbox(
                "Provider",
                SUPPORTED_PROVIDERS,
                index=list(SUPPORTED_PROVIDERS).index(current_provider)
                      if current_provider in SUPPORTED_PROVIDERS else 0,
                format_func=lambda p: provider_labels.get(p, p),
                help="Choose any supported provider. MiniMax is default and is the lowest-cost option.",
            )
            # Note: model field autoupdates when provider changes only after save
            model_default = ai_settings.get("model") or DEFAULT_MODELS.get(provider_sel, "")
            model_input = st.text_input(
                "Model",
                value=model_default,
                help="Exact model identifier. MiniMax: `MiniMax-M2.7`. Anthropic: `claude-haiku-4-5-20251001`. OpenAI: `gpt-4o-mini`.",
            )
        with c2:
            base_default = ai_settings.get("base_url") or (DEFAULT_BASE_URLS.get(provider_sel) or "")
            base_input = st.text_input(
                "Base URL (optional override)",
                value=base_default or "",
                help="Leave as-is unless using a self-hosted or regional endpoint.",
                placeholder=DEFAULT_BASE_URLS.get(provider_sel) or "",
            )
            key_preview = redacted_settings().get("api_key_preview", "")
            new_key = st.text_input(
                "API key",
                type="password",
                placeholder=("current key: " + key_preview) if key_preview else "sk-... / your provider key",
                help="Stored only on this server in `config.json`. Never included in transaction or settings exports.",
            )

        enabled_new = st.checkbox(
            "Enable AI categorization",
            value=bool(ai_settings.get("enabled")),
            help="When enabled, the Review page offers a 'Suggest category' action for uncategorized and low-confidence rows.",
        )

        save_col, clear_col = st.columns([1, 1])
        save_clicked   = save_col.form_submit_button("Save settings", type="primary")
        clear_clicked  = clear_col.form_submit_button("Clear API key + disable")

    if save_clicked:
        update_kwargs = {
            "enabled":  enabled_new,
            "provider": provider_sel,
            "model":    model_input.strip() or DEFAULT_MODELS.get(provider_sel, ""),
            "base_url": base_input.strip() or None,
        }
        if new_key.strip():
            update_kwargs["api_key"] = new_key.strip()
        update_ai_settings(**update_kwargs)
        st.success("AI settings saved.")
        st.rerun()

    if clear_clicked:
        clear_api_key()
        st.success("API key cleared and AI disabled.")
        st.rerun()

    st.caption(
        "The key is stored only on this server (`config.json`) — never sent to the browser, "
        "never exported with your data, never committed to git."
    )

    # ── Test call ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("**Test the connection** — sends one example transaction to the provider.")
    if st.button("Test with a sample transaction", disabled=not status["ready"]):
        sample = {
            "raw_description": "NETFLIX.COM 888-638-3549 ON",
            "merchant": "Netflix",
            "amount": 16.99,
            "direction": "debit",
            "account_type": "mastercard",
        }
        with st.spinner(f"Calling {status['provider']}/{status['model']}..."):
            result = suggest_for_transaction(sample, timeout=25.0)
        from datetime import datetime
        st.session_state["ai_last_test"] = {
            "ok":       result is not None,
            "when":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "provider": status["provider"],
            "model":    status["model"],
            "result":   result,
        }

    last = st.session_state.get("ai_last_test")
    if last:
        badge = "✅ success" if last["ok"] else "❌ failed"
        st.caption(
            f"**Last test:** {badge} · {last['provider']} / {last['model']} · {last['when']}"
        )
        if last["ok"]:
            r = last["result"]
            st.success(
                f"Suggested: **{r['category']}**"
                + (f" / {r['subcategory']}" if r['subcategory'] else "")
                + f"  · confidence {r['confidence']:.0%}"
                + (f"  · _{r['rationale']}_" if r['rationale'] else "")
            )
        else:
            st.error(
                "No suggestion returned. Check your key, network, and model name. "
                "Ledger never auto-applies failed suggestions."
            )

# ═══════════════════════════════════════════════════════════════════════
# 5b. AI FEATURES — visible map of where MiniMax is used across Ledger
# ═══════════════════════════════════════════════════════════════════════
elif section == "AI Features":
    st.subheader("AI Features Map")
    st.markdown(
        "Every place MiniMax is used in Ledger. Each feature has a deterministic "
        "fallback so Ledger keeps working with AI disabled. Use the test buttons "
        "to confirm each surface is responding with your configured provider."
    )

    features = ai_features_status()
    any_ready = any(f.get("ready") for f in features)
    if any_ready:
        st.success(
            f"✅ Provider ready: **{features[0].get('provider')} / {features[0].get('model')}**"
        )
    else:
        st.warning(
            f"AI is currently off or not ready: {features[0].get('reason') or '—'}. "
            "Every feature below runs in deterministic fallback mode."
        )

    # ── Top-level generic health check ─────────────────────────────────
    st.markdown("#### Generic AI Health Check")
    st.caption(
        "Sends a tiny `{ok, echo}` schema to the configured provider. Confirms the "
        "full call → strip-thinking → balanced-JSON-extract → validate path actually "
        "works end-to-end. Categorization and per-feature explainers use the same "
        "provider but different prompts and schemas."
    )
    if st.button("Run AI Health Check", key="run_health_check",
                 disabled=not any_ready):
        with st.spinner("Calling MiniMax with a 10-token health prompt…"):
            hc = ai_health_check()
        _render_diag("ai_health_check", hc)
        if hc.get("ok"):
            st.caption(f"Echo received: `{hc.get('echo','')}`")
    elif not any_ready:
        st.caption("Enable AI and provide a key in **AI Categorization** to run the health check.")
    st.divider()

    for feat in features:
        with st.expander(f"**{feat['name']}**  ·  {feat['location']}"):
            st.markdown(f"**Purpose:** {feat['purpose']}")
            st.markdown(f"**Fallback when AI is off:** {feat['fallback']}")
            st.caption(f"Status: {'✅ ready' if feat.get('ready') else '⚠ ' + (feat.get('reason') or 'off')}")

            fid = feat["id"]

            if fid == "dashboard_copilot":
                if st.button("Test Dashboard Copilot", key=f"test_{fid}"):
                    with st.spinner("Testing…"):
                        conn_t = get_connection()
                        res = dashboard_copilot(conn_t)
                        conn_t.close()
                    _render_diag(fid, res)
                    st.write(f"**Headline:** {res.get('headline','')}")
                    st.write(f"**Summary:** {res.get('summary','')}")
                    if res.get("moves"):
                        st.write("**Moves:**")
                        for m in res["moves"]:
                            st.write(f"- {m}")

            elif fid == "review_triage":
                if st.button("Test Review Triage", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.database import get_flagged_transactions, get_ai_candidates
                    flagged = get_flagged_transactions(conn=conn_t)
                    cands = get_ai_candidates(limit=100, conn=conn_t)
                    with st.spinner("Testing…"):
                        res = review_triage_summary(flagged, len(cands))
                    conn_t.close()
                    _render_diag(fid, res)
                    st.write(f"**Headline:** {res.get('headline','')}")
                    st.write(f"**Summary:** {res.get('summary','')}")
                    if res.get("clean_first"):
                        for m in res["clean_first"]:
                            st.write(f"- {m}")

            elif fid == "ask_ledger":
                test_q = st.text_input("Question to try",
                                       value="Why is my score where it is?",
                                       key=f"askq_{fid}")
                if st.button("Test Ask Ledger", key=f"test_{fid}"):
                    conn_t = get_connection()
                    with st.spinner("Testing…"):
                        res = ask_ledger(test_q, conn_t)
                    conn_t.close()
                    _render_diag(fid, res)
                    st.write(f"**Skill routed:** `{res.get('skill','—')}`")
                    st.write(f"**Answer:** {res.get('answer','')}")
                    if res.get("bullets"):
                        for b in res["bullets"]:
                            st.write(f"- {b}")

            elif fid == "recommendation_explainer":
                if st.button("Test on top recommendation", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.insights import compute_recommendations
                    recs = compute_recommendations(conn=conn_t)
                    if not recs:
                        st.info("No recommendations yet — import more data first.")
                    else:
                        with st.spinner("Testing…"):
                            res = explain_recommendation(recs[0])
                        _render_diag(fid, res)
                        st.write(f"**Rec:** {recs[0].get('title','')}")
                        st.write(f"**Nature:** {res.get('nature','')}")
                        st.write(f"**Why it matters:** {res.get('why_it_matters','')}")
                        st.write(f"**Action:** {res.get('action','')}")
                    conn_t.close()

            elif fid == "mission_framing":
                if st.button("Test Mission framing", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.momentum import choose_mission
                    from utils.ai_explainer import mission_framing as _mf
                    m = choose_mission(conn=conn_t)
                    with st.spinner("Testing…"):
                        res = _mf(m, m.get("streaks") or {})
                    conn_t.close()
                    _render_diag(fid, res)
                    st.write(f"**Mission:** {m.get('title','')}")
                    st.write(f"**Framing line:** {res.get('line','')}")

            elif fid == "ai_categorizer":
                st.caption("Tested from **AI Categorization** section's 'Test with a sample transaction'.")

            elif fid == "weekly_review":
                if st.button("Test Weekly Review", key=f"test_{fid}"):
                    conn_t = get_connection()
                    with st.spinner("Testing…"):
                        res = weekly_review(conn_t)
                    conn_t.close()
                    _render_diag(fid, res)
                    st.write(f"**Headline:** {res.get('headline','')}")
                    st.write(f"**Focus:** {res.get('focus','')}")
                    for item in res.get("checklist") or []:
                        st.write(f"- {item}")

            elif fid == "mission_engine":
                st.caption("Mission Engine v2 is deterministic-only. AI paraphrasing is exposed via the **This Month's Mission** test (mission_framing).")
                if st.button("Show ranked mission options", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.momentum import mission_options
                    opts = mission_options(conn=conn_t, limit=3)
                    conn_t.close()
                    st.write(f"**{len(opts)} mission option(s):**")
                    for m in opts:
                        st.write(f"- **{m['title']}** ({m['difficulty']}) — {m['next_action']}")

            elif fid == "scenario_simulator":
                if st.button("Test Scenario Simulator", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.insights import scenario_simulate, top_controllable_categories
                    cats = top_controllable_categories(conn=conn_t, limit=1)
                    if not cats:
                        st.info("No controllable categories found yet — import more data.")
                    else:
                        sim = scenario_simulate(
                            {"category_cuts": {cats[0]["category"]: 0.20}}, conn=conn_t
                        )
                        with st.spinner("Explaining…"):
                            exp = explain_scenario(sim)
                        _render_diag(fid, exp)
                        if sim["baseline"]:
                            st.write(f"**Baseline:** {sim['baseline']['month']} · "
                                     f"spending ${sim['baseline']['spending']:,.0f}")
                        if sim["projected"]:
                            st.write(f"**Projected savings:** "
                                     f"${sim['projected']['delta_savings']:,.0f}/mo → "
                                     f"{sim['projected']['savings_rate']:.1f}% savings rate")
                        st.write(f"**Summary:** {exp.get('summary','')}")
                    conn_t.close()

            elif fid == "subscription_detective":
                st.caption("Subscription Detective is deterministic-only — runs without AI.")
                if st.button("Run Subscription Detective", key=f"test_{fid}"):
                    conn_t = get_connection()
                    from utils.insights import subscription_detective
                    det = subscription_detective(conn=conn_t)
                    conn_t.close()
                    st.success(f"✅ Deterministic — {det['count']} subscription(s) detected.")
                    st.write(f"**Monthly estimate:** ${det['monthly_estimate']:,.0f}  ·  "
                             f"**Annual:** ${det['annual_total']:,.0f}")
                    if det["candidates"]:
                        st.write(f"**{len(det['candidates'])} candidate(s) to review:**")
                        for c in det["candidates"]:
                            st.write(f"- {c['merchant']} · ~${c['avg_amount']:,.0f}/mo · "
                                     f"flags: {', '.join(c['flags']) or 'n/a'}")

            # Pass 24: planning-loop coaches link out to where they
            # actually render. Inline test buttons here would create a
            # second, parallel AI invocation path; instead we send the
            # tester to the real surface and surface the call status.
            elif fid in ("explain_month_plan", "explain_forecast", "coach_goals"):
                target_tab = {
                    "explain_month_plan": "Plan",
                    "explain_forecast":   "Forecast",
                    "coach_goals":        "Goals",
                }[fid]
                from utils.ai_explainer import last_ai_call_status
                last = last_ai_call_status(fid) or {}
                st.caption(
                    f"Test from the **Month Plan → {target_tab}** tab. "
                    "The coach panel renders the deterministic copy "
                    "immediately; click ✨ Generate AI summary to "
                    "exercise the AI path."
                )
                if last:
                    ok = last.get("ok")
                    fb = last.get("fallback")
                    badge = ("✅ ok" if ok and not fb
                             else ("⚠ fallback" if fb else "—"))
                    st.caption(
                        f"Last call: **{badge}**  ·  "
                        f"reason: {(last.get('reason') or '—')[:80]}  ·  "
                        f"at {last.get('at') or '—'}"
                    )
                else:
                    st.caption(
                        "No call recorded yet this session. Open the "
                        "Month Plan page to exercise this surface."
                    )
                if st.button(f"Open Month Plan → {target_tab}",
                             key=f"test_{fid}"):
                    st.switch_page("pages/12_Month_Plan.py")

    st.divider()
    st.caption(
        "All AI calls use the evidence packet you can see in the test output. "
        "No internet browsing, no transaction bodies other than what's shown here, "
        "no chain-of-thought. When AI is off, the deterministic fallback is always active."
    )

# ═══════════════════════════════════════════════════════════════════════
# 6. WATCH LIST
# ═══════════════════════════════════════════════════════════════════════
elif section == "Watch List":
    st.subheader("Merchant Watch List")
    st.markdown(
        "Merchants on this list are highlighted in the Transactions page and "
        "considered in Recommendations for price-change and subscription audits."
    )

    watch = get_watch_list(conn=conn)

    if watch:
        st.markdown("#### Current Watch List")
        for w in watch:
            wc1, wc2, wc3 = st.columns([3, 4, 1])
            wc1.write(f"**{w['merchant']}**")
            wc2.caption(w.get("reason") or "No reason set")
            if wc3.button("Remove", key=f"rm_watch_{w['merchant']}"):
                remove_from_watch_list(w["merchant"], conn=conn)
                conn.commit()
                st.success(f"Removed {w['merchant']}")
                st.rerun()
    else:
        st.info("No merchants on watch list.")

    st.divider()
    st.markdown("#### Add Merchant")
    with st.form("add_watch_form"):
        wa1, wa2 = st.columns(2)

        # Prefill from session if coming from Transactions page
        default_merchant = st.session_state.get("watch_add_merchant", "")
        new_watch = wa1.text_input("Merchant name", value=default_merchant)
        watch_reason = wa2.text_input("Reason (optional)", placeholder="e.g. Price increasing")
        if st.form_submit_button("Add to Watch List"):
            if new_watch:
                add_to_watch_list(new_watch, watch_reason, conn=conn)
                conn.commit()
                st.session_state.pop("watch_add_merchant", None)
                st.success(f"Added {new_watch} to watch list.")
                st.rerun()
            else:
                st.error("Enter a merchant name.")

    # Quick-add from recurring merchants
    st.divider()
    st.markdown("#### Quick-Add from Recurring Merchants")
    from utils.insights import recurring_merchants
    rec = recurring_merchants(min_months=3, conn=conn)
    watch_names = {w["merchant"] for w in watch}
    not_watched = [m for m in rec if m["merchant"] not in watch_names]

    if not_watched:
        choices = [f"{m['merchant']} (${m['avg_amount']:,.2f}/mo, {m['months_seen']} months)" for m in not_watched[:15]]
        merchant_map = {f"{m['merchant']} (${m['avg_amount']:,.2f}/mo, {m['months_seen']} months)": m["merchant"] for m in not_watched[:15]}
        sel = st.selectbox("Recurring merchant to watch", choices)
        if st.button("Add selected"):
            add_to_watch_list(merchant_map[sel], "Recurring — added via quick-add", conn=conn)
            conn.commit()
            st.success(f"Added {merchant_map[sel]}")
            st.rerun()
    else:
        st.info("All recurring merchants are already on your watch list.")

# ═══════════════════════════════════════════════════════════════════════
# 6. DATA & EXPORT
# ═══════════════════════════════════════════════════════════════════════
elif section == "Data & Export":
    # DB stats
    tx_count  = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    inv_count = conn.execute("SELECT COUNT(*) FROM investments").fetchone()[0]
    log_count = conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0]
    budg_count = conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0]
    prof_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    watch_count = conn.execute("SELECT COUNT(*) FROM watch_list").fetchone()[0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Transactions",   tx_count)
    c2.metric("Investments",    inv_count)
    c3.metric("Import batches", log_count)
    c4.metric("Budget targets", budg_count)
    c5.metric("Profiles",       prof_count)
    c6.metric("Watch list",     watch_count)

    db_path = Path(__file__).parent.parent / "data" / "finance.db"
    st.caption(f"Database: `{db_path}`")
    st.caption("**Backup:** copy `data/finance.db` to save all your data.")

    st.divider()

    # ── Pass 20: Data & Sharing Safety ────────────────────────────────
    st.subheader("Data & Sharing Safety")
    st.caption(
        "Ledger runs entirely on this computer and binds Streamlit to "
        "127.0.0.1 by default. Below is a quick check of what's safe "
        "to share and what isn't."
    )

    _root = Path(__file__).parent.parent
    _config = _root / "config.json"
    _ai_configured = False
    _ai_provider = ""
    try:
        if _config.exists():
            _cfg = json.loads(_config.read_text(encoding="utf-8"))
            _ai = (_cfg.get("ai") or {})
            _ai_configured = bool(_ai.get("api_key"))
            _ai_provider = _ai.get("provider") or ""
    except Exception:
        pass

    _is_loopback = True   # config.toml + launcher both bind 127.0.0.1
    _broken_venv_count = sum(
        1 for p in _root.glob(".venv.broken-*") if p.is_dir()
    )

    safety_rows = [
        ("App binds to localhost only",      _is_loopback,
         "127.0.0.1 — not exposed to LAN"),
        ("Database file present",            db_path.exists(),
         f"{db_path}"),
        ("AI key configured",                _ai_configured,
         f"provider: {_ai_provider}" if _ai_configured else "no key set"),
        (".venv broken backups",             _broken_venv_count == 0,
         f"{_broken_venv_count} backup dir(s) — safe to delete"
         if _broken_venv_count else "none"),
    ]
    for label, ok, detail in safety_rows:
        icon = "✅" if ok else "⚠️"
        st.markdown(
            f"<div style='padding:6px 10px;margin:3px 0;"
            f"border:1px solid rgba(255,255,255,0.06);border-radius:6px'>"
            f"<span>{icon}</span> "
            f"<b>{label}</b> "
            f"<span style='color:#8b949e;font-size:0.85rem;margin-left:8px'>"
            f"{detail}</span></div>",
            unsafe_allow_html=True,
        )

    with st.expander("How to share Ledger safely", expanded=False):
        st.markdown(
            "**Use the share-zip script.** Manually zipping the project "
            "folder will leak `.venv/`, `data/finance.db`, "
            "`config.json`, and `launcher.log`. The script defends "
            "against that.\n\n"
            "```\n"
            "# user mode (default) — clean share for friends/users\n"
            "python -m scripts.make_share_zip\n\n"
            "# dev mode — also includes CLAUDE_HANDOFF.md\n"
            "python -m scripts.make_share_zip --include-dev-notes\n"
            "```\n"
            "User mode **always excludes**:\n"
            "- `.venv/` and `.venv.broken-*` directories\n"
            "- `__pycache__/`, `.pytest_cache/`, `*.pyc`\n"
            "- `config.json` (your API key)\n"
            "- `data/finance.db` (your transactions)\n"
            "- `launcher.log` and any `.env` files\n"
            "- `exports/` and `dist/`\n"
            "- `CLAUDE_HANDOFF.md` and other developer notes\n\n"
            "Every file the script *would* include is also scanned for "
            "API-key patterns (`sk-…`, `Bearer …`, `api_key=…`). "
            "If anything matches, the build aborts.\n\n"
            "**If you ever shared the project folder unzipped** (or "
            "shared a zip that contained `config.json`), rotate the AI "
            "API key right away — the key was readable inside that "
            "folder."
        )

    with st.expander("OpenClaw / agent context export", expanded=False):
        st.markdown(
            "**Export Ledger's read-only state for an external agent:**\n"
            "```\n"
            "python -m scripts.export_agent_context "
            "--out exports/openclaw_finance_context.json\n"
            "```\n"
            "The export contains KPIs, top categories, subscriptions, "
            "recommendations, money progress, investment summary, and "
            "net worth — all derived. **No raw account numbers, no API "
            "keys, no config secrets.** See `OPENCLAW_FINANCE_AGENT.md` "
            "for the agent contract and the recommended prompt."
        )
        if st.button("📤 Export agent context now",
                     key="settings_export_ctx"):
            try:
                from utils.agent_context import build_agent_context
                import json as _json
                ctx = build_agent_context()
                out = _root / "exports" / "openclaw_finance_context.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(_json.dumps(ctx, indent=2, default=str),
                               encoding="utf-8")
                st.success(f"Exported {out}")
            except Exception as e:
                st.error(f"Export failed: {e}")

    st.divider()

    # Watch folder
    st.subheader("PDF Watch Folder")
    st.caption(
        "Set this to the folder where you save Tangerine PDF statements. "
        "Ledger will detect new files and prompt you to import them."
    )
    current = get_watch_folder() or ""
    new_folder = st.text_input(
        "Watch folder path",
        value=current,
        placeholder=watch_folder_placeholder(),
    )
    wc1, wc2, wc3 = st.columns(3)
    if wc1.button("Save watch folder"):
        if new_folder and Path(new_folder).is_dir():
            set_watch_folder(new_folder)
            st.success(f"Watching: {new_folder}")
        elif not new_folder:
            set_watch_folder(None)
            st.info("Watch folder disabled.")
        else:
            st.error(
                "Path not found. On Windows use backslashes, e.g. "
                r"C:\Users\YourName\Documents\Statements"
            )
    if wc2.button("Clear watch folder"):
        set_watch_folder(None)
        st.info("Watch folder cleared.")
    if wc3.button("Open data folder"):
        open_folder_in_explorer(Path(__file__).parent.parent / "data")

    st.divider()

    # Pass 32: raw JSON export, settings import/export, and the
    # destructive Delete-all-transactions zone are all advanced/dev
    # tools. They render only when the user is in advanced or
    # developer mode. Basic-mode users see a one-line note pointing
    # them to the share-zip flow above for backup.
    if _settings_mode in ("advanced", "developer"):
        st.subheader("Export Transactions")
        with st.expander("Download transactions as JSON"):
            if st.button("Generate JSON export"):
                rows = conn.execute("SELECT * FROM transactions").fetchall()
                data = [dict(r) for r in rows]
                st.download_button(
                    "⬇ Download transactions.json",
                    data=json.dumps(data, indent=2),
                    file_name="transactions.json",
                    mime="application/json",
                )

        st.subheader("Export / Import Settings")
        with st.expander("Export settings as JSON"):
            if st.button("Generate settings export"):
                budgets_data = conn.execute("SELECT category, amount FROM budgets").fetchall()
                profiles_data = conn.execute("SELECT name, description, budgets_json, notes FROM profiles").fetchall()
                sw_data = conn.execute("SELECT * FROM score_weights ORDER BY id LIMIT 1").fetchone()
                watch_data = conn.execute("SELECT merchant, reason FROM watch_list").fetchall()
                settings_bundle = {
                    "exported_at": datetime.now().isoformat(),
                    "version": "2.0",
                    "budgets": [dict(r) for r in budgets_data],
                    "profiles": [dict(r) for r in profiles_data],
                    "score_weights": dict(sw_data) if sw_data else {},
                    "watch_list": [dict(r) for r in watch_data],
                }
                st.download_button(
                    "⬇ Download ledger-settings.json",
                    data=json.dumps(settings_bundle, indent=2),
                    file_name="ledger-settings.json",
                    mime="application/json",
                )

        with st.expander("Import settings from JSON"):
            st.warning("This will overwrite existing budgets, profiles, score weights, and watch list.")
            uploaded = st.file_uploader("Upload ledger-settings.json", type=["json"])
            if uploaded:
                try:
                    bundle = json.loads(uploaded.read())
                    st.json(bundle)
                    if st.button("Apply imported settings"):
                        # Budgets
                        for b in bundle.get("budgets", []):
                            upsert_budget(b["category"], b["amount"], conn=conn)
                        # Profiles
                        for p in bundle.get("profiles", []):
                            upsert_profile(p["name"], p.get("description",""),
                                           p.get("budgets_json","{}"), p.get("notes",""), conn=conn)
                        # Score weights
                        sw = bundle.get("score_weights", {})
                        if sw:
                            save_score_weights(
                                sw.get("savings_weight", 30), sw.get("diversity_weight", 20),
                                sw.get("debt_weight", 25), sw.get("consistency_weight", 25), conn=conn
                            )
                        # Watch list
                        for w in bundle.get("watch_list", []):
                            add_to_watch_list(w["merchant"], w.get("reason",""), conn=conn)
                        conn.commit()
                        st.success("Settings imported successfully.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Failed to parse file: {e}")

        st.divider()

        # Danger zone — only in developer mode. The DELETE-confirm gate
        # already prevents accidents, but we hide it from advanced mode
        # too because most users never need to nuke their transactions.
        if _settings_mode == "developer":
            with st.expander("Danger Zone"):
                st.warning("Permanently deletes all transactions and the import log (so PDFs can be re-imported). Investments are kept.")
                confirm = st.text_input("Type DELETE to confirm")
                if st.button("Delete all transactions", type="primary"):
                    if confirm == "DELETE":
                        delete_all_transactions()
                        st.success("All transactions deleted.")
                        st.rerun()
                    else:
                        st.error("Type DELETE (all caps) to confirm.")
    else:
        st.caption(
            "Looking for raw JSON exports, settings import, or the "
            "transaction-delete tool? Switch the mode picker to "
            "Advanced or Developer."
        )

# ═══════════════════════════════════════════════════════════════════════
# 7. ABOUT
# ═══════════════════════════════════════════════════════════════════════
elif section == "About":
    st.subheader("Ledger — Personal Finance Dashboard v2.1 (Windows-ready)")
    st.markdown("""
**Stack:** Python 3.11 · Streamlit · SQLite · pdfplumber · Plotly

**What's new in v2.0:**
- **Profiles** — switch between budget sets (Normal/Tight/Vacation/No-Spend)
- **Recommendations engine** — ranked, actionable, grounded in your data
- **Score weights** — customize how your Money Pulse is calculated
- **Watch list** — track specific merchants for price changes
- **Smarter Review queue** — per-item Why Flagged / Context / Next Action
- **Settings export/import** — backup and restore your configuration
- **Onboarding wizard** — guided first-run setup
- **Merchant drilldown** — full history per merchant in Transactions

**Design principles:**
- All data stored locally in `data/finance.db` — nothing sent anywhere
- Parsers tuned to real Tangerine PDF format (chequing v2 + Mastercard v4 bounding-box)
- Double-counting prevention: CC payments and savings transfers excluded from spending
- Backfill-safe: importing older or newer PDFs always updates trends correctly
- No internet required after initial setup

**Data stays local. Always.**

---

**Version:** 2.0.0  
**Supported sources:** Tangerine Chequing PDF, Tangerine Mastercard PDF, CSV

**Limitations:**
- PDF parsing uses pdfplumber — works on digital PDFs, not scanned images
- Tangerine has no public API — PDF/CSV import only
- Investment data entered manually
    """)

conn.close()

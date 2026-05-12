"""
Review Flagged — smart review queue with per-item Why Flagged / Context / Next Action.
"""
import streamlit as st
import pandas as pd
from collections import Counter, defaultdict

from utils.database import (
    init_db, get_connection, get_flagged_transactions, update_transaction,
    apply_ai_suggestion, accept_ai_suggestion, reject_ai_suggestion,
    get_ai_candidates, apply_category_to_merchant, upsert_learned_rule,
    verify_merchant_category,
    # Pass 16 — explicit-IDs apply (the previous merchant-only helper updated
    # categories for all siblings but only cleared the flag on the current row,
    # so the user saw the queue drop by 1 instead of by N. The new helper acts
    # on an explicit ID list so the displayed count and the DB action match.)
    get_merchant_transaction_ids, apply_category_by_ids, get_learned_rule,
)
from config.categories import CATEGORIES
from utils.styles import inject_styles
from utils.ai_categorizer import (
    suggest_for_transaction, suggest_for_transaction_v2,
    verify_for_transaction, provider_status,
)
from utils.ai_explainer import review_triage_summary

st.set_page_config(page_title="Review · Ledger", page_icon="⚑", layout="wide")
inject_styles()
init_db()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Review Flagged")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

# ── Pass 15: persistent save banner + module-level save executor ────────
# Defined here (before conn = get_connection()) so that _render_save_banner()
# can be called immediately after the connection is opened without a NameError.
# Python executes module-level statements top-to-bottom; the call at line ~36
# would fail if these definitions appeared further down the file.
def _render_save_banner() -> None:
    """Render any pending banner stored in session_state at the top of the page.

    The banner survives st.rerun() so the user always sees the result of
    their save action (Pass 14 success messages flashed away on rerun).
    """
    banner = st.session_state.get("review_save_banner")
    if not banner:
        return
    severity = banner.get("severity", "success")
    msg = banner.get("message", "")
    detail = banner.get("detail", "")
    bcol_msg, bcol_x = st.columns([8, 1])
    with bcol_msg:
        if severity == "warning":
            st.warning(msg)
        elif severity == "error":
            st.error(msg)
        else:
            st.success(msg)
        if detail:
            st.caption(detail)
    with bcol_x:
        if st.button("✕ Dismiss", key="review_banner_dismiss"):
            st.session_state.pop("review_save_banner", None)
            st.rerun()


def _execute_save(
    *,
    tx_id: int,
    expected_merchant: str,
    new_cat: str,
    new_note: str,
    clear_flag: bool,
    apply_mode: str,            # 'self' | 'safe' | 'force'
    teach_rule: bool,
) -> None:
    """Single source of truth for save actions on the Review page.

    Pass 16 redesign — explicit-transaction-IDs path:

    For apply_mode in ('safe', 'force') the handler now:
      1. Re-reads the CURRENT row from the DB (defensive against stale widgets).
      2. Validates the merchant string still matches the one the UI rendered.
      3. SELECTs the exact list of transaction IDs that will be updated, using
         the same logic as the merchant_count / merchant_uncertain_count
         displayed beside the button — so promise and action cannot diverge.
      4. Performs a single UPDATE … WHERE id IN (…) that sets category +
         is_transfer (synced to the new category) AND, if clear_flag, clears
         is_flagged and writes flag_reason='reviewed' atomically — so flag
         clear applies to the SAME row set as the category apply.
      5. Reads back the IDs and reports exact verified counts:
            'Updated N transaction(s), cleared M flag(s).'
      6. Stores a banner in session_state so the result survives st.rerun().

    For apply_mode == 'self' the behaviour is unchanged: only the current row
    is updated; siblings are left alone.

    For teach_rule=True we ALSO read back from learned_rules to confirm the
    rule landed (the user's manual-test feedback was that Teach Ledger
    "appears unreliable" — the silent path was the root cause).
    """
    # 1. Defensive fresh read.
    fresh = conn.execute(
        "SELECT id, merchant, category, subcategory FROM transactions WHERE id=?",
        (int(tx_id),),
    ).fetchone()
    if fresh is None:
        st.session_state["review_save_banner"] = {
            "severity": "error",
            "message":  f"Transaction #{tx_id} no longer exists. Refresh and try again.",
        }
        st.rerun()
        return

    db_merchant = (fresh["merchant"] or "").strip()
    if db_merchant != expected_merchant:
        st.session_state["review_save_banner"] = {
            "severity": "error",
            "message":  (f"Merchant changed from '{expected_merchant}' to "
                         f"'{db_merchant}' since this page rendered. "
                         "Refresh and try again."),
        }
        st.rerun()
        return

    db_subcategory = fresh["subcategory"] or ""

    # 2. Compute target ID list per mode.
    if apply_mode == "self" or not db_merchant:
        target_ids = [int(tx_id)]
        # 'self' uses the simpler update_transaction path so the user can also
        # update notes (apply_category_by_ids ignores notes). We replicate the
        # explicit-IDs reporting shape so the banner is consistent.
        upd = {"category": new_cat, "notes": new_note}
        if clear_flag:
            upd["is_flagged"]  = 0
            upd["flag_reason"] = "reviewed"
            # Pass 34a hotfix: a manual review is the highest-confidence
            # signal we have. Promote parse_confidence to 'high' so the
            # row stops appearing in low-confidence / AI-candidate lists.
            # Without this, rows with a real category but parse_confidence
            # still 'low' kept showing up under "Uncategorized / Low
            # confidence" after the user thought they were done.
            upd["parse_confidence"] = "high"
        # Snapshot whether the row was flagged for accurate clear count.
        was_flagged = int(fresh["is_flagged"] if "is_flagged" in fresh.keys() else 0) == 1 \
            if hasattr(fresh, "keys") else False
        # `fresh` from SELECT above didn't include is_flagged; re-read it.
        was_flagged_row = conn.execute(
            "SELECT is_flagged FROM transactions WHERE id=?", (int(tx_id),),
        ).fetchone()
        was_flagged = bool(was_flagged_row and int(was_flagged_row["is_flagged"] or 0))
        update_transaction(int(tx_id), upd, conn=conn)
        n_requested = 1
        n_with_category = 1  # we just set it
        n_flags_cleared = 1 if (clear_flag and was_flagged) else 0
    else:
        # safe / force: explicit ID list from the same source the UI used.
        target_ids = get_merchant_transaction_ids(
            db_merchant,
            only_uncertain=(apply_mode == "safe"),
            conn=conn,
        )
        # Always include the current row even in safe mode so the user's
        # explicit save action is not skipped if their row was high-confidence.
        if int(tx_id) not in target_ids:
            target_ids.append(int(tx_id))

        # Notes only update on the current row — the bulk apply doesn't
        # touch siblings' notes.
        if new_note:
            conn.execute(
                "UPDATE transactions SET notes=? WHERE id=?",
                (new_note, int(tx_id)),
            )

        result = apply_category_by_ids(
            target_ids, new_cat,
            subcategory=db_subcategory,
            clear_flags=clear_flag,
            conn=conn,
        )
        n_requested      = result["requested"]
        n_with_category  = result["now_with_category"]
        n_flags_cleared  = result["flags_cleared"]

    # 3. Teach rule (independent of apply mode). Verify by readback.
    teach_msg = ""
    teach_severity_warn = False
    if teach_rule and db_merchant:
        upsert_learned_rule(db_merchant, new_cat, db_subcategory, conn=conn)
        # Verify the rule landed by re-reading. If not present, escalate.
        rule = get_learned_rule(db_merchant, conn=conn)
        if rule and rule.get("category") == new_cat:
            teach_msg = (f" · learned rule saved: **'{db_merchant}' → {new_cat}** "
                         "(future imports will use it automatically)")
        else:
            teach_msg = (f" · ⚠ Teach Ledger failed for '{db_merchant}' — rule "
                         "did not appear in learned_rules. Try again.")
            teach_severity_warn = True

    # 4. Commit.
    conn.commit()

    # 5. Build banner.
    if apply_mode == "self":
        msg = f"Saved transaction #{tx_id}.{teach_msg}"
        if clear_flag and n_flags_cleared:
            msg += f" Cleared 1 flag."
        detail = ""
        # If sibling rows exist, tell the user what's still untouched so they
        # don't think force/safe didn't run.
        sibling_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant=? AND id != ?",
            (db_merchant, int(tx_id)),
        ).fetchone()[0] if db_merchant else 0
        if sibling_count > 0:
            detail = (f"Other '{db_merchant}' rows untouched ({sibling_count} sibling(s) "
                      "still unchanged). Use Safe / Force apply to also update them.")
        severity = "warning" if teach_severity_warn else "success"
    elif apply_mode == "safe":
        msg = (f"**Updated {n_with_category} transaction(s), cleared "
               f"{n_flags_cleared} flag(s)** (Safe apply on '{db_merchant}').{teach_msg}")
        # In safe mode, total siblings = uncertain we touched + hand-categorized
        # we preserved. Show the preserved count for transparency.
        total_for_merchant = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE merchant=?",
            (db_merchant,),
        ).fetchone()[0]
        preserved = total_for_merchant - n_requested
        if preserved > 0:
            detail = (f"{preserved} hand-categorized '{db_merchant}' row(s) preserved. "
                      "Use Force apply to also overwrite those.")
        else:
            detail = "All matching siblings updated."
        severity = "warning" if teach_severity_warn else "success"
    else:  # force
        msg = (f"**Updated {n_with_category} transaction(s), cleared "
               f"{n_flags_cleared} flag(s)** (Force apply on '{db_merchant}' "
               f"→ {new_cat}).{teach_msg}")
        if n_with_category < n_requested:
            severity = "warning"
            detail = (f"⚠ {n_requested - n_with_category} requested row(s) did NOT "
                      "land on the target category. Likely a different merchant "
                      "spelling (for example, compacted names vs spaced names). Search "
                      "Transactions for the variant and update those manually.")
        else:
            severity = "warning" if teach_severity_warn else "success"
            detail = (f"All {n_requested} matching rows transitioned to "
                      f"'{new_cat}' and is_transfer was synced. ID list: "
                      f"{', '.join(str(i) for i in target_ids[:8])}"
                      + ("…" if len(target_ids) > 8 else ""))

    st.session_state["review_save_banner"] = {
        "severity": severity,
        "message":  msg,
        "detail":   detail,
    }
    # Clear any "armed" flags for force buttons so the next page render
    # doesn't show them stuck in the armed state.
    for k in list(st.session_state.keys()):
        if k.endswith(f"_force_armed_{tx_id}"):
            st.session_state.pop(k, None)
    st.rerun()


conn = get_connection()

# Persistent save banner (Pass 15) — shown immediately under the title so the
# user always sees the result of their last save action even after st.rerun().
_render_save_banner()

flagged = get_flagged_transactions(conn=conn)

# AI candidates (uncategorized / low-confidence / Misc) — separate from flagged queue
ai_candidates = get_ai_candidates(limit=200, conn=conn)
ai_stat = provider_status()

# ── AI status strip ─────────────────────────────────────────────────────
if ai_stat["enabled"] or ai_candidates:
    with st.container():
        sc1, sc2 = st.columns([3, 2])
        with sc1:
            if ai_stat["ready"]:
                st.caption(
                    f"🧠 **AI categorization ready** — {ai_stat['provider']} · {ai_stat['model']}. "
                    f"Uncategorized / low-confidence rows: **{len(ai_candidates)}**. "
                    f"Suggestions are review-only — accept or reject each one."
                )
            elif ai_stat["enabled"]:
                st.caption(f"🧠 AI enabled but not ready — {ai_stat.get('reason','')}")
            else:
                st.caption(
                    f"🧠 AI categorization is off. {len(ai_candidates)} rows could use a suggestion. "
                    f"Enable it in Settings → AI Categorization."
                )
        with sc2:
            if st.button("Open AI Settings", use_container_width=True):
                st.switch_page("pages/9_Settings.py")

        # ── Batch suggest with size selector ────────────────────────────
        pending_target = [t for t in ai_candidates if t.get("ai_suggested_at") is None]
        eligible_n     = len(pending_target)

        bs1, bs2, bs3 = st.columns([2, 1.5, 1.5])
        with bs1:
            st.caption(
                f"**Bulk suggest** — {eligible_n} eligible row(s) (uncategorized / "
                f"Misc / low-confidence, no prior suggestion). Already-categorized rows "
                f"use the per-row **Verify with AI** button below."
            )
        with bs2:
            options = ["25", "50", "All eligible"]
            batch_choice = st.selectbox(
                "Batch size",
                options,
                index=0,
                key="bulk_batch_choice",
                disabled=not ai_stat["ready"] or eligible_n == 0,
                help=("Pick how many rows to send to the model in one click. "
                      "Each row is one short call; the page shows progress."),
            )
            if batch_choice == "All eligible":
                batch_n = eligible_n
            else:
                batch_n = min(int(batch_choice), eligible_n)
        with bs3:
            disabled = not ai_stat["ready"] or batch_n == 0
            if st.button(
                f"Suggest for {batch_n} rows",
                disabled=disabled,
                help=(f"Calls {ai_stat['provider']}/{ai_stat['model']} for {batch_n} eligible row(s). "
                      "Suggestions are stored against the row — you review and accept individually."
                      if ai_stat["ready"]
                      else "Enable AI in Settings → AI Categorization first."),
                use_container_width=True,
                type="primary",
            ):
                # Pass 17: tiered v2 path. Each row tries deterministic rules
                # first; only ambiguous rows hit MiniMax. Fallback covers AI
                # parse failures so we never produce a "raw parse error" row.
                progress = st.progress(0.0, text="Requesting suggestions...")
                tier_counts = {"rule": 0, "ai": 0, "fallback": 0, "none": 0}
                for i, tx in enumerate(pending_target[:batch_n]):
                    try:
                        v2 = suggest_for_transaction_v2(tx, timeout=25.0)
                    except Exception:
                        v2 = {"ok": False, "tier": "none"}
                    tier_counts[v2.get("tier", "none")] = tier_counts.get(v2.get("tier", "none"), 0) + 1
                    if v2.get("ok") and v2.get("category"):
                        # Translate v2 → apply_ai_suggestion shape.
                        apply_ai_suggestion(tx["id"], {
                            "category":    v2["category"],
                            "subcategory": v2["subcategory"],
                            "confidence":  v2["confidence"],
                            "provider":    v2["provider"],
                            "model":       v2["model"],
                            "rationale":   v2["rationale"],
                        }, conn=conn)
                    progress.progress((i + 1) / max(1, batch_n),
                                      text=f"Suggested {i+1}/{batch_n}")
                conn.commit()
                progress.empty()
                ok = tier_counts["rule"] + tier_counts["ai"] + tier_counts["fallback"]
                skipped = max(0, eligible_n - batch_n)
                msg = (
                    f"Bulk suggest · **{ok}/{batch_n}** succeeded "
                    f"(rule **{tier_counts['rule']}**, AI **{tier_counts['ai']}**, "
                    f"fallback **{tier_counts['fallback']}**, none **{tier_counts['none']}**). "
                    f"{skipped} skipped of {eligible_n} eligible. "
                    "Review suggestions below before accepting."
                )
                if ok > 0:
                    st.success(msg)
                else:
                    st.warning(msg + " (No deterministic rule matched and the AI "
                                     "tier failed. The fallback tier should normally "
                                     "catch these — check Settings → AI for connectivity.)")
                st.rerun()
        st.caption(
            "ℹ️ AI suggestions are review-only. Nothing is applied to your ledger until "
            "you click **Accept** on a row."
        )
    st.divider()

if not flagged and not ai_candidates:
    st.success("Nothing to review — all transactions look good.")
    conn.close()
    st.stop()

DIRECTION_BADGE = {
    "debit":     ("💸 Debit",    "#f85149"),
    "credit":    ("✅ Credit",   "#3fb950"),
    "payment":   ("💳 Payment",  "#8b949e"),
    "transfer":  ("🔄 Transfer", "#4f86c6"),
    "cancelled": ("✖ Cancelled", "#6e7681"),
}
CONF_COLOR = {"high": "#3fb950", "medium": "#e3b341", "low": "#f85149"}



def _render_ai_block(tx: dict, merchant_count: int):
    """Render AI suggestion chip + accept/reject / Suggest / Verify button for a row."""
    sug_cat = tx.get("ai_suggested_category")
    if sug_cat:
        confidence = tx.get("ai_confidence") or 0.0
        provider = tx.get("ai_provider") or "?"
        model    = tx.get("ai_model") or "?"
        rationale = tx.get("ai_rationale") or ""
        accepted = tx.get("ai_accepted")
        status_txt = (
            "accepted" if accepted == 1
            else "rejected" if accepted == 0
            else "pending"
        )
        st.markdown(
            f"<span style='background:#4f86c6;color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-size:11px'>🧠 AI · {provider} / {model}</span> &nbsp;"
            f"<span style='background:#30363d;color:#e6edf3;padding:2px 8px;"
            f"border-radius:4px;font-size:11px'>confidence {confidence:.0%}</span> &nbsp;"
            f"<span style='background:#30363d;color:#e6edf3;padding:2px 8px;"
            f"border-radius:4px;font-size:11px'>{status_txt}</span>",
            unsafe_allow_html=True,
        )
        sub_line = f" / {tx.get('ai_suggested_subcategory')}" if tx.get("ai_suggested_subcategory") else ""
        st.markdown(f"**Suggestion:** {sug_cat}{sub_line}")
        if rationale:
            st.caption(f"_Why: {rationale}_")
        if accepted is None:  # pending
            ac1, ac2, _ = st.columns([1, 1, 4])
            if ac1.button("Accept", key=f"ai_accept_{tx['id']}", type="primary"):
                accept_ai_suggestion(tx["id"], conn=conn)
                # Also mark reviewed and clear flag if any
                update_transaction(
                    int(tx["id"]),
                    {"is_flagged": 0, "flag_reason": "reviewed"},
                    conn=conn,
                )
                conn.commit()
                st.success(f"Accepted suggestion for #{tx['id']}")
                st.rerun()
            if ac2.button("Reject", key=f"ai_reject_{tx['id']}"):
                reject_ai_suggestion(tx["id"], conn=conn)
                conn.commit()
                st.rerun()
        return

    # No stored AI suggestion. Offer the right path based on whether the row
    # is already categorized.
    current_cat = (tx.get("category") or "").strip()
    is_categorized = bool(current_cat) and current_cat.lower() != "misc"
    parse_conf = (tx.get("parse_confidence") or "high")

    verify_key = f"ai_verify_result_{tx['id']}"
    verify_res = st.session_state.get(verify_key)

    if not ai_stat["ready"]:
        st.caption("🧠 AI is off — enable it in Settings → AI Categorization to "
                   "suggest or verify categories for this row.")
    else:
        # Show Verify (categorized) or Suggest (uncategorized / low-conf) button
        if is_categorized and parse_conf != "low":
            btn_label = f"🧠 Verify with AI"
            btn_help  = (f"Ask {ai_stat['provider']}/{ai_stat['model']} whether "
                         f"the current category ({current_cat}) looks right. "
                         "AI sees only the row's evidence — not the assigned category. "
                         "Result is review-only.")
            if st.button(btn_label, key=f"ai_verify_{tx['id']}", help=btn_help):
                with st.spinner("Asking the model..."):
                    st.session_state[verify_key] = verify_for_transaction(tx, timeout=25.0)
                st.rerun()
        else:
            if st.button(
                "🧠 Suggest category",
                key=f"ai_suggest_{tx['id']}",
                help=("Tier 1 deterministic rules first; AI only if ambiguous; "
                      "deterministic fallback if AI fails. You review before "
                      "it's applied."),
            ):
                with st.spinner("Categorizing (rules → AI → fallback)..."):
                    v2 = suggest_for_transaction_v2(tx, timeout=25.0)
                if v2.get("ok") and v2.get("category"):
                    apply_ai_suggestion(tx["id"], {
                        "category":    v2["category"],
                        "subcategory": v2["subcategory"],
                        "confidence":  v2["confidence"],
                        "provider":    v2["provider"],
                        "model":       v2["model"],
                        "rationale":   (f"[{v2['source']}] " + (v2["rationale"] or "")
                                        + (f"  ·  {v2['note']}" if v2.get("note") else "")).strip(),
                    }, conn=conn)
                    conn.commit()
                    st.rerun()
                else:
                    # Last resort — let the user see the verify-mode answer
                    # rather than a silent failure. v2 only returns ok=False
                    # if all 3 tiers couldn't produce ANY category.
                    with st.spinner("All tiers exhausted — running verify mode..."):
                        st.session_state[verify_key] = verify_for_transaction(tx, timeout=25.0)
                    st.rerun()

    # Render any pending verify result for this row
    if verify_res is not None:
        _render_verify_result(tx, verify_res)
        if st.button("Clear AI verify result", key=f"ai_verify_clear_{tx['id']}"):
            st.session_state.pop(verify_key, None)
            st.rerun()


def _render_verify_result(tx: dict, vr: dict) -> None:
    """Render a verify_for_transaction result inline."""
    mode = vr.get("mode") or "failed"
    suggested = vr.get("suggested_category", "")
    sug_sub   = vr.get("suggested_subcategory", "")
    current   = vr.get("current_category", "")
    rationale = vr.get("rationale", "")
    conf      = float(vr.get("confidence", 0.0))
    allowed   = bool(vr.get("allowed", False))
    provider  = vr.get("provider", "")
    model     = vr.get("model", "")

    if mode == "agree":
        bar_color  = "#3fb950"
        bar_bg     = "rgba(63,185,80,0.06)"
        headline   = f"AI agrees this looks like **{suggested or current}**"
    elif mode == "suggest_change":
        bar_color  = "#e3b341"
        bar_bg     = "rgba(227,179,65,0.06)"
        headline   = (f"AI would suggest **{suggested}** "
                      f"(currently {current or 'uncategorized'})")
    elif mode == "uncertain":
        bar_color  = "#8b949e"
        bar_bg     = "rgba(139,148,158,0.06)"
        headline   = (f"AI is uncertain — best guess **{suggested}** "
                      f"at {conf:.0%} confidence")
    else:  # failed
        bar_color  = "#f85149"
        bar_bg     = "rgba(248,81,73,0.06)"
        headline   = f"AI verify failed — {vr.get('reason', 'no reason returned')}"

    safety_chip = ""
    if mode != "failed" and not allowed:
        safety_chip = ("<span style='background:#8b949e;color:#fff;padding:1px 7px;"
                       "border-radius:3px;font-size:10px;font-weight:600;"
                       "margin-left:6px'>SYSTEM CATEGORY — manual only</span>")

    sub_line = f" / {sug_sub}" if sug_sub else ""
    rat_line = f"<div style='font-size:0.78rem;color:#8b949e;margin-top:4px'>_Why: {rationale}_</div>" if rationale else ""

    st.markdown(
        f"<div style='background:{bar_bg};border:1px solid rgba(255,255,255,0.07);"
        f"border-left:3px solid {bar_color};border-radius:6px;padding:8px 12px;margin-top:6px'>"
        f"<div style='font-size:0.88rem;color:#e6edf3'>{headline}{sub_line}{safety_chip}</div>"
        f"{rat_line}"
        f"<div style='font-size:0.72rem;color:#6e7681;margin-top:4px'>"
        f"confidence {conf:.0%} · {provider}/{model}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Apply / store buttons — only when AI's suggestion is allowed and differs
    # from the current category.
    if mode == "suggest_change" and allowed and suggested:
        ap1, ap2, _ = st.columns([1.4, 1.2, 4])
        if ap1.button("Apply suggestion", key=f"ai_verify_apply_{tx['id']}", type="primary"):
            sug_payload = {
                "category":    suggested,
                "subcategory": sug_sub,
                "confidence":  conf,
                "rationale":   rationale,
                "provider":    provider,
                "model":       model,
            }
            apply_ai_suggestion(tx["id"], sug_payload, conn=conn)
            accept_ai_suggestion(tx["id"], conn=conn)
            update_transaction(int(tx["id"]),
                               {"is_flagged": 0, "flag_reason": "reviewed"}, conn=conn)
            conn.commit()
            st.session_state.pop(f"ai_verify_result_{tx['id']}", None)
            st.success(f"Applied {suggested} to #{tx['id']}")
            st.rerun()
        if ap2.button("Store as suggestion", key=f"ai_verify_store_{tx['id']}",
                      help="Save the AI suggestion against this row without applying it. "
                           "Appears in the suggestion-pending state."):
            sug_payload = {
                "category":    suggested,
                "subcategory": sug_sub,
                "confidence":  conf,
                "rationale":   rationale,
                "provider":    provider,
                "model":       model,
            }
            apply_ai_suggestion(tx["id"], sug_payload, conn=conn)
            conn.commit()
            st.session_state.pop(f"ai_verify_result_{tx['id']}", None)
            st.rerun()


def _render_row_editor(tx: dict, key_prefix: str, show_clear_flag: bool = True):
    """Shared editor UI used by both the flagged queue and the AI queue."""
    direction = tx.get("direction", "debit")
    badge_label, badge_color = DIRECTION_BADGE.get(direction, ("?", "#8b949e"))
    conf = tx.get("parse_confidence", "high")
    conf_color = CONF_COLOR.get(conf, "#8b949e")

    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(f"**Raw description:** `{tx.get('raw_description','')}`")
        st.markdown(
            f"<span style='background:{badge_color};color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-size:11px'>{badge_label}</span> &nbsp;"
            f"<span style='background:{conf_color};color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-size:11px'>Confidence: {conf}</span>",
            unsafe_allow_html=True,
        )
        st.write("")
        if tx.get("notes"):
            st.caption(f"Notes: {tx['notes']}")
    with c2:
        st.metric("Amount", f"${abs(tx.get('amount',0)):,.2f}")
        st.caption(f"ID: {tx['id']}")
        st.caption(f"Account: {tx.get('account_type','')}")
        if tx.get("statement_period"):
            st.caption(f"Statement: {tx['statement_period']}")

    # Count sibling transactions sharing the same merchant (for apply-to-all)
    merchant = (tx.get("merchant") or "").strip()
    merchant_count = 0
    merchant_uncertain_count = 0
    if merchant:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE merchant=? AND id<>?",
            (merchant, int(tx["id"])),
        ).fetchone()
        merchant_count = int(row["n"]) if row else 0
        # Count siblings that are "uncertain" (NULL/empty/Misc/low-confidence) —
        # these are the safe-to-overwrite rows.
        urow = conn.execute(
            """
            SELECT COUNT(*) AS n FROM transactions
            WHERE merchant=? AND id<>?
              AND (category IS NULL OR category='' OR category='Misc'
                   OR parse_confidence='low')
            """,
            (merchant, int(tx["id"])),
        ).fetchone()
        merchant_uncertain_count = int(urow["n"]) if urow else 0

    # AI block (suggestion chip / actions / suggest-now button)
    _render_ai_block(tx, merchant_count)

    st.markdown("**Edit this transaction:**")
    ec1, ec2 = st.columns([2, 2])
    new_cat  = ec1.selectbox(
        "Category", CATEGORIES,
        index=CATEGORIES.index(tx["category"]) if tx.get("category") in CATEGORIES else 0,
        key=f"{key_prefix}_cat_{tx['id']}",
    )
    new_note = ec2.text_input(
        "Note", value=tx.get("notes") or "", key=f"{key_prefix}_note_{tx['id']}",
    )

    # Teach-Ledger checkbox (decoupled from save action — user can tick this
    # any time and it applies to whichever Save button they click).
    teach_rule = False
    clear_flag = False
    opt_col1, opt_col2 = st.columns([3, 2])
    if merchant:
        teach_rule = opt_col1.checkbox(
            f"Also teach Ledger: '{merchant}' → this category for future imports",
            value=False,
            key=f"{key_prefix}_teach_{tx['id']}",
            help="Saves this as a learned rule, consulted before the static rules on "
                 "future imports.",
        )
    if show_clear_flag:
        clear_flag = opt_col2.checkbox(
            "Clear flag (mark reviewed) on save",
            value=True, key=f"{key_prefix}_clear_{tx['id']}",
        )

    # ── Explicit save buttons ───────────────────────────────────────────
    # Pass 15 redesign:
    #   • Force apply uses a TWO-CLICK confirmation. First click "arms" the
    #     button (stores intent in session_state); second click executes.
    #     This eliminates any chance of an accidental click + the user is
    #     visually shown what's about to happen between the two clicks.
    #   • Save handler re-fetches the tx row from the DB at click-time so
    #     a stale closure variable can't make the wrong update.
    #   • Result is written to session_state["review_save_banner"] and rendered
    #     at the top of the page on the next rerun — survives navigation noise.
    st.markdown("**Save options:**")

    arm_key = f"{key_prefix}_force_armed_{tx['id']}"
    is_armed = bool(st.session_state.get(arm_key))

    # Render the button row. We always show "Save this row only"; we add the
    # apply-to-merchant buttons only when there are siblings.
    #
    # Pass 35d clear-flag semantics: when the "Clear flag" checkbox is
    # hidden (`show_clear_flag=False` — the AI / Low-confidence queue),
    # treat every save as a manual review. Before this fix, saving
    # from the AI queue did NOT promote parse_confidence='high', so
    # rows like ID 684 (SQ *C&C GAMEBRIDGE) stayed stuck in the queue
    # after Save + Force. The expression below reads as: clear when
    # the checkbox says clear, OR when no checkbox exists at all.
    if merchant and merchant_count > 0:
        bcol1, bcol2, bcol3 = st.columns([1.4, 1.7, 1.9])
        if bcol1.button(
            "💾 Save this row only",
            key=f"{key_prefix}_save_self_{tx['id']}",
            type="secondary",
            help="Update only this transaction. Other rows for this merchant are untouched.",
            use_container_width=True,
        ):
            _execute_save(
                tx_id=int(tx["id"]),
                expected_merchant=merchant,
                new_cat=new_cat,
                new_note=new_note,
                clear_flag=bool(clear_flag or not show_clear_flag),
                apply_mode="self",
                teach_rule=bool(teach_rule),
            )
        safe_disabled = merchant_uncertain_count == 0
        safe_label = (
            f"💾 Save + Safe apply ({merchant_uncertain_count} uncertain sibling(s))"
            if not safe_disabled
            else "💾 Save + Safe apply (0 uncertain — nothing to do)"
        )
        if bcol2.button(
            safe_label,
            key=f"{key_prefix}_save_safe_{tx['id']}",
            type="secondary",
            disabled=safe_disabled,
            help=("Save this row AND apply the same category to siblings that are "
                  "uncategorized / Misc / low-confidence. Hand-categorized siblings "
                  "are preserved."),
            use_container_width=True,
        ):
            _execute_save(
                tx_id=int(tx["id"]),
                expected_merchant=merchant,
                new_cat=new_cat,
                new_note=new_note,
                clear_flag=bool(clear_flag or not show_clear_flag),
                apply_mode="safe",
                teach_rule=bool(teach_rule),
            )
        # Force apply uses two-click confirm to remove any UX ambiguity.
        force_label = (
            f"⚠ Confirm Force apply — overwrite {merchant_count + 1} row(s)?"
            if is_armed
            else f"⚠ Save + Force apply (all {merchant_count + 1} '{merchant}' row(s))"
        )
        force_help = (
            f"Click ONCE more to overwrite this row + every other '{merchant}' row "
            f"with category '{new_cat}'. Click anywhere else to cancel."
            if is_armed
            else f"Use when you're certain '{new_cat}' is correct for every "
                 f"'{merchant}' row. Click once to ARM, click again to EXECUTE — "
                 f"prevents accidental overwrites."
        )
        if bcol3.button(
            force_label,
            key=f"{key_prefix}_save_force_{tx['id']}",
            type="primary",
            help=force_help,
            use_container_width=True,
        ):
            if is_armed:
                # Second click — execute.
                st.session_state.pop(arm_key, None)
                _execute_save(
                    tx_id=int(tx["id"]),
                    expected_merchant=merchant,
                    new_cat=new_cat,
                    new_note=new_note,
                    clear_flag=bool(clear_flag or not show_clear_flag),
                    apply_mode="force",
                    teach_rule=bool(teach_rule),
                )
            else:
                # First click — arm.
                st.session_state[arm_key] = True
                st.rerun()
        if is_armed:
            st.warning(
                f"⚠ **Armed.** One more click of **{force_label}** will overwrite "
                f"this row + every other '{merchant}' row to **{new_cat}**. "
                "Click any other button (or change a field) to cancel."
            )
    else:
        if st.button(
            "💾 Save this row",
            key=f"{key_prefix}_save_self_{tx['id']}",
            type="primary",
            use_container_width=False,
        ):
            _execute_save(
                tx_id=int(tx["id"]),
                expected_merchant=merchant,
                new_cat=new_cat,
                new_note=new_note,
                clear_flag=bool(clear_flag or not show_clear_flag),
                apply_mode="self",
                teach_rule=bool(teach_rule),
            )

    # ── Pass 35d: "Mark reviewed" fallback ──────────────────────────
    # When a row already has a real category but is stuck in the
    # Low-confidence queue (because parse_confidence='low' from a
    # pre-Pass-34a save), a one-click "Mark reviewed" path removes
    # it. Idempotent: sets is_flagged=0, flag_reason='reviewed',
    # parse_confidence='high'. No category change, no teach-Ledger
    # side effects.
    _existing_cat = (tx.get("category") or "").strip()
    _existing_real = (
        _existing_cat
        and _existing_cat not in ("", "Misc", "Uncategorized")
    )
    if _existing_real:
        if st.button(
            f"✓ Mark reviewed (already '{_existing_cat}')",
            key=f"{key_prefix}_mark_reviewed_{tx['id']}",
            type="secondary",
            help=(
                "Use when this row already has the right category but "
                "is stuck in the queue due to old low parse confidence. "
                "Removes it from Review without changing the category."
            ),
        ):
            from utils.database import mark_transaction_reviewed
            mark_transaction_reviewed(int(tx["id"]), conn=conn)
            conn.commit()
            st.session_state["review_save_banner"] = {
                "severity": "success",
                "message":  (
                    f"Marked transaction #{tx['id']} as reviewed. "
                    f"Category '{_existing_cat}' preserved; row removed "
                    "from the Low-confidence queue."
                ),
                "detail":   "",
            }
            st.rerun()


if flagged:
    # ── MiniMax Triage Summary (grounded) ───────────────────────────────
    triage_key = "ai_triage_cache"
    trg_hdr, trg_btn = st.columns([4, 1])
    with trg_hdr:
        st.markdown(
            '<p class="ledger-section-header">MiniMax Triage</p>',
            unsafe_allow_html=True,
        )
    with trg_btn:
        if st.button("↻ Refresh triage", key="triage_refresh", use_container_width=True):
            st.session_state.pop(triage_key, None)
    if triage_key not in st.session_state:
        with st.spinner("Summarizing queue…"):
            st.session_state[triage_key] = review_triage_summary(flagged, len(ai_candidates))
    triage = st.session_state[triage_key]

    _ok = triage.get("ok")
    _border = "#4f86c6" if _ok else "#8b949e"
    _badge_color = "#4f86c6" if _ok else "#8b949e"
    _badge  = "AI · grounded" if _ok else "Deterministic fallback"
    st.markdown(
        f"<div style='background:rgba(79,134,198,0.05);border:1px solid rgba(79,134,198,0.2);"
        f"border-left:3px solid {_border};border-radius:8px;padding:12px 16px;margin-bottom:10px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px'>"
        f"<span style='font-size:1rem;font-weight:700;color:#e6edf3'>{triage.get('headline','')}</span>"
        f"<span style='background:{_badge_color};color:#fff;padding:2px 8px;"
        f"border-radius:4px;font-size:10px;font-weight:600'>{_badge}</span>"
        f"</div>"
        f"<div style='font-size:0.9rem;color:#c9d1d9;line-height:1.5'>{triage.get('summary','')}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    moves = triage.get("clean_first") or []
    if moves:
        st.markdown("**Clean these first:**")
        for m in moves:
            st.markdown(f"- {m}")
    if triage.get("error"):
        st.caption(f"⚠ {triage['error']}")
    st.caption(f"Grounded in: {', '.join(triage.get('grounded_from') or [])}  ·  "
               f"{triage.get('provider','—')}/{triage.get('model','—')}")
    st.divider()

    # ── Summary strip ───────────────────────────────────────────────────
    total_flagged = len(flagged)
    total_amount  = sum(abs(t.get("amount", 0)) for t in flagged)

    reason_counts = Counter()
    for t in flagged:
        for r in (t.get("flag_reason") or "").split(";"):
            r = r.strip()
            if r:
                reason_counts[r] += 1

    _HIGH_IMPACT_REASONS = ("cash advance", "nsf", "large debit")
    high_impact = [
        t for t in flagged
        if any(r in (t.get("flag_reason") or "").lower() for r in _HIGH_IMPACT_REASONS)
    ]
    high_impact_value = sum(abs(t.get("amount", 0)) for t in high_impact)

    m1, m2, m3 = st.columns(3)
    m1.metric("Items in queue", total_flagged)
    m2.metric("Total value", f"${total_amount:,.2f}",
              help="Absolute value across all flagged items regardless of reason.")
    m3.metric(
        "High-impact items", len(high_impact),
        delta=f"${high_impact_value:,.0f}" if high_impact else "none",
        delta_color="inverse" if high_impact else "normal",
        help=(
            "Cash advances, NSFs, and debits over $2,000 — these materially affect "
            "your cashflow picture. Clear these first; data-quality flags (low parse "
            "confidence, cancelled pairs) can wait."
        ),
    )

    if reason_counts:
        cols = st.columns(min(4, len(reason_counts)))
        for i, (reason, count) in enumerate(reason_counts.most_common(4)):
            cols[i].metric(reason.replace("_", " ").title(), count)

    st.divider()

    # ── Filter bar ─────────────────────────────────────────────────────
    f1, f2 = st.columns([2, 1])
    with f1:
        reason_filter = st.selectbox(
            "Filter by reason",
            ["All"] + [r for r, _ in reason_counts.most_common()],
        )
    with f2:
        conf_filter = st.selectbox("Parse confidence", ["All", "low", "medium", "high"])

    filtered = flagged
    if reason_filter != "All":
        filtered = [t for t in filtered if reason_filter in (t.get("flag_reason") or "")]
    if conf_filter != "All":
        filtered = [t for t in filtered if t.get("parse_confidence") == conf_filter]

    st.caption(f"Showing {len(filtered)} of {total_flagged} items")
    st.divider()
else:
    filtered = []
    total_flagged = 0

# ── Flagged queue rendering (uses _render_row_editor helper above) ──────

REASON_CONTEXT = {
    "low parse confidence": {
        "why":  "The merchant name couldn't be clearly identified from the PDF text.",
        "next": "Check the raw description and assign a correct category manually.",
    },
    "cash advance": {
        "why":  "A cash advance was taken on the credit card — these carry high interest (22–30% APR).",
        "next": "Ensure this is repaid quickly. Consider it separate from normal spending.",
    },
    "large debit": {
        "why":  "A single debit over $2,000 was detected — could be a large purchase, payment, or error.",
        "next": "Confirm this is correctly categorized and not a double-import or transfer.",
    },
    "cancelled transaction": {
        "why":  "This transaction was cancelled. It should net to $0 with a matching reversal.",
        "next": "Find the matching reversal in Transactions. If missing, may affect totals.",
    },
    "nsf": {
        "why":  "Non-sufficient funds or returned item — a transaction was rejected by your bank.",
        "next": "Check your account for any associated fees and confirm the original charge was not re-processed.",
    },
    "reviewed": {
        "why":  "Previously reviewed — still flagged for tracking.",
        "next": "Clear the flag if no further action is needed.",
    },
}

_REASON_ORDER = [
    "cash advance",
    "nsf",
    "large debit",
    "cancelled transaction",
    "low parse confidence",
    "reviewed",
]

if flagged:
    groups: dict[str, list] = defaultdict(list)
    for tx in filtered:
        reasons_raw = (tx.get("flag_reason") or "").lower()
        primary = next((k for k in _REASON_ORDER if k in reasons_raw), "other")
        groups[primary].append(tx)

    def _reason_sort_key(k: str) -> int:
        try:
            return _REASON_ORDER.index(k)
        except ValueError:
            return 99

    for reason_key in sorted(groups, key=_reason_sort_key):
        group_txs = groups[reason_key]
        ctx = REASON_CONTEXT.get(reason_key, {
            "why":  reason_key or "Flagged for manual review.",
            "next": "Review this transaction and clear the flag or re-categorize.",
        })
        heading = reason_key.replace("_", " ").title() if reason_key != "other" else "Other"
        st.markdown(
            f'<p class="ledger-section-header">{heading} ({len(group_txs)})</p>',
            unsafe_allow_html=True,
        )
        st.caption(f"{ctx['why']}  ·  **Next:** {ctx['next']}")

        auto_expand = len(group_txs) == 1
        for tx in group_txs:
            label = (
                f"{tx.get('transaction_date','')}  ·  "
                f"{tx.get('merchant') or tx.get('raw_description','')[:40]}  ·  "
                f"${abs(tx.get('amount', 0)):,.2f}  ·  "
                f"{tx.get('category','Uncategorized')}"
            )
            with st.expander(label, expanded=auto_expand):
                _render_row_editor(tx, key_prefix="flag", show_clear_flag=True)

    st.divider()

# ── AI queue: uncategorized / low-confidence rows not already flagged ───
_flagged_ids = {int(t["id"]) for t in flagged}
ai_only = [t for t in ai_candidates if int(t["id"]) not in _flagged_ids]

if ai_only:
    st.markdown(
        f'<p class="ledger-section-header">Uncategorized / Low-Confidence ({len(ai_only)})</p>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Rows the rule-based categorizer wasn't sure about. "
        "Suggest a category (AI, if enabled), accept/reject, or edit directly."
    )
    for tx in ai_only[:50]:
        label = (
            f"{tx.get('transaction_date','')}  ·  "
            f"{tx.get('merchant') or tx.get('raw_description','')[:40]}  ·  "
            f"${abs(tx.get('amount', 0)):,.2f}  ·  "
            f"{tx.get('category') or 'Uncategorized'}"
        )
        with st.expander(label, expanded=False):
            _render_row_editor(tx, key_prefix="aiq", show_clear_flag=False)
    if len(ai_only) > 50:
        st.caption(f"Showing first 50 of {len(ai_only)}. Import fewer rows at a time or accept some suggestions to continue.")
    st.divider()

# ── Bulk actions ────────────────────────────────────────────────────────
if flagged:
    st.subheader("Bulk Actions")
    ba1, ba2 = st.columns(2)

    if not st.session_state.get("confirm_bulk_clear"):
        if ba1.button(
            f"Clear all flags in current filter ({len(filtered)} items)",
            type="secondary",
        ):
            st.session_state.confirm_bulk_clear = True
            st.rerun()
    else:
        st.warning(
            f"This will mark **{len(filtered)} transaction(s)** as reviewed and clear their flags. "
            f"This cannot be undone automatically."
        )
        conf_col, cancel_col = st.columns(2)
        if conf_col.button("Yes, clear all", type="primary"):
            for tx in filtered:
                update_transaction(tx["id"], {"is_flagged": 0, "flag_reason": "reviewed"}, conn=conn)
            conn.commit()
            st.session_state.confirm_bulk_clear = False
            st.success(f"Cleared {len(filtered)} flags.")
            st.rerun()
        if cancel_col.button("Cancel"):
            st.session_state.confirm_bulk_clear = False
            st.rerun()

# ── Legend ──────────────────────────────────────────────────────────────
with st.expander("Why are transactions flagged?"):
    st.markdown("""
| Reason | What it means | What to do |
|--------|---------------|------------|
| low parse confidence | Merchant name unclear from PDF text | Verify category is correct |
| cash advance | Cash taken from credit card — high interest | Repay quickly; confirm amount |
| large debit | Single debit > $2,000 | Confirm not a double-import or transfer |
| cancelled transaction | Cancelled e-transfer | Find matching reversal or zero out |
| nsf | Non-sufficient funds or returned item | Check for fees; confirm no re-processing |
    """)

conn.close()

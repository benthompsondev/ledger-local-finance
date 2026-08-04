"""
Import / Sync — multi-file upload with auto-detection, batch preview, and per-file results.

v3.0 changes:
  - accept_multiple_files=True; upload chequing + Mastercard PDFs in one batch
  - auto-detect statement type per file (no manual type selector for PDFs)
  - batch summary panel before import (file count, detected types, month coverage)
  - per-file expander: parse stats, preview table, insert/skip/flag counts
  - "Preview only" mode still supported
  - unrecognised files fall back to manual type override
  - Windows-safe temp file handling preserved
"""
import streamlit as st
import hashlib
import json
from pathlib import Path
from datetime import date
import pandas as pd
import calendar
import traceback

from utils.database import init_db, get_connection, get_import_log
from utils.categorization_service import preview_import_batch_undo, undo_import_batch
from utils.import_service import persist_import_batch
from utils.insights import coverage_summary
from utils.watcher import get_watch_folder, set_watch_folder, get_pending_files, mark_imported
from utils.platform_utils import temp_pdf, is_valid_directory, watch_folder_placeholder
from parsers.tangerine_chequing   import parse_pdf as parse_chequing
from parsers.tangerine_mastercard import parse_pdf as parse_mc
from parsers.tangerine_savings    import parse_pdf as parse_savings
from parsers.csv_import           import parse_csv
from parsers.detect               import detect_statement_type, detect_label, detect_with_confidence
from utils.styles import inject_styles

st.set_page_config(page_title="Import · Ledger", page_icon="📥", layout="wide")
init_db()
inject_styles()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Import / Sync")
with col_action:
    st.button("＋ Add Data", type="primary", use_container_width=True, disabled=True)

conn = get_connection()

# ══════════════════════════════════════════════════════════════════════
# Import result — persistent completion panel.
# Computed from the database (batches newer than the last completed
# check-in), so it survives reruns and restarts instead of vanishing
# the moment Streamlit refreshes. Ends the task properly: result →
# important issues → what changed → next action.
# ══════════════════════════════════════════════════════════════════════
from utils.import_summary import summarize_import

_imp = summarize_import(conn=conn)
_imp_sig = ",".join(str(b) for b in _imp.get("batch_ids") or [])
if (_imp.get("available")
        and st.session_state.get("import_summary_dismissed") != _imp_sig):

    if _imp["duplicates_only"]:
        _headline = ("Nothing new — everything in these files was "
                     "already imported")
        _accent = "#8b949e"
    else:
        _headline = (f"Imported {_imp['inserted']} new transaction"
                     f"{'s' if _imp['inserted'] != 1 else ''}"
                     + (f" · {_imp['skipped']} duplicate"
                        f"{'s' if _imp['skipped'] != 1 else ''} skipped"
                        if _imp["skipped"] else ""))
        _accent = "#3fb950"

    _period_bits = []
    if _imp["months_complete"]:
        _period_bits.append(
            ", ".join(_imp["months_complete"]) + " now complete")
    if _imp["months_partial"]:
        _period_bits.append(
            ", ".join(_imp["months_partial"]) + " still partial")
    _files_line = " · ".join(
        f"{f['filename']} ({f['inserted']} new)" for f in _imp["files"][:3])
    if len(_imp["files"]) > 3:
        _files_line += f" · +{len(_imp['files']) - 3} more"

    with st.container(border=True):
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:8px'>"
            f"<span style='width:10px;height:10px;border-radius:50%;"
            f"background:{_accent};display:inline-block'></span>"
            f"<span style='font-size:1.15rem;font-weight:800;"
            f"color:#e6edf3'>{_headline}</span></div>"
            f"<div style='font-size:0.85rem;color:#8b949e;margin-top:4px'>"
            f"{_files_line}<br>"
            f"Data through <b style='color:#c9d1d9'>{_imp['data_through']}"
            f"</b>"
            + (" · " + " · ".join(_period_bits) if _period_bits else "")
            + "</div>",
            unsafe_allow_html=True,
        )

        _rev = _imp["review"]
        if _rev["material_count"]:
            _items_txt = "".join(
                f"<li>{(i.get('merchant') or i.get('raw_description') or '?')[:40]}"
                f" · ${abs(float(i.get('amount') or 0)):,.0f}"
                f" · {i.get('category') or 'Uncategorized'}</li>"
                for i in _rev["items"]
            )
            st.markdown(
                f"<div style='background:rgba(227,179,65,0.07);"
                f"border:1px solid rgba(227,179,65,0.3);"
                f"border-left:3px solid #e3b341;border-radius:8px;"
                f"padding:10px 14px;margin:10px 0'>"
                f"<b style='color:#e3b341'>{_rev['material_count']} item"
                f"{'s' if _rev['material_count'] != 1 else ''} worth "
                f"checking</b> "
                f"<span style='color:#c9d1d9'>(${_rev['material_total']:,.0f}"
                f" total) — big enough to move your numbers.</span>"
                f"<ul style='margin:6px 0 0 18px;color:#c9d1d9;"
                f"font-size:0.85rem'>{_items_txt}</ul>"
                f"</div>",
                unsafe_allow_html=True,
            )
        if _rev["deferred_count"]:
            st.caption(
                f"{_rev['deferred_count']} small uncategorized item"
                f"{'s' if _rev['deferred_count'] != 1 else ''} can wait — "
                "they're in Transactions whenever you want them."
            )

        if not _imp["duplicates_only"]:
            st.markdown(
                f"<div style='font-size:0.9rem;color:#c9d1d9;"
                f"margin:4px 0'>This import added "
                f"<b style='color:#3fb950'>${_imp['income_added']:,.0f}"
                f"</b> income and <b style='color:#f97316'>"
                f"${_imp['spending_added']:,.0f}</b> spending "
                f"({_imp['first_date']} → {_imp['last_date']}). "
                f"Transfers and card payments excluded, as always.</div>",
                unsafe_allow_html=True,
            )

        _a1, _a2, _a3 = st.columns([1.6, 1.6, 3])
        with _a1:
            if st.button("Run weekly check-in →", type="primary",
                         use_container_width=True,
                         key="import_run_checkin"):
                st.switch_page("pages/0_Home.py")
        with _a2:
            if _rev["material_count"]:
                if st.button(f"Review {_rev['material_count']} item"
                             f"{'s' if _rev['material_count'] != 1 else ''}",
                             use_container_width=True,
                             key="import_review_material"):
                    st.session_state["review_batch_ids"] = list(_imp["batch_ids"])
                    st.switch_page("pages/8_Review.py")
            else:
                st.caption("Nothing needs review.")
        with _a3:
            if st.button("Dismiss", key="import_summary_dismiss"):
                st.session_state["import_summary_dismissed"] = _imp_sig
                st.rerun()

        if len(_imp["batch_ids"]) == 1:
            _undo_id = int(_imp["batch_ids"][0])
            if st.button("Undo this import…", key="import_completion_undo"):
                st.session_state["import_undo_confirm_id"] = _undo_id
                st.rerun()

            if st.session_state.get("import_undo_confirm_id") == _undo_id:
                _undo_preview = preview_import_batch_undo(_undo_id, conn=conn)
                if _undo_preview["available"]:
                    _edit_warning = (
                        f" **Warning:** {_undo_preview['edited_count']} transaction(s) "
                        "were edited after import."
                        if _undo_preview["edited_count"] else ""
                    )
                    _rule_note = (
                        f" {_undo_preview['rule_count']} saved merchant rule(s) "
                        "came from this batch and will be preserved."
                        if _undo_preview["rule_count"] else ""
                    )
                    st.warning(
                        f"**Confirm import undo**\n\n"
                        f"- File: **{_undo_preview['filename']}**\n"
                        f"- Imported: {_undo_preview['imported_at']}\n"
                        f"- Transactions: {_undo_preview['transaction_count']}\n"
                        f"- Income / spending: ${_undo_preview['income']:,.2f} / "
                        f"${_undo_preview['spending']:,.2f}\n"
                        f"- Date range: {_undo_preview['first_date'] or 'none'} to "
                        f"{_undo_preview['last_date'] or 'none'}\n\n"
                        "Only records owned by this batch will be removed."
                        f"{_edit_warning}{_rule_note}"
                    )
                    _edited_ack = True
                    if _undo_preview["edited_count"]:
                        _edited_ack = st.checkbox(
                            "I understand that my edited transaction rows "
                            "will be removed.",
                            key="import_completion_undo_edited_ack",
                        )
                    _uc1, _uc2, _ = st.columns([1.6, 1, 4])
                    if _uc1.button("Confirm undo", type="primary",
                                   key="import_completion_undo_confirm",
                                   disabled=not _edited_ack):
                        _undo_result = undo_import_batch(_undo_id, conn=conn)
                        conn.commit()
                        st.session_state.pop("import_undo_confirm_id", None)
                        st.session_state.pop("import_summary_dismissed", None)
                        st.session_state["import_delete_msg"] = (
                            f"Undid {_undo_result.get('filename', 'import')} — "
                            f"{_undo_result.get('transactions_deleted', 0)} transaction(s) removed."
                        )
                        st.rerun()
                    if _uc2.button("Cancel", key="import_completion_undo_cancel"):
                        st.session_state.pop("import_undo_confirm_id", None)
                        st.rerun()
                else:
                    st.info("This import was already undone.")
                    st.session_state.pop("import_undo_confirm_id", None)

    st.divider()

# ── Coverage summary ────────────────────────────────────────────────────
cov = coverage_summary(conn=conn)
st.subheader("Statement Coverage")

if cov["months"]:
    month_set = set(cov["months"])
    gap_set   = set(cov["gap_months"])
    all_range = sorted(set(cov["months"] + cov["gap_months"]))

    cols = st.columns(min(12, len(all_range)))
    for j, mo in enumerate(all_range):
        is_gap = mo in gap_set
        bg = "#da3633" if is_gap else "#238636"
        label = f"{'⚠ ' if is_gap else ''}{mo}"
        cols[j % 12].markdown(
            f"<div style='background:{bg};color:white;text-align:center;padding:4px 2px;"
            f"border-radius:4px;font-size:11px;margin-bottom:4px'>{label}</div>",
            unsafe_allow_html=True,
        )

    meta = f"Coverage: **{cov['first_month']}** → **{cov['last_month']}** · {cov['total_months']} months imported"
    if gap_set:
        meta += f" · ⚠️ {len(gap_set)} gap(s) — import older PDFs to fill"
    st.caption(meta)
else:
    st.info("No data imported yet. Upload your first statement below.")

st.divider()

# ── Tab layout ──────────────────────────────────────────────────────────
tab_upload, tab_manual, tab_watcher, tab_history = st.tabs(
    ["Upload Files", "Add Manually", "Watch Folder", "Import History"])

# ══════════════════════════════════════════════════════════════════════════
# Tab 1: Upload
# ══════════════════════════════════════════════════════════════════════════
with tab_upload:

    # Pass 19: structure the upload tab around three audiences so non-
    # Tangerine users have a clear lane. The actual upload handler below
    # is unchanged — Tangerine PDF parsing is bit-for-bit identical to
    # before.
    st.markdown(
        "<p class='ledger-muted'>Drop any mix of Tangerine Chequing, Savings, and Mastercard PDFs "
        "— or CSV files — in a single batch. Statement type is detected automatically.</p>",
        unsafe_allow_html=True,
    )

    with st.expander("📘 Which import path is right for me?", expanded=False):
        st.markdown(
            "**1. Tangerine PDFs — best supported.** Drop your Chequing, "
            "Savings, and Mastercard statement PDFs directly. Type is "
            "auto-detected per-file with high confidence; the parser is "
            "purpose-built for Tangerine's three statement layouts.\n\n"
            "**2. Other bank CSV — recommended for non-Tangerine users.** "
            "Most Canadian banks (TD, RBC, Scotia, BMO, CIBC, Simplii, "
            "Wealthsimple) let you export transactions as CSV. Drop the "
            "CSV here — Ledger detects common column aliases (`Date` / "
            "`Transaction Date`, `Description` / `Memo` / `Payee`, "
            "`Amount` or split `Debit` / `Credit`, etc.). If columns "
            "don't match cleanly, the preview will show what was parsed; "
            "you can still import and fix categories in the Review queue.\n\n"
            "**3. Other bank PDFs — experimental.** Generic bank PDF "
            "parsing isn't shipping yet (every bank's PDF layout differs "
            "and a wrong parser silently corrupts data). For now, please "
            "use your bank's CSV export. If your bank only offers PDFs "
            "and you'd like Ledger to support that statement format, "
            "send the file format details and a redacted sample.\n\n"
            "All imports stay on this computer. Nothing is uploaded."
        )

    uploaded_files = st.file_uploader(
        "Drag and drop PDF or CSV statement files here",
        type=["pdf", "csv"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    # ── Options row ────────────────────────────────────────────────────
    # Account identity now comes from real accounts (selected per file
    # below); the old free-text label is retired.
    account_id = None
    preview_only = st.checkbox("Preview only — don't import yet",
                               value=False)

    from utils.account_picker import (
        account_selectbox, render_new_account_form,
        apply_account_to_transactions, DETECTED_TYPE_TO_ACCOUNT_TYPE,
    )
    from utils.database import list_accounts as _list_accounts

    _active_accounts = [a for a in _list_accounts(conn=conn)
                        if not a.get("is_archived")]
    if not _active_accounts:
        st.info(
            "**First, create the account these transactions belong to.** "
            "Every transaction in Ledger lives in an account so balances "
            "and duplicates stay trustworthy.",
            icon="🏦",
        )
        if render_new_account_form(conn, key="upload_first"):
            st.rerun()
    else:
        with st.expander("➕ Create another account", expanded=False):
            if render_new_account_form(conn, key="upload_more"):
                st.rerun()

    st.caption(
        "Supports: Tangerine Chequing, Savings, and Mastercard PDFs + CSV files. "
        "CC payments and savings ↔ chequing transfers are automatically excluded from cashflow totals."
    )

    # ── If files are staged, show batch scan ──────────────────────────
    if uploaded_files:

        # ── Step 1: scan each file to detect type + read bytes ────────
        file_infos = []
        for uf in uploaded_files:
            raw = uf.read()
            uf.seek(0)   # reset so parsers can read again
            file_hash = hashlib.md5(raw).hexdigest()

            # Write to temp just for detection (pdfplumber reads page 1-2 only)
            detected    = "unknown"
            confidence  = "low"
            csv_headers = []
            csv_sample  = []
            csv_needs_mapping = False
            csv_profile = None
            with temp_pdf(uf.name, raw) as tmp:
                detected, confidence = detect_with_confidence(tmp)
                if detected == "csv":
                    # Does the automatic parser actually understand this
                    # file? If not, the user maps columns once and we
                    # remember the mapping by header signature.
                    from utils.csv_mapping import (
                        csv_preview, probe_csv, find_import_profile,
                    )
                    _pv = csv_preview(tmp)
                    csv_headers = _pv.get("headers") or []
                    csv_sample = _pv.get("rows") or []
                    if csv_headers:
                        csv_profile = find_import_profile(csv_headers,
                                                          conn=conn)
                        if csv_profile is None:
                            csv_needs_mapping = probe_csv(tmp)["needs_mapping"]

            file_infos.append({
                "uploaded_file": uf,
                "raw":           raw,
                "hash":          file_hash,
                "detected":      detected,
                "confidence":    confidence,
                "label":         detect_label(detected),
                "csv_headers":   csv_headers,
                "csv_sample":    csv_sample,
                "csv_needs_mapping": csv_needs_mapping,
                "csv_profile":   csv_profile,
                "csv_mapping":   (csv_profile or {}).get("mapping"),
            })

        # Flag files whose MD5 hash already exists in import_log
        existing_hashes = {
            r[0] for r in conn.execute(
                "SELECT file_hash FROM import_log WHERE file_hash IS NOT NULL"
            ).fetchall()
        }
        for fi in file_infos:
            fi["already_imported"] = fi["hash"] in existing_hashes

        # ── Step 2: batch summary banner ─────────────────────────────
        st.markdown('<p class="ledger-section-header">Batch Summary</p>', unsafe_allow_html=True)

        n_chq = sum(1 for f in file_infos if f["detected"] == "chequing")
        n_sav = sum(1 for f in file_infos if f["detected"] == "savings")
        n_mc  = sum(1 for f in file_infos if f["detected"] == "mastercard")
        n_csv = sum(1 for f in file_infos if f["detected"] == "csv")
        n_unk = sum(1 for f in file_infos if f["detected"] == "unknown")

        bsc1, bsc2, bsc3, bsc4, bsc5 = st.columns(5)
        bsc1.metric("Files staged",      len(file_infos))
        bsc2.metric("Chequing PDFs",     n_chq)
        bsc3.metric("Savings PDFs",      n_sav)
        bsc4.metric("Mastercard PDFs",   n_mc)
        bsc5.metric("CSV / Unknown",     n_csv + n_unk)

        # ── Manual override: show for unknown files + low-confidence detections ──
        _OVERRIDE_MAP = {
            "Tangerine Chequing PDF":   "chequing",
            "Tangerine Savings PDF":    "savings",
            "Tangerine Mastercard PDF": "mastercard",
            "Generic CSV":              "csv",
        }
        _TYPE_TO_LABEL = {v: k for k, v in _OVERRIDE_MAP.items()}

        needs_override = [
            f for f in file_infos
            if f["detected"] == "unknown" or f["confidence"] == "low"
        ]
        if needs_override:
            with st.expander(
                f"⚠️ {len(needs_override)} file(s) need type confirmation",
                expanded=True,
            ):
                st.caption(
                    "These files had low-confidence auto-detection. "
                    "Confirm or correct the detected type before importing."
                )
                for fi in needs_override:
                    uf = fi["uploaded_file"]
                    current_label = _TYPE_TO_LABEL.get(fi["detected"], "Tangerine Chequing PDF")
                    opts = list(_OVERRIDE_MAP.keys())
                    default_idx = opts.index(current_label) if current_label in opts else 0
                    override = st.selectbox(
                        f"{uf.name}  — detected: {fi['label']} ({fi['confidence']} confidence)",
                        opts,
                        index=default_idx,
                        key=f"override_{uf.name}",
                    )
                    fi["detected"] = _OVERRIDE_MAP[override]
                    fi["label"]    = detect_label(fi["detected"])

        # ── Step 2.5: CSV column mapping (only when auto-parse fails) ──
        # A CSV whose headers match a saved profile maps silently. A CSV
        # the alias parser can't read gets a one-time mapping editor;
        # the mapping can be saved and is auto-applied to future files
        # with the same header signature.
        _needs_map = [f for f in file_infos
                      if f["detected"] == "csv" and f["csv_needs_mapping"]
                      and not f["csv_mapping"]]
        _profiled = [f for f in file_infos
                     if f["detected"] == "csv" and f["csv_profile"]]
        for fi in _profiled:
            st.caption(
                f"🧩 **{fi['uploaded_file'].name}** — using your saved "
                f"mapping “{fi['csv_profile'].get('name', 'profile')}”."
            )
        if _needs_map:
            import pandas as _pd_map
            from utils.csv_mapping import validate_mapping as _validate_map
            st.markdown(
                '<p class="ledger-section-header">Map CSV Columns</p>',
                unsafe_allow_html=True)
            st.caption(
                "Ledger couldn't recognize these files' columns "
                "automatically. Point it at the right ones once — it "
                "remembers the mapping for future files from this bank."
            )
            for fi in _needs_map:
                _h = fi["csv_headers"]
                _k = fi["hash"][:10]
                with st.expander(f"🧩 {fi['uploaded_file'].name}",
                                 expanded=True):
                    if fi["csv_sample"]:
                        st.dataframe(
                            _pd_map.DataFrame(fi["csv_sample"], columns=_h),
                            use_container_width=True, hide_index=True)
                    mc1, mc2, mc3 = st.columns(3)
                    _date_col = mc1.selectbox("Date column", _h,
                                              key=f"map_date_{_k}")
                    _desc_col = mc2.selectbox("Description column", _h,
                                              key=f"map_desc_{_k}")
                    _mode = mc3.radio(
                        "Amounts are…",
                        ["signed", "split"],
                        format_func=lambda v: (
                            "One column (+/−)" if v == "signed"
                            else "Two columns (in / out)"),
                        key=f"map_mode_{_k}", horizontal=True)
                    if _mode == "signed":
                        _amount_col = st.selectbox("Amount column", _h,
                                                   key=f"map_amt_{_k}")
                        st.caption(
                            "Signed source amounts are preserved: positive is "
                            "money in and negative is money out."
                        )
                        _mapping = {"date_col": _date_col,
                                    "desc_col": _desc_col,
                                    "amount_mode": "signed",
                                    "amount_col": _amount_col,
                                    "positive_is": "credit"}
                    else:
                        ma1, ma2 = st.columns(2)
                        _debit_col = ma1.selectbox(
                            "Money-out column (withdrawals)", _h,
                            key=f"map_debit_{_k}")
                        _credit_col = ma2.selectbox(
                            "Money-in column (deposits)", _h,
                            key=f"map_credit_{_k}")
                        _mapping = {"date_col": _date_col,
                                    "desc_col": _desc_col,
                                    "amount_mode": "split",
                                    "debit_col": _debit_col,
                                    "credit_col": _credit_col}
                    _problems = _validate_map(_mapping, _h)
                    if _problems:
                        for p in _problems:
                            st.caption("⚠ " + p)
                    else:
                        fi["csv_mapping"] = _mapping
                        sp1, sp2 = st.columns([1, 2])
                        fi["csv_save_profile"] = sp1.checkbox(
                            "Remember this mapping",
                            value=True, key=f"map_save_{_k}")
                        fi["csv_profile_name"] = sp2.text_input(
                            "Profile name",
                            value=fi["uploaded_file"].name.rsplit(".", 1)[0],
                            key=f"map_name_{_k}",
                            label_visibility="collapsed")

        # ── Step 2.7: destination accounts ────────────────────────────
        # The user must know which account receives each file BEFORE
        # anything persists. Pre-selected by detected statement type or
        # the CSV profile's remembered account — always visible, always
        # changeable, never silent.
        if _active_accounts:
            st.markdown(
                '<p class="ledger-section-header">Destination Accounts</p>',
                unsafe_allow_html=True)
            for fi in file_infos:
                _want_type = DETECTED_TYPE_TO_ACCOUNT_TYPE.get(
                    fi["detected"])
                _suggest = None
                _prof = fi.get("csv_profile") or {}
                _prof_acct = _prof.get("suggested_account_ref")
                if _prof_acct and any(a["id"] == _prof_acct
                                      for a in _active_accounts):
                    _suggest = _prof_acct
                elif _want_type:
                    _match = next((a for a in _active_accounts
                                   if a["type"] == _want_type), None)
                    _suggest = _match["id"] if _match else None
                fi["dest_account"] = account_selectbox(
                    conn, key=f"dest_{fi['hash'][:10]}",
                    label=f"→ {fi['uploaded_file'].name}",
                    suggested_id=_suggest,
                    help_text="Which account these transactions belong "
                              "to.")
                if (fi["dest_account"] and _want_type
                        and fi["dest_account"]["type"] != _want_type):
                    from utils.database import ACCOUNT_TYPE_LABELS as _ATL
                    st.warning(
                        f"“{fi['uploaded_file'].name}” looks like a "
                        f"**{_ATL.get(_want_type, _want_type)}** "
                        f"statement, but the selected account "
                        f"“{fi['dest_account']['name']}” is a "
                        f"{_ATL.get(fi['dest_account']['type'])}. "
                        f"Ledger will import wherever you point it — "
                        f"double-check before importing.",
                        icon="⚠️",
                    )
        else:
            for fi in file_infos:
                fi["dest_account"] = None
            st.warning("Create an account above before importing — "
                       "staged files need a destination.")

        # ── Honest path for unsupported files ─────────────────────────
        _unsupported = [f for f in file_infos if f["detected"] == "unknown"]
        if _unsupported:
            st.info(
                "**Some files aren't in a format Ledger can parse yet.** "
                "PDF layouts differ per bank, and guessing silently "
                "corrupts data — so Ledger won't try. What works instead: "
                "**(1)** export the same transactions as CSV from your "
                "bank's website and drop that here (you can map its "
                "columns above if needed), or **(2)** enter the few "
                "transactions that matter in the **Add Manually** tab. "
                "If you'd like your bank's PDF supported properly, keep "
                "a redacted sample.",
                icon="📄",
            )

        # ── Step 3: per-file preview rows ─────────────────────────────
        st.markdown('<p class="ledger-section-header">Files to Import</p>', unsafe_allow_html=True)

        for fi in file_infos:
            icon = {"chequing": "🏦", "savings": "💰", "mastercard": "💳", "csv": "📄"}.get(fi["detected"], "❓")
            conf = fi.get("confidence", "high")
            conf_color = {"high": "#34d058", "medium": "#f59e0b", "low": "#ef4444"}.get(conf, "#8b949e")
            conf_badge = (
                f"<span style='font-size:0.68rem;font-weight:700;color:{conf_color};"
                f"border:1px solid {conf_color};border-radius:3px;"
                f"padding:1px 5px;margin-left:6px;opacity:0.85'>{conf.upper()}</span>"
            )
            already_badge = (
                "<span style='font-size:0.68rem;font-weight:700;color:#f59e0b;"
                "border:1px solid #f59e0b;border-radius:3px;"
                "padding:1px 5px;margin-left:6px;opacity:0.85'>ALREADY IMPORTED</span>"
                if fi.get("already_imported") else ""
            )
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;padding:6px 0;"
                f"border-bottom:1px solid rgba(255,255,255,0.06)'>"
                f"<span style='font-size:1.1rem'>{icon}</span>"
                f"<span style='font-weight:500'>{fi['uploaded_file'].name}</span>"
                f"<span style='color:#8b949e;font-size:0.78rem;margin-left:4px'>{fi['label']}</span>"
                f"{conf_badge}{already_badge}"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='margin-bottom:0.8rem'></div>", unsafe_allow_html=True)

        # ── Step 4: action button ─────────────────────────────────────
        btn_label = "Preview All" if preview_only else f"Import {len(file_infos)} File(s)"
        do_import = st.button(btn_label, type="primary")

        if do_import:
            total_in = total_sk = total_fl = 0

            st.markdown('<p class="ledger-section-header">Results</p>', unsafe_allow_html=True)

            for fi in file_infos:
                uf         = fi["uploaded_file"]
                raw        = fi["raw"]
                file_hash  = fi["hash"]
                dtype      = fi["detected"]
                account_type_str = fi["label"]

                result      = None
                parse_error = None

                try:
                    with temp_pdf(uf.name, raw) as tmp_path:
                        if dtype == "chequing":
                            result = parse_chequing(tmp_path, statement_period=None)
                        elif dtype == "savings":
                            result = parse_savings(tmp_path, statement_period=None)
                        elif dtype == "mastercard":
                            result = parse_mc(tmp_path, statement_period=None)
                        elif dtype == "csv":
                            if fi.get("csv_mapping"):
                                from utils.csv_mapping import parse_csv_mapped
                                _csv_dest = fi.get("dest_account") or {}
                                _saved_profile = fi.get("csv_profile") or {}
                                _saved_type = str(_saved_profile.get("account_type") or "")
                                if _saved_profile and (
                                    not _saved_type or _saved_type != _csv_dest.get("type")
                                ):
                                    raise ValueError(
                                        "The saved CSV profile is missing or conflicts "
                                        "with this destination account type. Reset or "
                                        "relearn the profile before importing."
                                    )
                                result = parse_csv_mapped(
                                    tmp_path, fi["csv_mapping"],
                                    account_type=_csv_dest.get("type") or "csv",
                                    account_id=account_id or None,
                                    statement_period=None)
                            elif fi.get("csv_needs_mapping"):
                                raise ValueError(
                                    f"'{uf.name}' needs its columns mapped "
                                    f"first — use the Map CSV Columns "
                                    f"section above, then import again.")
                            else:
                                _csv_dest = fi.get("dest_account") or {}
                                result = parse_csv(
                                    tmp_path,
                                    account_type=_csv_dest.get("type") or "csv",
                                    account_id=account_id or None,
                                    statement_period=None)
                        else:
                            raise ValueError(
                                f"'{uf.name}' isn't a supported format. "
                                f"Export a CSV from your bank instead, or "
                                f"use the Add Manually tab.")

                except Exception as exc:
                    parse_error = exc

                # Determine icon + header label
                status_prefix = "Preview:" if preview_only else "✓"
                icon = {"chequing": "🏦", "savings": "💰", "mastercard": "💳", "csv": "📄"}.get(dtype, "❓")
                _parsed_count = len(result.get("transactions", [])) if result else 0
                _period_str   = result.get("statement_period", "—") if result else "—"
                _expander_suffix = (
                    f"{_parsed_count} tx  ·  {_period_str}" if _parsed_count > 0
                    else "0 transactions — check type"
                )

                with st.expander(
                    f"{icon} {status_prefix} {uf.name}  [{fi['label']}]  ·  {_expander_suffix}",
                    expanded=True,
                ):
                    if parse_error is not None:
                        st.error(
                            f"**Could not parse `{uf.name}`**\n\n"
                            f"{parse_error}\n\n"
                            "Common causes:\n"
                            "- Wrong type detected (use manual override above)\n"
                            "- Scanned/image PDF (only digital PDFs supported)\n"
                            "- Corrupted or password-protected file"
                        )
                        continue

                    txs    = result.get("transactions", [])

                    # Every transaction needs a destination account
                    # before persistence — no account, no import.
                    _dest = fi.get("dest_account")
                    if txs and not preview_only and not _dest:
                        st.error(
                            f"**`{uf.name}` has no destination account.** "
                            "Select one in the Destination Accounts "
                            "section above, then import again."
                        )
                        continue
                    if txs and _dest:
                        # Tangerine PDF parsers set account_type per row
                        # and statement-summary lookups depend on it —
                        # preserve it for PDFs; CSVs take the account's
                        # own type token.
                        apply_account_to_transactions(
                            txs, _dest,
                            keep_account_type=(dtype in
                                               ("chequing", "savings",
                                                "mastercard")))
                        from utils.financial_semantics import (
                            consolidate_material_review, promote_recurring_income,
                        )
                        promote_recurring_income(txs)
                        consolidate_material_review(txs)
                    errors = result.get("errors", [])
                    stats  = result.get("stats", {})
                    period = result.get("statement_period", "")

                    # Stats row
                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("Parsed",   stats.get("total_parsed", 0))
                    m2.metric("Debits",   stats.get("debits",  0))
                    m3.metric("Credits",  stats.get("credits", 0))
                    m4.metric("Flagged",  stats.get("flagged", 0))
                    m5.metric("Period",   period or "—")

                    if errors:
                        with st.expander(f"{len(errors)} parse warning(s)"):
                            for e in errors[:8]:
                                st.caption(f"• {e}")

                    # Pass 19: show detected column mapping for CSV files
                    # so non-Tangerine users can confirm Ledger picked
                    # the right date / description / amount columns
                    # before they trust the import.
                    if dtype == "csv" and txs:
                        sample = txs[0]
                        st.caption(
                            "🔍 **Detected mapping** — date: "
                            f"`transaction_date`, description: "
                            f"`raw_description`, amount: `amount`. "
                            f"First parsed row: "
                            f"{sample.get('transaction_date','?')} · "
                            f"{(sample.get('raw_description','?') or '')[:40]} · "
                            f"${abs(sample.get('amount',0)):,.2f} "
                            f"({sample.get('direction','?')}). "
                            "If the date or amount look wrong, your CSV "
                            "may need column renaming before re-upload."
                        )

                    if not txs:
                        st.error(
                            f"**No transactions parsed from `{uf.name}`** — "
                            f"detected as: {fi['label']}\n\n"
                            "**What to do:** If the file type above looks wrong, "
                            "re-upload and use the override selector that appears "
                            "for any low-confidence detection. "
                            "Also check that this is a digital PDF (not a scanned image)."
                        )
                        continue

                    # Preview table (first 12 rows)
                    preview_rows = []
                    for tx in txs[:12]:
                        preview_rows.append({
                            "Date":        tx.get("transaction_date", ""),
                            "Description": (tx.get("raw_description", "") or "")[:45],
                            "Category":    tx.get("category", ""),
                            "Source":      tx.get("category_source", ""),
                            "Why":         tx.get("category_explanation", ""),
                            "Amount":      f"${abs(tx.get('amount', 0)):,.2f}",
                            "Direction":   tx.get("direction", ""),
                            "Confidence":  tx.get("parse_confidence", ""),
                        })
                    st.dataframe(
                        pd.DataFrame(preview_rows),
                        use_container_width=True,
                        hide_index=True,
                    )
                    if len(txs) > 12:
                        st.caption(f"... and {len(txs) - 12} more rows not shown")

                    # ── Actual insert ──────────────────────────────────
                    if not preview_only:
                        try:
                            _saved = persist_import_batch(
                                {
                                    "filename": uf.name,
                                    "file_hash": file_hash,
                                    "account_type": account_type_str,
                                    "statement_period": period,
                                    "rows_parsed": stats.get("total_parsed", 0),
                                    "errors": errors,
                                },
                                txs,
                                account_id=account_id or None,
                                statement_summary=(result or {}).get(
                                    "statement_summary"
                                ) or None,
                                statement_account_type=dtype,
                                conn=conn,
                            )
                            conn.commit()
                        except Exception as _persist_error:
                            conn.rollback()
                            st.error(
                                f"**Nothing from `{uf.name}` was saved.** "
                                "Ledger rolled back the import because its "
                                f"database write failed "
                                f"({type(_persist_error).__name__})."
                            )
                            continue

                        batch_id = _saved["batch_id"]
                        inserted = _saved["inserted"]
                        skipped = _saved["skipped"]
                        flagged = _saved["flagged"]
                        total_in += inserted
                        total_sk += skipped
                        total_fl += flagged

                        # A user-built mapping that just imported
                        # successfully is worth remembering: future
                        # files with the same headers map silently.
                        if (fi.get("csv_mapping")
                                and not fi.get("csv_profile")
                                and fi.get("csv_save_profile")):
                            try:
                                from utils.csv_mapping import save_import_profile
                                _pid = save_import_profile(
                                    fi.get("csv_profile_name")
                                    or uf.name.rsplit(".", 1)[0],
                                    fi["csv_headers"],
                                    fi["csv_mapping"],
                                    account_type=(fi.get("dest_account") or {}).get("type") or "",
                                    suggested_account_ref=(fi.get("dest_account") or {}).get("id"),
                                    conn=conn,
                                )
                                # Remember the account as a SUGGESTION
                                # only — always shown, always changeable.
                                if _pid and fi.get("dest_account"):
                                    conn.execute(
                                        "UPDATE import_profiles SET "
                                        "suggested_account_ref=? WHERE id=?",
                                        (fi["dest_account"]["id"], _pid),
                                    )
                                conn.commit()
                                st.caption(
                                    "🧩 Mapping saved — files with these "
                                    "columns will import automatically "
                                    "from now on."
                                )
                            except Exception:
                                pass

                        # ── Per-file import summary ────────────────────────
                        s1, s2, s3, s4 = st.columns(4)
                        s1.success(f"✓ {inserted} new transactions")
                        if skipped:
                            s2.warning(f"{skipped} duplicates skipped")
                        if flagged:
                            s3.warning(f"{flagged} flagged for review")
                        st.markdown(
                            f"<div style='font-size:0.75rem;color:#8b949e;margin-top:4px'>"
                            f"File: <b style='color:#c9d1d9'>{uf.name}</b>  ·  "
                            f"Type: <b style='color:#c9d1d9'>{fi['label']}</b>  ·  "
                            f"Parser: <b style='color:#c9d1d9'>{dtype}</b>  ·  "
                            f"Period: <b style='color:#c9d1d9'>{_period_str}</b>  ·  "
                            f"Credits: <b style='color:#34d058'>{stats.get('credits',0)}</b>  ·  "
                            f"Debits: <b style='color:#f97316'>{stats.get('debits',0)}</b>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

            # ── Batch-level summary ────────────────────────────────────
            if not preview_only and total_in + total_sk > 0:
                # Clear any dismissed marker so the persistent Import
                # result panel (top of page) shows for THIS import, then
                # rerun into it. The panel is DB-driven, so nothing is
                # lost in the rerun — that's where the completion story,
                # material review items, and next actions live now.
                st.session_state.pop("import_summary_dismissed", None)
                st.rerun()
            elif preview_only:
                st.info("Preview complete — no data was written. Uncheck 'Preview only' to import.")

# ══════════════════════════════════════════════════════════════════════════
# Tab 2: Watch Folder
# ══════════════════════════════════════════════════════════════════════════
with tab_manual:
    # Manual entries are first-class citizens: they persist through the
    # same atomic batch path as files, so they get duplicate protection,
    # provenance, batch undo, and the import-result panel for free.
    st.markdown(
        "Type in transactions Ledger can't import — cash spending, an "
        "unsupported bank, or a one-off correction. Entries show up "
        "everywhere imported ones do, and can be removed from Import "
        "History like any batch."
    )
    from config.categories import CATEGORIES as _ALL_CATS
    from datetime import date as _date_m
    from utils.account_picker import (
        account_selectbox as _acct_select,
        render_new_account_form as _new_acct_form,
    )

    # Real account selection — never an empty selector: with no
    # accounts yet, guide creation first.
    _m_account = _acct_select(
        conn, key="manual_account", label="Account",
        help_text="Which account this money moved through. Create a "
                  "'Cash' account for wallet spending.")
    if _m_account is None:
        st.info("Create your first account to start adding "
                "transactions.", icon="🏦")
        if _new_acct_form(conn, key="manual_first"):
            st.rerun()
    else:
        with st.expander("➕ New account", expanded=False):
            if _new_acct_form(conn, key="manual_more"):
                st.rerun()

    with st.form("manual_tx_form", clear_on_submit=True):
        mf1, mf2 = st.columns([1.2, 2.4])
        _m_date = mf1.date_input("Date", value=_date_m.today(),
                                 format="YYYY-MM-DD")
        _m_desc = mf2.text_input(
            "Description*", placeholder="e.g. Farmers market — cash")
        mf3, mf4 = st.columns([1.2, 1.4])
        _m_amount = mf3.number_input("Amount ($)*", min_value=0.0,
                                     step=1.0, format="%.2f")
        _is_liability_acct = bool(
            _m_account
            and _m_account.get("type") in ("credit_card", "loan",
                                           "mortgage", "other_liability"))
        _m_dir = mf4.radio(
            "This was…", ["debit", "credit"],
            format_func=lambda v: (
                ("A purchase / charge" if v == "debit"
                 else "A payment / refund") if _is_liability_acct
                else ("Money out" if v == "debit" else "Money in")),
            horizontal=True)
        _m_cat = st.selectbox(
            "Category (optional — Ledger categorizes automatically)",
            ["(automatic)"] + _ALL_CATS)
        _m_submit = st.form_submit_button(
            "＋ Add transaction", type="primary",
            disabled=_m_account is None)

    if _m_submit:
        if not _m_desc.strip() or _m_amount <= 0:
            st.error("A description and an amount above zero are "
                     "required.")
        else:
            from utils.categorizer import enrich_transaction
            _m_tx = {
                "account_type": _m_account["type"],
                "account_ref": int(_m_account["id"]),
                "account_id": _m_account["name"],
                "transaction_date": _m_date.isoformat(),
                "raw_description": _m_desc.strip(),
                "amount": float(_m_amount),
                "direction": _m_dir,
                "currency": "CAD",
                "parse_confidence": "high",
                "statement_period": _m_date.strftime("%Y-%m"),
            }
            if _m_cat != "(automatic)":
                _m_tx["category"] = _m_cat
                _m_tx["category_source"] = "user_edit"
            enrich_transaction(_m_tx)
            try:
                _m_saved = persist_import_batch(
                    {
                        "filename": "Manual entry",
                        "account_type": "Manual",
                        "statement_period": _m_date.strftime("%Y-%m"),
                        "rows_parsed": 1,
                    },
                    [_m_tx],
                    conn=conn,
                )
                conn.commit()
            except Exception as _m_err:
                conn.rollback()
                st.error(f"Nothing was saved — the database write "
                         f"failed ({type(_m_err).__name__}).")
            else:
                if _m_saved["inserted"]:
                    st.session_state.pop("import_summary_dismissed", None)
                    st.success(
                        f"Added: {_m_desc.strip()} · "
                        f"${float(_m_amount):,.2f} "
                        f"({'money out' if _m_dir == 'debit' else 'money in'}"
                        f" · {_m_account['name']}) — category "
                        f"“{_m_tx.get('category') or 'Uncategorized'}”."
                    )
                else:
                    st.warning(
                        "An identical entry (same account, date, "
                        "description, and amount) is already recorded, "
                        "so this one was skipped. If it's genuinely a "
                        "second purchase, tweak the description — e.g. "
                        "add “#2”."
                    )

    # Recent manual entries, for quick confidence and corrections.
    _recent_manual = conn.execute(
        """
        SELECT t.transaction_date, t.raw_description, t.amount,
               t.direction, t.category,
               COALESCE(a.name, t.account_type) AS account_label
        FROM transactions t
        JOIN import_log l ON l.id = t.import_batch_id
        LEFT JOIN accounts a ON a.id = t.account_ref
        WHERE l.filename = 'Manual entry'
        ORDER BY t.transaction_date DESC, t.id DESC LIMIT 8
        """
    ).fetchall()
    if _recent_manual:
        st.markdown("**Recent manual entries**")
        st.dataframe(
            pd.DataFrame([{
                "Date": r["transaction_date"],
                "Description": r["raw_description"],
                "Amount": f"${abs(float(r['amount'] or 0)):,.2f}",
                "In/Out": ("Money in" if r["direction"] == "credit"
                           else "Money out"),
                "Category": r["category"] or "Uncategorized",
                "Account": r["account_label"],
            } for r in _recent_manual]),
            use_container_width=True, hide_index=True)
        st.caption(
            "Wrong entry? Remove its batch under Import History, or "
            "edit the row in Transactions."
        )

with tab_watcher:
    st.markdown("""
    Set a folder on your computer where Tangerine statement PDFs are saved.
    The app checks for new files each time this page loads.

    **Windows example paths:**
    - `C:\\Users\\YourName\\Documents\\Statements`
    - `C:\\Users\\YourName\\Downloads`

    **Optional:** Install `watchdog` for live file detection (works on Windows):
    ```
    pip install watchdog
    ```
    Without it, the folder is checked on each page load — which is fine for normal use.
    """)

    current_folder = get_watch_folder() or ""
    placeholder = watch_folder_placeholder()
    new_folder = st.text_input(
        "Watch folder path",
        value=current_folder,
        placeholder=placeholder,
    )
    wf1, wf2 = st.columns(2)
    if wf1.button("Save watch folder"):
        if new_folder and is_valid_directory(new_folder):
            set_watch_folder(new_folder)
            st.success(f"Watching: {new_folder}")
            st.rerun()
        elif not new_folder:
            set_watch_folder(None)
            st.info("Watcher disabled.")
        else:
            st.error(
                "Folder not found. On Windows, check that:\n"
                "- The path uses backslashes or forward slashes (both work)\n"
                "- The folder actually exists\n"
                "- You have permission to read it"
            )
    if wf2.button("Clear"):
        set_watch_folder(None)
        st.info("Watch folder cleared.")

    pending = get_pending_files()
    if pending:
        st.subheader("Detected Files")
        for f in pending:
            status_icon = {"new": "🟢", "imported": "✅", "skipped": "⏭️"}.get(f["status"], "⚪")
            fc1, fc2, fc3 = st.columns([4, 1, 2])
            fc1.write(f"{status_icon} **{f['filename']}** ({f['size_kb']} KB · {f['modified']})")
            fc3.caption(f["status"])
    elif current_folder:
        st.caption("No new files detected in watch folder.")

# ══════════════════════════════════════════════════════════════════════════
# Tab 3: Import History
# ══════════════════════════════════════════════════════════════════════════
with tab_history:
    for _key in ("confirm_delete_id", "import_delete_msg"):
        if _key not in st.session_state:
            st.session_state[_key] = None

    if st.session_state.import_delete_msg:
        st.success(st.session_state.import_delete_msg)
        st.session_state.import_delete_msg = None

    log = get_import_log(conn=conn)

    if not log:
        st.info("No imports yet.")
    else:
        # Count per filename to detect duplicates
        _fn_counts: dict = {}
        for _e in log:
            _fn_counts[_e["filename"]] = _fn_counts.get(_e["filename"], 0) + 1

        st.caption(f"{len(log)} import batch(es) on record")

        # Column header row (Pass 35: widened Period column).
        _hc = st.columns([3.0, 1.3, 2.4, 0.7, 1.6, 1])
        for _col, _lbl in zip(_hc, ["File", "Type", "Period", "Rows", "Imported At", ""]):
            _col.markdown(
                f"<span style='font-size:0.75rem;color:#8b949e'>{_lbl}</span>",
                unsafe_allow_html=True,
            )

        # Pass 35 Phase 5: derive a friendly statement-period label from
        # min/max transaction dates whenever the raw statement_period
        # column is empty or carries an ugly fallback like
        # "ledger_document..." — the raw value stays in the DB so any
        # external consumer can still read it.
        from utils.insights import friendly_import_period as _friendly_period
        from utils.database import get_statement_summary_for_batch as _get_stmt_sum
        # Widen the Period column so longer labels (e.g.
        # "2026-04 statement / 2026-04-08 to 2026-05-07") aren't truncated.
        _hist_widths = [3.0, 1.3, 2.4, 0.7, 1.6, 1]
        for _entry in log:
            _dup = _fn_counts.get(_entry["filename"], 0) > 1
            _dup_mark = " ⚠️" if _dup else ""
            _c1, _c2, _c3, _c4, _c5, _c6 = st.columns(_hist_widths)
            _c1.markdown(f"**{_entry['filename']}{_dup_mark}**")
            _c2.caption(_entry.get("account_type") or "—")
            _c3.caption(_friendly_period(
                statement_period=_entry.get("statement_period") or "",
                batch_id=_entry.get("id"),
                conn=conn,
            ))
            _c4.caption(str(_entry.get("rows_inserted", 0)))
            _c5.caption((_entry.get("imported_at") or "")[:16])
            if _c6.button("Undo", key=f"del_{_entry['id']}", type="secondary"):
                st.session_state.confirm_delete_id = _entry["id"]
                st.rerun()

            # Pass 35c: if this batch has a saved Mastercard statement
            # summary, show interest / fees / new balance / due date on
            # the next line. Compact — only renders when at least one
            # field is populated, to keep non-MC rows unchanged.
            _ss = _get_stmt_sum(_entry["id"], conn=conn)
            if _ss:
                _bits: list[str] = []
                if _ss.get("interest_charges") is not None:
                    _bits.append(f"interest \\${_ss['interest_charges']:,.2f}")
                if _ss.get("fees") is not None:
                    _bits.append(f"fees \\${_ss['fees']:,.2f}")
                if _ss.get("cash_advances_total") is not None:
                    _bits.append(
                        f"cash advances \\${_ss['cash_advances_total']:,.2f}"
                    )
                if _ss.get("new_balance") is not None:
                    _bits.append(f"new balance \\${_ss['new_balance']:,.2f}")
                if _ss.get("payment_due_date"):
                    _bits.append(f"due {_ss['payment_due_date']}")
                if _bits:
                    st.caption(" · ".join(_bits))

        if any(v > 1 for v in _fn_counts.values()):
            st.caption("⚠️ Files marked with ⚠️ appear more than once — consider removing the duplicate.")

        # ── Confirmation widget ────────────────────────────────────────
        if st.session_state.confirm_delete_id is not None:
            _target = next(
                (_e for _e in log if _e["id"] == st.session_state.confirm_delete_id), None
            )
            if _target:
                st.divider()
                _target_period = _friendly_period(
                    statement_period=_target.get("statement_period") or "",
                    batch_id=_target.get("id"),
                    conn=conn,
                )
                _undo_preview = preview_import_batch_undo(_target["id"], conn=conn)
                _edited_line = (
                    f"- **Edited after import:** {_undo_preview['edited_count']} ⚠\n"
                    if _undo_preview.get("edited_count") else
                    "- **Edited after import:** none detected\n"
                )
                _rules_line = (
                    f"- **Saved merchant rules from this batch:** "
                    f"{_undo_preview['rule_count']} (preserved)\n"
                )
                st.warning(
                    f"**Undo this import batch?**\n\n"
                    f"- **File:** {_target['filename']}\n"
                    f"- **Type:** {_target.get('account_type') or '—'}\n"
                    f"- **Period:** {_target_period}\n"
                    f"- **Imported:** {_undo_preview.get('imported_at') or '—'}\n"
                    f"- **Transactions:** {_undo_preview.get('transaction_count', 0)}\n"
                    f"- **Income / spending represented:** "
                    f"${_undo_preview.get('income', 0):,.2f} / "
                    f"${_undo_preview.get('spending', 0):,.2f}\n"
                    f"- **Date range:** {_undo_preview.get('first_date') or 'none'} → "
                    f"{_undo_preview.get('last_date') or 'none'}\n"
                    f"{_edited_line}{_rules_line}\n"
                    "Only Ledger rows owned by this batch will be removed in one "
                    "transaction. Unrelated imports and saved merchant rules stay. "
                    "No source files will be removed from your computer."
                )
                _history_edited_ack = True
                if _undo_preview.get("edited_count"):
                    _history_edited_ack = st.checkbox(
                        "I understand that my edited transaction rows "
                        "will be removed.",
                        key=f"history_undo_edited_ack_{_target['id']}",
                    )
                _ca, _cb, _ = st.columns([1.4, 1, 4])
                if _ca.button(
                    "Confirm Undo", type="primary", key="confirm_del_btn",
                    disabled=not _history_edited_ack,
                ):
                    _result = undo_import_batch(_target["id"], conn=conn)
                    conn.commit()
                    st.session_state.import_delete_msg = (
                        f"Undid imported statement: {_target['filename']} "
                        f"— {_result.get('transactions_deleted', 0)} transaction(s) removed."
                    )
                    st.session_state.confirm_delete_id = None
                    st.rerun()
                if _cb.button("Cancel", key="cancel_del_btn"):
                    st.session_state.confirm_delete_id = None
                    st.rerun()

conn.close()

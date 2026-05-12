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

from utils.database import init_db, get_connection, insert_transaction, insert_import_log, get_import_log, delete_import_batch
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
tab_upload, tab_watcher, tab_history = st.tabs(["Upload Files", "Watch Folder", "Import History"])

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
    opt1, opt2 = st.columns(2)
    with opt1:
        account_id = st.text_input(
            "Account label (optional — applied to all files)",
            placeholder="e.g. CHQ-1272 or MC-8600",
        )
    with opt2:
        preview_only = st.checkbox("Preview only — don't import yet", value=False)

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
            with temp_pdf(uf.name, raw) as tmp:
                detected, confidence = detect_with_confidence(tmp)

            file_infos.append({
                "uploaded_file": uf,
                "raw":           raw,
                "hash":          file_hash,
                "detected":      detected,
                "confidence":    confidence,
                "label":         detect_label(detected),
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
                            result = parse_csv(tmp_path,
                                               account_id=account_id or None,
                                               statement_period=None)
                        else:
                            raise ValueError(f"Unrecognised file type — cannot parse '{uf.name}'")

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
                        batch_id = insert_import_log({
                            "filename":         uf.name,
                            "file_hash":        file_hash,
                            "account_type":     account_type_str,
                            "statement_period": period,
                            "rows_parsed":      stats.get("total_parsed", 0),
                            "errors":           errors,
                        }, conn)
                        conn.commit()

                        inserted = skipped = flagged = 0
                        for tx in txs:
                            tx["import_batch_id"] = batch_id
                            if account_id:
                                tx["account_id"] = account_id
                            ok, _ = insert_transaction(tx, conn)
                            if ok:
                                inserted += 1
                                if tx.get("is_flagged"):
                                    flagged += 1
                            else:
                                skipped += 1

                        conn.execute(
                            "UPDATE import_log SET rows_inserted=?,rows_skipped=?,rows_flagged=? WHERE id=?",
                            (inserted, skipped, flagged, batch_id),
                        )
                        # Pass 35c: persist the Mastercard PDF statement
                        # summary (interest_charges, fees, new_balance,
                        # cash_advances_total, ...) so compute_score can
                        # use authoritative bank-provided values instead
                        # of guessing from transaction rows. Best-effort:
                        # only fires when the parser actually populated
                        # the summary dict.
                        _stmt_sum = (result or {}).get("statement_summary") or {}
                        if _stmt_sum:
                            try:
                                from utils.database import upsert_statement_summary
                                upsert_statement_summary(
                                    _stmt_sum,
                                    import_batch_id=batch_id,
                                    # Store the normalized account type so
                                    # compute_score(account_type="mastercard")
                                    # can find the summary. The human label
                                    # remains in import_log.account_type.
                                    account_type=dtype,
                                    conn=conn,
                                )
                            except Exception as _e:
                                # Don't fail the import on summary-persist errors.
                                st.caption(
                                    f"Note: statement summary not saved "
                                    f"({type(_e).__name__})."
                                )
                        conn.commit()
                        total_in += inserted
                        total_sk += skipped
                        total_fl += flagged

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
                st.success(
                    f"**Batch import complete** — "
                    f"{total_in} new transactions across {len(file_infos)} file(s). "
                    f"{total_sk} duplicates skipped, {total_fl} flagged for review. "
                    "All trends and dashboards update automatically."
                )
                st.rerun()
            elif preview_only:
                st.info("Preview complete — no data was written. Uncheck 'Preview only' to import.")

# ══════════════════════════════════════════════════════════════════════════
# Tab 2: Watch Folder
# ══════════════════════════════════════════════════════════════════════════
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
            if _c6.button("Remove", key=f"del_{_entry['id']}", type="secondary"):
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
                st.warning(
                    f"**Remove this import batch?**\n\n"
                    f"- **File:** {_target['filename']}\n"
                    f"- **Type:** {_target.get('account_type') or '—'}\n"
                    f"- **Period:** {_target_period}\n"
                    f"- **Transactions that will be removed:** {_target.get('rows_inserted', 0)}\n\n"
                    "Only Ledger database rows for this batch will be deleted. "
                    "No files will be removed from your computer."
                )
                _ca, _cb, _ = st.columns([1.4, 1, 4])
                if _ca.button("Confirm Remove", type="primary", key="confirm_del_btn"):
                    _n = delete_import_batch(_target["id"], conn=conn)
                    conn.commit()
                    st.session_state.import_delete_msg = (
                        f"Removed imported statement: {_target['filename']} "
                        f"— {_n} transaction(s) removed."
                    )
                    st.session_state.confirm_delete_id = None
                    st.rerun()
                if _cb.button("Cancel", key="cancel_del_btn"):
                    st.session_state.confirm_delete_id = None
                    st.rerun()

conn.close()

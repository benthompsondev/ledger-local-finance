"""
Diagnostics.

Single page for manual-test readiness:
  • App / Environment
  • Database Health
  • Finance Logic Health
  • AI Health (last call statuses; never raw prompts/responses)
  • OpenClaw / Sharing Health
  • In-app Smoke Test runner
  • In-app Bug Report bundle button
  • Manual Test Checklist (session_state-only persistence)

No DB writes. No AI calls. Always read-only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from utils.database import init_db
from utils.diagnostics import build_diagnostics
from utils.styles import inject_styles

st.set_page_config(page_title="Diagnostics · App Health · Ledger",
                   page_icon="🩺", layout="wide")
init_db()
inject_styles()

# Pass 31: clearer page title + plain-language intro card so a normal
# user (or reviewer) understands when this page is useful.
st.title("🩺 Diagnostics · App Health")
st.markdown(
    "<div style='background:rgba(79,134,198,0.05);"
    "border:1px solid rgba(79,134,198,0.2);"
    "border-left:3px solid #4f86c6;border-radius:8px;"
    "padding:10px 14px;margin-bottom:14px;font-size:0.88rem'>"
    "<b>When to use this page.</b> Use Diagnostics when something "
    "seems broken, before sharing a bug report, or before sending "
    "context to OpenClaw. Everything here is read-only — no DB "
    "writes, no AI calls."
    "</div>",
    unsafe_allow_html=True,
)

# Always rebuild fresh — diagnostics is cheap.
diag = build_diagnostics()

# ── App / Environment ─────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">App / Environment</p>',
            unsafe_allow_html=True)
env = diag.get("environment") or {}
e1, e2, e3, e4 = st.columns(4)
e1.metric("Pass",   env.get("ledger_pass") or env.get("ledger_version") or "—")
e2.metric("Python", env.get("python") or "—")
e3.metric("Streamlit", env.get("streamlit") or "—")
e4.metric("In venv", "yes" if env.get("in_venv") else "no")
with st.expander("Environment detail", expanded=False):
    st.code(json.dumps(env, indent=2, default=str), language="json")

# ── Database Health ───────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Database Health</p>',
            unsafe_allow_html=True)
db = diag.get("database") or {}
counts = db.get("counts") or {}
date_range = db.get("date_range") or {}
if not db.get("db_exists"):
    st.error(
        "**Database does not exist yet.** Open the **Import** page "
        "and load a Tangerine PDF or CSV to create `data/finance.db`."
    )
else:
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Transactions", counts.get("transactions", 0))
    d2.metric("Imports",      counts.get("import_batches", 0))
    d3.metric("Flagged",      counts.get("flagged", 0))
    d4.metric("Uncategorized", counts.get("uncategorized", 0))

    d5, d6, d7, d8 = st.columns(4)
    d5.metric("Inv. snapshots",   counts.get("investment_snapshots", 0))
    d6.metric("Net worth snaps",  counts.get("net_worth_snapshots", 0))
    d7.metric("Monthly plans",    counts.get("monthly_plans", 0))
    d8.metric("Goals",            counts.get("goals", 0))

    if date_range:
        st.caption(
            f"Date range: **{date_range.get('first_d') or '—'}** → "
            f"**{date_range.get('last_d') or '—'}**"
        )
    if db.get("missing_tables"):
        st.warning(
            f"⚠️ Missing tables: {', '.join(db['missing_tables'])}. "
            "Run `init_db()` (any page does this on load) to create them."
        )
    else:
        st.success(f"All {len(db.get('tables') or [])} tables present.")

# ── Finance Logic Health ─────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Finance Logic Health</p>',
            unsafe_allow_html=True)
fin = diag.get("finance") or {}
risk = (fin.get("forecast_risk") or "").replace("_", " ")
risk_color = {
    "on track":          "#34d058",
    "watch":             "#f59e0b",
    "danger":            "#ef4444",
    "insufficient data": "#8b949e",
}.get(risk, "#8b949e")
f1, f2, f3, f4 = st.columns(4)
f1.metric("Analysis anchor", fin.get("analysis_anchor") or "—")
f2.metric("Plan saved",      "yes" if fin.get("plan_saved") else "no",
          delta=fin.get("plan_mode") if fin.get("plan_saved") else None)
f3.metric("Forecast risk",   risk or "—")
f4.metric("Review queue",    fin.get("review_queue_count", 0))

f5, f6, f7, f8 = st.columns(4)
f5.metric("Locked commitments / mo",
          f"${(fin.get('commitment_monthly_estimate') or 0):,.0f}")
f6.metric("Variable watch / mo",
          f"${(fin.get('variable_monthly_watch') or 0):,.0f}")
f7.metric("Active subs",      fin.get("active_subscriptions_count", 0))
f8.metric("Stale subs",       fin.get("stale_inactive_count", 0))

with st.expander("Forecast detail", expanded=False):
    st.code(json.dumps(fin, indent=2, default=str), language="json")

# ── AI Health ─────────────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">AI Health</p>',
            unsafe_allow_html=True)
ai = diag.get("ai") or {}
a1, a2, a3 = st.columns(3)
a1.metric("Configured", "yes" if ai.get("configured") else "no")
a2.metric("Provider",   ai.get("provider") or "—")
a3.metric("Ready",      "yes" if ai.get("ready") else "no",
          delta=ai.get("ready_reason") if not ai.get("ready") else None,
          delta_color="inverse" if not ai.get("ready") else "normal")
if ai.get("key_preview"):
    st.caption(f"Key preview (masked): `{ai['key_preview']}`")

last_calls = ai.get("last_calls") or {}
if last_calls and not last_calls.get("_error"):
    with st.expander(
        f"Last AI call statuses ({len(last_calls)} feature(s))",
        expanded=False,
    ):
        rows = []
        for fid, status in last_calls.items():
            rows.append({
                "feature": fid,
                "attempted": status.get("attempted"),
                "ok":        status.get("ok"),
                "fallback":  status.get("fallback"),
                "reason":    (status.get("reason") or "")[:80],
                "at":        status.get("at"),
            })
        try:
            import pandas as pd
            st.dataframe(pd.DataFrame(rows),
                         use_container_width=True, hide_index=True)
        except Exception:
            st.code(json.dumps(rows, indent=2, default=str), language="json")
elif last_calls.get("_error"):
    st.warning(f"AI status read error: {last_calls['_error']}")
else:
    st.caption("No AI calls recorded this session yet.")

# ── OpenClaw / Sharing Health ────────────────────────────────────
st.markdown(
    '<p class="ledger-section-header">OpenClaw / Sharing Health</p>',
    unsafe_allow_html=True,
)
sh = diag.get("sharing") or {}
sh1, sh2, sh3 = st.columns(3)
sh1.metric("config.json present",
           "yes" if sh.get("config_json_present") else "no")
sh2.metric("DB present",
           "yes" if sh.get("finance_db_present") else "no",
           delta=(f"{sh.get('finance_db_size_kb',0):,.0f} KB"
                  if sh.get("finance_db_present") else None))
sh3.metric("Last export", sh.get("last_export") or "—")
st.info(sh.get("warning", ""))
st.code(
    "# Export agent context (safe to share with OpenClaw — "
    "but contains merchant names)\n"
    f"{sh.get('export_context_command')}\n\n"
    "# Build a clean share zip (USER mode by default)\n"
    f"{sh.get('share_zip_command')}\n\n"
    "# Build a sanitized bug-report bundle\n"
    f"{sh.get('bug_report_command')}",
    language="bash",
)

# ── In-app smoke test ─────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Smoke test</p>',
            unsafe_allow_html=True)
st.caption(
    "Runs `python -m scripts.smoke_test` in a subprocess. The output "
    "is captured here — no secrets are ever shown."
)
if st.button("▶️ Run smoke test", key="diag_run_smoke"):
    with st.spinner("Running smoke test…"):
        try:
            r = subprocess.run(
                [sys.executable, "-m", "scripts.smoke_test"],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True, text=True, timeout=120,
            )
            st.session_state["diag_smoke_out"] = (
                (r.stdout or "")
                + ("\n--- STDERR ---\n" + r.stderr if r.stderr else "")
                + f"\n--- exit code: {r.returncode} ---\n"
            )
            st.session_state["diag_smoke_rc"] = r.returncode
        except Exception as e:
            st.session_state["diag_smoke_out"] = f"smoke test failed: {e!r}"
            st.session_state["diag_smoke_rc"] = -1

if "diag_smoke_out" in st.session_state:
    rc = st.session_state.get("diag_smoke_rc", -1)
    if rc == 0:
        st.success("Smoke test PASSED.")
    else:
        st.error(f"Smoke test FAILED (exit {rc}).")
    text = st.session_state["diag_smoke_out"]
    # Tail: keep last ~200 lines so the page stays responsive.
    text = "\n".join(text.splitlines()[-200:])
    st.code(text, language="text")

# ── Bug report bundle ────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Bug report bundle</p>',
            unsafe_allow_html=True)
st.caption(
    "Creates a sanitized zip you can share with someone debugging "
    "Ledger. Contains diagnostics, redacted launcher log, and smoke "
    "output — never contains config.json, finance.db, raw "
    "transactions, AI prompts, or API keys."
)
bcol1, bcol2 = st.columns([1, 4])
with bcol1:
    if st.button("📦 Create bundle", key="diag_make_bug"):
        with st.spinner("Bundling…"):
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "scripts.make_bug_report"],
                    cwd=str(Path(__file__).resolve().parent.parent),
                    capture_output=True, text=True, timeout=180,
                )
                st.session_state["diag_bug_out"] = (
                    (r.stdout or "") + (r.stderr or "")
                )
                st.session_state["diag_bug_rc"] = r.returncode
            except Exception as e:
                st.session_state["diag_bug_out"] = f"bundle failed: {e!r}"
                st.session_state["diag_bug_rc"] = -1
with bcol2:
    if "diag_bug_out" in st.session_state:
        rc = st.session_state.get("diag_bug_rc", -1)
        if rc == 0:
            st.success("Bundle written. See exports/bug_reports/.")
        else:
            st.error(f"Bundle failed (exit {rc}).")
        st.code(st.session_state["diag_bug_out"], language="text")

# ── Manual Test Checklist (Pass 31: developer-only expander) ─────
# User feedback: "Move the long manual checklist into an expander."
# Wrapped in an expander so the page reads as App Health by default;
# the ~38-item checklist is still here for support/dev work.
import os as _os_diag
_dev_mode_diag = _os_diag.environ.get(
    "LEDGER_DEV_MODE", "").strip().lower() in {"1","true","yes","on"}

_checklist_expander = st.expander(
    "Developer manual test checklist",
    expanded=_dev_mode_diag,
)
with _checklist_expander:
    st.caption(
        "Click through the flows below in order. Checkboxes persist "
        "for this session only — closing the browser tab clears them."
    )

    CHECKLIST: list[tuple[str, list[str]]] = [
        ("A. Launch / Setup", [
            "Launcher started Ledger via Ledger_Launcher.py / .bat / .ps1",
            "Browser opened http://127.0.0.1:8501",
            "Settings page loads; AI key value is masked (only `sk-…1234`)",
            "Settings → Data & Sharing Safety panel renders",
        ]),
        ("B. Import", [
            "Import → drop a Tangerine Chequing PDF → preview rows",
            "Import → drop a Tangerine Mastercard PDF → preview rows",
            "Import → drop a generic CSV → 'Detected mapping' caption appears",
            "Re-uploading the same file shows ALREADY IMPORTED badge",
        ]),
        ("C. Review / Categorization", [
            "Review queue opens; flagged rows visible",
            "Bulk-suggest button runs on uncategorized rows (with or without AI)",
            "Saving a category change updates the row and clears flag",
            "Force-apply an existing merchant rule — count matches DB",
        ]),
        ("D. Dashboard / Trends", [
            "Dashboard loads without console errors",
            "Top merchants chart renders",
            "Money Pulse gauge renders",
            "Reduce page is reachable from sidebar",
        ]),
        ("E. Reduce / Subscriptions", [
            "Reduce page loads",
            "Active vs Stale subscription split is visible",
            "Lazy AI summary: page does NOT block on AI",
            "Top Cancellation Candidates make sense (no groceries here)",
            "3 practical cuts (Small / Medium / Big) render",
        ]),
        ("F. Monthly Plan / Forecast", [
            "Plan → Plan tab → pick a mode → Generate",
            "Save plan → coach panel updates after rerun",
            "Forecast tab → safe-to-spend appears (after plan saved)",
            "Forecast variable-watch caption: shows ~$X across N merchants",
            "Bills tab → 4 groups visible: Fixed / Subs / Variable watch / Stale",
            "Groceries / Shopping land in 'Variable watch', NOT 'Fixed'",
            "Goals tab → create a cash_buffer goal → progress updates",
        ]),
        ("G. Net Worth", [
            "Net Worth → top metrics row visible above tabs",
            "Holdings CSV import preview shows columns",
            "Snapshot history reflects the new import",
            "Cash / debts → add a chequing balance → Overview updates",
            "Net worth Plotly line chart renders after ≥1 snapshot",
            "Milestone card shows next milestone + pace per snapshot",
        ]),
        ("H. OpenClaw / Sharing", [
            "Run `python -m scripts.export_agent_context` → file in exports/",
            "Run `python -m scripts.make_share_zip` → user zip; no handoff inside",
            "Run `python -m scripts.make_share_zip --include-dev-notes` → handoff included",
            "Run `python -m scripts.make_bug_report` → zip in exports/bug_reports/",
            "Open bug-report zip → no config.json, no finance.db, no API key",
        ]),
    ]

    if "diag_checklist" not in st.session_state:
        st.session_state["diag_checklist"] = {}
    state = st.session_state["diag_checklist"]

    total = sum(len(items) for _, items in CHECKLIST)
    done = sum(1 for k in state if state[k])
    st.progress(done / total if total else 0)
    st.caption(f"{done} / {total} steps checked.")

    for section, items in CHECKLIST:
        st.markdown(f"#### {section}")
        for item in items:
            key = f"chk:{section}:{item}"
            state[key] = st.checkbox(
                item, value=state.get(key, False), key=key,
            )

    cc1, cc2 = st.columns([1, 5])
    with cc1:
        if st.button("Reset checklist", key="diag_reset_checklist"):
            for k in list(state.keys()):
                if k.startswith("chk:"):
                    state[k] = False
                    st.session_state.pop(k, None)
            st.rerun()
    with cc2:
        st.caption(
            "When you find a bug: click 📦 Create bundle above and share "
            "the zip from `exports/bug_reports/`."
        )

# ── Quick navigation ─────────────────────────────────────────────
st.markdown('<p class="ledger-section-header">Quick jumps</p>',
            unsafe_allow_html=True)
n1, n2, n3, n4, n5 = st.columns(5)
if n1.button("📥 Import",      use_container_width=True):
    st.switch_page("pages/3_Import.py")
if n2.button("🗓 Month Plan",  use_container_width=True):
    st.switch_page("pages/12_Month_Plan.py")
if n3.button("📈 Investments", use_container_width=True):
    st.switch_page("pages/7_Investments.py")
if n4.button("⚙ Settings",     use_container_width=True):
    st.switch_page("pages/9_Settings.py")
if n5.button("🏠 Home",        use_container_width=True):
    st.switch_page("app.py")

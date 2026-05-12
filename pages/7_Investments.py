"""
Investments — manual portfolio entry + holdings CSV snapshots + net worth.

Pass 19 changes
───────────────
• Adds a "Holdings CSV import" section that snapshots a brokerage export
  into investment_snapshot_batches / investment_positions. Snapshots
  accumulate over time so portfolio value can be charted later.
• Adds an "Account balances" section (manual cash, credit card, loan
  balances) feeding net-worth math.
• Adds a "Net worth" section that combines the latest investment
  snapshot with the latest account balances and lets the user save a
  net_worth_snapshots row for history.
• Legacy manual-holding entry is preserved unchanged so existing entries
  keep working.
"""
import hashlib
import json
from datetime import date

import pandas as pd
import streamlit as st

from parsers.investments_csv import parse_holdings_csv
from utils.database import (
    init_db, get_connection,
    get_investments, upsert_investment,
    insert_investment_snapshot, get_investment_snapshots,
    get_latest_investment_snapshot, delete_investment_snapshot,
    insert_account_balance, get_account_balances, delete_account_balance,
    compute_net_worth_now, insert_net_worth_snapshot,
    get_net_worth_snapshots,
    ASSET_KINDS, LIABILITY_KINDS,
)
from utils.platform_utils import temp_pdf
from components.charts import investment_allocation
from utils.styles import inject_styles

st.set_page_config(page_title="Net Worth", page_icon="📈", layout="wide")
init_db()
inject_styles()
st.title("Net Worth")
st.caption(
    "Track your assets, liabilities, and net worth over time. "
    "Holdings and cash balances live on this computer — Ledger never "
    "fetches live prices."
)

# ══════════════════════════════════════════════════════════════════════
# Pass 30 — Top metrics row before the tabs.
# User feedback: "Net Worth should be stronger: compare month over
# month, show assets/liabilities/debt movement … and a better graph."
# We promote the four headline numbers (Net Worth, Assets, Debt,
# Change vs last snapshot) to a strip ABOVE the tabs so the page's
# main answer is visible without clicking around.
# ══════════════════════════════════════════════════════════════════════
_conn_top = get_connection()
try:
    _nw_top = compute_net_worth_now(conn=_conn_top)
    _hist_top = get_net_worth_snapshots(conn=_conn_top, limit=2) or []
finally:
    _conn_top.close()

_top1, _top2, _top3, _top4 = st.columns(4)
_top1.metric("Net worth",        f"${(_nw_top['net_worth'] or 0):,.0f}")
_top2.metric("Assets",           f"${(_nw_top['total_assets'] or 0):,.0f}")
_top3.metric("Debt / liabilities", f"${(_nw_top['total_liabilities'] or 0):,.0f}")

# Change since previous snapshot — only render when at least 2 snaps
# exist (the latest is the implicit "now"; we compare to history[1]).
_delta_str = "—"
_delta_color = "off"
if len(_hist_top) >= 2:
    _latest = float(_hist_top[0].get("net_worth") or 0)
    _prior  = float(_hist_top[1].get("net_worth") or 0)
    _delta  = _latest - _prior
    _delta_str = f"${_delta:,.0f}"
    _delta_color = "normal" if _delta >= 0 else "inverse"
elif _nw_top.get("net_worth") and not _hist_top:
    _delta_str = "first snapshot"
_top4.metric(
    "Change vs last snapshot",
    _delta_str,
    delta=(f"{(_hist_top[0].get('as_of_date') if _hist_top else '')} "
           f"→ {(_hist_top[1].get('as_of_date') if len(_hist_top) >= 2 else '')}"
           if len(_hist_top) >= 2 else None),
    delta_color=_delta_color,
)
st.divider()

conn = get_connection()

tab_overview, tab_csv, tab_manual, tab_balances, tab_history = st.tabs([
    "Overview",
    "Holdings CSV import",
    "Manual holdings",
    "Cash / debts",
    "Snapshot history",
])

# ══════════════════════════════════════════════════════════════════════
# Tab 1 — Overview (latest snapshot + net worth)
# ══════════════════════════════════════════════════════════════════════
with tab_overview:
    snap = get_latest_investment_snapshot(conn=conn)
    nw = compute_net_worth_now(conn=conn)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Latest portfolio value",
        f"${(snap['total_market_value_native'] if snap else 0):,.2f}",
        help=("Sum of market values from your most-recent holdings "
              "snapshot, in original currencies (no FX conversion)."),
    )
    k2.metric("Total assets",      f"${nw['total_assets']:,.2f}")
    k3.metric("Total liabilities", f"${nw['total_liabilities']:,.2f}")
    k4.metric("Net worth",         f"${nw['net_worth']:,.2f}")

    if nw["mixed_currency"]:
        # Pass 30: softened tone. User feedback: "Mixed currency
        # warning. Keep it, but make it less alarming."
        st.info(
            f"Mixed currencies detected ({', '.join(nw['currencies'])}). "
            "Totals are approximate until FX conversion is added.",
            icon="ℹ️",
        )
    if nw["missing"]:
        st.caption("Missing inputs: " + "; ".join(nw["missing"]))

    # ══════════════════════════════════════════════════════════════════
    # Pass 31 — Net Worth flagship: chart + milestone card + collapsed
    # holdings. Users open this page to answer "am I building wealth or
    # losing ground?". The chart and milestone live above the fold; the
    # raw holdings/snapshot table moves into an expander.
    # ══════════════════════════════════════════════════════════════════
    nw_hist = get_net_worth_snapshots(conn=conn) or []

    # ── Chart: Plotly line + markers, smart y-axis ──────────────────
    if len(nw_hist) >= 1:
        import plotly.graph_objects as _go
        from config.theme import (
            BG_CARD as _BG, TEXT_BASE as _TXT,
            TEXT_MUTED as _MUT, BORDER as _BRD,
        )
        # snapshots come back newest-first; flip for time axis
        _hist_chrono = list(reversed(nw_hist))
        _xs = [h.get("as_of_date") for h in _hist_chrono]
        _nws = [float(h.get("net_worth") or 0) for h in _hist_chrono]
        _assets = [float(h.get("total_assets") or 0) for h in _hist_chrono]
        _liabs  = [float(h.get("total_liabilities") or 0) for h in _hist_chrono]

        st.markdown(
            '<p class="ledger-section-header">Net worth over time</p>',
            unsafe_allow_html=True,
        )
        _show_assets = st.toggle(
            "Show assets and liabilities",
            value=False, key="nw_show_components",
            help=("Adds two extra lines to the chart so you can see "
                  "asset growth and debt change separately."),
        )
        fig_nw = _go.Figure()
        fig_nw.add_scatter(
            x=_xs, y=_nws, name="Net worth", mode="lines+markers",
            line=dict(color="#34d058", width=2.5),
            marker=dict(size=7, color="#34d058"),
            hovertemplate=(
                "<b>%{x}</b><br>Net worth: $%{y:,.0f}<extra></extra>"
            ),
        )
        if _show_assets:
            fig_nw.add_scatter(
                x=_xs, y=_assets, name="Assets",
                mode="lines+markers",
                line=dict(color="#4f86c6", width=1.5, dash="dot"),
                marker=dict(size=5),
                hovertemplate="<b>%{x}</b><br>Assets: $%{y:,.0f}<extra></extra>",
            )
            fig_nw.add_scatter(
                x=_xs, y=_liabs, name="Liabilities",
                mode="lines+markers",
                line=dict(color="#f85149", width=1.5, dash="dot"),
                marker=dict(size=5),
                hovertemplate="<b>%{x}</b><br>Liabilities: $%{y:,.0f}<extra></extra>",
            )

        # Smart y-axis: when net worth lives in a tight band well
        # above zero, plotly's default 0-baseline makes the line look
        # flat. Pad ±10% around min/max so changes are visible.
        if _nws:
            _mn, _mx = min(_nws), max(_nws)
            if _mx - _mn < _mx * 0.05 or _mn > _mx * 0.6:
                _pad = max(50, (_mx - _mn) * 0.5 or _mx * 0.05)
                _yrange = [max(0, _mn - _pad), _mx + _pad]
            else:
                _yrange = None
        else:
            _yrange = None

        fig_nw.update_layout(
            paper_bgcolor=_BG, plot_bgcolor=_BG,
            font=dict(color=_TXT, size=12),
            xaxis=dict(showgrid=False, color=_MUT, linecolor=_BRD),
            yaxis=dict(
                showgrid=True, gridcolor=_BRD, color=_MUT,
                tickprefix="$", tickformat=",.0f",
                range=_yrange,
            ),
            legend=dict(orientation="h", y=-0.18, x=0,
                        font=dict(color=_MUT, size=11)),
            margin=dict(l=4, r=4, t=10, b=4),
            height=320, hovermode="x unified",
        )
        st.plotly_chart(fig_nw, use_container_width=True,
                         key="nw_chart_plotly")

        # ── Milestone card ──────────────────────────────────────────
        # Auto-pick a milestone above the current net worth using a
        # round-number ladder. Override later if a goal_targets row
        # links to net_worth.
        import math
        _cur_nw = float(_nws[-1] if _nws else 0)
        def _next_milestone(v: float) -> float:
            if v < 1_000:        step = 500
            elif v < 10_000:     step = 1_000
            elif v < 50_000:     step = 5_000
            elif v < 250_000:    step = 25_000
            elif v < 1_000_000:  step = 50_000
            else:                step = 100_000
            return float(math.floor(v / step + 1) * step)

        _ms = _next_milestone(_cur_nw)
        _gap = _ms - _cur_nw
        _pct = (_cur_nw / _ms * 100) if _ms > 0 else 0
        # Pace = average snapshot-to-snapshot delta over the last 3
        # snapshots (or fewer if we don't have 3 yet).
        _pace = 0.0
        if len(_nws) >= 2:
            _deltas = [(_nws[i] - _nws[i-1])
                       for i in range(1, min(len(_nws), 4))]
            _pace = sum(_deltas) / max(1, len(_deltas))

        _bar_color = "#34d058" if _pace >= 0 else "#e3b341"
        _pace_str  = (f"+${_pace:,.0f}" if _pace >= 0 else f"-${abs(_pace):,.0f}")
        st.markdown(
            f"<div style='background:rgba(52,208,88,0.05);"
            f"border:1px solid rgba(52,208,88,0.2);"
            f"border-left:3px solid #34d058;border-radius:8px;"
            f"padding:14px 16px;margin-top:6px'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"margin-bottom:4px'>"
            f"<div style='font-size:1rem;font-weight:700;color:#e6edf3'>"
            f"Next milestone: ${_ms:,.0f}</div>"
            f"<div style='font-size:0.85rem;color:#8b949e'>"
            f"Pace per snapshot: {_pace_str}</div></div>"
            f"<div style='font-size:0.85rem;color:#c9d1d9;"
            f"margin-bottom:6px'>"
            f"${_gap:,.0f} to go ({_pct:.0f}% of milestone)</div>"
            f"<div style='background:rgba(255,255,255,0.05);"
            f"border-radius:4px;height:8px;overflow:hidden'>"
            f"<div style='background:{_bar_color};height:100%;"
            f"width:{max(0,min(100,_pct)):.0f}%'></div>"
            f"</div></div>".replace("$", r"\$"),
            unsafe_allow_html=True,
        )
    else:
        st.info(
            "No net-worth snapshots yet. Save your first one below to "
            "start the timeline."
        )

    # ── Save a net-worth snapshot (Pass 31: smart CTA copy) ─────────
    _today_iso = date.today().isoformat()
    _has_today_snap = any(
        (h.get("as_of_date") or "")[:7] == _today_iso[:7]
        for h in nw_hist
    )
    st.markdown('<p class="ledger-section-header">Save snapshot</p>',
                unsafe_allow_html=True)
    if _has_today_snap:
        st.caption(
            f"Snapshot saved for this month "
            f"({(nw_hist[0].get('as_of_date') if nw_hist else '')}). "
            "You can refresh it after balance changes."
        )
        _btn_label = "🔄 Update this month's snapshot"
    else:
        st.caption(
            "Capture today's computed net worth into history. "
            "Recommended once per month."
        )
        _btn_label = "📌 Save this month's snapshot"
    if st.button(_btn_label, key="save_nw"):
        if not nw["breakdown"]:
            st.warning("Nothing to snapshot — add cash balances or import "
                       "a holdings CSV first.")
        else:
            insert_net_worth_snapshot({
                "as_of_date":        _today_iso,
                "total_assets":      nw["total_assets"],
                "total_liabilities": nw["total_liabilities"],
                "net_worth":         nw["net_worth"],
                "source_breakdown":  nw["breakdown"],
                "currency":          "mixed" if nw["mixed_currency"] else "CAD",
                "mixed_currency":    1 if nw["mixed_currency"] else 0,
            }, conn=conn)
            conn.commit()
            st.success("Net worth snapshot saved.")
            st.rerun()

    # ── Latest holdings table → moved to expander (Pass 31) ─────────
    if snap:
        positions = snap.get("positions") or []
        if positions:
            with st.expander(
                f"Holdings details ({snap.get('as_of_date','')}, "
                f"{len(positions)} positions)",
                expanded=False,
            ):
                df = pd.DataFrame(positions)
                display_cols = [c for c in [
                    "account_name", "account_type", "ticker",
                    "security_name", "quantity", "market_price",
                    "market_value", "market_value_currency",
                    "unrealized_return",
                ] if c in df.columns]
                st.dataframe(df[display_cols],
                              use_container_width=True, hide_index=True)
                try:
                    st.plotly_chart(
                        investment_allocation(positions),
                        use_container_width=True,
                    )
                except Exception:
                    pass
    else:
        st.caption(
            "No imported snapshot yet. Use **Holdings CSV import** or "
            "**Manual holdings** to add one."
        )

    # ── Snapshot history table → moved to expander ──────────────────
    if nw_hist:
        with st.expander(
            f"Snapshot history ({len(nw_hist)} entries)",
            expanded=False,
        ):
            df_nw_full = pd.DataFrame(nw_hist)
            st.dataframe(
                df_nw_full[[c for c in
                             ["as_of_date", "total_assets",
                              "total_liabilities", "net_worth"]
                             if c in df_nw_full.columns]],
                use_container_width=True, hide_index=True,
            )

# ══════════════════════════════════════════════════════════════════════
# Tab 2 — Holdings CSV import
# ══════════════════════════════════════════════════════════════════════
with tab_csv:
    st.markdown(
        "Upload a brokerage **holdings report** CSV (Questrade, "
        "Wealthsimple, IBKR, etc.). Ledger snapshots it into history "
        "without overwriting prior imports."
    )

    up = st.file_uploader(
        "Drop a holdings CSV",
        type=["csv"],
        accept_multiple_files=False,
        key="inv_csv_upload",
    )

    if up:
        raw = up.read()
        up.seek(0)
        file_hash = hashlib.md5(raw).hexdigest()

        # Use the temp_pdf helper as a generic "write bytes to disk" tool;
        # parse_holdings_csv only needs a path.
        from pathlib import Path
        import tempfile, os
        tmp = Path(tempfile.mkdtemp()) / up.name
        tmp.write_bytes(raw)

        try:
            parsed = parse_holdings_csv(tmp)
        finally:
            try: os.remove(tmp); os.rmdir(tmp.parent)
            except Exception: pass

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Positions",   parsed["row_count"])
        m2.metric("As of",       parsed["as_of_date"] or "—")
        m3.metric("Currencies",  ", ".join(parsed["currencies"]) or "—")
        m4.metric("Total (native)",
                  f"{parsed['total_market_value_native']:,.2f}")

        if parsed["mixed_currency"]:
            st.warning(
                "Mixed currencies in this snapshot. Ledger will store "
                "totals in their original currencies and flag "
                "`mixed_currency=True`. No FX conversion is performed."
            )

        if parsed["errors"]:
            with st.expander(f"{len(parsed['errors'])} parse warning(s)"):
                for e in parsed["errors"][:20]:
                    st.caption(f"• {e}")

        if not parsed["positions"]:
            st.error(
                "No holdings parsed. The file may not be a holdings "
                "report, or its columns may not match anything Ledger "
                "recognizes (Symbol / Quantity / Market Value / etc)."
            )
        else:
            df_prev = pd.DataFrame(parsed["positions"])
            preview_cols = [c for c in [
                "account_name", "account_type", "ticker", "security_name",
                "quantity", "market_price", "market_value",
                "market_value_currency",
            ] if c in df_prev.columns]
            st.dataframe(df_prev[preview_cols].head(25),
                         use_container_width=True, hide_index=True)
            if len(df_prev) > 25:
                st.caption(f"... and {len(df_prev) - 25} more rows.")

            notes = st.text_input(
                "Notes (optional)",
                placeholder="e.g. Q2 rebalance, after $5k contribution",
                key="inv_csv_notes",
            )
            if st.button("📥 Import this snapshot",
                         type="primary", key="commit_snap"):
                # Guard against accidental dup imports of the same file.
                existing_hash = conn.execute(
                    "SELECT id FROM investment_snapshot_batches "
                    "WHERE file_hash=?",
                    (file_hash,),
                ).fetchone()
                if existing_hash:
                    st.warning(
                        "This exact file has been imported before "
                        f"(batch id {existing_hash[0]}). Snapshot was not "
                        "duplicated. Delete the prior batch from "
                        "Snapshot history if you want to re-import."
                    )
                else:
                    batch_id = insert_investment_snapshot(
                        batch={
                            "source_file":  up.name,
                            "file_hash":    file_hash,
                            "as_of_date":   parsed["as_of_date"],
                            "currencies_seen": ",".join(parsed["currencies"]),
                            "mixed_currency": 1 if parsed["mixed_currency"] else 0,
                            "notes":        notes or None,
                        },
                        positions=parsed["positions"],
                        conn=conn,
                    )
                    conn.commit()
                    st.success(
                        f"Snapshot saved (batch {batch_id}, "
                        f"{parsed['row_count']} positions)."
                    )
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════
# Tab 3 — Manual holdings (legacy)
# ══════════════════════════════════════════════════════════════════════
with tab_manual:
    st.caption(
        "Quick ad-hoc holdings entry. Stored in the legacy `investments` "
        "table — kept around for backward compatibility. For full "
        "history, prefer Holdings CSV import."
    )
    st.info(
        "ℹ️ **Manual entries do NOT feed net-worth history.** They show "
        "up here for quick reference. To track portfolio over time, "
        "use **Holdings CSV import** — each upload becomes a snapshot "
        "and is folded into net worth automatically."
    )

    holdings = get_investments(conn=conn)
    if holdings:
        df = pd.DataFrame(holdings)
        display_cols = [c for c in ["account_name","account_type","ticker",
                                    "security_name","quantity","book_value",
                                    "market_value","currency","as_of_date"]
                        if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True,
                     hide_index=True)
    else:
        st.info("No manual holdings recorded yet.")

    st.subheader("Add / Update Holding")
    with st.form("add_holding"):
        c1, c2 = st.columns(2)
        account_name = c1.text_input("Account name*",
                                     placeholder="e.g. Questrade TFSA")
        account_type = c2.selectbox("Account type",
                                    ["TFSA","RRSP","FHSA","Non-reg","Other"])

        c3, c4, c5 = st.columns(3)
        ticker       = c3.text_input("Ticker (optional)",
                                     placeholder="e.g. XEQT")
        security_name= c4.text_input("Security name",
                                     placeholder="e.g. iShares Core Equity ETF")
        currency     = c5.selectbox("Currency", ["CAD","USD"])

        c6, c7, c8 = st.columns(3)
        quantity     = c6.number_input("Units / shares", min_value=0.0,
                                       value=0.0, step=0.001, format="%.4f")
        book_value   = c7.number_input("Book value ($)", min_value=0.0,
                                       value=0.0, step=0.01)
        market_value = c8.number_input("Market value ($)*", min_value=0.0,
                                       value=0.0, step=0.01)

        as_of_date = st.date_input("As of date", value=date.today())
        notes      = st.text_input("Notes")

        if st.form_submit_button("Save holding"):
            if not account_name or market_value == 0:
                st.error("Account name and market value are required.")
            else:
                upsert_investment({
                    "account_name":  account_name,
                    "account_type":  account_type,
                    "ticker":        ticker or None,
                    "security_name": security_name or None,
                    "quantity":      quantity or None,
                    "book_value":    book_value or None,
                    "market_value":  market_value,
                    "currency":      currency,
                    "as_of_date":    as_of_date.isoformat(),
                    "notes":         notes or None,
                }, conn)
                conn.commit()
                st.success("Holding saved.")
                st.rerun()

    # TFSA / RRSP contribution tracker (unchanged from prior version).
    st.subheader("Contribution Room Tracker")
    contrib_rows = conn.execute(
        "SELECT * FROM contributions ORDER BY year DESC, account_type"
    ).fetchall()
    contrib_list = [dict(r) for r in contrib_rows]
    if contrib_list:
        df = pd.DataFrame(contrib_list)
        display_cols = [c for c in ["account_type","year","contributed",
                                    "room_available","notes"]
                        if c in df.columns]
        st.dataframe(df[display_cols], use_container_width=True,
                     hide_index=True)

    with st.form("add_contrib"):
        cc1, cc2, cc3 = st.columns(3)
        c_type = cc1.selectbox("Account", ["TFSA","RRSP","FHSA","Other"])
        c_year = cc2.number_input("Year", min_value=2000,
                                  max_value=date.today().year+1,
                                  value=date.today().year, step=1)
        c_contributed = cc3.number_input("Amount contributed ($)",
                                         min_value=0.0, value=0.0,
                                         step=100.0)
        c_room = st.number_input("Room available ($)", min_value=0.0,
                                 value=0.0, step=100.0)
        c_notes = st.text_input("Notes", key="c_notes")

        if st.form_submit_button("Save"):
            conn.execute(
                "INSERT OR REPLACE INTO contributions "
                "(account_type, year, contributed, room_available, notes) "
                "VALUES (?,?,?,?,?)",
                (c_type, int(c_year), c_contributed, c_room or None,
                 c_notes or None),
            )
            conn.commit()
            st.success("Saved.")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════
# Tab 4 — Cash / debts (account_balances)
# ══════════════════════════════════════════════════════════════════════
with tab_balances:
    st.caption(
        "Add cash, savings, credit card, loan, or mortgage balances "
        "manually. These feed net worth alongside your latest holdings "
        "snapshot. Only the most-recent balance per account counts."
    )

    bals = get_account_balances(conn=conn, latest_only=True)
    if bals:
        df_bals = pd.DataFrame(bals)
        cols_show = [c for c in ["account_name","account_kind","balance",
                                 "currency","as_of_date","notes"]
                     if c in df_bals.columns]
        st.dataframe(df_bals[cols_show], use_container_width=True,
                     hide_index=True)
        for b in bals:
            with st.expander(f"Manage {b['account_name']} "
                             f"({b['account_kind']})"):
                if st.button("Delete this balance",
                             key=f"del_bal_{b['id']}"):
                    delete_account_balance(b["id"], conn=conn)
                    conn.commit()
                    st.rerun()
    else:
        st.info("No balances recorded yet.")

    st.subheader("Add / update balance")
    KIND_OPTS = sorted(ASSET_KINDS | LIABILITY_KINDS)
    with st.form("add_balance"):
        b1, b2 = st.columns(2)
        b_name = b1.text_input("Account name*",
                               placeholder="e.g. Tangerine Chequing, Visa")
        b_kind = b2.selectbox("Kind", KIND_OPTS, index=KIND_OPTS.index("cash"))

        b3, b4, b5 = st.columns(3)
        b_amount = b3.number_input("Balance ($)*", value=0.0, step=10.0,
                                   format="%.2f")
        b_currency = b4.selectbox("Currency", ["CAD","USD"])
        b_date = b5.date_input("As of date", value=date.today())

        b_notes = st.text_input("Notes (optional)")

        if st.form_submit_button("Save balance"):
            if not b_name:
                st.error("Account name is required.")
            else:
                # Liabilities are stored as positive numbers; net worth
                # math subtracts them based on account_kind.
                insert_account_balance({
                    "account_name": b_name,
                    "account_kind": b_kind,
                    "balance":      abs(float(b_amount)),
                    "currency":     b_currency,
                    "as_of_date":   b_date.isoformat(),
                    "notes":        b_notes or None,
                }, conn=conn)
                conn.commit()
                st.success("Balance saved.")
                st.rerun()

# ══════════════════════════════════════════════════════════════════════
# Tab 5 — Snapshot history
# ══════════════════════════════════════════════════════════════════════
with tab_history:
    snaps = get_investment_snapshots(conn=conn)
    if not snaps:
        st.info("No imported snapshots yet.")
    else:
        st.caption(f"{len(snaps)} snapshot batch(es) on record.")
        for s in snaps:
            cols = st.columns([2, 2, 2, 1.5, 1])
            cols[0].markdown(f"**{s.get('as_of_date','—')}**")
            cols[1].caption(s.get("source_file") or "—")
            cols[2].caption(f"{s.get('row_count',0)} positions · "
                            f"{s.get('currencies_seen') or '—'}")
            cols[3].caption(
                f"${(s.get('total_market_value_native') or 0):,.2f}"
            )
            if cols[4].button("Remove", key=f"snap_del_{s['id']}"):
                delete_investment_snapshot(s["id"], conn=conn)
                conn.commit()
                st.rerun()

conn.close()

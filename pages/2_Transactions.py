"""
Transactions — searchable, filterable ledger with merchant history and inline editing.
v2.0: session-state filters from Recommendations, merchant detail drawer, watch list integration.
Pass 30 copy: "Drilldown" renamed to "Merchant history" everywhere user-facing.
"""
import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.database import (
    init_db, get_connection, update_transaction, get_watch_list, add_to_watch_list,
)
from utils.insights import merchant_detail
from config.categories import CATEGORIES

from utils.styles import inject_styles
st.set_page_config(page_title="Transactions · Ledger", page_icon="📋", layout="wide")
init_db()
inject_styles()

col_title, col_action = st.columns([5, 1])
with col_title:
    st.title("Transactions")
with col_action:
    if st.button("＋ Add Data", type="primary", use_container_width=True):
        st.switch_page("pages/3_Import.py")

conn = get_connection()

# Pass 16: tiny breadcrumb so the user knows the date range was auto-widened
# by a merchant/category handoff. Helpful when the result count is small —
# they can see why and can narrow back via the Filters expander.
_handoff_msg = None  # set later, just after we know default_merchant/cat

# ── Pre-fill filters from session state (set by Recommendations / Reduce) ──
# Pass 16: when navigating from Reduce or Recommendations with a specific
# merchant, the default 90-day date filter often hides matching rows (e.g.
# Pixverse Singapore last charged 2026-01-05; with today 2026-04-28 the
# default From=today-90d=2026-01-28 excludes every Pixverse row → "search
# returns nothing"). We expand the date window automatically when the caller
# specified a merchant or a category, so the search handoff actually finds
# matching rows. The user can still narrow afterwards via the Filters expander.
default_cat      = st.session_state.pop("txn_category_filter", None)
default_merchant = st.session_state.pop("txn_merchant_filter", None)
# Sentinel: caller wants the full history (no narrow date filter).
_force_all_time  = bool(default_cat or default_merchant) \
                   or st.session_state.pop("txn_period_all", False)

if default_merchant:
    _handoff_msg = (f"📍 Showing all-time results for merchant **{default_merchant}**. "
                    "Date filter widened automatically — narrow it via Filters below if needed.")
elif default_cat:
    _handoff_msg = (f"📍 Showing all-time results for category **{default_cat}**. "
                    "Date filter widened automatically — narrow it via Filters below if needed.")
if _handoff_msg:
    st.info(_handoff_msg)

# ── PROMINENT SEARCH (top of page, full-width) ──────────────────────────
# Hoisted out of the Filters expander so it's always visible without
# extra clicks. Pass 14 manual-test feedback: search felt "buried" inside
# the expander and wasn't getting used.
st.markdown('<p class="ledger-section-header">Search</p>', unsafe_allow_html=True)
search_term = st.text_input(
    "Search transactions",
    value=default_merchant or "",
    placeholder="Type to search merchant, description, category, notes, or amount — e.g. mortgage, groceries, OpenAI, Cash Advance, 124.50",
    help=("Case-insensitive substring match across merchant, raw description, "
          "category, notes, and the amount text. Combines with all filters below. "
          "Search is applied client-side after the SQL filters run."),
    label_visibility="collapsed",
    key="txn_search_input",
)

# ── Quick chips (right under the search box for visibility) ─────────────
chip_options = {
    "none":          "All",
    "flagged":       "⚑ Flagged",
    "uncategorized": "❔ Uncategorized",
    "low_conf":      "🟡 Low confidence",
    "subscriptions": "🔁 Subscriptions",
    "large_debits":  "💸 Large debits ($500+)",
    "transfers":     "🔄 Transfers",
    "cash_advance":  "🚨 Cash advance",
}
quick_choice = st.radio(
    "Quick filter",
    options=list(chip_options.keys()),
    index=0,
    horizontal=True,
    format_func=lambda k: chip_options[k],
    key="txn_quick_chip_selector",
    help=("One-click shortcuts that combine with the filters below. Pick **All** to clear."),
    label_visibility="collapsed",
)

# ── Filters (date / category / account / direction) ─────────────────────
with st.expander("Filters", expanded=False):
    f1, f2, f3, f4, f5 = st.columns(5)
    today = date.today()

    # When a caller asked for all-time (e.g. Reduce → "See merchant txns"),
    # default the From-date to the earliest imported month so the search
    # handoff actually finds matching rows.
    if _force_all_time:
        _earliest_row = conn.execute(
            "SELECT MIN(transaction_date) AS m FROM transactions"
        ).fetchone()
        _earliest = _earliest_row["m"] if _earliest_row and _earliest_row["m"] else None
        try:
            _from_default = (date.fromisoformat(_earliest)
                             if _earliest else today - timedelta(days=365 * 5))
        except (TypeError, ValueError):
            _from_default = today - timedelta(days=365 * 5)
    else:
        _from_default = today - timedelta(days=90)
    start_date = f1.date_input("From", value=_from_default)
    end_date   = f2.date_input("To",   value=today)
    all_cats   = ["All"] + sorted(CATEGORIES)
    cat_default_idx = all_cats.index(default_cat) if default_cat in all_cats else 0
    cat_filter = f3.selectbox("Category", all_cats, index=cat_default_idx)
    acc_filter = f4.selectbox("Account",  ["All", "chequing", "mastercard", "csv"])
    dir_filter = f5.selectbox("Direction", ["All", "debit", "credit", "transfer", "payment", "cancelled"])

    show_transfers = st.checkbox(
        "Include transfers & CC payments",
        value=False,
        help=(
            "Hidden by default — these are account moves, not real spending "
            "(savings↔chequing, e-Transfers, Mastercard payoff). Turn on to audit them."
        ),
    )
    show_flagged   = st.checkbox("Flagged only", value=False)

# ── Build query ──────────────────────────────────────────────────────────
conditions = ["transaction_date BETWEEN ? AND ?"]
params     = [start_date.isoformat(), end_date.isoformat()]

# Quick-chip overrides (applied alongside other filters). When a chip selects
# transfers we MUST relax the show_transfers gate so they appear.
chip_force_show_transfers = quick_choice == "transfers"
if (not show_transfers) and (not chip_force_show_transfers):
    conditions.append("is_transfer = 0")
    conditions.append("direction NOT IN ('payment', 'cancelled')")

if cat_filter != "All":
    conditions.append("category = ?")
    params.append(cat_filter)

if acc_filter != "All":
    conditions.append("account_type = ?")
    params.append(acc_filter)

if dir_filter != "All":
    conditions.append("direction = ?")
    params.append(dir_filter)

if show_flagged:
    conditions.append("is_flagged = 1")

# Quick-chip SQL filters
if quick_choice == "flagged":
    conditions.append("is_flagged = 1")
elif quick_choice == "uncategorized":
    conditions.append("(category IS NULL OR category = '' OR category = 'Misc')")
elif quick_choice == "low_conf":
    conditions.append("parse_confidence = 'low'")
elif quick_choice == "subscriptions":
    conditions.append("category = 'Subscriptions & Digital'")
elif quick_choice == "large_debits":
    conditions.append("direction = 'debit' AND ABS(amount) >= 500")
elif quick_choice == "transfers":
    # Show transfer-like rows: direction='transfer' OR Transfer Out/In categories.
    conditions.append(
        "(direction='transfer' OR category IN ('Transfer','Transfer Out','Transfer In'))"
    )
elif quick_choice == "cash_advance":
    conditions.append("category = 'Cash Advance'")

where = "WHERE " + " AND ".join(conditions)
rows = conn.execute(
    f"SELECT * FROM transactions {where} ORDER BY transaction_date DESC",
    params,
).fetchall()
df = pd.DataFrame([dict(r) for r in rows])

# Search filter (client-side, extends across merchant/description/category/notes/amount)
#
# Pass 17 fix — `regex=False` everywhere
# ──────────────────────────────────────
# Manual testing reported "OpenAI ChatGPT" and "Patreon" handoffs from Reduce
# returning 0 results. Root cause: `str.contains` defaults to regex=True. Live
# merchant strings like 'Openai *Chatgpt Subscr San Franciscoca' and
# 'Patreon* Membership Internet' contain literal `*` characters; in regex `*`
# is a quantifier, so the actual `*` in the data never matched. Pixverse
# Singapore (no metachars) worked, masking the bug. Fix: pass regex=False
# everywhere so the needle is treated as a literal substring.
#
# Pass 17 alias fallback
# ──────────────────────
# When the exact merchant string returns 0 rows, try a list of common alias
# tokens derived from the original needle (e.g. "OpenAI" / "ChatGPT" for
# "Openai *Chatgpt Subscr ..."). This makes Reduce's "See merchant
# transactions" deep links robust even when the stored merchant differs in
# punctuation/spacing from the user's mental model.
if search_term and not df.empty:
    needle = str(search_term).strip()
    if needle:
        amount_str = df["amount"].apply(lambda v: f"{abs(v):.2f}" if v is not None else "")
        def _build_mask(n: str):
            return (
                df["raw_description"].astype(str).str.contains(n, case=False, na=False, regex=False) |
                df["merchant"].astype(str).str.contains(n, case=False, na=False, regex=False) |
                df["category"].astype(str).str.contains(n, case=False, na=False, regex=False) |
                df["notes"].astype(str).str.contains(n, case=False, na=False, regex=False) |
                amount_str.str.contains(n.lstrip("$").replace(",", ""), case=False, na=False, regex=False)
            )
        mask = _build_mask(needle)
        # If the literal needle returns nothing, try a small set of robust
        # aliases. The aliases handle both Tangerine quirks (PATREON*,
        # OPENAI *CHATGPT) and casual user input ("OpenAI" instead of the
        # full city-suffixed merchant string).
        if not mask.any():
            from re import sub as _re_sub
            up = needle.upper()
            aliases = []
            # Generic: split off the first 1-2 words as a robust prefix token.
            first_word = needle.split()[0] if needle.split() else ""
            if first_word and len(first_word) >= 4:
                # Strip Tangerine-style "*" punctuation
                aliases.append(first_word.replace("*", "").strip())
            # Targeted aliases for the merchants the user explicitly listed.
            if "OPENAI" in up or "CHATGPT" in up:
                aliases.extend(["OpenAI", "ChatGPT"])
            if "PATREON" in up:
                aliases.append("Patreon")
            if "PIXVERSE" in up:
                aliases.append("Pixverse")
            if "AMAZON CHANNELS" in up:
                aliases.append("Amazon Channels")
            if "DISNEY" in up:
                aliases.append("Disney")
            if "ROGERS" in up:
                aliases.append("Rogers")
            if "UTILITY" in up:
                aliases.extend(["Hydro", "Internet", "Phone"])
            if "MORTGAGE" in up:
                aliases.append("Mortgage")
            if "CREEM" in up or "IMAGINEX" in up:
                aliases.extend(["Imaginex", "Creem"])
            # Keep tokens unique while preserving order.
            seen = set()
            aliases = [a for a in aliases if a and not (a.lower() in seen or seen.add(a.lower()))]
            for alias in aliases:
                am = _build_mask(alias)
                if am.any():
                    mask = am
                    break
        df = df[mask]

# ── Watch list set ──────────────────────────────────────────────────────
watch_list = get_watch_list(conn=conn)
watch_names = {w["merchant"] for w in watch_list}

# ── Summary strip ────────────────────────────────────────────────────────
chip_caption = ""
if quick_choice != "none":
    chip_caption = f" · chip: <b>{chip_options[quick_choice]}</b>"
search_caption = f" · search: <code>{search_term}</code>" if search_term else ""
st.markdown(
    f"<div style='font-size:0.95rem;color:#e6edf3;margin:8px 0;'>"
    f"Showing <b>{len(df)}</b> transaction(s){chip_caption}{search_caption}."
    f"</div>",
    unsafe_allow_html=True,
)

if not df.empty:
    total_debit  = df[df["direction"] == "debit"]["amount"].apply(abs).sum()
    total_credit = df[df["direction"] == "credit"]["amount"].apply(abs).sum()
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Transactions", len(df))
    s2.metric("Total Spent",  f"${total_debit:,.2f}")
    s3.metric("Total Income", f"${total_credit:,.2f}")
    s4.metric("Net",          f"${total_credit - total_debit:,.2f}")

    # Display table
    display_cols = [
        "id", "transaction_date", "merchant", "category", "direction",
        "amount", "currency", "is_flagged", "flag_reason", "parse_confidence", "raw_description"
    ]
    display_cols = [c for c in display_cols if c in df.columns]
    disp = df[display_cols].copy()

    # Highlight watch-list merchants
    if "merchant" in disp.columns:
        disp["watched"] = disp["merchant"].apply(lambda m: "👀" if m in watch_names else "")

    disp["amount"]     = disp["amount"].apply(lambda x: f"${abs(x):,.2f}")
    disp["is_flagged"] = disp["is_flagged"].apply(lambda x: "⚑" if x else "")
    disp.columns       = [c.replace("_", " ").title() for c in disp.columns]

    st.dataframe(disp, use_container_width=True, hide_index=True)

    st.divider()

    # ── Merchant history (Pass 30 rename from "Drilldown") ─────────────
    st.subheader("Merchant history")
    all_merchants = sorted(df["merchant"].dropna().unique().tolist()) if "merchant" in df.columns else []

    if all_merchants:
        selected_merchant = st.selectbox(
            "Select a merchant to view its history",
            ["— select —"] + all_merchants,
        )
        if selected_merchant != "— select —":
            detail = merchant_detail(selected_merchant, conn=conn)
            stats  = detail.get("stats", {})

            dc1, dc2, dc3, dc4, dc5 = st.columns(5)
            dc1.metric("Transactions",  stats.get("tx_count", 0))
            dc2.metric("Total paid",    f"${stats.get('total_paid', 0):,.2f}")
            dc3.metric("Avg charge",    f"${stats.get('avg_amount', 0):,.2f}")
            dc4.metric("Range",         f"${stats.get('min_amount', 0):,.2f} – ${stats.get('max_amount', 0):,.2f}")
            dc5.metric("Category",      stats.get("category", "—"))

            if stats.get("first_seen"):
                st.caption(
                    f"First seen: **{stats['first_seen']}** · Last seen: **{stats['last_seen']}**"
                )

            # Monthly breakdown table
            monthly = detail.get("monthly", [])
            if monthly:
                st.markdown("**Monthly charges:**")
                mon_df = pd.DataFrame(monthly)
                mon_df.columns = ["Month", "Total", "Count"]
                mon_df["Total"] = mon_df["Total"].apply(lambda x: f"${x:,.2f}")
                st.dataframe(mon_df, use_container_width=True, hide_index=True)

            # All transactions
            txs = detail.get("transactions", [])
            if txs:
                with st.expander(f"All {len(txs)} transactions for {selected_merchant}"):
                    tx_df = pd.DataFrame(txs)[
                        ["transaction_date", "raw_description", "amount", "category", "parse_confidence"]
                    ]
                    tx_df["amount"] = tx_df["amount"].apply(lambda x: f"${abs(x):,.2f}")
                    tx_df.columns   = ["Date", "Raw Description", "Amount", "Category", "Confidence"]
                    st.dataframe(tx_df, use_container_width=True, hide_index=True)

            # Watch list toggle
            is_watched = selected_merchant in watch_names
            wl1, wl2 = st.columns(2)
            if not is_watched:
                if wl1.button(f"👀 Add '{selected_merchant}' to Watch List"):
                    add_to_watch_list(selected_merchant, "Added from Transactions page", conn=conn)
                    conn.commit()
                    st.success(f"Added {selected_merchant} to watch list.")
                    st.rerun()
            else:
                wl1.success(f"'{selected_merchant}' is on your watch list.")

    st.divider()

    # ── Inline edit ─────────────────────────────────────────────────────
    st.subheader("Edit a Transaction")
    with st.form("edit_tx"):
        tx_id = st.number_input("Transaction ID", min_value=1, step=1)
        new_cat  = st.selectbox("Category", CATEGORIES)
        new_note = st.text_input("Notes")
        clear_flag = st.checkbox("Clear flag")
        if st.form_submit_button("Save"):
            upd = {"category": new_cat, "notes": new_note}
            if clear_flag:
                upd["is_flagged"]  = 0
                upd["flag_reason"] = ""
            update_transaction(int(tx_id), upd, conn=conn)
            conn.commit()
            st.success(f"Transaction {tx_id} updated.")
            st.rerun()

else:
    st.info("No transactions match the current filters.")

conn.close()

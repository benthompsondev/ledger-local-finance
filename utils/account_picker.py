"""
Shared Streamlit account-selection widgets.

Used by the Add Data upload flow and manual entry so account selection
and quick creation behave identically everywhere. Pure UI — all writes
go through utils.database account CRUD.
"""
from __future__ import annotations

import streamlit as st

from utils.database import (
    ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS, create_account, list_accounts,
)
# Re-exported for existing callers; the pure helpers moved to
# utils.account_assignment so the native desktop sidecar can use them
# without pulling in Streamlit.
from utils.account_assignment import (  # noqa: F401
    DETECTED_TYPE_TO_ACCOUNT_TYPE, apply_account_to_transactions,
)


def render_new_account_form(conn, *, key: str,
                            title: str = "Create a new account"):
    """Inline account-creation form. Returns the new account id or None."""
    with st.form(f"new_account_{key}", clear_on_submit=True):
        st.markdown(f"**{title}**")
        c1, c2 = st.columns([2, 1.4])
        name = c1.text_input("Account name*",
                             placeholder="e.g. Everyday Chequing")
        acct_type = c2.selectbox(
            "Type*", ACCOUNT_TYPES,
            format_func=lambda t: ACCOUNT_TYPE_LABELS.get(t, t))
        c3, c4, c5 = st.columns([1.6, 1, 1.2])
        institution = c3.text_input("Bank / institution (optional)")
        currency = c4.text_input("Currency", value="CAD", max_chars=3)
        opening = c5.number_input("Opening balance ($)", value=0.0,
                                  step=100.0, format="%.2f",
                                  help="What the account held before "
                                       "your first imported/entered "
                                       "transaction. Liabilities: the "
                                       "amount owed.")
        if st.form_submit_button("Create account", type="primary"):
            try:
                aid = create_account({
                    "name": name, "type": acct_type,
                    "institution": institution, "currency": currency,
                    "opening_balance": opening,
                }, conn=conn)
                conn.commit()
                st.success(f"Account “{name.strip()}” created.")
                return aid
            except ValueError as e:
                st.error(str(e))
    return None


def account_selectbox(conn, *, key: str, label: str = "Account",
                      suggested_id=None, help_text: str = ""):
    """Selectbox over active accounts. Returns the account dict or None
    (when no accounts exist). Never renders an empty selector — callers
    should show render_new_account_form when this returns None."""
    accounts = [a for a in list_accounts(conn=conn)
                if not a.get("is_archived")]
    if not accounts:
        return None
    ids = [a["id"] for a in accounts]
    by_id = {a["id"]: a for a in accounts}
    default_idx = 0
    if suggested_id in by_id:
        default_idx = ids.index(suggested_id)
    picked = st.selectbox(
        label, ids, index=default_idx,
        format_func=lambda i: (
            f"{by_id[i]['name']} · "
            f"{ACCOUNT_TYPE_LABELS.get(by_id[i]['type'], by_id[i]['type'])}"
            + (f" · {by_id[i]['institution']}"
               if by_id[i].get("institution") else "")),
        key=key, help=help_text or None)
    return by_id.get(picked)

"""
Cross-page navigation helpers (Pass 18).

Why this exists
───────────────
Reduce, Recommendations, Dashboard, and Spending all link into the
Transactions page by setting `st.session_state["txn_merchant_filter"]`
(or `txn_category_filter`) plus `txn_period_all=True` and then calling
`st.switch_page("pages/2_Transactions.py")`. That logic was duplicated
in 4+ places, with subtle differences (some pages forgot the period
sentinel, some didn't set the search box pre-fill, etc.).

The Transactions page already has alias-fallback for stored merchant
strings with literal `*` (Tangerine "OPENAI *CHATGPT", "PATREON*"). This
module surfaces the same alias map so other pages can preview which
search token is most likely to actually return rows when displaying
"See merchant transactions" buttons.

Public helpers:

  merchant_search_token(merchant)
      Cleanest single token to put in the search box (e.g. "OpenAI" for
      "Openai *Chatgpt Subscr San Franciscoca"). Used to display a
      preview tooltip; not required for the link to work.

  transaction_search_aliases(merchant)
      Ordered list of fallback tokens. Mirrors the alias list in
      pages/2_Transactions.py so callers know what the page will try.

  set_transaction_search(merchant=None, category=None, all_time=True)
      Set the session-state keys that pages/2_Transactions.py reads.
      Caller still drives the actual `st.switch_page(...)` call so this
      module stays Streamlit-import-free at module load time.
"""
from __future__ import annotations

from typing import Optional


# Mirror of the alias cases handled by pages/2_Transactions.py. Centralising
# here so future merchants can be added in one place. Keys are matched
# case-insensitively against the user-supplied needle (or stored merchant).
_MERCHANT_ALIASES: dict[str, list[str]] = {
    "OPENAI":          ["OpenAI", "ChatGPT"],
    "CHATGPT":         ["OpenAI", "ChatGPT"],
    "PATREON":         ["Patreon"],
    "PIXVERSE":        ["Pixverse"],
    "AMAZON CHANNELS": ["Amazon Channels"],
    "DISNEY":          ["Disney"],
    "ROGERS":          ["Rogers"],
    "UTILITY":         ["Hydro", "Internet", "Phone"],
    "MORTGAGE":        ["Mortgage"],
    "CREEM":           ["Creem", "Imaginex"],
    "IMAGINEX":        ["Imaginex", "Creem"],
}


def merchant_search_token(merchant: Optional[str]) -> str:
    """Best clean substring to display in the search box for a stored merchant.

    Examples:
        "Openai *Chatgpt Subscr San Franciscoca" → "OpenAI"
        "PATREON* Membership Internet"            → "Patreon"
        "Pixverse Singapore"                      → "Pixverse"
    Falls back to the first whitespace-delimited word with '*' stripped.
    """
    if not merchant:
        return ""
    up = str(merchant).upper()
    for needle, aliases in _MERCHANT_ALIASES.items():
        if needle in up and aliases:
            return aliases[0]
    first_word = str(merchant).split()[0] if str(merchant).split() else str(merchant)
    return first_word.replace("*", "").strip()


def transaction_search_aliases(merchant: Optional[str]) -> list[str]:
    """Ordered list of fallback search tokens for a stored merchant.

    Mirrors what pages/2_Transactions.py tries when the literal merchant
    needle returns 0 rows. Always includes the merchant itself first.
    """
    if not merchant:
        return []
    out: list[str] = [str(merchant)]
    up = str(merchant).upper()
    for needle, aliases in _MERCHANT_ALIASES.items():
        if needle in up:
            out.extend(aliases)
    # Generic prefix
    first_word = str(merchant).split()[0] if str(merchant).split() else ""
    if first_word and len(first_word) >= 4:
        out.append(first_word.replace("*", "").strip())
    # De-dup preserving order, case-insensitively.
    seen: set[str] = set()
    deduped: list[str] = []
    for token in out:
        key = token.lower()
        if token and key not in seen:
            seen.add(key)
            deduped.append(token)
    return deduped


def set_transaction_search(
    *,
    merchant: Optional[str] = None,
    category: Optional[str] = None,
    all_time: bool = True,
) -> None:
    """Populate Transactions-page session-state keys.

    Caller still does the `st.switch_page(...)` afterwards. This is a
    pure session-state mutator so non-Streamlit callers (tests) can
    invoke it without crashing on missing context.
    """
    try:
        import streamlit as st
    except Exception:
        return
    if merchant is not None:
        st.session_state["txn_merchant_filter"] = merchant
    if category is not None:
        st.session_state["txn_category_filter"] = category
    if all_time:
        st.session_state["txn_period_all"] = True

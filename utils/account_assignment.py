"""Pure account-assignment helpers shared by every SignalSpace frontend.

These used to live in utils.account_picker, but that module imports
Streamlit at the top, which the native desktop sidecar must not bundle.
Anything here must stay import-safe without Streamlit.
"""
from __future__ import annotations

# Detected statement types → the account type that usually receives them.
DETECTED_TYPE_TO_ACCOUNT_TYPE = {
    "chequing": "chequing",
    "savings": "savings",
    "mastercard": "credit_card",
}

# Tangerine PDF parsers set account_type per row and statement-summary
# lookups depend on it — imports of these detected types must preserve it.
PDF_STATEMENT_TYPES = ("chequing", "savings", "mastercard")


def apply_account_to_transactions(txs: list[dict], account: dict,
                                  *, keep_account_type: bool = False
                                  ) -> None:
    """Stamp account identity onto parsed transactions in place.

    keep_account_type=True preserves the parser-provided account_type
    string (Tangerine PDF parsers set it and statement-summary lookups
    depend on it); otherwise the account's own type token is written.
    """
    for tx in txs:
        tx["account_ref"] = int(account["id"])
        tx["account_id"] = account["name"]
        if not keep_account_type:
            tx["account_type"] = account["type"]

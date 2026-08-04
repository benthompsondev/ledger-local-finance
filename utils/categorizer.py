"""
Rule-based categorizer. Applies rules from config/rules.py to assign
category and subcategory to a transaction based on merchant/description.

direction / is_transfer contract
─────────────────────────────────
direction values and what they mean:
  'credit'    — real money in (payroll, e-Transfer received, refund, interest)
  'debit'     — real money out (purchases, e-Transfers sent, mortgage)
  'transfer'  — internal account move (savings↔chequing) — NOT cashflow
  'payment'   — CC payment from chequing — NOT cashflow
  'cancelled' — reversed/cancelled transaction — NOT cashflow

is_transfer = 1 for ALL of the above except 'credit'/'debit'. It is also 1
              when an ordinary signed row is explicitly categorized as an
              internal transfer or credit-card payment.

IMPORTANT: is_transfer is set twice:
  1. Early, by is_transfer() keyword/direction scan (fast, may be incomplete)
  2. Late, by enrich_transaction() consistency pass (authoritative)
Always use the value from the DB after enrich_transaction() has run.
"""
import re
import unicodedata
from config.rules import RULES
from config.categories import CATEGORIES, NON_SPENDING_CATEGORIES


def _lookup_learned_rule(merchant_normalized: str, conn=None):
    """Consult the learned_rules table (user corrections promoted to rules).
    Returns a rule dict or None. Safe to call before
    init_db() — returns None if the table is absent."""
    if not merchant_normalized:
        return None
    try:
        from utils.database import get_connection
    except Exception:
        return None
    close = False
    try:
        if conn is None:
            conn = get_connection()
            close = True
        row = conn.execute(
            "SELECT * FROM learned_rules "
            "WHERE merchant_normalized=? AND COALESCE(enabled,1)=1 "
            "ORDER BY id LIMIT 1",
            (merchant_normalized,),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        return None
    finally:
        if close and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return None


def normalize_merchant(raw: str) -> str:
    """Return a conservative, deterministic merchant matcher.

    The output is an uppercase exact key. Only high-confidence statement
    noise is removed; meaningful embedded words and digits stay intact.
    """
    if not isinstance(raw, str):
        return ""
    s = unicodedata.normalize("NFKC", raw).strip().upper()
    if not s:
        return ""

    institution_prefixes = (
        "CANCELLED INTERAC E-TRANSFER TO:",
        "CANCELLED INTERAC E-TRANSFER FROM:",
        "INTERAC E-TRANSFER FROM:", "INTERAC E-TRANSFER TO:",
        "EFT DEPOSIT FROM", "EFT WITHDRAWAL TO",
        "INTERNET DEPOSIT FROM", "INTERNET WITHDRAWAL TO",
    )
    for prefix in institution_prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):].strip(" :-*")
            break

    # Common card/payment processors. Require punctuation after the prefix so
    # a real merchant beginning with the same letters is not stripped.
    s = re.sub(r"^(?:SQ|TST|PAYPAL|PP|SP)\s*[*.:-]\s*", "", s)

    # Explicitly delimited city/province fragments. Run this before comma
    # normalization so the delimiter remains available as a conservative
    # signal that the trailing words are statement location noise.
    province = r"(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT|CA)"
    s = re.sub(
        rf"\s*(?:-|,)\s*[A-Z][A-Z .'-]{{2,}}\s+{province}$", "", s
    )

    # Normalize punctuation to token boundaries while preserving ampersands,
    # apostrophes, and meaningful hyphens inside merchant names.
    s = re.sub(r"[|_/\\,;]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .:-*#")

    # Processor/reference/order tokens at the end.
    s = re.sub(
        r"\s+(?:REF(?:ERENCE)?|AUTH|CONF(?:IRMATION)?|ORDER|TXN|TRX|ID)"
        r"\s*[:#-]?\s*[A-Z0-9-]{5,}$",
        "", s,
    )
    s = re.sub(r"\s+[A-Z]{0,4}\d[A-Z0-9-]{7,}$", "", s)

    # Terminal/store suffixes and standalone trailing store numbers.
    s = re.sub(
        r"\s+(?:STORE|STO|TERMINAL|TERM|TML|POS)\s*#?\s*\d{2,6}$",
        "", s,
    )
    s = re.sub(r"\s+#\s*\d{2,6}$", "", s)

    # Date-like suffixes separated from the merchant name.
    s = re.sub(
        r"\s+(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|"
        r"\d{1,2}[-/.]\d{1,2}(?:[-/.]20\d{2})?)$",
        "", s,
    )

    # Compact processor locations such as SAN FRANCISCOCA.
    s = re.sub(r"\s+SAN\s+FRANCISCOCA$", "", s)

    s = re.sub(r"\s+", " ", s).strip(" .:-*#")
    return s


_PROTECTED_CATEGORIES = frozenset({
    "Transfer", "Internal Transfer", "Credit Card Payment", "Payment",
    "Cancelled", "Refund / Credit", "Reimbursement / Insurance Reimbursement",
    "Fees / Interest", "Cash Advance", "Interest Income", "Rewards / Cashback",
})


_STATEMENT_MEMO_CATEGORIES = {
    "RESTAURANT": ("Food & Convenience", "Restaurant"),
    "E-GAMES": ("Entertainment", "Games"),
    "ENTERTAINMENT": ("Entertainment", "Entertainment"),
    "GAS": ("Gas / Transport", "Gas"),
    "GROCERIES": ("Groceries", "Groceries"),
    "PARKING": ("Gas / Transport", "Parking"),
    "BILL PAYMENT": ("Utilities / Bills", "Bill payment"),
    "HOME IMPROVEMENT": ("Home Improvement", "Home improvement"),
    "DRUG STORE": ("Health / Care", "Drug store"),
}


def statement_memo_category(memo: str) -> tuple[str, str] | None:
    """Extract a bank-supplied purchase category from a Tangerine memo.

    ``Other`` and foreign-currency markers are deliberately not mapped. They
    carry no useful spending meaning, so merchant rules get the next chance.
    """
    match = re.search(r"\bCATEGORY:\s*([^~]+?)\s*$", str(memo or ""), re.I)
    if not match:
        return None
    return _STATEMENT_MEMO_CATEGORIES.get(match.group(1).strip().upper())


def protected_category(raw_description: str, amount: float, direction: str,
                       static_category: str) -> tuple[str | None, str | None]:
    """Return a protected category and reason when a user rule must not win."""
    desc = (raw_description or "").upper()
    if direction == "payment":
        return "Credit Card Payment", "credit-card payment detection"
    if direction == "transfer":
        return "Transfer", "internal transfer detection"
    if direction == "cancelled":
        return "Cancelled", "cancelled transaction detection"
    if static_category in _PROTECTED_CATEGORIES:
        return static_category, f"protected built-in {static_category} rule"
    if "CASH ADVANCE" in desc or "ATM ADVANCE" in desc:
        return "Cash Advance", "cash advance detection"
    if (re.search(r"\bNSF\b", desc)
            or any(k in desc for k in ("SERVICE CHARGE", "ANNUAL FEE", "LATE PAYMENT FEE"))):
        return "Fees / Interest", "fee/interest detection"
    if direction == "credit" and float(amount or 0) < 0:
        return "Refund / Credit", "refund/credit direction detection"
    if "REIMBURSE" in desc:
        return "Reimbursement / Insurance Reimbursement", "reimbursement detection"
    return None, None


def categorize(raw_description: str, amount: float = 0.0, direction: str = "debit") -> tuple[str, str, float]:
    """
    Returns (category, subcategory, confidence_score 0.0-1.0).
    confidence_score: 1.0 = exact match, 0.8 = keyword match, 0.5 = fallback
    """
    raw_text = str(raw_description or "")
    desc_upper = raw_text.upper()

    # Walk rules in priority order
    # Rules can be dicts {pattern, match_type, category, subcategory, confidence}
    # OR legacy tuples (pattern, category, subcategory, is_recurring)
    for rule in RULES:
        if isinstance(rule, dict):
            pattern    = rule.get("pattern", "")
            match_type = rule.get("match_type", "contains")
            cat        = rule.get("category", "Uncategorized")
            sub        = rule.get("subcategory", "")
            conf       = rule.get("confidence", 1.0)
        elif isinstance(rule, (tuple, list)) and len(rule) >= 2:
            pattern    = rule[0]
            cat        = rule[1] if len(rule) > 1 else "Uncategorized"
            sub        = rule[2] if len(rule) > 2 else ""
            conf       = 1.0
            match_type = "contains"
        else:
            continue

        if cat is None:  # skip rows (Opening Balance etc)
            continue

        matched = False
        if match_type == "contains":
            matched = pattern.upper() in desc_upper
        elif match_type == "startswith":
            matched = desc_upper.startswith(pattern.upper())
        elif match_type == "endswith":
            matched = desc_upper.endswith(pattern.upper())
        elif match_type == "regex":
            matched = bool(re.search(pattern, raw_text, re.IGNORECASE))
        elif match_type == "exact":
            matched = desc_upper == pattern.upper()

        if matched:
            return cat, sub or "", conf

    # Fallback
    return "Uncategorized", "", 0.5


def determine_direction(raw_description: str, amount: float, account_type: str) -> str:
    """
    Determine the semantic direction of a transaction.
    Returns one of: 'debit' | 'credit' | 'transfer' | 'payment' | 'cancelled'
    """
    desc_upper = raw_description.upper()

    if account_type == "chequing":
        if desc_upper.startswith("CANCELLED"):
            return "cancelled"
        if "TANGERINE CREDIT CARD PAYMENT" in desc_upper:
            return "payment"
        if "INTERNET DEPOSIT FROM TANGERINE SAVINGS" in desc_upper:
            return "transfer"
        if "INTERNET DEPOSIT" in desc_upper and "SAVINGS" in desc_upper:
            return "transfer"
        if any(k in desc_upper for k in ["EFT DEPOSIT", "INTERAC E-TRANSFER FROM:", "INTEREST PAID",
                                          "DEPOSIT FROM DRAFTERS", "DEPOSIT FROM SHAKEPAY"]):
            return "credit"
        if any(k in desc_upper for k in ["EFT WITHDRAWAL", "INTERAC E-TRANSFER TO:",
                                          "INTERNET TRANSFER TO", "NSF"]):
            return "debit"
        # Amount sign fallback: chequing amounts are always positive in the PDF,
        # direction already resolved above in parser — this is a safety net
        return "debit"

    elif account_type == "savings":
        # Savings parser already sets direction before calling enrich_transaction.
        # This branch handles any re-categorization passes.
        if "INTERNET WITHDRAWAL TO TANGERINE" in desc_upper:
            return "transfer"   # internal — savings → chequing
        if "INTERNET TRANSFER TO" in desc_upper:
            return "transfer"
        if "INTEREST PAID" in desc_upper or "INTEREST EARNED" in desc_upper:
            return "credit"
        if "EFT DEPOSIT" in desc_upper or "MANULIFE" in desc_upper:
            return "credit"
        if "REWARDS REDEMPTION" in desc_upper:
            return "credit"
        return "credit"  # savings entries are deposits by default

    elif account_type == "mastercard":
        if "PAYMENT - THANK YOU" in desc_upper:
            return "payment"
        return "credit" if amount > 0 else "debit"

    # Generic CSV
    return "credit" if amount > 0 else "debit"


def is_transfer(raw_description: str, direction: str) -> bool:
    """Flag transactions that represent money moving between own accounts."""
    desc_upper = raw_description.upper()
    if direction in ("payment", "cancelled", "transfer"):
        return True
    transfer_keywords = [
        "TANGERINE CREDIT CARD PAYMENT",
        "INTERNET DEPOSIT FROM TANGERINE SAVINGS",
        "INTERNET WITHDRAWAL TO TANGERINE CHEQUING",
        "INTERNET TRANSFER",
        "TRANSFER TO SAVINGS",
        "TRANSFER FROM SAVINGS",
    ]
    return any(k in desc_upper for k in transfer_keywords)


def should_flag(raw_description: str, direction: str, amount: float,
                parse_confidence: float, transaction_type: str = "",
                category: str = "") -> tuple[bool, str]:
    """Return (should_flag, reason) for manual review."""
    desc_upper = raw_description.upper()
    reasons = []

    if transaction_type == "ambiguous_inflow":
        reasons.append("confirm whether this inflow is income or a transfer")
    if parse_confidence < 0.6 and amount >= 50:
        reasons.append("meaningful uncategorized transaction")
    if "CASH ADVANCE" in desc_upper:
        reasons.append("cash advance")
    if direction == "cancelled":
        reasons.append("cancelled transaction — verify netting")
    if (amount >= 500 and direction == "debit"
            and transaction_type == "spending" and category == "Uncategorized"):
        reasons.append("large uncategorized purchase")
    from utils.financial_semantics import MATERIAL_OFFSET_REVIEW_TYPES
    if amount >= 500 and transaction_type in MATERIAL_OFFSET_REVIEW_TYPES:
        reasons.append("large credit may materially change spending")
    if re.search(r"\bNSF\b", desc_upper) or "RETURNED" in desc_upper:
        reasons.append("NSF/returned item")

    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def enrich_transaction(tx: dict, conn=None, *, use_learned_rules: bool = True) -> dict:
    """
    Apply categorization, direction, transfer flag, and review flag to a raw transaction dict.
    Modifies and returns the dict in-place.
    """
    raw = str(tx.get("raw_description") or "")
    amount = tx.get("amount", 0.0)
    account_type = tx.get("account_type", "")

    # Direction (parser may have already set this — don't override if set)
    if not tx.get("direction"):
        tx["direction"] = determine_direction(raw, amount, account_type)

    # Transfer flag
    if not tx.get("is_transfer"):
        tx["is_transfer"] = 1 if is_transfer(raw, tx["direction"]) else 0

    # Merchant normalization. Keep a readable display merchant while storing
    # an explicit stable matcher for rules and grouping.
    merchant_normalized = normalize_merchant(raw)
    if not tx.get("merchant"):
        tx["merchant"] = merchant_normalized.title()
    tx["merchant_normalized"] = merchant_normalized

    # Categorization — skip for payments/cancelled (internal movements)
    # NOTE: INTERAC e-Transfers (direction='debit'/'credit', not direction='transfer')
    # are real cashflow. A person's cleaned name is deliberately never used as
    # an automatic merchant classifier: only the full bank description and an
    # explicit user bulk correction can classify these rows.
    static_cat, static_sub, static_conf = categorize(raw, amount, tx["direction"])
    memo_category = tx.get("statement_category")
    if isinstance(memo_category, str) and memo_category:
        memo_category = (memo_category, str(tx.get("statement_subcategory") or ""))
    if not memo_category and tx.get("statement_memo"):
        memo_category = statement_memo_category(str(tx["statement_memo"]))
    protected_cat, protected_reason = protected_category(
        raw, amount, tx["direction"], static_cat
    )

    if tx["direction"] in ("payment", "cancelled"):
        # Pure internal: CC payment from chequing, savings pullback
        tx["category"] = tx["direction"].title()
        tx["subcategory"] = ""
        tx["parse_confidence"] = "high"
        tx["category_source"] = "protected"
        tx["category_explanation"] = f"Categorized by {protected_reason}."
        tx["category_rule_id"] = None
    elif tx["direction"] == "transfer":
        # Internal account movement (savings → chequing or chequing savings pullback)
        tx["category"] = "Transfer"
        # Set meaningful subcategory based on description
        desc_u = raw.upper()
        if "INTERNET WITHDRAWAL TO TANGERINE CHEQUING" in desc_u:
            tx["subcategory"] = "Internal Savings -> Chequing"
        elif "INTERNET DEPOSIT FROM TANGERINE SAVINGS" in desc_u:
            tx["subcategory"] = "Internal Savings -> Chequing"
        else:
            tx["subcategory"] = ""
        tx["parse_confidence"] = "high"
        tx["category_source"] = "protected"
        tx["category_explanation"] = "Categorized by internal transfer detection."
        tx["category_rule_id"] = None
    elif tx["is_transfer"]:
        # Legacy is_transfer flag (should rarely fire now)
        tx["category"] = "Transfer"
        tx["subcategory"] = ""
        tx["parse_confidence"] = "high"
        tx["category_source"] = "protected"
        tx["category_explanation"] = "Categorized by protected transfer detection."
        tx["category_rule_id"] = None
    elif protected_cat:
        tx["category"] = protected_cat
        tx["subcategory"] = static_sub or ""
        tx["parse_confidence"] = "high"
        tx["category_source"] = "protected"
        tx["category_explanation"] = f"Categorized by {protected_reason}."
        tx["category_rule_id"] = None
    else:
        # 1) Learned rules take priority over static rules — user-taught
        #    merchant→category mappings from the Review page.
        is_person_etransfer = raw.upper().startswith(
            ("INTERAC E-TRANSFER TO:", "INTERAC E-TRANSFER FROM:")
        )
        learned = (None if is_person_etransfer or not use_learned_rules else
                   _lookup_learned_rule(merchant_normalized, conn=conn))
        if learned:
            tx["category"] = learned["category"]
            tx["subcategory"] = learned.get("subcategory") or ""
            tx["parse_confidence"] = "high"
            tx["category_source"] = "user_rule"
            tx["category_rule_id"] = int(learned["id"])
            tx["category_explanation"] = (
                f"Categorized as {learned['category']} from your saved rule "
                f"for {merchant_normalized}."
            )
        elif memo_category:
            tx["category"], tx["subcategory"] = memo_category
            tx["parse_confidence"] = "high"
            tx["category_source"] = "statement_memo"
            tx["category_rule_id"] = None
            tx["category_explanation"] = (
                "Categorized from the bank-provided statement memo."
            )
        else:
            tx["category"] = static_cat
            tx["subcategory"] = static_sub
            # Convert float confidence to label
            if static_conf >= 0.9:
                tx["parse_confidence"] = "high"
            elif static_conf >= 0.7:
                tx["parse_confidence"] = "medium"
            else:
                tx["parse_confidence"] = "low"
            tx["category_source"] = "import_rule"
            tx["category_rule_id"] = None
            tx["category_explanation"] = (
                "Categorized by Ledger's built-in import rules."
                if static_cat != "Uncategorized"
                else "No matching saved or built-in rule; review needed."
            )

    tx["category_updated_at"] = tx.get("category_updated_at")

    # ── Consistency pass: derive is_transfer from final direction + category ──
    # This is the authoritative setting. It overrides the early is_transfer()
    # keyword scan, which can miss cases like "INTERAC e-Transfer To: [own name]"
    # where direction='debit' but category='Transfer' (set by rules.py).
    _NON_CASHFLOW = frozenset({
        "Transfer", "Credit Card Payment", "Payment", "Cancelled",
    })
    if tx["direction"] in ("payment", "transfer", "cancelled") or tx.get("category") in _NON_CASHFLOW:
        tx["is_transfer"] = 1

    # Canonical amount + transaction type are the final authority for all
    # cash-flow calculations. This runs after protected categorization.
    from utils.financial_semantics import canonicalize_transaction
    canonicalize_transaction(tx)
    tx_type = tx["transaction_type"]
    protected_type_categories = {
        "credit_card_payment": "Credit Card Payment",
        "internal_transfer": "Internal Transfer",
        "investment_transfer": "Investments",
        "refund": "Refund / Credit",
        "reimbursement": "Reimbursement / Insurance Reimbursement",
        "mortgage_payment": "Housing / Mortgage",
        "fee_interest": "Fees / Interest",
        "cash_advance": "Cash Advance",
        "employment_income": "Payroll Income",
        "interest_income": "Interest Income",
        "other_income": "Income",
    }
    if tx_type in protected_type_categories:
        tx["category"] = protected_type_categories[tx_type]
        tx["category_source"] = "protected"
        tx["category_rule_id"] = None
        tx["category_explanation"] = (
            "Protected by Ledger's financial transaction-type detection."
        )
        tx["parse_confidence"] = "high"
        if tx_type == "refund":
            from config.categories import SPENDING_CATEGORIES
            tx["subcategory"] = (
                static_cat if static_cat in SPENDING_CATEGORIES
                else "Uncategorized"
            )
        elif tx_type == "other_income":
            # Let income-source reporting fall back to the normalized sender
            # instead of presenting the old "e-Transfer Received" plumbing
            # label as though it were the source.
            tx["subcategory"] = ""
    if (tx_type == "spending" and tx.get("category") in {
        "Income", "Payroll Income", "Interest Income", "Rewards / Cashback",
    }):
        tx["category"] = "Uncategorized"
        tx["subcategory"] = ""
        tx["category_source"] = "consistency_check"
        tx["category_explanation"] = (
            "A non-spending rule conflicted with money leaving the account; "
            "kept as spending and deferred for category review."
        )
        tx["parse_confidence"] = "low"
    if tx_type == "spending" and (
        tx.get("category") == "Transfer Out"
        or raw.upper().startswith("INTERAC E-TRANSFER TO:")
    ):
        tx["category"] = "E-transfer / Personal payment"
        tx["subcategory"] = "Personal payment"
        tx["category_source"] = "import_rule"
        tx["category_explanation"] = (
            "Outgoing Interac e-transfer; counted as spending unless you "
            "explicitly mark it as an internal transfer."
        )
        tx["parse_confidence"] = "high"
    if tx_type in {
        "credit_card_payment", "internal_transfer", "investment_transfer",
        "personal_transfer", "cancelled",
    }:
        tx["is_transfer"] = 1
    # Flag for review
    conf_float = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(tx.get("parse_confidence", "low"), 0.5)
    flagged, reason = should_flag(
        raw, tx["direction"], abs(amount), conf_float,
        tx.get("transaction_type", ""), tx.get("category", ""),
    )
    tx["is_flagged"] = 1 if flagged else 0
    tx["flag_reason"] = reason

    return tx

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

is_transfer = 1 for ALL of the above except 'credit'/'debit'.
              Also = 1 for direction='debit'/'credit' when category is in
              the non-cashflow set ('Transfer', 'Credit Card Payment',
              'Payment', 'Cancelled') — e.g. INTERAC e-Transfer To own name.

IMPORTANT: is_transfer is set twice:
  1. Early, by is_transfer() keyword/direction scan (fast, may be incomplete)
  2. Late, by enrich_transaction() consistency pass (authoritative)
Always use the value from the DB after enrich_transaction() has run.
"""
import re
from config.rules import RULES
from config.categories import CATEGORIES, NON_SPENDING_CATEGORIES


def _lookup_learned_rule(merchant_normalized: str, conn=None):
    """Consult the learned_rules table (user corrections promoted to rules).
    Returns (category, subcategory) or (None, None). Safe to call before
    init_db() — returns None if the table is absent."""
    if not merchant_normalized:
        return None, None
    try:
        from utils.database import get_connection
    except Exception:
        return None, None
    close = False
    try:
        if conn is None:
            conn = get_connection()
            close = True
        row = conn.execute(
            "SELECT category, subcategory FROM learned_rules WHERE merchant_normalized=?",
            (merchant_normalized,),
        ).fetchone()
        if row:
            try:
                conn.execute(
                    "UPDATE learned_rules SET hit_count=COALESCE(hit_count,0)+1, "
                    "last_used_at=datetime('now') WHERE merchant_normalized=?",
                    (merchant_normalized,),
                )
                if close:
                    conn.commit()
            except Exception:
                pass
            return row["category"], row["subcategory"] or ""
    except Exception:
        return None, None
    finally:
        if close and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return None, None


def normalize_merchant(raw: str) -> str:
    """
    Produce a cleaned merchant name from a raw description.
    - Strip leading/trailing whitespace
    - Collapse multiple spaces
    - Remove order-ID suffixes like AMAZON* BD4EG2XM2
    - Remove city/province suffix patterns (e.g. CAMBRIDGE ON, SAN FRANCISCOCA)
    """
    s = raw.strip()
    # Remove Tangerine prefix tokens
    for prefix in ["EFT DEPOSIT FROM ", "EFT WITHDRAWAL TO ", "INTERAC E-TRANSFER FROM: ",
                   "INTERAC E-TRANSFER TO: ", "INTERNET DEPOSIT FROM ", "TANGERINE CREDIT CARD PAYMENT",
                   "CANCELLED INTERAC E-TRANSFER TO: ", "CANCELLED INTERAC E-TRANSFER FROM: "]:
        if s.upper().startswith(prefix):
            s = s[len(prefix):]

    # Strip Amazon order suffixes: AMAZON* XXXXXX
    s = re.sub(r'\bAMAZON\*\s+[A-Z0-9]{6,}\b', 'AMAZON', s, flags=re.IGNORECASE)

    # Strip trailing Canadian city + province 2-letter code
    s = re.sub(r'\s+[A-Z][A-Z\s]+\s+[A-Z]{2}\s*$', '', s).strip()

    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s)
    return s.title()


def categorize(raw_description: str, amount: float = 0.0, direction: str = "debit") -> tuple[str, str, float]:
    """
    Returns (category, subcategory, confidence_score 0.0-1.0).
    confidence_score: 1.0 = exact match, 0.8 = keyword match, 0.5 = fallback
    """
    desc_upper = raw_description.upper()

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
            matched = bool(re.search(pattern, raw_description, re.IGNORECASE))
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
        if "INTERNET WITHDRAWAL TO TANGERINE CHEQUING" in desc_upper:
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
        if amount < 0:
            return "credit"   # refund
        return "debit"

    # Generic CSV
    if amount < 0:
        return "credit"
    return "debit"


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


def should_flag(raw_description: str, direction: str, amount: float, parse_confidence: float) -> tuple[bool, str]:
    """Return (should_flag, reason) for manual review."""
    desc_upper = raw_description.upper()
    reasons = []

    if parse_confidence < 0.6:
        reasons.append("low parse confidence")
    if "CASH ADVANCE" in desc_upper:
        reasons.append("cash advance")
    if direction == "cancelled":
        reasons.append("cancelled transaction — verify netting")
    if amount > 2000 and direction == "debit":
        reasons.append(f"large debit ${amount:.2f}")
    if "NSF" in desc_upper or "RETURNED" in desc_upper:
        reasons.append("NSF/returned item")

    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def enrich_transaction(tx: dict) -> dict:
    """
    Apply categorization, direction, transfer flag, and review flag to a raw transaction dict.
    Modifies and returns the dict in-place.
    """
    raw = tx.get("raw_description", "")
    amount = tx.get("amount", 0.0)
    account_type = tx.get("account_type", "")

    # Direction (parser may have already set this — don't override if set)
    if not tx.get("direction"):
        tx["direction"] = determine_direction(raw, amount, account_type)

    # Transfer flag
    if not tx.get("is_transfer"):
        tx["is_transfer"] = 1 if is_transfer(raw, tx["direction"]) else 0

    # Merchant normalization
    if not tx.get("merchant"):
        tx["merchant"] = normalize_merchant(raw)

    # Categorization — skip for payments/cancelled (internal movements)
    # NOTE: INTERAC e-Transfers (direction='debit'/'credit', not direction='transfer')
    # are real cashflow and ARE categorized as Transfer Out / Transfer In.
    if tx["direction"] in ("payment", "cancelled"):
        # Pure internal: CC payment from chequing, savings pullback
        tx["category"] = tx["direction"].title()
        tx["subcategory"] = ""
        tx["parse_confidence"] = "high"
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
    elif tx["is_transfer"]:
        # Legacy is_transfer flag (should rarely fire now)
        tx["category"] = "Transfer"
        tx["subcategory"] = ""
        tx["parse_confidence"] = "high"
    else:
        # 1) Learned rules take priority over static rules — user-taught
        #    merchant→category mappings from the Review page.
        learned_cat, learned_sub = _lookup_learned_rule(tx.get("merchant", ""))
        if learned_cat:
            tx["category"] = learned_cat
            tx["subcategory"] = learned_sub or ""
            tx["parse_confidence"] = "high"
        else:
            cat, sub, conf = categorize(raw, amount, tx["direction"])
            tx["category"] = cat
            tx["subcategory"] = sub
            # Convert float confidence to label
            if conf >= 0.9:
                tx["parse_confidence"] = "high"
            elif conf >= 0.7:
                tx["parse_confidence"] = "medium"
            else:
                tx["parse_confidence"] = "low"

    # ── Consistency pass: derive is_transfer from final direction + category ──
    # This is the authoritative setting. It overrides the early is_transfer()
    # keyword scan, which can miss cases like "INTERAC e-Transfer To: [own name]"
    # where direction='debit' but category='Transfer' (set by rules.py).
    _NON_CASHFLOW = frozenset({
        "Transfer", "Credit Card Payment", "Payment", "Cancelled",
    })
    if tx["direction"] in ("payment", "transfer", "cancelled") or tx.get("category") in _NON_CASHFLOW:
        tx["is_transfer"] = 1

    # Flag for review
    conf_float = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(tx.get("parse_confidence", "low"), 0.5)
    flagged, reason = should_flag(raw, tx["direction"], abs(amount), conf_float)
    tx["is_flagged"] = 1 if flagged else 0
    tx["flag_reason"] = reason

    return tx

"""
parsers/detect.py — auto-detect Tangerine statement type from PDF content.

ROOT CAUSE OF v9 BUG (fixed here):
  Chequing PDFs contain "Tangerine Savings Account" inside transaction
  descriptions ("Internet Deposit from Tangerine Savings Account - 3031472835").
  The old detector matched this as a savings fingerprint because it searched
  raw text and didn't distinguish header occurrences from body occurrences.
  Additionally, the chequing guard "chequing statement" never matched because
  the PDF renders it as two separate lines: "Chequing\\nstatement".

FIX STRATEGY — three-tier priority:
  Tier 1 — Account header fingerprints (most reliable, appear in the
            "Account(s) at a Glance" table or section headers):
              Chequing:  "The Details - Tangerine Chequing Account"
              Savings:   "The Details - Tangerine Savings Account"
              MC:        "Money-Back World Mastercard"

  Tier 2 — Page-level multi-word signals that require BOTH a type keyword
            AND an absence-of-other-type guard. Guards use the same tier-1
            anchors so a transaction description can't fool them.

  Tier 3 — Single-word fallbacks only used when tiers 1+2 are inconclusive.

Returns:
  "savings"       — confident match for Tangerine Savings
  "chequing"      — confident match for chequing
  "mastercard"    — confident match for Mastercard
  "csv"           — file extension is .csv (not a PDF)
  "unknown"       — PDF but could not determine type; user must select manually

detect_confidence() returns ("type", "high"|"medium"|"low") for UI decisions.
"""
from pathlib import Path
import re


def _extract_text(path: str | Path, max_chars: int = 6000) -> str:
    """Extract plain text from first 2 pages of a PDF. Returns '' on any error."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return ""
            text = ""
            for pg in pdf.pages[:2]:
                text += (pg.extract_text() or "") + "\n"
                if len(text) > max_chars:
                    break
        return text
    except Exception:
        return ""


def _norm(text: str) -> str:
    """
    Collapse whitespace / newlines into single spaces so multi-line PDF tokens
    like "Chequing\\nstatement" become "Chequing statement" and can be matched.
    Preserves original case — callers do .lower() themselves.
    """
    return re.sub(r"[\s\n\r]+", " ", text).strip()


# ── Tier-1 anchors — appear ONLY in section headers, never in tx descriptions ──
# These are the "The Details - …" subsection labels that introduce each account's
# transaction table. A savings or chequing account will never have the other's header.
_CHQ_HEADER   = "the details - tangerine chequing account"
_SAV_HEADER   = "the details - tangerine savings account"
_MC_HEADERS   = [
    "money-back world mastercard",
    "the details - tangerine mastercard",
]

# ── Tier-1b: Account-at-a-Glance table entries ─────────────────────────────
# The table line is e.g. "Tangerine Chequing Account 4010461272 5,036.16"
# This only appears for the *primary* account of the statement.
# We detect it by matching "tangerine chequing account <digits>" pattern.
_RE_CHQ_TABLE = re.compile(r"tangerine chequing account\s+\d{6,}", re.IGNORECASE)
_RE_SAV_TABLE = re.compile(r"tangerine savings account\s+\d{6,}", re.IGNORECASE)

# ── Page-title tokens ───────────────────────────────────────────────────────
# Chequing PDFs open with three separate lines: "Tangerine" / "Chequing" / "statement"
# Savings PDFs open with a single line: "Statement"  (no "Chequing" prefix)
_RE_PAGE_TITLE_CHQ = re.compile(
    r"tangerine\s+chequing\s+statement", re.IGNORECASE
)


def detect_statement_type(path: str | Path) -> str:
    """Return 'savings' | 'chequing' | 'mastercard' | 'csv' | 'unknown'."""
    return detect_with_confidence(path)[0]


def detect_with_confidence(path: str | Path) -> tuple[str, str]:
    """
    Return (type, confidence) where confidence is 'high' | 'medium' | 'low'.
    'low' means the caller should show a manual override UI.
    """
    p = Path(path)

    if p.suffix.lower() == ".csv":
        return "csv", "high"

    if p.suffix.lower() != ".pdf":
        return "unknown", "low"

    raw_text = _extract_text(p)
    if not raw_text:
        return "unknown", "low"

    # Normalised text: newlines collapsed to spaces, lowercase
    t = _norm(raw_text).lower()

    # ── Tier 1: section-header anchors (most reliable) ──────────────────
    has_chq_header = _CHQ_HEADER in t
    has_sav_header = _SAV_HEADER in t
    has_mc_header  = any(h in t for h in _MC_HEADERS)

    if has_chq_header and not has_sav_header and not has_mc_header:
        return "chequing", "high"

    if has_sav_header and not has_chq_header and not has_mc_header:
        return "savings", "high"

    if has_mc_header and not has_chq_header:
        return "mastercard", "high"

    # Savings eStatements CAN also contain chequing section headers because
    # multi-account statements list every account.  In that case both
    # has_sav_header and has_chq_header may be True — but the PRIMARY account
    # (the one listed first and in the page title) determines the statement type.
    # Resolve by checking which header appears earlier in the text.
    if has_sav_header and has_chq_header:
        sav_pos = t.find(_SAV_HEADER)
        chq_pos = t.find(_CHQ_HEADER)
        primary = "savings" if sav_pos < chq_pos else "chequing"
        return primary, "high"

    # ── Tier 1b: Account-at-a-Glance table regex ────────────────────────
    has_chq_table = bool(_RE_CHQ_TABLE.search(t))
    has_sav_table = bool(_RE_SAV_TABLE.search(t))

    if has_chq_table and not has_sav_table:
        return "chequing", "high"

    # Savings table match: must NOT also have chequing table as primary
    # (chequing PDFs always contain "tangerine savings account" in tx lines
    #  but NOT a table entry because the regex requires trailing digits)
    if has_sav_table and not has_chq_table:
        return "savings", "high"

    # ── Tier 2: page-title pattern ───────────────────────────────────────
    if _RE_PAGE_TITLE_CHQ.search(t):
        return "chequing", "high"

    # Generic "mastercard" + "account number" — catches MC statements that
    # don't hit tier-1 (e.g. older format)
    if "mastercard" in t and "account number" in t:
        return "mastercard", "medium"

    # ── Tier 3: single-word fallbacks — low confidence ───────────────────
    if "chequing" in t and "mastercard" not in t:
        return "chequing", "low"

    if "mastercard" in t:
        return "mastercard", "low"

    # Only call it savings if there's a strong signal with no chequing anywhere
    if "savings" in t and "tangerine" in t and "chequing" not in t:
        return "savings", "low"

    return "unknown", "low"


def detect_label(dtype: str) -> str:
    """Return a human-readable label for a detected type."""
    return {
        "savings":    "Tangerine Savings PDF",
        "chequing":   "Tangerine Chequing PDF",
        "mastercard": "Tangerine Mastercard PDF",
        "csv":        "Generic CSV",
        "unknown":    "Unknown (select type manually)",
    }.get(dtype, "Unknown")

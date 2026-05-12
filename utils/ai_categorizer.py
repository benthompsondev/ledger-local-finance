"""
Provider-agnostic AI categorization adapter.

Pass 17 redesign — three-tier categorization
─────────────────────────────────────────────
The Review page's "AI Suggest" was returning "AI response was not parseable
JSON" on ~23/26 rows. Root cause: we called MiniMax-M2.7 with `max_tokens=200`
and used a naive parser that didn't strip `<think>...</think>` reasoning
blocks. Reasoning models burn most of their token budget on reasoning before
emitting JSON; with a 200-token cap the response truncated mid-think and the
parser saw no JSON at all.

The fix is layered:

  Tier 1 — Deterministic rules (instant, free).
           `categorize()` from utils.categorizer + `_lookup_learned_rule()`.
           If we get a category in the allowed set with confidence >= 0.8,
           return it as `source='rule'` and don't call AI at all.

  Tier 2 — AI (MiniMax / Anthropic / OpenAI).
           Larger token budget (1500), `<think>...</think>` strip, balanced-
           brace JSON extraction (same lenient parser the explainer uses).
           Returns `source='ai'` if the response is valid JSON with a
           category in the allowed set.

  Tier 3 — Deterministic fallback if AI failed/was junk.
           If `categorize()` produced ANY category (even Uncategorized at
           0.5), we return THAT as `source='fallback'` with a small note
           explaining the AI failure. The Review page never has to show the
           bare "parse failure" string as the only result.

The legacy callers (`suggest_for_transaction`, `verify_for_transaction`) are
preserved with their old shape so nothing else has to change. New callers
should use `suggest_for_transaction_v2(tx)` which returns the full tiered
result including `source`, `tier`, and human-friendly `note`.

The Settings page (9_Settings.py) writes config.json; the Review page
(8_Review.py) calls this module on user click.
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Optional
from abc import ABC, abstractmethod

from config.categories import CATEGORIES, NON_SPENDING_CATEGORIES
from utils.ai_config import get_ai_settings, ai_is_ready, DEFAULT_BASE_URLS

# Categories we never want the AI to return — these are system / non-cashflow
# and must come from the deterministic logic in enrich_transaction(), not AI.
# Pass 17: extended to cover the full system-category set (Internal Transfer,
# Refund / Credit, Payroll Income, Interest Income, Rewards / Cashback) so AI
# can't override the deterministic plumbing.
_AI_FORBIDDEN = frozenset({
    "Transfer", "Transfer In", "Transfer Out", "Internal Transfer",
    "Credit Card Payment", "Payment", "Cancelled",
    "Refund / Credit",
    "Income", "Payroll Income", "Interest Income", "Rewards / Cashback",
    "Savings", "Investments",
})
_AI_ALLOWED = tuple(c for c in CATEGORIES if c not in _AI_FORBIDDEN)


# ── Reasoning-tag stripping (Pass 17: borrowed from utils.ai_explainer) ──
# MiniMax-M2.7 wraps chain-of-thought in <think>...</think>. Anthropic does
# the same with <reasoning>. If we don't strip these, lenient JSON parsing
# may grab brace literals from the reasoning text and fail.
_THINK_PATTERNS = [
    re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<reasoning>.*?</reasoning>\s*", re.DOTALL | re.IGNORECASE),
]
_UNCLOSED_THINK = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    if not text:
        return ""
    s = text
    for pat in _THINK_PATTERNS:
        s = pat.sub("", s)
    s = _UNCLOSED_THINK.sub("", s)
    return s.strip()


def _extract_balanced_json(s: str) -> Optional[str]:
    """Return the first balanced {...} block in s, or None.

    More robust than `s.find('{')..s.rfind('}')` when the response mixes
    prose and JSON, or when reasoning leftovers contain stray braces."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    return s[start:i + 1]
    return None


_SYSTEM_PROMPT = (
    "You categorize personal bank/credit-card transactions into a fixed list "
    "of categories. Output MUST be a single JSON object — no prose, no "
    "<think>, no code fences. Schema: "
    '{"category":"...","subcategory":"...","confidence":0.0-1.0,'
    '"rationale":"short phrase","is_subscription_guess":true|false,'
    '"is_transfer_or_payment_guess":true|false}. '
    "If the merchant is genuinely ambiguous, pick the best fit and set "
    "confidence <= 0.5. Never invent a category outside the list. If the "
    "transaction looks like a transfer, payment, or income (not consumption), "
    "still pick the closest spending bucket and set "
    "is_transfer_or_payment_guess=true so the caller can route accordingly."
)


def _build_user_prompt(tx: dict) -> str:
    raw = tx.get("raw_description", "") or ""
    merchant = tx.get("merchant", "") or ""
    amount = tx.get("amount", 0) or 0
    direction = tx.get("direction", "debit") or "debit"
    account = tx.get("account_type", "") or ""

    return (
        f"Allowed categories (choose exactly one): {', '.join(_AI_ALLOWED)}.\n\n"
        f"Transaction:\n"
        f"  raw_description: {raw}\n"
        f"  merchant:        {merchant}\n"
        f"  amount:          {amount}\n"
        f"  direction:       {direction}   (debit=outflow, credit=inflow)\n"
        f"  account:         {account}\n\n"
        "Reply with ONE valid JSON object (schema in the system message). "
        "No reasoning text. No <think> blocks."
    )


# ── Providers ─────────────────────────────────────────────────────────────

class AIProvider(ABC):
    name: str = "abstract"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    @abstractmethod
    def suggest_raw(self, system_prompt: str, user_prompt: str, timeout: float = 20.0,
                    max_tokens: int = 200) -> str:
        """Return the raw assistant text (expected to be JSON).

        max_tokens controls the response cap. Categorization fits in 200; reasoning-style
        models (e.g. MiniMax-M2.7) emit chain-of-thought before JSON and need much more
        headroom for general explanation calls."""
        ...


class _OpenAICompatibleProvider(AIProvider):
    """Shared implementation for OpenAI-compatible chat/completions endpoints.
    MiniMax exposes an OpenAI-compatible API at https://api.minimax.io/v1 —
    this class is used for both. Subclasses set `name` and optionally override
    `_endpoint()` / `_headers()`."""

    def _endpoint(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if not base:
            raise RuntimeError(f"No base_url configured for provider '{self.name}'")
        return base + "/chat/completions"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }

    def suggest_raw(self, system_prompt: str, user_prompt: str, timeout: float = 20.0,
                    max_tokens: int = 200) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens":  int(max_tokens),
        }
        req = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected {self.name} response shape: {e}") from e


class MiniMaxProvider(_OpenAICompatibleProvider):
    """MiniMax (M2, M2.7, etc.) via its OpenAI-compatible endpoint.
    Default base_url: https://api.minimax.io/v1
    Default model:    MiniMax-M2.7 (overridable in Settings)."""
    name = "minimax"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url or DEFAULT_BASE_URLS["minimax"])


class OpenAIProvider(_OpenAICompatibleProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url or "https://api.openai.com/v1")


class AnthropicProvider(AIProvider):
    """Anthropic Messages API. Different request shape than OpenAI-compatible."""
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: Optional[str] = None):
        super().__init__(api_key, model, base_url or "https://api.anthropic.com/v1")

    def suggest_raw(self, system_prompt: str, user_prompt: str, timeout: float = 20.0,
                    max_tokens: int = 200) -> str:
        body = {
            "model": self.model,
            "max_tokens": int(max_tokens),
            "temperature": 0.0,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }
        url = (self.base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-api-key":         self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type":      "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        try:
            return payload["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected Anthropic response shape: {e}") from e


_PROVIDER_CLASSES = {
    "minimax":   MiniMaxProvider,
    "anthropic": AnthropicProvider,
    "openai":    OpenAIProvider,
}


def _make_provider() -> Optional[AIProvider]:
    ai = get_ai_settings()
    provider = ai.get("provider")
    cls = _PROVIDER_CLASSES.get(provider)
    if cls is None:
        return None
    return cls(
        api_key=ai.get("api_key", ""),
        model=ai.get("model", ""),
        base_url=ai.get("base_url"),
    )


# ── Public API ────────────────────────────────────────────────────────────

def _parse_json_from_text(text: str) -> Optional[dict]:
    """Lenient JSON parser. Handles think-tag wrapping, code fences, and
    prose. Returns the parsed dict or None.

    Pass 17: adopts the same strategy the explainer uses — strip
    `<think>...</think>`, then code fences, then try direct parse, then
    extract the first balanced {...} block. Fixes the 23/26 'AI response
    was not parseable JSON' failure rate seen with MiniMax-M2.7 on
    `max_tokens=200`. With reasoning stripped and a higher token budget,
    the model's actual JSON output gets through.
    """
    if not text:
        return None
    s = _strip_thinking(text).strip()
    # Strip markdown code fences if present
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    # Direct parse first
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Balanced-brace extraction (more robust than naive find/rfind)
    block = _extract_balanced_json(s)
    if block:
        try:
            obj = json.loads(block)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _validate_suggestion(d: dict) -> Optional[dict]:
    if not isinstance(d, dict):
        return None
    cat = (d.get("category") or "").strip()
    if cat not in _AI_ALLOWED:
        return None
    sub = (d.get("subcategory") or "").strip()
    try:
        conf = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    rationale = (d.get("rationale") or "").strip()[:240]
    return {
        "category":    cat,
        "subcategory": sub,
        "confidence":  conf,
        "rationale":   rationale,
    }


def _raw_ai_categorize(tx: dict, timeout: float = 30.0,
                       max_tokens: int = 1500) -> tuple[Optional[dict], str, Optional[object]]:
    """Low-level AI call shared by `suggest_for_transaction` and `verify_for_transaction`.

    Pass 17 changes:
      • `max_tokens` default 1500 (was 200). Reasoning models like MiniMax-M2.7
        emit chain-of-thought before JSON; 200 truncates mid-think and the
        parser sees no JSON. 1500 leaves room for ~600-token think + JSON.
      • `timeout` default 30s (was 20). Reasoning models take longer.
      • `_parse_json_from_text` now strips <think> blocks first.

    Returns (parsed_or_None, reason_str, provider_or_None).
    `parsed` is the AI's structured suggestion BEFORE the allowed-list filter.
    Never raises.
    """
    ready, why = ai_is_ready()
    if not ready:
        return None, why or "AI not ready", None

    provider = _make_provider()
    if provider is None:
        return None, "Provider factory returned None", None

    try:
        raw = provider.suggest_raw(
            _SYSTEM_PROMPT,
            _build_user_prompt(tx),
            timeout=timeout,
            max_tokens=max_tokens,
        )
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} from provider", provider
    except urllib.error.URLError as e:
        return None, f"Network error: {getattr(e, 'reason', e)}", provider
    except TimeoutError:
        return None, f"Timeout after {timeout:.0f}s", provider
    except (RuntimeError, OSError):
        return None, "Provider call failed", provider

    if not raw:
        return None, "Provider returned empty response", provider

    parsed = _parse_json_from_text(raw)
    if not isinstance(parsed, dict):
        return None, "AI response was not parseable JSON", provider

    cat = (parsed.get("category") or "").strip()
    sub = (parsed.get("subcategory") or "").strip()
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    rationale = (parsed.get("rationale") or "").strip()[:240]

    if not cat:
        return None, "AI returned empty category", provider

    return ({
        "category":    cat,
        "subcategory": sub,
        "confidence":  conf,
        "rationale":   rationale,
        "is_subscription_guess": bool(parsed.get("is_subscription_guess", False)),
        "is_transfer_or_payment_guess": bool(parsed.get("is_transfer_or_payment_guess", False)),
    }, "", provider)


def _deterministic_suggest(tx: dict) -> Optional[dict]:
    """Run the deterministic categorizer (config/rules.py + learned_rules)
    and return the same shape as an AI suggestion if the result is high
    confidence (>= 0.8), else None.

    Pass 17: deterministic rule matches to SYSTEM categories
    (Credit Card Payment / Internal Transfer / Payroll Income / etc.) ARE
    returned. They're not AI-eligible, but they're DETERMINISTICALLY correct
    — Tangerine label "EFT Deposit from SJH" really is Payroll Income, full
    stop. The user listed these explicitly as expected categorizer outputs.
    The `_AI_FORBIDDEN` filter is only for the AI tier.
    """
    try:
        from utils.categorizer import categorize, normalize_merchant
        from utils.database import get_connection
    except Exception:
        return None
    raw = tx.get("raw_description", "") or ""
    amount = tx.get("amount", 0) or 0
    direction = tx.get("direction", "debit") or "debit"

    # Try learned rules first.
    merchant = tx.get("merchant") or normalize_merchant(raw)
    learned_cat, learned_sub = (None, None)
    try:
        c = get_connection()
        row = c.execute(
            "SELECT category, subcategory FROM learned_rules WHERE merchant_normalized=?",
            (merchant,),
        ).fetchone()
        c.close()
        if row:
            learned_cat = row["category"]
            learned_sub = row["subcategory"] or ""
    except Exception:
        pass
    # Learned rules: trust the user's prior decision regardless of system/spending.
    if learned_cat and learned_cat in CATEGORIES:
        return {
            "category":    learned_cat,
            "subcategory": learned_sub or "",
            "confidence":  1.0,
            "rationale":   f"Learned rule for '{merchant}'",
            "is_subscription_guess": False,
            "is_transfer_or_payment_guess": learned_cat in _AI_FORBIDDEN,
        }

    # Static rules — accept system categories at high confidence too.
    cat, sub, conf = categorize(raw, amount, direction)
    if cat and cat in CATEGORIES and conf >= 0.8:
        return {
            "category":    cat,
            "subcategory": sub or "",
            "confidence":  conf,
            "rationale":   f"Rule match: '{cat}'",
            "is_subscription_guess": False,
            "is_transfer_or_payment_guess": cat in _AI_FORBIDDEN,
        }
    return None


def _deterministic_fallback(tx: dict) -> Optional[dict]:
    """Same as `_deterministic_suggest` but accepts ANY non-empty result —
    even Uncategorized at 0.5 confidence. Used as the AI-failure fallback
    so the Review page never shows the bare 'parse failure' string with no
    actionable category."""
    try:
        from utils.categorizer import categorize, normalize_merchant
    except Exception:
        return None
    raw = tx.get("raw_description", "") or ""
    amount = tx.get("amount", 0) or 0
    direction = tx.get("direction", "debit") or "debit"
    cat, sub, conf = categorize(raw, amount, direction)
    if not cat:
        return None
    # Allow Uncategorized through — it's a real signal that no rule matched.
    if cat not in _AI_ALLOWED and cat != "Uncategorized":
        return None
    return {
        "category":    cat,
        "subcategory": sub or "",
        "confidence":  conf,
        "rationale":   f"Fallback (no rule matched, AI failed)" if cat == "Uncategorized"
                       else f"Rule match (low conf): '{cat}'",
        "is_subscription_guess": False,
        "is_transfer_or_payment_guess": False,
    }


def suggest_for_transaction_v2(tx: dict, timeout: float = 30.0) -> dict:
    """Pass 17 — three-tier suggestion. Always returns a dict (never None).

    Schema:
        ok        bool       — at least one tier produced a usable category
        tier      str        — 'rule' | 'ai' | 'fallback' | 'none'
        source    str        — friendly label shown in the UI
        category  str        — empty string when ok=False
        subcategory str
        confidence float
        rationale str
        is_subscription_guess        bool
        is_transfer_or_payment_guess bool
        ai_reason str        — sanitized AI failure reason ('' on AI success
                               or when AI was skipped)
        provider  str
        model     str
        note      str        — UI hint when AI failed but fallback recovered
    """
    # ── Tier 1 — deterministic rule (high confidence) ──────────────────
    rule_hit = _deterministic_suggest(tx)
    if rule_hit:
        return {
            "ok":         True,
            "tier":       "rule",
            "source":     "Rule",
            **rule_hit,
            "ai_reason":  "",
            "provider":   "deterministic",
            "model":      "rules.py",
            "note":       "",
        }

    # ── Tier 2 — AI ────────────────────────────────────────────────────
    parsed, ai_reason, provider = _raw_ai_categorize(tx, timeout=timeout)
    if parsed is not None and parsed["category"] in _AI_ALLOWED and provider is not None:
        return {
            "ok":         True,
            "tier":       "ai",
            "source":     "AI",
            **parsed,
            "ai_reason":  "",
            "provider":   provider.name,
            "model":      provider.model,
            "note":       "",
        }

    # ── Tier 3 — fallback to whatever the deterministic categorizer
    # produced, even Uncategorized. This is the difference between Pass 16
    # ("AI parse failed → empty result") and Pass 17 ("AI parse failed,
    # but here's the rule-engine's best guess").
    fb = _deterministic_fallback(tx)
    if fb:
        note = (f"AI failed ({ai_reason}); using deterministic fallback."
                if ai_reason else "AI was skipped or unavailable; using deterministic fallback.")
        return {
            "ok":         True,
            "tier":       "fallback",
            "source":     "Fallback",
            **fb,
            "ai_reason":  ai_reason,
            "provider":   getattr(provider, "name", "deterministic") if provider else "deterministic",
            "model":      getattr(provider, "model", "rules.py")     if provider else "rules.py",
            "note":       note,
        }

    return {
        "ok":         False,
        "tier":       "none",
        "source":     "None",
        "category":   "",
        "subcategory":"",
        "confidence": 0.0,
        "rationale":  "",
        "is_subscription_guess": False,
        "is_transfer_or_payment_guess": False,
        "ai_reason":  ai_reason or "",
        "provider":   getattr(provider, "name", "") if provider else "",
        "model":      getattr(provider, "model", "") if provider else "",
        "note":       "No deterministic rule matched and AI was unable to help.",
    }


def suggest_for_transaction(tx: dict, timeout: float = 30.0) -> Optional[dict]:
    """LEGACY shape preserved for callers that still import this name.

    SAFE auto-categorization. Returns a suggestion dict or None.

    Pass 17 reroutes through the tiered v2 pipeline so deterministic rules
    catch the row before AI is even called — fixing the parse-failure
    dominance reported in manual testing. Returns None only when all three
    tiers fail (rule miss + AI fail + no fallback).

    Shape on success:
        {category, subcategory, confidence, rationale, provider, model}
    """
    v2 = suggest_for_transaction_v2(tx, timeout=timeout)
    if not v2["ok"]:
        return None
    return {
        "category":    v2["category"],
        "subcategory": v2["subcategory"],
        "confidence":  v2["confidence"],
        "rationale":   v2["rationale"],
        "provider":    v2["provider"],
        "model":       v2["model"],
    }


def verify_for_transaction(tx: dict, timeout: float = 20.0) -> dict:
    """Verify-or-correct path for already-categorized rows in Review.

    Unlike `suggest_for_transaction`, this NEVER silently drops a result. It
    returns a structured verdict the UI can render:
        - "AI agrees this looks like Housing / Mortgage"
        - "AI suggests Food & Convenience instead"
        - "AI failed: <reason>"

    A category in `_AI_FORBIDDEN` (e.g. Transfer Out for an e-Transfer to a
    person) is preserved here — that's a meaningful verification signal — but
    `allowed=False` is set so the UI knows the auto-accept path won't take it.

    Always returns a dict (never None). Schema:
        ok                    bool
        mode                  'agree' | 'suggest_change' | 'uncertain' | 'failed'
        current_category      str
        suggested_category    str
        suggested_subcategory str
        confidence            float (0..1)
        rationale             str
        allowed               bool   (suggested_category is in _AI_ALLOWED)
        agrees                bool   (suggested_category matches current_category)
        provider              str
        model                 str
        reason                str    (non-sensitive diag on failure)
    """
    current = (tx.get("category") or "").strip()
    parsed, reason, provider = _raw_ai_categorize(tx, timeout=timeout)

    if parsed is None:
        return {
            "ok":                  False,
            "mode":                "failed",
            "current_category":    current,
            "suggested_category":  "",
            "suggested_subcategory": "",
            "confidence":          0.0,
            "rationale":           "",
            "allowed":             False,
            "agrees":              False,
            "provider":            getattr(provider, "name", "") or "",
            "model":               getattr(provider, "model", "") or "",
            "reason":              reason or "AI verify failed",
        }

    sug_cat = parsed["category"]
    allowed = sug_cat in _AI_ALLOWED
    agrees = bool(current) and sug_cat.lower().strip() == current.lower().strip()

    if agrees:
        mode = "agree"
    elif parsed["confidence"] < 0.5:
        mode = "uncertain"
    else:
        mode = "suggest_change"

    return {
        "ok":                  True,
        "mode":                mode,
        "current_category":    current,
        "suggested_category":  sug_cat,
        "suggested_subcategory": parsed.get("subcategory", ""),
        "confidence":          parsed["confidence"],
        "rationale":           parsed["rationale"],
        "allowed":             allowed,
        "agrees":              agrees,
        "provider":            provider.name,
        "model":               provider.model,
        "reason":              "",
    }


def provider_status() -> dict:
    """Diagnostic info for Settings UI. Never exposes the key."""
    ai = get_ai_settings()
    ready, reason = ai_is_ready()
    return {
        "enabled":   bool(ai.get("enabled")),
        "provider":  ai.get("provider"),
        "model":     ai.get("model"),
        "base_url":  ai.get("base_url"),
        "ready":     ready,
        "reason":    reason,
        "has_key":   bool(ai.get("api_key")),
    }

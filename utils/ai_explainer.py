"""
Ledger AI Copilot — grounded explanations, summaries, and a small Ask Ledger.

Design contract
───────────────
• Deterministic data is the truth. AI only paraphrases evidence packets.
• Every explainer builds an evidence packet from local data, then passes it
  to one AI call. No chain-of-thought, no tool use, no internet.
• Every function returns a structured dict with `headline`, `summary`,
  `moves`, `grounded_from`, `provider`, `model`, `ok`, `error`, `fallback`.
• If AI is disabled or the call fails, a deterministic fallback is returned
  (never a hard error, never a blank panel).
• Output is capped (length + list size) and post-validated before display —
  the AI cannot smuggle new numbers that weren't in the packet.

Public API
──────────
    dashboard_copilot(conn)               -> dict
    explain_recommendation(rec, ctx)      -> dict
    review_triage_summary(flagged, ai_cnd)-> dict
    mission_framing(mission, streaks)     -> dict  (1-liner, AI-optional)
    ask_ledger(question, conn)            -> dict  (preset-question router)

Each function is safe to call even when AI is off — it short-circuits to
the deterministic fallback.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Optional

from utils.ai_config import get_ai_settings, ai_is_ready
from utils.ai_categorizer import _make_provider  # reuse the provider factory


# ── Shared helpers ────────────────────────────────────────────────────────

_MAX_SUMMARY_CHARS = 600
_MAX_MOVES = 3
_MAX_MOVE_CHARS = 140

# MiniMax-M2.7 and similar reasoning models emit chain-of-thought before JSON.
# Token budgets here are sized for *reliability*, not cost — let the model
# finish thinking AND emit the JSON. Categorization keeps its own 200 cap
# (small schema, small output) and is unaffected.
#
# Per-surface budgets (Phase 2 policy — output budget is not the constraint):
#   ai_health_check        → 2000   (tiny {ok, echo} schema)
#   mission_framing        → 4000   (one-line output, but M2.7 still thinks)
#   recommendation_explainer → 6000 (3 short fields)
#   review_triage          → 6000   (headline + summary + 3-item list)
#   dashboard_copilot      → 8000   (richer packet, 3 moves)
#   ask_ledger             → 8000   (variable skill packets)
#   weekly_review          → 10000  (largest packet, 5-item checklist; M2.7 over-thought at 4000)
#   scenario_simulator     → 8000   (rich evidence + 3 lists)
_EXPLAINER_MAX_TOKENS = 8000   # global default for explainer surfaces
_EXPLAINER_TIMEOUT_S  = 90.0   # M2.7 reasoning is slow; favor completion over speed

_BUDGET_HEALTH_CHECK   = 2000
_BUDGET_MISSION        = 4000
_BUDGET_REC            = 6000
_BUDGET_TRIAGE         = 6000
_BUDGET_COPILOT        = 8000
_BUDGET_ASK            = 8000
_BUDGET_WEEKLY         = 10000
_BUDGET_SCENARIO       = 8000
_BUDGET_REDUCE         = 8000   # Reduce workspace summary
_BUDGET_PROGRESS_COACH = 6000   # Money Progress level-up coaching

# ── Sanitization ─────────────────────────────────────────────────────────

def _sanitize(text: str) -> str:
    """Scrub anything resembling the configured API key from a string.

    Used on every error/diagnostic message before it ever leaves this module.
    Defence-in-depth: HTTP error bodies and exception strings should not contain
    the key, but if a misconfigured proxy or middleware echoed it, we still
    redact before display."""
    if not text:
        return text
    s = str(text)
    key = (get_ai_settings() or {}).get("api_key", "") or ""
    if key and key in s:
        s = s.replace(key, "[REDACTED]")
    # Also scrub anything matching long bearer-style tokens defensively
    s = re.sub(r"Bearer\s+[A-Za-z0-9._\-+/=]{16,}", "Bearer [REDACTED]", s)
    return s


# ── Last-call diagnostics (sanitized, in-memory) ─────────────────────────

# Module-level last-call status keyed by feature_id. Reset per call.
# Every value here is safe to show in the Settings UI — no key, no raw payloads.
_LAST_CALL: dict[str, dict] = {}


def _record_call_status(
    feature_id: str,
    *,
    attempted: bool,
    ok: bool,
    fallback: bool,
    reason: str = "",
    latency_ms: int = 0,
    response_chars: int = 0,
    parsed_keys: Optional[list[str]] = None,
    parse_error: str = "",
    validation_error: str = "",
    http_status: Optional[int] = None,
) -> None:
    ai = get_ai_settings()
    _LAST_CALL[feature_id] = {
        "feature":          feature_id,
        "provider":         ai.get("provider") or "—",
        "model":            ai.get("model") or "—",
        "attempted":        bool(attempted),
        "ok":               bool(ok),
        "fallback":         bool(fallback),
        "reason":           _sanitize(reason),
        "latency_ms":       int(latency_ms),
        "response_chars":   int(response_chars),
        "parsed_keys":      list(parsed_keys or []),
        "parse_error":      _sanitize(parse_error),
        "validation_error": _sanitize(validation_error),
        "http_status":      http_status,
        "ts":               time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def last_ai_call_status(feature_id: str) -> Optional[dict]:
    """Sanitized snapshot of the most recent AI call for a feature.
    Returns None if the feature hasn't been called this session."""
    snap = _LAST_CALL.get(feature_id)
    return dict(snap) if snap else None


def all_ai_call_statuses() -> dict[str, dict]:
    """Sanitized snapshot of every recorded last-call status."""
    return {k: dict(v) for k, v in _LAST_CALL.items()}


# ── AI call path ─────────────────────────────────────────────────────────

def _call_ai(
    system_prompt: str,
    user_prompt: str,
    *,
    feature_id: str,
    timeout: float = _EXPLAINER_TIMEOUT_S,
    max_tokens: int = _EXPLAINER_MAX_TOKENS,
) -> tuple[Optional[str], dict]:
    """One AI round-trip with diagnostics.

    Returns (raw_text_or_None, diagnostic_dict). `diagnostic_dict` has:
        attempted (bool), latency_ms (int), reason (str), http_status (int|None),
        response_chars (int)

    Never raises. The reason string is sanitized — safe to surface to UI.
    """
    diag: dict = {"attempted": False, "latency_ms": 0, "reason": "",
                  "http_status": None, "response_chars": 0}

    ready, why = ai_is_ready()
    if not ready:
        diag["reason"] = why or "AI disabled"
        return None, diag

    provider = _make_provider()
    if provider is None:
        diag["reason"] = "Provider factory returned None (config invalid)"
        return None, diag

    diag["attempted"] = True
    t0 = time.time()
    try:
        raw = provider.suggest_raw(system_prompt, user_prompt,
                                   timeout=timeout, max_tokens=max_tokens)
        diag["latency_ms"] = int((time.time() - t0) * 1000)
        diag["response_chars"] = len(raw or "")
        if not raw:
            diag["reason"] = "Provider returned empty response"
            return None, diag
        return raw, diag
    except urllib.error.HTTPError as e:
        diag["latency_ms"] = int((time.time() - t0) * 1000)
        diag["http_status"] = e.code
        diag["reason"] = _sanitize(f"HTTP {e.code} from provider")
        return None, diag
    except urllib.error.URLError as e:
        diag["latency_ms"] = int((time.time() - t0) * 1000)
        diag["reason"] = _sanitize(f"Network error: {getattr(e, 'reason', e)}")
        return None, diag
    except TimeoutError:
        diag["latency_ms"] = int((time.time() - t0) * 1000)
        diag["reason"] = f"Timeout after {timeout:.0f}s"
        return None, diag
    except (RuntimeError, OSError, ValueError) as e:
        diag["latency_ms"] = int((time.time() - t0) * 1000)
        diag["reason"] = _sanitize(f"{type(e).__name__}: {e}")
        return None, diag


_REPAIR_SYSTEM = (
    "Return only valid JSON. No reasoning. No <think>. No markdown. No code "
    "fences. Use only the provided Ledger evidence. Do not invent numbers. "
    "Keep text concise."
)


def _call_and_parse(
    system_prompt: str,
    user_prompt: str,
    *,
    feature_id: str,
    timeout: float = _EXPLAINER_TIMEOUT_S,
    max_tokens: int = _EXPLAINER_MAX_TOKENS,
    retry_max_tokens: Optional[int] = None,
) -> tuple[Optional[dict], str, dict]:
    """One AI round-trip with parsing + one retry on parse failure.

    Returns (parsed_dict_or_None, parse_error_str, diag).
    - Success:               parsed=dict,  parse_error="",  diag carries latency/chars/keys.
    - Call-level failure:    parsed=None,  parse_error=diag["reason"], diag["attempted"]
                              reflects whether the network call was actually made.
    - Parse failure (retry): parsed=None or dict (if retry won),
                              diag carries retry_attempted/retry_result/retry_*.

    Retry policy (Phase 3):
    - Fires only when the first call returned non-empty text but parsing failed.
    - Does NOT fire on: AI disabled, missing key, provider hard error, timeout,
      empty response, or schema validation failure (validation is the surface's job).
    - Retry uses a strict repair system prompt + the original user prompt.
    - Retry max_tokens defaults to the original budget (already generous).

    Diagnostics keys (sanitized; safe for UI):
        attempted, provider, model, latency_ms, response_chars, http_status,
        parsed_keys (on success),
        parse_error (on first-call parse failure),
        retry_attempted, retry_result ('success'|'parse_failed'|'call_failed'),
        retry_latency_ms, retry_response_chars, retry_parse_error,
        final_result ('success'|'parse_failed'|'call_failed').
    """
    raw, diag = _call_ai(system_prompt, user_prompt, feature_id=feature_id,
                         timeout=timeout, max_tokens=max_tokens)
    diag.setdefault("retry_attempted", False)

    # Call-level failure (network/http/timeout/disabled/empty) — never retry.
    if raw is None:
        diag["final_result"] = "call_failed"
        return None, diag.get("reason", ""), diag

    parsed, perr = _parse_json(raw)
    if parsed is not None:
        diag["parsed_keys"] = list(parsed.keys())
        diag["final_result"] = "success"
        return parsed, "", diag

    # Got text but couldn't parse it — almost always M2.7 over-thought past the
    # output budget and truncated before the JSON. One retry with an explicit
    # repair prompt and a fresh budget.
    diag["parse_error"] = perr
    diag["retry_attempted"] = True
    retry_budget = int(retry_max_tokens or max_tokens)
    repair_user = (
        "Your previous response could not be parsed as JSON. "
        "Return only valid JSON now — no reasoning, no <think>, no markdown, "
        "no code fences. Same evidence packet, same schema as before:\n\n"
        + user_prompt
    )

    raw2, diag2 = _call_ai(_REPAIR_SYSTEM, repair_user, feature_id=feature_id,
                           timeout=timeout, max_tokens=retry_budget)

    diag["retry_latency_ms"] = diag2.get("latency_ms", 0)
    diag["retry_response_chars"] = diag2.get("response_chars", 0)
    diag["latency_ms"] = diag.get("latency_ms", 0) + diag2.get("latency_ms", 0)

    if raw2 is None:
        diag["retry_result"] = "call_failed"
        diag["retry_parse_error"] = diag2.get("reason", "")
        diag["final_result"] = "call_failed"
        return None, perr, diag

    parsed2, perr2 = _parse_json(raw2)
    if parsed2 is not None:
        diag["retry_result"] = "success"
        diag["parsed_keys"] = list(parsed2.keys())
        diag["final_result"] = "success"
        # Use retry's response_chars as the final response_chars (most recent text)
        diag["response_chars"] = diag2.get("response_chars", 0)
        return parsed2, "", diag

    diag["retry_result"] = "parse_failed"
    diag["retry_parse_error"] = perr2
    diag["final_result"] = "parse_failed"
    return None, perr2 or perr, diag


# ── Reasoning-token + JSON parsing ───────────────────────────────────────

# Reasoning models like MiniMax-M2.7 wrap chain-of-thought in <think>...</think>.
# Some providers also use <reasoning>...</reasoning>. Strip both before parsing.
_THINK_PATTERNS = [
    re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<reasoning>.*?</reasoning>\s*", re.DOTALL | re.IGNORECASE),
]
# Truncated/unclosed think block: drop everything from <think> to end.
_UNCLOSED_THINK = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    if not text:
        return ""
    s = text
    for pat in _THINK_PATTERNS:
        s = pat.sub("", s)
    # If a think block was opened but never closed (response truncated mid-think),
    # drop it entirely so we don't try to parse reasoning prose as JSON.
    s = _UNCLOSED_THINK.sub("", s)
    return s.strip()


def _extract_balanced_json(s: str) -> Optional[str]:
    """Return the first balanced {...} block in s, or None.

    More robust than `s.find('{')..s.rfind('}')` when the response mixes prose
    and JSON, or when reasoning leftovers contain stray braces."""
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


def _parse_json(text: str) -> tuple[Optional[dict], str]:
    """Lenient JSON parser. Returns (parsed_or_None, parse_error_message).

    Strips <think>/<reasoning> blocks, code fences, and prose; extracts the first
    balanced JSON object; returns a sanitized error string when parsing fails."""
    if not text:
        return None, "empty response"
    s = _strip_thinking(text).strip()

    # Strip markdown code fences
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
        return (obj if isinstance(obj, dict) else None), ""
    except json.JSONDecodeError:
        pass

    # Balanced-brace extraction
    block = _extract_balanced_json(s)
    if block:
        try:
            obj = json.loads(block)
            return (obj if isinstance(obj, dict) else None), ""
        except json.JSONDecodeError as e:
            return None, f"json decode in extracted block: {e.msg} at col {e.colno}"

    return None, "no JSON object found in response"


def _clamp_str(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut + "…"


def _provider_label() -> tuple[str, str]:
    ai = get_ai_settings()
    return (ai.get("provider") or "—", ai.get("model") or "—")


def _ai_meta(ok: bool, error: str = "", fallback: bool = False,
             diagnostic: Optional[dict] = None) -> dict:
    prov, model = _provider_label()
    return {
        "provider":   prov,
        "model":      model,
        "ok":         ok,
        "error":      _sanitize(error),
        "fallback":   fallback,
        "diagnostic": diagnostic or {},
    }


def _record_from_diag(
    feature_id: str,
    *,
    ok: bool,
    fallback: bool,
    diag: dict,
    reason: str = "",
    parse_error: str = "",
    validation_error: str = "",
) -> None:
    """Forward sanitized fields from `diag` (returned by _call_and_parse) into
    `_LAST_CALL`. Convenience wrapper so each surface stays one short call."""
    _record_call_status(
        feature_id,
        attempted=bool(diag.get("attempted")),
        ok=ok,
        fallback=fallback,
        reason=reason or diag.get("reason", ""),
        latency_ms=diag.get("latency_ms", 0),
        response_chars=diag.get("response_chars", 0),
        http_status=diag.get("http_status"),
        parsed_keys=diag.get("parsed_keys") or [],
        parse_error=parse_error or diag.get("parse_error", ""),
        validation_error=validation_error,
    )
    snap = _LAST_CALL.get(feature_id)
    if snap is not None:
        # Carry retry fields through so Settings can render them.
        for k in ("retry_attempted", "retry_result", "retry_latency_ms",
                  "retry_response_chars", "retry_parse_error", "final_result"):
            if k in diag:
                snap[k] = diag[k]


# ── Evidence-packet builders ──────────────────────────────────────────────

def _top_recs_packet(recs: list[dict], limit: int = 3) -> list[dict]:
    out = []
    for r in recs[:limit]:
        out.append({
            "title":           r.get("title"),
            "type":            r.get("type"),
            "priority":        r.get("priority"),
            "annual_impact":   r.get("annual_impact", 0),
            "composite_score": r.get("composite_score", 0),
            "drivers":         (r.get("drivers") or [])[:3],
        })
    return out


def _dashboard_packet(conn: sqlite3.Connection) -> dict:
    """Single deterministic evidence packet for the dashboard copilot.

    Pass 35d: anchors latest_month / mom comparison on the latest
    COMPLETE statement month (statement_coverage().complete_months[-1])
    rather than the absolute last imported month. Without this, a
    partial current month (e.g. May 2026 with only the first week) made
    the Copilot say things like "No income in May; spending surged…"
    Mirrors the Pass 35b fix in monthly_review().
    """
    from utils.analytics import compute_score, score_label
    from utils.insights import (
        compute_recommendations, monthly_aggregates,
        statement_coverage,
    )

    score_data = compute_score(conn=conn)
    recs = compute_recommendations(conn=conn)
    aggs = monthly_aggregates(conn=conn)

    # Pass 35d: prefer complete-month aggregates for the Copilot
    # packet. Falls back to the latest two months only when fewer than
    # 2 complete months exist (fresh install).
    try:
        _cov = statement_coverage(conn=conn) or {}
        _complete_set = set(_cov.get("complete_months") or [])
        _latest_data = _cov.get("latest_data_month") or ""
    except Exception:
        _cov = {}
        _complete_set = set()
        _latest_data = ""
    _aggs_complete = [a for a in aggs if (a.get("month") or "") in _complete_set]
    if len(_aggs_complete) >= 2:
        _aggs_for_packet = _aggs_complete
        _uses_complete = True
    else:
        _aggs_for_packet = aggs
        _uses_complete = False

    latest = _aggs_for_packet[-1] if _aggs_for_packet else None
    prev   = _aggs_for_packet[-2] if len(_aggs_for_packet) >= 2 else None

    mom = {}
    if latest and prev:
        mom = {
            "latest_month":    latest["month"],
            "prior_month":     prev["month"],
            "spending_delta":  round(latest["spending"] - prev["spending"], 2),
            "income_delta":    round(latest["income"]   - prev["income"],   2),
            "net_delta":       round(latest["net"]      - prev["net"],      2),
        }

    flagged_n = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_flagged=1").fetchone()[0]

    return {
        "score":           score_data["total"],
        "score_label":     score_label(score_data["total"]),
        "data_confidence": score_data.get("data_confidence") or {},
        "sufficient":      bool(score_data.get("sufficient")),
        "prelim":          bool(score_data.get("prelim")),
        "dimensions":      score_data.get("dimensions") or [],
        "latest_month":    latest["month"] if latest else None,
        "latest": {
            "income":       latest["income"]       if latest else 0,
            "spending":     latest["spending"]     if latest else 0,
            "net":          latest["net"]          if latest else 0,
            "savings_rate": latest["savings_rate"] if latest else 0,
        } if latest else {},
        "mom":            mom,
        "flagged_count":  flagged_n,
        "top_recs":       _top_recs_packet(recs, limit=3),
        # Pass 35d: explicit statement-completeness context. Lets the
        # AI prompt and the deterministic fallback know that
        # latest_month is a COMPLETE month and that any in-progress
        # month should be referenced (if at all) as "recent partial
        # activity" — never as "the current month."
        "statement_completeness": {
            "uses_complete_months":  bool(_uses_complete),
            "complete_month":         latest["month"] if latest and _uses_complete else None,
            "latest_data_month":      _latest_data,
            "partial_months":         list(_cov.get("partial_months") or []),
        },
    }


# ── 1. Dashboard Copilot ──────────────────────────────────────────────────

_COPILOT_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Do not give financial advice beyond the evidence. "
    "Keep text concise.\n\n"
    "You are Ledger's local financial copilot. Explain the user's state using "
    "ONLY the JSON evidence packet — never invent numbers, categories, or "
    "merchants. Plain, calm, mature tone. No fluff, no emojis, no advisor "
    "persona.\n\n"
    "IMPORTANT — partial months: the 'latest_month' field is the latest "
    "COMPLETE statement month. The 'statement_completeness.partial_months' "
    "list names any in-progress months whose data is incomplete. Never "
    "treat a partial month as the current month, never claim the user has "
    "'no income' or 'low savings' in a partial month, and only reference a "
    "partial month with an explicit 'partial recent activity' caveat.\n\n"
    "JSON keys: headline (<=70 chars), summary (<=280 chars, 1–2 short "
    "sentences), moves (array of exactly 3 short imperatives, each "
    "<=120 chars, each grounded in the packet)."
)


def _deterministic_copilot(packet: dict) -> dict:
    """Fallback: builds the same shape from the packet without any AI."""
    score = packet.get("score", 0)
    label = packet.get("score_label", "")
    conf = packet.get("data_confidence") or {}
    level = conf.get("level", "insufficient")
    dims = packet.get("dimensions") or []
    recs = packet.get("top_recs") or []

    # Headline
    if level == "insufficient":
        headline = "Import more data to unlock a full picture"
    else:
        headline = f"Money Pulse {score}/100 — {label.lower()}"

    # Summary — pick the weakest sufficient dimension as the lens
    weak = None
    for d in sorted(dims, key=lambda d: (d["score"] / max(d["max"], 1))):
        if d.get("sufficient"):
            weak = d
            break

    mom = packet.get("mom") or {}
    latest = packet.get("latest") or {}

    parts: list[str] = []
    if weak:
        parts.append(f"{weak['label']} is the weakest dimension — {weak['reason']}")
    if mom:
        sd = mom["spending_delta"]
        sense = "up" if sd > 0 else "down" if sd < 0 else "flat"
        if abs(sd) >= 50:
            parts.append(
                f"Spending {sense} ${abs(sd):,.0f} vs {mom['prior_month']}."
            )
    if not parts and latest:
        parts.append(
            f"{packet.get('latest_month','Latest month')}: "
            f"net ${latest.get('net', 0):,.0f} on {latest.get('savings_rate', 0):.0f}% savings."
        )
    # Pass 35d: append a partial-month caveat so the user knows
    # latest_month is the latest *complete* month, not the in-progress
    # one. Defensive guard for older packets that lack the field.
    _sc = packet.get("statement_completeness") or {}
    _partials = _sc.get("partial_months") or []
    _latest_data = _sc.get("latest_data_month") or ""
    if _partials and _latest_data and _latest_data in _partials:
        parts.append(
            f"{_latest_data} is partial recent activity and is not "
            "counted as monthly truth yet."
        )
    summary = " ".join(parts)[:_MAX_SUMMARY_CHARS] or "Not enough data to summarize yet."

    # Moves: translate top recs
    moves: list[str] = []
    for r in recs[:3]:
        typ = (r.get("type") or "").upper()
        imp = r.get("annual_impact") or 0
        impact_str = f" (~${imp:,.0f}/yr)" if imp and imp > 0 else ""
        moves.append(_clamp_str(f"[{typ}] {r.get('title','')}{impact_str}", _MAX_MOVE_CHARS))
    if packet.get("flagged_count", 0) >= 5 and len(moves) < 3:
        moves.append(f"Clear {packet['flagged_count']} flagged rows in Review.")
    while len(moves) < 3:
        moves.append("Keep importing statements to sharpen every recommendation.")

    return {
        "headline":       headline,
        "summary":        summary,
        "moves":          moves[:_MAX_MOVES],
        "grounded_from":  _grounded_sources(packet),
        **_ai_meta(ok=False, fallback=True),
    }


def _grounded_sources(packet: dict) -> list[str]:
    """Human-readable evidence list, for the 'grounded in' caption."""
    src = []
    if packet.get("score") is not None:
        src.append(f"Money Pulse {packet['score']}/100")
    conf = packet.get("data_confidence") or {}
    if conf:
        src.append(f"Data confidence {conf.get('score','?')}/100 ({conf.get('level','?')})")
    if packet.get("latest_month"):
        src.append(f"Latest month: {packet['latest_month']}")
    mom = packet.get("mom") or {}
    if mom:
        src.append(f"vs {mom.get('prior_month','prior month')}")
    if packet.get("top_recs"):
        src.append(f"{len(packet['top_recs'])} top recommendations")
    return src


def _validate_copilot(parsed: dict) -> Optional[dict]:
    if not isinstance(parsed, dict):
        return None
    headline = _clamp_str(parsed.get("headline", ""), 90)
    summary  = _clamp_str(parsed.get("summary", ""), _MAX_SUMMARY_CHARS)
    raw_moves = parsed.get("moves") or []
    if not isinstance(raw_moves, list):
        return None
    moves = []
    for m in raw_moves[:_MAX_MOVES]:
        if isinstance(m, str):
            moves.append(_clamp_str(m, _MAX_MOVE_CHARS))
        elif isinstance(m, dict):
            moves.append(_clamp_str(m.get("text") or m.get("move") or "", _MAX_MOVE_CHARS))
    if not headline or not summary or not moves:
        return None
    return {"headline": headline, "summary": summary, "moves": moves}


def dashboard_copilot(conn: sqlite3.Connection) -> dict:
    """Main copilot: builds packet, calls AI once, falls back cleanly."""
    packet = _dashboard_packet(conn)
    fid = "dashboard_copilot"

    # Short-circuit when we genuinely can't say anything useful yet
    if packet.get("data_confidence", {}).get("score", 0) == 0:
        _record_call_status(fid, attempted=False, ok=False, fallback=True,
                            reason="no data yet")
        return {
            "headline": "No data yet",
            "summary":  "Import at least one statement to get started. "
                        "Everything Ledger shows is computed locally from imported data.",
            "moves":    [
                "Open the Import page and add a statement.",
                "Set a budget in Settings → Budgets after your first import.",
                "Check Review for anything the parser wasn't sure about.",
            ],
            "grounded_from": [],
            **_ai_meta(ok=False, fallback=True),
        }

    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_copilot(packet)
        out["error"] = reason
        return out

    user_prompt = (
        "Packet (authoritative — do not invent anything else):\n"
        + json.dumps(packet, ensure_ascii=False, default=float)
        + "\n\nReturn ONLY a JSON object — no prose, no markdown, no code fences. "
        'Keys: {"headline":"<=70 chars","summary":"<=280 chars",'
        '"moves":["exactly 3 short imperatives, each <=120 chars"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _COPILOT_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_COPILOT,
    )
    vetted = _validate_copilot(parsed) if parsed else None
    if vetted is None:
        verr = ""
        if parsed and not vetted:
            verr = "missing required keys (headline/summary/moves)"
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr, validation_error=verr)
        out = _deterministic_copilot(packet)
        out["error"] = (diag.get("reason") or perr or verr
                        or "AI response unparseable — fallback used.")
        out["diagnostic"] = {**diag, "parse_error": perr, "validation_error": verr}
        return out

    _record_from_diag(fid, ok=True, fallback=False, diag={**diag, "parsed_keys": list(vetted.keys())})
    return {
        **vetted,
        "grounded_from": _grounded_sources(packet),
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 2. Recommendation explainer ───────────────────────────────────────────

_REC_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Keep text concise.\n\n"
    "Explain a single personal-finance recommendation. Use ONLY the "
    "evidence packet. JSON keys: why_it_matters (<=240 chars), action "
    "(<=160 chars, imperative), nature ('money' or 'cleanup'). "
    "Plain, concrete tone — no fluff, no advisor persona."
)


def _deterministic_rec_explanation(rec: dict) -> dict:
    typ = rec.get("type", "investigate")
    nature = "cleanup" if typ in ("review", "fix") else "money"
    imp = rec.get("annual_impact", 0) or 0
    why_bits = []
    if imp > 0:
        why_bits.append(f"Estimated ~${imp:,.0f}/year impact if acted on.")
    conf = rec.get("confidence")
    ctrl = rec.get("controllability")
    urg  = rec.get("urgency")
    if conf is not None and ctrl is not None and urg is not None:
        why_bits.append(
            f"Signal confidence {int(conf*100)}% · "
            f"you control {int(ctrl*100)}% of this · "
            f"urgency {int(urg*100)}%."
        )
    why = " ".join(why_bits) or rec.get("body", "")
    action = rec.get("action_label") or rec.get("title") or "Open the recommendation"
    return {
        "why_it_matters": _clamp_str(why, 240),
        "action":         _clamp_str(action, 160),
        "nature":         nature,
        "grounded_from":  [d.get("kind", "") for d in (rec.get("drivers") or [])],
        **_ai_meta(ok=False, fallback=True),
    }


def explain_recommendation(rec: dict, context: Optional[dict] = None) -> dict:
    packet = {
        "rec": {
            "title":           rec.get("title"),
            "body":            rec.get("body"),
            "type":            rec.get("type"),
            "priority":        rec.get("priority"),
            "annual_impact":   rec.get("annual_impact"),
            "composite_score": rec.get("composite_score"),
            "confidence":      rec.get("confidence"),
            "controllability": rec.get("controllability"),
            "urgency":         rec.get("urgency"),
            "drivers":         rec.get("drivers") or [],
            "evidence":        rec.get("evidence"),
            "category":        rec.get("category"),
        },
        "context": context or {},
    }

    fid = "recommendation_explainer"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_rec_explanation(rec)
        out["error"] = reason
        return out

    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"why_it_matters":"<=240 chars","action":"<=160 chars","nature":"money or cleanup"}'
    )
    parsed, perr, diag = _call_and_parse(
        _REC_SYSTEM, user_prompt, feature_id=fid, max_tokens=_BUDGET_REC,
    )
    if not isinstance(parsed, dict):
        _record_from_diag(fid, ok=False, fallback=True, diag=diag, parse_error=perr)
        out = _deterministic_rec_explanation(rec)
        out["error"] = (diag.get("reason") or perr
                        or "AI response unparseable — fallback used.")
        out["diagnostic"] = {**diag, "parse_error": perr}
        return out

    why    = _clamp_str(str(parsed.get("why_it_matters", "")), 240)
    action = _clamp_str(str(parsed.get("action", "")), 160)
    nature = str(parsed.get("nature", "")).strip().lower()
    if nature not in ("money", "cleanup"):
        nature = "cleanup" if rec.get("type") in ("review", "fix") else "money"
    if not why or not action:
        verr = "missing why_it_matters or action"
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error=verr,
                          reason="AI response incomplete")
        out = _deterministic_rec_explanation(rec)
        out["error"] = "AI response incomplete — using deterministic summary."
        out["diagnostic"] = {**diag, "validation_error": verr}
        return out
    _record_from_diag(fid, ok=True, fallback=False,
                      diag={**diag, "parsed_keys": ["why_it_matters", "action", "nature"]})
    return {
        "why_it_matters": why,
        "action":         action,
        "nature":         nature,
        "grounded_from":  [d.get("kind", "") for d in (rec.get("drivers") or [])],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 3. Review triage summary ──────────────────────────────────────────────

_TRIAGE_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Keep text concise.\n\n"
    "Summarize a personal-finance Review queue. JSON keys: "
    "headline (<=80 chars), summary (<=240 chars), clean_first (array of "
    "2–3 short imperatives with reasons, each <=120 chars). Distinguish "
    "high-impact items (cash advance, NSF, large debit) from data-quality "
    "items (low parse confidence, cancelled pairs). No fluff."
)


def _deterministic_triage(packet: dict) -> dict:
    total = packet.get("total_flagged", 0)
    hi    = packet.get("high_impact_count", 0)
    hi_v  = packet.get("high_impact_value", 0)
    by    = packet.get("by_reason") or {}

    if total == 0:
        headline = "Queue is clear"
        summary  = "No flagged items to review. Import more statements to keep coverage current."
        moves = ["Keep imports up to date.", "Spot-check Recent Transactions on the Dashboard.", "No action required now."]
    elif hi > 0:
        headline = f"{hi} high-impact item(s) worth ${hi_v:,.0f} to review first"
        summary = (
            f"{total} flagged total; {hi} are financially material "
            f"(cash advance / NSF / large debit). The rest are mostly data quality."
        )
        reasons_bits = [f"{n} {k}" for k, n in sorted(by.items(), key=lambda kv: -kv[1])[:3]]
        moves = [
            f"Clear the {hi} high-impact item(s) first — they affect cashflow totals.",
            f"Then work through: {', '.join(reasons_bits)}.",
            "Low parse-confidence rows can be batch-cleared once spot-checked.",
        ]
    else:
        headline = f"{total} items to review — mostly data quality"
        summary = "No financially material flags detected — clearing is bookkeeping."
        moves = [
            "Use 'Clear all flags in current filter' for the low-parse batch once verified.",
            "Consider enabling AI suggestions in Settings to speed up categorization.",
            "Re-run categorization after updating rules to sync any stale flags.",
        ]
    return {
        "headline":     headline,
        "summary":      summary,
        "clean_first":  moves[:3],
        "grounded_from": [f"flagged={total}", f"high_impact={hi}"],
        **_ai_meta(ok=False, fallback=True),
    }


def review_triage_summary(flagged: list[dict], ai_candidate_count: int = 0) -> dict:
    # Build packet
    from collections import Counter
    reasons = Counter()
    for t in flagged:
        for r in (t.get("flag_reason") or "").split(";"):
            r = r.strip()
            if r:
                reasons[r] += 1

    _HIGH_IMPACT = ("cash advance", "nsf", "large debit")
    hi_items = [t for t in flagged
                if any(r in (t.get("flag_reason") or "").lower() for r in _HIGH_IMPACT)]
    hi_value = sum(abs(t.get("amount", 0)) for t in hi_items)

    # Top-$ rows with their reason for grounding
    top_amounts = sorted(flagged, key=lambda t: abs(t.get("amount", 0)), reverse=True)[:3]
    top_desc = [
        {
            "merchant": (t.get("merchant") or t.get("raw_description") or "")[:40],
            "amount":   round(abs(t.get("amount", 0)), 2),
            "reason":   (t.get("flag_reason") or "").split(";")[0].strip(),
            "date":     t.get("transaction_date"),
        }
        for t in top_amounts
    ]

    packet = {
        "total_flagged":     len(flagged),
        "by_reason":         dict(reasons),
        "high_impact_count": len(hi_items),
        "high_impact_value": round(hi_value, 2),
        "ai_candidate_count": ai_candidate_count,
        "top_amounts":       top_desc,
    }

    fid = "review_triage"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_triage(packet)
        out["error"] = reason
        return out

    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"headline":"<=80 chars","summary":"<=240 chars","clean_first":["2-3 short imperatives <=120 chars each"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _TRIAGE_SYSTEM, user_prompt, feature_id=fid, max_tokens=_BUDGET_TRIAGE,
    )
    if not isinstance(parsed, dict):
        _record_from_diag(fid, ok=False, fallback=True, diag=diag, parse_error=perr)
        out = _deterministic_triage(packet)
        out["error"] = (diag.get("reason") or perr
                        or "AI response unparseable — fallback used.")
        out["diagnostic"] = {**diag, "parse_error": perr}
        return out
    headline = _clamp_str(str(parsed.get("headline", "")), 100)
    summary  = _clamp_str(str(parsed.get("summary", "")), 280)
    raw_moves = parsed.get("clean_first") or []
    moves: list[str] = []
    for m in raw_moves[:3]:
        if isinstance(m, str):
            moves.append(_clamp_str(m, _MAX_MOVE_CHARS))
        elif isinstance(m, dict):
            moves.append(_clamp_str(m.get("text") or m.get("move") or "", _MAX_MOVE_CHARS))
    if not headline or not summary or not moves:
        verr = "missing headline/summary/clean_first"
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error=verr,
                          reason="AI response incomplete")
        out = _deterministic_triage(packet)
        out["error"] = "AI response incomplete — using deterministic summary."
        out["diagnostic"] = {**diag, "validation_error": verr}
        return out
    _record_from_diag(fid, ok=True, fallback=False,
                      diag={**diag, "parsed_keys": ["headline", "summary", "clean_first"]})
    return {
        "headline":     headline,
        "summary":      summary,
        "clean_first":  moves,
        "grounded_from": [f"flagged={packet['total_flagged']}",
                          f"high_impact={packet['high_impact_count']}"],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 4. Mission framing (1-liner) ──────────────────────────────────────────

_MISSION_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Keep text concise.\n\n"
    "Write ONE short, plain-English sentence (<=150 chars) framing the "
    "user's chosen monthly mission, grounded in the packet. No cheer, "
    "no advisor persona, no emojis. JSON: {\"line\":\"...\"}."
)


def mission_framing(mission: dict, streaks: dict) -> dict:
    """Optional AI one-liner for the Mission card. Fallback = mission.description."""
    fid = "mission_framing"
    ready, reason = ai_is_ready()
    fallback_line = mission.get("description") or mission.get("title") or ""
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        return {
            "line":         _clamp_str(fallback_line, 150),
            "grounded_from": ["mission", "streaks"],
            "error":        reason,
            **_ai_meta(ok=False, fallback=True),
        }

    packet = {"mission": mission, "streaks": streaks}
    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose. {"line":"<=150 chars"}'
    )
    parsed, perr, diag = _call_and_parse(
        _MISSION_SYSTEM, user_prompt, feature_id=fid,
        timeout=45.0, max_tokens=_BUDGET_MISSION,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        return {
            "line":         _clamp_str(fallback_line, 150),
            "grounded_from": ["mission", "streaks"],
            "error":        diag.get("reason") or
                            (f"AI response unparseable ({perr}) — fallback used." if perr
                             else "AI call failed — using deterministic framing."),
            "diagnostic":   diag,
            **_ai_meta(ok=False, fallback=True),
        }
    line = _clamp_str(str(parsed.get("line", "")), 150) if isinstance(parsed, dict) else ""
    if not line:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing 'line'")
        return {
            "line":         _clamp_str(fallback_line, 150),
            "grounded_from": ["mission", "streaks"],
            "error":        "AI response missing 'line' — fallback used.",
            "diagnostic":   diag,
            **_ai_meta(ok=False, fallback=True),
        }
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "line":         line,
        "grounded_from": ["mission", "streaks"],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 5. Ask Ledger — skill router ──────────────────────────────────────────

ASK_PRESETS = [
    ("top_cuts",                "What should I cut first this month?"),
    ("active_subscriptions",    "Which active subscriptions should I review?"),
    ("over_target_categories",  "Which categories are above target?"),
    ("what_changed",            "What changed since last month?"),
    ("cleanup_first",           "What transactions should I clean up first?"),
    ("explain_score",           "Why is my Money Pulse where it is?"),
    ("safest_hundred",          "What is my safest $100/month savings move?"),
    ("weekly_focus",            "What should I focus on this week?"),
    # Pass 22 — Ask Ledger v3 presets aligned to the planning loop.
    ("month_plan",              "What is my plan this month?"),
    ("forecast_risk",           "Am I on track this month?"),
    ("safe_to_spend",           "How much can I safely spend?"),
    ("bills_due",               "What bills are coming up?"),
    ("category_targets",        "Which category target should I focus on?"),
    ("goal_progress",           "How are my goals doing?"),
    ("next_payday_focus",       "What should I do before next payday?"),
    ("reminder_suggestions",    "What should OpenClaw remind me about?"),
]

SUPPORTED_SKILLS = {p[0] for p in ASK_PRESETS}


def _route_question(q: str) -> Optional[str]:
    """Return a supported skill id, or None when the question is out-of-scope.

    Returning None lets the caller refuse cleanly instead of faking an answer.
    """
    s = (q or "").lower().strip()
    if not s:
        return None
    # Block obviously out-of-scope asks (markets, news, taxes, predictions, etc.)
    _OUT_OF_SCOPE = (
        "stock", "invest in", "crypto", "bitcoin", "ethereum", "market", "s&p",
        "news", "weather", "tax return", "tax filing", "should i buy",
        "predict", "forecast", "rate hike", "interest rate hike", "mortgage rate",
    )
    if any(k in s for k in _OUT_OF_SCOPE):
        return None
    # Pass 22 — planning-loop routes run FIRST so phrases like
    # "am i on track this month" don't get swallowed by "this month"
    # in the older what_changed route below.
    if any(k in s for k in ("safe to spend", "safely spend", "how much can i spend",
                             "spending headroom", "spend left")):
        return "safe_to_spend"
    if any(k in s for k in ("on track", "off track", "behind this month",
                             "ahead this month", "forecast", "projected",
                             "month-end", "end of month")):
        return "forecast_risk"
    if any(k in s for k in ("bill", "bills due", "what's coming", "what is coming",
                             "upcoming", "commitment", "due this month")):
        return "bills_due"
    if any(k in s for k in ("category target", "budget target", "category to focus",
                             "which target", "where am i over budget",
                             "biggest budget gap")):
        return "category_targets"
    if any(k in s for k in ("goal", "milestone", "buffer", "emergency fund",
                             "net worth target", "savings target")):
        return "goal_progress"
    if any(k in s for k in ("next payday", "before payday", "until payday",
                             "rest of month", "leftover")):
        return "next_payday_focus"
    if any(k in s for k in ("plan this month", "month plan", "this month plan",
                             "monthly plan", "what is my plan", "what's my plan")):
        return "month_plan"
    if any(k in s for k in ("openclaw", "remind me", "reminder", "agent")):
        return "reminder_suggestions"

    if any(k in s for k in ("score", "why is my", "rating", "grade")):
        return "explain_score"
    if any(k in s for k in ("safest", "$100", "100/month", "100 a month", "small saving",
                             "easy savings", "easiest saving")):
        return "safest_hundred"
    if any(k in s for k in ("save more", "savings plan", "save $", "save next month")):
        return "safest_hundred"
    if any(k in s for k in ("this week", "weekly", "focus this week", "best move today")):
        return "weekly_focus"
    if any(k in s for k in ("above target", "over budget", "over target", "above budget",
                             "category", "drill", "biggest spend", "largest category")):
        return "over_target_categories"
    if any(k in s for k in ("change", "changed", "vs last", "this month", "compared",
                             "last month", "since last")):
        return "what_changed"
    if any(k in s for k in ("cut", "reduce", "spend less", "trim", "first this month",
                             "what should i cut")):
        return "top_cuts"
    if any(k in s for k in ("clean up", "cleanup", "review", "flag", "suspicious",
                             "queue", "uncategorized")):
        return "cleanup_first"
    if any(k in s for k in ("active subscription", "active subs", "subscription",
                             "subscribe", "recurring", "netflix", "spotify")):
        return "active_subscriptions"
    return None  # Unsupported — refuse cleanly


_ASK_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Do not give financial advice beyond the "
    "evidence. Keep text concise.\n\n"
    "Answer the user's question about their personal finances using ONLY "
    "the JSON evidence packet — never invent numbers. Short, concrete, "
    "calm. JSON keys: answer (<=420 chars), bullets (array of 2–4 short "
    "lines, each <=120 chars, each grounded in the packet)."
)


def _build_ask_packet(skill: str, conn: sqlite3.Connection) -> dict:
    from utils.analytics import compute_score, score_label
    from utils.insights import (
        compute_recommendations, monthly_aggregates, recurring_merchants,
    )

    packet = {"skill": skill}
    if skill == "explain_score":
        sd = compute_score(conn=conn)
        packet["score"]       = sd["total"]
        packet["label"]       = score_label(sd["total"])
        packet["dimensions"]  = sd.get("dimensions") or []
        packet["data_confidence"] = sd.get("data_confidence") or {}

    elif skill == "what_changed":
        aggs = monthly_aggregates(conn=conn)
        if aggs:
            latest = aggs[-1]
            prev = aggs[-2] if len(aggs) >= 2 else None
            packet["latest"] = latest
            packet["prior"]  = prev
            if prev:
                packet["delta"] = {
                    "spending": round(latest["spending"] - prev["spending"], 2),
                    "income":   round(latest["income"]   - prev["income"],   2),
                    "net":      round(latest["net"]      - prev["net"],      2),
                    "savings_rate": round(latest["savings_rate"] - prev["savings_rate"], 1),
                }
            # Category-level MoM drivers
            try:
                rows = conn.execute("""
                    SELECT category,
                           SUM(CASE WHEN strftime('%Y-%m', transaction_date)=? THEN ABS(amount) ELSE 0 END) AS cur,
                           SUM(CASE WHEN strftime('%Y-%m', transaction_date)=? THEN ABS(amount) ELSE 0 END) AS prv
                    FROM transactions
                    WHERE direction='debit' AND is_transfer=0
                      AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                           'Payment','Credit Card Payment','Cancelled')
                    GROUP BY category
                """, (latest["month"], prev["month"] if prev else "")).fetchall()
                drivers = [
                    {"category": r["category"],
                     "latest": round(r["cur"] or 0, 2),
                     "prior":  round(r["prv"] or 0, 2),
                     "delta":  round((r["cur"] or 0) - (r["prv"] or 0), 2)}
                    for r in rows
                ]
                drivers = [d for d in drivers if abs(d["delta"]) >= 20]
                drivers.sort(key=lambda d: -abs(d["delta"]))
                packet["category_drivers"] = drivers[:5]
            except Exception:
                packet["category_drivers"] = []

    elif skill == "top_cuts":
        recs = compute_recommendations(conn=conn)
        cuts = [r for r in recs if r.get("type") in ("cut", "optimize")][:5]
        packet["cuts"] = _top_recs_packet(cuts, limit=5)

    elif skill == "active_subscriptions":
        # Pass 18: ACTIVE candidates only — never push the user to
        # cancel a stale sub that's likely already gone.
        from utils.insights import subscription_detective
        det = subscription_detective(conn=conn)
        packet["active_candidates"] = [
            {"merchant": c["merchant"], "monthly": c["avg_amount"],
             "annual": c["annual"], "flags": c["flags"],
             "months_seen": c["months_seen"], "last_seen": c.get("last_seen", "")}
            for c in (det.get("active_candidates") or [])
        ]
        packet["active_subscriptions_top"] = [
            {"merchant": s["merchant"], "monthly": s["avg_amount"],
             "annual": s["annual"]}
            for s in (det.get("active_subs") or [])[:8]
        ]
        packet["active_monthly_estimate"] = float(det.get("active_monthly_estimate") or 0)
        packet["active_annual_total"]     = float(det.get("active_annual_total") or 0)
        packet["active_candidate_annual_total"] = float(det.get("active_candidate_annual_total") or 0)
        packet["stale_count"]             = len(det.get("stale_subs") or [])
        packet["anchor_date"]             = det.get("anchor_date") or ""
        packet["active_window_days"]      = int(det.get("active_window_days") or 60)

    elif skill == "over_target_categories":
        # Categories that are over-budget OR drift-flagged + the latest
        # 90-day controllable-cat averages.
        from utils.insights import (
            budget_vs_actuals, category_drift, top_controllable_categories,
            imported_months,
        )
        months = imported_months(conn=conn)
        latest_month = months[-1] if months else None
        bva = []
        if latest_month:
            bva = budget_vs_actuals(latest_month, conn=conn) or []
        drift = category_drift(lookback_months=2, conn=conn) or []
        packet["latest_month"] = latest_month
        packet["over_budget"] = [
            {"category": b["category"], "actual": b["actual"],
             "budget": b["budget"], "remaining": b["remaining"]}
            for b in bva if b["over_budget"] and b["budget"]
        ]
        packet["drift_flagged"] = [
            {"category": d["category"], "recent_avg": d["recent_avg"],
             "prior_avg": d["prior_avg"], "abs_change": d["abs_change"],
             "pct_change": d["pct_change"]}
            for d in drift if d["flagged"]
        ][:5]
        packet["controllable_top"] = top_controllable_categories(conn=conn, limit=5)

    elif skill == "cleanup_first":
        # Highest-leverage data-cleanup queue: high-impact flags, then
        # uncategorized, then low-confidence. Pass 18: prioritise cash
        # advance / fees / large debit reasons.
        row = conn.execute("""
            SELECT COUNT(*) AS c,
                   SUM(CASE WHEN flag_reason LIKE '%cash advance%' OR flag_reason LIKE '%nsf%'
                             OR flag_reason LIKE '%large debit%' THEN 1 ELSE 0 END) AS hi
            FROM transactions WHERE is_flagged=1
        """).fetchone()
        top_hi = conn.execute("""
            SELECT id, transaction_date, merchant, raw_description, amount, flag_reason
            FROM transactions WHERE is_flagged=1
              AND (flag_reason LIKE '%cash advance%' OR flag_reason LIKE '%nsf%'
                   OR flag_reason LIKE '%large debit%')
            ORDER BY ABS(amount) DESC LIMIT 3
        """).fetchall()
        # Uncategorized + low confidence counts.
        u_row = conn.execute("""
            SELECT
                SUM(CASE WHEN category='Uncategorized' OR category IS NULL OR category='' THEN 1 ELSE 0 END) AS uncat,
                SUM(CASE WHEN parse_confidence IS NOT NULL AND parse_confidence < 0.5 THEN 1 ELSE 0 END) AS lowc
            FROM transactions WHERE direction != 'cancelled'
        """).fetchone()
        packet["flagged_total"]     = row["c"] or 0
        packet["high_impact_count"] = row["hi"] or 0
        packet["top_high_impact"]   = [
            {"merchant": (t["merchant"] or t["raw_description"] or "")[:40],
             "amount": round(abs(t["amount"] or 0), 2),
             "reason": (t["flag_reason"] or "").split(";")[0].strip(),
             "date":   t["transaction_date"]}
            for t in top_hi
        ]
        packet["uncategorized"]   = int(u_row["uncat"] or 0) if u_row else 0
        packet["low_confidence"]  = int(u_row["lowc"] or 0) if u_row else 0

    elif skill == "safest_hundred":
        # Combine ACTIVE subscription candidates + controllable category
        # targets + a target floor of $100/month. We return up to 4
        # concrete "moves" the user could combine to clear $100.
        from utils.insights import top_controllable_categories, subscription_detective
        aggs = monthly_aggregates(conn=conn)
        latest = aggs[-1] if aggs else None
        if latest:
            packet["latest"] = {
                "month": latest["month"], "income": latest["income"],
                "spending": latest["spending"], "net": latest["net"],
                "savings_rate": latest["savings_rate"],
            }
        det = subscription_detective(conn=conn)
        # Build candidate moves with deterministic monthly_save figures.
        moves: list[dict] = []
        for c in (det.get("active_candidates") or [])[:3]:
            moves.append({
                "kind":         "cancel_subscription",
                "label":        f"Cancel {c['merchant']}",
                "monthly_save": round(float(c["avg_amount"] or 0), 2),
                "annual_save":  round(float(c["annual"] or 0), 2),
                "evidence":     f"~${c['avg_amount']:,.2f}/mo · flagged: "
                                f"{', '.join(c.get('flags') or []) or 'review'}",
            })
        for ctl in top_controllable_categories(conn=conn, limit=3) or []:
            m_avg = float(ctl["monthly_avg"] or 0)
            if m_avg < 50:
                continue
            save = round(m_avg * 0.20, 2)
            moves.append({
                "kind":         "cut_category",
                "label":        f"Cut {ctl['category']} by 20%",
                "monthly_save": save,
                "annual_save":  round(save * 12, 2),
                "evidence":     (
                    f"90-day avg ${m_avg:,.2f}/mo · "
                    f"target ${m_avg * 0.80:,.2f}/mo"
                ),
            })
        packet["moves"] = moves[:4]
        # Smallest set that crosses $100/month — pre-computed so the
        # answer can be specific instead of a hand-wave.
        ranked = sorted(moves, key=lambda m: -m["monthly_save"])
        running = 0.0
        chosen: list[dict] = []
        for m in ranked:
            chosen.append(m)
            running += m["monthly_save"]
            if running >= 100:
                break
        packet["plan"] = {
            "moves":        chosen,
            "monthly_total": round(running, 2),
            "annual_total":  round(running * 12, 2),
            "hits_100":     running >= 100,
        }

    elif skill == "weekly_focus":
        # Evidence: flagged count, streaks, top rec, recent week delta
        from utils.momentum import compute_streaks
        s = compute_streaks(conn=conn)
        packet["streaks"] = s
        recs = compute_recommendations(conn=conn)
        packet["top_rec"] = _top_recs_packet(recs, limit=1)[0] if recs else None
        # Last 7 days of spending
        row = conn.execute("""
            SELECT SUM(ABS(amount)) AS total, COUNT(*) AS cnt
            FROM transactions
            WHERE direction='debit' AND is_transfer=0
              AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                   'Payment','Credit Card Payment','Cancelled',
                                   'Housing / Mortgage','Fees / Interest')
              AND transaction_date >= date('now', '-7 days')
        """).fetchone()
        packet["last_7d_spending"] = round(row["total"] or 0, 2) if row else 0
        packet["last_7d_tx_count"] = int(row["cnt"] or 0) if row else 0

    elif skill == "category_drilldown":
        # Biggest consumption category over last 90d + its top merchants
        row = conn.execute("""
            SELECT category, SUM(ABS(amount)) AS total
            FROM transactions
            WHERE direction='debit' AND is_transfer=0
              AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                                   'Payment','Credit Card Payment','Cancelled',
                                   'Housing / Mortgage','Fees / Interest','Cash Advance')
              AND transaction_date >= date('now', '-90 days')
            GROUP BY category ORDER BY total DESC LIMIT 1
        """).fetchone()
        if row and row["category"]:
            cat = row["category"]
            merchants = conn.execute("""
                SELECT merchant, SUM(ABS(amount)) AS total, COUNT(*) AS cnt
                FROM transactions
                WHERE category=? AND direction='debit' AND is_transfer=0
                  AND transaction_date >= date('now', '-90 days')
                GROUP BY merchant ORDER BY total DESC LIMIT 5
            """, (cat,)).fetchall()
            packet["top_category"] = cat
            packet["total_90d"]    = round(row["total"] or 0, 2)
            packet["monthly_avg"]  = round((row["total"] or 0) / 3.0, 2)
            packet["top_merchants"] = [
                {"merchant": m["merchant"], "total_90d": round(m["total"], 2),
                 "tx_count": m["cnt"]}
                for m in merchants
            ]

    elif skill == "merchant_explain":
        # Biggest recurring merchant excluding housing / mortgage
        from utils.insights import recurring_merchants
        rec = recurring_merchants(min_months=2, conn=conn)
        rec = [r for r in rec if r["category"] not in ("Housing / Mortgage", "Transfer", "Transfer Out")][:1]
        packet["merchant_top"] = rec[0] if rec else None

    # ── Pass 22: Ask Ledger v3 — planning-loop skills ──────────────
    elif skill in ("month_plan", "forecast_risk", "safe_to_spend",
                   "bills_due", "category_targets", "goal_progress",
                   "next_payday_focus", "reminder_suggestions"):
        from utils.planner import (
            analysis_anchor, forecast_month, bills_and_commitments,
            goal_progress as _gp_calc,
        )
        from utils.database import get_monthly_plan, get_goals
        anchor = analysis_anchor(conn=conn)
        plan = get_monthly_plan(anchor, conn=conn)
        packet["anchor_month"] = anchor
        packet["plan"] = plan or {"saved": False, "month": anchor}

        if skill in ("month_plan", "category_targets",
                     "next_payday_focus", "reminder_suggestions"):
            packet["category_targets"] = (plan or {}).get("category_targets") or []

        if skill in ("forecast_risk", "safe_to_spend",
                     "next_payday_focus", "reminder_suggestions",
                     "month_plan"):
            try:
                packet["forecast"] = forecast_month(
                    plan_month=anchor, conn=conn)
            except Exception as e:
                packet["forecast"] = {"error": str(e)}

        if skill in ("bills_due", "next_payday_focus",
                     "reminder_suggestions", "forecast_risk"):
            try:
                packet["bills"] = bills_and_commitments(conn=conn)
            except Exception as e:
                packet["bills"] = {"items": [], "error": str(e)}

        if skill in ("goal_progress", "reminder_suggestions"):
            try:
                packet["goals"] = _gp_calc(get_goals(conn=conn) or [],
                                            conn=conn)
            except Exception as e:
                packet["goals"] = []

        if skill == "next_payday_focus":
            # Heuristic: most-recent income credit ≥ $200 anchors a
            # rough payday cadence. We surface the date but never
            # claim certainty.
            row = conn.execute("""
                SELECT MAX(transaction_date) AS d
                FROM transactions
                WHERE direction='credit' AND amount >= 200
                  AND category IN ('Payroll Income','Income')
            """).fetchone()
            packet["last_payday_guess"] = (row["d"] if row else None)

    return packet


def _deterministic_ask(skill: str, packet: dict) -> dict:
    if skill == "explain_score":
        score = packet.get("score", 0)
        lbl   = packet.get("label", "")
        dims  = packet.get("dimensions") or []
        bullets = [f"{d['label']}: {d['score']:.0f}/{d['max']:.0f} — {d['reason']}"
                   for d in dims[:4]]
        answer = f"Your Money Pulse is {score}/100 ({lbl}). Main drivers below."
    elif skill == "what_changed":
        latest = packet.get("latest") or {}
        prior  = packet.get("prior") or {}
        delta  = packet.get("delta") or {}
        drivers = packet.get("category_drivers") or []
        if latest and prior:
            answer = (
                f"{latest.get('month','?')} vs {prior.get('month','?')}: "
                f"spending {'+' if delta.get('spending',0)>=0 else ''}${delta.get('spending',0):,.0f}, "
                f"income {'+' if delta.get('income',0)>=0 else ''}${delta.get('income',0):,.0f}, "
                f"net {'+' if delta.get('net',0)>=0 else ''}${delta.get('net',0):,.0f}."
            )
            bullets = [
                f"{d['category']}: ${d['latest']:,.0f} vs ${d['prior']:,.0f} "
                f"({'+' if d['delta']>=0 else ''}${d['delta']:,.0f})"
                for d in drivers[:4]
            ]
        else:
            answer = "Need at least two months of data to compare."
            bullets = []
    elif skill == "top_cuts":
        cuts = packet.get("cuts") or []
        if cuts:
            top = cuts[0]
            answer = (f"Top cut: {top.get('title','')} — "
                      f"~${(top.get('annual_impact') or 0):,.0f}/year. "
                      f"Open it from Recommendations to act on it.")
        else:
            answer = ("No clear cut opportunities found yet. Import more "
                      "months or clear the review queue so spending is "
                      "categorised correctly.")
        bullets = [f"{c['title']} — ~${c.get('annual_impact') or 0:,.0f}/yr"
                   for c in cuts[:4]]
    elif skill == "active_subscriptions":
        cands = packet.get("active_candidates") or []
        active_n = len(packet.get("active_subscriptions_top") or [])
        active_monthly = float(packet.get("active_monthly_estimate") or 0)
        cand_annual = float(packet.get("active_candidate_annual_total") or 0)
        stale_n = int(packet.get("stale_count") or 0)
        if cands:
            answer = (
                f"{len(cands)} active subscription(s) flagged for review — "
                f"cancelling all would free ~${cand_annual:,.0f}/year. "
                f"Active total: ${active_monthly:,.0f}/mo across {active_n} "
                f"service(s)."
            )
        elif active_n:
            answer = (
                f"{active_n} active subscription(s) at "
                f"~${active_monthly:,.0f}/mo — none flagged for review "
                f"this period. ({stale_n} likely-stopped sub(s) live in "
                f"Reduce → Possibly already stopped.)"
            )
        else:
            answer = "No active recurring subscriptions detected yet."
        bullets = [
            (f"{c['merchant']}: ${c['monthly']:,.2f}/mo "
             f"(${c['annual']:,.0f}/yr) · "
             f"{', '.join(c['flags']) or 'review'}")
            for c in cands[:4]
        ]
    elif skill == "over_target_categories":
        over = packet.get("over_budget") or []
        drift = packet.get("drift_flagged") or []
        ctl = packet.get("controllable_top") or []
        latest_month = packet.get("latest_month") or ""
        bits = []
        if over:
            bits.append(f"{len(over)} category(ies) are over budget in {latest_month}.")
        if drift:
            bits.append(f"{len(drift)} category(ies) climbed materially over the last 2 months.")
        if not bits:
            bits.append("No category is currently above its budget or drifting up.")
        answer = " ".join(bits)
        bullets = []
        for b in over[:3]:
            bullets.append(
                f"{b['category']}: ${b['actual']:,.2f} actual vs "
                f"${b['budget']:,.2f} budget"
            )
        for d in drift[:3]:
            bullets.append(
                f"{d['category']}: ${d['recent_avg']:,.0f}/mo recently vs "
                f"${d['prior_avg']:,.0f}/mo before "
                f"({'+' if d['abs_change']>=0 else ''}${d['abs_change']:,.0f}/mo)"
            )
        if not bullets:
            for c in ctl[:3]:
                bullets.append(
                    f"{c['category']}: 90-day avg ${c['monthly_avg']:,.0f}/mo "
                    f"(top controllable category)"
                )
    elif skill == "cleanup_first":
        tot = packet.get("flagged_total", 0)
        hi  = packet.get("high_impact_count", 0)
        uncat = packet.get("uncategorized", 0)
        lowc  = packet.get("low_confidence", 0)
        bits = []
        if hi:
            bits.append(f"{hi} financially-material flagged item(s) — clean these first.")
        elif tot:
            bits.append(f"{tot} flagged item(s) waiting in Review.")
        if uncat:
            bits.append(f"{uncat} uncategorized row(s).")
        if lowc:
            bits.append(f"{lowc} low-confidence categorization(s).")
        answer = " ".join(bits) if bits else "Queue is clear — no cleanup needed."
        bullets = [f"${r['amount']:,.0f} · {r['merchant']} · {r['reason']} ({r['date']})"
                   for r in (packet.get("top_high_impact") or [])]
    elif skill == "safest_hundred":
        plan = packet.get("plan") or {}
        moves_chosen = plan.get("moves") or []
        running = float(plan.get("monthly_total") or 0)
        hits = bool(plan.get("hits_100"))
        if moves_chosen and hits:
            answer = (
                f"Combine these moves to free ~${running:,.0f}/month "
                f"(~${plan.get('annual_total', 0):,.0f}/year). All grounded "
                f"in your last 90 days — no extrapolation."
            )
        elif moves_chosen:
            answer = (
                f"Best you can stack from current data: "
                f"~${running:,.0f}/month (~${plan.get('annual_total', 0):,.0f}/year). "
                f"Below the $100 floor — import more months or review "
                f"controllable categories for additional levers."
            )
        else:
            answer = (
                "No deterministic levers cleared yet — try Reduce → "
                "Active Cancellation Candidates and the Controllable "
                "category targets."
            )
        bullets = [
            f"{m['label']} → ~${m['monthly_save']:,.0f}/mo · {m['evidence']}"
            for m in moves_chosen
        ]
    elif skill == "weekly_focus":
        s = packet.get("streaks") or {}
        top = packet.get("top_rec") or {}
        wk = packet.get("last_7d_spending", 0)
        flagged = s.get("flagged_count", 0)
        bits = []
        if flagged >= 5:
            bits.append(f"Review queue has {flagged} items — clear the high-impact ones this week.")
        if top:
            bits.append(f"Top rec: {top.get('title','')} (~${top.get('annual_impact') or 0:,.0f}/yr).")
        bits.append(f"Last 7 days spent ${wk:,.0f} across {packet.get('last_7d_tx_count',0)} transactions.")
        answer = " ".join(bits) if bits else "Nothing urgent — keep imports current."
        bullets = []
        if top: bullets.append(f"Open Recommendations → start with: {top.get('title','')}")
        if flagged >= 5: bullets.append(f"Open Review → {flagged} flagged item(s)")
        if s.get("days_since_cash_advance") is not None and s["days_since_cash_advance"] < 30:
            bullets.append(f"Avoid new cash advances — last one was {s['days_since_cash_advance']} day(s) ago.")
    elif skill == "category_drilldown":
        cat = packet.get("top_category")
        if not cat:
            answer, bullets = "No clear top category yet — import more data.", []
        else:
            answer = (f"{cat} is your biggest controllable category: "
                      f"${packet.get('total_90d',0):,.0f} over 90 days "
                      f"(~${packet.get('monthly_avg',0):,.0f}/mo).")
            bullets = [f"{m['merchant']}: ${m['total_90d']:,.0f} ({m['tx_count']} tx)"
                       for m in (packet.get('top_merchants') or [])[:4]]
    elif skill == "merchant_explain":
        m = packet.get("merchant_top")
        if not m:
            answer, bullets = "No recurring merchant detected yet.", []
        else:
            answer = (f"{m['merchant']} ({m['category']}): "
                      f"~${m['avg_amount']:,.0f}/mo across {m['months_seen']} months "
                      f"(annualized ~${m['est_annual']:,.0f}).")
            bullets = [
                f"Range: ${m['min_amount']:,.0f} – ${m['max_amount']:,.0f} per charge.",
                f"Total seen: ${m['total_paid']:,.0f} across {m['tx_count']} transaction(s).",
            ]
    # ── Pass 22 deterministic answers for the planning loop ─────────
    elif skill == "month_plan":
        plan = packet.get("plan") or {}
        anchor = packet.get("anchor_month") or ""
        if not plan.get("mode"):
            answer = (f"No plan saved for {anchor}. Open Month Plan, "
                      f"pick a mode, and click Save to lock targets.")
            bullets = ["Modes: normal, tight, reset, aggressive_save, "
                       "sub_cleanup, debt_recovery, stabilize.",
                       "Targets are derived from your last 3 months of data."]
        else:
            answer = (f"{plan['mode']} plan for {plan.get('month')}: "
                      f"income ${plan.get('income_target',0):,.0f}, "
                      f"spending ${plan.get('spending_target',0):,.0f}, "
                      f"savings ${plan.get('savings_target',0):,.0f}.")
            tgts = plan.get("category_targets") or []
            bullets = [
                (f"{t.get('category')}: ${t.get('target_amount',0):,.0f} "
                 f"({t.get('difficulty','')})")
                for t in tgts[:4]
            ]
    elif skill == "forecast_risk":
        fc = packet.get("forecast") or {}
        risk = (fc.get("risk_level") or "").replace("_", " ")
        if not fc or not risk:
            answer, bullets = "Forecast unavailable — import statements first.", []
        else:
            answer = (
                f"Risk: {risk}. Day {fc.get('days_elapsed',0)} of "
                f"{fc.get('days_in_month',0)}. Projected net "
                f"${fc.get('projected_net',0):,.0f} "
                f"(rate {fc.get('projected_savings_rate',0)*100:.0f}%)."
            )
            bullets = [
                f"MTD spending ${fc.get('mtd_spending',0):,.0f}, "
                f"income ${fc.get('mtd_income',0):,.0f}",
                f"Upcoming bills ~${fc.get('upcoming_bills_total',0):,.0f}",
            ]
            for d in (fc.get("drivers") or [])[:2]:
                bullets.append(f"Driver: {d.get('category')} "
                               f"${d.get('total',0):,.0f} MTD")
    elif skill == "safe_to_spend":
        fc = packet.get("forecast") or {}
        sts = fc.get("safe_to_spend")
        if sts is None:
            answer = ("No saved plan yet — Ledger needs a spending "
                      "target to compute safe-to-spend. Open Month Plan "
                      "and save a plan.")
            bullets = []
        else:
            answer = (f"Safe to spend: ${sts:,.0f} for the rest of "
                      f"{fc.get('month','')}. "
                      f"Day {fc.get('days_elapsed',0)} of "
                      f"{fc.get('days_in_month',0)}.")
            bullets = [
                "Basis: spending_target − (MTD spending + upcoming bills).",
                f"MTD spending: ${fc.get('mtd_spending',0):,.0f}",
                f"Upcoming bills: ${fc.get('upcoming_bills_total',0):,.0f}",
            ]
    elif skill == "bills_due":
        bills = packet.get("bills") or {}
        items = (bills.get("items") or [])[:5]
        if not items:
            answer, bullets = "No recurring bills detected yet.", []
        else:
            answer = (
                f"{bills.get('count',0)} commitment(s) tracked. "
                f"~${bills.get('monthly_estimate',0):,.0f}/month locked in. "
                f"Top items below."
            )
            bullets = [
                (f"{i.get('merchant')}: ${i.get('est_amount',0):,.0f} "
                 f"({i.get('frequency','')}) · "
                 f"next ~{i.get('expected_next') or '—'}")
                for i in items[:4]
            ]
    elif skill == "category_targets":
        tgts = packet.get("category_targets") or []
        if not tgts:
            answer = ("No saved category targets — open Month Plan and "
                      "save a plan to enable this view.")
            bullets = []
        else:
            tight = [t for t in tgts
                     if t.get("difficulty") in ("tight", "normal")]
            tight.sort(key=lambda t: -float(t.get("target_amount") or 0))
            top = tight[0] if tight else tgts[0]
            answer = (
                f"Focus: {top.get('category')} at "
                f"${top.get('target_amount',0):,.0f} "
                f"({top.get('difficulty','')}). "
                f"Driving the biggest controllable cut."
            )
            bullets = [
                (f"{t.get('category')}: "
                 f"${t.get('target_amount',0):,.0f} ({t.get('difficulty','')})")
                for t in tgts[:4]
            ]
    elif skill == "goal_progress":
        goals = packet.get("goals") or []
        if not goals:
            answer = ("No active goals. Add one in Month Plan → Goals "
                      "(cash buffer, net worth, debt reduction).")
            bullets = []
        else:
            top = goals[0]
            pct = (top.get("progress_pct") or 0) * 100
            answer = (
                f"{top.get('name')}: ${top.get('current_amount',0):,.0f} "
                f"of ${top.get('target_amount',0):,.0f} ({pct:.0f}%). "
                f"Next milestone ${top.get('next_milestone',0):,.0f}."
            )
            bullets = [
                (f"{g.get('name')}: {(g.get('progress_pct') or 0)*100:.0f}% "
                 f"(${g.get('current_amount',0):,.0f} / "
                 f"${g.get('target_amount',0):,.0f})")
                for g in goals[:4]
            ]
    elif skill == "next_payday_focus":
        fc = packet.get("forecast") or {}
        last_payday = packet.get("last_payday_guess")
        sts = fc.get("safe_to_spend")
        if sts is not None:
            answer = (
                f"Until next payday: keep discretionary spend below "
                f"~${sts:,.0f}. Last payroll-like deposit: "
                f"{last_payday or 'not detected'}."
            )
        else:
            answer = ("No saved plan — save one to compute "
                      "until-next-payday headroom. Last payroll-like "
                      f"deposit: {last_payday or 'not detected'}.")
        bullets = [
            f"Days remaining in month: {fc.get('days_remaining', '—')}",
            f"Projected net: ${fc.get('projected_net',0):,.0f}",
        ]
        bills = (packet.get("bills") or {}).get("items") or []
        upcoming = [b for b in bills
                    if (b.get("expected_next") or "")
                    and b.get("included_in_forecast")]
        for b in upcoming[:2]:
            bullets.append(
                f"Upcoming: {b.get('merchant')} "
                f"~${b.get('est_amount',0):,.0f} ({b.get('expected_next')})"
            )
    elif skill == "reminder_suggestions":
        # Stitch concrete reminders from the planning evidence.
        fc = packet.get("forecast") or {}
        plan = packet.get("plan") or {}
        bills = packet.get("bills") or {}
        goals = packet.get("goals") or []
        risk = (fc.get("risk_level") or "").replace("_", " ")
        rs = []
        if risk in ("danger", "watch"):
            rs.append(
                f"Weekly: review forecast risk ({risk}). "
                f"Projected net ${fc.get('projected_net',0):,.0f}."
            )
        if not plan.get("mode"):
            rs.append("Open Month Plan and save a plan for this month.")
        else:
            rs.append(
                f"Mid-month: check safe-to-spend before the weekend "
                f"(plan mode: {plan.get('mode')})."
            )
        if bills.get("count"):
            rs.append(
                f"Audit {bills.get('count',0)} commitment(s) "
                f"(~${bills.get('monthly_estimate',0):,.0f}/mo)."
            )
        for g in goals[:2]:
            rs.append(
                f"Track goal '{g.get('name')}' "
                f"({(g.get('progress_pct') or 0)*100:.0f}%)."
            )
        rs.append("Monthly: snapshot net worth on Investments page.")
        rs = rs[:5]
        answer = (
            f"{len(rs)} suggested reminder(s) for OpenClaw to surface. "
            "All are read-only suggestions — Ledger never schedules "
            "them automatically."
        )
        bullets = rs
    else:
        answer, bullets = "No answer available.", []
    return {
        "skill":        skill,
        "answer":       _clamp_str(answer, 420),
        "bullets":      [_clamp_str(b, 120) for b in bullets][:4],
        "grounded_from": list(packet.keys()),
        **_ai_meta(ok=False, fallback=True),
    }


def ask_ledger(question: str, conn: sqlite3.Connection,
               skill_override: Optional[str] = None) -> dict:
    skill = skill_override or _route_question(question)
    if skill is None:
        # Clean refusal — no AI call, no invented answer
        supported = [lbl for _, lbl in ASK_PRESETS]
        return {
            "skill":        "unsupported",
            "answer":       ("That question is outside what Ledger can answer from your "
                             "local data. Ledger only explains what's already in your "
                             "imported statements — no market data, no predictions, no "
                             "external lookups."),
            "bullets":      ["Try one of these supported questions:"] + [f"· {s}" for s in supported[:5]],
            "grounded_from": [],
            "refused":      True,
            **_ai_meta(ok=False, fallback=True),
        }
    packet = _build_ask_packet(skill, conn)
    fid = "ask_ledger"

    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_ask(skill, packet)
        out["error"] = reason
        return out

    user_prompt = (
        f"User question: {question!r}\n"
        f"Skill: {skill}\n"
        f"Packet:\n{json.dumps(packet, ensure_ascii=False, default=float)}\n\n"
        'Return ONLY JSON — no prose, no markdown. '
        '{"answer":"<=420 chars","bullets":["2-4 short lines, each <=120 chars"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _ASK_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_ASK,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_ask(skill, packet)
        out["error"] = diag.get("reason") or (
            f"AI response unparseable ({perr}) — fallback used." if perr
            else "AI call failed — using deterministic answer."
        )
        out["diagnostic"] = diag
        return out
    answer = _clamp_str(str(parsed.get("answer", "")), 420)
    raw_bullets = parsed.get("bullets") or []
    bullets: list[str] = []
    for b in raw_bullets[:4]:
        if isinstance(b, str):
            bullets.append(_clamp_str(b, 120))
        elif isinstance(b, dict):
            bullets.append(_clamp_str(b.get("text") or b.get("line") or "", 120))
    if not answer:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing 'answer'")
        out = _deterministic_ask(skill, packet)
        out["error"] = "AI response incomplete — using deterministic answer."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "skill":        skill,
        "answer":       answer,
        "bullets":      bullets,
        "grounded_from": list(packet.keys()),
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 6. Weekly Review — 5-minute money ritual ────────────────────────────

_WEEKLY_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Keep text concise.\n\n"
    "Write a brief weekly money check-in using ONLY the JSON packet. "
    "JSON keys: headline (<=90 chars), focus (<=200 chars), checklist "
    "(array of exactly 5 short items, each <=120 chars, each a concrete "
    "action or question grounded in the packet). Mature tone, no "
    "cheerleading, no emojis."
)


def _weekly_review_packet(conn: sqlite3.Connection) -> dict:
    from utils.analytics import compute_score, score_label
    from utils.insights import (
        compute_recommendations, monthly_aggregates, subscription_detective,
    )
    from utils.momentum import compute_streaks, mission_options

    streaks = compute_streaks(conn=conn)
    aggs = monthly_aggregates(conn=conn)
    latest = aggs[-1] if aggs else None
    prev   = aggs[-2] if len(aggs) >= 2 else None

    # Last 7 days
    row = conn.execute("""
        SELECT SUM(ABS(amount)) AS total, COUNT(*) AS cnt
        FROM transactions
        WHERE direction='debit' AND is_transfer=0
          AND category NOT IN ('Transfer','Transfer Out','Transfer In',
                               'Payment','Credit Card Payment','Cancelled',
                               'Housing / Mortgage','Fees / Interest')
          AND transaction_date >= date('now', '-7 days')
    """).fetchone()
    spending_7d = round(row["total"] or 0, 2) if row else 0
    tx_7d = int(row["cnt"] or 0) if row else 0

    recs = compute_recommendations(conn=conn)
    top_rec = recs[0] if recs else None

    det = subscription_detective(conn=conn)
    sub_candidates = det["candidates"]

    score = compute_score(conn=conn)
    missions = mission_options(conn=conn, limit=2)

    return {
        "score":               score["total"],
        "score_label":         score_label(score["total"]),
        "data_confidence":     score.get("data_confidence") or {},
        "latest":              latest,
        "prior":               prev,
        "spending_7d":         spending_7d,
        "tx_7d":               tx_7d,
        "flagged_count":       streaks.get("flagged_count", 0),
        "days_since_cash_advance": streaks.get("days_since_cash_advance"),
        "top_rec":             _top_recs_packet([top_rec], limit=1)[0] if top_rec else None,
        "sub_candidate_count": len(sub_candidates),
        "sub_monthly":         det["monthly_estimate"],
        "missions":            [{"title": m["title"],
                                 "next_action": m["next_action"],
                                 "difficulty": m["difficulty"]} for m in missions],
    }


def _deterministic_weekly(p: dict) -> dict:
    flagged = p.get("flagged_count", 0)
    sp7 = p.get("spending_7d", 0)
    tx7 = p.get("tx_7d", 0)
    top_rec = p.get("top_rec") or {}
    sub_n = p.get("sub_candidate_count", 0)
    sub_m = p.get("sub_monthly", 0)
    missions = p.get("missions") or []
    ca_days = p.get("days_since_cash_advance")

    headline = f"Last 7 days: ${sp7:,.0f} across {tx7} transactions"
    focus_bits = []
    if flagged >= 5:
        focus_bits.append(f"{flagged} items in the review queue — worth 5 minutes.")
    if top_rec:
        impact = top_rec.get("annual_impact") or 0
        focus_bits.append(
            f"Top opportunity: {top_rec.get('title','')}"
            + (f" (~${impact:,.0f}/yr)" if impact else "")
            + "."
        )
    if not focus_bits:
        focus_bits.append("Nothing urgent — keep imports current and monitor drift.")
    focus = " ".join(focus_bits)

    checklist: list[str] = []
    if flagged >= 3:
        checklist.append(f"Open Review — clear the {flagged} flagged item(s) (start with high-impact).")
    if sub_n > 0:
        checklist.append(f"Open Subscription Detective — {sub_n} candidate(s) flagged, "
                         f"~${sub_m:,.0f}/mo bill.")
    if top_rec:
        checklist.append(f"Act on top rec: {top_rec.get('title','')}")
    if missions:
        checklist.append(f"Consider mission: {missions[0]['title']} — {missions[0]['next_action']}")
    if ca_days is not None and ca_days < 30:
        checklist.append(f"Watch cash advances — last one was {ca_days} day(s) ago.")
    while len(checklist) < 5:
        checklist.append("Skim Recent Transactions for anything unexpected.")
    checklist = checklist[:5]

    return {
        "headline":    _clamp_str(headline, 90),
        "focus":       _clamp_str(focus, 200),
        "checklist":   [_clamp_str(x, 120) for x in checklist],
        "grounded_from": [f"flagged={flagged}", f"7d_spend=${sp7:,.0f}",
                          f"subs={sub_n}", f"missions={len(missions)}"],
        **_ai_meta(ok=False, fallback=True),
    }


def weekly_review(conn: sqlite3.Connection) -> dict:
    packet = _weekly_review_packet(conn)
    fid = "weekly_review"

    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_weekly(packet)
        out["error"] = reason
        return out

    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"headline":"<=90 chars","focus":"<=200 chars",'
        '"checklist":["exactly 5 short imperatives, each <=120 chars"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _WEEKLY_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_WEEKLY,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_weekly(packet)
        out["error"] = diag.get("reason") or (
            f"AI response unparseable ({perr}) — fallback used." if perr
            else "AI call failed — using deterministic summary."
        )
        out["diagnostic"] = diag
        return out
    headline = _clamp_str(str(parsed.get("headline", "")), 90)
    focus    = _clamp_str(str(parsed.get("focus", "")), 200)
    raw_list = parsed.get("checklist") or []
    checklist: list[str] = []
    for m in raw_list[:5]:
        if isinstance(m, str):
            checklist.append(_clamp_str(m, 120))
        elif isinstance(m, dict):
            checklist.append(_clamp_str(m.get("text") or m.get("item") or "", 120))
    if not headline or not focus or len(checklist) < 3:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing headline/focus/checklist (need >=3 items)")
        out = _deterministic_weekly(packet)
        out["error"] = "AI response incomplete — using deterministic summary."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "headline":    headline,
        "focus":       focus,
        "checklist":   checklist,
        "grounded_from": [f"flagged={packet['flagged_count']}",
                          f"7d_spend=${packet['spending_7d']:,.0f}",
                          f"subs={packet['sub_candidate_count']}"],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 7. Scenario explainer ─────────────────────────────────────────────────

_SCENARIO_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers — every figure must come from the packet. "
    "Keep text concise.\n\n"
    "Explain the result of a deterministic personal-finance 'what-if' "
    "simulation using ONLY the JSON packet. JSON keys: summary "
    "(<=280 chars), risks (array of 1–3 short lines, each <=120 chars), "
    "next_steps (array of 1–3 short imperatives, each <=120 chars). "
    "Plain tone, no fluff."
)


def _deterministic_scenario(sim: dict) -> dict:
    baseline  = sim.get("baseline") or {}
    projected = sim.get("projected") or {}
    impact    = sim.get("impact_breakdown") or []
    gap       = sim.get("required_extra_cut")

    if not baseline:
        return {
            "summary":    "Need imported data to simulate a scenario.",
            "risks":      [],
            "next_steps": ["Import at least one month of statements."],
            "grounded_from": [],
            **_ai_meta(ok=False, fallback=True),
        }

    delta = projected.get("delta_savings", 0)
    ann   = projected.get("delta_savings_annual", 0)
    new_sr = projected.get("savings_rate", 0)
    summary = (
        f"Baseline {baseline.get('month','?')}: spending ${baseline.get('spending',0):,.0f}, "
        f"savings rate {baseline.get('savings_rate',0):.0f}%. "
        f"This scenario saves ~${delta:,.0f}/mo "
        f"(~${ann:,.0f}/yr) → new savings rate {new_sr:.0f}%."
    )
    if gap:
        summary += f" To hit your target, still need ${gap:,.0f}/mo more in cuts."

    risks: list[str] = []
    for i in impact:
        pct_of_spend = (i["monthly_savings"] / max(baseline.get("spending", 1), 1)) * 100
        if pct_of_spend >= 30:
            risks.append(f"Cutting '{i['source']}' is ~{pct_of_spend:.0f}% of spending — check feasibility.")
    if not risks:
        risks = ["Assumes categories and subscriptions behave like the last 90 days."]

    next_steps = []
    for i in impact[:3]:
        next_steps.append(f"{i['source']} → ~${i['monthly_savings']:,.0f}/mo")
    if not next_steps:
        next_steps = ["Adjust the sliders and re-run the simulation."]

    return {
        "summary":    _clamp_str(summary, 280),
        "risks":      [_clamp_str(r, 120) for r in risks][:3],
        "next_steps": [_clamp_str(n, 120) for n in next_steps][:3],
        "grounded_from": [f"baseline={baseline.get('month','?')}",
                          f"delta=${delta:,.0f}/mo"],
        **_ai_meta(ok=False, fallback=True),
    }


def explain_scenario(sim: dict) -> dict:
    """Wrap a deterministic scenario result with AI narrative (fallback always safe)."""
    fid = "scenario_simulator"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_scenario(sim)
        out["error"] = reason
        return out
    user_prompt = (
        "Scenario result (authoritative — do not change numbers):\n"
        + json.dumps(sim, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"summary":"<=280 chars","risks":["1-3 short lines, each <=120 chars"],'
        '"next_steps":["1-3 short imperatives, each <=120 chars"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _SCENARIO_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_SCENARIO,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_scenario(sim)
        out["error"] = diag.get("reason") or (
            f"AI response unparseable ({perr}) — fallback used." if perr
            else "AI call failed — using deterministic summary."
        )
        out["diagnostic"] = diag
        return out
    summary = _clamp_str(str(parsed.get("summary", "")), 280)
    risks_raw = parsed.get("risks") or []
    next_raw  = parsed.get("next_steps") or []
    risks     = [_clamp_str(str(r), 120) for r in risks_raw if isinstance(r, (str, dict))][:3]
    next_steps = [_clamp_str(str(n), 120) for n in next_raw if isinstance(n, (str, dict))][:3]
    if not summary:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing 'summary'")
        out = _deterministic_scenario(sim)
        out["error"] = "AI response incomplete — using deterministic summary."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "summary":    summary,
        "risks":      risks,
        "next_steps": next_steps,
        "grounded_from": [f"baseline={(sim.get('baseline') or {}).get('month','?')}",
                          f"delta=${(sim.get('projected') or {}).get('delta_savings',0):,.0f}/mo"],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 8. Generic health check ──────────────────────────────────────────────

_HEALTH_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Keep text concise.\n\n"
    "You are a JSON test endpoint. JSON keys: ok (boolean), echo (string)."
)


def ai_health_check() -> dict:
    """One small live AI call that exercises the full call/parse path.

    Returns a sanitized diagnostic dict. Useful for Settings to confirm that
    a) the configured provider/model is reachable,
    b) the parser handles <think> wrappers,
    c) the validator handles a tiny schema.
    Never raises. No transactions or PII enter the prompt."""
    fid = "ai_health_check"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        return {
            "ok":        False,
            "fallback":  True,
            "reason":    reason or "AI not ready",
            "diagnostic": {"attempted": False, "latency_ms": 0,
                           "response_chars": 0, "reason": reason},
        }

    user_prompt = (
        'Return ONLY this JSON object: {"ok": true, "echo": "ledger-health-ok"}. '
        "No prose, no markdown."
    )
    parsed, perr, diag = _call_and_parse(
        _HEALTH_SYSTEM, user_prompt, feature_id=fid,
        timeout=45.0, max_tokens=_BUDGET_HEALTH_CHECK,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        reason_msg = diag.get("reason") or (f"parse failed: {perr}" if perr else "AI call failed")
        return {"ok": False, "fallback": True, "reason": reason_msg,
                "diagnostic": diag}

    ok_flag = bool(parsed.get("ok"))
    echo = str(parsed.get("echo", ""))
    if not ok_flag or "ledger-health" not in echo.lower():
        verr = "schema mismatch (ok or echo missing/wrong)"
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error=verr)
        return {"ok": False, "fallback": True, "reason": verr,
                "diagnostic": diag}

    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {"ok": True, "fallback": False, "reason": "",
            "diagnostic": diag, "echo": echo}


# ── 9. Reduce workspace summary ──────────────────────────────────────────

_REDUCE_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers — every figure must come from the packet. "
    "Keep text concise.\n\n"
    "You help the user reduce spending. Given a packet of subscription "
    "candidates, controllable categories, and recurring merchants, write a "
    "short workspace summary. JSON keys: headline (<=90 chars), "
    "first_move (<=200 chars, the single most useful action), "
    "candidates (array of 2–4 short strings, each <=120 chars, ranked "
    "cancellation candidates with the savings figure inline), "
    "categories (array of 1–3 short strings, each <=120 chars, controllable "
    "categories worth reducing). Mature tone, no shame, no hype."
)


def _deterministic_reduce(packet: dict) -> dict:
    """Fallback for the Reduce workspace summary. Same shape as the AI path."""
    subs = packet.get("subscription_candidates") or []
    ctrl = packet.get("controllable_categories") or []
    monthly_est = float(packet.get("monthly_estimate") or 0)
    annual_est  = float(packet.get("annual_total") or 0)
    candidate_annual = float(packet.get("candidate_annual_total") or 0)

    if not subs and not ctrl:
        return {
            "headline":   "Not enough data to suggest cuts yet",
            "first_move": "Import a few months of statements so Ledger can spot recurring "
                          "charges and controllable categories.",
            "candidates": [],
            "categories": [],
            "grounded_from": [],
            **_ai_meta(ok=False, fallback=True),
        }

    if subs:
        first = subs[0]
        first_move = (
            f"Review {first.get('merchant', 'top candidate')} first — "
            f"~${first.get('annual', 0):,.0f}/yr"
        )
        if first.get("flags"):
            first_move += f" ({', '.join(first['flags'])})"
        first_move += "."
    elif ctrl:
        c = ctrl[0]
        first_move = (
            f"Trim {c.get('category', 'top category')} — averaging "
            f"~${c.get('monthly_avg', 0):,.0f}/mo over the last 90 days. "
            f"A 20% cut returns ~${(c.get('monthly_avg', 0) * 0.20 * 12):,.0f}/yr."
        )
    else:
        first_move = "Pick the largest controllable category and aim for a 20% reduction."

    headline = (
        f"~${candidate_annual:,.0f}/yr in cancellation candidates · "
        f"~${monthly_est:,.0f}/mo recurring (~${annual_est:,.0f}/yr total)"
        if candidate_annual > 0
        else f"~${monthly_est:,.0f}/mo in recurring charges (~${annual_est:,.0f}/yr)"
    )

    cand_lines = []
    for s in subs[:4]:
        flags_str = (" · " + ", ".join(s.get("flags") or [])) if s.get("flags") else ""
        cand_lines.append(
            _clamp_str(
                f"{s.get('merchant', '?')} — ~${s.get('annual', 0):,.0f}/yr{flags_str}",
                120,
            )
        )

    cat_lines = []
    for c in ctrl[:3]:
        avg = float(c.get("monthly_avg") or 0)
        cat_lines.append(
            _clamp_str(
                f"{c.get('category', '?')} — ~${avg:,.0f}/mo · 20% cut = ~${avg*0.20*12:,.0f}/yr",
                120,
            )
        )

    return {
        "headline":     _clamp_str(headline, 90),
        "first_move":   _clamp_str(first_move, 200),
        "candidates":   cand_lines,
        "categories":   cat_lines,
        "grounded_from": [
            f"subs={len(subs)}",
            f"controllable={len(ctrl)}",
            f"annual=${annual_est:,.0f}",
        ],
        **_ai_meta(ok=False, fallback=True),
    }


def reduce_workspace_summary(packet: dict) -> dict:
    """Wrap a deterministic reduce-workspace packet with AI narrative.

    Packet shape (built by pages/11_Reduce.py):
        subscription_candidates: list[{merchant, annual, monthly, months_seen, flags}]
        controllable_categories: list[{category, monthly_avg, total_90d}]
        monthly_estimate / annual_total / candidate_annual_total: floats
    """
    fid = "reduce_workspace"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_reduce(packet)
        out["error"] = reason
        return out

    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"headline":"<=90 chars","first_move":"<=200 chars",'
        '"candidates":["2-4 lines, each <=120 chars"],'
        '"categories":["1-3 lines, each <=120 chars"]}'
    )
    parsed, perr, diag = _call_and_parse(
        _REDUCE_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_REDUCE,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag, parse_error=perr)
        out = _deterministic_reduce(packet)
        out["error"] = diag.get("reason") or (
            f"AI response unparseable ({perr}) — fallback used." if perr
            else "AI call failed — using deterministic summary."
        )
        out["diagnostic"] = diag
        return out

    headline   = _clamp_str(str(parsed.get("headline", "")), 90)
    first_move = _clamp_str(str(parsed.get("first_move", "")), 200)
    cands_raw  = parsed.get("candidates") or []
    cats_raw   = parsed.get("categories") or []
    candidates = [_clamp_str(str(c), 120) for c in cands_raw if isinstance(c, (str, dict))][:4]
    categories = [_clamp_str(str(c), 120) for c in cats_raw  if isinstance(c, (str, dict))][:3]

    if not headline or not first_move:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing headline/first_move")
        out = _deterministic_reduce(packet)
        out["error"] = "AI response incomplete — using deterministic summary."
        out["diagnostic"] = diag
        return out

    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "headline":     headline,
        "first_move":   first_move,
        "candidates":   candidates,
        "categories":   categories,
        "grounded_from": [
            f"subs={len(packet.get('subscription_candidates') or [])}",
            f"controllable={len(packet.get('controllable_categories') or [])}",
            f"annual=${packet.get('annual_total', 0):,.0f}",
        ],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 10. Money Progress level-up coaching ─────────────────────────────────

_PROGRESS_COACH_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Keep text concise.\n\n"
    "You coach the user on financial momentum. Given an XP / level / "
    "momentum scorecard, write supportive, mature, NON-shameful copy. "
    "JSON keys: explanation (<=200 chars, why the level/momentum is where "
    "it is), next_moves (array of 2–3 short imperatives, each <=120 "
    "chars, the highest-leverage XP moves), recovery_note (<=160 chars, "
    "shown ONLY when momentum_label is 'Recovery mode' or 'Pressure' — "
    "encouragement without shame, otherwise empty string)."
)


def _deterministic_progress_coach(mp: dict) -> dict:
    """Fallback coach copy. Same shape as the AI path."""
    level    = int(mp.get("level") or 1)
    momentum = mp.get("momentum_label") or "Steady"
    breakdown = mp.get("breakdown") or []
    risks     = mp.get("risks") or []

    # Pick top 3 dimensions where ratio xp/cap is lowest — biggest XP headroom.
    headroom = sorted(
        [(b, b["cap"] - b["xp"]) for b in breakdown if b["cap"] > 0],
        key=lambda t: -t[1],
    )[:3]
    moves = []
    for b, gap in headroom:
        if gap < 1:
            continue
        if b["key"] == "review_hygiene":
            moves.append("Clear flagged Review items — each one is XP and cleaner totals.")
        elif b["key"] == "savings_rate":
            moves.append("Lift savings rate above 25% next month — biggest single bucket.")
        elif b["key"] == "subscription_hold":
            moves.append("Review the Reduce workspace — trim subscriptions below 5% of income.")
        elif b["key"] == "no_cash_advance":
            moves.append("Avoid cash advances for 90+ days — restores full streak XP.")
        elif b["key"] == "controllable_cap":
            moves.append("Hold controllable spend below last month's pace — eases pressure.")
        elif b["key"] == "data_completeness":
            moves.append("Import any missing-month statements to close coverage gaps.")
        elif b["key"] == "positive_streak":
            moves.append("Close the current month positive — extends the streak.")
        else:
            moves.append(f"Improve {b['label']}: {b['note']}")

    if momentum == "Recovery mode":
        explanation = (
            f"Level {level} · Recovery mode. The latest month closed negative, but "
            "XP from positive dimensions still counts — small wins matter."
        )
        recovery = "One positive month closes Recovery mode. Pick a single bucket above and act on it."
    elif momentum == "Pressure":
        explanation = (
            f"Level {level} · Pressure. Several dimensions are under-earning — pick "
            "the biggest gap and act on it this week."
        )
        recovery = "Pressure usually clears in one or two intentional weeks."
    else:
        explanation = (
            f"Level {level} · {momentum}. {mp.get('progress_pct', 0)}% to next level. "
            "Wins this period drove the score; keep the winning habits."
        )
        recovery = ""

    return {
        "explanation":   _clamp_str(explanation, 200),
        "next_moves":    [_clamp_str(m, 120) for m in moves[:3]],
        "recovery_note": _clamp_str(recovery, 160),
        "grounded_from": [f"level={level}", f"momentum={momentum}",
                          f"buckets={len(breakdown)}"],
        **_ai_meta(ok=False, fallback=True),
    }


def coach_money_progress(mp: dict) -> dict:
    """AI coaching wrapper around `money_progress(conn)` output.

    AI never mutates XP — it only paraphrases the deterministic scorecard
    and surfaces 2–3 highest-leverage next moves with mature, non-shameful copy.
    Always returns a stable shape, fallback on failure.
    """
    fid = "progress_coach"
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False, fallback=True, reason=reason)
        out = _deterministic_progress_coach(mp)
        out["error"] = reason
        return out

    # Slim the packet — coach doesn't need bucket notes, just the values.
    slim = {
        "level":          mp.get("level"),
        "xp":             mp.get("xp"),
        "xp_to_next":     mp.get("xp_to_next"),
        "progress_pct":   mp.get("progress_pct"),
        "momentum_label": mp.get("momentum_label"),
        "momentum_score": mp.get("momentum_score"),
        "wins":           [w["label"] for w in (mp.get("wins") or [])][:4],
        "risks":          [r["label"] for r in (mp.get("risks") or [])][:4],
        "buckets":        [
            {"key": b["key"], "label": b["label"], "xp": b["xp"], "cap": b["cap"]}
            for b in (mp.get("breakdown") or [])
        ],
    }

    user_prompt = (
        "Scorecard (authoritative — do not change numbers):\n"
        + json.dumps(slim, ensure_ascii=False, default=float)
        + '\n\nReturn ONLY JSON — no prose, no markdown. '
        '{"explanation":"<=200 chars","next_moves":["2-3 short imperatives, each <=120 chars"],'
        '"recovery_note":"<=160 chars or empty"}'
    )
    parsed, perr, diag = _call_and_parse(
        _PROGRESS_COACH_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_PROGRESS_COACH,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag, parse_error=perr)
        out = _deterministic_progress_coach(mp)
        out["error"] = diag.get("reason") or (
            f"AI response unparseable ({perr}) — fallback used." if perr
            else "AI call failed — using deterministic coach."
        )
        out["diagnostic"] = diag
        return out

    explanation = _clamp_str(str(parsed.get("explanation", "")), 200)
    moves_raw   = parsed.get("next_moves") or []
    moves       = [_clamp_str(str(m), 120) for m in moves_raw if isinstance(m, (str, dict))][:3]
    recovery    = _clamp_str(str(parsed.get("recovery_note", "")), 160)

    if not explanation or not moves:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing explanation/next_moves")
        out = _deterministic_progress_coach(mp)
        out["error"] = "AI response incomplete — using deterministic coach."
        out["diagnostic"] = diag
        return out

    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "explanation":   explanation,
        "next_moves":    moves,
        "recovery_note": recovery,
        "grounded_from": [
            f"level={mp.get('level')}",
            f"momentum={mp.get('momentum_label')}",
            f"buckets={len(mp.get('breakdown') or [])}",
        ],
        **_ai_meta(ok=True, diagnostic=diag),
    }


# ── 11. AI features status (for Settings page) ────────────────────────────

def ai_features_status() -> list[dict]:
    """A map of every AI-powered surface in Ledger, for Settings to render."""
    ready, reason = ai_is_ready()
    ai = get_ai_settings()
    base = {"provider": ai.get("provider"), "model": ai.get("model"),
            "ready": ready, "reason": reason}
    return [
        {
            "id":          "dashboard_copilot",
            "name":        "Dashboard Copilot",
            "location":    "Dashboard · top of page",
            "purpose":     "Plain-English explanation of score, month state, and top 3 moves.",
            "fallback":    "Deterministic summary built from dimensions + recommendations.",
            **base,
        },
        {
            "id":          "recommendation_explainer",
            "name":        "Recommendation Explainer",
            "location":    "Recommendations · per-card",
            "purpose":     "Explains why each rec matters and distinguishes money vs cleanup.",
            "fallback":    "Uses rec drivers + confidence/controllability/urgency directly.",
            **base,
        },
        {
            "id":          "review_triage",
            "name":        "Review Triage Summary",
            "location":    "Review · top of page when queue is non-empty",
            "purpose":     "Calls out high-impact items to clear first.",
            "fallback":    "Deterministic grouping by reason type.",
            **base,
        },
        {
            "id":          "mission_framing",
            "name":        "This Month's Mission",
            "location":    "Dashboard · momentum card",
            "purpose":     "One-line framing of the current month's chosen mission.",
            "fallback":    "Uses the mission's built-in description.",
            **base,
        },
        {
            "id":          "ask_ledger",
            "name":        "Ask Ledger",
            "location":    "Dashboard · compact Q&A panel",
            "purpose":     "Five preset grounded questions routed to evidence skills.",
            "fallback":    "Deterministic answer from the skill's evidence packet.",
            **base,
        },
        {
            "id":          "ai_categorizer",
            "name":        "AI Categorization",
            "location":    "Review · Suggest category (per-row + bulk)",
            "purpose":     "Suggests a category for uncategorized / low-confidence rows.",
            "fallback":    "Keyword + learned rules. Nothing auto-applied.",
            **base,
        },
        {
            "id":          "weekly_review",
            "name":        "Weekly Review",
            "location":    "Dashboard · 5-minute money ritual",
            "purpose":     "Summarizes last 7 days + flags what to act on this week.",
            "fallback":    "Deterministic checklist from flagged count, top rec, and subscription candidates.",
            **base,
        },
        {
            "id":          "mission_engine",
            "name":        "Mission Engine v2",
            "location":    "Dashboard · Mission Options",
            "purpose":     "Paraphrases each of 2–3 mission options with a one-line framing.",
            "fallback":    "Deterministic missions with built-in descriptions + next actions.",
            **base,
        },
        {
            "id":          "scenario_simulator",
            "name":        "Savings Scenario Simulator",
            "location":    "Spending · What-if section",
            "purpose":     "Explains a deterministic 'cut X by Y%' simulation — risks + next steps.",
            "fallback":    "Deterministic summary from impact breakdown + gap vs target.",
            **base,
        },
        {
            "id":          "subscription_detective",
            "name":        "Subscription Detective",
            "location":    "Dashboard · Subscription Detective card",
            "purpose":     "Ranks subscriptions with flags (stale / price increase / duplicate).",
            "fallback":    "Purely deterministic — the detective runs without AI. AI only paraphrases the audit summary.",
            **base,
        },
        {
            "id":          "explain_month_plan",
            "name":        "Month Plan Coach",
            "location":    "Month Plan · Plan tab",
            "purpose":     "Narrates the saved plan: targets, top moves, risk note.",
            "fallback":    "Deterministic mode summary + tight-category list.",
            **base,
        },
        {
            "id":          "explain_forecast",
            "name":        "Forecast Coach",
            "location":    "Month Plan · Forecast tab",
            "purpose":     "Explains forecast risk, what to watch this week, one conservative next action.",
            "fallback":    "Deterministic recap from MTD + projected + safe-to-spend.",
            **base,
        },
        {
            "id":          "coach_goals",
            "name":        "Goal Progress Coach",
            "location":    "Month Plan · Goals tab",
            "purpose":     "Summarizes progress, next milestone, one suggested action.",
            "fallback":    "Deterministic per-goal % + tiered next-milestone math.",
            **base,
        },
    ]


# ── 11. Pass 22 — Plan / Forecast / Goals coaches ───────────────────────
#
# Three deterministic-first AI wrappers tied to the Month Plan page.
# Each follows the established pattern:
#   1. Build a small evidence packet from utils.planner output.
#   2. Render a deterministic copy that ALWAYS works.
#   3. If MiniMax is configured, overlay a one-call AI narrative.
#   4. On AI failure, fall back to the deterministic copy + diagnostic.
#
# No DB writes. No invented numbers. Cached by the page via
# utils/ai_cache.evidence_hash + get_or_compute.

_PLAN_COACH_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Stay under length budgets. Mature, calm "
    "tone. No professional financial advisor voice.\n\n"
    "JSON keys: headline (<=90 chars), summary (<=300 chars), "
    "actions (array of exactly 3 short imperatives, each <=120 chars, "
    "each grounded in the packet), risk_note (<=160 chars)."
)

_FORECAST_COACH_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Mature, calm tone.\n\n"
    "JSON keys: risk_explanation (<=240 chars), what_matters_most "
    "(<=200 chars), watch_this_week (<=200 chars), next_action "
    "(<=160 chars, ONE conservative concrete step)."
)

_GOAL_COACH_SYSTEM = (
    "Output ONLY a valid JSON object. No markdown. No code fences. "
    "No reasoning. No <think>. Use only the provided Ledger evidence. "
    "Do not invent numbers. Mature, supportive, no fluff.\n\n"
    "JSON keys: progress_summary (<=240 chars), next_milestone "
    "(<=160 chars), suggested_action (<=160 chars), caution "
    "(<=160 chars; empty string if data is sufficient)."
)


def _plan_evidence_packet(plan: dict, forecast: Optional[dict] = None) -> dict:
    """Compact, JSON-safe evidence for explain_month_plan."""
    if not plan:
        return {"saved": False, "month": None}
    return {
        "saved":            True,
        "month":            plan.get("month"),
        "mode":             plan.get("mode"),
        "income_target":    float(plan.get("income_target")   or 0),
        "spending_target":  float(plan.get("spending_target") or 0),
        "savings_target":   float(plan.get("savings_target")  or 0),
        "category_targets": [
            {
                "category":      t.get("category"),
                "target_amount": float(t.get("target_amount") or 0),
                "difficulty":    t.get("difficulty"),
                "basis":         t.get("basis"),
            }
            for t in (plan.get("category_targets") or [])[:8]
        ],
        "win_condition":    plan.get("notes") or "",
        "forecast_risk":    (forecast or {}).get("risk_level"),
        "projected_net":    (forecast or {}).get("projected_net"),
    }


def _deterministic_plan_coach(packet: dict) -> dict:
    if not packet.get("saved"):
        return {
            "headline":   "No plan saved this month.",
            "summary":    ("Pick a mode in Month Plan and click Save to "
                           "lock targets for the month."),
            "actions":    [
                "Open Month Plan and pick a mode.",
                "Review the generated category targets.",
                "Click Save plan to commit.",
            ],
            "risk_note":  "Without a saved plan, safe-to-spend is unavailable.",
            **_ai_meta(ok=False, fallback=True),
        }
    mode = packet.get("mode") or "normal"
    sav = float(packet.get("savings_target") or 0)
    inc = float(packet.get("income_target")  or 0)
    rate = (sav / inc * 100) if inc > 0 else 0
    targets = packet.get("category_targets") or []
    top = sorted(targets, key=lambda t: -float(t.get("target_amount") or 0))
    actions: list[str] = []
    if top:
        actions.append(
            f"Hold {top[0].get('category')} at "
            f"${top[0].get('target_amount',0):,.0f}."
        )
    tight = [t for t in targets if t.get("difficulty") == "tight"]
    if tight:
        cats = ", ".join(t.get("category", "") for t in tight[:3])
        actions.append(f"Tight categories ({len(tight)}): {cats}.")
    actions.append(
        f"Aim for ${sav:,.0f} saved this month ({rate:.0f}% of income)."
    )
    return {
        "headline":  f"{mode.replace('_',' ').title()} plan locked in.",
        "summary":   (
            f"Plan for {packet.get('month')}: target income "
            f"${inc:,.0f}, spending "
            f"${float(packet.get('spending_target') or 0):,.0f}, "
            f"savings ${sav:,.0f} ({rate:.0f}%)."
        ),
        "actions":   actions[:3],
        "risk_note": (
            f"Forecast risk: "
            f"{(packet.get('forecast_risk') or 'unknown').replace('_',' ')}. "
            "Re-check mid-month."
        ),
        **_ai_meta(ok=False, fallback=True),
    }


def explain_month_plan(plan: dict, forecast: Optional[dict] = None) -> dict:
    """AI-narrated plan summary with deterministic fallback."""
    fid = "explain_month_plan"
    packet = _plan_evidence_packet(plan, forecast)
    ready, reason = ai_is_ready()
    if not ready or not packet.get("saved"):
        _record_call_status(fid, attempted=False, ok=False,
                            fallback=True,
                            reason=reason or "no plan saved")
        out = _deterministic_plan_coach(packet)
        if reason:
            out["error"] = reason
        return out
    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + "\n\nReturn ONLY JSON. {\"headline\":\"<=90\","
        "\"summary\":\"<=300\",\"actions\":"
        "[\"3 lines, each <=120\"],\"risk_note\":\"<=160\"}"
    )
    parsed, perr, diag = _call_and_parse(
        _PLAN_COACH_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_REDUCE,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_plan_coach(packet)
        out["error"] = diag.get("reason") or "AI failed — fallback used."
        out["diagnostic"] = diag
        return out
    headline  = _clamp_str(str(parsed.get("headline","")),  90)
    summary   = _clamp_str(str(parsed.get("summary","")),   300)
    risk_note = _clamp_str(str(parsed.get("risk_note","")), 160)
    actions_raw = parsed.get("actions") or []
    actions = [_clamp_str(str(a), 120) for a in actions_raw[:3]
               if isinstance(a, (str, dict))]
    if not headline or not summary:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing fields")
        out = _deterministic_plan_coach(packet)
        out["error"] = "AI response incomplete — fallback used."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "headline":  headline,
        "summary":   summary,
        "actions":   actions,
        "risk_note": risk_note,
        **_ai_meta(ok=True, diagnostic=diag),
    }


def _deterministic_forecast_coach(fc: dict, plan: Optional[dict]) -> dict:
    risk = (fc.get("risk_level") or "").replace("_", " ")
    proj = float(fc.get("projected_net") or 0)
    rate = float(fc.get("projected_savings_rate") or 0)
    bills_total = float(fc.get("upcoming_bills_total") or 0)
    sts = fc.get("safe_to_spend")
    risk_explanation = (
        f"Forecast risk: {risk or 'unknown'}. Projected net "
        f"${proj:,.0f} ({rate*100:.0f}% rate). Upcoming bills "
        f"~${bills_total:,.0f} not yet hit."
    )
    drivers = fc.get("drivers") or []
    if drivers:
        what_matters = (
            f"Top MTD driver: {drivers[0].get('category')} "
            f"${drivers[0].get('total',0):,.0f}. "
            "Containing this category has the biggest impact."
        )
    else:
        what_matters = ("No category yet dominates spending — "
                        "watch where the next $500 goes.")
    if sts is not None:
        watch = (f"Hold weekly discretionary spend below "
                 f"~${sts/4:,.0f} to keep safe-to-spend positive.")
        next_action = (f"Cap discretionary spend at "
                       f"~${sts:,.0f} for the rest of the month.")
    elif plan and plan.get("spending_target"):
        watch = "Save a plan to enable safe-to-spend math."
        next_action = "Open Month Plan and confirm targets."
    else:
        watch = "Import latest statements to refresh MTD numbers."
        next_action = "Run the Import page if data is more than a week old."
    return {
        "risk_explanation":   risk_explanation,
        "what_matters_most":  what_matters,
        "watch_this_week":    watch,
        "next_action":        next_action,
        **_ai_meta(ok=False, fallback=True),
    }


def explain_forecast(fc: dict, plan: Optional[dict] = None) -> dict:
    """AI narration of the forecast with a deterministic fallback."""
    fid = "explain_forecast"
    if not fc:
        out = _deterministic_forecast_coach({}, plan)
        out["error"] = "No forecast data."
        return out
    ready, reason = ai_is_ready()
    if not ready:
        _record_call_status(fid, attempted=False, ok=False,
                            fallback=True, reason=reason)
        out = _deterministic_forecast_coach(fc, plan)
        out["error"] = reason
        return out
    packet = {
        "month":              fc.get("month"),
        "anchor_date":        fc.get("anchor_date"),
        "days_elapsed":       fc.get("days_elapsed"),
        "days_in_month":      fc.get("days_in_month"),
        "mtd_income":         fc.get("mtd_income"),
        "mtd_spending":       fc.get("mtd_spending"),
        "projected_income":   fc.get("projected_income"),
        "projected_spending": fc.get("projected_spending"),
        "projected_net":      fc.get("projected_net"),
        "projected_savings_rate": fc.get("projected_savings_rate"),
        "upcoming_bills_total":   fc.get("upcoming_bills_total"),
        "risk_level":         fc.get("risk_level"),
        "drivers":            (fc.get("drivers") or [])[:3],
        "safe_to_spend":      fc.get("safe_to_spend"),
        "plan_savings_target": (plan or {}).get("savings_target"),
        "plan_mode":          (plan or {}).get("mode"),
    }
    user_prompt = (
        "Packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + "\n\nReturn ONLY JSON. {\"risk_explanation\":\"<=240\","
        "\"what_matters_most\":\"<=200\",\"watch_this_week\":\"<=200\","
        "\"next_action\":\"<=160\"}"
    )
    parsed, perr, diag = _call_and_parse(
        _FORECAST_COACH_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_REDUCE,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_forecast_coach(fc, plan)
        out["error"] = diag.get("reason") or "AI failed — fallback used."
        out["diagnostic"] = diag
        return out
    re_  = _clamp_str(str(parsed.get("risk_explanation","")),  240)
    wm   = _clamp_str(str(parsed.get("what_matters_most","")), 200)
    wt   = _clamp_str(str(parsed.get("watch_this_week","")),   200)
    na   = _clamp_str(str(parsed.get("next_action","")),       160)
    if not (re_ and wm and na):
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing fields")
        out = _deterministic_forecast_coach(fc, plan)
        out["error"] = "AI response incomplete — fallback used."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "risk_explanation":  re_,
        "what_matters_most": wm,
        "watch_this_week":   wt,
        "next_action":       na,
        **_ai_meta(ok=True, diagnostic=diag),
    }


def _deterministic_goal_coach(progressed: list[dict]) -> dict:
    if not progressed:
        return {
            "progress_summary": "No active goals tracked yet.",
            "next_milestone":   "Create one in Month Plan → Goals.",
            "suggested_action": ("Suggested: cash buffer goal of 1 month "
                                 "of expenses (auto-tracks cash balance)."),
            "caution":          "Without a goal, milestones can't be measured.",
            **_ai_meta(ok=False, fallback=True),
        }
    top = progressed[0]
    pct = (top.get("progress_pct") or 0) * 100
    cur = float(top.get("current_amount") or 0)
    tgt = float(top.get("target_amount")  or 0)
    nxt = float(top.get("next_milestone") or 0)
    summary = f"{top.get('name')}: ${cur:,.0f} of ${tgt:,.0f} ({pct:.0f}%)."
    if len(progressed) > 1:
        summary += f" {len(progressed)-1} other active goal(s) tracked."
    next_milestone = f"Next milestone: ${nxt:,.0f} (${nxt-cur:,.0f} to go)."
    if pct < 25:
        action = "Set a small monthly contribution to start the streak."
    elif pct < 75:
        action = "Hold the line — automate the next contribution if possible."
    else:
        action = "Almost there — schedule the final transfer."
    caution = ""
    if not top.get("linked_metric"):
        caution = ("Goal current_amount is manual — update it after each "
                   "contribution so progress stays accurate.")
    return {
        "progress_summary": summary,
        "next_milestone":   next_milestone,
        "suggested_action": action,
        "caution":          caution,
        **_ai_meta(ok=False, fallback=True),
    }


def coach_goals(progressed: list[dict]) -> dict:
    """AI-narrated goal coaching with deterministic fallback."""
    fid = "coach_goals"
    ready, reason = ai_is_ready()
    if not ready or not progressed:
        _record_call_status(fid, attempted=False, ok=False,
                            fallback=True,
                            reason=reason or "no goals to coach")
        out = _deterministic_goal_coach(progressed or [])
        if reason:
            out["error"] = reason
        return out
    packet = [
        {
            "name":          g.get("name"),
            "type":          g.get("type"),
            "current":       float(g.get("current_amount")  or 0),
            "target":        float(g.get("target_amount")   or 0),
            "progress_pct":  float(g.get("progress_pct")    or 0),
            "next_milestone": float(g.get("next_milestone") or 0),
            "linked_metric": g.get("linked_metric"),
            "target_date":   g.get("target_date"),
        }
        for g in progressed[:5]
    ]
    user_prompt = (
        "Goals packet:\n" + json.dumps(packet, ensure_ascii=False, default=float)
        + "\n\nReturn ONLY JSON. {\"progress_summary\":\"<=240\","
        "\"next_milestone\":\"<=160\",\"suggested_action\":\"<=160\","
        "\"caution\":\"<=160\"}"
    )
    parsed, perr, diag = _call_and_parse(
        _GOAL_COACH_SYSTEM, user_prompt, feature_id=fid,
        max_tokens=_BUDGET_REDUCE,
    )
    if parsed is None:
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          parse_error=perr)
        out = _deterministic_goal_coach(progressed)
        out["error"] = diag.get("reason") or "AI failed — fallback used."
        out["diagnostic"] = diag
        return out
    ps  = _clamp_str(str(parsed.get("progress_summary","")), 240)
    nm  = _clamp_str(str(parsed.get("next_milestone","")),    160)
    sa  = _clamp_str(str(parsed.get("suggested_action","")),  160)
    cau = _clamp_str(str(parsed.get("caution","")),           160)
    if not (ps and sa):
        _record_from_diag(fid, ok=False, fallback=True, diag=diag,
                          validation_error="missing fields")
        out = _deterministic_goal_coach(progressed)
        out["error"] = "AI response incomplete — fallback used."
        out["diagnostic"] = diag
        return out
    _record_from_diag(fid, ok=True, fallback=False, diag=diag)
    return {
        "progress_summary": ps,
        "next_milestone":   nm,
        "suggested_action": sa,
        "caution":          cau,
        **_ai_meta(ok=True, diagnostic=diag),
    }

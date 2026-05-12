"""
Lightweight evidence-hash AI cache backed by `st.session_state`.

Why this exists
───────────────
Pass 11–14 cached AI results in `st.session_state["<feature>_cache"]` keyed by
feature name only. Two failure modes that pass:

1. After the user re-imports / edits transactions, the cached AI summary is
   still the old one — there's no signal to invalidate it. Manual feedback
   in Pass 15 testing: "What can I reduce?" sometimes shows stale numbers.
2. Long calls (Reduce summary, Weekly Review) re-run on every navigation if
   session_state was cleared (browser refresh, multipage edge cases). User
   pays the latency for an answer they already had.

Solution: cache by `(feature_id, evidence_hash)`. Evidence is whatever
deterministic data the AI summarizes — categories totals, latest score,
flagged count, etc. Caller computes a stable hash; if it matches the cached
hash for that feature, return cached. Otherwise call the AI fn and store.

Design notes
────────────
- Pure stdlib (`hashlib.blake2b`, no extra deps).
- Stored in `st.session_state["_ai_cache"]` as `{feature_id: {"hash": h, "value": v}}`.
- Caller can force regenerate via `force=True` (used by ↻ Refresh buttons).
- Caller can force-clear with `clear(feature_id)`.
- Hashing prefers `json.dumps(sort_keys=True, default=str)` so dicts /
  lists hash deterministically across reruns.
- This is in-process / per-session ONLY. We don't want to persist AI text
  to disk — it could go stale silently between sessions.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

try:
    import streamlit as st
except ImportError:                                   # pragma: no cover
    st = None  # type: ignore


_CACHE_KEY = "_ai_cache"


def _state() -> dict:
    """Return the per-session cache dict, creating it on first use."""
    if st is None:
        # Tests / non-Streamlit contexts: use a module-level dict so the
        # cache helper still functions in CLI verification scripts.
        global _fallback_state
        try:
            return _fallback_state
        except NameError:
            _fallback_state = {}                        # type: ignore[name-defined]
            return _fallback_state                      # type: ignore[name-defined]
    if _CACHE_KEY not in st.session_state:
        st.session_state[_CACHE_KEY] = {}
    return st.session_state[_CACHE_KEY]


def evidence_hash(*evidence: Any) -> str:
    """Stable short hash of arbitrary deterministic evidence.

    Each argument is serialized with `json.dumps(sort_keys=True, default=str)`
    and concatenated with a separator before hashing. Any object that
    json-serializes round-trip-stably is fine — dicts, lists, primitives.
    Non-serializable objects fall through to `default=str` so the hash is
    still produced (just less precise).

    Returns a 16-char hex digest — short enough to be readable in debug
    captions, long enough to make collisions effectively impossible for
    a single user's session.
    """
    h = hashlib.blake2b(digest_size=8)
    for item in evidence:
        try:
            blob = json.dumps(item, sort_keys=True, default=str).encode("utf-8")
        except (TypeError, ValueError):
            blob = repr(item).encode("utf-8", "replace")
        h.update(blob)
        h.update(b"\x00")        # separator so concatenation isn't ambiguous
    return h.hexdigest()


def get_or_compute(
    feature_id: str,
    ev_hash: str,
    compute: Callable[[], Any],
    *,
    force: bool = False,
) -> Any:
    """Return the cached value for `feature_id` if its evidence hash matches,
    otherwise call `compute()` and cache the result.

    `force=True` bypasses the cache and always recomputes. Useful for ↻
    Refresh buttons.
    """
    cache = _state()
    entry = cache.get(feature_id)
    if (not force) and entry and entry.get("hash") == ev_hash:
        return entry["value"]
    value = compute()
    cache[feature_id] = {"hash": ev_hash, "value": value}
    return value


def get_cached(feature_id: str) -> tuple[Any, str]:
    """Return `(value, hash)` if cached, else `(None, "")`."""
    entry = _state().get(feature_id)
    if entry:
        return entry["value"], entry["hash"]
    return None, ""


def clear(feature_id: str) -> None:
    """Drop a specific feature's cache entry."""
    _state().pop(feature_id, None)


def clear_all() -> None:
    """Drop every cached AI value. Useful from a 'Clear AI cache' button."""
    cache = _state()
    cache.clear()

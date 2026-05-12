"""
AI categorization config.

Stored at project-root `config.json` (not tracked, not exported). Server-side only —
never read from the browser, never written into exports. Keys are redacted
whenever the config is echoed back to the UI.

Shape:
    {
        "ai": {
            "enabled":  false,
            "provider": "minimax" | "anthropic" | "openai",
            "model":    "MiniMax-M2.7",
            "api_key":  "...",
            "base_url": null,   # optional override for OpenAI-compatible endpoints
            "max_suggest_per_batch": 25
        }
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

SUPPORTED_PROVIDERS = ("minimax", "anthropic", "openai")

# Default models per provider — users can override in Settings.
DEFAULT_MODELS = {
    "minimax":   "MiniMax-M2.7",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai":    "gpt-4o-mini",
}

# Default OpenAI-compatible base URLs. Only MiniMax has a non-null default.
# OpenAI uses its SDK default; Anthropic uses its own endpoint.
DEFAULT_BASE_URLS = {
    "minimax":   "https://api.minimax.io/v1",
    "anthropic": None,
    "openai":    None,
}


def _default_config() -> dict:
    return {
        "ai": {
            "enabled":  False,
            "provider": "minimax",
            "model":    DEFAULT_MODELS["minimax"],
            "api_key":  "",
            "base_url": None,
            "max_suggest_per_batch": 25,
        }
    }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return _default_config()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _default_config()

    # Fill in any missing fields so callers can rely on shape.
    base = _default_config()
    ai = data.get("ai") or {}
    base["ai"].update({k: v for k, v in ai.items() if v is not None or k == "base_url"})
    return base


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_ai_settings() -> dict:
    return load_config().get("ai", {})


def update_ai_settings(
    enabled: Optional[bool] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_suggest_per_batch: Optional[int] = None,
) -> dict:
    cfg = load_config()
    ai = cfg.setdefault("ai", {})
    if enabled is not None:
        ai["enabled"] = bool(enabled)
    if provider is not None:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        ai["provider"] = provider
        # Reset model/base_url defaults when provider changes unless caller provided them.
        if model is None:
            ai["model"] = DEFAULT_MODELS[provider]
        if base_url is None:
            ai["base_url"] = DEFAULT_BASE_URLS[provider]
    if model is not None:
        ai["model"] = model
    if api_key is not None:
        ai["api_key"] = api_key
    if base_url is not None:
        ai["base_url"] = base_url or None
    if max_suggest_per_batch is not None:
        ai["max_suggest_per_batch"] = int(max_suggest_per_batch)
    save_config(cfg)
    return ai


def clear_api_key() -> None:
    cfg = load_config()
    cfg.setdefault("ai", {})["api_key"] = ""
    cfg["ai"]["enabled"] = False
    save_config(cfg)


def ai_is_ready() -> tuple[bool, str]:
    """Returns (ready, reason)."""
    ai = get_ai_settings()
    if not ai.get("enabled"):
        return False, "AI categorization is disabled in Settings."
    if not ai.get("api_key"):
        return False, "No API key set for the selected provider."
    if ai.get("provider") not in SUPPORTED_PROVIDERS:
        return False, f"Unsupported provider: {ai.get('provider')}"
    if not ai.get("model"):
        return False, "No model configured."
    return True, ""


def redacted_settings() -> dict:
    """Returns AI settings with api_key redacted — safe for UI display."""
    ai = get_ai_settings().copy()
    key = ai.get("api_key", "")
    if key:
        ai["api_key_preview"] = key[:6] + "…" + key[-4:] if len(key) > 12 else "set"
    else:
        ai["api_key_preview"] = ""
    ai.pop("api_key", None)
    return ai

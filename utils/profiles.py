"""Separate sets of finances living in one installation.

The isolation here is a **directory boundary, not a filter**. Every profile
gets its own data directory, so it gets its own ``finance.db``, its own
backups and its own exports. Nothing queries "the transactions for profile
X", because there is no shared table to query: a profile that is not active
is not open. That matters more than it might sound. A filter has to be
applied correctly in every one of the dozens of places this engine reads
money, and one missed ``WHERE`` leaks somebody's finances into somebody
else's screen. A directory cannot be missed.

The default profile is deliberately the odd one out. Its data directory *is*
the existing root, the one that already holds ``finance.db``. An installation
that has been running for months becomes a profiled installation without a
single byte moving, which is the only migration that cannot corrupt
anything. New profiles live under ``<root>/profiles/<id>/`` and start empty.

The registry is a small JSON file at the root, outside every profile, naming
the profiles and which one is active. The engine runs one process per
request, so each request reads it during import and resolves its paths once.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REGISTRY_NAME = "profiles.json"
PROFILES_DIRNAME = "profiles"

# The profile whose directory is the data root itself. Its id is fixed
# because existing installations are silently adopted into it.
DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "My Finances"

MAX_NAME_LENGTH = 60

# An override for one process, used by tests and by any tool that needs to
# read a specific profile without disturbing which one the app has active.
ACTIVE_OVERRIDE_ENV = "LEDGER_PROFILE"


class ProfileError(RuntimeError):
    """A profile operation that must not be silently swallowed."""


def _registry_path(root: Path) -> Path:
    return root / REGISTRY_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "active": DEFAULT_PROFILE_ID,
        "profiles": [{
            "id": DEFAULT_PROFILE_ID,
            "name": DEFAULT_PROFILE_NAME,
            "created_at": _now(),
        }],
    }


def _read_registry(root: Path) -> dict[str, Any]:
    """The registry, or a fresh one describing the installation as it is.

    A missing or unreadable registry is not an error. Every installation
    before profiles existed has no registry and a database sitting at the
    root, and the correct reading of that is "one profile, the default one".
    Refusing to start would strand exactly the users this has to work for.
    """
    path = _registry_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank_registry()
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
        return _blank_registry()

    seen: set[str] = set()
    profiles: list[dict[str, Any]] = []
    for entry in raw["profiles"]:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        profiles.append({
            "id": pid,
            "name": str(entry.get("name") or pid).strip() or pid,
            "created_at": str(entry.get("created_at") or ""),
        })
    if not any(p["id"] == DEFAULT_PROFILE_ID for p in profiles):
        profiles.insert(0, _blank_registry()["profiles"][0])

    active = str(raw.get("active") or DEFAULT_PROFILE_ID)
    if not any(p["id"] == active for p in profiles):
        active = DEFAULT_PROFILE_ID
    return {"version": 1, "active": active, "profiles": profiles}


def _write_registry(root: Path, registry: dict[str, Any]) -> None:
    """Replace the registry atomically.

    A half-written registry would leave the app unable to decide which
    finances to open, so the new file is written beside the old one and
    moved over it.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = _registry_path(root)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(registry, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def profile_dir(root: Path, profile_id: str) -> Path:
    """Where one profile's finances live.

    The default profile resolves to the root itself. That is the whole
    migration story: an existing ``finance.db`` is already in the right
    place.
    """
    if profile_id == DEFAULT_PROFILE_ID:
        return root
    return root / PROFILES_DIRNAME / profile_id


def active_profile_id(root: Path) -> str:
    override = os.environ.get(ACTIVE_OVERRIDE_ENV, "").strip()
    if override:
        return override
    return _read_registry(root)["active"]


def active_profile_dir(root: Path) -> Path:
    """The directory the current process should read finances from."""
    return profile_dir(root, active_profile_id(root))


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())[:MAX_NAME_LENGTH].strip()
    if not cleaned:
        raise ProfileError("A profile needs a name.")
    return cleaned


def _new_id(existing: set[str]) -> str:
    while True:
        candidate = uuid.uuid4().hex[:12]
        # Never collide with the reserved id, and never reuse one, because a
        # reused id would inherit a deleted profile's directory.
        if candidate != DEFAULT_PROFILE_ID and candidate not in existing:
            return candidate


def list_profiles(root: Path) -> dict[str, Any]:
    """Every profile, which is active, and whether each holds any data yet."""
    registry = _read_registry(root)
    active = registry["active"]
    items = []
    for entry in registry["profiles"]:
        directory = profile_dir(root, entry["id"])
        database = directory / "finance.db"
        items.append({
            **entry,
            "active": entry["id"] == active,
            "is_default": entry["id"] == DEFAULT_PROFILE_ID,
            "has_data": database.is_file() and database.stat().st_size > 0,
            "path": str(directory),
        })
    return {"active": active, "profiles": items}


def create_profile(root: Path, name: str) -> dict[str, Any]:
    """Add an empty profile. Creating one never touches another's data."""
    registry = _read_registry(root)
    cleaned = _clean_name(name)
    taken = {p["name"].casefold() for p in registry["profiles"]}
    if cleaned.casefold() in taken:
        raise ProfileError(f"There is already a profile called {cleaned!r}.")

    new_id = _new_id({p["id"] for p in registry["profiles"]})
    directory = profile_dir(root, new_id)
    directory.mkdir(parents=True, exist_ok=True)
    registry["profiles"].append({
        "id": new_id, "name": cleaned, "created_at": _now(),
    })
    _write_registry(root, registry)
    return {"id": new_id, "name": cleaned}


def rename_profile(root: Path, profile_id: str, name: str) -> dict[str, Any]:
    registry = _read_registry(root)
    cleaned = _clean_name(name)
    target = next(
        (p for p in registry["profiles"] if p["id"] == profile_id), None
    )
    if target is None:
        raise ProfileError("That profile no longer exists.")
    taken = {
        p["name"].casefold() for p in registry["profiles"]
        if p["id"] != profile_id
    }
    if cleaned.casefold() in taken:
        raise ProfileError(f"There is already a profile called {cleaned!r}.")
    target["name"] = cleaned
    _write_registry(root, registry)
    return {"id": profile_id, "name": cleaned}


def switch_profile(root: Path, profile_id: str) -> dict[str, Any]:
    registry = _read_registry(root)
    if not any(p["id"] == profile_id for p in registry["profiles"]):
        raise ProfileError("That profile no longer exists.")
    registry["active"] = profile_id
    _write_registry(root, registry)
    return {"active": profile_id}


def delete_profile(root: Path, profile_id: str) -> dict[str, Any]:
    """Remove a profile and the finances inside it.

    Two refusals, both because the alternative is ambiguous rather than
    merely risky. The default profile's directory *is* the data root, so
    deleting it would mean deleting the registry and every other profile
    along with it. And deleting whichever profile is currently open would
    leave the running app reading a directory that no longer exists.
    """
    import shutil

    if profile_id == DEFAULT_PROFILE_ID:
        raise ProfileError(
            "The first profile cannot be deleted. Rename it instead, or "
            "delete the profiles created after it."
        )
    registry = _read_registry(root)
    if registry["active"] == profile_id:
        raise ProfileError(
            "Switch to another profile before deleting this one."
        )
    target = next(
        (p for p in registry["profiles"] if p["id"] == profile_id), None
    )
    if target is None:
        raise ProfileError("That profile no longer exists.")

    directory = profile_dir(root, profile_id)
    # Belt and braces: only ever remove a directory that is genuinely inside
    # the profiles folder. A malformed id must not be able to point this at
    # the data root.
    expected_parent = (root / PROFILES_DIRNAME).resolve()
    resolved = directory.resolve()
    if resolved.parent != expected_parent or resolved == expected_parent:
        raise ProfileError("That profile's location is not safe to remove.")
    if resolved.exists():
        shutil.rmtree(resolved)

    registry["profiles"] = [
        p for p in registry["profiles"] if p["id"] != profile_id
    ]
    _write_registry(root, registry)
    return {"deleted": profile_id, "active": registry["active"]}


def ensure_registry(root: Path) -> dict[str, Any]:
    """Write the registry if it is missing, adopting what is already there.

    Called once on startup. An installation with a database at the root and
    no registry becomes an installation with one profile named "My Finances"
    pointing at that same database. Nothing is copied, moved or rewritten.
    """
    path = _registry_path(root)
    if path.is_file():
        return _read_registry(root)
    registry = _blank_registry()
    _write_registry(root, registry)
    return registry


def active_profile(root: Path) -> dict[str, Any]:
    """Just enough about the open profile for a screen to name it."""
    registry = _read_registry(root)
    active = registry["active"]
    entry = next(
        (p for p in registry["profiles"] if p["id"] == active),
        {"id": DEFAULT_PROFILE_ID, "name": DEFAULT_PROFILE_NAME},
    )
    return {
        "id": entry["id"],
        "name": entry["name"],
        "is_default": entry["id"] == DEFAULT_PROFILE_ID,
        "count": len(registry["profiles"]),
    }

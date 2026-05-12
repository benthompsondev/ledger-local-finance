"""
PDF/CSV watcher — monitors a folder for new statement files and offers import.
Uses watchdog if available; falls back to polling.

Usage (called from Settings page or as a background thread):
    from utils.watcher import WatcherState, get_pending_files, mark_imported

The watcher is OPT-IN — users set a watch folder in Settings. It never auto-imports
without user confirmation.
"""
import os
import json
import hashlib
from pathlib import Path
from datetime import datetime

WATCHER_STATE_FILE = Path(__file__).parent.parent / "data" / "watcher_state.json"


def _load_state() -> dict:
    if WATCHER_STATE_FILE.exists():
        try:
            return json.loads(WATCHER_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"watch_folder": None, "seen_files": {}}


def _save_state(state: dict):
    WATCHER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHER_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_watch_folder() -> str | None:
    return _load_state().get("watch_folder")


def set_watch_folder(path: str | None):
    state = _load_state()
    state["watch_folder"] = path
    _save_state(state)


def file_hash(filepath: Path) -> str:
    h = hashlib.md5()
    h.update(filepath.read_bytes())
    return h.hexdigest()


def get_pending_files() -> list[dict]:
    """
    Scan the watch folder for PDF/CSV files not yet imported.
    Returns list of {path, filename, size_kb, modified, hash, status}
    status: 'new' | 'imported' | 'skipped'
    """
    state = _load_state()
    folder = state.get("watch_folder")
    if not folder or not Path(folder).is_dir():
        return []

    seen = state.get("seen_files", {})
    results = []

    for p in sorted(Path(folder).iterdir()):
        if p.suffix.lower() not in (".pdf", ".csv"):
            continue
        try:
            h = file_hash(p)
            status = seen.get(h, {}).get("status", "new")
            results.append({
                "path":     str(p),
                "filename": p.name,
                "size_kb":  round(p.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "hash":     h,
                "status":   status,
            })
        except Exception:
            pass

    return results


def mark_imported(file_hash_str: str):
    state = _load_state()
    state.setdefault("seen_files", {})[file_hash_str] = {
        "status": "imported",
        "at": datetime.now().isoformat(),
    }
    _save_state(state)


def mark_skipped(file_hash_str: str):
    state = _load_state()
    state.setdefault("seen_files", {})[file_hash_str] = {
        "status": "skipped",
        "at": datetime.now().isoformat(),
    }
    _save_state(state)


# Optional: watchdog-based live detection
# Only used if user has watchdog installed; degrades gracefully otherwise.
def start_watchdog(callback=None):
    """
    Start a background watchdog observer on the watch folder.
    callback(filepath) is called when a new PDF/CSV is detected.
    Returns the observer object (call .stop() to halt).
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        folder = get_watch_folder()
        if not folder or not Path(folder).is_dir():
            return None

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    p = Path(event.src_path)
                    if p.suffix.lower() in (".pdf", ".csv") and callback:
                        callback(str(p))

        observer = Observer()
        observer.schedule(Handler(), folder, recursive=False)
        observer.start()
        return observer
    except ImportError:
        return None  # watchdog not installed — polling fallback in UI

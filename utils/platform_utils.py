"""
platform_utils.py — cross-platform file, path, and temp-file helpers.

All other modules should use these instead of hardcoded /tmp paths or
platform-specific assumptions. Works correctly on Windows, macOS, and Linux.
"""
import os
import sys
import tempfile
import shutil
import platform
from pathlib import Path
from typing import Optional
import contextlib


# ── App and user-data roots ────────────────────────────────────────────
# PyInstaller exposes bundled assets under ``sys._MEIPASS``. In normal
# development ``__file__`` still resolves to the repository root.
APP_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).parent.parent)
).resolve()


def _configured_data_root() -> Optional[Path]:
    """Return the explicit persistent-data root, when configured.

    Packaged SpendShape sets LEDGER_DATA_DIR to ``%LOCALAPPDATA%\\Ledger``. The
    variable is also useful for tests and portable troubleshooting. An empty
    value means normal repository mode.

    That folder keeps its old name on purpose. The product is now SpendShape,
    but renaming the directory would orphan every existing database, so the
    path stays where the data already is.
    """
    raw = os.environ.get("LEDGER_DATA_DIR", "").strip()
    if not raw:
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def get_data_dir() -> Path:
    """Directory containing SpendShape's SQLite databases and watcher state."""
    return _configured_data_root() or (APP_ROOT / "data")


def get_config_path() -> Path:
    """AI settings path, persistent across packaged-app upgrades."""
    root = _configured_data_root()
    return (root / "config.json") if root else (APP_ROOT / "config.json")


def get_exports_dir() -> Path:
    """Export root. Repo mode keeps the existing ``exports/`` behavior."""
    root = _configured_data_root()
    return (root / "exports") if root else (APP_ROOT / "exports")


def get_logs_dir() -> Path:
    """Launcher/runtime log root."""
    root = _configured_data_root()
    return (root / "logs") if root else APP_ROOT


def get_backup_dir() -> Path:
    """SQLite-safe backup destination for the active data root."""
    return get_data_dir() / "backups"


# Compatibility constant for existing imports. Environment configuration is
# established before app modules load, so resolving once is intentional.
DATA_DIR = get_data_dir()


def ensure_data_dir() -> Path:
    """Create data/ directory if it doesn't exist. Returns the path."""
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ── Temp file helpers ──────────────────────────────────────────────────

def get_temp_dir() -> Path:
    """
    Returns a platform-safe temp directory.
    Uses tempfile.gettempdir() which resolves to:
      Windows : C:\\Users\\<user>\\AppData\\Local\\Temp
      macOS   : /var/folders/...  or  /tmp
      Linux   : /tmp
    """
    return Path(tempfile.gettempdir())


@contextlib.contextmanager
def temp_pdf(filename: str, file_bytes: bytes):
    """
    Context manager that writes bytes to a temp file and yields the Path.
    Guarantees cleanup even if an exception occurs, and handles Windows
    file-locking by retrying the delete.

    Usage:
        with temp_pdf(uploaded_file.name, uploaded_file.read()) as path:
            result = parse_pdf(path)
    """
    # Use NamedTemporaryFile with delete=False so we control the lifecycle.
    # Windows cannot delete an open file, so we close it first then delete manually.
    suffix = Path(filename).suffix.lower() or ".pdf"
    # Sanitize filename for Windows (remove chars illegal in filenames)
    safe_name = _safe_filename(filename)

    tmp = tempfile.NamedTemporaryFile(
        prefix=f"ledger_{safe_name}_",
        suffix=suffix,
        delete=False,
    )
    tmp_path = Path(tmp.name)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        tmp.close()   # must close before reading on Windows
        yield tmp_path
    finally:
        _safe_delete(tmp_path)


def write_temp_file(filename: str, file_bytes: bytes) -> Path:
    """
    Write bytes to a platform-safe temp location. Caller is responsible
    for calling safe_delete() when done.
    Returns the Path to the temp file.
    """
    safe_name = _safe_filename(filename)
    suffix = Path(filename).suffix.lower() or ".tmp"
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"ledger_{safe_name}_",
        suffix=suffix,
        delete=False,
    )
    tmp_path = Path(tmp.name)
    tmp.write(file_bytes)
    tmp.flush()
    tmp.close()
    return tmp_path


def safe_delete(path: Path):
    """
    Delete a file, handling Windows file-locking gracefully.
    Silently ignores errors (e.g. file already deleted, still locked).
    """
    _safe_delete(path)


# ── Path utilities ─────────────────────────────────────────────────────

def resolve_path(path_str: str) -> Path:
    """
    Resolve a user-supplied path string to an absolute Path.
    Expands ~ (home dir) and environment variables, works on Windows.
    """
    return Path(os.path.expandvars(os.path.expanduser(path_str))).resolve()


def is_valid_directory(path_str: str) -> bool:
    """Return True if path_str points to an existing directory on this OS."""
    if not path_str:
        return False
    try:
        return resolve_path(path_str).is_dir()
    except (OSError, ValueError):
        return False


def open_folder_in_explorer(path: Path):
    """
    Open a folder in the system file explorer (Windows Explorer, Finder, Nautilus).
    Silent no-op if the platform is unsupported or the path doesn't exist.
    """
    if not path.is_dir():
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(path))          # Windows
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(path)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass  # Never raise — this is purely a convenience feature


# ── Platform info ──────────────────────────────────────────────────────

def is_windows() -> bool:
    return sys.platform == "win32"


def is_mac() -> bool:
    return sys.platform == "darwin"


def platform_name() -> str:
    return platform.system()  # "Windows", "Darwin", "Linux"


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def watch_folder_placeholder() -> str:
    """Returns an OS-appropriate example path for the watch folder input."""
    if is_windows():
        return r"C:\Users\YourName\Documents\Statements"
    elif is_mac():
        return "/Users/YourName/Downloads/Statements"
    else:
        return "/home/yourname/Downloads/Statements"


# ── Internal helpers ───────────────────────────────────────────────────

def _safe_filename(name: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    illegal = r'\/:*?"<>|'
    for ch in illegal:
        name = name.replace(ch, "_")
    # Truncate to 40 chars to keep temp names readable
    stem = Path(name).stem[:40]
    return stem or "file"


def _safe_delete(path: Path):
    """Delete with Windows-safe retry on PermissionError."""
    if not path or not path.exists():
        return
    try:
        path.unlink()
    except PermissionError:
        # Windows: file may still be held open briefly — try once more
        import time
        time.sleep(0.1)
        try:
            path.unlink()
        except Exception:
            pass  # Give up silently — OS will clean up on reboot
    except Exception:
        pass

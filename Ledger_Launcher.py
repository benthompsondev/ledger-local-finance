"""
Ledger desktop launcher.

This script gets Ledger running on Windows without assuming the local Python
environment is already healthy. It checks the existing venv, falls back through
common Python launchers, rebuilds a broken venv when needed, writes a local log,
and shows copy/paste repair commands if startup fails.

It does not change finance app data or business logic. It only prepares the
environment and starts Streamlit.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

# Tk is stdlib; only used for popup. Importing inside main() keeps headless
# environments (CI, etc.) from crashing on missing Tk.

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"  # Windows path; POSIX handled below
if os.name != "nt":
    VENV_PYTHON = VENV_DIR / "bin" / "python"
REQUIREMENTS = BASE_DIR / "requirements.txt"
APP_FILE = BASE_DIR / "app.py"
LOG_FILE = BASE_DIR / "launcher.log"
PORT = 8501


# ── Logging ──────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    """Append a single timestamped line to launcher.log. Never throws."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] {msg}\n")
    except Exception:
        # Logging must never fail the run.
        pass
    # Best-effort console mirror for users running from a terminal.
    try:
        print(msg)
    except Exception:
        pass


def _log_section(title: str) -> None:
    _log("")
    _log(f"=== {title} ===")


# ── Subprocess helpers ──────────────────────────────────────────────────

def _run(cmd: list[str], *, label: str,
         capture: bool = True) -> tuple[int, str, str]:
    """Run `cmd`, capture stdout/stderr, log the command + return code.

    Returns (returncode, stdout, stderr). Never raises; the caller decides
    how to react to a non-zero return code.
    """
    _log(f"$ {label}: {' '.join(_q(c) for c in cmd)}")
    try:
        r = subprocess.run(
            cmd, cwd=str(BASE_DIR),
            capture_output=capture, text=True,
        )
    except FileNotFoundError as e:
        _log(f"  -> FileNotFoundError: {e}")
        return 127, "", str(e)
    except Exception as e:
        _log(f"  -> exception: {e}")
        return 1, "", str(e)
    if capture:
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if out:
            _log(f"  stdout: {out[:600]}")
        if err:
            _log(f"  stderr: {err[:1200]}")
    _log(f"  -> rc={r.returncode}")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def _q(s: str) -> str:
    """Quote an argv token for the log only — not for shell execution."""
    return f'"{s}"' if (" " in s) else s


# ── Python detection ────────────────────────────────────────────────────

def _try(cmd: list[str]) -> bool:
    """Probe `cmd --version` quietly. True if it exits 0 within 10s."""
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True,
                           text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def detect_host_python(*, allow_existing_venv: bool = True) -> Optional[list[str]]:
    """Return the argv prefix of a working Python interpreter, or None.

    Detection order:
      1. existing project .venv\\Scripts\\python.exe (only if --version works)
      2. py -3.14
      3. py -3
      4. py
      5. python
      6. python3

    Each candidate is verified by actually invoking `--version`. We never
    return a hardcoded path; what's returned is the literal argv we'll
    invoke later.
    """
    candidates: list[list[str]] = []

    if allow_existing_venv and VENV_PYTHON.exists():
        candidates.append([str(VENV_PYTHON)])

    # `py` launcher with progressively-broader version flags.
    py_exe = shutil.which("py")
    if py_exe:
        candidates.extend([
            ["py", "-3.14"],
            ["py", "-3"],
            ["py"],
        ])

    # PATH-based python(3).
    if shutil.which("python"):
        candidates.append(["python"])
    if shutil.which("python3"):
        candidates.append(["python3"])

    for c in candidates:
        if _try(c):
            return c
        else:
            _log(f"python candidate FAILED: {' '.join(c)}")

    return None


def python_version_string(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True,
                           text=True, timeout=10)
        return (r.stdout or r.stderr or "").strip()
    except Exception:
        return "unknown"


# ── venv validation ─────────────────────────────────────────────────────

def venv_health() -> tuple[bool, str]:
    """Return (healthy, reason). Healthy = python AND pip both work.

    `pip` failure is the key signal we care about: that's where the
    corrupted-vendored-idna ImportError surfaces. We treat ANY non-zero
    exit from `python -m pip --version` as "rebuild the venv".
    """
    if not VENV_PYTHON.exists():
        return False, "venv missing"

    rc, _, err = _run([str(VENV_PYTHON), "--version"],
                      label="probe venv python", capture=True)
    if rc != 0:
        return False, f"venv python failed: {err.strip()[:200]}"

    rc, _, err = _run([str(VENV_PYTHON), "-m", "pip", "--version"],
                      label="probe venv pip", capture=True)
    if rc != 0:
        # The exact failure mode from the user's report:
        #   ImportError: cannot import name 'idnadata' from partially
        #   initialized module 'pip._vendor.idna' (most likely due to a
        #   circular import)
        # We don't need to special-case it — any pip failure means rebuild.
        return False, f"venv pip failed: {err.strip()[:300]}"

    return True, "ok"


# ── venv rebuild ────────────────────────────────────────────────────────

def _backup_broken_venv() -> Optional[Path]:
    """Move .venv to .venv.broken-YYYYMMDD-HHMMSS. Return new path, or None
    if the venv didn't exist."""
    if not VENV_DIR.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BASE_DIR / f".venv.broken-{stamp}"
    _log(f"renaming broken venv to {target.name}")
    try:
        VENV_DIR.rename(target)
    except OSError as e:
        # Windows: if Python is still running from this venv, rename will
        # fail with WinError 32. Surface a clear error.
        _log(f"ERROR: could not rename venv: {e}")
        raise
    return target


def rebuild_venv(host_py: list[str]) -> None:
    """Rename the existing venv (if any), create a fresh one, bootstrap pip.

    This avoids the corrupted-pip path: ensurepip writes a known-good pip
    into the new venv directly, without going through the host's broken
    pip._vendor module graph.
    """
    _log_section("rebuilding venv")
    _backup_broken_venv()

    rc, _, err = _run(host_py + ["-m", "venv", str(VENV_DIR)],
                      label="create fresh venv")
    if rc != 0 or not VENV_PYTHON.exists():
        raise RuntimeError(
            f"venv creation failed (rc={rc}). stderr: {err.strip()[:300]}"
        )

    # Bootstrap pip inside the new venv. This is the line that survives
    # a broken host pip — ensurepip ships its own copy of pip/setuptools.
    rc, _, err = _run([str(VENV_PYTHON), "-m", "ensurepip", "--upgrade"],
                      label="ensurepip --upgrade")
    if rc != 0:
        raise RuntimeError(
            f"ensurepip failed (rc={rc}). stderr: {err.strip()[:300]}"
        )


def upgrade_packaging_tools() -> None:
    """python -m pip install --upgrade pip setuptools wheel.

    Always via `python -m pip` so we never depend on a `pip.exe` shim.
    Safe to call on a fresh OR existing venv.
    """
    rc, _, err = _run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade",
         "pip", "setuptools", "wheel"],
        label="upgrade pip/setuptools/wheel",
    )
    if rc != 0:
        raise RuntimeError(
            f"upgrade pip/setuptools/wheel failed (rc={rc}). "
            f"stderr: {err.strip()[:400]}"
        )


def install_requirements() -> None:
    """Install requirements.txt into the venv. Required before launch."""
    if not REQUIREMENTS.exists():
        _log(f"WARN: requirements.txt not found at {REQUIREMENTS}")
        return
    rc, _, err = _run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        label="install requirements",
    )
    if rc != 0:
        raise RuntimeError(
            f"requirements install failed (rc={rc}). "
            f"stderr: {err.strip()[:600]}"
        )


def ensure_streamlit_importable() -> None:
    """Last-mile guard: streamlit must be importable before we Popen it."""
    rc, _, _ = _run([str(VENV_PYTHON), "-m", "streamlit", "--version"],
                    label="probe streamlit")
    if rc != 0:
        _log("streamlit not importable -- attempting one-shot install")
        rc, _, err = _run(
            [str(VENV_PYTHON), "-m", "pip", "install", "streamlit"],
            label="install streamlit fallback",
        )
        if rc != 0:
            raise RuntimeError(
                f"streamlit install failed (rc={rc}). "
                f"stderr: {err.strip()[:300]}"
            )


# ── Launch ──────────────────────────────────────────────────────────────

def prepare_demo_data() -> None:
    """Create or refresh the fake demo database for first-time review."""
    _log_section("preparing demo data")
    rc, _, err = _run(
        [str(VENV_PYTHON), "-m", "scripts.create_demo_data", "--force"],
        label="create demo data",
    )
    if rc != 0:
        raise RuntimeError(
            f"demo data creation failed (rc={rc}). "
            f"stderr: {err.strip()[:400]}"
        )


def launch_streamlit(*, demo: bool = False) -> None:
    if not APP_FILE.exists():
        raise RuntimeError(f"app.py not found at {APP_FILE}")
    _log_section("launching Streamlit")
    env = os.environ.copy()
    if demo:
        env["LEDGER_DEMO_DB"] = "1"
        _log("demo mode enabled: LEDGER_DEMO_DB=1")
    # Bind to localhost only. Streamlit defaults can expose the app on
    # every network interface; Ledger should stay local unless the user
    # deliberately changes this.
    cmd = [
        str(VENV_PYTHON), "-m", "streamlit", "run", str(APP_FILE),
        "--server.address", "127.0.0.1",
        "--server.port", str(PORT),
    ]
    _log(f"$ {' '.join(_q(c) for c in cmd)}")
    subprocess.Popen(cmd, cwd=str(BASE_DIR), env=env)
    time.sleep(3)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception as e:
        _log(f"webbrowser.open failed: {e}")


# ── Manual repair instructions ──────────────────────────────────────────

REPAIR_INSTRUCTIONS = (
    "Manual repair (copy/paste into a fresh CMD window):\n"
    "\n"
    f'  cd /d "{BASE_DIR}"\n'
    "  rmdir /s /q .venv\n"
    "  py -3.14 -m venv .venv\n"
    "  .\\.venv\\Scripts\\python.exe -m ensurepip --upgrade\n"
    "  .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip setuptools wheel\n"
    "  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n"
    "  .\\.venv\\Scripts\\python.exe -m streamlit run app.py\n"
    "\n"
    "If `py -3.14` is not available, substitute `py -3` or the full path "
    "to your Python install (e.g. "
    "C:\\Path\\To\\Python314\\python.exe).\n"
    f"Diagnostics: {LOG_FILE}"
)


# ── Popup helpers ───────────────────────────────────────────────────────

def _popup(title: str, message: str, *, error: bool = False) -> None:
    try:
        from tkinter import Tk, messagebox
    except Exception:
        # Headless / no Tk — fall back to stderr.
        sys.stderr.write(f"\n{title}\n{message}\n")
        return
    root = Tk()
    root.withdraw()
    try:
        if error:
            messagebox.showerror(title, message)
        else:
            messagebox.showinfo(title, message)
    finally:
        root.destroy()


# ── Main ────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Ledger's local Python environment and start the app."
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "Create fake demo data and launch Ledger against the demo database. "
            "Use this for first-time review or screenshots."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])

    # Truncate the log on each run so launcher.log captures the current
    # session in isolation. Prior sessions live in `launcher.log.prev` if
    # the user wants them.
    try:
        if LOG_FILE.exists():
            prev = LOG_FILE.with_suffix(".log.prev")
            try:
                if prev.exists():
                    prev.unlink()
                LOG_FILE.rename(prev)
            except Exception:
                pass
        LOG_FILE.write_text(
            f"=== Ledger launcher run "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n"
            f"cwd={BASE_DIR}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        os.chdir(BASE_DIR)

        if not APP_FILE.exists():
            _log(f"FATAL: app.py not found at {APP_FILE}")
            _popup(
                "Ledger launcher",
                f"app.py not found in:\n{BASE_DIR}\n\n"
                f"This launcher must live next to app.py.",
                error=True,
            )
            return 1

        # Step 1: detect a host Python.
        _log_section("detecting host Python")
        host_py = detect_host_python(allow_existing_venv=True)
        if host_py is None:
            _log("FATAL: no working Python found")
            _popup(
                "Ledger launcher",
                "Ledger could not find a working Python.\n\n"
                "Install Python 3.12 or newer from "
                "https://www.python.org/downloads/ and tick "
                "'Add Python to PATH' during install.\n\n"
                f"Diagnostics: {LOG_FILE}",
                error=True,
            )
            return 1
        _log(f"selected host python: {' '.join(host_py)}")
        _log(f"selected host python version: {python_version_string(host_py)}")
        _log(f"py launcher available: {bool(shutil.which('py'))}")

        # Step 2: validate the existing venv.
        _log_section("validating venv")
        healthy, reason = venv_health()
        _log(f"venv health: {'OK' if healthy else 'BROKEN'} ({reason})")

        # Step 3: rebuild if needed. If the only host Python we found IS
        # the broken venv Python, re-detect without the venv option.
        if not healthy:
            _log_section("recovering venv")
            if VENV_PYTHON.exists() and host_py == [str(VENV_PYTHON)]:
                _log("re-detecting host Python (skipping broken venv)")
                host_py = detect_host_python(allow_existing_venv=False)
                if host_py is None:
                    _log("FATAL: venv broken AND no host Python to rebuild")
                    _popup(
                        "Ledger launcher",
                        "Ledger could not prepare its Python environment. "
                        "The existing .venv appears corrupted, and no "
                        "host Python is available to rebuild it.\n\n"
                        f"{REPAIR_INSTRUCTIONS}",
                        error=True,
                    )
                    return 1
            rebuild_venv(host_py)

        # Step 4: upgrade packaging tools (always via `python -m pip`).
        _log_section("upgrading packaging tools")
        upgrade_packaging_tools()

        # Step 5: install requirements.
        _log_section("installing requirements")
        install_requirements()

        # Step 6: streamlit guard.
        _log_section("verifying streamlit")
        ensure_streamlit_importable()

        # Step 7: optional fake demo data.
        if args.demo:
            prepare_demo_data()

        # Step 8: launch.
        launch_streamlit(demo=args.demo)
        _log("launcher: complete")
        return 0

    except Exception as e:
        _log(f"FATAL: {e!r}")
        _log(traceback.format_exc())
        _popup(
            "Ledger launcher error",
            f"Ledger could not prepare its Python environment.\n\n"
            f"What failed: {e}\n\n"
            f"{REPAIR_INSTRUCTIONS}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())

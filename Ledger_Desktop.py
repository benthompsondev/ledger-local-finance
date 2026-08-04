"""Windows launcher for the installed and portable Ledger application."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

from utils import __version__


APP_NAME = "Ledger"
APP_PUBLISHER = "Ben Thompson"
APP_VERSION = __version__
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
HEALTH_PATH = "/_stcore/health"
STARTUP_TIMEOUT_SECONDS = 60
CONTROL_TIMEOUT_SECONDS = 2


def bundle_root() -> Path:
    """Return bundled assets, or the repository root during development."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve()


def executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_data_root() -> Path:
    override = os.environ.get("LEDGER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local).resolve() / APP_NAME
    return Path.home() / ".ledger"


def configure_data_root(override: Optional[str] = None) -> Path:
    root = Path(override).expanduser().resolve() if override else default_data_root()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["LEDGER_DATA_DIR"] = str(root)
    return root


def log_path(data_root: Path) -> Path:
    return data_root / "logs" / "launcher.log"


def configure_logging(data_root: Path) -> logging.Logger:
    destination = log_path(data_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ledger.desktop")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()
    handler = RotatingFileHandler(
        destination,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _port_is_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((DEFAULT_HOST, int(port)))
        return True
    except OSError:
        return False


def find_available_port(preferred: Optional[int] = None, *, strict: bool = False) -> int:
    """Choose a loopback-only port, falling back when the default is occupied."""
    if preferred == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((DEFAULT_HOST, 0))
            return int(probe.getsockname()[1])
    candidate = int(preferred or DEFAULT_PORT)
    if _port_is_available(candidate):
        return candidate
    if strict:
        raise OSError(f"Requested port {candidate} is already in use.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((DEFAULT_HOST, 0))
        return int(probe.getsockname()[1])


def runtime_file(data_root: Path) -> Path:
    return data_root / "runtime.json"


def read_runtime_state(data_root: Path) -> Optional[dict[str, Any]]:
    try:
        state = json.loads(runtime_file(data_root).read_text(encoding="utf-8"))
        return {
            "pid": int(state["pid"]),
            "port": int(state["port"]),
            "control_port": int(state["control_port"]),
            "version": str(state.get("version", "")),
            "started_at": str(state.get("started_at", "")),
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def write_runtime_state(
    data_root: Path,
    *,
    pid: int,
    port: int,
    control_port: int,
) -> None:
    path = runtime_file(data_root)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(
            {
                "pid": int(pid),
                "port": int(port),
                "control_port": int(control_port),
                "version": APP_VERSION,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temp, path)


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def health_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{int(port)}{HEALTH_PATH}"


def app_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{int(port)}"


def server_is_healthy(port: int, timeout: float = 0.75) -> bool:
    request = urllib.request.Request(health_url(port), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(32).decode("utf-8", errors="replace").strip().lower()
            return response.status == 200 and body == "ok"
    except (OSError, urllib.error.URLError, ValueError):
        return False


def send_control(control_port: int, command: str) -> bool:
    try:
        with socket.create_connection(
            (DEFAULT_HOST, int(control_port)), timeout=CONTROL_TIMEOUT_SECONDS
        ) as connection:
            connection.sendall((command.strip().upper() + "\n").encode("ascii"))
            connection.settimeout(CONTROL_TIMEOUT_SECONDS)
            return connection.recv(32).decode("ascii", errors="replace").strip() == "OK"
    except OSError:
        return False


def running_url(data_root: Path) -> Optional[str]:
    state = read_runtime_state(data_root)
    if not state or not process_is_running(state["pid"]):
        return None
    if server_is_healthy(state["port"]):
        return app_url(state["port"])
    return None


def _mutex_name(data_root: Path) -> str:
    digest = hashlib.sha256(str(data_root).lower().encode("utf-8")).hexdigest()[:16]
    return f"Local\\LedgerLauncher-{digest}"


def acquire_launch_mutex(data_root: Path) -> tuple[Optional[int], bool]:
    if os.name != "nt":
        return None, False
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, True, _mutex_name(data_root))
    if not handle:
        raise OSError("Windows could not create the Ledger startup lock.")
    already_exists = ctypes.windll.kernel32.GetLastError() == 183
    if already_exists:
        ctypes.windll.kernel32.CloseHandle(handle)
        return None, True
    return int(handle), False


def release_launch_mutex(handle: Optional[int]) -> None:
    if os.name == "nt" and handle:
        import ctypes

        ctypes.windll.kernel32.ReleaseMutex(handle)
        ctypes.windll.kernel32.CloseHandle(handle)


def _legacy_candidates(destination: Path) -> list[Path]:
    """Find an existing repo-mode DB when a private build is near its repo."""
    candidates = [
        Path.cwd() / "data" / "finance.db",
        install_root() / "data" / "finance.db",
    ]
    exe = executable_path()
    if getattr(sys, "frozen", False) and len(exe.parents) >= 3:
        candidates.append(exe.parents[2] / "data" / "finance.db")
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved != destination.resolve() and resolved not in unique:
            unique.append(resolved)
    return [path for path in unique if path.is_file()]


def _looks_like_ledger_database(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        return "transactions" in tables and "import_log" in tables
    except sqlite3.DatabaseError:
        return False
    finally:
        if "conn" in locals():
            conn.close()


def import_legacy_database(source: Path, destination: Path) -> None:
    if not _looks_like_ledger_database(source):
        raise ValueError("The selected legacy database is not a Ledger database.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dest = sqlite3.connect(destination)
    try:
        src.backup(dest)
        dest.commit()
        dest.execute("PRAGMA journal_mode=DELETE").fetchone()
    finally:
        dest.close()
        src.close()


def offer_legacy_import(data_root: Path, logger: logging.Logger) -> None:
    destination = data_root / "finance.db"
    if destination.exists():
        return
    candidates = _legacy_candidates(destination)
    if not candidates:
        return
    source = candidates[0]
    try:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        accepted = messagebox.askyesno(
            "Ledger found existing data",
            "Ledger found an existing repository-mode database:\n\n"
            f"{source}\n\n"
            "Copy it into Ledger's private data folder? The original will not be changed.",
        )
        root.destroy()
    except Exception as exc:
        logger.info("Legacy import prompt unavailable: %s", exc)
        return
    if accepted:
        import_legacy_database(source, destination)
        logger.info("Imported existing repository database into packaged data root")


class StartupStatus:
    """Small native Tk status window shown while the local server starts."""

    def __init__(self, enabled: bool, logger: logging.Logger) -> None:
        self.root = None
        self.label = None
        self.progress = None
        if not enabled or os.name != "nt":
            return
        try:
            from tkinter import Tk, ttk

            root = Tk()
            root.title(APP_NAME)
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", lambda: None)
            width, height = 420, 135
            x = max(0, (root.winfo_screenwidth() - width) // 2)
            y = max(0, (root.winfo_screenheight() - height) // 2)
            root.geometry(f"{width}x{height}+{x}+{y}")
            try:
                root.iconbitmap(default=str(sys.executable))
            except Exception:
                pass
            frame = ttk.Frame(root, padding=24)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Ledger is starting", font=("Segoe UI", 13, "bold")).pack(
                anchor="w"
            )
            self.label = ttk.Label(frame, text="Starting the private local server...")
            self.label.pack(anchor="w", pady=(8, 10))
            self.progress = ttk.Progressbar(frame, mode="indeterminate")
            self.progress.pack(fill="x")
            self.progress.start(10)
            self.root = root
            self.pump()
        except Exception as exc:
            logger.warning("Startup status window unavailable: %s", exc)

    def update(self, message: str) -> None:
        if self.label is not None:
            self.label.configure(text=message)
        self.pump()

    def pump(self) -> None:
        if self.root is not None:
            self.root.update_idletasks()
            self.root.update()

    def close(self) -> None:
        if self.progress is not None:
            self.progress.stop()
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass
        self.root = None


def show_startup_error(message: str, destination: Path, *, no_dialog: bool) -> None:
    detail = (
        f"{message}\n\n"
        "Ledger did not start. No financial data was removed.\n\n"
        f"Startup log:\n{destination}"
    )
    if no_dialog or os.name != "nt":
        print(detail, file=sys.stderr)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, detail, "Ledger could not start", 0x10)


def open_default_browser(url: str, logger: logging.Logger) -> None:
    logger.info("Opening default browser at %s", url)
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
    elif not webbrowser.open(url, new=2):
        raise RuntimeError(f"The default browser did not accept {url}.")
    logger.info("Browser launch requested successfully")


def _launcher_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    return [str(Path(sys.executable).resolve()), str(Path(__file__).resolve())]


def start_server_process(
    data_root: Path,
    port: int,
    control_port: int,
    logger: logging.Logger,
) -> subprocess.Popen[Any]:
    command = _launcher_command() + [
        "--serve",
        "--data-dir",
        str(data_root),
        "--port",
        str(port),
        "--control-port",
        str(control_port),
        "--no-dialog",
    ]
    server_output = data_root / "logs" / "streamlit.log"
    stream = server_output.open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200 | 0x08000000
    logger.info("Starting bundled Streamlit server")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(install_root()),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )
    finally:
        stream.close()
    write_runtime_state(
        data_root,
        pid=process.pid,
        port=port,
        control_port=control_port,
    )
    logger.info("Server process started with PID %s", process.pid)
    return process


def wait_for_server(
    data_root: Path,
    process: Optional[subprocess.Popen[Any]],
    status: StartupStatus,
    logger: logging.Logger,
    timeout: int = STARTUP_TIMEOUT_SECONDS,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read_runtime_state(data_root)
        if (
            state
            and process_is_running(state["pid"])
            and server_is_healthy(state["port"])
            and send_control(state["control_port"], "PING")
        ):
            url = app_url(state["port"])
            logger.info("Health endpoint ready at %s", health_url(state["port"]))
            logger.info("Ledger ready at %s", url)
            return url
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"The bundled Streamlit server exited with code {process.returncode}."
            )
        status.update("Waiting for Ledger to become ready...")
        time.sleep(0.2)
    raise TimeoutError(f"Ledger did not become ready within {timeout} seconds.")


def stop_server_process(data_root: Path, logger: logging.Logger) -> bool:
    state = read_runtime_state(data_root)
    if not state:
        logger.info("Shutdown requested; no runtime state exists")
        return True
    pid = state["pid"]
    logger.info("Shutdown requested for server PID %s", pid)
    if not process_is_running(pid):
        runtime_file(data_root).unlink(missing_ok=True)
        logger.info("Removed stale runtime state")
        return True
    if not send_control(state["control_port"], "SHUTDOWN"):
        logger.error("Running server did not accept the shutdown request")
        return False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not process_is_running(pid):
            runtime_file(data_root).unlink(missing_ok=True)
            logger.info("Server stopped cleanly")
            return True
        time.sleep(0.1)
    logger.error("Server did not stop within 10 seconds")
    return False


def _control_server(
    data_root: Path,
    control_port: int,
    logger: logging.Logger,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((DEFAULT_HOST, int(control_port)))
        server.listen(4)
        logger.info("Control channel listening on %s:%s", DEFAULT_HOST, control_port)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(CONTROL_TIMEOUT_SECONDS)
                try:
                    command = connection.recv(32).decode("ascii", errors="replace").strip()
                except OSError:
                    continue
                if command == "REUSE":
                    logger.info("Existing Ledger instance reused by a second launch")
                    connection.sendall(b"OK\n")
                elif command == "PING":
                    connection.sendall(b"OK\n")
                elif command == "SHUTDOWN":
                    logger.info("Server accepted a controlled shutdown request")
                    connection.sendall(b"OK\n")
                    runtime_file(data_root).unlink(missing_ok=True)
                    for handler in logger.handlers:
                        handler.flush()
                    os._exit(0)
                else:
                    connection.sendall(b"ERROR\n")


def run_server(args: argparse.Namespace) -> int:
    data_root = configure_data_root(args.data_dir)
    logger = configure_logging(data_root)
    app_path = bundle_root() / "app.py"
    if not app_path.is_file():
        logger.error("Bundled app.py not found at %s", app_path)
        return 1
    if not args.port or not args.control_port:
        logger.error("Internal server launch is missing its port arguments")
        return 1
    write_runtime_state(
        data_root,
        pid=os.getpid(),
        port=args.port,
        control_port=args.control_port,
    )
    logger.info("Executable path: %s", executable_path())
    logger.info("Ledger version: %s", APP_VERSION)
    logger.info("Data directory: %s", data_root)
    logger.info("Selected server port: %s", args.port)
    control = threading.Thread(
        target=_control_server,
        args=(data_root, args.control_port, logger),
        daemon=True,
    )
    control.start()
    try:
        from streamlit import config as streamlit_config
        from streamlit.web import bootstrap

        flag_options = {
            "global.developmentMode": False,
            "server.address": DEFAULT_HOST,
            "server.port": args.port,
            "server.headless": True,
            "server.enableCORS": False,
            "server.enableXsrfProtection": True,
            "browser.serverAddress": DEFAULT_HOST,
            "browser.serverPort": args.port,
            "browser.gatherUsageStats": False,
            "server.fileWatcherType": "none",
        }
        streamlit_config._main_script_path = str(app_path)
        bootstrap.load_config_options(flag_options=flag_options)
        logger.info("Starting Streamlit on %s", app_url(args.port))
        bootstrap.run(str(app_path), False, [], flag_options)
        logger.info("Streamlit server stopped")
        return 0
    except KeyboardInterrupt:
        logger.info("Streamlit server stopped by user")
        return 0
    except Exception:
        logger.exception("Bundled Streamlit server failed")
        return 1
    finally:
        runtime_file(data_root).unlink(missing_ok=True)


def _reuse_existing(
    data_root: Path,
    args: argparse.Namespace,
    logger: logging.Logger,
    *,
    wait: bool,
) -> bool:
    state = read_runtime_state(data_root)
    if not state or not process_is_running(state["pid"]):
        return False
    if wait:
        status = StartupStatus(not args.no_status, logger)
        try:
            url = wait_for_server(data_root, None, status, logger)
        finally:
            status.close()
    elif server_is_healthy(state["port"]):
        url = app_url(state["port"])
    else:
        return False
    send_control(state["control_port"], "REUSE")
    logger.info("Reusing existing server at %s", url)
    if not args.no_browser:
        open_default_browser(url, logger)
    return True


def run_launcher(args: argparse.Namespace) -> int:
    data_root = configure_data_root(args.data_dir)
    logger = configure_logging(data_root)
    destination = log_path(data_root)
    logger.info("Launcher invoked")
    logger.info("Executable path: %s", executable_path())
    logger.info("Ledger version: %s", APP_VERSION)
    logger.info("Data directory: %s", data_root)

    if args.shutdown:
        return 0 if stop_server_process(data_root, logger) else 1

    try:
        if _reuse_existing(data_root, args, logger, wait=False):
            return 0

        mutex, another_launcher = acquire_launch_mutex(data_root)
        if another_launcher:
            logger.info("Another Ledger launcher is already starting; waiting for it")
            if _reuse_existing(data_root, args, logger, wait=True):
                return 0
            raise RuntimeError("Another Ledger launch did not become ready.")

        process: Optional[subprocess.Popen[Any]] = None
        status = StartupStatus(not args.no_status, logger)
        try:
            if _reuse_existing(data_root, args, logger, wait=False):
                return 0
            stale = read_runtime_state(data_root)
            if stale:
                if process_is_running(stale["pid"]):
                    logger.warning("Stopping an unhealthy stale Ledger server")
                    stop_server_process(data_root, logger)
                runtime_file(data_root).unlink(missing_ok=True)

            if not args.demo and not args.data_dir:
                offer_legacy_import(data_root, logger)
            if args.demo:
                os.environ["LEDGER_DEMO_DB"] = "1"
                from scripts.create_demo_data import main as create_demo_data

                result = create_demo_data(
                    ["--out", str(data_root / "finance.demo.db"), "--force"]
                )
                if result != 0:
                    raise RuntimeError("Demo data creation failed.")

            strict_port = args.port is not None
            port = find_available_port(args.port, strict=strict_port)
            control_port = find_available_port(0)
            while control_port == port:
                control_port = find_available_port(0)
            logger.info("Selected server port: %s", port)
            logger.info("Selected control port: %s", control_port)
            status.update("Starting Ledger's private local server...")
            process = start_server_process(data_root, port, control_port, logger)
            url = wait_for_server(data_root, process, status, logger)
            if not args.no_browser:
                status.update("Opening Ledger in your browser...")
                open_default_browser(url, logger)
            return 0
        except Exception:
            logger.exception("Ledger startup failed")
            if process is not None and process.poll() is None:
                state = read_runtime_state(data_root)
                if state:
                    send_control(state["control_port"], "SHUTDOWN")
            show_startup_error(
                "Ledger encountered an error while starting.",
                destination,
                no_dialog=args.no_dialog,
            )
            return 1
        finally:
            status.close()
            release_launch_mutex(mutex)
    except Exception:
        logger.exception("Ledger launcher failed")
        show_startup_error(
            "Ledger encountered an error while starting.",
            destination,
            no_dialog=args.no_dialog,
        )
        return 1


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Ledger for Windows.")
    parser.add_argument("--data-dir", help="Private data-root override")
    parser.add_argument("--port", type=int, help="Fixed localhost port")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--shutdown", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--control-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--no-status", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-dialog", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.serve:
            return run_server(args)
        return run_launcher(args)
    except Exception:
        # Python initialized, so do one last best-effort log/dialog pass. This
        # covers failures that happen before the normal launcher logger exists.
        fallback_root = default_data_root()
        destination = log_path(fallback_root)
        try:
            logger = configure_logging(fallback_root)
            logger.exception("Ledger failed before normal launcher startup")
        except Exception:
            pass
        show_startup_error(
            "Ledger encountered an error before startup could complete.",
            destination,
            no_dialog=args.no_dialog,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

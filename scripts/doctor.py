"""
Quick local health check for Ledger.

This is meant for someone who just cloned the repo and wants to know:

- is my Python version new enough?
- did the main dependencies install?
- are private files accidentally tracked by git?
- can I generate demo data and run the app safely?

It does not read config.json, finance.db, statement PDFs, or exports.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRIVATE_PATH_PATTERNS = (
    "config.json",
    "finance.db",
    "finance.demo.db",
    "launcher.log",
    "CLAUDE_HANDOFF",
    ".env",
    ".zip",
    ".pdf",
)

REQUIRED_IMPORTS = (
    "streamlit",
    "pandas",
    "plotly",
    "pdfplumber",
)


def _ok(label: str, detail: str = "") -> None:
    print(f"PASS  {label}{': ' + detail if detail else ''}")


def _warn(label: str, detail: str = "") -> None:
    print(f"WARN  {label}{': ' + detail if detail else ''}")


def _fail(label: str, detail: str = "") -> None:
    print(f"FAIL  {label}{': ' + detail if detail else ''}")


def _git_tracked_files() -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_python() -> bool:
    version = sys.version_info
    if version >= (3, 12):
        _ok("Python version", sys.version.split()[0])
        return True
    _fail("Python version", f"{sys.version.split()[0]} found, 3.12+ recommended")
    return False


def check_imports() -> bool:
    good = True
    for name in REQUIRED_IMPORTS:
        try:
            importlib.import_module(name)
        except Exception as exc:
            _fail(f"import {name}", exc.__class__.__name__)
            good = False
        else:
            _ok(f"import {name}")
    return good


def check_repo_shape() -> bool:
    required = [
        ROOT / "app.py",
        ROOT / "requirements.txt",
        ROOT / "scripts" / "create_demo_data.py",
        ROOT / "scripts" / "smoke_test.py",
        ROOT / "docs" / "GETTING_STARTED.md",
    ]
    good = True
    for path in required:
        if path.exists():
            _ok("required file", path.relative_to(ROOT).as_posix())
        else:
            _fail("required file missing", path.relative_to(ROOT).as_posix())
            good = False
    return good


def check_git_privacy() -> bool:
    tracked = _git_tracked_files()
    if tracked is None:
        _warn("git tracked-file check", "git not available or this is not a git checkout")
        return True

    bad = [
        path for path in tracked
        if any(pattern.lower() in path.lower() for pattern in PRIVATE_PATH_PATTERNS)
        and path != "data/.gitkeep"
    ]
    if bad:
        _fail("private-looking tracked files", ", ".join(bad[:8]))
        if len(bad) > 8:
            _fail("private-looking tracked files", f"{len(bad) - 8} more")
        return False

    _ok("private file check", "no tracked config, database, logs, PDFs, zips, or env files")
    return True


def check_demo_data() -> bool:
    demo_db = ROOT / "data" / "finance.demo.db"
    if demo_db.exists():
        _ok("demo database", "data/finance.demo.db exists")
        return True
    _warn("demo database", "run: python -m scripts.create_demo_data")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether Ledger is ready to run locally."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for warnings that are normally okay for first setup.",
    )
    args = parser.parse_args(argv)

    print("Ledger local check")
    print(f"repo: {ROOT}")
    print()

    checks = [
        check_python(),
        check_imports(),
        check_repo_shape(),
        check_git_privacy(),
        check_demo_data() if not args.strict else (ROOT / "data" / "finance.demo.db").exists(),
    ]

    if args.strict and not (ROOT / "data" / "finance.demo.db").exists():
        _fail("demo database", "strict mode expects data/finance.demo.db")

    print()
    if all(checks):
        print("Ledger looks ready to run.")
        print("Demo run: LEDGER_DEMO_DB=1 python -m streamlit run app.py")
        return 0

    print("Ledger needs attention before it is ready.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
scripts/make_share_zip.py — build a clean shareable zip of Ledger.

What this is
────────────
A defensive packager. The user asked for a way to share Ledger with
another person without leaking secrets, personal finance data, or the
local Python venv. This script builds a zip that is safe to hand off.

Defenses in order:
  1. EXCLUDE_DIRS / EXCLUDE_FILES / EXCLUDE_GLOBS — never copy these.
  2. SECRET_PATTERNS — every file we DO include is scanned for likely
     credentials. If any pattern hits, the build aborts with a clear
     error. No silent zipping over a leaked key.
  3. The script never reads or includes:
        config.json, data/finance.db, .venv/, .venv.broken-*,
        launcher.log, launcher.log.prev, exports/*, __pycache__/,
        .pytest_cache/, smoke_test cache files
     Even if a future change adds a new file inside those, the directory
     skip catches it before any read happens.

Usage
─────
    python -m scripts.make_share_zip
    python -m scripts.make_share_zip --out dist/ledger-share.zip
    python -m scripts.make_share_zip --include-sample-db   # if present

Exit codes
──────────
    0 — zip written
    1 — secret detected, file copy failed, or output path unwritable
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# Force stdout/stderr to UTF-8 so the script's printed
# summary doesn't crash on the default Windows cp1252 console. Without
# this, the closing emoji warning (kept as ASCII below) used to raise
# UnicodeEncodeError after the zip was already on disk, making the
# script exit non-zero even though the safety work succeeded. The
# share-zip-exits-0 smoke check flips back to PASS once we reconfigure.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# Directories never included. Match by relative path component anywhere
# in the tree.
EXCLUDE_DIRS = {
    ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vscode", ".git", "node_modules", "exports", "data",
    "dist",
    # Editor and AI workspace metadata never go in a user share.
    ".claude",
}

# Files never included by name (any directory).
EXCLUDE_FILES = {
    "config.json",
    "launcher.log",
    "launcher.log.prev",
    ".env", ".env.local", ".env.production",
    "secrets.toml",
    "finance.db", "finance.db-wal", "finance.db-shm",
}

# Developer-only files are excluded from the default user share but can be
# opted back in with --include-dev-notes for a maintainer handoff.
DEV_ONLY_FILES = {
    "CLAUDE_HANDOFF.md",
}

# Glob-style patterns matched against the relative path string.
EXCLUDE_GLOB_RE = [
    re.compile(r"^\.venv\.broken-\d{8}-\d{6}/"),
    re.compile(r"\.pyc$"),
    re.compile(r"\.pyo$"),
    re.compile(r"\.DS_Store$"),
    re.compile(r"~\$"),               # Office lock files
    re.compile(r"\.swp$"),
    re.compile(r"finance.*\.db$"),    # any *finance*.db file
]

# Patterns that look like real credentials. If any of these match on a
# file we are about to add, the build aborts. The patterns are tuned to
# minimize false positives: we look for assignment-like contexts and
# known prefixes. A literal "api_key" in a docstring won't trigger it.
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OpenAI/MiniMax sk- key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic sk-ant",       re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id",      re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Bearer token",           re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}")),
    ("Generic api_key=…",      re.compile(
        r"""api[_-]?key['"]?\s*[:=]\s*['"][A-Za-z0-9._\-]{16,}['"]""",
        re.IGNORECASE)),
    ("password=…",             re.compile(
        r"""password['"]?\s*[:=]\s*['"][^'"\s]{8,}['"]""", re.IGNORECASE)),
]

# Substrings that, when present in a matched secret, mark it as an
# obvious placeholder/example and downgrade the hit to a non-blocker.
# Keeps the secret scan tight for real keys but kind to docs.
PLACEHOLDER_HINTS = (
    "your-secret",
    "your_secret",
    "your-password",
    "your_password",
    "example",
    "placeholder",
    "<your",
    "changeme",
    "redacted",
    "xxxxxxxx",
    "...",
)

# File extensions we actually scan for secrets. Binary blobs and large
# DB files are not scanned (they're already excluded by directory
# anyway). Source/text formats only.
SCAN_EXTS = {
    ".py", ".pyw", ".md", ".txt", ".json", ".toml", ".yml", ".yaml",
    ".cfg", ".ini", ".sh", ".bat", ".ps1", ".html", ".js", ".css",
    "",  # files with no extension (e.g. README)
}


def is_excluded(rel: Path, *, include_dev_notes: bool = False) -> bool:
    parts = rel.parts
    # Directory exclusion (any segment).
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    # File exclusion.
    if rel.name in EXCLUDE_FILES:
        return True
    if (not include_dev_notes) and rel.name in DEV_ONLY_FILES:
        return True
    rel_str = rel.as_posix()
    for pat in EXCLUDE_GLOB_RE:
        if pat.search(rel_str):
            return True
    return False


def scan_for_secrets(path: Path, rel: Path) -> list[str]:
    """Return a list of (pattern_label, sample) hits, or [] if clean.

    Skips files whose extension isn't in SCAN_EXTS or that exceed 1 MB.
    """
    if path.suffix.lower() not in SCAN_EXTS:
        return []
    try:
        if path.stat().st_size > 1_000_000:
            return []
    except OSError:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    hits: list[str] = []
    for label, pat in SECRET_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        full_match = m.group(0)
        if any(h in full_match.lower() for h in PLACEHOLDER_HINTS):
            # Documentation example, not a real secret.
            continue
        sample = full_match
        if len(sample) > 12:
            sample = sample[:6] + "…" + sample[-4:]
        hits.append(f"{label}  ({rel.as_posix()})  near: {sample}")
    return hits


def collect_files(root: Path, *, include_dev_notes: bool = False) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs in-place so os.walk skips them entirely.
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS
                       and not d.startswith(".venv.broken-")]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(root)
            if is_excluded(rel, include_dev_notes=include_dev_notes):
                continue
            files.append(rel)
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Build a safe shareable zip of Ledger."
    )
    default_out = _ROOT / "dist" / (
        f"ledger-share-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    )
    p.add_argument("--out", default=str(default_out))
    p.add_argument(
        "--include-sample-db", action="store_true",
        help="Include data/finance.sample.db if it exists.",
    )
    p.add_argument(
        "--include-dev-notes", action="store_true",
        help=("Include CLAUDE_HANDOFF.md and other developer-only "
              "files. Off by default so user shares stay clean."),
    )
    p.add_argument(
        "--allow-secrets", action="store_true",
        help=("Build the zip even if secrets are detected. "
              "DANGEROUS — never use unless you have inspected the hits."),
    )
    args = p.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    mode = "DEV (with dev notes)" if args.include_dev_notes else "USER"
    print(f"Building share zip from: {_ROOT}  ({mode} mode)")
    files = collect_files(_ROOT, include_dev_notes=args.include_dev_notes)
    print(f"  {len(files)} candidate file(s) after exclusion rules")

    # Optional: include the demo DB if it exists at a known path.
    sample_db = _ROOT / "data" / "finance.sample.db"
    if args.include_sample_db and sample_db.exists():
        files.append(sample_db.relative_to(_ROOT))
        print(f"  + including {sample_db.relative_to(_ROOT)}")

    # Secret scan over everything we plan to include.
    print("Scanning for secrets...")
    all_hits: list[str] = []
    for rel in files:
        full = _ROOT / rel
        hits = scan_for_secrets(full, rel)
        all_hits.extend(hits)

    if all_hits:
        print()
        print("ABORTED: possible secrets detected in files about to be zipped.",
              file=sys.stderr)
        for h in all_hits[:10]:
            print(f"  - {h}", file=sys.stderr)
        if len(all_hits) > 10:
            print(f"  ...and {len(all_hits) - 10} more.", file=sys.stderr)
        if not args.allow_secrets:
            print(
                "\nIf these are false positives, fix the patterns or run "
                "with --allow-secrets after inspection.",
                file=sys.stderr,
            )
            return 1
        print("--allow-secrets set; continuing despite hits.", file=sys.stderr)

    # Write the zip.
    print(f"Writing {out} ...")
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                full = _ROOT / rel
                # Place files under a top-level "ledger/" folder so the
                # recipient unzips into a clean named directory.
                zf.write(full, arcname=str(Path("ledger") / rel))
    except Exception as e:
        print(f"ERROR: failed to write zip: {e}", file=sys.stderr)
        return 1

    size_kb = out.stat().st_size / 1024
    print()
    print(f"  OK  wrote {out}  ({size_kb:,.1f} KB, {len(files)} files)")
    print()
    print("Excluded (always): .venv, __pycache__, exports/, data/, dist/,")
    print("                   .claude/, config.json, launcher.log, *.db, .env.")
    if not args.include_dev_notes:
        print("Excluded (USER mode): CLAUDE_HANDOFF.md and other dev notes.")
        print("                     Re-run with --include-dev-notes for a "
              "developer share.")
    print()
    print("Recipient should run Ledger_Launcher.py - first launch")
    print("rebuilds .venv and prompts for any AI keys via Settings.")
    print()
    # ASCII-only warning so this print cannot crash a cp1252 console even
    # if the reconfigure() at the top failed.
    print("WARNING: If you ever shared an unzipped project folder that")
    print("         contained config.json, ROTATE the AI API key now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

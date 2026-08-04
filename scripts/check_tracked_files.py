"""Refuse to track anything that could carry real financial data.

This replaces an inline regex in the CI workflow that was narrower than it
looked. It matched `finance.*\\.db($|-)`, which requires the path to end at
`.db` or continue with a hyphen, so a scratch database copy whose name
continued past `.db` passed the check and reached a public branch.
It also matched only files named `finance*`, so any other `.sqlite` file was
invisible, and it never looked at `.csv` at all even though bank statements are
this app's main private input.

This checks the **current tree only**. It cannot see git history, so a clean
result does not mean a private file was never committed. Removing something
from history is a separate, deliberate operation.

Run directly:

    python -m scripts.check_tracked_files
"""
from __future__ import annotations

import re
import subprocess
import sys

# Exact paths only. Directory-wide exemptions were how `docs/finance.db`,
# `docs/real-statement.pdf` and `tests/fixtures/private-bank-export.csv` all
# read as safe: allowing a whole folder means the guard stops looking at what
# is in it. Every sanctioned file is named here individually.
ALLOWED_PATHS = frozenset({
    # Invented transaction data used by the categorization tests.
    "tests/fixtures/categorization_first_import.csv",
    "tests/fixtures/categorization_second_import.csv",
    # Templates with placeholder values and no secrets.
    ".streamlit/secrets.toml.template",
    "config.json.example",
})

# Extensions that can hold a real ledger, statement or export, wherever they
# appear in the name. `finance.db.backup`, `ledger.sqlite3.old` and
# `statement.csv.bak` all have to fail.
_PRIVATE_EXTENSIONS = (
    "db", "sqlite", "sqlite3", "csv", "ofx", "qfx", "xls", "xlsx", "pdf", "zip",
)
_EXTENSION_PATTERN = re.compile(
    r"\.(" + "|".join(_PRIVATE_EXTENSIONS) + r")(?=$|[.\-_])",
    re.IGNORECASE,
)

# Names that are private regardless of extension.
_NAME_PATTERNS = (
    re.compile(r"(^|/)config\.json$", re.IGNORECASE),
    re.compile(r"(^|/)\.env($|[.\-])", re.IGNORECASE),
    re.compile(r"launcher\.log", re.IGNORECASE),
    re.compile(r"CLAUDE_HANDOFF", re.IGNORECASE),
    re.compile(r"secrets?\.(toml|json|ya?ml)$", re.IGNORECASE),
    # An extensionless `finance` file is a database by another name. Files with
    # a real extension are judged by _PRIVATE_EXTENSIONS instead, so ordinary
    # source and docs such as finance_agent_prompt.md are unaffected.
    re.compile(r"(^|/)finance[^/.]*$", re.IGNORECASE),
)

def is_private_path(path: str) -> bool:
    """True when a tracked path could carry real financial or secret data.

    A `.sample`, `.example` or `.template` suffix is not a free pass. It used
    to be, which made `finance.db.sample` look safe. Only the exact paths in
    ALLOWED_PATHS are exempt.
    """
    candidate = str(path).strip().replace("\\", "/")
    if not candidate:
        return False
    if candidate in ALLOWED_PATHS:
        return False
    if _EXTENSION_PATTERN.search(candidate):
        return True
    return any(pattern.search(candidate) for pattern in _NAME_PATTERNS)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    offenders = sorted(path for path in tracked_files() if is_private_path(path))
    if offenders:
        print("Private artifact paths are tracked in git:")
        for path in offenders:
            print(f"  {path}")
        print(
            "\nRemove them from the index. A file deleted in a later commit is "
            "still in history and still public."
        )
        return 1
    print("No private artifact paths tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

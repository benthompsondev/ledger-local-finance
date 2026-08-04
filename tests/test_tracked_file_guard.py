"""The tracked-file guard must refuse private data paths and local artifacts."""

import pytest

from scripts.check_tracked_files import is_private_path


BLOCKED = [
    "data/invented.db.local-copy",
    "data/finance.db",
    "data/finance.db-wal",
    "data/finance.db.backup",
    "data/finance.demo.db",
    "finance.db",
    "data/ledger.sqlite",
    "data/ledger.sqlite3",
    "backup/my-ledger.sqlite3.old",
    "data/statements.csv",
    "downloads/chequing-july.csv",
    "exports/transactions.xlsx",
    "statements/visa.pdf",
    "data/account.ofx",
    "data/account.qfx",
    "share/ledger-bundle.zip",
    "config.json",
    ".env",
    ".env.local",
    "logs/launcher.log",
    "CLAUDE_HANDOFF.md",
    "secrets.toml",
    ".streamlit/secrets.toml",
    "docs/finance.db",
    "docs/real-statement.pdf",
    "docs/statements/visa.pdf",
    "tests/fixtures/private-bank-export.csv",
    "tests/fixtures/anything-not-explicitly-allowed.csv",
    "finance.db.sample",
    "data/finance.db.example",
    "exports/transactions.xlsx.template",
]

ALLOWED = [
    "tests/fixtures/categorization_first_import.csv",
    "tests/fixtures/categorization_second_import.csv",
    ".streamlit/secrets.toml.template",
    "config.json.example",
    "openclaw/finance_agent_prompt.md",
    "utils/financial_semantics.py",
    "utils/insights.py",
    "desktop/src/HomeView.tsx",
    "README.md",
    "docs/GETTING_STARTED.md",
    "src-tauri/tauri.conf.json",
    "package.json",
    "requirements.txt",
    "scripts/check_tracked_files.py",
]


@pytest.mark.parametrize("path", BLOCKED)
def test_private_paths_are_refused(path):
    assert is_private_path(path) is True, f"{path} should be blocked"


@pytest.mark.parametrize("path", ALLOWED)
def test_ordinary_paths_are_allowed(path):
    assert is_private_path(path) is False, f"{path} should be allowed"


def test_database_copy_with_an_extra_suffix_is_refused():
    assert is_private_path("data/invented.db.local-copy") is True


def test_only_exact_paths_are_exempt_not_whole_directories():
    from scripts.check_tracked_files import ALLOWED_PATHS

    for allowed in ALLOWED_PATHS:
        assert is_private_path(allowed) is False
        assert "/" not in allowed or not allowed.endswith("/"), (
            "ALLOWED_PATHS must contain files, not directory prefixes"
        )

    assert is_private_path("tests/fixtures/categorization_first_import.csv") is False
    assert is_private_path("tests/fixtures/categorization_third_import.csv") is True


def test_the_guard_only_speaks_for_the_current_tree():
    import scripts.check_tracked_files as guard

    assert "history" in (guard.__doc__ or "").lower(), (
        "the module should say plainly that it checks the working tree only"
    )


def test_backslash_paths_are_normalized():
    assert is_private_path(r"data\invented.db.local-copy") is True


def test_the_current_tree_is_clean():
    from scripts.check_tracked_files import tracked_files

    offenders = [path for path in tracked_files() if is_private_path(path)]
    assert offenders == [], f"private paths tracked: {offenders}"

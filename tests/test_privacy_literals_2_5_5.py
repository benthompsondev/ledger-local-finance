"""Keep known private identifiers out of the tracked tree.

The repository path guard refuses private files. These checks cover content in
tracked text files without storing the private values they are meant to catch.
Only one-way SHA-256 fingerprints are retained here.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess

import pytest


REPO = pathlib.Path(__file__).resolve().parents[1]

_PRIVATE_TEN_DIGIT_FINGERPRINTS = {
    "0307c82c46370dec36163cb76d737cb09e7b596e874b023c709cc72e0e767155",
    "e3380566c01cb63f58f7c7e4bfcc671c924d3dc17824ffcadaab434ec800f869",
    "123203e462b6b2affd854519705adbba33e53277b7125e808ebd964ecdfc01b5",
}
_PRIVATE_ARTIFACT_FINGERPRINT = (
    "94b24c35ed52a824aa66590deb48402043f763f9a7525a28f132f02e5b994cf7"
)
_PRIVATE_OBJECT_FINGERPRINT = (
    "371f02dbc32d99804414399733ed3b9beebfa9b53495626435106441bfc16bb9"
)
_PRIVATE_ARTIFACT_LENGTH = 30

_SANITIZED_SOURCES = (
    "parsers/detect.py",
    "parsers/tangerine_chequing.py",
    "parsers/tangerine_savings.py",
)
_TEXT_SUFFIXES = {
    ".css", ".html", ".ini", ".js", ".json", ".md", ".mjs", ".ps1",
    ".py", ".rs", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tracked_text_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True,
        cwd=REPO,
    )
    return [
        line for line in result.stdout.splitlines()
        if line.strip() and pathlib.Path(line).suffix.lower() in _TEXT_SUFFIXES
    ]


def _read(relative: str) -> str:
    try:
        return (REPO / relative).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def test_there_are_files_to_scan() -> None:
    assert len(_tracked_text_files()) >= 50


def test_known_private_identifiers_never_return() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        content = _read(path)
        for digit_run in re.findall(r"\d{10,}", content):
            windows = (
                digit_run[index:index + 10]
                for index in range(len(digit_run) - 9)
            )
            if any(
                _fingerprint(window) in _PRIVATE_TEN_DIGIT_FINGERPRINTS
                for window in windows
            ):
                offenders.append(path)
                break

    assert not offenders, f"known private identifier found in: {offenders}"


def test_private_artifact_name_never_returns() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        content = _read(path).lower()
        windows = (
            content[index:index + _PRIVATE_ARTIFACT_LENGTH]
            for index in range(len(content) - _PRIVATE_ARTIFACT_LENGTH + 1)
        )
        if any(_fingerprint(window) == _PRIVATE_ARTIFACT_FINGERPRINT for window in windows):
            offenders.append(path)

    assert not offenders, f"private artifact name found in: {offenders}"


def test_private_object_identifier_never_returns() -> None:
    offenders: list[str] = []
    for path in _tracked_text_files():
        candidates = re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", _read(path))
        if any(_fingerprint(candidate.lower()) == _PRIVATE_OBJECT_FINGERPRINT for candidate in candidates):
            offenders.append(path)

    assert not offenders, f"private object identifier found in: {offenders}"


@pytest.mark.parametrize("relative", _SANITIZED_SOURCES)
def test_parser_examples_carry_no_account_shaped_numbers(relative: str) -> None:
    offenders: list[str] = []
    for number, line in enumerate(_read(relative).splitlines(), start=1):
        for run in re.findall(r"\d{7,}", line):
            if set(run) != {"0"}:
                offenders.append(f"{relative}:{number}")

    assert not offenders, "account-shaped number found in: " + "; ".join(offenders)


def test_the_parsers_still_match_what_they_documented() -> None:
    from parsers.detect import _RE_CHQ_TABLE, _RE_SAV_TABLE
    from parsers.tangerine_savings import ORPHAN_ACCT_RE

    assert _RE_CHQ_TABLE.search("Tangerine Chequing Account 0000000000 1,000.00")
    assert _RE_SAV_TABLE.search("tangerine savings account 0000000000 12.00")
    assert not _RE_CHQ_TABLE.search("Tangerine Chequing Account 12 5.00")

    assert ORPHAN_ACCT_RE.match("- 0000000000")
    assert ORPHAN_ACCT_RE.match("0000000000")
    assert ORPHAN_ACCT_RE.match("- 9876543210"), (
        "a ten-digit orphan line must still be discarded whatever its digits"
    )
    assert not ORPHAN_ACCT_RE.match("- 987654")

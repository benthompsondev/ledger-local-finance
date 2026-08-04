"""Three small things that were visibly wrong on screen.

Source-level checks, because each is a rendering decision with no engine
behind it to assert against.
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
CHARTS = REPO / "desktop" / "src" / "charts.tsx"
AI_VIEW = REPO / "desktop" / "src" / "AiView.tsx"
TAURI_CONF = REPO / "src-tauri" / "tauri.conf.json"


def test_a_month_name_keeps_its_capital_letter() -> None:
    """The period label read "july 2026 through day 30".

    It is built from a month name, which is a proper noun in every sentence
    position, and it follows a separator anyway.
    """
    source = CHARTS.read_text(encoding="utf-8")

    assert "periodLabel.toLowerCase()" not in source
    assert "${periodLabel}" in source


def test_no_other_label_is_lowercased_on_the_way_out() -> None:
    """Anything else doing this to a user-facing label would read the same."""
    offenders = [
        f"{number}: {line.strip()[:90]}"
        for number, line in enumerate(
            CHARTS.read_text(encoding="utf-8").splitlines(), start=1,
        )
        if ".toLowerCase()" in line and "Label" in line
    ]

    assert not offenders, offenders


def test_coach_states_its_beta_limits_up_front() -> None:
    source = AI_VIEW.read_text(encoding="utf-8")

    assert "Beta: provider compatibility and AI answers may be incomplete" in source
    assert "calculated locally" in source
    # Still labelled in the eyebrow, and still says AI does no arithmetic.
    assert 'className="beta-tag"' in source
    assert "never calculates anything" in source


def test_the_beta_notice_sits_above_the_findings() -> None:
    """A warning under the results is a warning nobody reads first."""
    source = AI_VIEW.read_text(encoding="utf-8")

    notice = source.index("Beta: provider compatibility")
    # The rendered element, not the import at the top of the file.
    findings = source.index("<AnalysisContextNote")

    assert notice < findings


def test_the_tauri_config_has_no_blank_line_at_the_end() -> None:
    """One terminator, not two. Line-ending agnostic: the repo is CRLF on
    Windows and LF in CI, and either is fine as long as it appears once."""
    raw = TAURI_CONF.read_bytes()

    assert raw.rstrip(b"\r\n").endswith(b"}"), "config does not end with }"
    trailing = raw[len(raw.rstrip(b"\r\n")):]
    assert trailing in (b"\n", b"\r\n"), (
        f"expected one line terminator, found {trailing!r}"
    )


def test_the_tauri_config_is_still_valid_json() -> None:
    import json

    config = json.loads(TAURI_CONF.read_text(encoding="utf-8"))

    assert config["productName"] == "Northstar Ledger"
    assert config["plugins"]["updater"]["pubkey"]

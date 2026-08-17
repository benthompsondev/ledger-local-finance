"""Three small things that were visibly wrong on screen.

Source-level checks, because each is a rendering decision with no engine
behind it to assert against.
"""
from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
CHARTS = REPO / "desktop" / "src" / "charts.tsx"
AI_VIEW = REPO / "desktop" / "src" / "AiView.tsx"
PLAN_VIEW = REPO / "desktop" / "src" / "PlanView.tsx"
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


def test_every_explicit_plan_action_invalidates_older_previews() -> None:
    """Preset, save and refresh are all newer decisions than a queued preview."""
    source = PLAN_VIEW.read_text(encoding="utf-8")

    boundaries = {
        "refresh": ("const refresh =", "useEffect(() => { void refresh()"),
        "save": ("const save =", "if (!data && !error)"),
    }
    for handler, (opening, closing) in boundaries.items():
        start = source.index(opening)
        end = source.index(closing, start)
        body = source[start:end]
        assert "cancelPendingPreview()" in body, handler
        assert "gate.current.begin()" in body, handler


def test_preset_and_save_ignore_stale_results() -> None:
    source = PLAN_VIEW.read_text(encoding="utf-8")

    boundaries = {
        "save": ("const save =", "if (!data && !error)"),
    }
    for handler, (opening, closing) in boundaries.items():
        start = source.index(opening)
        end = source.index(closing, start)
        body = source[start:end]
        assert "if (!gate.current.isLatest(token)) return" in body, handler

        # A background refresh can supersede the response, but it must not
        # leave the explicit action's busy indicator stuck on forever.
        assert "if (gate.current.isLatest(token)) setBusy(false)" not in body
        assert "setBusy(false);" in body

    # Every editable input is locked while an explicit action is running.
    assert source.count("disabled={busy}") >= 3


def test_save_repreviews_the_current_form_before_persisting() -> None:
    """Clicking Save inside the debounce window must not send stale flexible room."""
    source = PLAN_VIEW.read_text(encoding="utf-8")
    start = source.index("const save =")
    end = source.index("if (!data && !error)", start)
    body = source[start:end]

    preview = body.index("await previewPlan(")
    save = body.index("await savePlan(")
    assert preview < save
    # The allowance is no longer sent at all; the re-preview exists so the
    # coherence check runs against the values actually being saved.
    assert "flexibleAllowance" not in body
    assert "if (!currentPreview.equation.coherent)" in body


def test_live_preview_pauses_while_an_explicit_action_is_busy() -> None:
    source = PLAN_VIEW.read_text(encoding="utf-8")
    comment = source.index("// An explicit action")
    start = source.rindex("useEffect(() => {", 0, comment)
    effect = source[start:source.index("const save =")]
    assert "if (!data || busy) return" in effect
    assert "form.mode, busy]" in effect


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

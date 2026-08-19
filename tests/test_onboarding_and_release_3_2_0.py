"""Getting started, the placeholder, and the permanent download name.

These are source-level assertions on purpose. The onboarding decision lives
in the React shell, which pytest cannot render, so what is pinned here is the
shape of the decision: which profile it belongs to, when it is allowed to
appear, and that dismissing it is remembered.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "desktop" / "src"
APP = (SRC / "App.tsx").read_text(encoding="utf-8")
HOME = (SRC / "HomeView.tsx").read_text(encoding="utf-8")
GUIDE = (SRC / "GettingStarted.tsx").read_text(encoding="utf-8")
PREFS = (SRC / "preferences.ts").read_text(encoding="utf-8")
SETTINGS = (SRC / "SettingsView.tsx").read_text(encoding="utf-8")
PROFILES_PANEL = (SRC / "ProfilesPanel.tsx").read_text(encoding="utf-8")
BUILD = (REPO / "scripts" / "build_native_desktop.ps1").read_text(encoding="utf-8")


# ── onboarding appears only when it should ──────────────────────────────

def test_the_guide_is_limited_to_a_profile_holding_nothing() -> None:
    """An established set of finances must never be interrupted by it."""
    assert "profile.transactions===0" in APP
    assert "readGettingStartedDismissed(profile.id)" in APP


def test_the_guide_is_dismissible_and_the_answer_is_remembered() -> None:
    assert "saveGettingStartedDismissed" in GUIDE
    assert "Dismiss" in GUIDE


def test_dismissal_is_recorded_per_profile() -> None:
    """One person finishing setup must not silence a new empty profile."""
    assert 'const GETTING_STARTED_PREFIX = "spendshape.gettingStarted.";' in PREFS
    assert "GETTING_STARTED_PREFIX + profileId" in PREFS
    # An empty id must not write a shared key that every profile then reads.
    assert 'if (!profileId) return false;' in PREFS
    assert 'if (!profileId) return;' in PREFS


def test_the_guide_can_be_reopened_from_settings() -> None:
    assert "onShowGuide" in SETTINGS
    assert "Show the getting started guide" in SETTINGS
    assert "onShowGuide={reopenGuide}" in APP
    assert "saveGettingStartedDismissed(profile.id,false)" in APP


def test_home_renders_the_guide_above_everything_else() -> None:
    """An empty profile has no freshness or check-in worth reading first."""
    guide_at = HOME.index("<GettingStarted")
    error_at = HOME.index('className="error-panel"')
    assert guide_at < error_at


def test_a_reset_clears_every_profiles_guide_state() -> None:
    body = PREFS[PREFS.index("export function clearSignalSpacePreferences"):]
    assert "GETTING_STARTED_PREFIX" in body


# ── what the guide actually says ────────────────────────────────────────

def test_csv_is_recommended_and_pdf_is_called_beta() -> None:
    assert "<b>CSV</b>" in GUIDE
    assert "PDF statements are Beta" in GUIDE
    assert "fail on many banks" in GUIDE


def test_the_guide_does_not_promise_every_bank_works() -> None:
    assert "a few will not work at all" in GUIDE
    assert "refuses it rather than guessing" in GUIDE


def test_the_guide_says_where_the_data_lives_and_that_there_is_no_account() -> None:
    assert "no account to create" in GUIDE
    assert "keeps a database file on this machine" in GUIDE


def test_plan_is_explained_rather_than_demanded() -> None:
    """Plan needs finished months, so day one must not ask for one."""
    assert "Plan comes later" in GUIDE
    assert "a few complete months behind it" in GUIDE


# ── placeholder ─────────────────────────────────────────────────────────

def test_no_personal_name_ships_as_a_profile_placeholder() -> None:
    assert "Tori" not in PROFILES_PANEL
    assert 'placeholder="My second profile"' in PROFILES_PANEL


# ── permanent download name ─────────────────────────────────────────────

def test_the_build_emits_a_stable_installer_name() -> None:
    """Public download buttons resolve one filename in every release."""
    assert "SignalSpaceFinance-setup.exe" in BUILD


def test_the_stable_copy_is_verified_against_the_versioned_one() -> None:
    assert "Stable installer copy does not match the versioned installer." in BUILD


def test_the_versioned_installer_is_still_produced() -> None:
    """The updater manifest and its .sig refer to the versioned filename."""
    assert 'SignalSpaceFinance_${Version}_x64-setup.exe' in BUILD
    # A copy, never a rename: renaming would strand the detached signature.
    assert "Copy-Item -LiteralPath $FinalInstaller" in BUILD

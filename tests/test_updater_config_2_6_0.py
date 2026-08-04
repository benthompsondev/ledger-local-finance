"""The updater must stay narrow, signed, and silent until asked.

An updater is the one part of a local-first finance app that deliberately
reaches the internet, so the interesting properties are all about restraint:
it must not check on its own, must not be able to reach anywhere except
Northstar's own releases, must not be able to install anything that is not
signed by Northstar's key, and must not have been handed general network,
shell or filesystem access on the way in.

These read the shipped configuration and source rather than a live GitHub, so
they run offline and cannot pass by accident.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TAURI_CONF = REPO / "src-tauri" / "tauri.conf.json"
CAPABILITIES = REPO / "src-tauri" / "capabilities" / "main.json"
CARGO = REPO / "src-tauri" / "Cargo.toml"
MAIN_RS = REPO / "src-tauri" / "src" / "main.rs"
PANEL = REPO / "desktop" / "src" / "UpdatePanel.tsx"
UPDATER = REPO / "desktop" / "src" / "updater.ts"
SETTINGS = REPO / "desktop" / "src" / "SettingsView.tsx"

REPO_URL = "https://github.com/benthompsondev/ledger-local-finance"
MANIFEST_URL = f"{REPO_URL}/releases/latest/download/latest.json"

# Keys belonging to other projects of Ben's, as one-way fingerprints. Sharing
# an updater key across projects means one compromise is both, so this refuses
# the reuse without carrying the key itself into Northstar's public tree.
_FOREIGN_KEY_FINGERPRINTS = {
    "ffc7f7c8b7e9708e961b206140fd280917b5f90538cea432310a38e5de5ae117",
}


@pytest.fixture(scope="module")
def conf() -> dict:
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capabilities() -> dict:
    return json.loads(CAPABILITIES.read_text(encoding="utf-8"))


# ── where it may look ────────────────────────────────────────────────────

def test_the_endpoint_is_northstars_own_release_manifest(conf) -> None:
    endpoints = conf["plugins"]["updater"]["endpoints"]

    assert endpoints == [MANIFEST_URL], (
        "the update endpoint is not Northstar's own release manifest"
    )


def test_the_endpoint_is_fixed_and_not_templated(conf) -> None:
    """A templated endpoint is one config change away from anywhere."""
    for endpoint in conf["plugins"]["updater"]["endpoints"]:
        assert endpoint.startswith("https://"), endpoint
        assert "{{" not in endpoint, "the endpoint is templated"
        assert endpoint.startswith(REPO_URL), endpoint


def test_updater_artifacts_are_produced_by_the_build(conf) -> None:
    assert conf["bundle"]["createUpdaterArtifacts"] is True, (
        "without this the build produces no archive for the updater to install"
    )


# ── what it will accept ──────────────────────────────────────────────────

def test_the_signing_key_belongs_to_northstar(conf) -> None:
    """Another project's key must never be pasted in here."""
    import hashlib

    pubkey = conf["plugins"]["updater"]["pubkey"]
    fingerprint = hashlib.sha256(pubkey.encode("utf-8")).hexdigest()

    assert fingerprint not in _FOREIGN_KEY_FINGERPRINTS, (
        "the updater is configured with another project's signing key"
    )


def test_the_key_is_either_absent_or_a_real_key(conf) -> None:
    """Empty means nothing verifies, so nothing installs. That is the safe
    state, and it is the state 2.6.0 ships in until the production key is
    generated.

    What must never appear is a plausible-looking placeholder: that would read
    as configured while trusting a key nobody holds, which is the one outcome
    worse than having no updater at all.
    """
    pubkey = conf["plugins"]["updater"]["pubkey"]
    assert isinstance(pubkey, str)

    if pubkey == "":
        return  # fail-closed: Tauri can verify nothing, so it accepts nothing

    assert len(pubkey) > 40, "pubkey is set but too short to be a real key"
    assert re.fullmatch(r"[A-Za-z0-9+/=]+", pubkey), (
        "pubkey is not base64; a comment or path was pasted in"
    )
    for placeholder in ("TODO", "CHANGEME", "PLACEHOLDER", "XXXX"):
        assert placeholder not in pubkey.upper(), "the pubkey is a placeholder"


def test_signature_verification_is_never_disabled(conf) -> None:
    updater = conf["plugins"]["updater"]

    for unsafe in ("dangerousInsecureTransportProtocol", "insecure", "skipVerify"):
        assert unsafe not in updater, f"{unsafe} is set on the updater"


# ── what it was granted ──────────────────────────────────────────────────

def test_only_the_updater_and_restart_were_granted(capabilities) -> None:
    granted = set(capabilities["permissions"]) - {
        p for p in capabilities["permissions"] if isinstance(p, str) and p.startswith("allow-")
    }

    assert "updater:default" in capabilities["permissions"]
    assert "process:allow-restart" in capabilities["permissions"]
    # core:app:allow-version predates this work and reads only the version.
    assert granted <= {"updater:default", "process:allow-restart",
                       "core:app:allow-version"}, granted


def test_no_general_network_shell_or_filesystem_access(capabilities) -> None:
    """The point of a narrow capability file is that it stays narrow.

    Only what was actually granted counts; the description deliberately names
    the things Northstar does *not* take, so it is excluded from the scan.
    """
    blob = json.dumps(capabilities["permissions"])

    for forbidden in (
        "http:default", "http:allow-fetch", "shell:allow-execute",
        "shell:allow-open", "shell:default", "fs:default", "fs:allow-read",
        "fs:allow-write", "process:allow-exit", "webview:", "window:allow-",
        "clipboard", "devtools",
    ):
        assert forbidden not in blob, f"{forbidden} was granted"


def test_no_arbitrary_url_opener_was_granted(capabilities) -> None:
    for permission in capabilities["permissions"]:
        identifier = (
            permission if isinstance(permission, str)
            else permission.get("identifier", "")
        )
        if not identifier.startswith("opener:"):
            continue
        # If an opener is ever added it has to be scoped to Northstar's repo.
        scope = json.dumps(permission)
        assert REPO_URL in scope, "an unscoped URL opener was granted"


def test_process_may_restart_but_not_anything_else(capabilities) -> None:
    process_grants = [
        p for p in capabilities["permissions"]
        if isinstance(p, str) and p.startswith("process:")
    ]

    assert process_grants == ["process:allow-restart"], process_grants


def test_the_plugins_are_actually_registered() -> None:
    source = MAIN_RS.read_text(encoding="utf-8")

    assert "tauri_plugin_updater::Builder::new().build()" in source
    assert "tauri_plugin_process::init()" in source
    assert 'tauri-plugin-updater = "2"' in CARGO.read_text(encoding="utf-8")
    assert 'tauri-plugin-process = "2"' in CARGO.read_text(encoding="utf-8")


# ── when it may run ──────────────────────────────────────────────────────

def test_nothing_checks_for_updates_on_mount() -> None:
    """Opening Northstar must make no GitHub request at all."""
    source = PANEL.read_text(encoding="utf-8")

    # No effect hook exists in the panel, so there is nothing that could fire
    # on mount, on an interval, or on a dependency change.
    assert "useEffect" not in source, (
        "the update panel gained an effect; it must only act on a click"
    )
    assert "setInterval" not in source and "setTimeout" not in source, (
        "the update panel gained a timer"
    )
    for auto in ("checkOnStartup", "autoCheck", "checkAutomatically"):
        assert auto not in source


def test_the_check_only_happens_from_a_click() -> None:
    source = PANEL.read_text(encoding="utf-8")

    # Every call site of checkNow is an onClick handler.
    call_sites = re.findall(r"([\w.\s=>{()]*)checkNow\(\)", source)
    assert call_sites, "checkNow is never called"
    for site in call_sites:
        assert "onClick" in site or "const checkNow" in site or "void" in site


def test_no_finance_data_is_sent_with_the_check() -> None:
    """The request carries a version and nothing else."""
    source = UPDATER.read_text(encoding="utf-8")

    for leak in (
        "transactions", "accounts", "balance", "net_worth", "safe_to_spend",
        "telemetry", "analytics", "machineId", "deviceId", "userId",
    ):
        assert leak not in source, f"the updater references {leak}"


def test_the_check_is_bounded_by_a_timeout() -> None:
    source = UPDATER.read_text(encoding="utf-8")

    assert "CHECK_TIMEOUT_MS" in source
    assert "timeout:" in source, "the check can hang forever"


def test_a_downgrade_is_refused_even_if_the_manifest_offers_one() -> None:
    source = UPDATER.read_text(encoding="utf-8")

    assert "isNewerVersion" in source, (
        "nothing compares the offered version against the installed one"
    )
    assert "update.close()" in source, "a rejected update is not released"


# ── what a person is told ────────────────────────────────────────────────

def test_every_state_the_brief_requires_is_rendered() -> None:
    source = PANEL.read_text(encoding="utf-8")

    for kind in ("idle", "checking", "latest", "available", "installing",
                 "restart", "error"):
        assert f'"{kind}"' in source, f"no {kind} state"
    # Restart is explicit, never automatic.
    assert "Restart Northstar" in source
    assert "relaunch" in source


def test_the_privacy_note_sits_beside_the_button() -> None:
    source = PANEL.read_text(encoding="utf-8")

    assert "only when you press" in source
    assert "background" in source
    assert "signed" in source.lower()


def test_settings_no_longer_claims_there_is_no_connection() -> None:
    """"Connection: None" stopped being true the moment this shipped."""
    source = SETTINGS.read_text(encoding="utf-8")

    assert "None. Local sidecar only." not in source
    assert "checking for updates contacts GitHub" in source
    assert "UpdatePanel" in source

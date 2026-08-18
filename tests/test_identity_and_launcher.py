"""The visible name, and the fact that the app can actually be started.

Two things are pinned here. The first is the rename boundary: what a person
sees says SignalSpace Finance, while every identifier an upgrade depends on
keeps its original value, because renaming those orphans existing data.

The second is the launcher, which is here because it broke in 3.0.0 and
nothing caught it. Tauri only creates a Start Menu shortcut when it is not in
update mode, and it only migrates an existing one that already carries the
current product name. A rename guarantees neither condition holds: the old
shortcut is filed under the old name, so nothing is migrated, nothing is
created, and the installer hook's legacy cleanup then deletes the only
shortcut that existed. The result was an installation with no Start Menu
entry, invisible to Windows Search, launchable only by browsing to AppData.
"""
from __future__ import annotations

from pathlib import Path

from scripts.verify_installed_app import PRODUCT_NAME, _is_product_entry

ROOT = Path(__file__).resolve().parents[1]
HOOKS = (ROOT / "src-tauri" / "windows" / "hooks.nsh").read_text(encoding="utf-8")
CONFIG = (ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")

# Every visible name the product has shipped under. Each one can still be
# sitting in a Start Menu or an uninstall key on a real machine.
LEGACY_VISIBLE_NAMES = ("Ledger", "Northstar Ledger", "SpendShape")


def test_the_visible_name_is_the_current_one():
    assert PRODUCT_NAME == "SignalSpace Finance"
    assert '"productName": "SignalSpace Finance"' in CONFIG
    assert '"title": "SignalSpace Finance"' in CONFIG


def test_the_verifier_knows_the_current_and_every_legacy_name():
    assert _is_product_entry(PRODUCT_NAME, PRODUCT_NAME)
    for legacy in LEGACY_VISIBLE_NAMES:
        assert _is_product_entry(legacy, legacy), legacy
    assert not _is_product_entry("Budget Helper", "Budget Helper")
    assert not _is_product_entry("SignalSpace Notes", "SignalSpace Notes")


def test_installer_cleanup_covers_every_legacy_visible_name():
    for old_name in LEGACY_VISIBLE_NAMES:
        assert f'Uninstall\\{old_name}"' in HOOKS, old_name
        assert f"$SMPROGRAMS\\{old_name}.lnk" in HOOKS, old_name
        assert f"$DESKTOP\\{old_name}.lnk" in HOOKS, old_name


def test_the_installer_creates_a_start_menu_shortcut_itself():
    """The fix for 3.0.0, pinned.

    Relying on Tauri to do this is what failed. The hook must create the
    shortcut unconditionally, after the cleanup that would otherwise leave
    the machine with none.
    """
    assert 'CreateShortcut "$SMPROGRAMS\\${PRODUCTNAME}.lnk"' in HOOKS
    assert "${MAINBINARYNAME}.exe" in HOOKS

    create_at = HOOKS.index('CreateShortcut "$SMPROGRAMS\\${PRODUCTNAME}.lnk"')
    for old_name in LEGACY_VISIBLE_NAMES:
        delete_at = HOOKS.index(f"$SMPROGRAMS\\{old_name}.lnk")
        assert delete_at < create_at, (
            f"the {old_name} cleanup runs after the new shortcut is made, so "
            "it can delete what was just created"
        )


def test_the_shortcut_carries_an_app_user_model_id():
    """Otherwise a searched or pinned entry can group under a stale identity."""
    assert "SetLnkAppUserModelId" in HOOKS


def test_the_installed_app_check_fails_without_a_launcher():
    """A release gate that only proves files landed is not enough.

    verify_installed_app is the gate that runs against a real machine after
    installing. It has to look for the shortcut, or 3.0.0 happens again.
    """
    source = (ROOT / "scripts" / "verify_installed_app.py").read_text(
        encoding="utf-8"
    )
    assert "_launchers" in source
    assert "No Start Menu entry" in source
    assert "_has_embedded_icon" in source


def test_load_bearing_upgrade_identifiers_stay_legacy():
    """Renaming any of these orphans an existing installation's data."""
    shell = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")

    assert '"identifier": "dev.benthompson.ledger"' in CONFIG
    assert 'join("Ledger")' in shell
    assert "$LOCALAPPDATA\\Programs\\Ledger" in HOOKS
    # The binary name is what every existing shortcut and uninstall entry
    # points at, so it outlives the visible name too.
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    assert 'name = "ledger-native"' in cargo


def test_stored_preference_keys_keep_their_namespace():
    """Renaming these silently resets a user's saved choices."""
    prefs = (ROOT / "desktop" / "src" / "preferences.ts").read_text(
        encoding="utf-8"
    )
    assert "spendshape." in prefs, (
        "the stored-preference namespace is a compatibility surface, not "
        "branding"
    )

"""Upgrade and visible-name boundaries for the SpendShape rename."""
from __future__ import annotations

from pathlib import Path

from scripts.verify_installed_app import _is_product_entry


def test_installed_app_verifier_recognizes_current_and_legacy_names():
    assert _is_product_entry("SpendShape", "SpendShape")
    assert _is_product_entry("Northstar Ledger", "Northstar Ledger")
    assert _is_product_entry("Ledger", "Ledger")
    assert not _is_product_entry("Budget Helper", "Budget Helper")
    assert not _is_product_entry("SpendShape Notes", "SpendShape Notes")


def test_installer_cleanup_covers_both_legacy_visible_names():
    root = Path(__file__).resolve().parents[1]
    hooks = (root / "src-tauri" / "windows" / "hooks.nsh").read_text(
        encoding="utf-8"
    )
    for old_name in ("Ledger", "Northstar Ledger"):
        assert f'Uninstall\\{old_name}"' in hooks
        assert f'$SMPROGRAMS\\{old_name}.lnk' in hooks
        assert f'$DESKTOP\\{old_name}.lnk' in hooks


def test_load_bearing_upgrade_identifiers_stay_legacy():
    root = Path(__file__).resolve().parents[1]
    config = (root / "src-tauri" / "tauri.conf.json").read_text(
        encoding="utf-8"
    )
    shell = (root / "src-tauri" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )
    hooks = (root / "src-tauri" / "windows" / "hooks.nsh").read_text(
        encoding="utf-8"
    )
    assert '"identifier": "dev.benthompson.ledger"' in config
    assert 'join("Ledger")' in shell
    assert '$LOCALAPPDATA\\Programs\\Ledger' in hooks

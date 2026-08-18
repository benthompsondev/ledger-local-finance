"""Check what Windows actually recorded after SpendShape was installed.

verify_windows_installer.py checks the artifact: its name, its size, the
bundle inside it. Nothing checked what the machine looked like once that
artifact had run, so the version a user sees in Settings › Apps was never
part of any release evidence. An upgrade that installed a new executable
beside a stale uninstall entry, or that left the pre-rename "Ledger" entry
behind next to "SpendShape", would have passed every gate.

Reads only. Run it after installing, against a real machine:

    python -m scripts.verify_installed_app
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from utils import __version__

PRODUCT_NAME = "SpendShape"
LEGACY_PRODUCT_NAMES = frozenset({"ledger", "northstar ledger"})
_UNINSTALL_PATHS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _is_product_entry(display_name: str, subkey_name: str) -> bool:
    """Recognize the current app and both legacy visible names exactly."""
    names = {display_name.strip().lower(), subkey_name.strip().lower()}
    return PRODUCT_NAME.lower() in names or bool(names & LEGACY_PRODUCT_NAMES)


def _uninstall_entries() -> list[dict[str, str]]:
    """Every Add/Remove Programs entry that looks like this app."""
    import winreg

    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    found: list[dict[str, str]] = []
    for hive_name, path in _UNINSTALL_PATHS:
        try:
            root = winreg.OpenKey(hives[hive_name], path)
        except OSError:
            continue
        with root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    subkey_name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, subkey_name) as subkey:
                        def value(name: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(subkey, name)[0])
                            except OSError:
                                return ""

                        display = value("DisplayName")
                        if not _is_product_entry(display, subkey_name):
                            continue
                        found.append({
                            "hive": hive_name,
                            "key": subkey_name,
                            "display_name": display,
                            "display_version": value("DisplayVersion"),
                            "install_location": value("InstallLocation").strip('"'),
                        })
                except OSError:
                    continue
    return found


def _executable_versions(install_location: str) -> dict[str, str]:
    """ProductVersion of each executable the installer put down."""
    directory = Path(install_location or "")
    if not directory.is_dir():
        return {}
    import subprocess

    versions: dict[str, str] = {}
    for exe in sorted(directory.glob("*.exe")):
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item -LiteralPath '{exe}').VersionInfo.ProductVersion"],
            capture_output=True, text=True, check=False,
        )
        versions[exe.name] = completed.stdout.strip()
    return versions


def verify_installed(expected_version: str = "") -> dict[str, object]:
    """Raise unless the installed app is wholly at the expected version."""
    expected = expected_version or __version__
    if sys.platform != "win32":
        raise RuntimeError("The installed app can only be checked on Windows.")

    entries = _uninstall_entries()
    if not entries:
        raise RuntimeError(
            f"{PRODUCT_NAME} is not in Installed Apps. Install the built "
            "artifact before running this check."
        )
    # More than one entry is the pre-rename ghost: an old "Ledger" key left
    # beside the current one, showing its own stale version in the same list.
    if len(entries) > 1:
        listed = ", ".join(
            f"{e['hive']}\\{e['key']} = {e['display_version'] or '(no version)'}"
            for e in entries
        )
        raise RuntimeError(
            f"Installed Apps carries {len(entries)} SpendShape or legacy entries, so at "
            f"least one is stale: {listed}"
        )

    entry = entries[0]
    if entry["display_name"] != PRODUCT_NAME:
        raise RuntimeError(
            f"Installed Apps calls this {entry['display_name']!r}, "
            f"expected {PRODUCT_NAME!r}"
        )
    if entry["display_version"] != expected:
        raise RuntimeError(
            f"Installed Apps reports version {entry['display_version']!r}, "
            f"expected {expected!r}. The executable and the uninstall entry "
            "have gone out of step."
        )

    versions = _executable_versions(entry["install_location"])
    if not versions:
        raise RuntimeError(
            f"No executables found at {entry['install_location']!r}"
        )
    wrong = {
        name: value for name, value in versions.items() if value != expected
    }
    if wrong:
        raise RuntimeError(
            "These installed executables are not at "
            f"{expected}: {wrong}"
        )
    return {
        "display_name": entry["display_name"],
        "display_version": entry["display_version"],
        "registry_key": f"{entry['hive']}\\{entry['key']}",
        "install_location": entry["install_location"],
        "executables": versions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version", default="",
        help="Version to require; defaults to the repository version.",
    )
    args = parser.parse_args()
    result = verify_installed(args.version)
    print(
        f"PASS installed app: {result['display_name']} "
        f"{result['display_version']} at {result['registry_key']}"
    )
    for name, version in sorted(result["executables"].items()):
        print(f"  {name}: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

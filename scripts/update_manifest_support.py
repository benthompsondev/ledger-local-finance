"""The artifact names a Northstar release produces.

One definition, so the build script, the release checks and the version
consistency test all expect the same filenames. Getting these wrong is how an
updater manifest ends up pointing at the interactive installer, which
downloads perfectly and then installs nothing.
"""
from __future__ import annotations

import pathlib


def expected_installer_name(version: str) -> str:
    """The NSIS installer a person downloads and double-clicks."""
    return f"NorthstarLedger_{version}_x64-setup.exe"


def expected_updater_archive_name(version: str) -> str:
    """The archive Tauri's updater downloads and unpacks.

    Produced by `bundle.createUpdaterArtifacts`. It is *not* the installer:
    the updater cannot run an interactive installer, so a manifest pointing at
    the .exe produces an update that appears to work and changes nothing.
    """
    return f"{expected_installer_name(version)}.nsis.zip"


def expected_signature_name(version: str) -> str:
    """The detached signature beside the archive."""
    return f"{expected_updater_archive_name(version)}.sig"


def bundle_dir(repo: pathlib.Path) -> pathlib.Path:
    return repo / "src-tauri" / "target" / "release" / "bundle" / "nsis"


def release_artifacts(repo: pathlib.Path, version: str) -> dict[str, pathlib.Path]:
    """Every file a signed Northstar release needs, by role."""
    directory = bundle_dir(repo)
    return {
        "installer": directory / expected_installer_name(version),
        "updater_archive": directory / expected_updater_archive_name(version),
        "signature": directory / expected_signature_name(version),
    }

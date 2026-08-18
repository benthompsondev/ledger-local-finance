"""The artifact names a SignalSpace release produces.

One definition, so the build script, the release checks and the version
consistency test all expect the same filenames. Getting these wrong is how an
updater manifest ends up pointing at the interactive installer, which
downloads perfectly and then installs nothing.
"""
from __future__ import annotations

import pathlib


def expected_installer_name(version: str) -> str:
    """The NSIS installer a person downloads and double-clicks."""
    return f"SignalSpaceFinance_{version}_x64-setup.exe"


def expected_updater_artifact_name(version: str) -> str:
    """What the updater downloads.

    For Tauri 2's NSIS bundler this is the installer itself: the build signs
    it in place and writes `<installer>.exe.sig` beside it, rather than
    producing the separate archive Tauri 1 used. Verified by building 2.6.0.
    """
    return expected_installer_name(version)


def expected_signature_name(version: str) -> str:
    """The detached signature beside the artifact."""
    return f"{expected_updater_artifact_name(version)}.sig"


def bundle_dir(repo: pathlib.Path) -> pathlib.Path:
    return repo / "src-tauri" / "target" / "release" / "bundle" / "nsis"


def release_artifacts(repo: pathlib.Path, version: str) -> dict[str, pathlib.Path]:
    """Every file a signed SignalSpace release needs, by role."""
    directory = bundle_dir(repo)
    return {
        "installer": directory / expected_installer_name(version),
        "updater_artifact": directory / expected_updater_artifact_name(version),
        "signature": directory / expected_signature_name(version),
    }

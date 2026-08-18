"""Verify the Windows installer input bundle and versioned setup artifact."""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from scripts.verify_windows_portable import _assert_safe_names
from utils import __version__


def verify_installer(installer: Path, bundle: Path) -> dict[str, object]:
    installer = installer.resolve()
    bundle = bundle.resolve()
    expected_name = f"SpendShape_{__version__}_x64-setup.exe"
    if installer.name != expected_name:
        raise RuntimeError(f"Expected installer name {expected_name}, got {installer.name}")
    if not installer.is_file() or installer.stat().st_size < 1_000_000:
        raise RuntimeError(f"Installer is missing or unexpectedly small: {installer}")
    required = [bundle / "ledger-engine.exe", bundle / "_internal"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Native sidecar bundle is incomplete: " + ", ".join(missing))
    # CI stands a placeholder in at this path so the Rust can compile without
    # spending several minutes on PyInstaller. That is fine for a compile
    # check and useless for a release, so a release must never accept one.
    placeholder = bundle / "PLACEHOLDER.txt"
    if placeholder.exists():
        raise RuntimeError(
            f"The sidecar directory holds the CI placeholder ({placeholder}). "
            "Build the real engine with scripts/build_native_desktop.ps1 "
            "before verifying a release."
        )
    engine = bundle / "ledger-engine.exe"
    if engine.stat().st_size < 1_000_000:
        raise RuntimeError(
            f"{engine} is {engine.stat().st_size} bytes, too small to be a "
            "PyInstaller build of the engine."
        )
    names = [str(path.relative_to(bundle)) for path in bundle.rglob("*")]
    _assert_safe_names(names)
    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        raise RuntimeError(f"Ledger version is not installer-safe: {__version__}")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest().upper()
    return {
        "files": sum(path.is_file() for path in bundle.rglob("*")),
        "size": installer.stat().st_size,
        "sha256": digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    result = verify_installer(args.installer, args.bundle)
    print(
        f"PASS installer: {result['size']} bytes, {result['files']} bundled files, "
        f"SHA-256 {result['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

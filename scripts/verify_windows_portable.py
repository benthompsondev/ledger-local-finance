"""Fail closed when a portable Ledger artifact contains private runtime files."""
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_NAMES = {
    "config.json", "finance.db", "finance.demo.db",
    "launcher.log", "launcher.log.prev", ".env",
}


def _assert_safe_names(names: list[str]) -> None:
    for raw in names:
        path = PurePosixPath(raw.replace("\\", "/"))
        parts = tuple(part.lower() for part in path.parts)
        app_relative = parts[1:] if parts[:1] == ("_internal",) else parts
        if app_relative[:1] in (("data",), ("backups",)):
            raise RuntimeError(f"Private data directory found in artifact: {raw}")
        if path.name.lower() in FORBIDDEN_NAMES:
            raise RuntimeError(f"Private runtime file found in artifact: {raw}")
        if path.name.lower().endswith((".db-wal", ".db-shm")):
            raise RuntimeError(f"SQLite sidecar found in artifact: {raw}")


def verify_folder(folder: Path) -> dict:
    folder = folder.resolve()
    required = [
        folder / "Ledger.exe",
        folder / "README.txt",
        folder / "_internal" / "app.py",
        folder / "_internal" / "pages" / "0_Home.py",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Portable folder is incomplete: " + ", ".join(missing))
    names = [str(path.relative_to(folder)) for path in folder.rglob("*")]
    _assert_safe_names(names)
    return {"files": sum(path.is_file() for path in folder.rglob("*"))}


def verify_zip(path: Path) -> dict:
    path = path.resolve()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _assert_safe_names(names)
        required = {"Ledger.exe", "README.txt", "_internal/app.py"}
        normalized = {name.replace("\\", "/") for name in names}
        missing = sorted(required - normalized)
        if missing:
            raise RuntimeError("Portable zip is incomplete: " + ", ".join(missing))
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return {"files": len(names), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--zip", dest="zip_path", type=Path)
    args = parser.parse_args(argv)
    if not args.folder and not args.zip_path:
        parser.error("provide --folder or --zip")
    if args.folder:
        result = verify_folder(args.folder)
        print(f"PASS portable folder: {result['files']} files, no private data")
    if args.zip_path:
        result = verify_zip(args.zip_path)
        print(
            f"PASS portable zip: {result['files']} files, "
            f"SHA-256 {result['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

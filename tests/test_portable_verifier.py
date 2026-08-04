from __future__ import annotations

import zipfile

import pytest

from scripts.verify_windows_portable import verify_folder, verify_zip


def _portable_tree(root):
    (root / "_internal" / "pages").mkdir(parents=True)
    (root / "Ledger.exe").write_bytes(b"demo exe")
    (root / "README.txt").write_text("private build", encoding="utf-8")
    (root / "_internal" / "app.py").write_text("# demo", encoding="utf-8")
    (root / "_internal" / "pages" / "0_Home.py").write_text(
        "# demo", encoding="utf-8"
    )


def test_portable_folder_accepts_complete_private_safe_tree(tmp_path):
    _portable_tree(tmp_path)
    assert verify_folder(tmp_path)["files"] == 4


def test_portable_folder_rejects_database(tmp_path):
    _portable_tree(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "finance.db").write_bytes(b"private")
    with pytest.raises(RuntimeError, match="Private data"):
        verify_folder(tmp_path)


def test_portable_zip_is_checked_and_hashed(tmp_path):
    folder = tmp_path / "Ledger"
    _portable_tree(folder)
    archive = tmp_path / "Ledger.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for path in folder.rglob("*"):
            if path.is_file():
                out.write(path, path.relative_to(folder).as_posix())
    result = verify_zip(archive)
    assert result["files"] == 4
    assert len(result["sha256"]) == 64

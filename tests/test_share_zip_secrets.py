from pathlib import Path

from scripts.make_share_zip import scan_for_secrets


def test_share_zip_allows_explicitly_invented_api_key_fixture(tmp_path) -> None:
    fixture = tmp_path / "fixture.py"
    fixture.write_text(
        'api_key="invented-cloud-key-that-must-stay-local"\n',
        encoding="utf-8",
    )

    assert scan_for_secrets(fixture, Path("fixture.py")) == []


def test_share_zip_still_blocks_unmarked_generic_api_key(tmp_path) -> None:
    fixture = tmp_path / "settings.py"
    fixture.write_text(
        'api_key="abcdef1234567890secretvalue"\n',
        encoding="utf-8",
    )

    hits = scan_for_secrets(fixture, Path("settings.py"))
    assert len(hits) == 1
    assert "Generic api_key" in hits[0]

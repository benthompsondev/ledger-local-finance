from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest

import Ledger_Desktop as desktop


def _legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY, marker TEXT);"
        "CREATE TABLE import_log (id INTEGER PRIMARY KEY);"
        "INSERT INTO transactions(marker) VALUES ('portable marker');"
    )
    conn.commit()
    conn.close()


def test_default_data_root_uses_localappdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LEDGER_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert desktop.default_data_root() == tmp_path / "Ledger"


def test_data_root_environment_override_wins(monkeypatch, tmp_path: Path) -> None:
    override = tmp_path / "isolated data"
    monkeypatch.setenv("LEDGER_DATA_DIR", str(override))

    assert desktop.default_data_root() == override


def test_configure_data_root_sets_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LEDGER_DATA_DIR", raising=False)
    root = desktop.configure_data_root(str(tmp_path / "private"))

    assert root.is_dir()
    assert Path(desktop.os.environ["LEDGER_DATA_DIR"]) == root


def test_available_port_is_loopback_bindable() -> None:
    port = desktop.find_available_port(0)

    assert 0 < port < 65536


def test_default_port_conflict_chooses_another_loopback_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind((desktop.DEFAULT_HOST, 0))
        port = occupied.getsockname()[1]

        selected = desktop.find_available_port(port, strict=False)

    assert selected != port
    assert selected > 0


def test_explicit_port_conflict_fails_clearly() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind((desktop.DEFAULT_HOST, 0))
        port = occupied.getsockname()[1]

        with pytest.raises(OSError, match=f"Requested port {port} is already in use"):
            desktop.find_available_port(port, strict=True)


def test_runtime_state_round_trip(tmp_path: Path) -> None:
    desktop.write_runtime_state(tmp_path, pid=1234, port=8501, control_port=8502)

    state = desktop.read_runtime_state(tmp_path)

    assert state is not None
    assert state["pid"] == 1234
    assert state["port"] == 8501
    assert state["control_port"] == 8502
    assert state["version"] == desktop.APP_VERSION
    raw = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    assert raw["version"] == desktop.APP_VERSION


def test_console_error_includes_exact_log_path(capsys, tmp_path: Path) -> None:
    destination = tmp_path / "logs" / "launcher.log"

    desktop.show_startup_error("Test startup failure.", destination, no_dialog=True)

    error = capsys.readouterr().err
    assert "Test startup failure." in error
    assert str(destination) in error


def test_legacy_import_uses_sqlite_backup_and_preserves_source(tmp_path) -> None:
    source = tmp_path / "legacy.db"
    destination = tmp_path / "portable" / "finance.db"
    _legacy_db(source)

    desktop.import_legacy_database(source, destination)
    copied = sqlite3.connect(destination)
    original = sqlite3.connect(source)
    try:
        assert copied.execute(
            "SELECT marker FROM transactions"
        ).fetchone()[0] == "portable marker"
        assert original.execute(
            "SELECT marker FROM transactions"
        ).fetchone()[0] == "portable marker"
    finally:
        copied.close()
        original.close()


def test_invalid_legacy_file_is_rejected(tmp_path) -> None:
    source = tmp_path / "other.db"
    sqlite3.connect(source).close()

    with pytest.raises(ValueError, match="not a Ledger database"):
        desktop.import_legacy_database(source, tmp_path / "finance.db")


def test_installer_keeps_program_and_data_paths_separate() -> None:
    script = (
        Path("packaging/windows/Ledger.iss").read_text(encoding="utf-8").lower()
    )

    assert "defaultdirname={localappdata}\\programs\\{#appname}" in script
    assert "{localappdata}\\ledger" not in script
    assert "[uninstalldelete]" not in script
    assert "privilegesrequired=lowest" in script

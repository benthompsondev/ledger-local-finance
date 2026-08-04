# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH)
BUILD_ROOT = ROOT / "build" / "windows"
APP_ICON = BUILD_ROOT / "Ledger.ico"
VERSION_INFO = ROOT / "packaging" / "windows" / "version_info.txt"
CONSOLE_BUILD = os.environ.get("LEDGER_BUILD_CONSOLE") == "1"
EXE_NAME = "Ledger-Debug" if CONSOLE_BUILD else "Ledger"

streamlit_datas, streamlit_bins, streamlit_hidden = collect_all("streamlit")
datas = streamlit_datas
datas += collect_data_files("plotly")
datas += collect_data_files("pdfplumber")
datas += [
    (str(ROOT / "app.py"), "."),
    (str(ROOT / "pages"), "pages"),
    (str(ROOT / ".streamlit" / "config.toml"), ".streamlit"),
]

hiddenimports = streamlit_hidden
for package in ("pages", "utils", "components", "parsers", "config", "scripts"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(ROOT / "Ledger_Desktop.py")],
    pathex=[str(ROOT)],
    binaries=streamlit_bins,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(APP_ICON),
    version=str(VERSION_INFO),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=EXE_NAME,
)

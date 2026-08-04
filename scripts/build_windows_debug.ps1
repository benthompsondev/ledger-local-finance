# RETIRED PACKAGING PATH - do not use for releases.
# This script belongs to the legacy Streamlit/PyInstaller browser prototype.
# The supported Windows product is the native Tauri build:
#   .\scripts/build_native_desktop.ps1  (see docs/NATIVE_DESKTOP_SLICE.md)
# Pass -ILegacyPrototype to run anyway (rollback/reference only).
[CmdletBinding()]
param(
    [switch]$ILegacyPrototype,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if (-not $ILegacyPrototype) {
    throw ("This is the RETIRED legacy Streamlit/PyInstaller packaging path. " +
           "Build the supported native Windows app with scripts/build_native_desktop.ps1. " +
           "If you really need the legacy prototype, re-run with -ILegacyPrototype.")
}
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtual environment not found: $Python"
}

Push-Location $RepoRoot
try {
    if (-not $SkipInstall) {
        & $Python -m pip install -r requirements.lock.txt -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    }
    & $Python -m scripts.build_windows_icon --out "build\windows\Ledger.ico"
    if ($LASTEXITCODE -ne 0) { throw "Windows icon build failed." }
    $env:LEDGER_BUILD_CONSOLE = "1"
    & $Python -m PyInstaller --clean --noconfirm Ledger.spec
    if ($LASTEXITCODE -ne 0) { throw "Diagnostic build failed." }
    Write-Host "Diagnostic executable: $(Join-Path $RepoRoot 'dist\Ledger-Debug\Ledger-Debug.exe')"
}
finally {
    Remove-Item Env:LEDGER_BUILD_CONSOLE -ErrorAction SilentlyContinue
    Pop-Location
}

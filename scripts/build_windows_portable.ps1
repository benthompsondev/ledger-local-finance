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
$Dist = Join-Path $RepoRoot "dist"
$AppFolder = Join-Path $Dist "Ledger"
$ZipPath = Join-Path $Dist "Ledger-windows-x64-portable.zip"
$ChecksumPath = "$ZipPath.sha256.txt"

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

    Remove-Item Env:LEDGER_BUILD_CONSOLE -ErrorAction SilentlyContinue
    & $Python -m PyInstaller --clean --noconfirm Ledger.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    Copy-Item -LiteralPath "docs\WINDOWS_INSTALL.md" `
        -Destination (Join-Path $AppFolder "README.txt") -Force
    & $Python -m scripts.verify_windows_portable --folder $AppFolder
    if ($LASTEXITCODE -ne 0) { throw "Portable-folder safety check failed." }

    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $AppFolder "*") `
        -DestinationPath $ZipPath -CompressionLevel Optimal
    & $Python -m scripts.verify_windows_portable --zip $ZipPath
    if ($LASTEXITCODE -ne 0) { throw "Portable-zip safety check failed." }
    $Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
    Set-Content -LiteralPath $ChecksumPath `
        -Value "$Hash  $(Split-Path $ZipPath -Leaf)" -Encoding ascii

    Write-Host "Portable folder: $AppFolder"
    Write-Host "Portable zip:    $ZipPath"
    Write-Host "SHA-256:         $Hash"
}
finally {
    Pop-Location
}

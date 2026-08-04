# RETIRED PACKAGING PATH - do not use for releases.
# This script belongs to the legacy Streamlit/PyInstaller browser prototype.
# The supported Windows product is the native Tauri build:
#   .\scripts/build_native_desktop.ps1  (see docs/NATIVE_DESKTOP_SLICE.md)
# Pass -ILegacyPrototype to run anyway (rollback/reference only).
[CmdletBinding()]
param(
    [switch]$ILegacyPrototype,
    [switch]$SkipInstall,
    [switch]$SkipBundle
)

$ErrorActionPreference = "Stop"

if (-not $ILegacyPrototype) {
    throw ("This is the RETIRED legacy Streamlit/PyInstaller packaging path. " +
           "Build the supported native Windows app with scripts/build_native_desktop.ps1. " +
           "If you really need the legacy prototype, re-run with -ILegacyPrototype.")
}
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Bundle = Join-Path $RepoRoot "dist\Ledger"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtual environment not found: $Python"
}

$AppVersion = (& $Python -c "from utils import __version__; print(__version__)").Trim()
if (-not $AppVersion) { throw "Could not read the Ledger version." }

$CompilerCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
)
$Compiler = $CompilerCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $Compiler) {
    throw "Inno Setup was not found. Install it with: winget install --id JRSoftware.InnoSetup.7 -e -s winget -i"
}

Push-Location $RepoRoot
try {
    if (-not $SkipBundle) {
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
            -Destination (Join-Path $Bundle "README.txt") -Force
    }

    & $Python -m scripts.verify_windows_portable --folder $Bundle
    if ($LASTEXITCODE -ne 0) { throw "Bundle safety check failed." }

    & $Compiler "/DAppVersion=$AppVersion" "packaging\windows\Ledger.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

    $Installer = Join-Path $RepoRoot "dist\Ledger-Setup-$AppVersion-x64.exe"
    & $Python -m scripts.verify_windows_installer --installer $Installer --bundle $Bundle
    if ($LASTEXITCODE -ne 0) { throw "Installer verification failed." }
    $Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash
    $Checksum = "$Installer.sha256.txt"
    Set-Content -LiteralPath $Checksum `
        -Value "$Hash  $(Split-Path $Installer -Leaf)" -Encoding ascii

    Write-Host "Installer: $Installer"
    Write-Host "SHA-256:  $Hash"
}
finally {
    Pop-Location
}

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipSidecar
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$TargetTriple = (& rustc --print host-tuple).Trim()
# The engine ships as a PyInstaller onedir bundle: it starts in well under a
# second, where a onefile build re-extracted ~85 MB on every request.
$SidecarDir = Join-Path $RepoRoot "build\native-sidecar-dir\ledger-engine"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project virtual environment not found: $Python"
}
if ($TargetTriple -ne "x86_64-pc-windows-msvc") {
    throw "This Windows build expects x86_64-pc-windows-msvc, got $TargetTriple."
}

Push-Location $RepoRoot
try {
    if (-not $SkipInstall) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw "Node dependency installation failed." }
        & $Python -m pip install -r requirements.lock.txt -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
    }

    if (-not $SkipSidecar) {
        & $Python -m scripts.build_native_icon --out "build\native-icon.png"
        if ($LASTEXITCODE -ne 0) { throw "Ledger native icon build failed." }
        & npm.cmd run tauri -- icon "build\native-icon.png"
        if ($LASTEXITCODE -ne 0) { throw "Tauri icon generation failed." }

        & $Python -m PyInstaller --clean --noconfirm --onedir --hide-console hide-early `
            --name ledger-engine `
            --paths $RepoRoot `
            --distpath "build\native-sidecar-dir" `
            --workpath "build\native-sidecar-work" `
            --specpath "build\native-sidecar-spec" `
            "desktop\engine\ledger_engine.py"
        if ($LASTEXITCODE -ne 0) { throw "Ledger Python sidecar build failed." }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $SidecarDir "ledger-engine.exe") -PathType Leaf)) {
        throw "Packaged engine not found: $SidecarDir\ledger-engine.exe"
    }

    & npm.cmd run desktop:build
    if ($LASTEXITCODE -ne 0) { throw "Tauri desktop build failed." }

    $Config = Get-Content (Join-Path $RepoRoot "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
    $Version = [string]$Config.version
    $BundleDir = [IO.Path]::GetFullPath((Join-Path $RepoRoot "src-tauri\target\release\bundle\nsis"))
    $FinalInstaller = [IO.Path]::GetFullPath((Join-Path $BundleDir "NorthstarLedger_${Version}_x64-setup.exe"))
    if (-not $FinalInstaller.StartsWith($BundleDir, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Resolved installer path escaped the NSIS bundle directory."
    }
    $BuiltInstaller = Get-ChildItem -LiteralPath $BundleDir -Filter "*${Version}*setup.exe" -File |
        Where-Object { $_.FullName -ne $FinalInstaller } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($BuiltInstaller) {
        Move-Item -LiteralPath $BuiltInstaller.FullName -Destination $FinalInstaller -Force
    }
    if (-not (Test-Path -LiteralPath $FinalInstaller -PathType Leaf)) {
        throw "Expected installer was not produced: $FinalInstaller"
    }

    # Verify the artifact that was actually produced, not one built earlier.
    # A build that emits an installer nobody checked is how a private file or a
    # missing sidecar reaches a machine, so a failure here fails the build.
    Write-Host "Verifying installer..."
    & $Python -m scripts.verify_windows_installer `
        --installer $FinalInstaller `
        --bundle $SidecarDir
    if ($LASTEXITCODE -ne 0) {
        throw "Installer verification failed for $FinalInstaller"
    }

    # Updater artifacts. `bundle.createUpdaterArtifacts` produces the archive
    # and, when a signing key is present, its detached signature. The
    # signature is what makes an update installable at all, so a release build
    # without one is reported loudly rather than shipped quietly.
    $UpdaterArchive = Join-Path $BundleDir "NorthstarLedger_${Version}_x64-setup.nsis.zip"
    $UpdaterSignature = "$UpdaterArchive.sig"
    if (Test-Path -LiteralPath $UpdaterArchive -PathType Leaf) {
        Write-Host "Updater archive: $UpdaterArchive"
        if (Test-Path -LiteralPath $UpdaterSignature -PathType Leaf) {
            if ((Get-Item -LiteralPath $UpdaterSignature).Length -eq 0) {
                throw "Updater signature is empty: $UpdaterSignature"
            }
            Write-Host "Updater signature: $UpdaterSignature"
            Write-Host "Next: node scripts/update_manifest.mjs --version $Version ..."
        }
        else {
            Write-Warning ("No updater signature was produced. Set " +
                "TAURI_SIGNING_PRIVATE_KEY (and its password) before building " +
                "a release, or this version cannot be installed by the updater.")
        }
    }
    else {
        Write-Warning ("No updater archive was produced. Check that " +
            "bundle.createUpdaterArtifacts is true in tauri.conf.json.")
    }

    Write-Host "Northstar Ledger installer: $FinalInstaller"
}
finally {
    Pop-Location
}

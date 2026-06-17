# ─────────────────────────────────────────────────────────────────────────────
#  Ledger — Windows PowerShell launcher
#
#  Right-click → "Run with PowerShell", or run from a PS terminal.
#  Add -Demo to create fake demo data and launch against the demo DB:
#    .\run_windows.ps1 -Demo
#
#  First-time PowerShell users: if you see an execution-policy error, run:
#    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#  then try again.
#
#  Mirrors the recovery logic in Ledger_Launcher.py:
#    • detect Python via existing venv → py -3.14 → py -3 → py → python → python3
#    • validate the venv (python --version + python -m pip --version)
#    • on corruption: rename .venv to .venv.broken-YYYYMMDD-HHMMSS,
#      rebuild via python -m venv + ensurepip
#    • use `python -m pip ...` everywhere — never bare pip
#    • log every decision to launcher.log
#    • on failure show a clear error + manual repair commands
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$Demo
)

$ErrorActionPreference = "Continue"  # we handle errors ourselves
Set-Location -Path $PSScriptRoot

$Base   = $PSScriptRoot
$Venv   = Join-Path $Base ".venv"
$VPy    = Join-Path $Venv "Scripts\python.exe"
$Reqs   = Join-Path $Base "requirements.txt"
$App    = Join-Path $Base "app.py"
$LogF   = Join-Path $Base "launcher.log"

# ── Logging ─────────────────────────────────────────────────────────────────
"=== Ledger launcher run $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $LogF -Encoding UTF8
"cwd=$Base" | Out-File -FilePath $LogF -Append -Encoding UTF8

function Write-Log {
    param([string]$Msg)
    $line = "[$(Get-Date -Format HH:mm:ss)] $Msg"
    Write-Host $line
    $line | Out-File -FilePath $LogF -Append -Encoding UTF8
}

function Write-Section {
    param([string]$Title)
    Write-Log ""
    Write-Log "=== $Title ==="
}

function Repair-Instructions {
    @"
------------------------------------------------------------------
 Manual repair (copy/paste into a fresh PowerShell window):
------------------------------------------------------------------
   cd "$Base"
   Remove-Item -Recurse -Force .venv
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m ensurepip --upgrade
   .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m streamlit run app.py
------------------------------------------------------------------
 If 'py -3.14' is unavailable, substitute 'py -3' or the full path to
 your Python install. Diagnostics: $LogF
------------------------------------------------------------------
"@
}

# ── Probe a candidate Python invocation (returns $true if --version works) ──
function Test-PythonCandidate {
    param([string[]]$Cmd)
    try {
        $null = & $Cmd[0] @($Cmd[1..($Cmd.Count-1)] + @("--version")) 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ── Detect a usable Python ───────────────────────────────────────────────────
function Find-HostPython {
    param([switch]$AllowExistingVenv = $true)

    $candidates = @()

    if ($AllowExistingVenv -and (Test-Path $VPy)) {
        $candidates += ,@($VPy)
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += ,@("py","-3.14")
        $candidates += ,@("py","-3")
        $candidates += ,@("py")
    }
    if (Get-Command python  -ErrorAction SilentlyContinue) { $candidates += ,@("python")  }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { $candidates += ,@("python3") }

    foreach ($c in $candidates) {
        if (Test-PythonCandidate -Cmd $c) {
            return ,$c
        } else {
            Write-Log "python candidate FAILED: $($c -join ' ')"
        }
    }
    return $null
}

# ── Run a command, capture stdout/stderr, log return code ───────────────────
function Invoke-Logged {
    param([string]$Label, [string[]]$Cmd)
    Write-Log "$ ${Label}: $($Cmd -join ' ')"
    $tmp = New-TemporaryFile
    try {
        & $Cmd[0] @($Cmd[1..($Cmd.Count-1)]) *> $tmp
        $rc = $LASTEXITCODE
    } catch {
        $rc = 1
        $_.Exception.Message | Out-File -FilePath $tmp -Encoding UTF8
    }
    $out = Get-Content $tmp -Raw -ErrorAction SilentlyContinue
    if ($out) {
        ($out.Trim() -split "`n" |
            Select-Object -First 30 |
            ForEach-Object { Write-Log "    $_" }) | Out-Null
    }
    Write-Log "  -> rc=$rc"
    Remove-Item $tmp -ErrorAction SilentlyContinue
    return $rc
}

# ── Validate venv ────────────────────────────────────────────────────────────
function Test-VenvHealthy {
    if (-not (Test-Path $VPy)) { return @($false, "venv missing") }
    $rc = Invoke-Logged -Label "probe venv python" -Cmd @($VPy, "--version")
    if ($rc -ne 0) { return @($false, "venv python failed") }
    $rc = Invoke-Logged -Label "probe venv pip" -Cmd @($VPy, "-m", "pip", "--version")
    if ($rc -ne 0) { return @($false, "venv pip failed (likely corrupted)") }
    return @($true, "ok")
}

# ── Rebuild venv ─────────────────────────────────────────────────────────────
function Rebuild-Venv {
    param([string[]]$HostPy)
    Write-Section "rebuilding venv"
    if (Test-Path $Venv) {
        $stamp = (Get-Date -Format "yyyyMMdd-HHmmss")
        $backup = "$Venv.broken-$stamp"
        Write-Log "renaming broken venv to $backup"
        try {
            Rename-Item -Path $Venv -NewName (Split-Path $backup -Leaf) -ErrorAction Stop
        } catch {
            Write-Log "ERROR: could not rename venv: $_"
            throw "could not rename existing .venv (possibly in use): $_"
        }
    }

    $rc = Invoke-Logged -Label "create fresh venv" `
                       -Cmd ($HostPy + @("-m", "venv", $Venv))
    if ($rc -ne 0 -or -not (Test-Path $VPy)) {
        throw "venv creation failed (rc=$rc)"
    }
    $rc = Invoke-Logged -Label "ensurepip --upgrade" `
                       -Cmd @($VPy, "-m", "ensurepip", "--upgrade")
    if ($rc -ne 0) {
        throw "ensurepip failed (rc=$rc)"
    }
}

# ── Main flow ────────────────────────────────────────────────────────────────
try {
    if (-not (Test-Path $App)) {
        Write-Log "FATAL: app.py not found at $App"
        Write-Host ""
        Write-Host "Cannot find app.py at $App" -ForegroundColor Red
        Write-Host "This launcher must live in the same folder as app.py."
        Read-Host "Press Enter to exit"
        exit 1
    }

    Write-Section "detecting host Python"
    $hostPy = Find-HostPython -AllowExistingVenv:$true
    if (-not $hostPy) {
        Write-Log "FATAL: no working Python found"
        Write-Host ""
        Write-Host "Ledger could not find a working Python." -ForegroundColor Red
        Write-Host "Install Python 3.11+ from https://www.python.org/downloads/"
        Write-Host "and tick 'Add Python to PATH' during install."
        Write-Host ""
        Write-Host "Diagnostics: $LogF"
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Log "selected host python: $($hostPy -join ' ')"
    $verOut = & $hostPy[0] @($hostPy[1..($hostPy.Count-1)] + @("--version")) 2>&1
    Write-Log "selected host python version: $verOut"
    Write-Log ("py launcher available: " +
               ([bool](Get-Command py -ErrorAction SilentlyContinue)))

    Write-Section "validating venv"
    $health = Test-VenvHealthy
    Write-Log ("venv health: " + $(if ($health[0]) {"OK"} else {"BROKEN"}) +
               " ($($health[1]))")

    if (-not $health[0]) {
        Write-Section "recovering venv"
        # If our only host candidate was the broken venv, re-detect.
        if ((Test-Path $VPy) -and ($hostPy.Count -eq 1) -and ($hostPy[0] -eq $VPy)) {
            Write-Log "re-detecting host Python (skipping broken venv)"
            $hostPy = Find-HostPython -AllowExistingVenv:$false
            if (-not $hostPy) {
                Write-Log "FATAL: venv broken AND no host Python to rebuild"
                Write-Host ""
                Write-Host "Ledger could not prepare its Python environment." -ForegroundColor Red
                Write-Host "The existing .venv appears corrupted, and no host"
                Write-Host "Python is available to rebuild it."
                Write-Host (Repair-Instructions)
                Read-Host "Press Enter to exit"
                exit 1
            }
        }
        Rebuild-Venv -HostPy $hostPy
    }

    Write-Section "upgrading packaging tools"
    $rc = Invoke-Logged -Label "upgrade pip/setuptools/wheel" `
        -Cmd @($VPy, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
    if ($rc -ne 0) {
        throw "pip/setuptools/wheel upgrade failed (rc=$rc)"
    }

    Write-Section "installing requirements"
    if (Test-Path $Reqs) {
        $rc = Invoke-Logged -Label "install requirements" `
            -Cmd @($VPy, "-m", "pip", "install", "-r", $Reqs)
        if ($rc -ne 0) {
            throw "requirements install failed (rc=$rc)"
        }
    } else {
        Write-Log "WARN: requirements.txt not found at $Reqs"
    }

    Write-Section "verifying streamlit"
    $rc = Invoke-Logged -Label "probe streamlit" `
        -Cmd @($VPy, "-m", "streamlit", "--version")
    if ($rc -ne 0) {
        Write-Log "streamlit not importable -- attempting one-shot install"
        $rc = Invoke-Logged -Label "install streamlit fallback" `
            -Cmd @($VPy, "-m", "pip", "install", "streamlit")
        if ($rc -ne 0) {
            throw "streamlit install failed (rc=$rc)"
        }
    }

    if ($Demo) {
        Write-Section "preparing demo data"
        $rc = Invoke-Logged -Label "create demo data" `
            -Cmd @($VPy, "-m", "scripts.create_demo_data", "--force")
        if ($rc -ne 0) {
            throw "demo data creation failed (rc=$rc)"
        }
        $env:LEDGER_DEMO_DB = "1"
        Write-Log "demo mode enabled: LEDGER_DEMO_DB=1"
    }

    Write-Section "launching Streamlit"
    Write-Host ""
    if ($Demo) {
        Write-Host "Starting Ledger demo mode at http://localhost:8501" -ForegroundColor Green
    } else {
        Write-Host "Starting Ledger at http://localhost:8501" -ForegroundColor Green
    }
    Write-Host "Press Ctrl+C in this window to stop."
    Write-Host ""
    # Bind to localhost only by default.
    & $VPy "-m" "streamlit" "run" $App "--server.address" "127.0.0.1" "--server.port" "8501"
    $rc = $LASTEXITCODE
    Write-Log "streamlit exited with rc=$rc"
    if ($rc -ne 0) {
        Write-Host ""
        Write-Host "Streamlit did not start cleanly." -ForegroundColor Yellow
        Write-Host "If port 8501 is busy, try:"
        Write-Host "    & '$VPy' -m streamlit run app.py --server.port 8502"
        Write-Host ""
        Write-Host "Diagnostics: $LogF"
    }
}
catch {
    Write-Log ("FATAL: {0}" -f $_.Exception.Message)
    Write-Host ""
    Write-Host "Ledger could not prepare its Python environment." -ForegroundColor Red
    Write-Host "What failed: $($_.Exception.Message)"
    Write-Host ""
    Write-Host (Repair-Instructions)
    Read-Host "Press Enter to exit"
    exit 1
}

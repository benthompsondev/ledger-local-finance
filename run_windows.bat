@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM =====================================================================
REM Ledger - Windows launcher (Pass 18: robust + shareable)
REM
REM Goals:
REM   - No hardcoded Python paths or usernames.
REM   - Detect Python dynamically: existing venv -> py -3.14 -> py -3 ->
REM     py -> python -> python3.
REM   - Validate the venv (Python + pip) BEFORE trusting it.
REM   - If the venv is corrupted (e.g. ImportError from pip vendored idna),
REM     rename it to .venv.broken-YYYYMMDD-HHMMSS and rebuild from scratch
REM     using ensurepip.
REM   - Use python -m pip everywhere -- never bare pip.
REM   - Log every decision to launcher.log so we can debug headless failures.
REM   - On any unrecoverable failure, print the exact manual commands the
REM     user can run to repair the environment by hand.
REM
REM Double-click this file to start Ledger on Windows.
REM =====================================================================

cd /d "%~dp0"

set "LOG=%~dp0launcher.log"
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "REQS=%~dp0requirements.txt"
set "APP=%~dp0app.py"
set "LAUNCH_OK="

REM Build a sortable timestamp for log entries / broken-venv backup names.
REM We avoid `wmic` (deprecated on Win11) and just use %DATE% / %TIME%, then
REM strip the parts that vary by locale to a safe ASCII fallback.
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do set "_TS=%%I"
if not defined _TS (
    REM Fallback: %DATE% / %TIME% (locale-dependent but always present).
    set "_TS=%DATE:/=-% %TIME::=-%"
    set "_TS=!_TS: =0!"
)
set "TS=%_TS:~0,8%-%_TS:~8,6%"

REM --- Initialise log ---------------------------------------------------
> "%LOG%" echo === Ledger launcher run %TS% ===
>>"%LOG%" echo cwd=%~dp0
>>"%LOG%" echo.

call :LOG "Detecting Python..."

REM --- Step 1: Detect a usable Python ----------------------------------
REM We pick HOST_PY: the interpreter we'll use to BUILD the venv if needed.
REM We separately validate VPY (the venv's Python) below.

set "HOST_PY="
set "HOST_PY_DESC="

REM 1a) Existing venv Python (if it runs at all).
if exist "%VPY%" (
    "%VPY%" --version >nul 2>nul
    if !errorlevel! EQU 0 (
        set "HOST_PY=%VPY%"
        set "HOST_PY_DESC=existing .venv"
        call :LOG "candidate: existing venv at %VPY%"
    ) else (
        call :LOG "existing .venv\Scripts\python.exe failed --version (errorlevel !errorlevel!)"
    )
)

REM 1b) py -3.14 -> py -3 -> py
if not defined HOST_PY (
    where py >nul 2>nul
    if !errorlevel! EQU 0 (
        for %%V in (-3.14 -3 "") do (
            if not defined HOST_PY (
                py %%~V --version >nul 2>nul
                if !errorlevel! EQU 0 (
                    set "HOST_PY=py %%~V"
                    set "HOST_PY_DESC=py launcher (%%~V)"
                    call :LOG "candidate: py launcher option '%%~V'"
                )
            )
        )
    ) else (
        call :LOG "py launcher: not found"
    )
)

REM 1c) python (PATH)
if not defined HOST_PY (
    where python >nul 2>nul
    if !errorlevel! EQU 0 (
        python --version >nul 2>nul
        if !errorlevel! EQU 0 (
            set "HOST_PY=python"
            set "HOST_PY_DESC=python on PATH"
            call :LOG "candidate: python (PATH)"
        )
    )
)

REM 1d) python3 (PATH)
if not defined HOST_PY (
    where python3 >nul 2>nul
    if !errorlevel! EQU 0 (
        python3 --version >nul 2>nul
        if !errorlevel! EQU 0 (
            set "HOST_PY=python3"
            set "HOST_PY_DESC=python3 on PATH"
            call :LOG "candidate: python3 (PATH)"
        )
    )
)

if not defined HOST_PY (
    call :LOG "FATAL: no working Python found"
    echo.
    echo ==================================================================
    echo  Ledger could not find a working Python.
    echo ==================================================================
    echo.
    echo  Install Python 3.11 or newer from https://www.python.org/downloads/
    echo  During install, tick "Add Python to PATH".
    echo.
    echo  After installing, double-click this file again.
    echo.
    echo  Diagnostics: see launcher.log
    echo.
    pause
    exit /b 1
)

REM Capture host Python version into the log.
for /f "tokens=*" %%V in ('%HOST_PY% --version 2^>^&1') do set "HOST_PY_VER=%%V"
call :LOG "selected host python: %HOST_PY%  (%HOST_PY_DESC%)  -> %HOST_PY_VER%"

REM --- Step 2: Validate / build / repair the venv ----------------------

set "NEED_REBUILD="

if not exist "%VPY%" (
    call :LOG "venv: missing -> will create"
    set "NEED_REBUILD=missing"
) else (
    REM Run two validation probes: Python launches AND pip imports cleanly.
    "%VPY%" --version >nul 2>"%LOG%.tmp"
    if !errorlevel! NEQ 0 (
        call :LOG "venv: python --version failed"
        type "%LOG%.tmp" >>"%LOG%"
        set "NEED_REBUILD=python_broken"
    ) else (
        "%VPY%" -m pip --version >"%LOG%.tmp" 2>&1
        if !errorlevel! NEQ 0 (
            call :LOG "venv: pip --version failed (likely corrupted pip vendor)"
            type "%LOG%.tmp" >>"%LOG%"
            set "NEED_REBUILD=pip_broken"
        ) else (
            for /f "tokens=*" %%P in ('"%VPY%" -m pip --version 2^>nul') do (
                call :LOG "venv pip OK: %%P"
            )
        )
    )
    if exist "%LOG%.tmp" del "%LOG%.tmp" >nul 2>nul
)

if defined NEED_REBUILD (
    REM Pick the BUILD interpreter: never the broken venv Python.
    set "BUILD_PY=%HOST_PY%"
    if "%HOST_PY%"=="%VPY%" (
        REM Existing venv was our only candidate but it is broken -- need
        REM a real host Python. Re-run detection skipping the venv.
        set "HOST_PY="
        where py >nul 2>nul
        if !errorlevel! EQU 0 (
            for %%V in (-3.14 -3 "") do (
                if not defined HOST_PY (
                    py %%~V --version >nul 2>nul
                    if !errorlevel! EQU 0 set "HOST_PY=py %%~V"
                )
            )
        )
        if not defined HOST_PY (
            where python >nul 2>nul
            if !errorlevel! EQU 0 set "HOST_PY=python"
        )
        if not defined HOST_PY (
            where python3 >nul 2>nul
            if !errorlevel! EQU 0 set "HOST_PY=python3"
        )
        set "BUILD_PY=!HOST_PY!"
    )
    if not defined BUILD_PY (
        call :LOG "FATAL: venv broken AND no host Python available to rebuild it"
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )

    REM Move the broken venv aside (don't delete -- preserve for forensics).
    if exist "%VENV%" (
        set "BACKUP=%VENV%.broken-%TS%"
        call :LOG "renaming broken venv to !BACKUP!"
        move /y "%VENV%" "!BACKUP!" >>"%LOG%" 2>&1
        if !errorlevel! NEQ 0 (
            call :LOG "ERROR: could not rename venv. It may be in use; try closing terminals/IDEs."
            call :REPAIR_INSTRUCTIONS
            pause
            exit /b 1
        )
    )

    call :LOG "creating fresh venv with: %BUILD_PY% -m venv .venv"
    %BUILD_PY% -m venv "%VENV%" >>"%LOG%" 2>&1
    if !errorlevel! NEQ 0 (
        call :LOG "FATAL: venv creation failed"
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )

    if not exist "%VPY%" (
        call :LOG "FATAL: venv created but %VPY% missing"
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )

    REM Bootstrap pip from inside the venv via ensurepip -- this avoids the
    REM corrupted-vendored-idna ImportError that sinks the legacy
    REM `pip install --upgrade pip` path on a broken Python install.
    call :LOG "bootstrapping pip with ensurepip"
    "%VPY%" -m ensurepip --upgrade >>"%LOG%" 2>&1
    if !errorlevel! NEQ 0 (
        call :LOG "ERROR: ensurepip failed"
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )
)

REM --- Step 3: Upgrade packaging tools ---------------------------------
REM Always run this -- safe on a fresh or existing venv. Never use `pip`
REM as a bare command; always go via `python -m pip`.
call :LOG "upgrading pip / setuptools / wheel"
"%VPY%" -m pip install --upgrade pip setuptools wheel >>"%LOG%" 2>&1
if !errorlevel! NEQ 0 (
    call :LOG "ERROR: pip/setuptools/wheel upgrade failed"
    echo.
    echo Ledger could not prepare its Python environment. The packaging
    echo tools (pip / setuptools / wheel) failed to upgrade. See launcher.log
    echo for the full traceback.
    echo.
    call :REPAIR_INSTRUCTIONS
    pause
    exit /b 1
)

REM --- Step 4: Install requirements ------------------------------------
if exist "%REQS%" (
    call :LOG "installing requirements from %REQS%"
    "%VPY%" -m pip install -r "%REQS%" >>"%LOG%" 2>&1
    if !errorlevel! NEQ 0 (
        call :LOG "ERROR: requirements install failed"
        echo.
        echo Ledger could not install required packages from requirements.txt.
        echo See launcher.log for details.
        echo.
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )
) else (
    call :LOG "WARN: requirements.txt not found at %REQS%"
)

REM Sanity: streamlit must be importable before we try to launch.
"%VPY%" -m streamlit --version >>"%LOG%" 2>&1
if !errorlevel! NEQ 0 (
    call :LOG "streamlit not importable -- attempting one-shot install"
    "%VPY%" -m pip install streamlit >>"%LOG%" 2>&1
    if !errorlevel! NEQ 0 (
        call :LOG "FATAL: streamlit install failed"
        call :REPAIR_INSTRUCTIONS
        pause
        exit /b 1
    )
)

REM --- Step 5: Launch ---------------------------------------------------
if not exist "%APP%" (
    call :LOG "FATAL: %APP% not found"
    echo.
    echo Cannot find app.py at %APP%
    echo This launcher must live in the same folder as app.py.
    echo.
    pause
    exit /b 1
)

call :LOG "launching: %VPY% -m streamlit run app.py"
echo.
echo Starting Ledger at http://localhost:8501
echo Press Ctrl+C in this window to stop.
echo.

REM Pass 19: bind to localhost only by default.
"%VPY%" -m streamlit run "%APP%" --server.address 127.0.0.1 --server.port 8501
set "RC=%ERRORLEVEL%"
call :LOG "streamlit exited with rc=%RC%"
if "%RC%" NEQ "0" (
    echo.
    echo Streamlit did not start cleanly.
    echo If port 8501 is busy, try:
    echo     "%VPY%" -m streamlit run app.py --server.port 8502
    echo.
    echo Diagnostics: see launcher.log
    echo.
)

pause
endlocal
exit /b 0


REM =====================================================================
REM Subroutines
REM =====================================================================

:LOG
REM Append a timestamped line to launcher.log AND mirror to stdout.
echo [%TIME%] %~1
>>"%LOG%" echo [%TIME%] %~1
goto :EOF

:REPAIR_INSTRUCTIONS
echo.
echo ------------------------------------------------------------------
echo  Manual repair commands (copy/paste into a fresh CMD window):
echo ------------------------------------------------------------------
echo  cd /d "%~dp0"
echo  rmdir /s /q .venv
echo  py -3.14 -m venv .venv
echo  .\.venv\Scripts\python.exe -m ensurepip --upgrade
echo  .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
echo  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
echo  .\.venv\Scripts\python.exe -m streamlit run app.py
echo ------------------------------------------------------------------
echo  If `py -3.14` is not available, substitute `py -3` or the full path
echo  to your Python install (e.g. C:\Path\To\Python314\python.exe).
echo  Diagnostics: launcher.log
echo ------------------------------------------------------------------
echo.
goto :EOF

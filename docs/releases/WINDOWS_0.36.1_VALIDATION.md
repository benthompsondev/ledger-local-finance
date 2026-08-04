# Windows 0.36.1 validation

Date: 2026-07-18

Status: local installer acceptance passed. Clean Windows VM acceptance is still required before calling this a final release.

## Silent-launch root cause

The original `Ledger.exe` was run without the one-directory `_internal` bundle. Reproducing that launch returned this PyInstaller error before Ledger's Python code could start:

```text
Failed to load Python DLL '_internal\python314.dll'.
LoadLibrary: The specified module could not be found.
```

That explains the silent launch from inside the zip. The old launcher could not write its own log or show an error because the Python runtime had not loaded yet.

The complete extracted bundle did not have a hidden-import or frozen-path failure. A temporary console build started Streamlit successfully from a path containing spaces. The old occupied-port path returned exit code 1 and wrote a traceback, but it had no Windows error dialog.

## Fix

- The primary artifact is now a single Inno Setup EXE. It always installs the complete PyInstaller one-directory bundle.
- Production uses the no-console PyInstaller bootloader.
- A small status window stays visible while Ledger starts.
- The launcher logs the executable, version, data directory, selected ports, server startup, HTTP health result, browser launch, reuse, shutdown, and exceptions.
- Readiness requires `200 ok` from `/_stcore/health`, not just an open TCP port.
- A second launch reuses the healthy server and opens the browser again.
- The default port falls back to a free loopback port when 8501 is occupied.
- Startup failures show a native Windows dialog with the exact launcher-log path.
- Upgrade and uninstall request a controlled server shutdown.
- Program files stay under `%LOCALAPPDATA%\Programs\Ledger`; private files stay under `%LOCALAPPDATA%\Ledger`.

## Exact artifact

```text
dist\Ledger-Setup-0.36.1-x64.exe
Size: 80,711,533 bytes
SHA-256: 1562D981D4F75A9C5A8EDECA2ACAD225701BE17FE46299178D1440DDB471CEA6
```

Built with PyInstaller 6.21.0 and Inno Setup 7.0.2.

## Local Windows results

| Check | Result |
|---|---|
| Fresh per-user install without elevation | Pass |
| Default install path | Pass |
| Start Menu launch | Pass |
| Optional desktop shortcut launch | Pass |
| First launch with no finance database | Pass |
| Browser launch after HTTP health | Pass |
| Home, Add Data, Goals, and Settings | Pass |
| Private files outside the program folder | Pass, none found |
| Second launch reused the same server PID | Pass |
| Default-port conflict selected a free port | Pass |
| Forced startup failure showed native dialog and log | Pass |
| Upgrade from installed 0.36.0 to 0.36.1 | Pass |
| Upgrade preserved SQLite integrity and test marker | Pass |
| Uninstall removed binaries, shortcuts, and registry entry | Pass |
| Uninstall preserved database and test marker | Pass |
| Reinstall found the preserved database | Pass |
| Production bundle from a path with spaces | Pass |
| No permanent console window | Pass |

Local validation logs are generated under `dist\windows-validation\`. They are intentionally untracked because launcher and installer logs can contain local paths.

## Antivirus and signing

The original zip-member failure was reproduced as a missing bundled DLL, not an antivirus event. Windows Defender could not run an on-demand scan on this machine because Defender's product features are disabled while Bitdefender is installed. No recent Defender detection was recorded, but that is not an antivirus clean bill of health.

The installer is not code-signed. `Get-AuthenticodeSignature` returns `NotSigned`, so SmartScreen warnings remain expected.

## Clean VM gate

Not passed. This host has Python installed and does not have Windows Sandbox, Hyper-V Manager, VirtualBox, QEMU, Docker, or Podman available for a clean no-Python Windows guest. No cloud VM or publishing step was authorized.

The exact installer above must still be installed and launched on a clean Windows VM with no Python installed. Until that happens, treat 0.36.1 as a locally validated release candidate, not the final Windows release.

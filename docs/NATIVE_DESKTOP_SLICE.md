# SignalSpace native desktop

SignalSpace's Windows product is a Tauri 2 desktop application. It covers
the core weekly workflow natively: Home, Add Data, Plan, Insights,
Transactions, and secondary Settings. It runs on SignalSpace's existing
deterministic Python finance engine.

## Architecture

- Tauri 2 owns the native window and Windows NSIS installer.
- React and TypeScript render the screens.
- Rust exposes narrow typed commands for Home, accounts, import preview and
  confirmation, manual transactions, transaction correction, Plan, Goals,
  Insights, backup/restore, and native file dialogs.
- Each data command spawns the packaged `ledger-engine`, writes one JSON
  request to stdin, reads one JSON response from stdout, and the process
  exits. No engine process outlives a request.
- No HTTP server, localhost listener, external browser, or console window is
  used.
- The Python engine reuses the existing `utils`/`parsers` modules — parsing,
  duplicate detection, categorization, and `weekly_checkin()` are the same
  code the Streamlit app runs. Nothing financial is re-implemented in
  TypeScript or Rust.

### Engine packaging

The engine is a PyInstaller **onedir** bundle shipped inside Tauri's resource
directory and spawned by absolute path. A onefile sidecar was measured
re-extracting ~85 MB on every request (~3.8 s per click); onedir answers in
under half a second. The frontend ACL is unchanged — the webview can still
invoke only the typed commands above.

## Data boundaries

The desktop shell sets `LEDGER_DATA_DIR` before the Python modules load.
Production uses:

```text
%LOCALAPPDATA%\Ledger
```

Within that private root:

- `finance.db` is the live SQLite database.
- `backups\` contains validated database backups.
- `logs\native-engine.log` contains engine runtime events.
- `logs\native-shell.log` records shell-side failures (for example, a missing
  or crashing engine) so startup problems are never silent.
- `config.json` remains the optional local configuration file.
- `exports\` remains the local export directory.

The installer must never include or delete this directory.

Program files use `%LOCALAPPDATA%\Programs\Ledger`. A small Tauri NSIS hook
overrides Tauri's normal current-user default because that default would
otherwise collide with Ledger's established private data root.

## Build

From the repository root:

```powershell
.\scripts\build_native_desktop.ps1
```

The script builds the onedir Python engine, builds the React frontend, and
asks Tauri to create the current-user NSIS installer at
`src-tauri\target\release\bundle\nsis\SignalSpaceFinance_<version>_x64-setup.exe`.

## Legacy Windows prototype (retired)

The earlier PyInstaller/Streamlit and Inno Setup files remain only as rollback
reference. They are **not** the native desktop shipping architecture and must
not be used to migrate more pages. Their build scripts
(`scripts\build_windows_installer.ps1`, `scripts\build_windows_portable.ps1`,
`scripts\build_windows_debug.ps1`) refuse to run without an explicit
`-ILegacyPrototype` switch.

## Rollback

1. Close SignalSpace.
2. Uninstall `SignalSpace` from Windows Installed Apps, or run
   `& "$env:LOCALAPPDATA\Programs\Ledger\uninstall.exe"`.
3. Confirm `%LOCALAPPDATA%\Ledger\finance.db` still exists. Do not delete the
   `%LOCALAPPDATA%\Ledger` folder.
4. Revert the local native commits with `git revert <commit>`.
5. Continue using the existing Streamlit launcher against the preserved
   database. The legacy source was not removed.

## Known native limitations

- CSV files that need custom column mapping must be mapped once in the
  Streamlit Add Data page; the native app then reuses the saved profile
  automatically (including its remembered destination account).
- Import undo and grouped review are not yet native.
- The watch-folder importer is not part of the native app.
- The unsigned installer still needs a real code-signing certificate and a
  clean-Windows-VM acceptance pass.
- A second launch currently opens a second native window.

## Proposed migration order

1. Single-instance focus and signed clean-VM release acceptance
2. Review (grouped corrections) and import undo
3. Native CSV column mapping
4. Optional AI-assisted explanations

Do not start the next slice until the installed workflow build has been
manually accepted.

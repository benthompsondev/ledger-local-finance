> **RETIRED.** This describes the legacy Streamlit/PyInstaller browser
> prototype, kept only as rollback reference. The supported Windows product
> is the native Tauri application - see `docs/NATIVE_DESKTOP_SLICE.md`.
> Its build scripts now refuse to run without `-ILegacyPrototype`.

Ledger for Windows - secondary portable build

1. Keep the whole Ledger folder together.
2. Extract the full folder before starting Ledger. Do not run Ledger.exe from
   inside the zip because the executable needs the adjacent _internal folder.
3. Double-click Ledger.exe. A small status window appears while the private local
   server starts, then Ledger opens in your default browser.

Your private data is not stored in this app folder. It lives under:

    %LOCALAPPDATA%\Ledger

That folder contains finance.db, backups, exports, settings, and launcher logs.
Replacing the portable app folder does not replace your data.

Ledger opens only on 127.0.0.1 and does not require an internet connection for
normal use. Optional AI categorization can contact the provider you configure.

Backup and restore

Open Settings, then Import & Backup. Create a backup before an unusual change,
and keep downloaded backup files private. Ledger also creates automatic recovery
points before migrations, import undo, and restore. Close and reopen Ledger after
a restore.

Unsigned build note

This private build is not code-signed. Windows SmartScreen may show a warning.
Only run a copy you built yourself or received directly from the repository owner.
Verify the zip's SHA-256 value against the accompanying .sha256.txt file.

Troubleshooting

Launcher logs are stored at:

    %LOCALAPPDATA%\Ledger\logs\launcher.log

Ledger should not be exposed to your LAN. Startup errors appear in a Windows dialog
with the launcher-log path.

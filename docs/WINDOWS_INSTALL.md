> **RETIRED.** This describes the legacy Streamlit/PyInstaller browser
> prototype, kept only as rollback reference. The supported Windows product
> is the native Tauri application - see `docs/NATIVE_DESKTOP_SLICE.md`.
> Its build scripts now refuse to run without `-ILegacyPrototype`.

# Ledger for Windows

Ledger installs for the current Windows user. It does not need administrator rights.

## Install

1. Double-click `Ledger-Setup-<version>-x64.exe`.
2. Keep the default install folder unless you have a reason to change it.
3. Choose whether to add a desktop shortcut.
4. Leave **Launch Ledger** checked on the last page.

The installer adds Ledger to the Start Menu and to **Settings > Apps > Installed apps**. Ledger opens in your default browser after its private local server is ready.

This build is unsigned. Windows SmartScreen may show **Windows protected your PC**. If you built the installer yourself or received it from the repository owner, select **More info**, then **Run anyway**. Check the SHA-256 value before running a shared copy.

## Where files go

Program files:

```text
%LOCALAPPDATA%\Programs\Ledger
```

Private data, backups, settings, and launcher logs:

```text
%LOCALAPPDATA%\Ledger
```

Installing a newer version and reinstalling Ledger leave the data folder alone.

## Upgrade

Run the newer setup EXE. Setup asks the running Ledger server to stop, replaces the application files, and keeps `%LOCALAPPDATA%\Ledger` unchanged. Launch Ledger after setup finishes so any database migration can run with its automatic backup.

## Uninstall

Open **Settings > Apps > Installed apps**, find **Ledger**, and select **Uninstall**. This removes the program files and shortcuts. It intentionally keeps `%LOCALAPPDATA%\Ledger` so an uninstall does not erase financial history.

To remove everything manually, first create and copy out any backup you want to keep. Uninstall Ledger, then delete `%LOCALAPPDATA%\Ledger` yourself. That final deletion is permanent and removes the database, backups, configuration, exports, and logs.

## Troubleshooting

Ledger writes startup details here:

```text
%LOCALAPPDATA%\Ledger\logs\launcher.log
```

If startup fails, Ledger shows a Windows error dialog with that same path. The local server binds only to `127.0.0.1`; it is not exposed to the local network.

## Roll back the application

1. Uninstall the current Ledger version through Windows Settings.
2. Install the previous setup EXE.
3. Launch Ledger. The existing `%LOCALAPPDATA%\Ledger` data remains in place.

If a newer release has already migrated the database, restore the automatic pre-migration backup from **Settings > Import & Backup** after reinstalling the older app. Keep a separate copy of the current data folder before a manual rollback.

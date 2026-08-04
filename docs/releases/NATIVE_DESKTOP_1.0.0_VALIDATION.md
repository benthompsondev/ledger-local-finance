# Northstar Ledger native desktop 1.0.0 validation

Northstar 1.0.0 makes the numbers conservative, the CSV workflow adaptable,
and Home, Plan, Insights, and Transactions tell one consistent story. This
record is completed from the exact packaged installer after installed-app
acceptance, not from development mode alone.

## Release artifact

- Installed product: `Northstar Ledger 1.0.0`
- Installer: `NorthstarLedger_1.0.0_x64-setup.exe`
- Size: 270,905,821 bytes (258.36 MiB)
- SHA-256: `29648885E252B3A52200A56AF013183E1F701DF26E8EF54590AB7777589CA156`
- Install mode: current user
- Install location: `%LOCALAPPDATA%\Programs\Ledger`
- Data root: `%LOCALAPPDATA%\Ledger`
- Network model: local Tauri window and packaged sidecar, with no TCP listener
- Authenticode: unsigned

## Automated verification

- Python: 237 tests passed
- React production build: passed
- Rust check: passed
- Rust tests: 3 passed
- Native doctor in strict mode: passed
- Python bytecode compilation: passed
- Tracked-file privacy check: passed

## Financial acceptance

Seven private statement exports were exercised only as local inputs against a
fresh disposable database and the exact packaged sidecar.

- Source rows parsed: 1,018
- Unique transactions stored: 916
- Overlapping rows skipped during the first import: 102
- Source-to-stored sign mismatches: 0
- Second-import rows inserted: 0
- Second-import duplicates skipped: 1,018
- 90-day Money In: $22,420.93 on both Home and Insights
- 90-day Spending: $20,890.99 on both Home and Insights
- Home spending donut: $20,890.99
- Insights spending categories: $20,890.99
- Income sources populated: 8
- Top merchants populated: 10

The test also confirmed that positive and negative e-transfers keep their
source sign and remain genuine money in or spending unless explicitly matched
as internal transfers. Payments, savings transfers, investment movements, and
internal transfers remain excluded from headline income and spending.

An upgraded copy of the regular application database contained 916
transactions. Home and Insights reconciled to the same 90-day income and
spending totals shown above, and the installed UI presented 8 review items.
The live regular database was never opened for writes during release testing.

## Installed workflows

- The exact installer completed with exit code 0.
- Installed Apps, executable file metadata, product metadata, and About all
  reported version 1.0.0.
- The Start Menu shortcut launched the installed executable successfully.
- Home, Insights, Transactions, Add Data, and Settings were inspected with
  populated disposable data.
- Last 30 days persisted after relaunch, as did comfortable display density.
- Home reflowed cleanly at 1,024 by 700 and the 900 by 620 minimum size.
- The native statement picker opened, accepted paths containing spaces, and
  recognized an invented unknown-layout CSV. Mapping, preview, confirm, sign
  preservation, and duplicate handling passed through the exact packaged
  sidecar. The Windows dialog automation layer could not press its final Open
  control reliably, so the installed mapping screen itself was not automated.
- The app opened no browser, console window, or TCP listener. Closing it left
  no Northstar or sidecar process running.

## Data safety and upgrade checks

- Backup/reset/restore was exercised on a disposable 916-row database.
- Reset removed all 916 rows and left 0 transactions.
- Restore returned all 916 rows, and the reset preserved preferences.
- Silent uninstall removed the program directory and Start Menu shortcut.
- The regular database remained byte-for-byte unchanged through uninstall and
  reinstall.
- Reinstall restored the 1.0.0 registry entry, executable, and shortcut, then
  launched successfully against the disposable upgraded copy.
- The installed tree contained 1,569 files and no CSV, PDF, database,
  spreadsheet, log, environment, or private-key files.

## Privacy boundary

Private statement exports and regular databases are local test inputs only.
They are never copied into the repository or bundle. Repeatable regressions
use invented merchants and synthetic amounts.

## Known limitations

- The installer and executables are unsigned, so Windows SmartScreen can warn.
- Imports remain file-based. Direct institution connections are not part of
  this release.
- PDF import remains beta and limited to known layouts; CSV is recommended.
- Net worth and Safe to Spend require current balances and enough confirmed
  planning information.

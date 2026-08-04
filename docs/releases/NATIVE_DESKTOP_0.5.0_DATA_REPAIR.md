# Northstar Ledger native desktop 0.5.0 validation

Version 0.5.0 repairs legacy imported data that predates the signed-amount
contract. The repair workflow requires the original statement files, previews
every affected batch and row, creates a validated SQLite backup, and updates
the database in one transaction. Deliberate user category corrections are
preserved.

Settings now also provides a typed-confirmation financial-data reset. Reset
creates a validated backup before removing financial records, rules, profiles,
plans, goals, and balances. App preferences remain unless complete reset is
selected, and the completion state offers the exact backup for restoration.

## Release artifact

- Installed product: `Northstar Ledger 0.5.0`
- Installer: `NorthstarLedger_0.5.0_x64-setup.exe`
- Size: `270,784,764` bytes (`258.24 MiB`)
- SHA-256: `F0B34FE1D3B61A3BBFC5BB085E84264AE48276C046DA28EF5BBBB342DBFADA3D`
- Install mode: current user
- Data root: `%LOCALAPPDATA%\Ledger`

## Validation

| Check | Result |
| --- | --- |
| Python suite | Pass, 172 tests |
| Rust suite | Pass, 3 tests |
| TypeScript/Vite production build | Pass |
| Rust release build and NSIS bundle | Pass |
| Installed Apps version | Pass, 0.5.0 |
| Installed EXE and installer metadata | Pass, 0.5.0 |
| Installed application launch | Pass |
| Legacy repair preview and atomic backup | Pass |
| Signed source-to-database reconciliation | Pass, zero mismatches |
| Home and Insights 30/90-day reconciliation | Pass |
| Signed transaction display and e-transfer labels | Pass |
| Review and recurring-cost cleanup | Pass |
| Duplicate re-import protection | Pass, zero new transactions |
| Reset, backup, and restore on disposable installed-sidecar data | Pass |
| Preference persistence after relaunch | Pass |
| TCP listener | None |
| External browser or console window | None |
| CSV, PDF, spreadsheet, or SQLite data bundled | None |

Private statement names, merchants, balances, and financial totals are
intentionally omitted from this repository validation record.

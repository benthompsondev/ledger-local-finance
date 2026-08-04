# Northstar Ledger native desktop 0.9.0 validation

Northstar 0.9.0 is a focused quality release. It repairs action routing,
refreshes safe merchant categorization on upgraded databases, makes cash-flow
cards and charts drill into the transactions behind them, improves Quick
Review, and adds clearer evidence-based goal progress.

## Release artifact

- Installed product: `Northstar Ledger 0.9.0`
- Installer: `NorthstarLedger_0.9.0_x64-setup.exe`
- Size: 270,872,081 bytes (258.32 MiB)
- SHA-256: `7E2F57595C65591A8CD615D8BECB85933CCBD3A3CBB107849F22788A6546351A`
- Install mode: current user
- Install location: `%LOCALAPPDATA%\Programs\Ledger`
- Data root: `%LOCALAPPDATA%\Ledger`
- Network model: local Tauri window and packaged sidecar, with no TCP listener
- Authenticode: unsigned

The exact NSIS artifact above installed with exit code 0. Its Installed Apps
entry, executable file metadata, desktop shortcut, and Start menu shortcut all
reported or targeted Northstar Ledger 0.9.0. The installed executable launched
against both an empty disposable data root and a populated copied database.

## Verification

| Check | Result |
| --- | --- |
| Python suite | Pass, 212 tests |
| TypeScript and Vite production build | Pass |
| Rust desktop suite | Pass, 3 tests |
| Copied regular database | Pass, 916 transactions |
| Safe categorization coverage | Improved from 49.6% to 80.4% |
| High-priority review flags | Reduced from 36 to 6 |
| Quick Review candidates | 8 total after migration |
| Fresh private-file preview | 1,018 source rows, no parse errors |
| Fresh first import | 916 inserted, 102 overlapping rows skipped |
| Duplicate re-import | 0 inserted, all 1,018 source rows skipped |
| Stored-sign comparison | 1,018 rows checked, 0 missing, 0 mismatches |
| Home and Insights | Income, spending, and chart totals reconcile exactly |
| Reset, backup, and restore | Pass, restored all 916 transactions |
| Installed first-run launch | Pass, calm empty state and Add Data action |
| Installed populated launch | Pass, populated Home and 8-item review badge |
| Installed process network check | Pass, 0 TCP connections/listeners |
| Installed bundle data scan | Pass, no CSV, SQLite, or private statement data |

The 0.9.0 migration scanned all 916 copied transactions and safely changed 260
rows: 151 category values, 65 financial types, and 30 review states. It retained
81 genuinely unresolved spending rows rather than making low-confidence guesses.

## Privacy boundary

Private statement exports and regular databases are test inputs only. They are
never copied into the repository or bundle. Public regressions use invented
merchant names and sanitized amounts.

## Remaining limitations

- The installer and executables are unsigned, so Windows SmartScreen can warn.
- A large payment-processor cluster remains uncategorized until the user teaches
  Northstar merchant-specific rules; treating the processor name as a category
  would create false confidence.
- Imports remain file-based. OFX/QFX and direct institution connections are not
  part of this release.
- Net worth and safe-to-spend forecasts remain unavailable until current account
  balances are entered.
- Validation was completed on this Windows host, not a separate clean virtual
  machine.

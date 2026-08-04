# Northstar Ledger native desktop 0.8.0 validation

Northstar 0.8.0 adds shared household allocation, a transaction review drawer,
actionable forecast readiness, clearer income terms, and Investing goals. All
financial calculations remain in the local Python engine.

## Release artifact

- Installed product: `Northstar Ledger 0.8.0`
- Installer: `NorthstarLedger_0.8.0_x64-setup.exe`
- Install mode: current user
- Data root: `%LOCALAPPDATA%\Ledger`
- Authenticode: unsigned

The final artifact size, SHA-256, installed launch result, and Installed Apps
metadata are recorded after the exact NSIS artifact is built and exercised.

## Pre-package verification

| Check | Result |
| --- | --- |
| Python suite | Pass, 193 tests |
| TypeScript and Vite production build | Pass |
| Rust check | Pass |
| Copied regular database | Pass, 916 signed amounts unchanged |
| Home and Insights, 90-day income and spending | Exact reconciliation |
| My Share and Household category totals | Both reconcile to their spending headline |
| Shared mortgage example | $2,622.45 retained; $1,311.23 user share |
| Fresh private-file import | 7 files, 1,018 source rows previewed |
| Fresh first import | 912 inserted, 106 overlaps skipped |
| Duplicate re-import | 0 inserted, all 1,018 source rows skipped |
| Backup, reset, and restore | Pass; restored transaction count matched |

No private statement, copied database, account identifier, merchant name, or
financial total was added to the repository. Private test paths were supplied
only to disposable local processes.

## Remaining limitations

- The installer and executables are unsigned, so Windows SmartScreen can warn.
- Shared reimbursements require user confirmation; Northstar does not infer a
  debt between people or settle shared balances.
- Investment goals track contributions or a saved account balance. There are
  no live holdings, market prices, or brokerage credentials.
- Imports remain file-based and validation is on this Windows host rather than
  a separate clean virtual machine.

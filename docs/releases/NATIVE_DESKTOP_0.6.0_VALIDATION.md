# Northstar Ledger native desktop 0.6.0 validation

Version 0.6.0 makes upgrades behave like fresh imports without rewriting the
signed financial record. A one-time, backed-up migration applies current safe
categorization and review rules only to eligible imported rows, while preserving
amounts, directions, deliberate user edits, and saved-rule results.

The native workflow also separates urgent review from ready category
suggestions, withholds plan forecasts until their required inputs exist, shows
expected dates for recurring costs, and removes a redundant Home forecast card.

## Release artifact

- Installed product: `Northstar Ledger 0.6.0`
- Installer: `NorthstarLedger_0.6.0_x64-setup.exe`
- Size: `270,804,230` bytes (`258.26 MiB`)
- SHA-256: `A805168A4C1C1B602E8F9FDF310142BF49FAE03B11DBD077DFD4EA14CCB98DDF`
- Install mode: current user
- Data root: `%LOCALAPPDATA%\Ledger`
- Authenticode: unsigned

## Upgrade validation

The regular application database was backed up with SQLite's online backup
mechanism before validation. The backup contained 624 transactions and passed
`PRAGMA quick_check`.

On first 0.6.0 launch, the migration scanned 624 imported transactions and
updated 27 eligible classifications. It changed no amount or direction and did
not touch rows with user provenance. High-priority review fell from 7 to 6;
deferred low-value review fell from 197 to 173; and 12 explainable category
suggestions became directly actionable. The migration marker makes subsequent
launches idempotent.

Home, Dashboard, and Insights produced identical income and spending totals for
both 30-day and 90-day periods. Plan forecasts remained unavailable because the
regular data did not contain every required plan and balance input.

## Fresh import validation

All seven supplied CSV statements were tested only in disposable local data
directories. No statement, database, account identifier, description, balance,
or private total was copied into the repository or bundled artifact.

| Check | Result |
| --- | --- |
| Source rows parsed | 1,018 |
| Parse errors | 0 |
| Unique transactions stored | 916 |
| Initial overlapping rows skipped | 102 |
| Source-to-parser sign mismatches | 0 |
| Source-to-database sign mismatches | 0 |
| Duplicate re-import | 0 inserted, all 1,018 rows skipped |
| Home, Dashboard, and Insights reconciliation | Exact for 30 and 90 days |
| Spending donut and ranked-bar reconciliation | Exact |
| Income donut and ranked-source reconciliation | Exact |
| Category correction refresh | Exact amount moved; headline total unchanged |
| Saved merchant rule | Applied and retained |
| Backup, reset, restore round trip | Pass; 916 rows restored |
| Sanitized asset/liability balance test | Pass; net worth updated immediately |

The final installed packaged sidecar repeated the seven-file import in another
fresh disposable directory. It stored 916 unique transactions, skipped the same
102 overlaps, passed `PRAGMA quick_check`, and then skipped all 1,018 rows on
re-import.

## Build and installed-app validation

| Check | Result |
| --- | --- |
| Python suite | Pass, 184 tests |
| Python compile check | Pass |
| Maintainer doctor | Pass |
| End-to-end smoke test | Pass |
| Rust suite | Pass, 3 tests |
| TypeScript/Vite production build | Pass |
| Rust release build and NSIS bundle | Pass |
| Installer metadata and Installed Apps version | Pass, 0.6.0 |
| Exact installer silent install and native launch | Pass |
| Settings requests after relaunch | Pass; no new database-lock error |
| Preference persistence | Pass; 90-day period survived relaunch and reinstall |
| Uninstall data preservation | Pass; 624 rows and signed-amount digest unchanged |
| Reinstall data discovery | Pass; regular database reopened automatically |
| Narrow-window navigation | Pass at 900 x 620 |
| TCP listener | None |
| External browser or console window | None |
| Private statement or database material bundled | None found |

The normal uninstall removes the program and Installed Apps entry but preserves
the local database and backups. Reinstalling this exact installer rediscovered
the data without import or repair prompts. The app is installed and running
against the regular data path for final user testing.

## Remaining limitations

- The installer and executables are not code-signed, so Windows SmartScreen may
  show a warning.
- The release was validated on this Windows host, not a separate clean Windows
  virtual machine.
- Imports remain file-based. There are no bank connections or live investment
  prices.
- Safe to Spend and projected net intentionally remain unavailable until a
  monthly plan, current balances, sufficient history, and aligned coverage are
  all present.

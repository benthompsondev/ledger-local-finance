# Northstar Ledger native desktop 0.7.0 validation

Version 0.7.0 is a focused clarity pass for Plan, Goals, Insights, and
Settings. It keeps financial calculations in the Python engine and gives the
native UI clearer monthly planning, goal progress, shorter insight sections,
and practical preferences.

## Release artifact

- Installed product: `Northstar Ledger 0.7.0`
- Installer: `NorthstarLedger_0.7.0_x64-setup.exe`
- Size: `270,833,900` bytes (`258.29 MiB`)
- SHA-256: `6C0C54C4A4E82DF2E49968115EF5C2B1324DE603CEF8C02F904381938EAF756C`
- Install mode: current user
- Data root: `%LOCALAPPDATA%\Ledger`
- Authenticode: unsigned

## What was checked

| Check | Result |
| --- | --- |
| Python test suite | Pass, 189 tests |
| Maintainer doctor | Pass |
| End-to-end smoke suite | Pass |
| TypeScript and Vite production build | Pass |
| Rust checks and tests | Pass, 3 tests |
| PyInstaller sidecar and NSIS release build | Pass |
| Installed Apps name and version | Pass, Northstar Ledger 0.7.0 |
| Exact installer silent install and native launch | Pass |
| Home, Dashboard, and Insights reconciliation | Exact for 30 and 90 days |
| Spending donut, ranked categories, and Spending | Exact totals |
| Income donut, ranked sources, and Money In | Exact totals |
| Plan equation and 90-day outlook | Exact two-decimal engine values |
| Goal create, pause, complete, and archive controls | Pass |
| Completed-goal progress | Pass, 100% with zero remaining |
| Installed preference persistence | Pass after full relaunch |
| Minimum window layout | Pass at 900 x 620 client size |
| Sanitized duplicate import | First import stored 4; second stored 0 and skipped 4 |
| Automatic recurring variable merchants | None included without confirmation |
| Findings limit | Pass, maximum three |
| TCP listener | None |
| External browser or console window | None |
| Private statement or database material bundled | None found |

The native window uses the installed WebView2 runtime for rendering. That is
the embedded Tauri view, not an external browser window or local web service.

## Upgrade and data safety

The final installer was first exercised against a copied regular database. The
copy passed SQLite `quick_check`, and the 30-day and 90-day headline numbers,
category totals, and income-source totals reconciled exactly.

The normal installed launch then created a SQLite-safe pre-migration backup in
the regular backup folder. The 0.7.0 migration corrected five eligible legacy
investment classifications. Transaction count, signed amounts, directions,
and the signed-record digest were unchanged, and the upgraded database passed
`quick_check`.

No private statement, copied database, account identifier, merchant name, or
financial total was added to the repository or installer.

## Remaining limitations

- The installer and executables are not code-signed, so Windows SmartScreen may
  show a warning.
- Validation was completed on this Windows host, not a separate clean virtual
  machine.
- The bundled Python analytics stack keeps the installer large.
- Imports remain file-based. There are no bank connections or live investment
  prices.
- Safe to Spend stays unavailable until its required plan, balance, history,
  and coverage inputs are present. Northstar does not invent a forecast when
  those inputs are missing.

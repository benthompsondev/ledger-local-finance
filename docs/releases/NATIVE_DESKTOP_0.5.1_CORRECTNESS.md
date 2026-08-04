# Northstar Ledger native desktop 0.5.1 validation

Version 0.5.1 separates current-period Money In from conservative Stable
Income, makes Safe to Spend depend on a confirmed plan and current balances,
and adds a plain-language formula breakdown. Recorded spending is shown in the
breakdown but is not subtracted twice because current balances already reflect
it.

Categorization now follows one deterministic precedence: protected financial
semantics, user merchant rules, bank statement memo, known merchant mapping,
then Uncategorized. Saved rules can be edited, disabled, or deleted. Bulk
merchant corrections can be undone. Recurring-cost detection uses genuine
outflows, cadence, amount stability, and known bill categories, with manual
include/exclude controls.

## Release artifact

- Installed product: `Northstar Ledger 0.5.1`
- Installer: `NorthstarLedger_0.5.1_x64-setup.exe`
- Size: `270,800,620` bytes (`258.26 MiB`)
- SHA-256: `9AC15C94382AD3CE5B4A2EDEE4B5DC5CE2974E3D60880257FF3D80F1FB83184E`
- Install mode: current user
- Data root: `%LOCALAPPDATA%\Ledger`

## Validation

| Check | Result |
| --- | --- |
| Python suite | Pass, 180 tests |
| Rust suite | Pass, 3 tests |
| TypeScript/Vite production build | Pass |
| Rust release build and NSIS bundle | Pass |
| Source-to-database signed-amount reconciliation | Pass, zero mismatches |
| Overlapping statement duplicate protection | Pass, zero new rows on re-import |
| Home and Insights 30/90-day reconciliation | Pass |
| Spending breakdown and headline total | Pass, exact reconciliation |
| Money In and Stable Income separation | Pass |
| Nesto mortgage and recurring classification | Pass |
| OpenAI and Anthropic recurring subscription detection | Pass |
| Forbidden income, transfer, payment, refund, or investment rows in recurring costs | Pass, zero rows |
| Safe to Spend missing-input state and formula reconciliation | Pass |
| Saved rule, bulk correction, immediate chart refresh, and undo | Pass |
| Manual investment value and net-worth update | Pass |
| Installed Apps version and installed application launch | Pass, 0.5.1 |
| Preference persistence after relaunch | Pass, 90-day selection survived process relaunch; prior 30-day view restored |
| TCP listener | None |
| External browser or console window | None |
| CSV, PDF, spreadsheet, SQLite data, or private statement names bundled | None |

Private statement names, transaction descriptions, account identifiers,
balances, and financial totals are intentionally omitted from this repository
validation record.

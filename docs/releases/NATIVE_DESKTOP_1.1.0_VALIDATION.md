# Northstar Ledger native desktop 1.1.0 validation

Northstar 1.1.0 is a focused usefulness release. Home now shows spending pace
and the top four month-over-month category changes without setup, while
Insights leads with the fuller same-day comparison and biggest movers.

## Release identity

- Product version: `1.1.0`
- Installer: `NorthstarLedger_1.1.0_x64-setup.exe`
- Runtime: Tauri 2 with the packaged local Python sidecar
- Data model: local SQLite under the selected application data directory
- Network model: no browser, localhost service, or TCP listener

## Calculation validation

- The populated synthetic profile spans four months, three account types, and
  204 invented transactions.
- Home and Insights use the same backend-owned spending-pace and category-pace
  packets. The proportional period breakdown reconciles to headline spending.
- Current and previous months are capped at the same day before comparison.
- The Amount Kept fallback uses the same day-capped month comparison when the
  selected rolling history is incomplete.
- Safe to Spend preserves a recurring bill's latest observed charge and only
  reserves commitments that have not cleared in the active month.

## Automated checks

- `python -m pytest -q`: 240 passed.
- `npm run desktop:check`: TypeScript/Vite build and Cargo check passed.
- `npm run desktop:test`: 3 Rust tests passed.

## Packaged-app evidence

- Installer path: `src-tauri/target/release/bundle/nsis/NorthstarLedger_1.1.0_x64-setup.exe`
- Size: 270,893,788 bytes
- SHA-256: `94EF8096003B85A5BB5F1D4F6DCB71DF81FACADEEE1410F04037AF63294C0E4A`
- Native bundle verification: 1,567 sidecar files; no database, statement,
  environment, log, or private runtime files.
- Install result: exit code 0. Installed Apps, executable product/file
  metadata, and Settings/About all reported `1.1.0`.
- Launch result: the exact installed executable opened against the disposable
  204-row profile without a browser, console child, or TCP listener.
- Home showed `$14,401` Money In, `$11,269` Spending, `$3,132` Amount Kept,
  a day-capped comparison, spending pace, four category comparisons, and a
  `$3,630` Safe to Spend value.
- Insights led with the same `$3,920` current-month spending pace/category
  total, non-zero deltas, and `Groceries increased $206` / `Gas / Transport
  decreased $14` as the biggest movers. Its rolling-period donut reconciled
  exactly to the `$11,269.26` headline spending total.
- Screenshot: `Northstar-1.1.0-Home.png` in the disposable validation folder.
- Screenshot: `Northstar-1.1.0-Insights.png` in the disposable validation folder.

The installed app was closed after validation so a normal shortcut launch
will use the user's regular local data path rather than the disposable profile.

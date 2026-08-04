# Northstar Ledger 2.5.3 - native desktop validation

Validated locally on 2026-08-03 from branch `feature/home-weekly-checkin`.
Nothing was pushed or published.

## Scope

This is the focused correction release following 2.5.2. It fixes:

- analysis-date cache isolation between SQLite databases;
- durable, deletable migration of legacy net-worth readings;
- calendar-aware net-worth comparisons;
- net-worth edit, blank-value and delete-confirmation behaviour;
- dated, single-currency account-balance prefills.

The implementation commits are `1f829f4`, `33f3179`, `8a9d76d`, `5b4794a`,
`536759d` and `7899f02`.

## Automated verification

| Check | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | 975 passed |
| `npm run test:charts` | 38 passed |
| `npm run build` | passed |
| `cargo check` / `cargo test` | passed / 5 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `scripts.check_tracked_files` / `scripts.doctor` | passed / passed |
| `git diff --check` | passed |

Every Python test used invented disposable data. The regular database at
`%LOCALAPPDATA%\Ledger\finance.db` was not opened.

## Packaged-engine smoke

The freshly packaged `ledger-engine.exe` was run against a new directory under
Windows TEMP. Four invented readings were saved for January, February, June and
July 2026. The package returned:

- four readings and a current net worth of $1,600;
- a real June-to-July month-over-month change of $100;
- no invented three-month comparison because April was not recorded;
- a six-calendar-month span containing four readings.

The disposable directory was removed after the smoke test.

## Installer

| | |
|---|---|
| Path | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.3_x64-setup.exe` |
| Size | 276,952,941 bytes (264.1 MB) |
| Bundled files | 1,567 |
| SHA-256 | `73BEA0C7FA2A1BF32165557643ECD2BA92884CF39F4ED6CDA189260A764BD82D` |

`scripts.verify_windows_installer` passed against this exact artifact and its
freshly built sidecar.

## Deliberately left for Ben's test

The installer was not launched or installed and the GUI was not controlled,
because Ben was using the computer while the package was built. The focused
manual checklist is `NET_WORTH_GUI_CHECKLIST_2_5_3.md`.

The installer remains unsigned and the app does not yet have an updater.

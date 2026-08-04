# Northstar Ledger native desktop 0.39.0 validation

## Status

Northstar Ledger 0.39.0 passed local Windows acceptance on July 18, 2026. The
exact installer was built, installed, and driven through the private weekly
workflow with disposable local storage. The existing `%LOCALAPPDATA%\Ledger`
database was unchanged after the upgrade and test cycle.

This build is ready for a private daily-use test. It is not approved as a
public release because the installer is unsigned and a clean Windows VM was
not available for an independent Python-free install check.

## Artifact

```text
src-tauri\target\release\bundle\nsis\NorthstarLedger_0.39.0_x64-setup.exe
```

- Size: `270,730,611 bytes` (about 258.2 MiB)
- SHA-256: `A9B290E1203895ED5140286E64805E0D0E1650D7113AAE162F11D721C0C4EF39`
- Authenticode: `NotSigned`
- Install scope: current user
- Program files: `%LOCALAPPDATA%\Programs\Ledger`
- Private data: `%LOCALAPPDATA%\Ledger`
- Stable application ID: `dev.benthompson.ledger`

## What was checked

The installed app completed these checks against a disposable data root:

- Fresh first run without a database-lock error.
- Native multi-file CSV selection and preview.
- Creation and assignment of chequing and credit-card accounts.
- Import of both private statement formats without recording statement values
  or descriptions in this document.
- Populated Home charts, Insights trends, merchants, recurring costs,
  findings, and account summaries.
- A category correction saved with user-edit provenance and refreshed the
  derived Home and Insights views.
- Exact-file re-import reported zero new transactions and skipped every known
  duplicate.
- Settings displayed version 0.39.0 and loaded backup controls without an ACL
  error.
- Clean close left no installed Ledger process or listening TCP port.
- Relaunch against the same disposable data root restored the populated Home
  view.
- Installed Apps showed one 0.39.0 entry, and both shortcuts targeted the
  installed executable.

The same importer and finance paths remain covered by fake/demo fixtures in
the automated suite. No private database, CSV, PDF, log, config, credential,
or API key is part of the installer payload.

## Automated checks

- Python: `156 passed in 9.87s`
- Native engine contract: `23 passed in 1.49s`
- Rust: `3 passed`
- `python -m compileall`: pass
- `cargo fmt --check`: pass
- `cargo clippy -- -D warnings`: pass
- TypeScript/Vite production build: pass
- `npm audit --audit-level=high`: 0 vulnerabilities
- Ledger doctor: pass
- Installer content scan: no private filename, database, statement, PDF, log,
  environment, private-key, or configuration payloads

The frontend package has no separate JavaScript test or lint script. TypeScript
compilation, the production Vite build, Rust tests, clippy, and the native
engine tests are the configured desktop checks.

## Fixed in 0.39.0

- CSV direction and merchant handling now agree with the canonical finance
  model.
- Home and Insights include the native chart and trend views needed for the
  weekly workflow.
- Cash-flow calculations use the same transfer and payment exclusions across
  native views.
- Exact-file re-import cannot move already-imported activity to another
  account.
- First-run database initialization waits for short SQLite locks, and Add Data
  is mounted only after it is opened.
- Account creation blocks repeated clicks while the sidecar request is active.
- Settings reads the installed application version through a narrowly scoped
  Tauri permission.

## Known limitations

- The installer and executables are unsigned, so Windows may show SmartScreen.
- A clean Windows VM was unavailable for this pass.
- A second launch can still open another native process/window.
- CSV files that need a custom column map must still be mapped once in the
  Streamlit reference app; native imports reuse saved profiles.
- Grouped review, import undo, watch-folder import, and optional AI explanations
  are not part of this native slice.

## Private test steps

1. Verify the installer hash with `Get-FileHash`.
2. Close Ledger, install the exact artifact, and launch it from the Start Menu.
3. Open Settings and confirm the storage location before importing anything.
4. Create a backup.
5. Preview a copied statement, confirm its destination account and totals, then
   import it.
6. Check Home and Insights, then correct one transaction category.
7. Preview the same statement again and confirm it reports zero new activity.

## Rollback

Uninstall Northstar Ledger from Installed Apps or run
`& "$env:LOCALAPPDATA\Programs\Ledger\uninstall.exe"`. Do not delete
`%LOCALAPPDATA%\Ledger`; uninstall and reinstall are designed to leave that
data directory in place.

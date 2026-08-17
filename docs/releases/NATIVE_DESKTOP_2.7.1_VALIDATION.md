# Northstar Ledger 2.7.1 validation

Validated locally on 2026-08-17. This patch makes Plan one clear monthly
decision and fixes recurring-cost choices that could disagree with the money
reserved in the plan. All finance checks used invented data and disposable
database folders. The normal database under `%LOCALAPPDATA%\Ledger` was not
opened or changed.

## What changed

- Plan shows one monthly equation and keeps saved figures distinct from
  unsaved edits.
- A saved plan survives relaunch, and editing it updates the same monthly row.
- Saved savings preferences still seed a new plan without bringing back the
  removed preset controls.
- Hidden legacy plan notes survive an edit instead of being erased.
- Confirming or rejecting monthly, quarterly and annual recurring costs now
  changes the Plan reserve consistently.
- Quarterly and annual commitments remain nonmonthly reserves after review,
  including when the user has renamed the merchant.
- Last Plan Result remains a presentation of canonical engine results. The
  frontend no longer reconstructs an absolute amount.
- Removed What-if and Money Focus frontend code and descriptions were cleaned
  up. Existing database rows and tables were deliberately left untouched.

## Verification

| Check | Result |
| --- | --- |
| Focused Plan, recurring-cost, shared-expense and release tests | 50 passed |
| `.venv\Scripts\python.exe -m pytest -q` | 1,246 passed |
| `npm run test:charts` | 95 passed |
| `npm run build` | passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check --manifest-path src-tauri/Cargo.toml` | passed |
| `cargo test --manifest-path src-tauri/Cargo.toml` | 5 passed |
| `.venv\Scripts\python.exe -m scripts.check_tracked_files` | passed |
| `.venv\Scripts\python.exe -m scripts.doctor --strict` | passed |
| `git diff --check` | passed; only expected line-ending notices |

The full Python suite passed before the release-only version and documentation
changes. The focused version-consistency test, frontend suite, production
frontend build, Rust check and Rust tests passed again at 2.7.1.

## Native persistence smoke

The complete development path was exercised with a disposable database:
React to Tauri to Rust command to the packaged Python sidecar to SQLite and
back to React.

- The first save created one August plan with a keep amount of $333.33.
- Two edits changed the same row to $444.44 and then $555.55.
- The preview changed immediately, while the saved-state label changed only
  after the save completed.
- After the native app was fully stopped and relaunched, Plan restored
  $555.55 and showed it as saved.
- The final optimized 2.7.1 binary repeated the relaunch check against the same
  disposable data and restored the saved amount.

The installer was deliberately not run over the existing Northstar
installation.

## Signed release artifacts

- Installer: `NorthstarLedger_2.7.1_x64-setup.exe`
- Size: 280,887,670 bytes
- SHA-256: `D8F03CBEFD66E07F9CEF3E51C0FBE6E2E6C8FA5798C617BE7687AA779368C5C0`
- Detached updater signature: 428 bytes
- Bundled files checked: 1,567

## Known release limitations

- The updater signature protects the update path, but the installer is not
  Authenticode-signed. Windows SmartScreen may still warn on a new machine.
- Automatic shared-expense rules remain advisory by design. Only explicit
  transaction-level shared allocations affect canonical actual cash flow.
- Coach remains a beta. No external AI provider call was needed for this
  release.

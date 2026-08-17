# Northstar Ledger 2.7.0 validation

Validated locally on 2026-08-17. This release adds two focused ways to look
back at a finished month: Counterfactual Replay in Insights and Last Plan
Result in Plan. All finance checks used invented data and disposable database
folders. The normal database under `%LOCALAPPDATA%\Ledger` was not opened or
changed.

## What changed

- Counterfactual Replay can reduce one real category or merchant by 25%, 50%
  or 100% and compare the finished month with the deterministic replay.
- The replay uses Northstar's canonical transaction direction, exclusions,
  shared-expense handling and cash-flow calculations.
- Last Plan Result compares a finished month only with its own exact saved
  plan when the plan timing and statement coverage are trustworthy.
- New monthly-plan writes use UTC consistently. Legacy rows keep their stored
  timestamps and are not given a historical verdict when their timing cannot
  be proved.
- The old calendar-sensitive Insights test now freezes its intended July 2026
  analysis date instead of failing as the real calendar moves forward.

## Verification

| Check | Result |
| --- | --- |
| Focused Counterfactual Replay, Last Plan Result, timestamp and version tests | 54 passed |
| `.venv\Scripts\python.exe -m pytest -q` | 1,276 passed |
| `npm run test:charts` | 100 passed |
| `npm run build` | passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check --manifest-path src-tauri/Cargo.toml` | passed |
| `cargo test --manifest-path src-tauri/Cargo.toml` | 5 passed |
| `.venv\Scripts\python.exe -m scripts.check_tracked_files` | passed |
| `.venv\Scripts\python.exe -m scripts.doctor` | passed |
| Freshly packaged engine with disposable demo data | passed |
| `git diff --check` | passed; only expected line-ending notices |

The first full Python run exposed the existing calendar-driven Insights test.
The same failure existed at the 2.6.3 baseline. Its fixed July fixture was made
deterministic by freezing the analysis date in the test only; no product
comparison logic changed. The complete suite then passed.

## Packaged-engine smoke

The freshly built `ledger-engine.exe` was run directly with
`LEDGER_DATA_DIR` pointed at generated demo data under the ignored `build`
directory. It returned:

- a deliberately unavailable Last Plan Result because no finished month had
  both complete coverage and an eligible exact saved plan;
- a July 2026 Counterfactual Replay using the Housing / Mortgage category;
- an actual net cash flow of $1,092.76 and a counterfactual net cash flow of
  $1,992.76 after a 50% reduction, freeing exactly $900.00.

This exercised the packaged Python sidecar and its real JSON request contract.
The focused hardening passes separately exercised the complete React to Tauri
to Rust to packaged-sidecar path for both features before this release build.

## Signed release artifacts

- Installer: `NorthstarLedger_2.7.0_x64-setup.exe`
- Size: 280,909,597 bytes
- SHA-256: `CE0122C67E27939FDA69D657EE99ABF34308432025FC35AC839B9F657705EE5D`
- Detached updater signature: 428 bytes
- Bundled files checked: 1,567
- `latest.json`: version 2.7.0, HTTPS release URL, validated
- Manifest signature matches the final `.exe.sig` byte for byte

The 2.7.0 installer was deliberately not installed over the existing app. The
real update test is Settings > App updates discovering this release from the
published `latest.json`, then downloading and installing it through Northstar's
built-in updater.

## Known release limitations

- The updater signature protects the update path, but the installer is not
  Authenticode-signed. Windows SmartScreen may still warn on a new machine.
- Coach remains a beta. No external AI provider call was needed for this
  release.

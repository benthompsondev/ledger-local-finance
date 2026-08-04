# Northstar Ledger 2.3.0 — native desktop validation

Private release candidate. Built, verified, installed and smoke-tested on
2026-07-31. Not pushed, not tagged, not published.

## What is in this build

Ten commits on `feature/home-weekly-checkin`, none of them pushed.

| Commit | Change |
|---|---|
| `665abf3` | Stop month-end pace from dropping the current month's trailing days |
| `7a66c87` | Subtract nonmonthly reserves everywhere the plan computes flexible room |
| `e26d137` | Compute the analysis anchor once per request instead of 24 times |
| `07606a3` | Validate and describe the native product, and close the privacy guard gap |
| `8d4cf12` | Stop Plan counting nonmonthly bills as fixed costs and reserve |
| `b2b235e` | Compare category averages over the same span the overall average uses |
| `8a0aea9` | Allow exact paths in the privacy guard, never whole directories |
| `ffce4f3` | Document the demo flow that exists, and say what AI actually sends |
| `13f242f` | Describe the navigation the app actually has |
| `9c12c1b` | Prepare Northstar Ledger 2.3.0 release candidate |

Two of these fix money defects that were visible on screen:

- **Month-end truncation.** On the 31st of a month following a 30-day month,
  every transaction dated the 31st vanished from the spending pace, the
  category breakdown and the category totals. Where all recent activity fell on
  that day, the category panel read zero beside a populated category list.
- **Plan double count.** A quarterly invoice was added to fixed costs at its
  full value and then subtracted again as a monthly reserve. A $300 quarterly
  bill with a $100 monthly set-aside removed $400 a month instead of $100.

## Pre-build validation

Every gate run at `9c12c1b` before packaging. Nothing was skipped or weakened.

| Check | Result |
|---|---|
| `git diff --check` | clean |
| `pytest -q` | **651 passed** |
| `npm run build` | passed |
| `npm run test:charts` | **6 passed** |
| `npm audit` | 0 vulnerabilities |
| `cargo check --manifest-path src-tauri/Cargo.toml` | passed, `ledger-native v2.3.0` |
| `cargo test --manifest-path src-tauri/Cargo.toml` | **5 passed** |
| `scripts.doctor` | passed |
| `scripts/check_tracked_files.py` | no private artifact paths tracked |

## Artifact

| | |
|---|---|
| Path | `src-tauri\target\release\bundle\nsis\NorthstarLedger_2.3.0_x64-setup.exe` |
| Size | 276,925,309 bytes (264.1 MiB) |
| SHA-256 | `EF0E88490B84AD1BF097269C70F6836F6A2FC8A263C07F1FCA3D4E7D5353B08E` |
| Bundled files | 1,567 |
| Packaged sidecar | 189.8 MiB |
| Authenticode | **NotSigned** |

The SHA-256 was computed independently after the build and matches the value
the in-build verifier reported.

`scripts/build_native_desktop.ps1` now runs `scripts.verify_windows_installer`
in module mode against the exact final artifact and fails the build if
verification fails. It passed inside the build and again when run explicitly
afterwards.

### Packaged content scan

The packaged sidecar contains none of:

- `finance.db` or `finance.demo.db`, or any `.db` / `.sqlite` / `.sqlite3`
- `.csv`, `.pdf`, `.ofx`, `.qfx`, `.xls`, `.xlsx`
- `.env`, `config.json`, or anything matching secret / api-key / credential
- `.log` files
- Streamlit or browser runtime entrypoints
- private download or desktop paths

## Installation

Installed silently over the existing per-user 2.2.0. The data directory was not
removed and no uninstall was performed.

| | |
|---|---|
| Installer exit code | 0 |
| Installed Apps | Northstar Ledger **2.3.0** |
| Shell executable | `%LOCALAPPDATA%\Programs\Ledger\ledger-native.exe`, ProductVersion 2.3.0, FileVersion 2.3.0 |
| Packaged engine | `%LOCALAPPDATA%\Programs\Ledger\engine\ledger-engine.exe`, present |

The engine executable carries no version resource. That is normal for a
PyInstaller bundle and is not a packaging fault.

## Installed-build smoke test

Run against a new disposable directory under `%TEMP%` with
`LEDGER_DATA_DIR` and `LEDGER_DEMO_DB=1`, seeded with generated invented data.
Every request went to the installed `ledger-engine.exe`, not to the repository
source.

### Read actions

| Action | Result |
|---|---|
| `home_summary` 30 days | pass |
| `home_summary` 90 days | pass |
| `home_dashboard` | pass |
| `insights_summary` 30 days | pass |
| `insights_summary` 90 days | pass |
| `plan_summary` | pass |
| `ai_coaching_summary` | pass |
| `list_transactions` | pass |
| `list_accounts` | pass |

### Signed CSV import

An invented three-row statement with one credit and two debits.

| Check | Result |
|---|---|
| Preview succeeded | pass |
| Preview preserved `+1500.00`, `-4.50`, `-82.10` | pass |
| First import inserted | **3 rows** |
| Re-import inserted | **0 rows** |
| Re-import skipped as duplicates | **3 rows** |

Duplicate protection therefore holds through the packaged sidecar, not only in
the test suite.

An earlier run of this harness failed to create its account because the demo
seed had already made one with the same name. The app was right to refuse a
duplicate account name; the harness was corrected to use a unique one.

### Cross-screen agreement

Home and Insights returned an identical analysis context for the same 90-day
period, so both screens describe the same window and the same data-through
date.

### Runtime posture, AI disabled

| Check | Result |
|---|---|
| App launched and stayed up | yes, window title "Northstar Ledger", responding |
| TCP listeners owned by the app tree | **none** |
| Outbound connections from `ledger-native.exe` | **none** |
| External browser window opened | none |
| Console window | none |
| `ledger-engine.exe` resident between requests | no, one-shot per request as designed |

One honest qualification. The app's own process made no outbound connection,
but its embedded `msedgewebview2.exe` child held two connections to a Microsoft
endpoint on port 443. That process is the Windows-supplied WebView2 runtime
from `C:\Program Files (x86)\Microsoft\EdgeWebView`, and the traffic is its own
update and telemetry behaviour rather than anything Northstar requested. No AI
provider request was made. Worth recording because "no network activity at all"
would be the wrong claim for anyone reading this later.

### Data isolation

The normal `%LOCALAPPDATA%\Ledger` directory was captured before the run as 19
files with sizes and modification times, and compared afterwards. It was
**identical**. `finance.db` was never opened, copied or read at any point.

## Verified by a person, not by this pass

These need a human at the screen and were not confirmed here:

- Settings/About visibly showing 2.3.0
- The six tabs rendering as Home, Add Data, Plan, Insights, Transactions, Coach
- Settings appearing as the header gear and no Goals tab
- Plan's "Review changes" scrolling, focusing and highlighting its destination
- "Use reviewed commitments" updating the preview
- A saved plan persisting across a close and relaunch, and `plan_drift` clearing
- No blank screen or unhandled error during ordinary use

The code paths behind each of these were exercised through the packaged
sidecar and are covered by the test suite. What is unverified is the visual
result.

## Remaining limitations

- **The installer is unsigned.** Windows SmartScreen will warn on a machine
  that has not seen it before.
- **There is no updater.** Moving to a later version means running a new
  installer by hand.
- **A private SQLite database remains in git history** at commit `f06758b`,
  reachable from the public `feature/home-weekly-checkin` branch. This build is
  private and unpushed; the branch must not be published until that history is
  cleaned and reviewed.
- Analytics screens take a few seconds against a synthetic 50,000-row ledger.
  Not a concern at present data sizes and deliberately out of scope here.

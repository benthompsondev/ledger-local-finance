# Northstar Ledger 2.6.2 validation

Validated locally on 2026-08-12 from public 2.6.1 commit `5892362`. This was a
trust-boundary pass, not a feature release. All finance tests used invented
data and disposable `LEDGER_DATA_DIR` folders. The normal database under
`%LOCALAPPDATA%\Ledger` was never opened or changed.

## Reproduced defects

### Plan accepted non-finite amounts

The Python JSON boundary accepted positive infinity as expected income. The
value passed every ordinary comparison and reached `monthly_plans`, leaving a
plan that could poison later totals and forecasts. One plan amount parser now
rejects NaN, positive or negative infinity, negative values and implausibly
large typo values before preview or save.

Regression: `tests/test_release_boundaries_2_6_2.py` proves preview and save
reject non-finite inputs and leave the database unchanged.

### Restore accepted a SQLite file that was not a usable Ledger database

Restore previously checked SQLite integrity and three table names. A file with
those names but no required columns passed validation, replaced the live file,
and then failed on startup with `no such column transaction_date`.

Validation now checks required core columns, the unique transaction dedup
constraint, ISO transaction dates, finite numeric amounts and supported
directions before the candidate can replace the working database. Legacy
pre-migration recovery retains its existing migration path.

Regressions cover a table-name-only database, a malformed transaction, a
missing dedup constraint and a backup created while another WAL connection has
an uncommitted row. In every refusal case the working database remains intact.

### Old imports weakened a starter plan

When the newest transaction history was stale, the starter-plan generator used
the user-facing calendar fallback as its historical anchor. That shifted its
lookback into empty months. Three steady $2,000 income months could therefore
be proposed as $1,333.33 or zero instead of $2,000.

The proposal now uses the latest supported data month for its evidence window.
The screen can still say the import is stale; the historical baseline no longer
pretends empty calendar months contain evidence.

### Supplied analysis dates could be ignored

`monthly_progress(today=...)` called `analysis_anchor()` without forwarding
that date. Historical, leap-year and boundary evaluations could silently read
the computer's current date instead. The supplied date now reaches the shared
analysis-period helper.

### Future net-worth readings looked measured

The net-worth chart promises that every point is a reading the user entered,
not a projection, but the form and engine accepted future months. The native
form now caps the month selector at the current month and the engine refuses a
future month even if a caller bypasses the form.

### Unavailable Safe to Spend claimed it was balance-checked

Home used the `balance-checked` eyebrow for every result that was not the flow
estimate, including `Not available`. It now uses the neutral `Safe to spend
now` label when the number is unavailable.

## Additional boundary evidence

- Import preview remains guidance rather than a stale write plan. A regression
  previews a two-row file, imports it through another request, then confirms
  the original selection. Confirm recalculates against the current database
  and skips both rows.
- A backup taken while a separate WAL writer holds an uncommitted insert is
  valid, contains the committed row and excludes the uncommitted row.
- The canonical `safe_to_spend_summary` path remains shared by Home and Plan.
  This release did not recreate any finance calculation in React.

## Automated verification

| Check | Result |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -q` | 1,215 passed |
| `npm run build` | passed |
| `npm run test:charts` | 92 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check --manifest-path src-tauri/Cargo.toml` | passed |
| `cargo test --manifest-path src-tauri/Cargo.toml` | 5 passed |
| `.venv\Scripts\python.exe -m scripts.check_tracked_files` | passed |
| `.venv\Scripts\python.exe -m scripts.doctor` | passed |
| `git diff --check` | clean; Git reported only expected LF/CRLF notices |

The suite covers the repository's synthetic CSV matrix, overlapping and
out-of-order imports, account-aware deduplication, planning dates and income
cadences, Safe to Spend reconciliation, net-worth editing and migration,
backup/restore, engine concurrency and updater manifest behavior.

## Exact packaged build

- Installer: `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.6.2_x64-setup.exe`
- Size: 277,837,954 bytes
- SHA-256: `6DF155F0B46C76AD8667A657AAB493E5D002DE8323B96C59E6AF2064151668B6`
- Signature: `NorthstarLedger_2.6.2_x64-setup.exe.sig`, 428 bytes
- Updater manifest: local `latest.json`, version 2.6.2
- Manifest signature equals the final `.exe.sig` byte for byte
- Manifest validation: passed

The manifest URL intentionally names the future GitHub `v2.6.2` release. No
tag, release or artifact was published during this pass, so the live updater
cannot find 2.6.2 until Ben chooses to publish it.

## Install and packaged-engine verification

The exact installer above installed over 2.6.1 with exit code 0.
`scripts.verify_installed_app` passed and found one Northstar Ledger entry:

- Installed Apps: Northstar Ledger 2.6.2
- `ledger-native.exe`: 2.6.2
- `uninstall.exe`: 2.6.2

The installed `ledger-engine.exe`, not the repository Python module, passed 32
checks against a fresh disposable database. The checks covered:

- empty first run;
- account and balance creation;
- three-row CSV preview and import;
- exact re-import skipping all three duplicates;
- source signs of +$2,000.00, -$75.25 and +$25.00;
- transaction recategorization;
- Plan save and packaged rejection of infinity;
- Home, Plan, Insights, review, patterns, Money Focus, Settings and data-safety
  payloads;
- deterministic Coach data with AI disabled;
- a $3,500 monthly net-worth reading;
- future net-worth refusal;
- backup, sentinel write, restore and sentinel removal;
- three transactions still present through independent engine processes.

No packaged sidecar remained after the requests.

## Native-window smoke

The installed `ledger-native.exe` was launched twice against the same disposable
profile. Both launches opened a window titled `Northstar Ledger`, created no
listening TCP socket, opened no external browser, closed normally and left no
Ledger process behind. The disposable engine log contained no `database is
locked` marker. Its two tracebacks were expected validation failures from the
deliberate infinity and future-net-worth probes.

Desktop automation could list and launch the native window but could not attach
an interactive control context in this Codex session. Therefore this is not a
claim that every button was clicked in the packaged UI. The screen actions were
exercised through the installed sidecar, and the native shell itself was
launched and relaunched, but the final manual click-through remains for Ben.

## Remaining limitations

- The installer is signed for Tauri's updater but is not Authenticode-signed,
  so Windows SmartScreen may still warn on another machine.
- The live updater was not exercised because publishing was explicitly out of
  scope. The local final signature and manifest do reconcile exactly.
- Optional external AI providers were not called. Coach is still a beta and
  deterministic local findings work with AI off.
- CSV import cannot infer a statement's official coverage dates when the bank
  omits them. Sparse-account completeness therefore remains conservative.
- The final hands-on control-by-control GUI checklist remains a manual smoke
  test because desktop automation could not attach to the window.

Nothing was pushed, tagged, released or history-rewritten.

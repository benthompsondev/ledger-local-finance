# Northstar Ledger 2.5.1 — independent trust repair and validation

Built, installed and validated on 2026-08-02. **Not pushed, tagged or
published.** This release is a focused correction to the unpushed 2.5.0 work.
Every database used for testing was invented and disposable. The regular
`%LOCALAPPDATA%\Ledger\finance.db` was not opened; its timestamp and containing
directory count were unchanged across the final install.

## What the independent review found

### Import preview and confirm disagreed

The desktop preview still used the old row-occurrence rule while confirmation
used the new whole-file provenance rule. The same two files could therefore say
`1 new / 1 duplicate` in preview and then insert two rows.

Preview now calls the real persistence path inside a SQLite savepoint and rolls
it back. The user sees the exact counts that confirmation will commit, including
the order in which several selected files are evaluated.

### A revised overlapping export duplicated shared rows

2.5.0 treated any file that did not fully reproduce an earlier batch as wholly
new. If a bank removed or corrected one earlier row, the next export inserted
all of its shared rows again.

Northstar now distinguishes three cases:

- an exact file re-import, which skips automatically;
- a file with no overlap, which imports as new activity;
- every other overlap, which is genuinely ambiguous and is not guessed.

For every non-identical overlapping file the import screen asks whether it is an
updated export or separate activity. The first choice skips matching rows; the
second keeps identical-looking purchases as real repeated activity. Confirmation
fails closed until one is selected. Full containment is still only overlap, not
proof: a different statement can carry the same two real purchases.

This is deliberately conservative. No row-only rule can prove whether identical
activity in two different files is a duplicate or two real purchases.

### Sparse accounts could still form a false coverage mosaic

The 2.5.0 per-account rule checked only each account's earliest and latest date.
An account with one row near each edge of a month could still anchor a combined
month whose middle was missing.

One actual import batch must now reach both ends of the month and carry the
required number of distinct active days before it can establish complete-month
coverage. Several fragments from one account can no longer form a trusted month.
A legitimately quiet account still cannot veto another statement that provides
complete coverage.

### Public-facing local-data wording was too absolute

The Home footer said no network service was used even though optional online AI
providers make outbound HTTPS requests when enabled. It now says that all
financial calculations are local and optional AI connects only when enabled.
Two empty-state month labels also retain normal capitalization.

## Regression coverage added

- ambiguous partial overlap requires an explicit decision;
- full containment without an exact file hash still requires a decision;
- revised export with removed rows can skip matching activity;
- separate repeated purchases can be kept deliberately;
- exact one-row file re-import remains automatic;
- multi-file preview respects selection order and rolls back completely;
- preview and confirmation report identical new and duplicate counts;
- two sparse edge rows cannot establish complete-month coverage.
- three partial import batches from one account cannot form complete coverage.

## Verification

| Check | Result |
|---|---|
| `pytest -q` | **872 passed** |
| `npm run build` | passed |
| `npm run test:charts` | 16 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` | passed, `ledger-native v2.5.1` |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | passed |
| `scripts.doctor` | passed |
| `git diff --check` | clean |

The final installed packaged engine passed 13 of 13 focused checks against a
disposable `LEDGER_DATA_DIR`. These covered first import, full-containment
overlap refusal, explicit separate-activity choice, signed persistence across
fresh engine processes, three same-account fragments remaining partial, and all
five guided cloud/local providers being present. An earlier broader 18-check
packaged run also covered exact re-import idempotency, Home/Insights loading,
no-key local providers, and actionable unreachable-local-model guidance.

A live MiniMax request using invented aggregates succeeded with the user's
locally stored key. Anthropic and OpenAI were not contacted live because no
throwaway API keys were available; their wire protocols are covered by local
HTTP server tests. Ollama and LM Studio were not installed on this computer;
their OpenAI-compatible route and connection-failure guidance were tested.

The exact final installed GUI was driven with invented CSV files. It:

- blocked a non-identical file that fully contained two earlier rows until a
  choice was made;
- changed from `one overlap decision needed` to `3 new / 0 known duplicates`
  after choosing separate activity;
- imported exactly those three rows;
- showed truthful stale-data wording instead of inventing a forecast;
- showed Northstar Ledger 2.5.1 in Settings;
- showed the corrected optional-AI network wording.

## Installer and installed state

| | |
|---|---|
| Installer | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.1_x64-setup.exe` |
| Size | 276,946,445 bytes (264.1 MB) |
| Bundled files | 1,567 |
| SHA-256 | `F431E0753CE797E3CF2FD4EECA6B6400F8CF262C4A23BD87E807B7DC502C315A` |
| Silent install | exit code 0 |
| Installed Apps | one Northstar Ledger entry, version 2.5.1 |
| Installed executables | `ledger-native.exe` 2.5.1; `uninstall.exe` 2.5.1 |

The inherited machine state initially still showed a 2.2.0 registry version even
though the 2.5.0 executables were present. Reinstalling the exact 2.5.0 artifact
repaired it, demonstrating that the package was sound but the handoff's installed
snapshot was stale. The final 2.5.1 artifact was then installed and checked
independently.

## Remaining limitations

- The installer is unsigned and has no updater. SmartScreen may warn on another
  computer.
- Python 3.12 is not installed here, so the handoff's isolated 3.12 month-end
  pace failure remains uninvestigated. CI and the installer use Python 3.14.
- A quiet partially exported account cannot always be distinguished from a
  genuinely quiet account when the bank CSV omits its statement period.
- No live Anthropic or OpenAI provider request was made in this pass.
- `cargo fmt --check` still reports pre-existing whole-file formatting drift in
  `src-tauri/src/main.rs`; no unrelated bulk reformat was included.

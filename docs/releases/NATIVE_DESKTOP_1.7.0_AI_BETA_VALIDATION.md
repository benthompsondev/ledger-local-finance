# Northstar Ledger 1.7.0 AI beta validation

Validated on Windows 11 on 2026-07-25.

## Release state

- Feature commit: `bc4315b`
- First-use MiniMax fix: `25ed2ca`
- Branch: `feature/home-weekly-checkin`
- Product, package, Tauri, Cargo, Python, executable, installer, and Installed
  Apps version: `1.7.0`
- No push or publication was performed.

## What changed

- MiniMax now defaults to the current `MiniMax-M3` model and the official
  OpenAI-compatible `https://api.minimax.io/v1` endpoint.
- A first-use MiniMax path replaces the old custom-endpoint-first setup.
- A connection check verifies provider, key, and model without sending finance
  data.
- Cloud keys are protected with Windows DPAPI for the current Windows user.
  Existing plaintext keys are migrated when the setting is read.
- Remote endpoints require HTTPS. Only localhost and IP loopback addresses are
  described as staying on this computer.
- Questions, transaction payloads, and provider errors are scrubbed for
  account-number and key leakage.
- MiniMax reasoning is split from the answer and reasoning tags are removed
  before display.
- Every AI answer receives a local currency/percentage grounding check. An
  unsupported figure triggers one stricter retry; a repeated unsupported figure
  hides the answer.
- The native app now has an **AI Beta** workspace with three practical starter
  questions, free-text questions, sharing-scope disclosure, coverage date,
  provider/model disclosure, and grounding status.

## Provider compatibility

The user-supplied MiniMax Token Plan key was used for a live compatibility
preflight without being written to the repository:

| Check | Result |
| --- | --- |
| Existing `abab6.5s-chat` preset | HTTP 400, unknown/obsolete model |
| `MiniMax-M3` at `https://api.minimax.io/v1` | HTTP 200 with the expected response |

The exact key was not copied into a source file, test fixture, validation
document, or packaged artifact.

## Automated verification

| Check | Result |
| --- | --- |
| Python suite | 444 passed |
| AI boundary tests | 27 passed |
| Chart scale tests | 2 passed |
| React/TypeScript production build | passed |
| Rust/Tauri command and ACL tests | 5 passed |
| Git diff whitespace check | passed |

The AI tests use invented secrets and mocked OpenAI-compatible responses. They
cover DPAPI storage and plaintext migration, remote HTTP rejection, strict
loopback detection, question scrubbing, error redaction, request limits,
reasoning removal, no-data connection checks, and invented-figure retry/blocking.

## Exact installed-artifact verification

Installer:

`src-tauri\target\release\bundle\nsis\NorthstarLedger_1.7.0_x64-setup.exe`

- Size: 271,008,515 bytes (258.45 MiB)
- SHA-256:
  `54FD8188B5A27AE387E9FBBE76E1740A39D00B131DA5A35F0E278A4E2AE45829`
- Silent install exit code: 0
- Installed executable product/file version: 1.7.0
- Installed Apps entry: Northstar Ledger 1.7.0

The exact installer was installed and launched with `LEDGER_DATA_DIR` pointed at
a disposable six-month synthetic database. The regular finance database was
not launched or modified.

Hands-on native checks passed:

1. Fresh AI settings defaulted to MiniMax and `MiniMax-M3`.
2. The AI Beta first-run state linked directly to the relevant Settings section.
3. A disposable loopback OpenAI-compatible endpoint passed **Save and test
   connection** without a key or finance payload.
4. Explicitly enabling AI made the Ask Northstar workspace available.
5. A starter question returned an answer, provider/model, latest imported
   transaction date, and local grounding-check result.
6. Closing and relaunching the exact installed app retained the AI provider,
   model, scope, months, and enabled state.
7. The packaged sidecar stored an invented cloud key as a `dpapi:v1` protected
   value, returned `key_storage=windows_protected`, and did not store the
   invented plaintext.
8. The release app owned no TCP listener. Its only persistent child processes
   were the embedded Microsoft Edge WebView runtime; no external browser or
   console sidecar remained open.
9. The installed application contained no CSV, SQLite, PDF, or JSONL financial
   data files and no tested private-source filename or transaction marker.

## Remaining limitations

- AI remains explanation-only. It cannot edit transactions, change plans, move
  money, or act autonomously.
- Questions are independent. There is no saved conversation history or
  long-term personal coaching memory yet.
- Personal context such as household size, priorities, and advice preferences
  is not part of the payload.
- Local providers still require the user to install and run Ollama or LM Studio.
- Custom OpenAI-compatible endpoints can vary. MiniMax and the packaged
  loopback workflow are the validated paths for this release.
- The regular database still needs the user's hands-on MiniMax test after
  installation because validation deliberately did not send private finance
  rows.

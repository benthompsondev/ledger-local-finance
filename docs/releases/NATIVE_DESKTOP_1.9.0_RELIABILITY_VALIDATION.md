# Northstar Ledger 1.9.0 reliability validation

Northstar 1.9.0 is a focused reliability release. I kept the finance math
deterministic and local, then tightened the optional AI boundary and the
statement import path around failures that normal happy-path testing missed.

## What changed

- MiniMax finance questions disable adaptive thinking so a short answer does
  not spend its whole response budget on hidden reasoning.
- Provider responses are classified before display. A response stopped by a
  token limit is retried once and is never shown as a complete answer.
- MiniMax errors returned inside an HTTP 200 response are handled separately
  from authentication, balance, rate-limit, safety, timeout, and malformed
  response failures.
- Raw provider responses are no longer copied into user-facing errors.
- Currency values, percentages, and incidental counts are checked separately.
  A history count can no longer make an invented dollar value look supported.
- The AI payload no longer includes goal names, targets, balances, or progress.
- A stored cloud API key is never attached to a localhost request.
- Ollama and LM Studio have clearer presets and a plain connection error when
  the local server or model is not running.
- Settings loads every SQLite-backed packet in order. This removes the
  competing startup requests behind the intermittent `database is locked`
  message.
- New goals do not silently reserve money in Plan. The goal form now has an
  explicit `Reserve this monthly amount in Plan and Safe to Spend` option.
- The cross-account duplicate guard now ignores duplicate-only audit batches
  with no transaction rows. This fixes a same-second ordering race found by
  repeated import testing.

## Test results

- Full Python suite: `474 passed`
- AI provider and privacy tests: `56 passed`
- Import stress: five fresh-database rounds of `132 passed`, for 660 passing
  import cases
- Focused same-file cross-account stress: 20 consecutive passing rounds
- React production build: passed
- Chart scale tests: `2 passed`
- Rust checks and command-permission tests: `5 passed`
- Final diff check: passed

The import matrix covers accepted and refused layouts, international bank
exports, alternate encodings and delimiters, split debit/credit columns,
signed-source preservation, manual mapping, duplicate-only re-imports, and
cross-account duplicate protection.

## Installed-artifact verification

- Installer:
  `src-tauri/target/release/bundle/nsis/NorthstarLedger_1.9.0_x64-setup.exe`
- Size: `271,017,365 bytes`
- SHA-256:
  `1D0E8B08992ADF766A20FDED1A3E00E08DE9CB35754A3273BBC5A7FE0A112A43`
- Silent install exit code: `0`
- Installed Apps version: `1.9.0`
- Executable file and product version: `1.9.0`
- Installed executable:
  `%LOCALAPPDATA%\Programs\Ledger\ledger-native.exe`

The exact installed artifact was launched against a disposable six-month demo
profile with 308 synthetic transactions. Home loaded `$4,801.20` of Money In,
`$3,563.26` of Spending, and `$1,237.94` kept for the latest 30-day period.
Income sources, category totals, and top merchants were populated.

Settings was opened repeatedly, allowed to settle, and checked again after a
full app relaunch. It loaded the 1.9.0 version, disposable data path, and saved
Ollama preset without a database-lock error. The local connection test failed
plainly when no Ollama server was running and told the user to start Ollama or
LM Studio and load the model.

The installed directory contained no CSV, SQLite, PDF, or JSONL financial data.
No private statement filename or MiniMax subscription-key prefix was found in
the installer or installed directory. The app opened no TCP listener and left
no Python or packaged sidecar process running after requests completed.

## Reference contracts

- MiniMax documents `finish_reason`, token-limit truncation, `base_resp`, and
  provider error codes in its
  [OpenAI-compatible chat API](https://platform.minimax.io/docs/api-reference/text-chat-openai)
  and [error-code reference](https://platform.minimax.io/docs/api-reference/errorcode).
- Ollama documents the Windows localhost service and model workflow in its
  [Windows guide](https://docs.ollama.com/windows) and
  [CLI reference](https://docs.ollama.com/cli).
- LM Studio documents its localhost server, `/v1/chat/completions`, and model
  identifier flow in its
  [OpenAI compatibility guide](https://lmstudio.ai/docs/developer/openai-compat).

## Known limitations

- The installer is unsigned, so Windows may show a SmartScreen warning.
- The local-provider transport was tested through a real loopback
  OpenAI-compatible server, but no full Ollama or LM Studio model was installed
  during this release.
- The exact 1.9.0 artifact did not make a live MiniMax call because the
  subscription key was not copied into build logs or automation input. The
  provider shapes, retries, privacy boundary, and errors are covered with
  deterministic HTTP tests; the installed connection is ready for the owner's
  key test.
- Each AI question is independent. Northstar does not keep an AI conversation
  history or let AI edit financial records.

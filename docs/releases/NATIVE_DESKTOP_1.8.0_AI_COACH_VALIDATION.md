# Northstar Ledger 1.8.0 AI Coach validation

Validated on Windows 11 on 2026-07-25. All financial and live-provider
testing used a disposable synthetic profile through `LEDGER_DATA_DIR`. The
regular Northstar database was not opened or modified.

## Release scope

Northstar 1.8.0 turns the AI Beta page into a bounded personalized coaching
surface:

- A local pattern brief compares the latest two complete months using the
  existing canonical finance calculations.
- The brief shows kept after spending, spending, the largest category change,
  active goal progress, and the existing monthly cash-flow chart.
- The user can choose a coaching focus, response style, and spending categories
  they consider essential. These preferences remain local and survive relaunch.
- Three guided questions turn the local brief into a practical explanation.
- MiniMax receives the selected summary and coaching choices only after the
  user enables AI. Financial calculations remain local.
- Safety instructions distinguish historical amount kept from current cash,
  block unsupported exact transfer suggestions, and call out stale data.
- Dollar and percentage figures in an answer are checked against the exact
  local payload. Unsupported figures cause a retry and then a blocked answer.

The product direction follows two useful patterns from current provider and
finance-product documentation: contextual AI insights with user feedback and
optional context, and an OpenAI-compatible MiniMax connection using the current
`MiniMax-M3` model.

- https://help.monarch.com/hc/en-us/articles/16116906962452-About-Monarch-s-AI-Features
- https://platform.minimax.io/docs/api-reference/text-openai-api
- https://platform.minimax.io/docs/api-reference/models/openai/list-models

AI categorization was deliberately left out of this release. Changing
transaction truth needs its own confidence, review, and rollback workflow.

## Defects caught during hands-on testing

1. An early live answer treated historical amount kept as cash available now
   and suggested moving that exact amount. The prompt contract now forbids that
   unless a current balance-checked amount explicitly allows it. A second live
   call correctly asked for newer data before suggesting money movement.
2. The first packaged build sent monthly `kept` values to a chart contract that
   expects `net`. The card showed `+$1,237.94` while the chart footer showed
   `$0`. The sidecar now uses the shared `net` field and a regression test
   protects the contract.
3. MiniMax commonly returned Markdown emphasis, while the native answer panel
   intentionally renders plain text. Provider cleanup now removes raw emphasis
   markers and has a regression test.

## Automated verification

- Python: `453 passed`
- React/TypeScript production build: passed
- Rust/Tauri command and permission tests: `5 passed`
- NSIS build: passed

## Live MiniMax verification

The exact installed artifact connected successfully to MiniMax using
`MiniMax-M3`. The obsolete `abab6.5s-chat` model was not used.

With totals-only sharing, the installed app answered a guided pattern question
using the local June-versus-May category changes. It:

- identified the two rising categories from the payload;
- explained that a lower Shopping month could be temporary;
- noted that imported activity was 26 days old;
- recommended importing current transactions before acting;
- introduced no unsupported dollar or percentage figures.

The UI reported that all dollar and percentage figures were verified against
Northstar's local payload. The authorized test key was removed from the
disposable database after the call, and AI was returned to the off state.

## Installed-artifact verification

- Installer:
  `src-tauri/target/release/bundle/nsis/NorthstarLedger_1.8.0_x64-setup.exe`
- Size: `271,012,313 bytes`
- SHA-256:
  `69112EC5432073E9207F14335FC9626317CE663645776F202C3621E39644A345`
- Silent install exit code: `0`
- Installed Apps version: `1.8.0`
- Executable file and product version: `1.8.0`
- Installed executable:
  `%LOCALAPPDATA%\Programs\Ledger\ledger-native.exe`

Hands-on checks against the disposable profile:

- Home amount kept: `$1,237.94`
- AI local brief amount kept: `$1,237.94`
- Cash-flow chart footer: `$1,238` after display rounding
- Focus preference survived reinstall and relaunch
- AI-off state still showed the complete local pattern brief
- No TCP listener
- No console or packaged Python sidecar left running after a request
- No CSV, SQLite, PDF, or JSONL private-data files in the installed directory
- No MiniMax subscription-key prefix in the installed directory or installer

## Known limitations

- Each AI question is independent; there is no conversation history.
- Local-model compatibility was not exercised in this release.
- Numeric grounding checks currency and percentages, not every unformatted
  number in natural language.
- AI explains deterministic Northstar calculations. It does not edit
  transactions, categories, goals, plans, or account balances.

# Northstar Ledger 1.9.1 AI answer hotfix

Northstar 1.9.1 fixes the first-question failure where a useful MiniMax answer
was replaced by an unsupported-figures warning.

## Root cause and repair

Northstar correctly rejected dollar or percentage targets that were not in its
local payload. It asked the provider for one grounded rewrite, but if that
rewrite also contained a new target, the app hid the entire answer.

The rewrite request now explicitly asks for no numeric targets. If a provider
still repeats one, Northstar removes only that unsupported target and keeps the
category or behavior recommendation. It does not weaken the deterministic
finance calculations or allow invented figures through.

## Verification

- Exact reported question added as a regression test.
- AI tests: `57 passed`.
- Full Python suite: `475 passed`.
- Same-file cross-account import stress: `20/20` consecutive runs passed.
- React production build: passed.
- Installer built and installed silently with exit code `0`.
- Installed executable file and product version: `1.9.1`.
- Installed Apps version: `1.9.1`.

The exact installed app was launched against a disposable six-month profile
with 308 synthetic transactions. The supplied MiniMax subscription key was
entered only into that disposable profile. Connection testing succeeded with
MiniMax-M3.

The reported custom question was then submitted three consecutive times. All
three live runs returned usable advice based on the synthetic Shopping,
Gas / Transport, income, and spending patterns. None showed the hidden-answer
failure.

## Installer

- Path:
  `src-tauri/target/release/bundle/nsis/NorthstarLedger_1.9.1_x64-setup.exe`
- Size: `271,017,048 bytes`
- SHA-256:
  `EFFAD0F9CBD999D5CCB60AF58E8C82F6B612DE706D3F3B8720230356CCF9BEDC`

The installer was built before the live key test. It contains no API key,
statement filename, CSV, SQLite database, PDF, or JSONL financial data.

## Limitation

AI wording remains provider-generated and can vary. Northstar verifies
financial figures and removes unsupported targets, but the user should still
judge whether a recommendation is practical.

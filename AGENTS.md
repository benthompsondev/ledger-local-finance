# Northstar Ledger — agent guide

Local-first personal finance desktop app. Import bank statements, clean up
transactions, see where the money went, plan the month. Everything stays on the
machine: no account, no server, and no network calls for any of the finance
math.

The one exception, and it matters when reasoning about this code: the optional
AI feature in `utils/ai_assist.py` makes outbound HTTPS calls to whichever
provider the user configured, carrying the summary scope they chose. It is off
until someone turns it on and stores a key. It can also be pointed at a model
running on the same machine, in which case nothing leaves at all. Treat
anything that widens what it sends as a privacy change, not a feature change.

**Shipping product is the native desktop app**: a Tauri 2 shell (Rust) around a
React + TypeScript frontend, talking to a packaged Python engine over stdin and
stdout. The old Streamlit app (`app.py`, `pages/`, `components/`) is retired and
still in the tree only because parts of it have not been deleted yet. Do not
build features there and do not treat it as the product.

## Commands

```bash
.venv/Scripts/python.exe -m pytest -q       # the real test suite
cd desktop && npm run build                 # tsc -b + vite build
cd src-tauri && cargo check && cargo test   # native shell
```

Build the Windows installer only when a release is actually being cut:

```powershell
powershell -File scripts/build_native_desktop.ps1
```

## Structure

- `desktop/src/` — the React UI. Six tabs, listed in `SCREENS` in `App.tsx`:
  `HomeView`, `AddDataView`, `PlanView`, `InsightsView`, `TransactionsView`,
  `AiView` (labelled Coach). `SettingsView` is the header gear, not a tab.
  `GoalsView` and Money Focus are retired and have no native navigation.
  Their stored rows are preserved. `types.ts` is the contract with the engine;
  `charts.tsx` holds every chart.
- `desktop/engine/ledger_engine.py` — the sidecar. One `*_action` function per
  request; every new action is hand-plumbed across a Rust command, the
  capability TOML, the engine action, `api.ts`, and `types.ts`.
- `src-tauri/` — the Tauri shell and its commands.
- `utils/` — the finance engine. The important ones: `financial_semantics.py`
  (canonical amount, direction and transaction type), `insights.py` (aggregates,
  pace, `money_runway`), `checkin.py` (`safe_to_spend_summary`, the one number
  every page must read), `analytics.py` (income baselines), `planner.py`
  (commitments and readiness), `database.py`.
- `parsers/` — statement import. `csv_dialect.py` decides a file's shape once;
  `csv_import.py` applies it.
- `tests/` — mostly engine-level integration tests that assert real financial
  outcomes, not units. Keep that bar.

## Rules

- **The math is the product.** Never let a number be shown more confidently than
  the data supports. If an input is missing, degrade the number and say so
  rather than hiding it behind a gate or quietly guessing.
- **Import fails closed.** A statement file has one date format, one decimal
  convention, one column layout. Detect them once per file, show what was
  detected, and refuse the file when the evidence is contradictory. Silently
  importing wrong dates or hundredfold amounts is the worst thing this app can
  do.
- **One canonical number per concept.** `safe_to_spend_summary` is the only
  Safe to Spend. Cash-flow exclusions live in `financial_semantics.py`. When a
  second copy of a rule appears, delete it.
- **Test against disposable data only.** Use the `ledger_db` fixture or set
  `LEDGER_DATA_DIR` to a temp directory. Never touch the real database at
  `%LOCALAPPDATA%\Ledger\finance.db`. Never commit or print real financial data,
  statements, exports, or screenshots containing them.
- **Verify before calling it done.** pytest, `npm run build`, and `cargo check`
  all green, plus a run of the screen you changed. Do not report unrun checks as
  passing.
- One writer at a time (Claude Code or Codex, never both); the diff is the
  handoff. Small focused commits on the current feature branch. Pushes go
  through Ben's git-push workflow, never by hand.
- Public repo: README and docs stay in Ben's voice, employer-neutral, demo data
  only.

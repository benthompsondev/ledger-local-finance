# Contributing to Northstar Ledger

Thanks for taking a look. This is a local-first personal finance app, so
contribution quality is mostly about trust: predictable math, private data
boundaries, and changes small enough to verify.

## What this project values

- Local-first data ownership.
- Deterministic calculations before any generated explanation.
- Plain-language UX that helps a normal person make one better money decision.
- Small, testable changes over broad rewrites.
- Privacy-safe examples, screenshots, exports and bug reports.

## The rules that matter most

- **The math is the product.** Never show a number more confidently than the
  data supports. If an input is missing, degrade the number and say so rather
  than hiding it behind a gate or guessing.
- **Import fails closed.** Detect a file's date format, decimal convention and
  column layout once, show what was detected, and refuse the file when the
  evidence is contradictory.
- **One canonical number per concept.** `safe_to_spend_summary` is the only
  Safe to Spend. If you find a second copy of a rule, delete it rather than
  keeping both in sync.
- **Explanations stay read-only.** AI features may summarize deterministic
  packets. They must never create, edit or delete financial records.

## Local setup

The shipping product is the native Tauri desktop app. You need Python 3.13+,
Node 24+ and Rust. CI runs Python 3.14 and Node 24; see the README for why
those are the floors.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm run desktop:dev
```

`npm run desktop:dev` opens the desktop window with hot reload. There is no
browser and no localhost server involved.

Work against demo data. There is no in-app toggle; demo mode is two
environment variables pointed at a disposable directory:

```powershell
$demoDir = Join-Path $env:TEMP "northstar-ledger-demo"
New-Item -ItemType Directory -Force -Path $demoDir | Out-Null
.\.venv\Scripts\python.exe -m scripts.create_demo_data --out "$demoDir\finance.demo.db" --force
$env:LEDGER_DATA_DIR = $demoDir
$env:LEDGER_DEMO_DB = "1"
npm run desktop:dev
```

Clear them when you are done so you are not developing against generated data
by accident:

```powershell
Remove-Item Env:LEDGER_DATA_DIR, Env:LEDGER_DEMO_DB
```

## Validation

Run all of these before opening a pull request. CI runs the same set.

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # the real test suite
npm run build                                    # tsc -b + vite build
npm run test:charts                              # chart and preference tests
cd src-tauri; cargo check; cargo test            # the native shell
.\.venv\Scripts\python.exe -m scripts.check_tracked_files
```

Tests must use disposable data only: the `ledger_db` fixture, `tmp_path`, or
`LEDGER_DATA_DIR` pointed at a temp directory. Never touch the real database at
`%LOCALAPPDATA%\Ledger\finance.db`.

Do not report unrun checks as passing.

## Before you open a pull request

1. Use demo data, never real financial data, for screenshots or examples.
2. Do not commit `data/`, `.venv/`, exports, logs, statement PDFs, bank CSVs,
   databases, or generated share zips. `scripts/check_tracked_files.py` will
   catch most of these, but it is a backstop, not permission to be careless.
3. Preserve Tangerine PDF import, generic CSV import, investment CSV import,
   net-worth snapshots and share-zip safety.
4. Any finance math change needs a test that asserts a real financial outcome,
   not a unit-level shape check.

## Pull request checklist

- [ ] The change is scoped and understandable.
- [ ] All validation commands above pass.
- [ ] No private data, screenshots, PDFs, CSVs, keys, logs or database files.
- [ ] Any finance math change includes a deterministic test.
- [ ] Any explanation or export change names the deterministic evidence packet
      it reads from.
- [ ] User-facing copy is practical and is not financial advice.

## A note on the legacy Streamlit code

`app.py`, `pages/`, `components/` and `Ledger_Launcher.py` are the retired
Streamlit version. They remain in the tree only because they have not been
deleted yet. Do not build features there and do not treat them as the product.

## Maintenance notes

Automation and coding agents are welcome, under the same rules as human
contributors:

- inspect before editing;
- keep private data out of commits;
- run the safety gates;
- explain the change in human terms;
- never let generated output mutate financial data.

Architecture and conventions live in [AGENTS.md](AGENTS.md). The maintainer
workflow is in `docs/MAINTAINER_WORKFLOW.md`.

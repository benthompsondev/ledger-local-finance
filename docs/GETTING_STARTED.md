# Getting started with SignalSpace

This is the beginner path for running SignalSpace on your own machine.

It is a local-first finance app for Windows. It runs on your computer, stores
everything in a local SQLite database, and does not need an account or an
internet connection for any of the finance math.

The safest first run is demo mode, which uses generated fake transactions so
you can look around before pointing it at real statements.

## Easiest way to try it

Download the latest Windows installer from
[GitHub Releases](https://github.com/benthompsondev/ledger-local-finance/releases/latest).
It is a per-user install and does not require Python, Node, Rust, or
administrator rights.

The installer is not Authenticode signed, so Windows SmartScreen may warn you
the first time. Check the SHA-256 value on the release page before running it.

The sections below are for running SignalSpace from source. For that you need:

- [Python 3.13+](https://www.python.org/downloads/) — tick **Add Python to
  PATH** during install if the installer offers it
- [Node.js 24+](https://nodejs.org/) — the frontend tests load TypeScript directly, and only Node 23.6 and up strip types without a flag
- [Rust](https://rustup.rs/) — the installer walks you through it
- [Git](https://git-scm.com/), if you want to clone from the command line

To check each one:

```powershell
python --version
node --version
cargo --version
```

If `python` is not found, try `py --version` instead.

You can also [take the browser product tour](https://benthompsondev.github.io/ledger-local-finance/)
before downloading anything. The tour uses screenshots and generated data; the
finance engine itself remains in the Windows desktop app.

## Get the code

```powershell
git clone https://github.com/benthompsondev/ledger-local-finance.git
cd ledger-local-finance
```

If you would rather not use Git, open the repo on GitHub, click **Code**, click
**Download ZIP**, extract it, and open PowerShell in the extracted folder.

## Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
```

The first `npm ci` and the first Rust build both take a few minutes. After
that they are cached.

## First run with demo data

There is no demo switch inside the app. Demo mode is two environment variables,
which is deliberate: the app can never quietly decide for itself whether it is
looking at invented data or yours.

Generate the dataset into a temp folder and point the app at it:

```powershell
$demoDir = Join-Path $env:TEMP "spendshape-demo"
New-Item -ItemType Directory -Force -Path $demoDir | Out-Null
.\.venv\Scripts\python.exe -m scripts.create_demo_data --out "$demoDir\finance.demo.db" --force
$env:LEDGER_DATA_DIR = $demoDir
$env:LEDGER_DEMO_DB = "1"
npm run desktop:dev
```

A desktop window opens. It does not use a browser and does not start a local
web server. Everything reads and writes inside that temp folder, so your real
database at `%LOCALAPPDATA%\Ledger\finance.db` is never opened.

When you are done, clear both variables. They only last for that PowerShell
session, but clearing them makes it obvious:

```powershell
Remove-Item Env:LEDGER_DATA_DIR, Env:LEDGER_DEMO_DB
```

Then start the app again with `npm run desktop:dev` to work against your real
data.

## What to look at first

1. **Home** — the weekly check-in. Safe to Spend, what changed, and the few
   things worth your attention.
2. **Insights** — spending pace against last month, category movement, income
   steadiness.
3. **Plan** — turn recent spending into a monthly plan and see what is actually
   left over after fixed costs, reserves, savings and buffer.
4. **Transactions** — the full ledger, searchable and filterable.

The sixth tab, **Coach**, only does anything once you configure an AI provider,
so skip it for now.

Settings is the gear in the top right, not a tab. Backup and restore,
preferences, categorization controls and the optional AI setup all live there.

## Using your own statements

When you are ready, clear the two demo variables above, restart the app, and go
to **Add Data**.

It handles:

- bank and credit card CSV exports
- Tangerine Mastercard, Chequing and Savings PDFs
- investment holdings CSV snapshots

Drop a file in and it will show you what it detected: the date format, the
decimal convention, and which columns it mapped. Check that summary before
confirming. If the file's evidence is contradictory the import stops rather
than guessing, which is deliberate — a silently wrong date or a hundredfold
amount is worse than a refused file.

Every import is recorded as a batch you can remove later. Re-importing the same
file does nothing, because duplicates are caught by both file hash and
transaction fingerprint.

## Building an installer

If you want a normal installed app rather than the dev window:

```powershell
powershell -File scripts/build_native_desktop.ps1
```

The installer is written to `src-tauri/target/release/bundle/nsis/`. It is a
per-user install and does not need administrator rights.

From version 2.6.0 onward, Settings can check GitHub for a signed update when
you press **Check for updates**. Nothing checks in the background. A manual
installer still upgrades the same per-user installation and keeps your data.

## If something fails

Run the readiness check:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor
```

It reports missing files, missing dependencies and anything private that has
been staged in git by mistake.

If the desktop window will not start, the most common causes are a missing Rust
toolchain or an incomplete `npm ci`. Both print a clear error.

To confirm the finance engine itself is healthy:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Privacy notes

- Your database lives at `%LOCALAPPDATA%\Ledger\finance.db` and the file never
  leaves your machine.
- All the finance math is local and deterministic. No telemetry, no analytics,
  no account.
- If you enable the optional online AI, the evidence packet for the question
  you asked is sent to the provider you configured. That packet contains real
  figures from your ledger: totals, categories, merchant names and dates,
  depending on the feature. Only the request you triggered is sent, and the app
  works fully without AI.
- Your provider key is encrypted with Windows DPAPI and tied to your Windows
  user account.
- Never commit real statements, exports, screenshots of real data, or the
  database. `scripts/doctor.py` will warn you if any of them are staged.

## Where to go next

- [README.md](../README.md) for what the app does and how it is built
- [AGENTS.md](../AGENTS.md) for architecture and conventions
- [CONTRIBUTING.md](../CONTRIBUTING.md) if you want to change something

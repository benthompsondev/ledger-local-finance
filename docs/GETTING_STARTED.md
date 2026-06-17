# Getting Started With Ledger

This is the beginner path for trying Ledger locally.

Ledger is a local-first finance app. It runs on your computer, stores data in a local SQLite database, and does not need an online account. The safest first run is demo mode, which uses fake generated transactions and fake balances.

## What You Need

- Python 3.12 or newer
- Git, if you want to clone the repo from the command line

On Windows, install Python from [python.org](https://www.python.org/downloads/) and tick **Add Python to PATH** if the installer offers it.

To check whether Python is available:

```powershell
py --version
```

If that does not work, try:

```powershell
python --version
```

## Download Ledger

### Option 1: Git Clone

```powershell
git clone https://github.com/benthompsondev/ledger-local-finance.git
cd ledger-local-finance
```

### Option 2: Download ZIP

1. Open the GitHub repo.
2. Click **Code**.
3. Click **Download ZIP**.
4. Extract the ZIP.
5. Open PowerShell in the extracted `ledger-local-finance` folder.

The ZIP option is fine if you only want to try the app.

## First Run With Fake Demo Data

Use demo mode first. It creates `data/finance.demo.db` with fake transactions and opens the app locally.

Windows:

```powershell
py Ledger_Launcher.py --demo
```

If `py` is not available:

```powershell
python Ledger_Launcher.py --demo
```

PowerShell helper:

```powershell
.\run_windows.ps1 -Demo
```

Linux/macOS:

```bash
make setup
make demo
```

Open:

```text
http://127.0.0.1:8501
```

The first run can take a few minutes because Ledger creates a local Python environment and installs dependencies.

## What To Click First

- **Dashboard**: Money Pulse, safe-to-spend, and weekly actions
- **Import**: where PDFs or CSVs are uploaded
- **Reduce**: subscriptions and controllable spending targets
- **Plan**: monthly target, bills, goals, and runway
- **Net Worth**: assets, liabilities, holdings, and snapshots
- **Reports**: deeper review after the dashboard

## Use Your Own Files

After demo mode works, close the app and open a fresh terminal.

Windows:

```powershell
py Ledger_Launcher.py
```

or:

```powershell
.\run_windows.ps1
```

Linux/macOS:

```bash
make run
```

Then use the **Import** page with supported files:

- Tangerine Mastercard PDFs
- Tangerine Chequing or Savings PDFs
- generic transaction CSV files
- holdings CSV snapshots

Ledger stores imported data in `data/finance.db`. That file stays local and is ignored by git.

## If Something Fails

Run the local readiness check:

Windows:

```powershell
.\.venv\Scripts\python.exe -m scripts.doctor
```

Linux/macOS:

```bash
make check
```

This checks the Python version, main dependencies, required files, demo-data status, and whether private-looking files are tracked by git.

If the Windows launcher fails, check `launcher.log` in the Ledger folder. It records the setup step that failed and usually includes copy/paste repair commands.

## Privacy Notes

Ledger is meant to run locally.

Do not upload or share:

- `data/`
- `config.json`
- statement PDFs
- exported files with real transactions
- screenshots with real balances or account details
- `launcher.log` if it contains local machine details

If you need to share a clean copy of the project, use:

```powershell
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

The share script excludes local databases, API keys, logs, exports, virtual environments, and generated private files.

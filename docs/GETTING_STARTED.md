# Getting Started With Ledger

This is the simplest path for someone trying Ledger for the first time.

Ledger is a local-first finance app. It runs on your computer, stores data in a local SQLite database, and does not need an online account. The safest first run is demo mode, which uses fake generated data.

## 1. Install Prerequisites

- Git
- Python 3.12 or newer

On Windows, open PowerShell and run:

```powershell
git --version
python --version
```

## 2. Download And Set Up

```powershell
git clone https://github.com/benthompsondev/ledger-local-finance.git
cd ledger-local-finance
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Run With Demo Data

```powershell
.\.venv\Scripts\python.exe -m scripts.create_demo_data
$env:LEDGER_DEMO_DB="1"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open:

```text
http://127.0.0.1:8501
```

Demo mode shows fake transactions, fake merchants, fake balances, and fake net-worth data. It is meant for screenshots, demos, and first-time review.

## 4. Use Your Own Files

Open a new PowerShell window so demo mode is not set, then run:

```powershell
cd ledger-local-finance
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Go to the Import page and upload supported files:

- Tangerine Mastercard PDFs
- Tangerine Chequing or Savings PDFs
- generic transaction CSV files
- holdings CSV snapshots

Ledger stores imported data in `data/finance.db`. That file stays local and is ignored by git.

## 5. What To Look At First

- Dashboard: current picture, Money Pulse, safe-to-spend, and next actions
- Import: upload PDFs or CSVs and check statement coverage
- Reduce: subscriptions and controllable spending targets
- Plan: monthly target, bills, goals, and runway
- Net Worth: assets, liabilities, holdings, and snapshots
- Reports: deeper breakdowns after the daily dashboard view

## Privacy Notes

Do not share a manually zipped copy of the project folder after importing real data. Use the built-in share script if you need a clean package:

```powershell
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

The share script excludes local databases, API keys, logs, exports, virtual environments, and generated private files.

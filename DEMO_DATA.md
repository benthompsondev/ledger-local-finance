# Ledger Demo Data

Ledger should be shown publicly with synthetic data only. Real
`data/finance.db`, real statements, real screenshots, and real exports must
never be committed.

## Generate the Demo Database

```powershell
.\.venv\Scripts\python.exe -m scripts.create_demo_data
```

This creates `data/finance.demo.db`. The demo database is ignored by git and can
be regenerated locally whenever screenshots or walkthroughs need fresh data.

## Run Ledger in Demo Mode

```powershell
$env:LEDGER_DEMO_DB="1"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open `http://127.0.0.1:8501`.

## What the Demo Data Covers

- Chequing, savings, and credit-card style accounts.
- Several months of income, fixed bills, subscriptions, and controllable spend.
- Credit-card payments marked as transfers.
- Investment holdings and net-worth snapshots.
- A saved monthly plan and goals.
- Enough history to exercise Dashboard, Money Runway, Reduce, Plan, Reports,
  Trends, Review, and OpenClaw export.

Every value is fictional. The merchant names use obvious demo labels and should
not be replaced with real personal merchants in public screenshots.

## Screenshot Rules

Use demo mode for every public screenshot:

- Dashboard
- Import
- Reduce
- Plan
- Net Worth
- Reports
- Trends

Save screenshots under `docs/screenshots/` only after visually checking that no
real names, account numbers, API keys, statement filenames, or private paths are
visible.

## Privacy Checklist

Before publishing:

- [ ] `git status --ignored` shows real databases and config files ignored.
- [ ] `git ls-files` does not include `data/`, `config.json`, logs, exports, PDFs, or zips.
- [ ] `scripts.make_share_zip` succeeds and excludes private artifacts.
- [ ] Screenshots were captured with `LEDGER_DEMO_DB=1`.
- [ ] No issue, PR, README image, or release artifact contains real financial data.

# Screenshot Guide

Use demo data only. Do not capture real merchant names, balances, statements,
account labels, file paths, or API-key settings.

The current public screenshots are from Northstar Ledger 2.5.5 and use the
generated database from `scripts.create_demo_data`:

| File | Screen |
|---|---|
| `native-home-2.5.5.png` | Home check-in, spending pace, and synthetic totals |
| `native-plan-2.5.5.png` | Current-month planning decisions |
| `native-insights-2.5.5.png` | Same-day spending and category comparisons |

Older images in this folder show retired versions of the interface. Keep them
only as development history; do not use them in the README or product tour.

Run the native desktop app with demo data:

```powershell
$demoDir = Join-Path $env:TEMP "northstar-ledger-demo"
New-Item -ItemType Directory -Force -Path $demoDir | Out-Null
.\.venv\Scripts\python.exe -m scripts.create_demo_data --out "$demoDir\finance.demo.db" --force
$env:LEDGER_DATA_DIR = $demoDir
$env:LEDGER_DEMO_DB = "1"
npm run desktop:dev
```

Before committing screenshots:

- Confirm every merchant and account label is obviously synthetic.
- Confirm no browser autofill, local file path, or API-key field appears.
- Keep the app version in the filename so old screens are not mistaken for the
  current product.

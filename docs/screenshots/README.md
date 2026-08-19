# Screenshot Guide

Use demo data only. Do not capture real merchant names, balances, statements,
account labels, file paths, or API-key settings.

**The current public screenshots live in [`site/assets/`](../../site/assets),
not here.** Those are what the README and the product tour use:

| File | Screen |
|---|---|
| `site/assets/home.png` | Home check-in with synthetic totals |
| `site/assets/insights.png` | What SignalSpace noticed |
| `site/assets/profiles.png` | Two separate sets of finances in one install |
| `site/assets/coach.png` | Coach with AI switched off |
| `site/assets/plan.png` | Planning the month |

This folder now keeps only the images a release validation document still
points at, as development history:

| File | Referenced by |
|---|---|
| `native-home-0.36.1.jpg` | `docs/releases/NATIVE_DESKTOP_0.36.1_VALIDATION.md` |
| `northstar-native-home-0.38.jpg` | `docs/releases/NATIVE_DESKTOP_0.38.0_VALIDATION.md` |

Do not use either in the README or the product tour. They show retired
versions of the interface under an older product name.

Run the native desktop app with demo data:

```powershell
$demoDir = Join-Path $env:TEMP "signalspace-demo"
New-Item -ItemType Directory -Force -Path $demoDir | Out-Null
.\.venv\Scripts\python.exe -m scripts.create_demo_data --out "$demoDir\finance.demo.db" --force
$env:LEDGER_DATA_DIR = $demoDir
$env:LEDGER_DEMO_DB = "1"
npm run desktop:dev
```

Before committing screenshots:

- Confirm every merchant and account label is obviously synthetic.
- Confirm no browser autofill, local file path, or API-key field appears.
- Confirm no other window is captured alongside the app.

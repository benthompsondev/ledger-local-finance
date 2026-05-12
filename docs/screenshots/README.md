# Screenshot Guide

Use demo data only. Do not capture real merchant names, balances, statements, account labels, or API-key settings.

Suggested public screenshot set:

| File | Page | What to Show |
|---|---|---|
| `dashboard.png` | Dashboard | Money Pulse, top categories, plan status, and next action |
| `import.png` | Import | supported file types and import history with demo statements |
| `reduce.png` | Reduce | weekly challenge, active candidates, and controllable cut targets |
| `plan.png` | Plan | safe-to-spend card and category targets |
| `net_worth.png` | Net Worth | net-worth trend and goal progress |
| `reports.png` | Reports | Monthly Review and Trends routing |

Run with demo data:

```powershell
.\.venv\Scripts\python.exe -m scripts.create_demo_data
$env:LEDGER_DEMO_DB="1"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Before committing screenshots:

- confirm the demo banner is visible
- confirm no real names or account labels appear
- confirm no browser autofill or local file paths appear
- keep images under `docs/screenshots/`

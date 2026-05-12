# Ledger — Demo Data Plan

This document describes the curated demo data needed before Ledger can be
shown publicly on GitHub. **No fake demo data ships in this repository
today.** Real `data/finance.db` is excluded by `.gitignore`; that file
must never be committed.

## Why demo data?

Screenshots and short walkthrough GIFs need a database that:
- Looks like a real personal finance ledger.
- Contains nothing that could be confused with real PII or real merchant
  patterns from a real person's life.
- Exercises the full feature surface (Plan, Forecast, Reduce, Money
  Moves, Net Worth) so the showcase can demonstrate the everyday loop.

## What the demo DB should contain

A canonical `data/finance_demo.db` should include:

| Item | Volume | Notes |
|---|---|---|
| Transactions | ~6 months | Two accounts (chequing + credit card). Realistic merchants/amounts. |
| Subscriptions | 4–6 active | Includes one stale/inactive and one price-increase candidate. |
| Controllable spend | Groceries, Shopping, Food & Convenience, Subscriptions, Gas | Showing realistic month-to-month variation. |
| Investment snapshot | 1 holdings CSV | Mixed-currency demo (CAD + USD). |
| Account balances | Cash + 1 credit card + 1 loan | For net-worth math. |
| Net-worth snapshots | 3+ over 3 months | Powers the milestone card. |
| Monthly plan | 1 saved (Tight mode) | Powers Forecast / safe-to-spend. |
| Goals | 1–2 active (cash buffer + investment contribution) | Different `linked_metric` values to show auto-tracking. |

## What the demo DB must NOT contain

- Real merchant names tied to real people (no real e-Transfer
  recipients, no real names in `notes`).
- Real account numbers (full PANs are already masked at parse time, but
  re-verify before committing demo data).
- Real API keys (none should ever land in the DB anyway; verify
  `config.json` is excluded).
- Real personal income figures.

## Suggested workflow

1. Build `data/finance_demo.db` locally from a synthetic seed script
   (TODO: `scripts/create_demo_data.py`, deferred — see Pass 26 known
   risks).
2. Add an environment flag to `app.py` (e.g. `LEDGER_DEMO_DB=1`) that
   switches `utils/database.DB_PATH` to the demo file.
3. Capture screenshots / a 60-second GIF of the daily loop:
   Today → Reduce → Month Plan → Trends.
4. Drop screenshots into `docs/screenshots/` (this folder also needs to
   be created when the demo data lands).

## Concrete fake-data schema (for Pass 28's seed script)

The seed script (`scripts/create_demo_data.py`, deferred to Pass 28)
should produce a `data/finance.demo.db` with the following exact
shapes. Every value below is fictional; do **not** use a real
person's income, real merchant aliases tied to Ben, or real account
numbers.

### Accounts (3)
| Display | account_type | currency |
|---|---|---|
| Demo Chequing | `chequing` | CAD |
| Demo Savings | `savings` | CAD |
| Demo Visa | `mastercard` (or `visa`) | CAD |

### Income (~6 months)
- Bi-weekly payroll deposits, $2,400 each, into chequing.
  Merchant: `DEMO EMPLOYER PAYROLL`.
- Occasional interest credits, $1.20 quarterly, into savings.

### Spending categories — controllable
- **Groceries**: 8–12 transactions/month, $30–$120 each.
  Merchants: `DEMO GROCERY`, `DEMO MARKET`, `DEMO PRODUCE`.
- **Shopping**: 4–6 transactions/month, $20–$200 each.
  Merchants: `DEMO ONLINE STORE`, `DEMO BOOKSHOP`.
- **Food & Convenience**: 6–10/month, $8–$25.
  Merchants: `DEMO COFFEE`, `DEMO LUNCH SPOT`.
- **Gas / Transport**: 3–5/month, $40–$80. `DEMO FUEL`.

### Subscriptions (Pass 23 commitments classifier)
| Merchant | Monthly | Active | Notes |
|---|---|---|---|
| `DEMO STREAM TV` | $15.99 | ✓ | active candidate |
| `DEMO MUSIC` | $11.99 | ✓ | active |
| `DEMO CLOUD STORAGE` | $9.99 | ✓ | active |
| `DEMO NEWSPAPER` | $19.99 | ✓ | price-increase candidate (was $14.99) |
| `DEMO GYM` | $40.00 | ✗ stale (last seen 4mo ago) | "possibly stopped" |
| `DEMO MEAL KIT` | $89.00 | ✗ cancelled | excluded from active KPIs |

### Fixed bills
- `DEMO LANDLORD` rent: $1,800/mo, category Housing / Mortgage.
- `DEMO HYDRO` utility: $90/mo, category Utilities / Bills.
- `DEMO INTERNET`: $80/mo, category Utilities / Bills.

### Credit-card payment transfers
- `PAYMENT - DEMO VISA`: monthly, paired credit on Visa + debit on
  chequing. Both flagged `is_transfer=1`.

### Investments
- One holdings CSV import producing 5–8 positions:
  - VFV.TO, VCN.TO (CAD)
  - VTI, VOO (USD)
  - One demo crypto position to exercise mixed-currency.
- Total demo portfolio value ~$25,000 CAD-equivalent.

### Account balances
- Chequing: $1,200
- Savings: $4,500
- Visa: -$420 (liability)

### Net-worth snapshots (3, one per month)
Built from balances + holdings, growing $200–$400 month-over-month
to power the milestone card visibly.

### Monthly plans (1)
- Mode: `tight`
- Targets: $5,200 spending / $1,200 savings on $6,400 income.
- 5 category targets at the 20% cut shape.

### Goals (2)
| Name | Type | Target | Linked metric |
|---|---|---|---|
| Demo emergency fund | `emergency_fund` | $10,000 | `cash_balance` |
| Demo investment year | `investment_contribution` | $6,000 | `investments` |

### Demo-mode flag
- Add an env var: `LEDGER_DEMO_DB=1` switches `utils/database.DB_PATH`
  from `data/finance.db` to `data/finance.demo.db`. The seed script
  must NOT overwrite a real `finance.db`; it should write to
  `data/finance.demo.db` only.

## What goes in `docs/screenshots/`

When the demo DB lands (Pass 28), capture against it. See
`docs/screenshots/README.md` for the planned shot list.

## Roadmap

This is part of the **GitHub Showcase Foundation** track.

### Done (Pass 26)
- `.gitignore` hardening (`data/*.db-shm`, `data/*.db-wal`, `exports/`,
  `launcher.log*`).
- This file describing the demo plan.
- Screenshots placeholder section in README.

### Done (Pass 27)
- Concrete fake-data schema documented (this file, above).
- `docs/screenshots/README.md` with planned shot list.

### Planned (Pass 28+)
- `scripts/create_demo_data.py` (deterministic synthetic seed).
- `docs/screenshots/` populated with the captured walkthrough.
- A "Demo mode" indicator in the UI when `LEDGER_DEMO_DB=1`.
- One-paragraph "Try the demo" section in README.

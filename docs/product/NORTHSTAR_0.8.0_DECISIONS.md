# Northstar Ledger 0.8.0 product decisions

Northstar 0.8.0 focuses on the weekly work people actually repeat: understand
their own share of household costs, correct uncertain transactions quickly,
and see what is missing before trusting a forecast.

## Walkthrough patterns adopted

- YNAB's approval flow keeps a review action attached to the transaction and
  uses prior category history for suggestions. Northstar uses a local review
  drawer, explainable suggestions, Save and next, and keyboard shortcuts.
- Copilot keeps To Review visible from its main dashboard and treats recurring
  items as a compact, inspectable list. Northstar adds a navigation badge and
  a short upcoming-commitments disclosure in Plan.
- Monarch presents collaboration as one shared financial view instead of a
  second budget. Northstar likewise stores one transaction and layers My Share
  or Household Total over it.
- Simplifi's spending plan explains what is available after bills and savings.
  Northstar keeps that equation, but now blocks confident forecasts with a
  checklist that links to the missing setup control.

Official product material reviewed:

- [YNAB transaction approval](https://support.ynab.com/en_us/approving-and-matching-transactions-a-guide-ByYNZaQ1i)
- [Copilot Dashboard and To Review](https://help.copilot.money/en/articles/6045480-dashboard-tab-overview)
- [Copilot web shortcuts](https://help.copilot.money/en/articles/11780342-copilot-money-for-web)
- [Monarch collaboration](https://www.monarchmoney.com/features/collaboration)
- [Simplifi Spending Plan](https://support.simplifi.quicken.com/en/articles/4212702-understanding-your-spending-plan)

## Shared expense model

- The imported signed amount is immutable financial evidence. Sharing never
  rewrites it.
- A user can set a shared-with name and default share, then mark a merchant,
  recurring commitment, category, or one transaction as shared.
- Currency is allocated with decimal half-up rounding. A $2,622.45 household
  mortgage at 50% becomes $1,311.23 for the user and $1,311.22 for the other
  person.
- Home, Plan, and Safe to Spend use My Share. Insights can switch between My
  Share and Household Total.
- A matching incoming e-transfer is only suggested as a shared reimbursement.
  It stays in Money In, never becomes Typical Monthly Payroll, and is excluded
  from personal net after the related expense has already been reduced.

## Review and forecast decisions

- Quick review shows flagged rows, deterministic suggestions, material single
  transactions, and one representative for repeated high-impact merchants.
  It does not turn every low-value Uncategorized row into an alert.
- Protected transfers and payments remain ahead of merchant rules. Saved user
  rules, statement memo, prior merchant corrections, and built-in mappings are
  the deterministic suggestion order.
- The editor exposes category, financial type, sharing, raw bank description,
  bulk merchant application, future-import memory, undo, and Save and next.
- Forecast readiness distinguishes missing plan, balances, stable income,
  uncertain commitments, history, and account coverage. Each item routes to
  the relevant native control.

## Goals and boundaries

- Investing joins the primary goal templates with monthly or quarterly
  contribution targets, manual progress, or an optional Investment account.
- Annual Expense moves under More templates.
- This release adds only two research-led extras: keyboard review and compact
  upcoming commitments.
- No cloud sync, bank credentials, external AI, live prices, or second shared
  database is introduced. A future opt-in local or external model should not
  be considered until deterministic corrections, explanations, and undo are
  mature enough to provide a trustworthy fallback.

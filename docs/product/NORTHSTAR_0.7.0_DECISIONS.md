# Northstar Ledger 0.7.0 product decisions

Northstar 0.7.0 is a clarity pass, not a larger budgeting system. The native
app should answer four ordinary questions without asking the user to learn
finance-app vocabulary:

1. What can I safely use this month?
2. What income and fixed costs support that answer?
3. What am I saving toward, and what should I add next?
4. What changed enough to deserve attention?

## Research translated into Northstar

- YNAB targets make a future amount actionable by calculating the contribution
  needed over time. Northstar uses the same useful idea in goal cards, but keeps
  five plain templates instead of exposing a category-target system.
- Quicken Simplifi separates recurring bills, savings goals, and remaining
  spending. Northstar adopts that inspectable monthly equation and avoids
  double-counting goals as both spending and savings.
- Monarch's flex-budget model emphasizes one flexible-spending number after
  income, fixed expenses, and savings. Northstar uses that as the primary Plan
  path and keeps direct amount editing available.
- Copilot lets people confirm and correct recurring items and keeps the review
  surface small. Northstar exposes confirm, exclude, rename, and recategorize
  controls while leaving recognized activity out of the review queue.

## Decisions

### Plan

- The primary Plan is monthly. A separate 90-day outlook shows the same five
  numbers for the next three months; it is not a second budgeting workflow.
- The equation is owned by the Python engine:
  `stable income - fixed costs - savings - buffer = flexible spending`.
- Stable income means recognized, recurring employment income. A saved plan is
  not allowed to redefine historical income.
- Fixed costs are inspectable recurring merchants included in the forecast.
- The UI offers three plain presets: Everyday plan, Save more, and Recovery
  month. Every amount can still be edited directly.
- Plan fields and equations use two decimal places. Transaction detail keeps
  the exact stored sign and cents.

### Goals

- Goal templates are Emergency fund, Planned purchase, Annual expense, Debt
  payoff, and General savings.
- Progress can be manual or linked to one existing Northstar account balance.
- Cards show progress, amount remaining, target date, monthly amount needed,
  suggested next contribution, and data source.
- Edit, pause, complete, and archive are lifecycle actions, not separate pages.
- Milestones at 25%, 50%, 75%, and completion use restrained status text and a
  progress bar. There are no points, streaks, confetti, or guilt messages.

### Insights

- Cash Flow Summary comes first for the selected 30- or 90-day period.
- Spending shows the top eight categories by default, with Show all for the
  complete reconciled breakdown. The proportional donut and exact ranked bars
  remain together.
- Top merchants is content-sized instead of inheriting the category card's
  height.
- Recurring costs and meaningful changes sit side by side. Findings compare
  equivalent complete periods, include positive changes, and remain capped at
  three.
- Net worth is a separate, collapsible section. With no balances, it shows one
  compact setup card rather than empty metrics.

### Settings

- Everyday preferences include landing page, 30/90-day default, density, Home
  secondary sections, net-worth visibility, and a preferred savings amount or
  percentage.
- Recognized payroll sources can be confirmed or excluded from the stable
  income baseline.
- Recurring items can be confirmed, excluded, renamed, or recategorized.
- Repeated variable merchants such as grocers do not become fixed recurring
  bills unless the user confirms them.

## Guardrails

- Financial calculations stay in Python. React renders engine results and
  sends edits for engine validation.
- Transfers, card payments, savings movements, investment movements, and
  refunds remain excluded from spending and income as already defined by the
  canonical financial semantics.
- All upgrade and destructive testing uses a copied database. The regular
  database is backed up before installation or migration.
- No private statement data, personal names, account identifiers, or database
  files belong in source control or validation documentation.

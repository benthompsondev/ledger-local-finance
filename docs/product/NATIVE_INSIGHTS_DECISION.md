# Native Home & Insights — decision note

Date: 2026-07-18. Scope: what financial intelligence the native app shows,
where it lives, and what from the Streamlit app is reused or retired.

## What the Python app already computes (reused as-is)

- `utils/insights.py::monthly_aggregates` — canonical per-month income /
  spending / net / savings rate with the shared exclusion language
  (CC payments, transfers, and cancelled rows). Refund-like credits count as
  Money In and never alter spending-category amounts.
- `utils/checkin.py::meaningful_changes` — ≤3 deterministic findings with
  evidence, comparison period, and a drill-down target.
- `utils/checkin.py::recommended_actions` — 1 primary + ≤1 secondary action.
- `utils/analytics.py` — `spending_by_category`, `top_merchants`,
  `find_recurring`, `spending_trend`.
- Net worth + account balance services.

New in this pass (same canonical filters, unit-tested):

- `utils/insights.py::spending_pace` — cumulative spending this month
  through today vs the previous month **through the same day**.
- `utils/insights.py::category_pace_breakdown` — ranked categories this
  month through today, each vs the previous month at the same point.

## Product decisions

- **Never compare a partial month to a full month.** Every current-month
  number is day-capped against the previous month and labeled
  "through day N"; partial bars render at reduced opacity with an
  asterisk. This is the standard treatment in strong finance products
  (pace vs "same point last month") and removes the main source of
  false alarms.
- **Home answers the seven weekly questions, in order:** freshness →
  verdict → Safe to Spend / income / spending / projected net → cash-flow
  trend → spending pace → where money went → ≤3 findings → 1–2 actions →
  goal progress. Nothing else.
- **Insights is the deep-dive**, organized as Trends (monthly income /
  spending / kept / savings rate), Spending (day-capped category ranking,
  merchants, recurring costs, findings), Accounts & net worth (balances,
  assets, liabilities). Goal editing stays on Goals.
- **Ranked horizontal bars, not pies.** Every category row shows amount,
  share, and a signed ▲/▼ same-point comparison, and clicks through to the
  matching transactions (prefilled filters on the Transactions screen).
- **One axis per chart; no dual-axis.** Income and spending are grouped
  bars on one scale; "kept" lives in tooltips, labels, and its own bar list.
- **Retired from Streamlit:** Money Pulse gauge, Mission Deck, Tiny Wins,
  the score dial, the 12-tab Trends sprawl, pie charts. Their strongest
  content (movers, recurring detection, fees) surfaces as findings instead.

## Chart engineering

- **No chart library.** Offline app, strict CSP, small chart-ready
  datasets from the sidecar — three bespoke SVG/HTML components
  (`desktop/src/charts.tsx`: CashflowChart, PaceChart, CategoryBars) keep
  the bundle tiny and fully controllable.
- **Financial truth is computed only in Python.** The new sidecar command
  `home_dashboard` returns labeled, comparison-carrying, completeness-
  flagged datasets; `insights_summary` gained `category_pace` and
  `recurring`. React only renders.
- Palette validated with the dataviz checks against the app's dark
  surface (#0e161e): income `#2ea043`, spending `#8957e5`, current-pace /
  magnitude `#3987e5`; previous-period references use muted ink. Deltas
  always pair color with a ▲/▼ symbol and signed text; series are always
  directly labeled or legended.

## Native slice implemented now

Home: cash-flow trend (≤12 months), spending pace, where-money-went with
drill-down, findings with drill-down. Insights: Trends, Spending (with
recurring), Accounts & net worth. Drill-downs land on Transactions with
prefilled filters. Deferred: balance history over time, fee/interest trend
chart, category creep multi-month view.

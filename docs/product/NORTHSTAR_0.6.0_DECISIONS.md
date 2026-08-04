# Northstar Ledger 0.6.0 product decisions

This pass stayed focused on daily trust: repair safe categories in upgraded
databases, keep transaction review small and actionable, and refuse to present
an incomplete plan as a confident forecast.

## Decisions

### Refresh safe built-in categories on upgrade

Northstar now performs a one-time categorization refresh for signed-contract
imports. It applies current bank-memo, merchant, and protected financial rules
only to rows without user provenance. Source amounts never change, deliberate
user edits and saved-rule results are skipped, and the database is backed up
before the migration.

This closes a product gap where a fresh import received better categories than
the same history in an upgraded database.

### Separate urgent review from ready suggestions

The transaction queue now distinguishes high-priority uncertainty, explainable
category suggestions, and low-value uncategorized rows that can remain quiet.
Suggestions can be filtered and accepted directly from the transaction table;
the full editor still supports matching-merchant updates, saved future rules,
and undo.

This follows the useful part of Monarch's explicit "needs review" model while
avoiding a queue that asks the user to confirm every transaction. See
[Monarch: Reviewing transactions](https://help.monarch.com/hc/en-us/articles/5528707082516-Reviewing-transactions).

### Gate forecasts on readiness

Projected spending and projected net are now hidden until a plan is confirmed,
current balances exist, transaction history is sufficient, and account
coverage is aligned. The Plan screen names the missing inputs instead of
showing a large low-confidence number. Unsaved plan edits survive navigation
within the current app session.

Quicken Simplifi's current Spending Plan describes money left after bills,
income, savings goals, and other planned expenses, including explicit
double-counting protections. Northstar keeps its own local deterministic model,
but uses the same product principle: a spendable-money number needs visible,
complete inputs. See [Quicken Simplifi: Understanding your Spending Plan](https://support.simplifi.quicken.com/en/articles/4212702-understanding-your-spending-plan).

The CFPB also found that people want plans but often find budgeting and
frequent benchmarking burdensome. Northstar therefore keeps one monthly plan
and a short readiness checklist instead of adding a full envelope-budgeting
system. See [CFPB: Consumer Insights on Managing Spending](https://files.consumerfinance.gov/f/documents/201702_cfpb_Consumer-Insights-on-Managing-Spending.pdf).

### Explain recurring estimates

Recurring costs remain limited to genuine spending with plausible cadence and
amount stability. Each supported recurring row now includes its cadence, last
observation, and an "expected around" date. User confirm/reject controls remain
available in Settings.

Copilot's recurring workflow similarly starts from observed transactions and
lets the user confirm frequency and matching filters. Northstar keeps this
local and deterministic. See [Copilot: Creating Recurrings](https://help.copilot.money/en/articles/3760068-creating-recurrings).

### Positive feedback stays factual

Northstar uses goal progress, savings progress, and evidence-backed positive
changes. It does not add points, guilt, streaks, or decorative rewards. CFPB
research connects a savings habit and emergency cushion with preparedness and
financial well-being, and finds guaranteed payday-style savings rules more
effective than contingent round-up rules. See [CFPB: Saving habits and financial security](https://www.consumerfinance.gov/data-research/research-reports/perceived-financial-preparedness-saving-habits-and-financial-security/)
and [CFPB: Consumer savings app strategies](https://www.consumerfinance.gov/data-research/research-reports/consumer-savings-app-strategies-and-savings-outcomes/).

## Deliberate non-goals

- No cloud connections, bank credentials, live pricing, or AI-generated truth.
- No React-owned financial calculations.
- No complex dashboard editor or full envelope-budgeting rebuild.
- No automatic overwrite of a user category, saved rule, balance, plan, or goal.

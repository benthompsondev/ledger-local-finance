# Ledger ↔ OpenClaw Finance Agent — Drop-In Pack

This document explains how to plug Ledger into an OpenClaw workspace as
a **read-only** finance context source for an external agent. It is
deliberately scoped: the agent reads, summarizes, and proposes; it
does **not** mutate Ledger's database in this pass.

---

## 1. What gets shared

The agent consumes one JSON file produced by Ledger:

```bash
python -m scripts.export_agent_context \
    --out exports/openclaw_finance_context.json \
    --period last_90_days
```

Optional flags:

| Flag                              | Effect                                           |
|-----------------------------------|--------------------------------------------------|
| `--period last_30_days`           | Tighter window for "current month" coaching     |
| `--period last_180_days`          | Wider window for trend questions                |
| `--period last_365_days`          | Full-year view                                   |
| `--include-recent-transactions`   | Append up to 50 most-recent transactions        |

The exported packet has these top-level sections (see
`utils/agent_context.py` for the source of truth):

- `generated_at`, `period`, `window`
- `coverage`, `latest_imported_month`
- `kpis` — income / spending / net / savings_rate
- `top_categories`, `top_merchants`
- `subscriptions` — active vs stale split
- `active_reduce_candidates`, `stale_subscriptions`
- `reduce_summary` — top controllable categories
- `recommendations` — totals + top 5
- `review_queue` — flagged count + reasons
- `money_progress` — level / xp / momentum / wins / risks
- `risks` — cash advance + fees in last 90 days
- `income_summary`
- `investments_summary` — top 5 holdings, by-account-type breakdown
- `net_worth_summary` — current totals + 24-snapshot history
- `month_plan` — current month's saved plan (mode, targets, category_targets[])
- `forecast` — month-end projection: MTD figures, projected income/spending/net, risk level (`on_track` / `watch` / `danger` / `insufficient_data`), drivers, safe_to_spend
- `goals` — active goals with auto-tracked progress (linked to net_worth / investments / cash_balance)
- `bills_summary` — **Pass 23 grouped**: `commitment_monthly_estimate` (fixed bills + active subscriptions, locked into forecast) and `variable_monthly_watch` (recurring grocery / shopping / gas — watched but NOT locked). Sub-arrays: `fixed_commitments`, `active_subscriptions`, `recurring_variable_merchants`, `stale_or_inactive`. The legacy `monthly_estimate` field now mirrors `commitment_monthly_estimate` only. `top_items` preserved for back-compat.
- `next_actions` — up to 5 short reminders distilled from plan + forecast + recommendations
- `reminder_suggestions` — Pass 22 — up to 6 concrete reminder strings (forecast-risk-aware) the agent can turn into `create_reminder` proposals
- `ask_ledger_supported_skills` — preset ids (see §4)
- `recent_transactions` (only with `--include-recent-transactions`)

---

## 1a. Privacy posture (Pass 23)

The export **does** include merchant names and category-level totals
because the agent needs them to give specific advice. That makes the
export sensitive — it is a fair description of where the user spends
money. Treat it accordingly:

- **Local OpenClaw use is fine.** The export stays on the user's
  machine.
- **Sharing the export with cloud tools is your call.** If the agent
  runs in a cloud workspace, the merchant names and KPIs are sent
  there. Decide whether that fits your privacy bar before exporting.
- **Future: privacy modes** (`--privacy summary` / `--privacy redacted`)
  will replace merchant names with stable labels. Not implemented in
  this pass — adding it without breaking the agent contract is its
  own focused change.
- **Always safe:** the export has never contained API keys, full
  account numbers, AI prompts/responses, or raw config. A regression
  test in `scripts/smoke_test.py` enforces this.

## 2. What is NEVER in the export

The export is built from derived analytics. The script never reads
config files or environment variables. The packet contains:

- ❌ no API keys (MiniMax / OpenAI / Anthropic)
- ❌ no raw `config.json` content
- ❌ no full account numbers (parser masks them at ingest as `•••<last4>`)
- ❌ no AI prompts / responses
- ❌ no full transaction list by default (opt-in via flag, capped at 50)
- ❌ no email, address, or other PII

A regression test in `scripts/smoke_test.py` asserts `sk-`, `Bearer `,
and `"api_key"` strings do not appear in the export.

---

## 3. Agent contract

### What the Finance Agent IS allowed to do
- Read the exported JSON.
- Summarize state (net worth, cashflow, subscriptions, etc).
- Prioritize action items by deterministic impact (annual_impact,
  controllable_total).
- Explain *why* something is flagged using the data already in the
  packet.
- Suggest next steps (cancel sub, set budget target, re-categorize).
- Cite sections back ("per `recommendations.top[2]`…") so the user can
  verify.

### What the Finance Agent is NOT allowed to do
- ❌ Mutate Ledger's database directly.
- ❌ Add, edit, or delete transactions, budgets, or recommendations.
- ❌ Move money, place trades, or initiate transfers.
- ❌ Provide individualized investment advice ("buy XEQT") — Ledger
  shows holdings and unrealized returns from your CSV; price-aware
  recommendations would need data Ledger doesn't fetch.
- ❌ Invent numbers not in the packet.
- ❌ Echo or store API keys, secrets, or any field the packet doesn't
  contain.

If the agent wants to act on the data, it must **propose** the action
in chat for the user to apply manually inside Ledger. A future pass
may add a write-approval workflow.

---

## 4. Supported skills

The export carries `ask_ledger_supported_skills` — preset ids the
agent can ask the user to invoke inside Ledger via the Ask Ledger
panel. As of Pass 19, these are:

| Skill id                   | What it does                                |
|----------------------------|---------------------------------------------|
| `top_cuts`                 | What should I cut first this month?         |
| `active_subscriptions`     | Which active subs should I review?          |
| `over_target_categories`   | Which categories are above target?          |
| `what_changed`             | What changed since last month?              |
| `cleanup_first`            | What transactions should I clean up first?  |
| `explain_score`            | Why is my health score where it is?         |
| `safest_hundred`           | What is my safest $100/month savings move?  |
| `weekly_focus`             | What should I focus on this week?           |

Future skills (design notes only — not yet implemented):

- `ledger.weekly_review()`
- `ledger.net_worth()` — already implicitly served by `net_worth_summary`
- `ledger.scenario(category, delta)` — what-if math
- `ledger.reduce_plan()` — currently served by `recommendations` + `reduce_summary`
- `ledger.investments_summary()` — already in packet

---

## 5. Recommended agent prompt

See `openclaw/finance_agent_prompt.md` for the canonical version.

Short form:

> You are the OpenClaw Finance Agent. You have read-only access to a
> JSON snapshot of the user's Ledger state. You explain, summarize,
> and propose actions; you never mutate the database. Cite the
> sections you used. If the user asks for something the snapshot
> can't answer, say so plainly — never invent numbers. For investment
> questions, only describe what's in `investments_summary`; do not
> recommend specific trades.

---

## 6. Proposal workflow (Pass 21 — schema only, apply deferred)

The agent does NOT mutate Ledger. When it wants to suggest a change,
it writes a structured **proposal JSON** to
`exports/openclaw_proposals/`. Ledger does not auto-apply proposals
in this pass — the schema is documented and exemplified now so
OpenClaw and Ledger agree on shape; an in-app review/approve flow
ships in a later pass.

- Schema: `openclaw/PROPOSAL_SCHEMA.md`
- Examples: `openclaw/proposals/example_*.json`

Proposal types currently defined:
- `create_month_plan`
- `update_category_target`
- `create_goal`
- `mark_subscription_reviewed`
- `add_manual_balance_snapshot`
- `create_reminder`

Every proposal carries `requires_user_approval: true` and an
`evidence` block citing the agent_context section it used. The
future apply-step will refuse proposals without those.

## 7. Coaching focus, not investment advice

The Pass 21 plan/forecast surfaces are designed for budget coaching:
"what is my plan for the month, am I on track, how much can I safely
spend, what should I cut next." The agent should lean into that
framing. Investment context is informational only — surface
positions, account-type breakdown, and unrealized returns that are
already in the snapshot, but do not recommend buys/sells.

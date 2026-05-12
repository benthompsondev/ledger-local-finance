# OpenClaw Finance Agent — Proposal Schema (Pass 21)

The agent is **read-only** with respect to Ledger's database. When it
wants to suggest a change, it writes a structured proposal JSON file
to `exports/openclaw_proposals/`. The user reviews each proposal
inside Ledger; nothing is applied automatically. A future pass will
add the in-app review UI and an apply step.

This document is the canonical schema. Any field not listed here is
ignored.

---

## Top-level shape

```jsonc
{
  "proposal_id":            "string (required)",   // stable id for dedup, e.g. "cancel_pixverse_2026-05"
  "type":                   "string (required)",   // see "Proposal types"
  "title":                  "string (required)",   // short, human-readable
  "reason":                 "string (required)",   // why this is being proposed
  "evidence":               "object (required)",   // exact context fields cited
  "proposed_change":        "object (required)",   // the diff the agent wants applied
  "risk":                   "low | medium | high", // best-effort
  "requires_user_approval": true,                  // always true in Pass 21
  "created_at":             "YYYY-MM-DDTHH:MM:SSZ",
  "agent_version":          "string"               // optional
}
```

The `evidence` object MUST cite the agent_context section(s) it used,
e.g. `{"section": "recommendations.top[2]"}` — so the user can verify.

---

## Proposal types

### `create_month_plan`
```jsonc
{
  "type": "create_month_plan",
  "proposed_change": {
    "month":            "YYYY-MM",
    "mode":             "tight",                  // one of utils.planner.PLAN_MODES
    "income_target":    6500,
    "spending_target":  4200,
    "savings_target":   2300,
    "category_targets": [
      { "category": "Groceries", "target_amount": 600, "difficulty": "normal" },
      { "category": "Food & Convenience", "target_amount": 300, "difficulty": "tight" }
    ]
  }
}
```

### `update_category_target`
```jsonc
{
  "type": "update_category_target",
  "proposed_change": {
    "month":         "YYYY-MM",
    "category":      "Subscriptions & Digital",
    "target_amount": 80,
    "difficulty":    "tight"
  }
}
```

### `create_goal`
```jsonc
{
  "type": "create_goal",
  "proposed_change": {
    "name":          "Cash buffer",
    "type":          "cash_buffer",
    "target_amount": 5000,
    "linked_metric": "cash_balance",
    "target_date":   "2026-12-31"
  }
}
```

### `mark_subscription_reviewed`
```jsonc
{
  "type": "mark_subscription_reviewed",
  "proposed_change": {
    "merchant":  "PIXVERSE",
    "decision":  "cancel",          // cancel | keep | downgrade
    "expected_monthly_savings": 7.50
  }
}
```

### `add_manual_balance_snapshot`
```jsonc
{
  "type": "add_manual_balance_snapshot",
  "proposed_change": {
    "account_name": "Tangerine Chq",
    "account_kind": "chequing",
    "balance":      2510.42,
    "currency":     "CAD",
    "as_of_date":   "2026-05-04"
  }
}
```

### `create_reminder`
```jsonc
{
  "type": "create_reminder",
  "proposed_change": {
    "title":       "Confirm Pixverse cancellation",
    "due_date":    "2026-05-12",
    "cadence":     "once",          // optional: once | weekly | monthly
    "linked_to":   "subscriptions[0]",
    "notes":       "Verify the next billing cycle does not charge."
  }
}
```

The Pass 22 `reminder_suggestions` section in `agent_context` is the
agent's primary input for choosing which reminders to propose. It
already returns concrete strings (e.g. *"Weekly: review forecast risk
(watch). Projected net $1,250."*) — the agent should turn each into a
`create_reminder` proposal with a sensible due date.

---

## Hard rules

1. The agent **never writes** anything other than proposal JSON files.
   It does not edit `data/finance.db`. It does not call any Ledger
   write API.
2. Every proposal includes `requires_user_approval: true`. Apps that
   later add an apply path MUST NOT honor proposals lacking this flag.
3. The agent must cite which `agent_context` section informed each
   proposal in `evidence`. Proposals without `evidence` are rejected
   on review.
4. No proposal may contain API keys, raw secrets, or full account
   numbers. Account numbers are stored masked (`•••<last4>`) at
   parse-time.
5. The `risk` field is informative — the user always decides.

---

## File layout

```
exports/openclaw_proposals/
  ├── 2026-05-04T18-12-00Z_cancel_pixverse.json
  ├── 2026-05-04T18-12-30Z_create_buffer_goal.json
  └── …
```

Filenames sort by timestamp. The Ledger UI for Pass 22+ will list
them, render the diff, and let the user approve / reject / archive
each.

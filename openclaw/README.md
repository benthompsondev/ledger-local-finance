# OpenClaw drop-in pack

Files in this folder describe how Ledger plugs into an OpenClaw
workspace as a **read-only** finance context source.

| File                       | What it is                                |
|----------------------------|-------------------------------------------|
| `README.md`                | This index                                 |
| `finance_agent_prompt.md`  | Recommended system prompt for the agent   |

The contract, supported skills, and what the agent must / must not do
live in the top-level **`OPENCLAW_FINANCE_AGENT.md`**. Read that first.

## Quickstart

```bash
# 1. Export Ledger's read-only context
python -m scripts.export_agent_context \
    --out exports/openclaw_finance_context.json

# 2. Drop the JSON file into your OpenClaw workspace's data folder.
# 3. Use openclaw/finance_agent_prompt.md as the agent's system prompt.
# 4. Refresh the export whenever you import new statements.
```

The export is regenerable on demand — there is no incremental sync.
Re-run the script after each import batch / categorisation pass.

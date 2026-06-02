# Ledger Agent Guide

Use this when an AI coding agent or maintainer works in this repository.

## First Principles

- Inspect the current worktree before editing.
- Treat local financial data as private.
- Keep deterministic calculations as the truth layer.
- AI may explain, summarize, and coach, but must not mutate financial data.
- OpenClaw context is read-only unless an explicit proposal file is reviewed.

## Never Commit

- `data/finance.db` or any `*.db`, `*.db-wal`, `*.db-shm`;
- `config.json`, `.env*`, `.streamlit/secrets.toml`;
- statement PDFs, bank CSVs, investment exports, screenshots with real data;
- generated exports, share zips, logs, or local handoff notes;
- account numbers, API keys, bearer tokens, emails, addresses, or private paths.

## Required Gates

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m compileall -q app.py pages utils parsers scripts components
.\.venv\Scripts\python.exe -m scripts.smoke_test
.\.venv\Scripts\python.exe -m scripts.export_agent_context
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

## Product Direction

Ledger should help answer:

- Where am I financially right now?
- Am I better or worse than last month?
- What changed?
- How much can I safely spend?
- What is the next practical move?
- What should I cut or protect this week?

Prefer a practical weekly loop over decorative charts:

`Import -> Review -> Understand -> Plan -> Forecast -> Reduce -> Track -> Export`

## Safe Change Pattern

1. Inspect relevant files and current behavior.
2. Make the smallest useful change.
3. Add or update smoke coverage for finance math, privacy, exports, or parsers.
4. Run the gates.
5. Summarize what changed, why it matters, and any remaining risk.

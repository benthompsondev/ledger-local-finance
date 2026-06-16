# Maintenance Guardrails

Ledger is a small open-source finance app, but it is maintained with habits that matter on larger projects: tests, reviewable changes, privacy boundaries, and clear contributor rules.

This document explains how I use coding tools without weakening the local-first safety model.

## Why The Guardrails Matter

Ledger has several areas where careful tooling helps:

- tracing Streamlit page flows across `pages/`, `utils/`, and `components/`;
- reviewing finance calculations for edge cases and regression risk;
- expanding smoke tests around imports, statement summaries, exports, and privacy;
- checking share zips and git state before public release;
- improving documentation, issue templates, release notes, and contributor guides;
- keeping OpenClaw/AI context read-only and grounded in deterministic packets.

The project is intentionally structured so maintenance can move faster while the app remains safe for personal financial data.

## Guardrails

Any coding assistant or automation must follow these rules:

- no real financial data in prompts, commits, issues, screenshots, or exports;
- no AI mutation of transactions, plans, goals, imports, or account data;
- deterministic helpers remain the source of truth;
- generated advice must cite local evidence packets;
- OpenClaw context remains read-only unless explicit proposal files are reviewed;
- every publishable change must pass validation and privacy checks.

## Current Maintenance Workflow

1. Inspect `git status` and the relevant files.
2. Make a small, focused change.
3. Run local validation:

   ```powershell
   .\.venv\Scripts\python.exe -m compileall -q app.py pages utils parsers scripts components
   .\.venv\Scripts\python.exe -m scripts.smoke_test
   .\.venv\Scripts\python.exe -m scripts.export_agent_context
   .\.venv\Scripts\python.exe -m scripts.make_share_zip
   ```

4. Check tracked files for private artifacts.
5. Push through GitHub Actions.
6. Review CI before treating the change as complete.

## Good Maintenance Tasks

- Add parser tests for a new bank statement format using synthetic fixtures.
- Review a scoring or budgeting change for false assumptions.
- Improve a Dashboard/Plan/Reduce workflow without adding clutter.
- Create a GitHub issue from a reproducible bug.
- Summarize a PR and point out missing privacy checks.
- Prepare release notes after a passing validation run.

## What This Shows As A Project

Ledger shows a practical pattern for tool-assisted maintenance:

- coding tools help with navigation, tests, docs, and review;
- the application keeps data ownership local;
- sensitive exports are explicitly guarded;
- human review stays in charge of financial behavior.

That combination is the point: the project has enough structure, safety gates, and real workflows for tooling to improve maintenance without becoming a source of unreviewed automation.

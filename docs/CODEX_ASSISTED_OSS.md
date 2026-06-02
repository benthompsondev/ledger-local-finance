# Codex-Assisted Open Source Maintenance

Ledger is a small open-source finance app, but it is maintained with the same
habits that matter on larger projects: tests, reviewable changes, privacy
boundaries, and clear contributor rules.

This document explains how Codex fits into the project without weakening the
local-first safety model.

## Why Codex Helps Here

Ledger has several areas where an AI coding assistant is useful:

- tracing Streamlit page flows across `pages/`, `utils/`, and `components/`;
- reviewing finance calculations for edge cases and regression risk;
- expanding smoke tests around imports, statement summaries, exports, and privacy;
- checking share zips and git state before public release;
- improving documentation, issue templates, release notes, and contributor guides;
- keeping OpenClaw/AI context read-only and grounded in deterministic packets.

The project is intentionally structured so Codex can help with maintenance while
the app remains safe for personal financial data.

## Guardrails

Codex and other AI tools must follow these rules:

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

## Good Codex Tasks For This Repo

- Add parser tests for a new bank statement format using synthetic fixtures.
- Review a scoring or budgeting change for false assumptions.
- Improve a Dashboard/Plan/Reduce workflow without adding clutter.
- Create a GitHub issue from a reproducible bug.
- Summarize a PR and point out missing privacy checks.
- Prepare release notes after a passing validation run.

## What This Shows As An OSS Project

Ledger demonstrates a practical pattern for AI-assisted maintenance:

- AI helps with code navigation, tests, docs, and review.
- The application keeps data ownership local.
- Sensitive exports are explicitly guarded.
- Human review stays in charge of financial behavior.

That combination is the point: Codex is useful here because the project has
enough structure, safety gates, and real workflows for it to improve maintenance
instead of becoming a source of unreviewed automation.

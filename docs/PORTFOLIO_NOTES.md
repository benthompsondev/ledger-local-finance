# Portfolio Notes

Ledger is meant to be useful as a real local app and credible as a technical
portfolio project.

## What It Demonstrates

- Python application structure beyond one-off scripts.
- Streamlit UI design for a practical workflow.
- SQLite schema design, migrations, and local persistence.
- PDF/CSV import paths with duplicate detection.
- Deterministic finance calculations with test coverage.
- GitHub Actions validation on every push.
- Privacy-safe export and share-package tooling.
- AI-assisted features with explicit read-only guardrails.

## Why It Matters For DevOps / Platform Roles

Ledger is not an infrastructure repo, but it shows habits that transfer directly
to DevOps and systems work:

- clear validation gates;
- safe handling of sensitive local data;
- repeatable local setup;
- CI that checks both code and private-artifact mistakes;
- structured documentation for contributors and AI agents;
- practical automation around release/share artifacts.

## Interview Talking Points

- "I built a local-first personal finance app because I wanted a real weekly-use
  product, not just a dashboard."
- "The app protects partial statement months so incomplete imports do not distort
  the score."
- "AI features are read-only and grounded in deterministic packets. The model can
  explain, but it cannot mutate financial records."
- "The share script and CI prevent private databases, configs, PDFs, logs, and
  generated exports from being published."
- "I used this project to practice Python app structure, SQLite, Streamlit,
  GitHub Actions, privacy-aware release checks, and Codex-assisted maintenance."

## Next Portfolio Upgrades

- Capture demo-data screenshots.
- Add a short walkthrough GIF.
- Add a first-run onboarding flow.
- Add synthetic parser fixtures for more banks.
- Create a small release with notes and a tagged version.

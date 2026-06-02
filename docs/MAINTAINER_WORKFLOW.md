# Maintainer Workflow

Ledger is maintained as a practical local-first finance app. The workflow is
designed to be friendly to human maintainers and AI coding agents while keeping
private data out of the repository.

## Weekly Maintenance Loop

1. Check `git status`.
2. Review open issues or user feedback.
3. Pick one small, testable improvement.
4. Inspect the relevant files before editing.
5. Keep finance math deterministic.
6. Run the validation gates.
7. Scan for private data before committing.
8. Write a plain-English commit message.

## Codex-Assisted OSS Workflow

Codex can help with:

- code inspection and call-path tracing;
- issue triage and reproduction steps;
- small feature passes;
- smoke-test expansion;
- privacy scans before publishing;
- README and contributor documentation;
- release checklists and GitHub Actions cleanup.

Codex should not:

- commit real financial data;
- invent finance numbers;
- bypass deterministic helper functions;
- convert read-only AI context into write access;
- publish a release without human review.

## Validation Commands

Run these from the repository root:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m compileall -q app.py pages utils parsers scripts components
.\.venv\Scripts\python.exe -m scripts.smoke_test
.\.venv\Scripts\python.exe -m scripts.export_agent_context
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

## Privacy Scan Before Push

Use a tracked-file scan before publishing:

```powershell
git ls-files | Select-String -Pattern "finance.db|config.json|launcher.log|CLAUDE_HANDOFF|\.claude|\.zip|\.pdf"
git grep -n "sk-cp-\|Bearer \|BEGIN PRIVATE KEY\|api_key=.*[A-Za-z0-9]"
```

Expected result: no real secrets or private files. References inside safety
tests and documentation are okay when they are clearly examples or exclusion
rules.

## Release Notes

Good release notes should explain:

- what changed for weekly users;
- which imports and safety gates were verified;
- whether any finance calculation changed;
- what remains intentionally local-only or read-only.

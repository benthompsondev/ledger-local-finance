# Contributing to Ledger

Thanks for taking a look at Ledger. This project is a local-first personal
finance app, so contribution quality is mostly about trust: predictable math,
private data boundaries, and practical workflows.

## What This Project Values

- Local-first data ownership.
- Deterministic calculations before generated explanations.
- Plain-language UX that helps a normal person make one better money decision.
- Small, testable changes over broad rewrites.
- Privacy-safe examples, screenshots, exports, and bug reports.

## Before You Open a Pull Request

1. Use demo data, never real financial data, for screenshots or examples.
2. Do not commit `data/`, `config.json`, `.venv/`, exports, logs, statement PDFs,
   bank CSVs, or generated share zips.
3. Keep explanation features read-only. They may summarize deterministic packets but must not
   create, edit, or delete financial records.
4. Preserve Tangerine PDF import, generic CSV import, investment CSV import,
   net-worth snapshots, launcher reliability, and share-zip safety.
5. Run the validation commands below.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open `http://127.0.0.1:8501`.

## Validation

```powershell
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m compileall -q app.py pages utils parsers scripts components
.\.venv\Scripts\python.exe -m scripts.smoke_test
.\.venv\Scripts\python.exe -m scripts.export_openclaw_context
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

## Pull Request Checklist

- [ ] The change is scoped and understandable.
- [ ] The validation commands pass.
- [ ] No private data, screenshots, PDFs, CSVs, keys, logs, or database files are included.
- [ ] Any finance math change includes a deterministic test or smoke assertion.
- [ ] Any explanation or export change explains which deterministic evidence packet it uses.
- [ ] User-facing copy is practical and not financial advice.

## Maintenance Notes

Automation and tooling are welcome here, but the rules are the same as for human
contributors:

- inspect before editing;
- keep private data out of commits;
- run the safety gates;
- explain the change in human terms;
- never let generated output mutate financial data.

See `docs/MAINTAINER_WORKFLOW.md` for the expected maintainer workflow.

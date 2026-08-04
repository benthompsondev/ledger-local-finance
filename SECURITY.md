# Security and Privacy

Northstar Ledger is a local-first Windows desktop app. Finance calculations,
statement imports, backups, settings, and the SQLite database stay on the
computer running it.

The product-tour website contains only static files and synthetic screenshots.
It cannot open a local database or run the finance engine in a browser.

Optional AI is the one deliberate exception to the local boundary. It is off
by default. When someone enables an online provider and asks a question, the
evidence packet for that request is sent to the provider they configured. AI
is read-only and cannot edit financial data.

## What Should Never Be Shared

Do not include any of the following in issues, pull requests, screenshots, share
zips, or demos:

- real bank statements, PDFs, CSVs, or investment exports;
- `data/finance.db` or any other local database;
- `config.json`, API keys, bearer tokens, or provider credentials;
- account numbers, addresses, emails, names, employer details, or internal paths;
- generated OpenClaw exports made from real financial data;
- launcher logs or bug reports that have not been reviewed.

## Built-In Safety Tools

Use the share builder instead of manually zipping the project:

```powershell
.\.venv\Scripts\python.exe -m scripts.make_share_zip
```

The script excludes local databases, config files, logs, virtual environments,
exports, and developer notes. It also scans included text files for common
secret patterns.

The repository and CI also run:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_tracked_files
.\.venv\Scripts\python.exe -m scripts.doctor
```

These checks block common database, statement, export, log, and secret paths.
They are a backstop; contributors still need to inspect every public diff.

## Reporting a Vulnerability

If you find a privacy or security issue, please open a GitHub issue with a
minimal synthetic reproduction. Do not paste real financial data or secrets.

Good reports include:

- what went wrong;
- what data could be exposed or changed;
- a synthetic example or demo-data reproduction;
- the command or UI flow that triggered it.

## API Key Rotation

If a real API key is ever pasted into a terminal, issue, screenshot, or commit,
rotate it immediately with the provider. Removing it from git history or local
files is not enough.

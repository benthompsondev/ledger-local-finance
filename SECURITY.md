# Security and Privacy

Ledger is designed as a local-first desktop-style app. It is not intended to be
hosted publicly with real financial data.

## What Should Never Be Shared

Do not include any of the following in issues, pull requests, screenshots, share
zips, or demos:

- real bank statements, PDFs, CSVs, or investment exports;
- `data/finance.db` or any other local database;
- `config.json`, API keys, bearer tokens, or `.streamlit/secrets.toml`;
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

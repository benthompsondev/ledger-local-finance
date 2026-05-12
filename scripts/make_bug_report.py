"""
scripts/make_bug_report.py — Pass 24.

Build a sanitized bug-report bundle so the user can share what's
broken without leaking secrets or personal finance data.

What goes IN the bundle:
  • diagnostics.json  — utils.diagnostics.build_diagnostics() output
  • smoke_output.txt  — last 200 lines of `python -m scripts.smoke_test`
  • launcher.log      — sanitized (lines containing key-like patterns
                        are redacted)
  • environment.txt   — short text summary of env block
  • README.txt        — what the bundle is + how to read it

What is NEVER in the bundle:
  • config.json, .env, secrets.toml — never copied
  • data/finance.db, finance.db-wal, finance.db-shm — never copied
  • API keys (regex-redacted from any text we copy)
  • raw transactions / merchant names — diagnostics.json has counts
    only, not row data
  • raw AI prompts/responses — only call status metadata
  • full account numbers — already masked at parse time

Usage:
    python -m scripts.make_bug_report
    python -m scripts.make_bug_report --out exports/bug_reports/foo.zip

Output:
    exports/bug_reports/ledger_bug_report_YYYYMMDD-HHMMSS.zip
    (returns full path on stdout)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Patterns to redact from ANY text we copy into the bundle.
_REDACTORS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),       "sk-•••REDACTED•••"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),    "sk-ant-•••REDACTED•••"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"),          "AKIA•••REDACTED•••"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer •••REDACTED•••"),
    (re.compile(
        r"""api[_-]?key['"]?\s*[:=]\s*['"][A-Za-z0-9._\-]{8,}['"]""",
        re.IGNORECASE,
    ), "api_key=•••REDACTED•••"),
    # Long base64-ish blobs (>40 chars of [A-Za-z0-9+/=_-]) — ATM tokens, etc.
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "•••LONG-TOKEN-REDACTED•••"),
]


def _redact(text: str) -> str:
    out = text
    for pat, repl in _REDACTORS:
        out = pat.sub(repl, out)
    return out


def _run_smoke() -> str:
    """Run smoke test and capture the last ~200 lines. Tolerant of
    failure — if smoke crashes, we capture the crash output."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "scripts.smoke_test"],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=120,
        )
        text = (r.stdout or "") + ("\n--- STDERR ---\n" + r.stderr if r.stderr else "")
        rc_line = f"\n--- exit code: {r.returncode} ---\n"
        text += rc_line
    except Exception as e:
        text = f"smoke_test failed to run: {e!r}"
    # Keep tail only.
    lines = text.splitlines()
    return "\n".join(lines[-200:])


def _redact_log(path: Path) -> str:
    if not path.exists():
        return "(no launcher.log present)"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(could not read launcher.log: {e})"
    return _redact(text)


def _readme_blurb(out_zip: Path) -> str:
    return (
        "Ledger bug report bundle\n"
        "========================\n\n"
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Bundle:    {out_zip.name}\n\n"
        "Files:\n"
        "  diagnostics.json  — environment, DB health, finance health,\n"
        "                      AI readiness, sharing status. No keys, no\n"
        "                      raw transactions, no merchant names.\n"
        "  environment.txt   — quick text summary.\n"
        "  smoke_output.txt  — last 200 lines of `scripts.smoke_test`.\n"
        "  launcher.log      — redacted (token-like patterns replaced).\n\n"
        "What is NEVER in this bundle:\n"
        "  config.json, .env, secrets.toml\n"
        "  data/finance.db (your transactions)\n"
        "  raw AI prompts/responses\n"
        "  raw API keys (any matched pattern is redacted)\n\n"
        "If you find anything sensitive in this zip, please open an\n"
        "issue describing what was leaked so the redactor can be\n"
        "tightened — but do NOT attach the leaking file.\n"
    )


def _env_summary(diag: dict) -> str:
    env = diag.get("environment") or {}
    db  = diag.get("database")    or {}
    ai  = diag.get("ai")          or {}
    fin = diag.get("finance")     or {}
    sh  = diag.get("sharing")     or {}
    lines = [
        f"Ledger:    {env.get('ledger_pass') or env.get('ledger_version')}",
        f"Python:    {env.get('python')}",
        f"Streamlit: {env.get('streamlit')}",
        f"Platform:  {env.get('platform')}",
        f"In venv:   {env.get('in_venv')}",
        f"Project:   {env.get('project_path')}",
        f"DB exists: {db.get('db_exists')}  (path: {db.get('db_path')})",
        f"Tables:    {len(db.get('tables') or [])}  "
            f"(missing: {len(db.get('missing_tables') or [])})",
        f"Tx count:  {(db.get('counts') or {}).get('transactions', 0)}",
        f"AI ready:  {ai.get('ready')}  "
            f"(provider: {ai.get('provider') or '-'}, "
            f"model: {ai.get('model') or '-'})",
        f"Plan:      saved={fin.get('plan_saved')}  "
            f"mode={fin.get('plan_mode') or '-'}  "
            f"risk={fin.get('forecast_risk')}",
        f"Sharing:   config.json={sh.get('config_json_present')}  "
            f"db={sh.get('finance_db_present')}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Build a sanitized bug-report bundle."
    )
    default_out = _ROOT / "exports" / "bug_reports" / (
        f"ledger_bug_report_"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    )
    p.add_argument("--out", default=str(default_out))
    p.add_argument("--skip-smoke", action="store_true",
                   help="Skip running the smoke test inside the bundle.")
    args = p.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Building bug report bundle: {out}")

    # 1. Diagnostics.
    try:
        from utils.diagnostics import build_diagnostics
        diag = build_diagnostics()
    except Exception as e:
        diag = {
            "_error": f"build_diagnostics failed: {e!r}",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
    diag_json = json.dumps(diag, indent=2, default=str)
    # Defense-in-depth: redact even diagnostics output before adding.
    diag_json = _redact(diag_json)

    # 2. Smoke output.
    smoke_text = "(skipped)" if args.skip_smoke else _run_smoke()
    smoke_text = _redact(smoke_text)

    # 3. Launcher log (redacted).
    log_text = _redact_log(_ROOT / "launcher.log")

    # 4. Env summary.
    env_text = _env_summary(diag)

    # 5. README.
    readme = _readme_blurb(out)

    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ledger_bug_report/README.txt",        readme)
            zf.writestr("ledger_bug_report/diagnostics.json",  diag_json)
            zf.writestr("ledger_bug_report/environment.txt",   env_text)
            zf.writestr("ledger_bug_report/smoke_output.txt",  smoke_text)
            zf.writestr("ledger_bug_report/launcher.log.txt",  log_text)
    except Exception as e:
        print(f"ERROR: failed to write zip: {e}", file=sys.stderr)
        return 1

    size_kb = out.stat().st_size / 1024
    print(f"  OK  wrote {out}  ({size_kb:,.1f} KB)")
    print()
    print("This bundle contains NO config.json, NO finance.db, NO API keys,")
    print("NO raw transactions, and NO AI prompts/responses. Safe to share.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

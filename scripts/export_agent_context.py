"""
scripts/export_agent_context.py — write Ledger's read-only agent context
to a JSON file so an external agent (OpenClaw Finance) can consume it.

Usage
─────
    python -m scripts.export_agent_context
    python -m scripts.export_agent_context --out exports/openclaw_finance_context.json
    python -m scripts.export_agent_context --period last_180_days

The exported packet is a single JSON object with the same shape
`utils.agent_context.build_agent_context()` returns. It is **read-only**:
the export script does not call any mutators, and the packet itself
contains no API keys, no raw config, and no full account numbers.

By default `include_recent_transactions` is False — pass
`--include-recent-transactions` to opt in (capped to 50 rows).

Exit codes
──────────
    0 — file written
    1 — error (printed to stderr)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python scripts/export_agent_context.py` from repo
# root without -m by injecting the parent dir onto sys.path.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.agent_context import build_agent_context  # noqa: E402
from utils.database import init_db                    # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Export Ledger's read-only agent context as JSON."
    )
    p.add_argument(
        "--out", default=str(_ROOT / "exports" / "openclaw_finance_context.json"),
        help="Output path (default: exports/openclaw_finance_context.json).",
    )
    p.add_argument(
        "--period", default="last_90_days",
        choices=("last_30_days", "last_90_days", "last_180_days",
                 "last_365_days"),
    )
    p.add_argument(
        "--include-recent-transactions", action="store_true",
        help="Include up to 50 most-recent transactions in the export.",
    )
    args = p.parse_args(argv)

    init_db()
    try:
        ctx = build_agent_context(
            period=args.period,
            include_recent_transactions=args.include_recent_transactions,
        )
    except Exception as e:
        print(f"ERROR: build_agent_context failed: {e}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        out_path.write_text(json.dumps(ctx, indent=2, default=str),
                            encoding="utf-8")
    except Exception as e:
        print(f"ERROR: could not write {out_path}: {e}", file=sys.stderr)
        return 1

    # Print a small summary so the caller can verify what was exported.
    sections = [k for k, v in ctx.items() if v not in (None, "", [], {})]
    print(f"Wrote {out_path}")
    print(f"  period:       {ctx.get('period')}")
    print(f"  generated_at: {ctx.get('generated_at')}")
    print(f"  sections:     {len(sections)}  ({', '.join(sections[:8])}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

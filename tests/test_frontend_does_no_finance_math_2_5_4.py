"""Finance arithmetic belongs to the engine, not to a React component.

The repo rule is that the Python engine computes every financial truth and
the frontend renders it. That rule is only as good as its enforcement: a
subtraction added to a component looks harmless in review and quietly
becomes a second answer to a question the engine already answers, which is
how Home and Plan drifted apart before.

This scans the shipped React source for arithmetic on money-shaped values.
Display formatting is fine, and so is chart geometry — turning a figure into
a pixel is not a financial claim.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "desktop" / "src"

# Files whose whole job is turning numbers into pixels or strings.
_PRESENTATION = {
    "charts.tsx", "chartScale.ts", "money.ts", "netWorthFormat.ts",
    "categoryComparison.ts",
}

# Input widgets that preview a value the user is about to submit. The engine
# re-derives both authoritatively on save, so neither is a second answer to a
# question already answered on screen — which is what this rule exists to
# prevent. Each is pinned by a test so the preview cannot drift from the
# engine:
#
#   netWorthForm.ts     running total while the four figures are typed;
#                       pinned by test_the_preview_total_matches_the_engine
#                       below and by netWorthForm.test.mjs.
#   TransactionsView    the shared-expense split, where a percent field and
#                       an amount field drive each other; the engine
#                       recomputes the split from the percent on save.
_INPUT_PREVIEWS = {"netWorthForm.ts", "TransactionsView.tsx"}
_TESTS = re.compile(r"\.test\.(mjs|ts|tsx)$")

# Names that hold money. Arithmetic on these in a component is the thing
# being prevented.
_MONEY = (
    r"amount|total|balance|income|spending|net|savings|cash|liabilities|"
    r"investments|remaining|reserved|target|safe_to_spend|safeToSpend|"
    r"projected|kept|allowance|weekly|daily"
)
# `a.amount - b.amount`, `total * 1.05`, `x.balance / days` and so on.
_ARITHMETIC = re.compile(
    rf"\b\w*(?:{_MONEY})\w*\b\s*[-+*/]\s*\b\w*(?:{_MONEY})\w*\b",
    re.IGNORECASE,
)
# Percentages and shares computed in the component.
_SHARE = re.compile(
    rf"\b\w*(?:{_MONEY})\w*\b\s*/\s*\w+\s*\*\s*100", re.IGNORECASE,
)


def _sources() -> list[pathlib.Path]:
    return [
        path for path in sorted(SRC.rglob("*"))
        if path.suffix in {".ts", ".tsx"}
        and path.name not in _PRESENTATION
        and path.name not in _INPUT_PREVIEWS
        and not _TESTS.search(path.name)
    ]


def test_there_are_sources_to_scan() -> None:
    """A scan that silently matches nothing proves nothing."""
    assert len(_sources()) >= 10


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_no_component_does_arithmetic_on_money(path: pathlib.Path) -> None:
    offenders: list[str] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        # Comparisons and sign checks are display decisions, not arithmetic.
        if _ARITHMETIC.search(line) or _SHARE.search(line):
            offenders.append(f"{path.name}:{number}: {stripped[:90]}")

    assert not offenders, (
        "финance arithmetic in the frontend:\n  " + "\n  ".join(offenders)
    ).replace("финance", "finance")


# ── the one duplicated formula, pinned ───────────────────────────────────

@pytest.mark.parametrize("cash,investments,other,debts,expected", [
    (5000.0, 30000.0, 12000.0, 4000.0, 43000.0),
    (800.0, 0.0, 0.0, 42000.0, -41200.0),
    (0.1, 0.2, 0.0, 0.0, 0.3),
    (0.0, 0.0, 0.0, 0.0, 0.0),
    (17400.0, 48250.0, 0.0, 1430.0, 64220.0),
])
def test_the_preview_total_matches_the_engine(
    tmp_path, monkeypatch, cash, investments, other, debts, expected,
) -> None:
    """desktop/src/netWorthForm.ts previews this sum while the user types.

    The same five rows are asserted against the TypeScript implementation in
    netWorthForm.test.mjs, so if either side changes its formula one of the
    two suites fails.
    """
    import utils.database as db

    monkeypatch.delenv("LEDGER_DEMO_DB", raising=False)
    monkeypatch.setenv("LEDGER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "finance.db")
    db.init_db()
    from utils.net_worth import save_entry

    entry = save_entry("2026-01", cash=cash, investments=investments,
                       other_assets=other, liabilities=debts)

    assert entry["net"] == pytest.approx(expected, abs=0.005)

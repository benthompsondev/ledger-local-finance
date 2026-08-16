"""
Counterfactual replay — a month you already lived, with one change applied.

Design contract
───────────────
• Nothing here forecasts. Every figure is arithmetic over transactions that
  were actually imported, so a replay can be wrong about what you *would*
  have done but never about what the numbers *were*.
• No new financial math. The actual and counterfactual totals both come from
  `financial_semantics.cashflow_totals` over the same row set, so a replay
  cannot drift away from what Insights and Home report for that month. The
  only difference between the two runs is the effective amount on the rows
  the change touches.
• Completed months only. A part-month of spending against a whole month of
  pay flatters every figure derived from it, and a replay is not worth
  showing when its baseline is already misleading.
• Spending rows only. Reducing a category must not delete the refund that
  shared its label — that would quietly turn a spending cut into an income
  cut and overstate the gain.

Public API
──────────
    counterfactual_packet(month, target_kind, target_key, reduction_pct, ...)
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Any, Optional

from utils.database import get_connection
from utils.financial_semantics import cashflow_role, cashflow_totals

# A replay needs a month whose income and spending are both fully present.
MAX_TARGETS = 12
# The evidence list is meant to be inspectable, not exhaustive.
MAX_EVIDENCE_ROWS = 60

TARGET_KINDS = ("category", "merchant")


def _conn(conn: Optional[sqlite3.Connection]) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    return get_connection(), True


def _amount(tx: dict) -> float:
    """The effective cash-flow amount, matching what cashflow_totals reads."""
    raw = tx.get("_cashflow_amount")
    if raw is None:
        raw = tx.get("amount") or 0.0
    return abs(float(raw))


def _month_label(month: str) -> str:
    try:
        year, number = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        return month
    names = ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December")
    if not 1 <= number <= 12:
        return month
    return f"{names[number - 1]} {year}"


def _rows_by_month(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Group supported transactions by month.

    Deliberately the same bounded query `monthly_aggregates` uses. A replay
    that read a wider or narrower window than the rest of Insights would
    report an "actual" the user cannot find anywhere else in the app.
    """
    from utils.analysis_period import supported_date_bounds

    grouped: dict[str, list[dict]] = defaultdict(list)
    first_supported, last_supported = supported_date_bounds(conn=conn)
    if first_supported is None or last_supported is None:
        return grouped
    rows = conn.execute(
        "SELECT * FROM transactions "
        "WHERE transaction_date BETWEEN ? AND ? "
        "ORDER BY transaction_date",
        (first_supported.isoformat(), last_supported.isoformat()),
    ).fetchall()
    for row in rows:
        tx = dict(row)
        month = str(tx.get("transaction_date") or "")[:7]
        if month:
            grouped[month].append(tx)
    return grouped


def _completed_months(conn: sqlite3.Connection) -> set[str]:
    """Months the engine already considers complete. One source of truth."""
    from utils.insights import statement_coverage

    coverage = statement_coverage(conn=conn).get(
        "statement_coverage_by_month"
    ) or {}
    return {
        month for month, detail in coverage.items()
        if (detail or {}).get("complete")
    }


def _shared(rows: list[dict], conn: sqlite3.Connection,
            share_view: str) -> list[dict]:
    from utils.shared_expenses import apply_shared_view

    return apply_shared_view(rows, conn, share_view)


def _target_key(tx: dict, kind: str) -> str:
    if kind == "category":
        return str(tx.get("category") or "Uncategorized")
    return str(
        tx.get("merchant_normalized") or tx.get("merchant") or ""
    ).strip()


def _target_label(tx: dict, kind: str) -> str:
    if kind == "category":
        return str(tx.get("category") or "Uncategorized")
    return str(tx.get("merchant") or tx.get("merchant_normalized") or "").strip()


def _matches(tx: dict, kind: str, key: str) -> bool:
    return _target_key(tx, kind) == key


def available_targets(rows: list[dict]) -> list[dict]:
    """What is worth replaying in this month, biggest spend first.

    Only spending rows count. A merchant with one charge is still offered:
    a single large purchase is often exactly the thing worth questioning.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for tx in rows:
        if cashflow_role(tx) != "spending":
            continue
        amount = _amount(tx)
        if amount <= 0:
            continue
        for kind in TARGET_KINDS:
            key = _target_key(tx, kind)
            if not key:
                continue
            slot = buckets.setdefault((kind, key), {
                "kind": kind,
                "key": key,
                "label": _target_label(tx, kind),
                "amount": 0.0,
                "tx_count": 0,
            })
            slot["amount"] += amount
            slot["tx_count"] += 1
    out = sorted(
        buckets.values(), key=lambda item: item["amount"], reverse=True,
    )
    for item in out:
        item["amount"] = round(item["amount"], 2)
    categories = [i for i in out if i["kind"] == "category"][:MAX_TARGETS]
    merchants = [i for i in out if i["kind"] == "merchant"][:MAX_TARGETS]
    return categories + merchants


def apply_reduction(rows: list[dict], kind: str, key: str,
                    reduction_pct: float) -> tuple[list[dict], list[dict]]:
    """Return (replayed rows, matched originals).

    Matched rows keep every field except their effective amount, which is
    scaled. Scaling rather than deleting keeps a partial cut expressible —
    "half the takeout" is a change a person can actually make, and deleting
    rows would only ever model "all of it".
    """
    factor = 1.0 - (float(reduction_pct) / 100.0)
    replayed: list[dict] = []
    matched: list[dict] = []
    for tx in rows:
        if cashflow_role(tx) == "spending" and _matches(tx, kind, key):
            changed = dict(tx)
            changed["_cashflow_amount"] = _amount(tx) * factor
            replayed.append(changed)
            matched.append(tx)
        else:
            replayed.append(tx)
    return replayed, matched


def _totals(rows: list[dict], share_view: str) -> dict[str, float]:
    totals = cashflow_totals(rows, view=share_view)
    return {
        "income": totals["income"],
        "spending": totals["spending"],
        "net": totals["net"],
        "savings_rate": totals["savings_rate"],
    }


def _rank(net_by_month: dict[str, float], month: str, net: float) -> int:
    """1 = best kept month. The replayed month uses the supplied net."""
    values = dict(net_by_month)
    values[month] = net
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    for position, (candidate, _) in enumerate(ordered, start=1):
        if candidate == month:
            return position
    return len(ordered)


def _evidence(matched: list[dict], reduction_pct: float) -> list[dict]:
    rows = sorted(matched, key=lambda tx: _amount(tx), reverse=True)
    factor = 1.0 - (float(reduction_pct) / 100.0)
    return [{
        "date": str(tx.get("transaction_date") or ""),
        "description": str(tx.get("merchant") or tx.get("raw_description") or ""),
        "category": str(tx.get("category") or ""),
        "amount": round(_amount(tx), 2),
        "replayed_amount": round(_amount(tx) * factor, 2),
    } for tx in rows[:MAX_EVIDENCE_ROWS]]


def counterfactual_packet(
    month: str = "",
    target_kind: str = "",
    target_key: str = "",
    reduction_pct: float = 100.0,
    conn: Optional[sqlite3.Connection] = None,
    share_view: str = "personal",
) -> dict[str, Any]:
    """Replay one completed month with one spending change applied.

    Called with no target it returns only what can be replayed, so the same
    action drives the month picker, the target list and the result.
    """
    c, opened = _conn(conn)
    try:
        return _packet(c, month, target_kind, target_key, reduction_pct,
                       share_view)
    finally:
        if opened:
            c.close()


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False, "reason": reason, "months": [], "month": "",
        "month_label": "", "targets": [], "replay": None,
    }


def _packet(conn: sqlite3.Connection, month: str, target_kind: str,
            target_key: str, reduction_pct: float,
            share_view: str) -> dict[str, Any]:
    grouped = _rows_by_month(conn)
    completed = _completed_months(conn)
    replayable = sorted(m for m in grouped if m in completed)
    if not replayable:
        return _unavailable(
            "A replay needs at least one finished month. Import a full month "
            "of statements and it will appear here."
        )

    # Every completed month, costed once, so ranking and the picker agree.
    summaries: list[dict[str, Any]] = []
    net_by_month: dict[str, float] = {}
    shared_rows: dict[str, list[dict]] = {}
    for candidate in replayable:
        rows = _shared(grouped[candidate], conn, share_view)
        shared_rows[candidate] = rows
        totals = _totals(rows, share_view)
        net_by_month[candidate] = totals["net"]
        summaries.append({
            "month": candidate,
            "label": _month_label(candidate),
            "income": totals["income"],
            "spending": totals["spending"],
            "net": totals["net"],
        })

    selected = month if month in shared_rows else replayable[-1]
    rows = shared_rows[selected]
    targets = available_targets(rows)
    packet: dict[str, Any] = {
        "available": True,
        "reason": "",
        "months": summaries,
        "month": selected,
        "month_label": _month_label(selected),
        "targets": targets,
        "replay": None,
    }

    kind = str(target_kind or "").strip()
    key = str(target_key or "").strip()
    if kind not in TARGET_KINDS or not key:
        return packet

    try:
        pct = float(reduction_pct)
    except (TypeError, ValueError):
        pct = float("nan")
    if not math.isfinite(pct) or not 0 < pct <= 100:
        packet["replay"] = {
            "available": False,
            "reason": "Choose a reduction between 1% and 100%.",
        }
        return packet

    replayed, matched = apply_reduction(rows, kind, key, pct)
    if not matched:
        packet["replay"] = {
            "available": False,
            "reason": (
                f"No spending on that in {_month_label(selected)}, so there "
                "is nothing to replay."
            ),
        }
        return packet

    actual = _totals(rows, share_view)
    counterfactual = _totals(replayed, share_view)
    freed = round(actual["spending"] - counterfactual["spending"], 2)
    target = next(
        (t for t in targets if t["kind"] == kind and t["key"] == key),
        {"kind": kind, "key": key, "label": key,
         "amount": round(sum(_amount(tx) for tx in matched), 2),
         "tx_count": len(matched)},
    )
    rank_actual = _rank(net_by_month, selected, actual["net"])
    rank_replayed = _rank(net_by_month, selected, counterfactual["net"])

    packet["replay"] = {
        "available": True,
        "reason": "",
        "target": target,
        "reduction_pct": round(pct, 2),
        "removed_entirely": pct >= 100,
        "actual": actual,
        "counterfactual": counterfactual,
        "freed": freed,
        "net_delta": round(counterfactual["net"] - actual["net"], 2),
        "savings_rate_delta": round(
            counterfactual["savings_rate"] - actual["savings_rate"], 1,
        ),
        "matched_count": len(matched),
        "rank": {
            "actual": rank_actual,
            "counterfactual": rank_replayed,
            "total": len(replayable),
            "improved": rank_replayed < rank_actual,
        },
        "transactions": _evidence(matched, pct),
        "evidence_truncated": len(matched) > MAX_EVIDENCE_ROWS,
        "months": [{
            **summary,
            "replayed_net": (
                counterfactual["net"] if summary["month"] == selected
                else summary["net"]
            ),
            "is_replayed": summary["month"] == selected,
        } for summary in summaries],
    }
    return packet

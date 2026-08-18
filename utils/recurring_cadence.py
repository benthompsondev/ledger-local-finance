"""Work out how often a merchant actually bills, and what to set aside.

The recurring detector groups charges by calendar month and asks for a
merchant in three distinct months. That describes a monthly subscription
well and cannot describe anything else: a quarterly insurance premium or an
annual property-tax bill never appears in three consecutive months, so the
whole app was blind to them. Nothing was reserved, and the money simply
disappeared in whichever month the bill happened to land.

This module reads the actual gaps between a merchant's charges instead. From
those it derives a cadence, the amount to put aside each month, when the next
charge is due, and whether the price has changed.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from statistics import median
from typing import Optional

# Typical gap in days for each cadence, with the range that counts as a
# match. Real billing wanders: "monthly" lands anywhere from the 28th to the
# 3rd, and an annual renewal can drift a fortnight.
CADENCES: tuple[tuple[str, int, int, float], ...] = (
    #  name          low   high   months in one period
    ("weekly",         5,    9,   0.2301),   # 7/30.44
    ("biweekly",      11,   18,   0.4602),   # 14/30.44
    ("monthly",       24,   38,   1.0),
    ("bimonthly",     50,   75,   2.0),
    ("quarterly",     78,  105,   3.0),
    ("semiannual",   160,  200,   6.0),
    ("annual",       330,  400,  12.0),
)

# Two charges are enough to establish a rhythm for a slow bill, because
# waiting for a third annual payment would mean two years of blindness. Fast
# cadences should show more before being trusted.
_MIN_OCCURRENCES = {"weekly": 4, "biweekly": 3}
_DEFAULT_MIN_OCCURRENCES = 2

# A price is "changed" only when it moves by both a real amount and a real
# share, so a utility bill drifting with usage is not reported as a hike.
_PRICE_CHANGE_MIN_ABS = 1.00
_PRICE_CHANGE_MIN_PCT = 0.05

# How far past its due date a bill can drift before it is treated as
# finished. A cancelled policy that keeps reserving money is as wrong as an
# invisible one: it quietly locks away money the user could spend, and it
# never stops on its own. Two full cycles plus a fortnight is late enough to
# be sure while surviving an ordinary billing delay.
_STALE_CYCLES = 2.0
_STALE_GRACE_DAYS = 14


def classify_gap(days: float) -> tuple[str, float]:
    """Return (cadence name, months per period) for a typical gap."""
    for name, low, high, months in CADENCES:
        if low <= days <= high:
            return name, months
    return "irregular", 0.0


def _charges_by_merchant(conn, *, since: Optional[str] = None) -> dict:
    from utils.financial_semantics import (
        sql_type_placeholders, transaction_types_for_role,
    )

    spending = transaction_types_for_role("spending")
    sql = (
        "SELECT merchant_normalized, merchant, raw_description, category, "
        "transaction_date, ABS(amount) AS amount FROM transactions "
        "WHERE transaction_type IN (" + sql_type_placeholders(spending) + ")"
    )
    params: list = list(spending)
    if since:
        sql += " AND transaction_date >= ?"
        params.append(since)
    sql += " ORDER BY transaction_date"

    grouped: dict[str, dict] = {}
    for row in conn.execute(sql, params).fetchall():
        key = str(
            row["merchant_normalized"] or row["merchant"]
            or row["raw_description"] or ""
        ).strip().upper()
        if not key:
            continue
        entry = grouped.setdefault(key, {
            "merchant_normalized": key,
            "merchant": row["merchant"] or row["raw_description"] or key,
            "category": row["category"],
            "charges": [],
        })
        entry["charges"].append(
            (str(row["transaction_date"])[:10], float(row["amount"] or 0))
        )
    return grouped


def detect_price_change(charges: list[tuple[str, float]]) -> Optional[dict]:
    """Find the most recent step change in what this merchant charges.

    Compares what is billed now against what came before it, rather than
    reporting every wobble. An average across a price rise is a figure that
    was never actually charged, which is what the plan used to show.
    """
    if len(charges) < 4:
        return None
    amounts = [amount for _day, amount in charges]
    current = amounts[-1]

    # Walk back to the first charge that still matches today's price.
    index = len(amounts) - 1
    while index > 0 and abs(amounts[index - 1] - current) <= max(
        _PRICE_CHANGE_MIN_ABS, abs(current) * _PRICE_CHANGE_MIN_PCT,
    ):
        index -= 1
    if index == 0:
        return None  # the price has always been what it is now

    previous_values = amounts[:index]
    previous = round(float(median(previous_values)), 2)
    if not previous:
        return None
    delta = current - previous
    if abs(delta) < _PRICE_CHANGE_MIN_ABS:
        return None
    if abs(delta) / abs(previous) < _PRICE_CHANGE_MIN_PCT:
        return None
    # Only report a change that then held; a single odd charge is not a rise.
    if len(amounts) - index < 2:
        return None

    # A price change is a step, not a slope. A utility bill creeping up with
    # usage every month has genuinely got dearer, but nothing happened on
    # any one date, so announcing "it went from X to Y on the 9th of May"
    # would be inventing an event. Insist both sides sit tightly around
    # their own level relative to the size of the jump.
    after_values = amounts[index:]
    spread = max(
        max(abs(value - previous) for value in previous_values),
        max(abs(value - current) for value in after_values),
    )
    if spread > abs(delta) / 2.0:
        return None

    gaps = [
        (date.fromisoformat(charges[i][0])
         - date.fromisoformat(charges[i - 1][0])).days
        for i in range(1, len(charges))
    ]
    typical_gap = median(gaps) if gaps else 30.44
    per_year = 365.25 / typical_gap if typical_gap else 12.0
    return {
        "previous_amount": previous,
        "current_amount": round(current, 2),
        "delta": round(delta, 2),
        "changed_on": charges[index][0],
        "annual_impact": round(delta * per_year, 2),
    }


def merchant_cadences(conn: Optional[sqlite3.Connection] = None,
                      today: Optional[str] = None) -> list[dict]:
    """Every merchant that bills on a recognisable rhythm.

    Includes the slow ones the monthly detector cannot see. Each entry
    carries the cadence, the current amount, what to set aside monthly, when
    the next charge is due, and any price change.
    """
    from utils.database import get_connection

    close = conn is None
    if close:
        conn = get_connection()
    try:
        from utils.analysis_period import supported_latest_date

        anchor_date = (
            date.fromisoformat(str(today)[:10])
            if today else supported_latest_date(conn=conn)
        )
        anchor = anchor_date.isoformat() if anchor_date else ""
        if not anchor:
            return []

        results = []
        for entry in _charges_by_merchant(conn).values():
            charges = sorted(entry["charges"])
            if len(charges) < 2:
                continue
            days = [date.fromisoformat(day) for day, _amount in charges]
            gaps = [
                (days[i] - days[i - 1]).days for i in range(1, len(days))
            ]
            # Same-day repeats (two coffees) are not a billing interval.
            gaps = [gap for gap in gaps if gap > 0]
            if not gaps:
                continue
            typical_gap = float(median(gaps))
            cadence, period_months = classify_gap(typical_gap)
            if cadence == "irregular":
                continue
            needed = _MIN_OCCURRENCES.get(cadence, _DEFAULT_MIN_OCCURRENCES)
            if len(charges) < needed:
                continue

            # Charges consistent enough to call a rhythm: allow a wide drift
            # for slow bills, which are rarely billed to the same day.
            #
            # Judged on the typical deviation rather than the worst one. A
            # missed payment caught up the following month puts one huge gap
            # and one tiny gap in the middle of an otherwise perfect
            # quarterly bill, and rejecting on the worst gap dropped that
            # bill out of the plan entirely.
            tolerance = max(6.0, typical_gap * 0.45)
            deviations = [abs(gap - typical_gap) for gap in gaps]
            within = sum(1 for d in deviations if d <= tolerance)
            if median(deviations) > tolerance:
                continue
            if within / len(deviations) < 0.6:
                continue

            recent = [amount for _day, amount in charges[-3:]]
            current_amount = round(float(median(recent)), 2)
            monthly = (
                round(current_amount / period_months, 2)
                if period_months else 0.0
            )
            last_day = days[-1]
            expected_next = last_day + timedelta(days=round(typical_gap))
            # A bill that stopped arriving has been cancelled, moved, or
            # paid off. Keeping money aside for it forever is money the user
            # cannot see and cannot spend.
            overdue_by = (anchor_date - expected_next).days
            is_active = overdue_by <= (
                typical_gap * (_STALE_CYCLES - 1) + _STALE_GRACE_DAYS
            )
            # Slow bills seen only twice are a guess worth stating quietly.
            if len(charges) >= max(4, needed + 1):
                confidence = "high"
            elif len(charges) >= needed + 1:
                confidence = "medium"
            else:
                confidence = "low"

            results.append({
                "merchant": entry["merchant"],
                "merchant_normalized": entry["merchant_normalized"],
                "category": entry["category"],
                "cadence": cadence,
                "period_months": (
                    int(period_months) if period_months >= 1
                    else round(period_months, 4)
                ),
                "typical_gap_days": round(typical_gap, 1),
                "occurrences": len(charges),
                "est_amount": current_amount,
                "monthly_setaside": monthly,
                "last_seen": charges[-1][0],
                "expected_next": expected_next.isoformat(),
                "days_until_next": (expected_next - anchor_date).days,
                "is_active": is_active,
                "overdue_days": max(0, overdue_by),
                "confidence": confidence,
                # The last few actual charges, so anything showing an
                # expected amount can show what it was drawn from. A figure
                # a person cannot check is a figure they have to take on
                # faith, and this one is a guess about the future.
                "recent_charges": [
                    {"date": day, "amount": round(float(amount), 2)}
                    for day, amount in charges[-3:]
                ],
                "price_change": detect_price_change(charges),
                "setaside_note": _setaside_note(
                    cadence, current_amount, monthly, expected_next,
                ),
            })
        results.sort(key=lambda row: -float(row["monthly_setaside"]))
        return results
    finally:
        if close:
            conn.close()


def _setaside_note(cadence: str, amount: float, monthly: float,
                   expected_next: date) -> str:
    """One plain sentence a person can act on."""
    if cadence == "monthly":
        return f"About ${amount:,.0f} every month."
    import calendar as _cal
    when = f"{_cal.month_name[expected_next.month]} {expected_next.year}"
    every = {
        "weekly": "every week", "biweekly": "every two weeks",
        "bimonthly": "every two months", "quarterly": "every three months",
        "semiannual": "every six months", "annual": "once a year",
    }.get(cadence, cadence)
    return (
        f"${amount:,.0f} {every}. Set aside ${monthly:,.0f} per month. "
        f"Next expected {when}."
    )

"""One thing you are saving for, measured from what you actually kept.

The old Goals tab asked people to type their progress in. That is data entry
dressed as a feature: the number is only ever as true as the last time someone
remembered to update it, and the app already knows the answer. Northstar
computes what was kept in every completed month, and what was kept is exactly
what funds a goal.

So a Money Focus is a target, a month to count from, and optionally what was
already put aside. Everything after that is measured. The pace is the real
pace, the finish date is the real finish date at that pace, and when the
months go backwards the focus goes backwards with them. Nothing here is
encouraging on purpose; it is accurate, which turns out to be the encouraging
part when it is going well and the useful part when it is not.

Deliberately one focus, not a list. A list of eleven goals is a wish board.
"""
from __future__ import annotations

from typing import Optional

# A month either finished or it did not. Only finished months fund the focus,
# because half a month of spending against a whole month of pay is the oldest
# way to make a figure look better than it is.
_MIN_MONTHS_FOR_PACE = 1


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _ensure_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS money_focus ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "name TEXT NOT NULL, "
        "target_amount REAL NOT NULL, "
        "target_date TEXT DEFAULT '', "
        "started_month TEXT NOT NULL, "
        "already_saved REAL DEFAULT 0, "
        "note TEXT DEFAULT '', "
        "updated_at TEXT DEFAULT (datetime('now','localtime')))"
    )


def get_focus(conn=None) -> Optional[dict]:
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        row = c.execute(
            "SELECT name, target_amount, target_date, started_month, "
            "already_saved, note FROM money_focus WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        return {
            "name": str(row[0]),
            "target_amount": round(float(row[1]), 2),
            "target_date": str(row[2] or ""),
            "started_month": str(row[3]),
            "already_saved": round(float(row[4] or 0), 2),
            "note": str(row[5] or ""),
        }
    finally:
        if opened:
            c.close()


def save_focus(name: str, target_amount: float, started_month: str,
               target_date: str = "", already_saved: float = 0.0,
               note: str = "", conn=None) -> dict:
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        label = str(name or "").strip()
        if not label:
            raise ValueError("Give the focus a name you will recognize.")
        amount = round(float(target_amount or 0), 2)
        if amount <= 0:
            raise ValueError("Set a target amount above zero.")
        month = str(started_month or "")[:7]
        if len(month) != 7 or month[4] != "-":
            raise ValueError("Choose the month to start counting from.")
        due = str(target_date or "")[:10]
        if due and (len(due) != 10 or due[4] != "-" or due[7] != "-"):
            raise ValueError("Use a full date, or leave the date empty.")
        c.execute(
            "INSERT INTO money_focus(id, name, target_amount, target_date, "
            "started_month, already_saved, note, updated_at) "
            "VALUES(1, ?, ?, ?, ?, ?, ?, datetime('now','localtime')) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "target_amount=excluded.target_amount, "
            "target_date=excluded.target_date, "
            "started_month=excluded.started_month, "
            "already_saved=excluded.already_saved, note=excluded.note, "
            "updated_at=excluded.updated_at",
            (label, amount, due, month,
             round(float(already_saved or 0), 2), str(note or "")),
        )
        c.commit()
        return get_focus(conn=c) or {}
    finally:
        if opened:
            c.close()


def clear_focus(conn=None) -> None:
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        c.execute("DELETE FROM money_focus WHERE id = 1")
        c.commit()
    finally:
        if opened:
            c.close()


def _add_months(month: str, count: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    total = (year * 12 + (mon - 1)) + count
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _months_between(start: str, end: str) -> int:
    """Whole months from one YYYY-MM to another. Negative if end is earlier."""
    return ((int(end[:4]) * 12 + int(end[5:7]))
            - (int(start[:4]) * 12 + int(start[5:7])))


def money_focus_summary(conn=None) -> dict:
    """The focus, funded by measured months rather than by typing."""
    from utils.insights import monthly_aggregates

    c, opened = _conn(conn)
    try:
        focus = get_focus(conn=c)
        all_months = monthly_aggregates(conn=c) or []
        complete = [m for m in all_months if m.get("complete")]
        latest_month = all_months[-1]["month"] if all_months else ""
        if not focus:
            return {
                "available": False,
                "needs_setup": True,
                "reason": (
                    "Pick one thing worth saving for. Northstar funds it from "
                    "what you actually keep each month, so the progress is "
                    "measured rather than typed in."
                ),
                "suggested_start": (
                    complete[-1]["month"] if complete else latest_month
                ),
                "months_available": len(complete),
                "months": [],
            }

        counted = [m for m in complete
                   if m["month"] >= focus["started_month"]]
        running = focus["already_saved"]
        months = []
        for month in counted:
            kept = round(float(month.get("net") or 0), 2)
            running = round(running + kept, 2)
            months.append({
                "month": month["month"],
                "kept": kept,
                "income": round(float(month.get("income") or 0), 2),
                "running": running,
            })

        saved = running
        target = focus["target_amount"]
        remaining = round(max(0.0, target - saved), 2)
        reached = saved >= target
        contributions = [m["kept"] for m in months]
        typical = (round(sum(contributions) / len(contributions), 2)
                   if contributions else 0.0)

        # The month in progress is shown beside the total, never inside it.
        # Adding a part-month to a run of whole ones is how progress bars
        # learn to lie.
        this_month = None
        if latest_month and (not counted or latest_month != counted[-1]["month"]):
            current = next(
                (m for m in all_months if m["month"] == latest_month), None)
            if current and latest_month >= focus["started_month"]:
                this_month = {
                    "month": latest_month,
                    "kept_so_far": round(float(current.get("net") or 0), 2),
                    "complete": False,
                }

        best = max(months, key=lambda m: m["kept"]) if months else None
        weakest = min(months, key=lambda m: m["kept"]) if months else None

        projection = _project(remaining, typical, len(months), latest_month,
                              reached)
        by_date = _against_the_date(
            focus["target_date"], remaining, typical, latest_month, reached)

        return {
            "available": True,
            "needs_setup": False,
            "focus": focus,
            "saved": saved,
            "target": target,
            "remaining": remaining,
            "reached": reached,
            "progress_pct": (round(min(100.0, max(0.0, saved / target * 100)), 1)
                             if target > 0 else 0.0),
            "months": months,
            "months_counted": len(months),
            "typical_month": typical,
            "best_month": best,
            "weakest_month": weakest,
            "this_month": this_month,
            "projection": projection,
            "by_target_date": by_date,
            "sentence": _sentence(focus, saved, target, remaining, typical,
                                  reached, projection, by_date),
        }
    finally:
        if opened:
            c.close()


def _project(remaining: float, typical: float, months_counted: int,
             latest_month: str, reached: bool) -> dict:
    if reached:
        return {"available": False, "reason": "You have reached the target."}
    if months_counted < _MIN_MONTHS_FOR_PACE:
        return {
            "available": False,
            "reason": ("A finish date appears once one whole month has been "
                       "counted."),
        }
    if typical <= 0:
        return {
            "available": False,
            "reason": ("At your recent rate nothing is being added, so there "
                       "is no honest finish date to show. Spending less than "
                       "you earn in a month is what moves this."),
        }
    months_to_go = int(-(-remaining // typical))  # ceiling division
    return {
        "available": True,
        "months_to_go": months_to_go,
        "finish_month": (_add_months(latest_month, months_to_go)
                         if latest_month else ""),
        "monthly_rate": typical,
        "reason": "",
    }


def _against_the_date(target_date: str, remaining: float, typical: float,
                      latest_month: str, reached: bool) -> dict:
    if not target_date or not latest_month or reached:
        return {"available": False}
    months_left = _months_between(latest_month, target_date[:7])
    if months_left <= 0:
        return {
            "available": True,
            "months_left": 0,
            "needed_monthly": remaining,
            "actual_monthly": typical,
            "on_track": remaining <= 0,
            "shortfall_monthly": round(remaining, 2),
            "past_due": True,
        }
    needed = round(remaining / months_left, 2)
    return {
        "available": True,
        "months_left": months_left,
        "needed_monthly": needed,
        "actual_monthly": typical,
        "on_track": typical >= needed,
        "shortfall_monthly": round(max(0.0, needed - typical), 2),
        "past_due": False,
    }


def _sentence(focus: dict, saved: float, target: float, remaining: float,
              typical: float, reached: bool, projection: dict,
              by_date: dict) -> str:
    name = focus["name"]
    if reached:
        return f"{name} is funded: ${saved:,.0f} against a ${target:,.0f} target."
    money_left = f"${remaining:,.0f}"
    if by_date.get("available") and not by_date.get("past_due"):
        needed = by_date["needed_monthly"]
        if by_date["on_track"]:
            return (f"{money_left} to go. You keep ${typical:,.0f} in a typical "
                    f"month and need ${needed:,.0f}, so the date holds.")
        return (f"{money_left} to go. That date needs ${needed:,.0f} a month "
                f"and you are keeping ${typical:,.0f}, a gap of "
                f"${by_date['shortfall_monthly']:,.0f} a month.")
    if projection.get("available"):
        return (f"{money_left} to go. At ${typical:,.0f} a month that is "
                f"{projection['months_to_go']} more month"
                f"{'' if projection['months_to_go'] == 1 else 's'}, around "
                f"{projection['finish_month']}.")
    return f"{money_left} to go. {projection.get('reason', '')}".strip()

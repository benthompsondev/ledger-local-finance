"""Net worth as a thing you measure once a month, not a thing we guess.

The previous version of this module followed a line: one starting figure the
user typed, plus each completed month's kept amount from then on. It drew
confidently and it was fiction. Kept-per-month comes from imported chequing
statements, so the line could not see a brokerage account move, a car lose
value, or a mortgage come down. The only table that could have held real
readings, `net_worth_snapshots`, was never written by the desktop app at all,
so the "measured" series it drew alongside was empty on every install. What
someone actually saw was their own opening guess, extrapolated.

So this is measurement instead. Once a month, four numbers:

    cash            what is in chequing, savings and cash accounts
    investments     brokerage, retirement, anything with a market value
    other assets    a car, a property, anything else worth counting
    liabilities     cards, loans, a mortgage; entered positive, subtracted

    net worth = cash + investments + other assets - liabilities

One row per month, keyed by month, so recording August twice corrects August
rather than adding a second August. The figures are prefilled from whatever
account balances have been entered, so the monthly update is a review rather
than a data-entry chore, and every prefilled figure can be overridden because
the user can see accounts SignalSpace cannot.

Kept-per-month has not been thrown away. It is still the best explanation of
*why* net worth moved, but it is now compared against two real measurements
instead of standing in for them. The gap between "your statements say you kept
$1,740" and "your net worth rose $2,900" is the interesting part, and it only
means something when both ends are real.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

# \Z, not $. In Python $ also matches just before a trailing newline, so
# "2026-01\n" satisfied a $-anchored pattern and was accepted as January.
_MONTH = re.compile(r"\A\d{4}-(0[1-9]|1[0-2])\Z")

# Recorded in app_migrations once the legacy carry-forward has run. The marker
# is the evidence the migration happened; the presence of a carried-forward
# row is not, because a reading can legitimately be deleted.
_LEGACY_MIGRATION_KEY = "net_worth_entries_from_legacy_v1"

# Which entry field an account's balance belongs in. Mirrors ASSET_KINDS and
# LIABILITY_KINDS in utils.database; kept as an explicit map because the
# grouping the user sees is coarser than the account kinds they set up.
_KIND_FIELD = {
    "chequing": "cash",
    "savings": "cash",
    "cash": "cash",
    "investment": "investments",
    "other_asset": "other_assets",
    "credit_card": "liabilities",
    "loan": "liabilities",
    "mortgage": "liabilities",
    "other_liability": "liabilities",
}
COMPONENTS = ("cash", "investments", "other_assets", "liabilities")
COMPONENT_LABELS = {
    "cash": "Cash",
    "investments": "Investments",
    "other_assets": "Other assets",
    "liabilities": "Debts",
}
# The currency the readings are kept in. SignalSpace has never invented an
# exchange rate anywhere, and a net worth entry is a single figure, so a
# balance in anything else is reported rather than converted or added.
LOCAL_CURRENCY = "CAD"

# Below this a month-over-month move is not worth narrating as a change.
_MATERIAL_MOVE = 50.0


def _conn(conn):
    if conn is not None:
        return conn, False
    from utils.database import get_connection
    return get_connection(), True


def _ensure_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS net_worth_entries ("
        "month TEXT PRIMARY KEY, "
        "cash REAL NOT NULL DEFAULT 0, "
        "investments REAL NOT NULL DEFAULT 0, "
        "other_assets REAL NOT NULL DEFAULT 0, "
        "liabilities REAL NOT NULL DEFAULT 0, "
        "note TEXT DEFAULT '', "
        "created_at TEXT DEFAULT (datetime('now','localtime')), "
        "updated_at TEXT DEFAULT (datetime('now','localtime')))"
    )
    _migrate_earlier_figures(conn)


def _migrate_earlier_figures(conn) -> None:
    """Carry forward anything the retired net-worth paths managed to store.

    Two of them could hold a real figure someone typed: the quick
    assets-and-debts estimate, and the snapshots the old Streamlit page
    wrote. Neither recorded a breakdown, so the unexplained amount lands in
    other assets with a note saying why. The total stays correct and the
    label stays honest, which is the trade that matters.

    Runs exactly once, recorded in app_migrations.

    The first version guarded on the canonical row existing, which is not a
    migration marker: the absence of a reading is a legitimate state, because
    readings can be deleted. Deleting a carried-forward month therefore
    returned True and the next read put it straight back. The inserts also
    rode on whichever connection was open, so a read that opened its own
    connection wrote the rows and then closed without committing, repeating
    the whole thing forever.
    """
    applied = conn.execute(
        "SELECT 1 FROM app_migrations WHERE migration_key=?",
        (_LEGACY_MIGRATION_KEY,),
    ).fetchone()
    if applied:
        return

    # Whether the caller already has work in flight. If they do, this rides
    # inside their transaction and they decide its fate; committing here
    # would commit their half-finished work too.
    caller_mid_transaction = bool(getattr(conn, "in_transaction", False))

    carried = 0
    for table in ("net_worth_estimates", "net_worth_snapshots"):
        try:
            rows = conn.execute(
                "SELECT substr(as_of_date,1,7) AS month, total_assets AS a, "
                f"total_liabilities AS l FROM {table} "
                "WHERE as_of_date IS NOT NULL AND as_of_date != '' "
                "ORDER BY as_of_date"
            ).fetchall()
        except Exception:  # noqa: BLE001 — table absent on older databases
            continue
        for row in rows:
            month = str(row[0] or "")
            if not _MONTH.match(month):
                continue
            # A reading already recorded for that month is a deliberate
            # figure and outranks anything reconstructed from a total.
            cursor = conn.execute(
                "INSERT OR IGNORE INTO net_worth_entries"
                "(month, cash, investments, other_assets, liabilities, note) "
                "VALUES(?, 0, 0, ?, ?, ?)",
                (
                    month,
                    round(float(row[1] or 0), 2),
                    round(float(row[2] or 0), 2),
                    "Carried over from an earlier estimate, which did not "
                    "record a breakdown. Split it up when convenient.",
                ),
            )
            carried += cursor.rowcount if cursor.rowcount > 0 else 0

    conn.execute(
        "INSERT OR IGNORE INTO app_migrations "
        "(migration_key, rows_scanned, rows_changed, details_json) "
        "VALUES (?,?,?,?)",
        (_LEGACY_MIGRATION_KEY, carried, carried, json.dumps({
            "sources": ["net_worth_estimates", "net_worth_snapshots"],
            "unexplained_amount_field": "other_assets",
        })),
    )
    if not caller_mid_transaction:
        # Nothing of the caller's is pending, so this commits only the
        # carry-forward and its marker. Without it a read-only path would
        # roll the whole migration back on close and repeat it forever.
        conn.commit()


def _valid_month(value) -> str:
    """Exactly YYYY-MM, or a refusal.

    Deliberately not ``str(value)[:7]``. Truncating meant "2026-01-garbage"
    silently addressed January, so a malformed month could overwrite or
    delete a real reading instead of being rejected.
    """
    month = str(value or "")
    if not _MONTH.match(month):
        raise ValueError(
            "Choose the month this reading is for, as YYYY-MM."
        )
    return month


def _net(row) -> float:
    return round(
        float(row["cash"]) + float(row["investments"])
        + float(row["other_assets"]) - float(row["liabilities"]),
        2,
    )


def _clean_amount(value, field: str) -> float:
    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        raise ValueError(
            f"{COMPONENT_LABELS[field]} must be a number."
        ) from None
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise ValueError(f"{COMPONENT_LABELS[field]} must be a real number.")
    if amount < 0:
        # Net worth can be negative; a component cannot. A negative debt is
        # someone owing you money, which belongs in other assets, and a
        # negative cash balance is an overdraft, which is a debt. Silently
        # accepting either flips a sign somewhere downstream.
        raise ValueError(
            f"{COMPONENT_LABELS[field]} cannot be negative. An overdraft "
            "belongs in Debts, and money owed to you belongs in Other assets."
        )
    if amount > 1_000_000_000:
        raise ValueError(
            f"{COMPONENT_LABELS[field]} looks like a typo at "
            f"${amount:,.2f}. Check the figure before saving."
        )
    return amount


def save_entry(month: str, *, cash=0, investments=0, other_assets=0,
               liabilities=0, note: str = "", conn=None, today=None) -> dict:
    """Record or correct one month. The month is the key, so this is a
    correction whenever that month already exists."""
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        month = _valid_month(month)
        current_month = (
            today.strftime("%Y-%m") if isinstance(today, date)
            else str(today)[:7] if today
            else date.today().strftime("%Y-%m")
        )
        if month > current_month:
            raise ValueError(
                "Net worth can only be recorded for this month or an earlier "
                "month. Future values would be a projection, not a reading."
            )
        values = {
            "cash": _clean_amount(cash, "cash"),
            "investments": _clean_amount(investments, "investments"),
            "other_assets": _clean_amount(other_assets, "other_assets"),
            "liabilities": _clean_amount(liabilities, "liabilities"),
        }
        c.execute(
            "INSERT INTO net_worth_entries"
            "(month, cash, investments, other_assets, liabilities, note) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(month) DO UPDATE SET cash=excluded.cash, "
            "investments=excluded.investments, "
            "other_assets=excluded.other_assets, "
            "liabilities=excluded.liabilities, note=excluded.note, "
            "updated_at=datetime('now','localtime')",
            (month, values["cash"], values["investments"],
             values["other_assets"], values["liabilities"], str(note or "")),
        )
        c.commit()
        return {"month": month, **values, "net": _net(values),
                "note": str(note or "")}
    finally:
        if opened:
            c.close()


def delete_entry(month: str, conn=None) -> bool:
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        deleted = c.execute(
            "DELETE FROM net_worth_entries WHERE month = ?",
            (_valid_month(month),),
        ).rowcount
        c.commit()
        return bool(deleted)
    finally:
        if opened:
            c.close()


def list_entries(conn=None) -> list[dict]:
    """Every recorded month, oldest first, each with its change on the last."""
    c, opened = _conn(conn)
    try:
        _ensure_table(c)
        rows = c.execute(
            "SELECT month, cash, investments, other_assets, liabilities, note "
            "FROM net_worth_entries ORDER BY month"
        ).fetchall()
        entries: list[dict] = []
        previous: Optional[float] = None
        for row in rows:
            values = {k: round(float(row[k]), 2) for k in COMPONENTS}
            net = _net(values)
            change = None if previous is None else round(net - previous, 2)
            entries.append({
                "month": str(row["month"]),
                **values,
                "net": net,
                "note": str(row["note"] or ""),
                "change": change,
                # A percentage against a negative or zero starting point is
                # not meaningful, so it is simply absent rather than wrong.
                "change_pct": (
                    round(change / previous * 100, 1)
                    if change is not None and previous and previous > 0
                    else None
                ),
            })
            previous = net
        return entries
    finally:
        if opened:
            c.close()


def suggested_entry(month: str = "", conn=None) -> dict:
    """Prefill one month from the account balances already entered.

    A suggestion, never a saved figure. It only knows about accounts with a
    balance snapshot, and it says which ones fed each number so a total that
    looks wrong can be traced without guessing.
    """
    from datetime import date

    from utils.database import get_account_balances

    c, opened = _conn(conn)
    try:
        month = str(month or "")[:7]
        if not _MONTH.match(month):
            month = date.today().strftime("%Y-%m")
        totals = {field: 0.0 for field in COMPONENTS}
        sources: list[dict] = []
        excluded: list[dict] = []
        for balance in get_account_balances(conn=c, latest_only=True):
            kind = str(balance.get("account_kind") or "").lower()
            field = _KIND_FIELD.get(kind)
            if field is None:
                # An account kind this grouping does not know. Guessing where
                # it belongs would put a number in a total nobody chose.
                continue
            amount = round(abs(float(balance.get("balance") or 0)), 2)
            # Blank predates the column being filled in, and those rows are
            # local by definition.
            currency = str(balance.get("currency") or LOCAL_CURRENCY).upper()
            row = {
                "account": str(balance.get("account_name") or ""),
                "kind": kind,
                "field": field,
                "amount": amount,
                "currency": currency,
                "as_of_date": str(balance.get("as_of_date") or ""),
            }
            if currency != LOCAL_CURRENCY:
                # SignalSpace has no exchange rate and invents one nowhere
                # else, so a foreign balance cannot join a local total. It is
                # reported instead, so the figure can be converted by hand
                # rather than quietly disappearing.
                excluded.append(row)
                continue
            totals[field] = round(totals[field] + amount, 2)
            sources.append(row)
        return {
            "month": month,
            **totals,
            "net": _net(totals),
            "sources": sources,
            "has_balances": bool(sources),
            "excluded": excluded,
            "excluded_currencies": sorted({r["currency"] for r in excluded}),
            "local_currency": LOCAL_CURRENCY,
        }
    finally:
        if opened:
            c.close()


def _kept_by_month(conn) -> dict[str, float]:
    """What the imported statements say was kept, per complete month."""
    from utils.insights import monthly_aggregates

    return {
        m["month"]: round(float(m.get("net") or 0), 2)
        for m in monthly_aggregates(conn=conn) if m.get("complete")
    }


def _month_index(month: str) -> int:
    """Months since year zero, so arithmetic on months is just arithmetic."""
    year, number = int(month[:4]), int(month[5:7])
    return year * 12 + (number - 1)


def _shift(month: str, months_back: int) -> str:
    index = _month_index(month) - months_back
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _months_apart(earlier: str, later: str) -> int:
    return _month_index(later) - _month_index(earlier)


def _move(earlier: dict, later: dict) -> dict:
    return {
        "from_month": earlier["month"],
        "to_month": later["month"],
        "amount": round(later["net"] - earlier["net"], 2),
        "from_net": earlier["net"],
        "to_net": later["net"],
        "months_apart": _months_apart(earlier["month"], later["month"]),
    }


def _change_over(by_month: dict[str, dict], latest: dict,
                 months_back: int) -> Optional[dict]:
    """Movement across exactly ``months_back`` calendar months, or nothing.

    Positions in the list are not months. With readings for January,
    February, June and July, three rows back from July is January, which is
    six months. Labelling that "three months" is a claim about the data that
    the data does not support, so a missing target month returns None and the
    screen says the comparison is unavailable rather than showing zero.
    """
    earlier = by_month.get(_shift(latest["month"], months_back))
    if earlier is None:
        return None
    return _move(earlier, latest)


def net_worth_overview(conn=None) -> dict:
    """Everything the Net worth screen needs, from measured entries only."""
    c, opened = _conn(conn)
    try:
        entries = list_entries(conn=c)
        prefill = suggested_entry(conn=c)
        if not entries:
            return {
                "has_entries": False,
                "reason": (
                    "Record what you are worth this month and SignalSpace will "
                    "track it from here. Four figures, once a month."
                ),
                "entries": [],
                "prefill": prefill,
                "months_recorded": 0,
            }

        latest = entries[-1]
        by_month = {e["month"]: e for e in entries}
        changes = [e["change"] for e in entries if e["change"] is not None]
        moves = [e for e in entries if e["change"] is not None]
        kept = _kept_by_month(c)

        # What the statements say against what was actually measured. Only
        # for months with an entry on both ends, so both numbers are real.
        # Only adjacent readings. kept[month] is one month's worth of
        # statement activity, so subtracting it from a six-month measured
        # change would call five months of movement "unexplained".
        explained = []
        for index in range(1, len(entries)):
            earlier, current = entries[index - 1], entries[index]
            apart = _months_apart(earlier["month"], current["month"])
            if apart != 1 or current["month"] not in kept:
                continue
            measured = current["change"]
            statement = kept[current["month"]]
            explained.append({
                "month": current["month"],
                "measured": measured,
                "kept": statement,
                "unexplained": round(measured - statement, 2),
                "months_apart": apart,
            })

        # The two readings the component table actually compares. They are
        # not necessarily a month apart, so the screen has to be able to say
        # which months they are.
        component_moves = None
        component_from = component_to = ""
        component_apart = None
        if len(entries) >= 2:
            previous = entries[-2]
            component_from = previous["month"]
            component_to = latest["month"]
            component_apart = _months_apart(component_from, component_to)
            component_moves = [
                {
                    "field": field,
                    "label": COMPONENT_LABELS[field],
                    "now": latest[field],
                    "before": previous[field],
                    "change": round(latest[field] - previous[field], 2),
                }
                for field in COMPONENTS
            ]

        return {
            "has_entries": True,
            "reason": "",
            "entries": entries,
            "prefill": prefill,
            "current": latest,
            "months_recorded": len(entries),
            # Each of these is that calendar period or nothing at all.
            "month_over_month": _change_over(by_month, latest, 1),
            "three_month": _change_over(by_month, latest, 3),
            "twelve_month": _change_over(by_month, latest, 12),
            # Whatever two readings do exist, whether or not they are
            # adjacent, so a sparse ledger still shows its movement — under
            # a label that says what it is comparing.
            "since_last_reading": (
                _move(entries[-2], latest) if len(entries) >= 2 else None
            ),
            "since_first": {
                **_move(entries[0], latest),
                "reading_count": len(entries),
            },
            "average_move": (
                round(sum(changes) / len(changes), 2) if changes else None
            ),
            "best_month": (
                max(moves, key=lambda e: e["change"]) if moves else None
            ),
            "worst_month": (
                min(moves, key=lambda e: e["change"]) if moves else None
            ),
            "component_moves": component_moves,
            "component_from_month": component_from,
            "component_to_month": component_to,
            "component_months_apart": component_apart,
            "explained": explained,
            "material_move": _MATERIAL_MOVE,
        }
    finally:
        if opened:
            c.close()

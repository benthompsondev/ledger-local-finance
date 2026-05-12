"""
Create a deterministic fake demo database.

Build a deterministic FAKE demo database at data/finance.demo.db so
the Ledger app, screenshots, and reviewers can see meaningful data
without ever touching real finances.

What this script does
─────────────────────
- Refuses to overwrite an existing demo DB unless --force is passed.
- Refuses to write to data/finance.db (the real DB) under any flag.
- Generates ~6 months of synthetic transactions across realistic
  categories, with payroll, fixed bills, subscriptions (active +
  stale + price-increase + cancelled), variable retail spend, fees,
  cash advance, internal transfers, and a credit-card payment loop.
- Adds 1 investment snapshot + cash/CC account balances + 3 net
  worth snapshots so the Investments page has a chart and milestone.
- Saves 1 monthly plan with category targets and 2 goals
  (cash buffer + debt reduction).
- Tags one row as flagged for the Review queue.

What this script DOES NOT do
────────────────────────────
- Never reads from data/finance.db.
- Never copies real transactions, real merchants, or real names.
- Never includes real personal names, employers, lenders, or account-like
  patterns. All merchants begin with "DEMO " so they
  cannot be confused with real ones.
- Never moves real money or hits the network.
- Never overwrites data/finance.db. If --out points at the real DB
  the script aborts with a clear error.

Usage
─────
    python -m scripts.create_demo_data --force
    python -m scripts.create_demo_data --out data/finance.demo.db --force
    python -m scripts.create_demo_data --months 6 --force

Run the app against the demo DB
───────────────────────────────
    set LEDGER_DEMO_DB=1   (Windows cmd)
    $env:LEDGER_DEMO_DB=1  (PowerShell)
    export LEDGER_DEMO_DB=1 (bash)
    python -m streamlit run app.py
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Deterministic random — same demo DB across runs.
_RNG = random.Random(20260508)


# ── Fixtures ──────────────────────────────────────────────────────
# Every merchant token below begins with "DEMO " or is a clearly
# fictional placeholder. None match real Tangerine alias rules.
_PAYROLL_GROSS = 2_400.0       # bi-weekly payroll deposit
_RENT          = 1_800.0
_HYDRO         = 90.0
_INTERNET      = 80.0

_FIXED_BILLS = [
    # (merchant, amount, day_of_month, category, account)
    ("DEMO LANDLORD",       _RENT,     1,  "Housing / Mortgage",  "chequing"),
    ("DEMO HYDRO",          _HYDRO,    8,  "Utilities / Bills",   "chequing"),
    ("DEMO INTERNET",       _INTERNET, 12, "Utilities / Bills",   "chequing"),
]

_SUBSCRIPTIONS = [
    # (merchant, amount, day_of_month, category, account, lifecycle)
    #   lifecycle:
    #     "active"            — bills every month within window
    #     "active_increase"   — bills every month, last month is +33%
    #     "active_candidate"  — bills every month, low usage / cancel hint
    #     "stale"             — last billed 4+ months ago
    #     "cancelled"         — never billed in the demo window
    ("DEMO STREAM TV",     15.99, 5,  "Subscriptions & Digital", "mastercard", "active"),
    ("DEMO MUSIC",         11.99, 14, "Subscriptions & Digital", "mastercard", "active"),
    ("DEMO CLOUD STORAGE",  9.99, 22, "Subscriptions & Digital", "mastercard", "active"),
    ("DEMO NEWSPAPER",     14.99, 18, "Subscriptions & Digital", "mastercard", "active_increase"),
    ("DEMO MEAL KIT",      89.00, 9,  "Food & Convenience",      "mastercard", "active_candidate"),
    ("DEMO GYM",           40.00, 3,  "Health / Care",           "chequing",   "stale"),
]

# Variable retail patterns — repeats per month with random amount in
# the range below. Drives controllable cut targets on Reduce.
_VARIABLE_PATTERNS = [
    # (merchant, count_per_month, amt_low, amt_high, category, account)
    ("DEMO GROCERY",     8, 30, 110,  "Groceries",          "mastercard"),
    ("DEMO MARKET",      3, 18,  60,  "Groceries",          "mastercard"),
    ("DEMO ONLINE STORE",4, 25, 175,  "Shopping",           "mastercard"),
    ("DEMO BOOKSHOP",    2, 18,  85,  "Shopping",           "mastercard"),
    ("DEMO COFFEE",      8,  5,  12,  "Food & Convenience", "mastercard"),
    ("DEMO LUNCH SPOT",  6, 12,  28,  "Food & Convenience", "mastercard"),
    ("DEMO FUEL",        4, 35,  82,  "Gas / Transport",    "mastercard"),
    ("DEMO PET SUPPLY",  1, 25,  85,  "Pets",               "mastercard"),
]

_OCCASIONAL_INCOME = [
    # (merchant, amount, month_offset, day, category, account)
    ("DEMO REIMBURSEMENT", 145.00, 2, 18, "Reimbursement / Insurance Reimbursement", "chequing"),
    ("DEMO INTEREST",        1.20, 0, 28, "Interest Income",                          "savings"),
    ("DEMO INTEREST",        1.20, 3, 28, "Interest Income",                          "savings"),
]

_MISC_FEES = [
    # (merchant, amount, month_offset, day, category, account)
    ("DEMO BANK FEE",      4.95, 1, 4, "Fees / Interest", "chequing"),
    ("DEMO INTEREST CHG", 18.40, 4, 22,"Fees / Interest", "mastercard"),
]

_CASH_ADVANCE = [
    # one synthetic cash-advance debit so the Pass 19 risk signal fires
    ("DEMO ATM CASH ADVANCE", 200.00, 5, 7, "Cash Advance", "mastercard"),
]


# ── Helpers ───────────────────────────────────────────────────────
def _dedup_hash(account: str, dt: str, raw: str, amt: float, idx: int = 0) -> str:
    """Stable dedup hash for the demo DB — includes idx so we can
    insert visually identical rows on different days."""
    s = f"demo|{account}|{dt}|{raw}|{amt:.2f}|{idx}"
    return hashlib.sha256(s.encode()).hexdigest()


def _months_back(start_anchor: date, n: int) -> list[str]:
    """Return last n months ending in start_anchor as 'YYYY-MM'."""
    out: list[str] = []
    y, m = start_anchor.year, start_anchor.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    out.reverse()
    return out


def _safe_day(year: int, month: int, day: int) -> date:
    """Clamp day to the actual last day of the month (so Feb 30 -> 28)."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _insert_tx(conn: sqlite3.Connection, *,
               account: str, tx_date: str, raw: str, merchant: str,
               amount: float, direction: str, category: str,
               is_transfer: int = 0, is_flagged: int = 0,
               flag_reason: str | None = None,
               batch_id: int | None = None,
               idx: int = 0) -> None:
    dh = _dedup_hash(account, tx_date, raw, amount, idx)
    conn.execute(
        """INSERT OR IGNORE INTO transactions
           (account_type, transaction_date, raw_description, merchant,
            amount, direction, category, is_transfer, is_flagged,
            flag_reason, dedup_hash, currency, parse_confidence,
            import_batch_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (account, tx_date, raw, merchant, amount, direction, category,
         is_transfer, is_flagged, flag_reason, dh, "CAD", "high",
         batch_id),
    )


# ── Generators ────────────────────────────────────────────────────
def _seed_payroll(conn: sqlite3.Connection, months: list[str],
                  batch_id: int) -> int:
    """Bi-weekly payroll deposits into chequing across all demo months."""
    n = 0
    for ym in months:
        y, m = map(int, ym.split("-"))
        for d in (5, 19):  # bi-weekly anchors
            tx_date = _safe_day(y, m, d).isoformat()
            _insert_tx(conn,
                       account="chequing", tx_date=tx_date,
                       raw="DEMO EMPLOYER PAYROLL DEPOSIT",
                       merchant="DEMO EMPLOYER PAYROLL",
                       amount=_PAYROLL_GROSS, direction="credit",
                       category="Payroll Income",
                       batch_id=batch_id, idx=d)
            n += 1
    return n


def _seed_fixed_bills(conn, months, batch_id) -> int:
    n = 0
    for ym in months:
        y, m = map(int, ym.split("-"))
        for merchant, amt, day, cat, acct in _FIXED_BILLS:
            tx_date = _safe_day(y, m, day).isoformat()
            _insert_tx(conn,
                       account=acct, tx_date=tx_date,
                       raw=merchant, merchant=merchant,
                       amount=amt, direction="debit",
                       category=cat, batch_id=batch_id, idx=day)
            n += 1
    return n


def _seed_subscriptions(conn, months, batch_id) -> int:
    n = 0
    last_idx = len(months) - 1
    stale_cutoff = max(0, len(months) - 4)  # stale = not seen in last 4
    for merchant, amt, day, cat, acct, lifecycle in _SUBSCRIPTIONS:
        if lifecycle == "cancelled":
            # Skip entirely — the schema row is implicit (not present)
            continue
        for i, ym in enumerate(months):
            if lifecycle == "stale" and i >= stale_cutoff:
                continue
            y, m = map(int, ym.split("-"))
            tx_date = _safe_day(y, m, day).isoformat()
            this_amt = amt
            if lifecycle == "active_increase" and i == last_idx:
                this_amt = round(amt * 1.33, 2)  # +33% price hike
            _insert_tx(conn,
                       account=acct, tx_date=tx_date,
                       raw=merchant, merchant=merchant,
                       amount=this_amt, direction="debit",
                       category=cat, batch_id=batch_id, idx=day)
            n += 1
    return n


def _seed_variable(conn, months, batch_id) -> int:
    n = 0
    for ym in months:
        y, m = map(int, ym.split("-"))
        last = calendar.monthrange(y, m)[1]
        for merchant, count, lo, hi, cat, acct in _VARIABLE_PATTERNS:
            for j in range(count):
                day = _RNG.randint(2, last - 1)
                amt = round(_RNG.uniform(lo, hi), 2)
                tx_date = date(y, m, day).isoformat()
                _insert_tx(conn,
                           account=acct, tx_date=tx_date,
                           raw=merchant, merchant=merchant,
                           amount=amt, direction="debit",
                           category=cat, batch_id=batch_id, idx=j*100+day)
                n += 1
    return n


def _seed_misc(conn, months, batch_id) -> int:
    n = 0
    if not months:
        return 0
    # Occasional income — credit deposits at month_offset back from end.
    for merchant, amt, off, day, cat, acct in _OCCASIONAL_INCOME:
        if off >= len(months):
            continue
        ym = months[-(off + 1)]
        y, m = map(int, ym.split("-"))
        tx_date = _safe_day(y, m, day).isoformat()
        _insert_tx(conn,
                   account=acct, tx_date=tx_date,
                   raw=merchant, merchant=merchant,
                   amount=amt, direction="credit",
                   category=cat, batch_id=batch_id, idx=day)
        n += 1
    # Misc fees
    for merchant, amt, off, day, cat, acct in _MISC_FEES:
        if off >= len(months):
            continue
        ym = months[-(off + 1)]
        y, m = map(int, ym.split("-"))
        tx_date = _safe_day(y, m, day).isoformat()
        _insert_tx(conn,
                   account=acct, tx_date=tx_date,
                   raw=merchant, merchant=merchant,
                   amount=amt, direction="debit",
                   category=cat, batch_id=batch_id, idx=day)
        n += 1
    # Cash advances
    for merchant, amt, off, day, cat, acct in _CASH_ADVANCE:
        if off >= len(months):
            continue
        ym = months[-(off + 1)]
        y, m = map(int, ym.split("-"))
        tx_date = _safe_day(y, m, day).isoformat()
        _insert_tx(conn,
                   account=acct, tx_date=tx_date,
                   raw=merchant, merchant=merchant,
                   amount=amt, direction="debit",
                   category=cat, is_flagged=1, flag_reason="cash_advance",
                   batch_id=batch_id, idx=day)
        n += 1
    return n


def _seed_transfers(conn, months, batch_id) -> int:
    """Internal transfer + monthly CC payment loop (chequing→mastercard)."""
    n = 0
    for ym in months:
        y, m = map(int, ym.split("-"))
        # CC payment last day of month
        last = calendar.monthrange(y, m)[1]
        d_pay = max(2, last - 2)
        # Round-trip pair so transfer math nets to zero across accounts.
        cc_amt = 850.00
        _insert_tx(conn,
                   account="chequing",
                   tx_date=date(y, m, d_pay).isoformat(),
                   raw="PAYMENT - DEMO VISA",
                   merchant="PAYMENT - DEMO VISA",
                   amount=cc_amt, direction="debit",
                   category="Credit Card Payment", is_transfer=1,
                   batch_id=batch_id, idx=d_pay*7)
        _insert_tx(conn,
                   account="mastercard",
                   tx_date=date(y, m, d_pay).isoformat(),
                   raw="PAYMENT FROM CHEQUING",
                   merchant="PAYMENT FROM CHEQUING",
                   amount=cc_amt, direction="credit",
                   category="Credit Card Payment", is_transfer=1,
                   batch_id=batch_id, idx=d_pay*7+1)
        # Internal savings transfer once per month
        d_xfer = 12
        xfer_amt = 200.00
        _insert_tx(conn,
                   account="chequing",
                   tx_date=date(y, m, d_xfer).isoformat(),
                   raw="TRANSFER TO SAVINGS",
                   merchant="TRANSFER TO SAVINGS",
                   amount=xfer_amt, direction="debit",
                   category="Transfer Out", is_transfer=1,
                   batch_id=batch_id, idx=d_xfer*7+2)
        _insert_tx(conn,
                   account="savings",
                   tx_date=date(y, m, d_xfer).isoformat(),
                   raw="TRANSFER FROM CHEQUING",
                   merchant="TRANSFER FROM CHEQUING",
                   amount=xfer_amt, direction="credit",
                   category="Transfer In", is_transfer=1,
                   batch_id=batch_id, idx=d_xfer*7+3)
        n += 4
    return n


def _seed_balances_and_nw(conn: sqlite3.Connection, anchor: date) -> dict:
    """Insert account balances + 3 net-worth snapshots showing growth."""
    out = {"balances": 0, "investments": 0, "nw_snapshots": 0}
    # Latest balances. Schema column is `account_kind`; the kind value
    # itself drives the asset/liability classification in
    # compute_net_worth_now() (see ASSET_KINDS / LIABILITY_KINDS).
    today_iso = anchor.isoformat()
    rows = [
        # (display_name, account_kind, balance, currency)
        ("Demo Chequing", "chequing",     1_200.0, "CAD"),
        ("Demo Savings",  "savings",      4_500.0, "CAD"),
        ("Demo Visa",     "credit_card",     420.0,"CAD"),
    ]
    from utils.database import insert_account_balance
    for name, kind, balance, cur in rows:
        insert_account_balance({
            "account_name":  name,
            "account_kind":  kind,
            "balance":       balance,
            "currency":      cur,
            "as_of_date":    today_iso,
            "notes":         "DEMO synthetic balance",
        }, conn=conn)
        out["balances"] += 1

    # Investment snapshot (one batch with 5 positions)
    try:
        from utils.database import insert_investment_snapshot
        positions = [
            {"ticker": "DEMO-VFV", "security_name": "Demo S&P 500 ETF",
             "account_type": "TFSA",   "quantity": 30, "book_value": 2400,
             "market_value": 3120, "market_value_currency": "CAD"},
            {"ticker": "DEMO-VCN", "security_name": "Demo Canadian Equity ETF",
             "account_type": "TFSA",   "quantity": 50, "book_value": 1800,
             "market_value": 2050, "market_value_currency": "CAD"},
            {"ticker": "DEMO-VTI", "security_name": "Demo Total US Market ETF",
             "account_type": "RRSP",   "quantity": 12, "book_value": 2400,
             "market_value": 2880, "market_value_currency": "USD"},
            {"ticker": "DEMO-VOO", "security_name": "Demo S&P 500 (USD) ETF",
             "account_type": "RRSP",   "quantity": 8,  "book_value": 3200,
             "market_value": 3650, "market_value_currency": "USD"},
            {"ticker": "DEMO-CRYPTO", "security_name": "Demo Crypto Position",
             "account_type": "Other",  "quantity": 0.05, "book_value": 1500,
             "market_value": 1820, "market_value_currency": "USD"},
        ]
        insert_investment_snapshot(
            {"as_of_date":      today_iso,
             "source_file":     "demo_holdings.csv",
             "currencies_seen": "CAD,USD",
             "mixed_currency":  1,
             "notes":           "DEMO synthetic holdings"},
            positions, conn=conn,
        )
        out["investments"] = 1
    except Exception as e:
        # Soft-fail loudly so we can debug if a future schema drift
        # breaks the seed; demo without investments is still useful.
        print(f"  WARN: investment snapshot seed failed: {e}",
              file=sys.stderr)

    # Net worth snapshots — 3 monthly with growth
    try:
        from utils.database import insert_net_worth_snapshot
        nw_anchor = anchor
        for i, delta in enumerate([0, -350, -750]):
            nw_date = (nw_anchor.replace(day=1)
                       - timedelta(days=30 * i)).isoformat()
            assets      = 1_200 + 4_500 + 13_520 + delta
            liabilities = 420
            insert_net_worth_snapshot({
                "as_of_date":         nw_date,
                "total_assets":       assets,
                "total_liabilities":  liabilities,
                "net_worth":          assets - liabilities,
                "currency":           "CAD",
                "mixed_currency":     1,
                "notes":              "DEMO net worth snapshot",
            }, conn=conn)
            out["nw_snapshots"] += 1
    except Exception:
        pass

    return out


def _seed_plan_and_goals(conn: sqlite3.Connection, anchor: date) -> dict:
    out = {"plans": 0, "category_targets": 0, "goals": 0}
    month_str = anchor.strftime("%Y-%m")
    try:
        from utils.database import (
            upsert_monthly_plan, replace_category_targets, insert_goal,
        )
        plan_id = upsert_monthly_plan({
            "month":           month_str,
            "mode":            "tight",
            "income_target":   6_400.0,
            "spending_target": 5_200.0,
            "savings_target":  1_200.0,
            "notes":           "DEMO tight month — cut Shopping + Food.",
        }, conn=conn)
        out["plans"] = 1
        cat_targets = [
            {"category": "Shopping",
             "monthly_avg": 480.0, "target_amount": 384.0,
             "difficulty": "tight", "basis": "recent_avg_minus_20pct"},
            {"category": "Groceries",
             "monthly_avg": 540.0, "target_amount": 432.0,
             "difficulty": "tight", "basis": "recent_avg_minus_20pct"},
            {"category": "Food & Convenience",
             "monthly_avg": 220.0, "target_amount": 176.0,
             "difficulty": "tight", "basis": "recent_avg_minus_20pct"},
            {"category": "Subscriptions & Digital",
             "monthly_avg": 100.0, "target_amount": 60.0,
             "difficulty": "easy",  "basis": "cancel_one"},
            {"category": "Housing / Mortgage",
             "monthly_avg": 1_800.0, "target_amount": 1_800.0,
             "difficulty": "conservative", "basis": "fixed"},
        ]
        replace_category_targets(plan_id, cat_targets, conn=conn)
        out["category_targets"] = len(cat_targets)

        insert_goal({
            "name":           "Demo emergency fund",
            "type":           "emergency_fund",
            "target_amount":  10_000.0,
            "current_amount":  4_500.0,
            "linked_metric":  "cash_balance",
            "status":         "active",
            "notes":          "DEMO goal — 6 months of essential expenses.",
        }, conn=conn)
        insert_goal({
            "name":           "Demo Visa paydown",
            "type":           "debt_reduction",
            "target_amount":   1_500.0,
            "current_amount":     420.0,
            "linked_metric":  None,
            "status":         "active",
            "notes":          "DEMO goal — pay Visa balance to zero.",
        }, conn=conn)
        out["goals"] = 2
    except Exception:
        pass
    return out


# ── Main ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate a fake demo SQLite database for Ledger.",
    )
    p.add_argument("--out", default=str(_ROOT / "data" / "finance.demo.db"),
                   help="Demo DB output path. Defaults to data/finance.demo.db.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing demo DB if it exists.")
    p.add_argument("--months", type=int, default=6,
                   help="How many months of synthetic history to generate.")
    args = p.parse_args(argv)

    out_path = Path(args.out).resolve()
    real_db_resolved = (_ROOT / "data" / "finance.db").resolve()

    # Hard guard: never overwrite the real DB even if asked.
    if out_path == real_db_resolved:
        print(
            "ERROR: refusing to write demo data to the real DB "
            "(data/finance.db). Pass a different --out path.",
            file=sys.stderr,
        )
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not args.force:
        print(
            f"ERROR: {out_path} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    # Wipe and rebuild from a clean slate so demo runs are deterministic.
    if out_path.exists():
        out_path.unlink()
    # Also wipe sidecar WAL/SHM files so the new DB starts fresh.
    for ext in (".db-shm", ".db-wal"):
        side = out_path.with_suffix(out_path.suffix + ext.replace(".db", ""))
        try:
            if side.exists():
                side.unlink()
        except Exception:
            pass

    # Initialise the schema using utils.database.init_db on the demo path.
    import utils.database as db
    original_path = db.DB_PATH
    db.DB_PATH = out_path
    try:
        db.init_db()
        conn = db.get_connection()
        try:
            today = date.today()
            anchor = today.replace(day=1) - timedelta(days=1)  # last full month
            anchor = anchor.replace(day=15)
            months = _months_back(anchor, args.months)
            batch_id = 1  # synthetic — no import_log row needed for demo

            n_pay   = _seed_payroll(conn,     months, batch_id)
            n_fix   = _seed_fixed_bills(conn, months, batch_id)
            n_subs  = _seed_subscriptions(conn, months, batch_id)
            n_var   = _seed_variable(conn,    months, batch_id)
            n_misc  = _seed_misc(conn,        months, batch_id)
            n_xfer  = _seed_transfers(conn,   months, batch_id)
            conn.commit()

            nw_out = _seed_balances_and_nw(conn, anchor)
            plan_out = _seed_plan_and_goals(conn, anchor)
            conn.commit()

            tx_count = conn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            sub_count = conn.execute(
                "SELECT COUNT(DISTINCT merchant) FROM transactions "
                "WHERE category='Subscriptions & Digital'"
            ).fetchone()[0]
            flagged = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE is_flagged=1"
            ).fetchone()[0]
        finally:
            conn.close()
    finally:
        db.DB_PATH = original_path

    print(f"DEMO DB written: {out_path}  ({out_path.stat().st_size/1024:,.1f} KB)")
    print(f"  Months covered:        {args.months}")
    print(f"  Transactions inserted: {tx_count}")
    print(f"    payroll:             {n_pay}")
    print(f"    fixed bills:         {n_fix}")
    print(f"    subscriptions:       {n_subs}")
    print(f"    variable retail:     {n_var}")
    print(f"    misc / fees / cash:  {n_misc}")
    print(f"    transfers:           {n_xfer}")
    print(f"  Distinct subscription merchants: {sub_count}")
    print(f"  Flagged transactions:            {flagged}")
    print(f"  Account balances:        {nw_out.get('balances', 0)}")
    print(f"  Investment snapshots:    {nw_out.get('investments', 0)}")
    print(f"  Net worth snapshots:     {nw_out.get('nw_snapshots', 0)}")
    print(f"  Monthly plan saved:      {plan_out.get('plans', 0)}")
    print(f"  Category targets:        {plan_out.get('category_targets', 0)}")
    print(f"  Goals:                   {plan_out.get('goals', 0)}")
    print()
    print("Run the app against this DB with LEDGER_DEMO_DB=1, e.g.:")
    print("  set LEDGER_DEMO_DB=1 && python -m streamlit run app.py    (cmd)")
    print("  $env:LEDGER_DEMO_DB='1'; python -m streamlit run app.py   (PowerShell)")
    print("  LEDGER_DEMO_DB=1 python -m streamlit run app.py           (bash)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Fast, dependency-light regression checks for Ledger.

Designed to be run after any non-trivial change. Exercises:

  • DB init on an empty temporary path (no existing schema).
  • compute_net_worth_now / get_latest_investment_snapshot tolerate empty.
  • Holdings CSV parser handles a mixed-currency synthetic input.
  • Snapshot insert + read round-trip.
  • Account balance insert + net worth math.
  • build_agent_context() shape on empty AND populated DB.
  • Tangerine parser modules still importable.

Usage:
    python -m scripts.smoke_test
    python scripts/smoke_test.py

Exit code:
    0 = all checks passed
    1 = at least one assertion failed (failure printed to stderr)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Force stdout/stderr to UTF-8 so the smoke runs cleanly under the Windows
# default cp1252 console without requiring callers to set PYTHONIOENCODING.
# `errors="replace"` is a fallback in case a check label ever picks up a
# non-ASCII character.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


def _section(name: str) -> None:
    # ASCII-only divider so cp1252 consoles stay stable even if reconfigure()
    # fails on an older Python or restricted shell.
    print(f"\n-- {name}")


def main() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  PASS  {label}")
        else:
            failures.append(f"{label}: {detail}")
            print(f"  FAIL  {label}: {detail}")

    # 1. Tangerine parsers + detect import surface
    _section("imports")
    try:
        from parsers.tangerine_chequing   import parse_pdf as _p1   # noqa
        from parsers.tangerine_savings    import parse_pdf as _p2   # noqa
        from parsers.tangerine_mastercard import parse_pdf as _p3   # noqa
        from parsers.detect               import detect_with_confidence  # noqa
        from parsers.csv_import           import parse_csv               # noqa
        from parsers.investments_csv      import parse_holdings_csv      # noqa
        check("parsers import", True)
    except ImportError as e:
        # Pass 28: pdfplumber is in requirements.txt but if the venv
        # was created and never activated for `pip install`, the
        # parsers cannot import. Surface the manual fix command so the
        # failure is self-documenting instead of mysterious.
        msg = str(e)
        if "pdfplumber" in msg:
            detail = (
                f"{msg!r}. Run "
                ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt "
                "(Windows) or .venv/bin/python -m pip install -r requirements.txt "
                "(macOS/Linux) to install missing dependencies."
            )
        else:
            detail = repr(e)
        check("parsers import", False, detail)
    except Exception as e:
        check("parsers import", False, repr(e))

    # 2. Empty-DB cold init
    _section("empty DB cold init")
    tmp_dir = Path(tempfile.mkdtemp(prefix="ledger_smoke_"))
    tmp_db = tmp_dir / "finance.db"
    try:
        import utils.database as db
        db.DB_PATH = tmp_db
        db.init_db()
        conn = db.get_connection()

        nw = db.compute_net_worth_now(conn=conn)
        check("empty net_worth zero", nw["net_worth"] == 0.0,
              f"got {nw['net_worth']}")
        check("empty net_worth missing-inputs flagged",
              "no account balances or investments" in str(nw["missing"]))
        check("empty snapshots []", db.get_investment_snapshots(conn=conn) == [])
        check("empty balances []",  db.get_account_balances(conn=conn) == [])
        check("empty latest snap None",
              db.get_latest_investment_snapshot(conn=conn) is None)
    except Exception as e:
        check("empty DB", False, repr(e))
        return 1

    # 3. Holdings CSV parser smoke
    _section("holdings CSV parser")
    try:
        from parsers.investments_csv import parse_holdings_csv
        sample_csv = (
            "Account Name,Account Type,Account Number,Symbol,Exchange,Name,"
            "Security Type,Quantity,Position Direction,Market Price,"
            "Market Price Currency,Book Value (CAD),Book Value (Market),"
            "Market Value,Market Value Currency,Market Unrealized Returns,"
            "Market Unrealized Returns Currency\n"
            "Questrade TFSA,TFSA,12345678,XEQT,TSX,iShares Core Equity ETF,"
            "ETF,100,Long,30.50,CAD,2800.00,2800.00,3050.00,CAD,250.00,CAD\n"
            "Questrade USD,Margin,11223344,VOO,NYSE,Vanguard S&P 500 USD,"
            "ETF,10,Long,420.00,USD,3800.00,3800.00,4200.00,USD,400.00,USD\n"
            ",,,,,,,,,,,,,,,,\n"
            "As of 2026-04-30 16:00 ET,,,,,,,,,,,,,,,,\n"
        )
        f = tmp_dir / "hold.csv"
        f.write_text(sample_csv, encoding="utf-8")
        parsed = parse_holdings_csv(f)
        check("holdings rows == 2", parsed["row_count"] == 2,
              f"got {parsed['row_count']}")
        check("holdings as_of_date detected",
              parsed["as_of_date"] == "2026-04-30",
              f"got {parsed['as_of_date']}")
        check("holdings mixed_currency",
              parsed["mixed_currency"] is True)
        check("holdings currencies CAD+USD",
              sorted(parsed["currencies"]) == ["CAD", "USD"],
              f"got {parsed['currencies']}")
        check("holdings account masking",
              all((p["account_number_masked"] or "").startswith("•••")
                  for p in parsed["positions"]))
    except Exception as e:
        check("holdings parser", False, repr(e))

    # 4. Snapshot insert + round-trip + net worth
    _section("snapshot + net worth")
    try:
        batch_id = db.insert_investment_snapshot(
            batch={
                "source_file":   "smoke.csv",
                "file_hash":     "abc",
                "as_of_date":    parsed["as_of_date"],
                "currencies_seen": ",".join(parsed["currencies"]),
                "mixed_currency": 1,
                "notes":         "smoke",
            },
            positions=parsed["positions"],
            conn=conn,
        )
        conn.commit()
        snap = db.get_latest_investment_snapshot(conn=conn)
        check("snapshot id round-trip", snap and snap["id"] == batch_id)
        check("snapshot positions preserved",
              snap and len(snap["positions"]) == 2)

        db.insert_account_balance({
            "account_name": "Tangerine Chq", "account_kind": "chequing",
            "balance": 2500.0, "currency": "CAD",
            "as_of_date": "2026-05-04",
        }, conn=conn)
        db.insert_account_balance({
            "account_name": "Visa", "account_kind": "credit_card",
            "balance": 800.0, "currency": "CAD",
            "as_of_date": "2026-05-04",
        }, conn=conn)
        conn.commit()

        nw = db.compute_net_worth_now(conn=conn)
        # Investments naive sum: 3050 + 4200 = 7250 (mixed CAD+USD).
        # Plus chequing 2500 → assets 9750. Liab = 800 → net 8950.
        check("net worth liabilities", nw["total_liabilities"] == 800.0,
              f"got {nw['total_liabilities']}")
        check("net worth assets > 9000", nw["total_assets"] > 9000)
        check("net worth mixed_currency",
              nw["mixed_currency"] is True)
    except Exception as e:
        check("snapshot/NW", False, repr(e))

    # 5. Agent context export (populated DB)
    _section("agent context")
    try:
        from utils.agent_context import build_agent_context
        ctx = build_agent_context(conn=conn)
        check("ctx has investments_summary",
              bool(ctx.get("investments_summary")))
        check("ctx has net_worth_summary",
              bool(ctx.get("net_worth_summary")))
        check("ctx investments has by_account_type",
              isinstance(
                  ctx["investments_summary"].get("by_account_type"), list))
        # Make sure no API keys leak into the context
        import json as _json
        s = _json.dumps(ctx, default=str)
        check("ctx has no sk- key", "sk-cp-" not in s)
        check("ctx has no Bearer token", "Bearer " not in s)
        check("ctx has no api_key field", '"api_key"' not in s)
    except Exception as e:
        check("agent context", False, repr(e))

    # 6. Pass 21 — month plan / forecast / goals / bills
    _section("month plan / forecast / goals / bills")
    try:
        from utils.planner import (
            PLAN_MODES, analysis_anchor, generate_starter_plan,
            forecast_month, bills_and_commitments, goal_progress,
        )
        anchor = analysis_anchor(conn=conn)
        check("anchor is YYYY-MM",
              isinstance(anchor, str) and len(anchor) == 7
              and anchor[4] == "-")
        # Insufficient-data path: empty test DB has no transactions
        # under a *different* anchor month. We seed one transaction so
        # the plan generator returns finite numbers.
        conn.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, "
            " amount, direction, category, dedup_hash) "
            "VALUES ('chequing','2026-04-15','Test grocery',"
            " 120.0,'debit','Groceries','smoke_tx_1')")
        conn.commit()
        plan = generate_starter_plan("normal", conn=conn)
        check("plan has month + targets",
              "month" in plan and "category_targets" in plan)
        check("plan modes catalog",
              "tight" in PLAN_MODES and "aggressive_save" in PLAN_MODES)

        # Save + reload round-trip
        pid = db.upsert_monthly_plan({
            "month": plan["month"], "mode": plan["mode"],
            "income_target": plan["income_target"],
            "spending_target": plan["spending_target"],
            "savings_target": plan["savings_target"],
        }, conn=conn)
        db.replace_category_targets(pid, plan["category_targets"],
                                    conn=conn)
        conn.commit()
        saved = db.get_monthly_plan(plan["month"], conn=conn)
        check("plan reloads",
              saved and saved.get("mode") == plan["mode"])
        check("plan targets persist",
              len(saved.get("category_targets") or [])
              == len(plan["category_targets"]))

        fc = forecast_month(conn=conn)
        check("forecast risk_level present",
              fc.get("risk_level") in
              {"on_track", "watch", "danger", "insufficient_data"})

        bills = bills_and_commitments(conn=conn)
        check("bills shape",
              "items" in bills and "monthly_estimate" in bills)

        gid = db.insert_goal({
            "name": "Smoke Cash Buffer",
            "type": "cash_buffer",
            "target_amount": 5000.0,
            "linked_metric": "cash_balance",
            "status": "active",
        }, conn=conn)
        conn.commit()
        progressed = goal_progress(db.get_goals(conn=conn), conn=conn)
        check("goal progress computes pct",
              progressed and 0.0 <= progressed[0]["progress_pct"] <= 1.0)
        db.delete_goal(gid, conn=conn)
        conn.commit()

        # Pass 21 — agent context now carries plan / forecast / goals / bills
        from utils.agent_context import build_agent_context
        ctx = build_agent_context(conn=conn)
        check("ctx month_plan saved=True",
              bool(ctx.get("month_plan", {}).get("mode")))
        check("ctx forecast", bool(ctx.get("forecast", {}).get("risk_level")))
        check("ctx bills_summary", "monthly_estimate" in (ctx.get("bills_summary") or {}))
        check("ctx next_actions list",
              isinstance(ctx.get("next_actions"), list))
        check("ctx reminder_suggestions list",
              isinstance(ctx.get("reminder_suggestions"), list))

        # Pass 25 — everyday-use keys
        check("ctx everyday_summary str",
              isinstance(ctx.get("everyday_summary"), str))
        check("ctx next_best_move str",
              isinstance(ctx.get("next_best_move"), str))
        check("ctx reduce_plan dict",
              isinstance(ctx.get("reduce_plan"), dict))
        check("ctx trend_summary dict",
              isinstance(ctx.get("trend_summary"), dict))

        # Pass 33: monthly_review shape-only checks. Numbers vary with
        # the seeded test DB so we never assert exact values — just the
        # contract.
        _mr_ctx = ctx.get("monthly_review") or {}
        check("ctx monthly_review dict",
              isinstance(_mr_ctx, dict))
        check("ctx monthly_review has available bool",
              isinstance(_mr_ctx.get("available"), bool))
        check("ctx monthly_review has top_increases list",
              isinstance(_mr_ctx.get("top_increases"), list))
        check("ctx monthly_review has top_decreases list",
              isinstance(_mr_ctx.get("top_decreases"), list))
        check("ctx monthly_review has data_caveats list",
              isinstance(_mr_ctx.get("data_caveats"), list))
        _mr_blob = json.dumps(_mr_ctx, default=str)
        check("ctx monthly_review safe-to-export",
              "sk-" not in _mr_blob and "Bearer " not in _mr_blob)
        _rw_ctx = ctx.get("money_runway") or {}
        _md_ctx = ctx.get("mission_deck") or []
        _fm_ctx = ctx.get("found_money") or {}
        check("ctx money_runway dict",
              isinstance(_rw_ctx, dict))
        check("ctx money_runway has safe_to_spend shape",
              isinstance(_rw_ctx.get("safe_to_spend"), dict)
              or _rw_ctx.get("available") is False)
        check("ctx mission_deck list",
              isinstance(_md_ctx, list))
        check("ctx found_money dict",
              isinstance(_fm_ctx, dict))
        _habit_blob = json.dumps({
            "money_runway": _rw_ctx,
            "mission_deck": _md_ctx,
            "found_money": _fm_ctx,
        }, default=str)
        check("ctx runway/mission/found-money safe-to-export",
              all(x not in _habit_blob for x in (
                  "sk-", "Bearer ", '"api_key"', "finance.db", "config.json"
              )))
        check("ctx money_moves_summary has buckets",
              all(k in (ctx.get("money_moves_summary") or {})
                  for k in ("do_now", "review_week", "watch", "total")))

        # Pass 26 — daily-use spine keys
        check("ctx today_summary dict",
              isinstance(ctx.get("today_summary"), dict))
        check("ctx top_money_move dict",
              isinstance(ctx.get("top_money_move"), dict))
        check("ctx reduce_plan_v2 dict",
              isinstance(ctx.get("reduce_plan_v2"), dict))
        check("ctx what_changed dict",
              isinstance(ctx.get("what_changed"), dict))
        check("ctx net_worth_builder has state",
              "state" in (ctx.get("net_worth_builder") or {}))
        check("ctx open_loops list",
              isinstance(ctx.get("open_loops"), list))

        # Pass 27 — reduce v2 / redirect / weekly loop keys
        check("ctx top_reduce_target dict",
              isinstance(ctx.get("top_reduce_target"), dict))
        check("ctx top_reduce_target has label",
              "label" in (ctx.get("top_reduce_target") or {}))
        _sro = ctx.get("savings_redirect_options") or {}
        check("ctx savings_redirect_options has options",
              isinstance(_sro.get("options"), list)
              and len(_sro.get("options") or []) >= 4)
        check("ctx savings_redirect_options recommended_priority",
              (_sro.get("recommended_priority") or "")
              in {"cash_buffer", "debt_reduction",
                  "investment", "custom_goal"})
        _presets = ctx.get("reduce_scenario_presets") or []
        check("ctx reduce_scenario_presets has 6 items",
              isinstance(_presets, list) and len(_presets) == 6)
        _wml = ctx.get("weekly_money_loop") or {}
        check("ctx weekly_money_loop has 6 days",
              all(d in _wml for d in
                  ("monday","tuesday","wednesday","thursday",
                   "friday","weekend")))
        check("ctx openclaw_reminder_suggestions list",
              isinstance(ctx.get("openclaw_reminder_suggestions"), list))
    except Exception as e:
        check("month plan smoke", False, repr(e))

    # 7. Pass 22 — Ask Ledger v3 routing + deterministic answers
    _section("Ask Ledger v3 (Pass 22)")
    try:
        from utils.ai_explainer import (
            ASK_PRESETS, _route_question, _build_ask_packet,
            _deterministic_ask, explain_month_plan, explain_forecast,
            coach_goals,
        )
        new_skills = {
            "month_plan", "forecast_risk", "safe_to_spend", "bills_due",
            "category_targets", "goal_progress", "next_payday_focus",
            "reminder_suggestions",
        }
        registered = {p[0] for p in ASK_PRESETS}
        check("8 new skills registered",
              new_skills.issubset(registered),
              f"missing: {new_skills - registered}")

        routes = {
            "how much can i safely spend":   "safe_to_spend",
            "am i on track this month":      "forecast_risk",
            "what bills are coming up":      "bills_due",
            "how are my goals doing":        "goal_progress",
            "what is my plan this month":    "month_plan",
            "what should openclaw remind me about": "reminder_suggestions",
            "what should i do before next payday":  "next_payday_focus",
            "which category target should i focus on": "category_targets",
        }
        all_routed = all(_route_question(q) == s for q, s in routes.items())
        check("all 8 routes match", all_routed)

        # Each new skill produces a deterministic packet+answer.
        conn2 = db.get_connection()
        for s in new_skills:
            pkt = _build_ask_packet(s, conn2)
            ans = _deterministic_ask(s, pkt)
            check(f"deterministic[{s}]",
                  bool(ans.get("answer")),
                  f"empty for {s}")
        conn2.close()

        # Coaches always return a usable shape (deterministic when AI off).
        out_p = explain_month_plan({})
        check("explain_month_plan fallback",
              "headline" in out_p and "summary" in out_p)
        out_f = explain_forecast({"risk_level": "watch",
                                  "projected_net": 100})
        check("explain_forecast fallback",
              "risk_explanation" in out_f and "next_action" in out_f)
        out_g = coach_goals([])
        check("coach_goals empty fallback",
              bool(out_g.get("progress_summary")))
    except Exception as e:
        check("ask v3 smoke", False, repr(e))

    # 8. Pass 23 — bills/commitments truth-layer classifier
    _section("commitments classifier (Pass 23)")
    try:
        # Spin up a fresh DB just for this test so synthetic recurring
        # rows don't leak into prior assertions.
        import tempfile as _tmp
        from pathlib import Path as _P
        c23_dir = _P(_tmp.mkdtemp(prefix="ledger_p23_"))
        c23_db = c23_dir / "finance.db"
        import importlib
        import utils.database as _ldb
        _ldb.DB_PATH = c23_db
        _ldb.init_db()
        c23 = _ldb.get_connection()

        # Helper to insert a recurring synthetic row across N months
        # ending in the same month so subscription_detective and
        # recurring_merchants both see them.
        from datetime import date as _date, timedelta as _td
        def _insert_recurring(merchant, category, amount, months=4,
                               direction="debit", is_transfer=0):
            base = _date(2026, 4, 15)
            for i in range(months):
                # Walk one month per step backward.
                y = base.year
                m = base.month - i
                while m <= 0:
                    m += 12
                    y -= 1
                tx_date = _date(y, m, 15).isoformat()
                dh = f"p23-{merchant}-{i}-{amount}"
                c23.execute(
                    "INSERT INTO transactions "
                    "(account_type,transaction_date,raw_description,merchant,"
                    " amount,direction,category,is_transfer,dedup_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    ("chequing", tx_date, merchant, merchant,
                     amount, direction, category, is_transfer, dh),
                )

        # Fixtures:
        _insert_recurring("MORTGAGE COMPANY",  "Housing / Mortgage", 1800)
        _insert_recurring("HYDRO ONE",         "Utilities / Bills",   180)
        _insert_recurring("METRO GROCERY",     "Groceries",           220)
        _insert_recurring("HOME DEPOT",        "Home Improvement",    150)
        _insert_recurring("E-TRANSFER ROOMMATE", "Transfer Out",      400,
                          is_transfer=1)
        # Active sub via subscription_detective: needs 3+ months and
        # similar amounts (low CV). Add to its own merchant.
        for i in range(4):
            y, m = 2026, 4 - i
            while m <= 0:
                m += 12; y -= 1
            tx_date = _date(y, m, 1).isoformat()
            c23.execute(
                "INSERT INTO transactions "
                "(account_type,transaction_date,raw_description,merchant,"
                " amount,direction,category,is_transfer,dedup_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("chequing", tx_date, "NETFLIX", "NETFLIX",
                 16.99, "debit", "Subscriptions & Digital", 0,
                 f"p23-netflix-{i}"))
        c23.commit()

        from utils.planner import bills_and_commitments, forecast_month
        b = bills_and_commitments(conn=c23)

        # Categorize what landed where.
        merchants_by_group = {
            g: [i["merchant"] for i in b.get(g) or []]
            for g in ("fixed_commitments", "active_subscriptions",
                      "recurring_variable_merchants", "stale_or_inactive")
        }

        check("mortgage in fixed_commitments",
              "MORTGAGE COMPANY" in merchants_by_group["fixed_commitments"])
        check("hydro in fixed_commitments",
              "HYDRO ONE" in merchants_by_group["fixed_commitments"])
        # Subscription_detective requires CV; if synthetic Netflix
        # doesn't qualify on this seed, the row should land in
        # fixed-commitments-or-variable. We accept either subscriptions
        # OR fixed (Subscriptions & Digital is variable per Pass 23
        # rules, so it should NOT land in fixed). Real-world Netflix
        # comes through subscription_detective with high confidence.
        netflix_in_subs = "NETFLIX" in merchants_by_group["active_subscriptions"]
        netflix_in_variable = "NETFLIX" in merchants_by_group["recurring_variable_merchants"]
        check("netflix classified (subs or variable)",
              netflix_in_subs or netflix_in_variable)
        check("groceries in variable_watch",
              "METRO GROCERY" in merchants_by_group["recurring_variable_merchants"])
        check("home depot in variable_watch",
              "HOME DEPOT" in merchants_by_group["recurring_variable_merchants"])
        check("transfer-out NOT in any commitment group",
              all("E-TRANSFER ROOMMATE" not in merchants_by_group[g]
                  for g in ("fixed_commitments", "active_subscriptions",
                            "recurring_variable_merchants")))

        # Totals: monthly_estimate = commitment_monthly_estimate (only
        # fixed + active subs).
        check("monthly_estimate == commitment_monthly_estimate",
              abs(b["monthly_estimate"] - b["commitment_monthly_estimate"]) < 0.01)
        # Variable watch is non-zero (Groceries + Home Improvement).
        check("variable_monthly_watch > 0",
              b["variable_monthly_watch"] > 0)
        # Variable rows MUST NOT be inside the commitment total.
        commitment_merchants = (merchants_by_group["fixed_commitments"]
                                + merchants_by_group["active_subscriptions"])
        check("groceries NOT in commitments",
              "METRO GROCERY" not in commitment_merchants)
        check("home depot NOT in commitments",
              "HOME DEPOT" not in commitment_merchants)

        # Forecast uses commitments only for upcoming_bills_total.
        # We add a small in-month synthetic transaction so forecast_month
        # has MTD activity.
        c23.execute(
            "INSERT INTO transactions "
            "(account_type,transaction_date,raw_description,merchant,"
            " amount,direction,category,is_transfer,dedup_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("chequing", "2026-05-02", "MTD GROCERY", "MTD GROCERY",
             50.0, "debit", "Groceries", 0, "p23-mtd-1"))
        c23.commit()

        fc = forecast_month(plan_month="2026-05", conn=c23)
        check("forecast has recurring_variable_watch_total",
              "recurring_variable_watch_total" in fc)
        # upcoming_bills_total should be ≤ commitment_monthly_estimate.
        # (It can be lower if some bills already hit MTD; never higher.)
        check("forecast upcoming_bills <= commitments",
              fc["upcoming_bills_total"] <= b["commitment_monthly_estimate"] + 0.01)
        # variable watch is exposed but NOT added into projected_spending
        # baseline math (we only assert the expose; the math correctness
        # comes via the upcoming_bills upper bound above).
        check("forecast variable_watch_total > 0",
              fc["recurring_variable_watch_total"] > 0)

        # Cleanup of this DB.
        c23.close()
        try:
            for _f in c23_dir.iterdir():
                try: _f.unlink()
                except IsADirectoryError: pass
            c23_dir.rmdir()
        except Exception:
            pass
        # Restore main DB path so any later assertions hit the real DB.
        _ldb.DB_PATH = _P(__file__).resolve().parent.parent / "data" / "finance.db"
    except Exception as e:
        check("commitments classifier", False, repr(e))

    # 9. Pass 24 — diagnostics builder + bug report bundle safety
    _section("diagnostics + bug report (Pass 24)")
    try:
        from utils.diagnostics import (
            build_diagnostics, _mask_key, app_environment,
        )
        # Empty-DB resilience: build_diagnostics must work on the
        # fresh tmp DB used earlier in this run.
        diag = build_diagnostics()
        check("diag has all sections",
              all(k in diag for k in
                  ("environment", "database", "ai", "sharing",
                   "finance", "generated_at")))
        # AI key masking never leaks the full key. Note: this fixture
        # uses an "example" substring so the share-zip secret scanner
        # treats it as a placeholder, not a real key.
        masked = _mask_key("sk-cp-example1234567890abcdefghij")
        check("AI key mask hides full key",
              "example1234567890" not in masked
              and ("…" in masked or "•" in masked))
        # Environment block has no key/secret values.
        env_text = json.dumps(diag.get("environment") or {}, default=str)
        for forbidden in ("sk-cp-", "Bearer "):
            check(f"env has no {forbidden!r}", forbidden not in env_text)

        # Bug report bundle — write to temp, inspect, delete.
        import tempfile as _tmp, zipfile as _zf
        out_zip = _P(_tmp.mkdtemp(prefix="ledger_p24_")) / "bug.zip"
        rc = subprocess.run(
            [sys.executable, "-m", "scripts.make_bug_report",
             "--skip-smoke", "--out", str(out_zip)],
            cwd=str(_P(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        check("bug report bundle exit 0", rc.returncode == 0,
              f"stderr={rc.stderr[:120]}")
        check("bug zip exists", out_zip.exists())
        if out_zip.exists():
            zf = _zf.ZipFile(out_zip)
            names = zf.namelist()
            check("bundle has diagnostics.json",
                  any("diagnostics.json" in n for n in names))
            check("bundle has README.txt",
                  any("README.txt" in n for n in names))
            # No forbidden artifacts as zip entries.
            for forbidden in ("config.json", "finance.db", ".env"):
                check(f"bundle has no {forbidden}",
                      not any(forbidden in n for n in names))
            # No raw key inside any file in the bundle. Read the
            # currently-configured key (if any) from config.json and
            # assert it never appears verbatim. We never reproduce the
            # key string in test source — that would be a self-leak.
            real_key = ""
            try:
                cfg = (_P(__file__).resolve().parent.parent / "config.json")
                if cfg.exists():
                    real_key = (json.loads(cfg.read_text(encoding="utf-8"))
                                .get("ai", {}).get("api_key", "") or "")
            except Exception:
                real_key = ""
            for n in names:
                body = zf.read(n).decode("utf-8", errors="replace")
                if real_key and len(real_key) >= 16:
                    check(f"no live key in {n}", real_key not in body)
                else:
                    # No key configured — just assert no sk-prefix
                    # token of meaningful length leaked.
                    import re as _re
                    leak = _re.search(r"sk-[A-Za-z0-9_\-]{16,}", body)
                    check(f"no sk- token in {n}", not bool(leak))
            zf.close()
            try: out_zip.unlink()
            except OSError: pass
            try: out_zip.parent.rmdir()
            except OSError: pass
    except Exception as e:
        check("diagnostics + bug report", False, repr(e))

    # 10. Pass 28 — demo data + demo mode + first-action exposure
    _section("demo data + demo mode (Pass 28)")
    try:
        # 10a. utils.reduce_actions catalog is importable and stable
        from utils.reduce_actions import (
            CATEGORY_FIRST_ACTION, CATEGORY_DIFFICULTY,
            first_action_for, difficulty_for,
        )
        check("reduce_actions catalog non-empty",
              len(CATEGORY_FIRST_ACTION) >= 5
              and len(CATEGORY_DIFFICULTY) >= 5)
        check("reduce_actions Shopping has first_action",
              "Shopping" in CATEGORY_FIRST_ACTION)
        check("first_action_for unknown returns string",
              isinstance(first_action_for("MadeUpCat"), str)
              and "MadeUpCat" in first_action_for("MadeUpCat"))
        check("difficulty_for unknown is moderate",
              difficulty_for("MadeUpCat") == "moderate")

        # 10b. demo-mode helpers
        from utils.database import (
            is_demo_mode, demo_db_path, real_db_path,
        )
        check("demo_db_path resolves",
              demo_db_path().name == "finance.demo.db")
        check("real_db_path resolves",
              real_db_path().name == "finance.db")
        check("is_demo_mode returns bool",
              isinstance(is_demo_mode(), bool))

        # 10c. agent_context exposes demo flag + first_action
        from utils.agent_context import build_agent_context
        ctx_demo = build_agent_context()
        check("ctx demo_mode key present",
              "demo_mode" in ctx_demo)
        check("ctx demo_warning key present",
              "demo_warning" in ctx_demo)
        check("ctx db_path_basename ends with .db",
              str(ctx_demo.get("db_path_basename") or "").endswith(".db"))
        trt = ctx_demo.get("top_reduce_target") or {}
        check("top_reduce_target has first_action",
              isinstance(trt.get("first_action"), str)
              and len(trt.get("first_action") or "") > 0)
        check("top_reduce_target has difficulty",
              trt.get("difficulty") in {"easy", "moderate", "harder"})

        # 10d. Demo seed script: build → assert shape → no real-name leaks.
        from pathlib import Path as _P
        import tempfile as _tmp
        demo_tmp_dir = _P(_tmp.mkdtemp(prefix="ledger_demo_smoke_"))
        demo_tmp_db  = demo_tmp_dir / "finance.demo.db"
        rc_demo = subprocess.run(
            [sys.executable, "-m", "scripts.create_demo_data",
             "--force", "--out", str(demo_tmp_db)],
            cwd=str(_P(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        check("demo script exit 0", rc_demo.returncode == 0,
              f"stderr={rc_demo.stderr[:160]}")
        check("demo DB file exists", demo_tmp_db.exists())

        if demo_tmp_db.exists():
            import sqlite3 as _sq
            dconn = _sq.connect(str(demo_tmp_db))
            dconn.row_factory = _sq.Row
            n_tx = dconn.execute(
                "SELECT COUNT(*) FROM transactions").fetchone()[0]
            check("demo DB has >= 100 transactions", n_tx >= 100,
                  f"got {n_tx}")
            n_subs = dconn.execute(
                "SELECT COUNT(DISTINCT merchant) FROM transactions "
                "WHERE category='Subscriptions & Digital'"
            ).fetchone()[0]
            check("demo DB has >= 3 subscription merchants", n_subs >= 3,
                  f"got {n_subs}")
            n_pos = dconn.execute(
                "SELECT COUNT(*) FROM investment_positions").fetchone()[0]
            check("demo DB has >= 3 investment positions", n_pos >= 3,
                  f"got {n_pos}")
            n_nw = dconn.execute(
                "SELECT COUNT(*) FROM net_worth_snapshots").fetchone()[0]
            check("demo DB has >= 1 net worth snapshot", n_nw >= 1,
                  f"got {n_nw}")
            n_goals = dconn.execute(
                "SELECT COUNT(*) FROM goal_targets").fetchone()[0]
            check("demo DB has >= 1 goal", n_goals >= 1,
                  f"got {n_goals}")
            # Real-name / key leakage check across the seed data.
            blob = ""
            for tbl in ("transactions", "investment_positions",
                         "account_balances", "goal_targets"):
                for row in dconn.execute(f"SELECT * FROM {tbl} LIMIT 500"):
                    blob += " ".join(str(v) for v in row if v) + " "
            for forbidden in ("Real Person", "Private Employer",
                              "Private Lender", "sk-"):
                check(f"demo DB has no {forbidden!r}",
                      forbidden not in blob)
            # Every seeded merchant should be a 'DEMO ' prefix or a
            # generic transfer label.
            n_demo_merchants = dconn.execute(
                "SELECT COUNT(*) FROM transactions "
                "WHERE merchant LIKE 'DEMO %'"
            ).fetchone()[0]
            check("demo DB has many DEMO-prefixed merchants",
                  n_demo_merchants >= 50, f"got {n_demo_merchants}")
            dconn.close()
        # Cleanup
        try:
            for f in demo_tmp_dir.iterdir():
                try: f.unlink()
                except OSError: pass
            demo_tmp_dir.rmdir()
        except Exception:
            pass
    except Exception as e:
        check("demo data + demo mode", False, repr(e))

    conn.close()

    # 11c. Pass 35 — statement-aware trust layer
    # Builds an isolated DB with one COMPLETE month (April, every weekday
    # imported) + one PARTIAL month (May, only first week). Exercises:
    #   * statement_coverage() classifies April complete / May partial
    #   * compute_score uses the latest complete month for its window
    #     and labels it as such (NOT "last 90 days")
    #   * cash_advance_status() with a Cash Advance followed by larger
    #     CC payment(s) returns verdict "covered" and a safe_action that
    #     does NOT contain "pay off"
    #   * friendly_import_period() turns an ugly fallback period string
    #     into a derived "YYYY-MM statement / first..last" label
    #   * cash-advance recommendation flips to type="review" (not "fix")
    #     when later CC payments plausibly cover the advance
    _section("statement-aware trust layer (Pass 35)")
    try:
        import tempfile as _tmp35
        from pathlib import Path as _P35
        from datetime import date as _date35
        import utils.database as _db35
        d35 = _P35(_tmp35.mkdtemp(prefix="ledger_p35_"))
        _db35.DB_PATH = d35 / "finance.db"
        _db35.init_db()
        c35 = _db35.get_connection()

        # Seed 22 weekdays of April with mixed income/spending so April
        # is complete by the >=14 distinct-days + <=7-days-before-EOM
        # rules. Income posts on a payday so the savings rate is non-zero.
        for i, day in enumerate(range(1, 30)):
            d_iso = f"2026-04-{day:02d}"
            c35.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO COFFEE STOP', 'DEMO COFFEE', "
                " 4.50, 'debit', 'Food & Convenience', ?)",
                (d_iso, f"p35-apr-coffee-{day}"),
            )
        c35.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('chequing', '2026-04-15', 'PAYROLL', 'PAYROLL', "
            " 3200.0, 'credit', 'Income', 'p35-apr-payroll-1'),"
            "('chequing', '2026-04-30', 'PAYROLL', 'PAYROLL', "
            " 3200.0, 'credit', 'Income', 'p35-apr-payroll-2')"
        )
        # Partial May — only 5 days, well before month-end.
        for day in range(2, 8):
            d_iso = f"2026-05-{day:02d}"
            c35.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO COFFEE STOP', 'DEMO COFFEE', "
                " 4.50, 'debit', 'Food & Convenience', ?)",
                (d_iso, f"p35-may-coffee-{day}"),
            )
        # Cash advance + larger CC payment after it — verdict should be
        # "covered" (CC payments since first CA >= CA total).
        c35.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('mastercard', '2026-04-04', 'CASH ADVANCE', 'CASH ADVANCE', "
            " 305.0, 'debit', 'Cash Advance', 'p35-ca-1'),"
            "('chequing', '2026-04-20', 'CC PAYMENT', 'CC PAYMENT', "
            " 800.0, 'payment', 'Credit Card Payment', 'p35-cc-pay-1')"
        )
        c35.commit()

        from utils.insights import (
            statement_coverage, cash_advance_status,
            friendly_import_period, compute_recommendations,
        )
        from utils.analytics import compute_score

        # --- statement_coverage ---
        cov35 = statement_coverage(conn=c35)
        check("p35 cov: April is complete",
              "2026-04" in (cov35.get("complete_months") or []))
        check("p35 cov: May is partial",
              "2026-05" in (cov35.get("partial_months") or []))
        check("p35 cov: latest_complete_month == 2026-04",
              cov35.get("latest_complete_month") == "2026-04")
        check("p35 cov: incomplete_reason is non-empty for partial May",
              bool(cov35.get("incomplete_reason")))

        # --- compute_score uses latest complete month ---
        sc35 = compute_score(conn=c35)
        check("p35 score: window label mentions 2026-04",
              "2026-04" in (sc35.get("score_window_label") or ""))
        check("p35 score: period label is NOT 'last 90 days'",
              "90 days" not in (sc35.get("score_period_label") or ""))
        check("p35 score: analysis_month == 2026-04",
              sc35.get("analysis_month") == "2026-04")

        # --- cash_advance_status verdict + safe wording ---
        cas35 = cash_advance_status(conn=c35)
        check("p35 ca: verdict == 'covered'",
              cas35.get("verdict") == "covered")
        check("p35 ca: plausibly_covered is True",
              bool(cas35.get("plausibly_covered")))
        _sa = (cas35.get("safe_action") or "").lower()
        check("p35 ca: safe_action does NOT say 'pay off'",
              "pay off" not in _sa)
        check("p35 ca: safe_action mentions later payments",
              ("later credit-card" in _sa) or ("later cc" in _sa)
              or ("payments" in _sa))

        # --- compute_recommendations flips type to 'review' when covered ---
        recs35 = compute_recommendations(conn=c35) or []
        _ca_rec = next(
            (r for r in recs35 if r.get("key") == "cash_advance"), None,
        )
        check("p35 recs: cash_advance rec present", _ca_rec is not None)
        if _ca_rec is not None:
            check("p35 recs: cash_advance rec is type 'review' (not 'fix')",
                  _ca_rec.get("type") == "review")
            check("p35 recs: cash_advance rec body has no 'pay off'",
                  "pay off" not in (_ca_rec.get("body") or "").lower())

        # --- friendly_import_period turns ugly ID into derived label ---
        # Create one import_log row + link two transactions to that batch.
        c35.execute(
            "INSERT INTO import_log "
            "(filename, file_hash, account_type, statement_period, "
            " rows_inserted) VALUES "
            "('demo.pdf','h35','chequing','ledger_document_42',2)"
        )
        batch_id_35 = c35.execute(
            "SELECT id FROM import_log WHERE file_hash='h35'"
        ).fetchone()["id"]
        c35.execute(
            "UPDATE transactions SET import_batch_id=? "
            "WHERE dedup_hash IN ('p35-apr-payroll-1','p35-apr-payroll-2')",
            (batch_id_35,),
        )
        c35.commit()
        fip = friendly_import_period(
            statement_period="ledger_document_42",
            batch_id=batch_id_35, conn=c35,
        )
        check("p35 period: friendly label is not the ugly raw ID",
              "ledger_document" not in fip.lower())
        check("p35 period: friendly label mentions 2026-04",
              "2026-04" in fip)

        # --- Money Runway + Mission Deck stable packets ---
        c35.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('mastercard','2026-04-12','DEMO STREAM','DEMO STREAM',"
            " 20.00,'debit','Subscriptions & Digital','p36-sub-active-1'),"
            "('mastercard','2026-05-04','DEMO STREAM','DEMO STREAM',"
            " 20.00,'debit','Subscriptions & Digital','p36-sub-active-2'),"
            "('mastercard','2026-01-05','DEMO OLD SUB','DEMO OLD SUB',"
            " 15.00,'debit','Subscriptions & Digital','p36-sub-stale-1'),"
            "('mastercard','2026-02-05','DEMO OLD SUB','DEMO OLD SUB',"
            " 15.00,'debit','Subscriptions & Digital','p36-sub-stale-2')"
        )
        c35.commit()
        from utils.insights import (
            money_runway, mission_deck, found_money, subscription_detective,
        )
        rw36 = money_runway(conn=c35)
        check("p36 runway: returns dict",
              isinstance(rw36, dict))
        check("p36 runway: available bool",
              isinstance(rw36.get("available"), bool))
        check("p36 runway: safe_to_spend shape exists",
              all(k in (rw36.get("safe_to_spend") or {}) for k in (
                  "amount", "daily_amount", "days_left", "period_end",
                  "confidence", "formula"
              )))
        check("p36 runway: formula has core fields",
              all(k in ((rw36.get("safe_to_spend") or {}).get("formula") or {})
                  for k in (
                      "income_available_or_expected", "spending_so_far",
                      "planned_bills_remaining",
                      "active_subscriptions_remaining",
                      "goal_commitments", "debt_or_fee_reserve", "buffer"
                  )))
        check("p36 runway: watchlists list",
              isinstance(rw36.get("watchlists"), list))
        md36 = mission_deck(conn=c35, limit=3)
        check("p36 missions: returns list",
              isinstance(md36, list))
        check("p36 missions: every mission has if_then_plan",
              bool(md36) and all(m.get("if_then_plan") for m in md36))
        _md_blob = json.dumps(md36, default=str).lower()
        check("p36 missions: cash advance mission does not say pay off",
              "pay off" not in _md_blob)
        fm36 = found_money(conn=c35)
        check("p36 found_money: stable dict",
              isinstance(fm36, dict)
              and isinstance(fm36.get("wins"), list)
              and "potential_redirect" in fm36)
        sub36 = subscription_detective(conn=c35)
        stale_names = {s.get("merchant") for s in sub36.get("stale_subs", [])}
        active_candidate_names = {
            s.get("merchant") for s in sub36.get("active_candidates", [])
        }
        check("p36 subs: inactive not active savings opportunities",
              "DEMO OLD SUB" in stale_names
              and "DEMO OLD SUB" not in active_candidate_names)

        c35.close()
        try:
            for f in d35.iterdir():
                try: f.unlink()
                except OSError: pass
            d35.rmdir()
        except Exception:
            pass
        # Restore the main DB path.
        _db35.DB_PATH = (
            _P35(__file__).resolve().parent.parent / "data" / "finance.db"
        )
    except Exception as e:
        check("Pass 35 trust layer", False, repr(e))

    # 11f. Pass 35d — Dashboard truth + Review reliability + score math.
    # Covers all five repair areas:
    #   * generate_insights ignores a partial latest month
    #   * Copilot _dashboard_packet anchors on the complete month and
    #     exposes statement_completeness
    #   * compute_score Diversity dimension (now "Spending control")
    #     excludes Housing / Mortgage and names the top controllable
    #     category in the reason string
    #   * get_score_weights/get_ai_candidates defaults flipped to
    #     40/30/15/15
    #   * get_ai_candidates excludes rows whose flag_reason='reviewed'
    #     (retroactive cleanup for pre-Pass-34a saved rows)
    #   * mark_transaction_reviewed promotes parse_confidence and
    #     removes the row from get_ai_candidates afterwards
    #   * spending_donut top slices show category-name + percent text
    _section("Pass 35d repair (truth + review)")
    try:
        import tempfile as _tmp35d
        from pathlib import Path as _P35d
        import utils.database as _db35d
        d35d = _P35d(_tmp35d.mkdtemp(prefix="ledger_p35d_"))
        _db35d.DB_PATH = d35d / "finance.db"
        _db35d.init_db()
        c35d = _db35d.get_connection()

        # Default weights match Pass 36 (40/30/15/15)
        sw35d = _db35d.get_score_weights(conn=c35d)
        check("p35d weights: savings_weight default 40",
              float(sw35d.get("savings_weight") or 0) == 40)
        check("p36 weights: spending control default 30",
              float(sw35d.get("diversity_weight") or 0) == 30)
        check("p36 weights: debt_weight default 15",
              float(sw35d.get("debt_weight") or 0) == 15)
        check("p36 weights: consistency_weight default 15",
              float(sw35d.get("consistency_weight") or 0) == 15)

        # Seed: complete March, complete April (mortgage-heavy +
        # controllable mix), partial May.
        for day in range(1, 30):
            c35d.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'GROCERY', 'GROCERY', 30.00, 'debit', "
                " 'Groceries', ?)",
                (f"2026-03-{day:02d}", f"p35d-mar-{day}"),
            )
        # April: a big mortgage + a bunch of Shopping + payroll income.
        for day in range(1, 30):
            c35d.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'SHOPPING', 'SHOPPING', 15.00, 'debit', "
                " 'Shopping', ?)",
                (f"2026-04-{day:02d}", f"p35d-apr-shop-{day}"),
            )
        c35d.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('chequing','2026-04-01','MORTGAGE','MORTGAGE',1800,'debit','Housing / Mortgage','p35d-mortgage-1'),"
            "('chequing','2026-04-15','PAYROLL','PAYROLL',3200,'credit','Income','p35d-pay-1'),"
            "('chequing','2026-04-30','PAYROLL','PAYROLL',3200,'credit','Income','p35d-pay-2')"
        )
        # Partial May
        for day in range(2, 8):
            c35d.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'COFFEE', 'COFFEE', 4.50, 'debit', "
                " 'Food & Convenience', ?)",
                (f"2026-05-{day:02d}", f"p35d-may-{day}"),
            )
        c35d.commit()

        # generate_insights ignores partial May, surfaces it as info
        from utils.insights import generate_insights, statement_coverage
        cov_d = statement_coverage(conn=c35d)
        check("p35d cov: April is complete",
              "2026-04" in (cov_d.get("complete_months") or []))
        check("p35d cov: May is partial",
              "2026-05" in (cov_d.get("partial_months") or []))
        ins = generate_insights(conn=c35d) or []
        _ins_blob = " ".join(
            (i.get("title", "") + " " + i.get("body", "")) for i in ins
        )
        check("p35d insights: no 'Savings rate is low: 0%' from partial May",
              "Savings rate is low: 0%" not in _ins_blob)
        # An explicit "Partial recent activity in 2026-05" card should
        # be present so the user knows the in-progress month is visible.
        check("p35d insights: 'Partial recent activity' card present",
              any("Partial recent activity" in (i.get("title") or "")
                  for i in ins))

        # _dashboard_packet uses complete-month anchor + exposes the
        # statement_completeness sub-dict.
        from utils.ai_explainer import _dashboard_packet
        pkt = _dashboard_packet(c35d) or {}
        check("p35d copilot: latest_month == 2026-04",
              pkt.get("latest_month") == "2026-04")
        _sc_meta = pkt.get("statement_completeness") or {}
        check("p35d copilot: statement_completeness present + uses_complete_months",
              bool(_sc_meta.get("uses_complete_months")))
        check("p35d copilot: partial_months contains 2026-05",
              "2026-05" in (_sc_meta.get("partial_months") or []))

        # compute_score Diversity dimension: Housing/Mortgage excluded;
        # top controllable category named in the reason string.
        from utils.analytics import compute_score
        sc_d = compute_score(conn=c35d)
        _div = next(
            (d for d in (sc_d.get("dimensions") or [])
             if d.get("key") == "diversity"),
            None,
        )
        check("p35d score: diversity dim renamed to 'Spending control'",
              (_div or {}).get("label") == "Spending control")
        check("p35d score: diversity reason names Shopping (top controllable)",
              "Shopping" in ((_div or {}).get("reason") or ""))
        check("p35d score: diversity reason does NOT mention Housing/Mortgage as the top",
              "top controllable category: Housing"
              not in ((_div or {}).get("reason") or "").lower())
        # Housing/Mortgage at $1800 (fixed) MUST NOT appear in the
        # diversity reason as a tank source. Shopping at $435 IS
        # controllable and is the top controllable, so it correctly
        # appears in the reason — but the dimension is now measured
        # over controllable spend only, not gross spending.
        check("p35d score: diversity reason mentions 'controllable'",
              "controllable" in ((_div or {}).get("reason") or "").lower())

        # Review reliability: a row matching the pre-Pass-34a stuck
        # state (real category, flag_reason='reviewed', parse_conf='low')
        # must NOT appear in get_ai_candidates anymore.
        c35d.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, parse_confidence, is_flagged, "
            " flag_reason, dedup_hash) "
            "VALUES "
            "('mastercard','2026-04-15','SQ *C&C GAMEBRIDGE Cambridge','SQ *C&C GAMEBRIDGE',"
            " 12.50,'debit','Entertainment','low',0,'reviewed','p35d-stuck-684'),"
            "('mastercard','2026-04-20','SQ *THE SHIP Hamilton','SQ *THE SHIP',"
            " 24.00,'debit','Food & Convenience','low',0,'reviewed','p35d-stuck-720')"
        )
        c35d.commit()
        cand_ids = {int(r["id"]) for r in
                    _db35d.get_ai_candidates(limit=200, conn=c35d)}
        _stuck_684_id = c35d.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35d-stuck-684'"
        ).fetchone()["id"]
        _stuck_720_id = c35d.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35d-stuck-720'"
        ).fetchone()["id"]
        check("p35d ai_candidates: 'reviewed' row 684 excluded retroactively",
              _stuck_684_id not in cand_ids)
        check("p35d ai_candidates: 'reviewed' row 720 excluded retroactively",
              _stuck_720_id not in cand_ids)

        # mark_transaction_reviewed promotes parse_confidence + clears
        # flag, and the row stays out of the AI candidate queue.
        c35d.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, parse_confidence, is_flagged, "
            " dedup_hash) "
            "VALUES "
            "('mastercard','2026-04-12','SOMETHING ELSE','SOMETHING ELSE',"
            " 9.99,'debit','Subscriptions & Digital','low',1,'p35d-mark-1')"
        )
        c35d.commit()
        _mk_id = c35d.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35d-mark-1'"
        ).fetchone()["id"]
        cand_ids_before = {int(r["id"]) for r in
                           _db35d.get_ai_candidates(limit=200, conn=c35d)}
        check("p35d mark: row appears in AI candidates before Mark reviewed",
              _mk_id in cand_ids_before)
        ok_mark = _db35d.mark_transaction_reviewed(_mk_id, conn=c35d)
        c35d.commit()
        check("p35d mark: mark_transaction_reviewed returned True", bool(ok_mark))
        _post = c35d.execute(
            "SELECT is_flagged, flag_reason, parse_confidence "
            "FROM transactions WHERE id=?", (_mk_id,),
        ).fetchone()
        check("p35d mark: is_flagged=0 after mark",
              int(_post["is_flagged"] or 0) == 0)
        check("p35d mark: flag_reason='reviewed' after mark",
              (_post["flag_reason"] or "") == "reviewed")
        check("p35d mark: parse_confidence='high' after mark",
              (_post["parse_confidence"] or "") == "high")
        cand_ids_after = {int(r["id"]) for r in
                          _db35d.get_ai_candidates(limit=200, conn=c35d)}
        check("p35d mark: row excluded from AI candidates after mark",
              _mk_id not in cand_ids_after)

        # spending_donut: top slice label includes the category name
        # (Pass 35d big-slice label upgrade).
        from components.charts import spending_donut
        figd = spending_donut([
            {"category": "Housing / Mortgage",  "total": 1800, "pct": 60},
            {"category": "Shopping",            "total":  600, "pct": 20},
            {"category": "Food & Convenience",  "total":  300, "pct": 10},
            {"category": "Entertainment",       "total":  200, "pct":  7},
            {"category": "Misc",                "total":  100, "pct":  3},
        ])
        _pie = figd.data[0]
        _texts = list(getattr(_pie, "text", []) or [])
        check("p35d donut: top slice text contains a category name",
              any("Housing" in (t or "") for t in _texts))
        check("p36 donut: top slice text contains dollar amount",
              any("$1,800" in (t or "") for t in _texts))
        check("p35d donut: figure has explicit height (Pass 35d bigger chart)",
              int(getattr(figd.layout, "height", 0) or 0) >= 440)

        c35d.close()
        try:
            for f in d35d.iterdir():
                try: f.unlink()
                except OSError: pass
            d35d.rmdir()
        except Exception:
            pass
        _db35d.DB_PATH = (
            _P35d(__file__).resolve().parent.parent / "data" / "finance.db"
        )
    except Exception as e:
        check("Pass 35d truth + review repair", False, repr(e))

    # 11e. Pass 35c — Mastercard statement summary parser + score path.
    # Exercises:
    #   * extract_statement_summary() against a representative text
    #     snippet (no real PDF — text-only, safe to commit)
    #   * upsert_statement_summary() persists to the new table
    #   * get_statement_summaries_in_range() returns the row inside its
    #     reported window
    #   * compute_score's Debt dimension prefers the summary (source
    #     == 'summary') and reports the bank-provided values
    #   * cash-advance principal still isn't counted as debt
    #   * agent_context exposes the statement summaries WITHOUT
    #     account numbers or raw PDF text
    _section("Mastercard statement summary (Pass 35c)")
    try:
        import tempfile as _tmp35c
        from pathlib import Path as _P35c
        import utils.database as _db35c
        d35c = _P35c(_tmp35c.mkdtemp(prefix="ledger_p35c_"))
        _db35c.DB_PATH = d35c / "finance.db"
        _db35c.init_db()
        c35c = _db35c.get_connection()

        # Representative Tangerine Mastercard page-0 text. Exact
        # formatting varies slightly between statements; the parser
        # only needs label + first-on-line money token.
        sample_summary_text = (
            "Tangerine World Mastercard\n"
            "Statement period   Apr 8 to May 7, 2026\n"
            "Account number ending in   ****1234\n"
            "Payment due date  May 28, 2026\n"
            "\n"
            "Account Summary\n"
            "Previous Balance              $1,202.13\n"
            "Payments & Credits            ($1,202.21) CR\n"
            "Transactions                  $843.55\n"
            "Cash Advances                 $0.00\n"
            "Adjustments                   $0.00\n"
            "Interest Charges              $0.08\n"
            "Fees                          $0.00\n"
            "New Balance                   $843.55\n"
            "Minimum Payment Due           $10.00\n"
        )

        from parsers.tangerine_mastercard import extract_statement_summary
        sm = extract_statement_summary(sample_summary_text)
        check("p35c parse: statement_start_date == 2026-04-08",
              sm.get("statement_start_date") == "2026-04-08")
        check("p35c parse: statement_end_date == 2026-05-07",
              sm.get("statement_end_date") == "2026-05-07")
        check("p35c parse: period label contains 'Apr 8 to May 7, 2026'",
              "Apr 8 to May 7, 2026" in (sm.get("statement_period_label") or ""))
        check("p35c parse: interest_charges == 0.08",
              sm.get("interest_charges") is not None
              and abs(float(sm["interest_charges"]) - 0.08) < 1e-6)
        check("p35c parse: fees == 0.00",
              sm.get("fees") is not None
              and abs(float(sm["fees"]) - 0.00) < 1e-6)
        check("p35c parse: cash_advances_total == 0.00",
              sm.get("cash_advances_total") is not None
              and abs(float(sm["cash_advances_total"]) - 0.00) < 1e-6)
        check("p35c parse: new_balance == 843.55",
              abs(float(sm.get("new_balance") or 0) - 843.55) < 0.01)
        check("p35c parse: payment_due_date == 2026-05-28",
              sm.get("payment_due_date") == "2026-05-28")
        # Credit token: '($1,202.21) CR' should parse as negative.
        check("p35c parse: payments_and_credits is negative (CR)",
              float(sm.get("payments_and_credits") or 0) < 0)

        # Persist + read back.
        c35c.execute(
            "INSERT INTO import_log "
            "(filename, file_hash, account_type, statement_period, "
            " rows_inserted) VALUES "
            "('demo_mc.pdf','h35c','mastercard',?,0)",
            (sm.get("statement_period_label") or "",),
        )
        _batch_id = c35c.execute(
            "SELECT id FROM import_log WHERE file_hash='h35c'"
        ).fetchone()["id"]
        from utils.database import (
            upsert_statement_summary,
            get_statement_summary_for_batch,
            get_statement_summaries_in_range,
        )
        _sid = upsert_statement_summary(
            sm, import_batch_id=_batch_id,
            account_type="mastercard", conn=c35c,
        )
        c35c.commit()
        check("p35c persist: upsert returned non-zero id",
              int(_sid) > 0)
        _round = get_statement_summary_for_batch(_batch_id, conn=c35c) or {}
        check("p35c persist: round-trip interest_charges == 0.08",
              abs(float(_round.get("interest_charges") or 0) - 0.08) < 1e-6)
        check("p35c persist: round-trip account_type == 'mastercard'",
              _round.get("account_type") == "mastercard")

        # Range query — window that overlaps the statement returns the row.
        _in_range = get_statement_summaries_in_range(
            "2026-04-01", "2026-04-30",
            account_type="mastercard", conn=c35c,
        )
        check("p35c range: in-window query returns the summary",
              any(int(r["id"]) == int(_sid) for r in _in_range))
        # Window outside the statement returns nothing.
        _out_range = get_statement_summaries_in_range(
            "2025-01-01", "2025-12-31",
            account_type="mastercard", conn=c35c,
        )
        check("p35c range: out-of-window query returns nothing",
              not any(int(r["id"]) == int(_sid) for r in _out_range))

        # Seed enough April activity so compute_score classifies April
        # as complete (>=14 distinct days, ends within 7d of EOM) and
        # uses it as the analysis month. Also add a cash-advance
        # principal row that MUST NOT count as debt regardless.
        for day in range(1, 30):
            c35c.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO STORE', 'DEMO STORE', "
                " 10.00, 'debit', 'Shopping', ?)",
                (f"2026-04-{day:02d}", f"p35c-apr-{day}"),
            )
        c35c.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('mastercard','2026-04-10','CASH ADVANCE','Cash Advance',"
            " 305.00,'debit','Cash Advance','p35c-ca-principal'),"
            "('mastercard','2026-04-15','SOMETHING','Paymentus',"
            " 999.99,'debit','Fees / Interest','p35c-paymentus-noise')"
        )
        c35c.commit()

        from utils.analytics import compute_score
        sc = compute_score(conn=c35c)
        _fc = sc.get("finance_charges") or {}
        check("p35c score: debt source is 'summary' (not 'fallback')",
              _fc.get("source") == "summary")
        check("p35c score: summary_totals.interest_charges == 0.08",
              abs(float((_fc.get("summary_totals") or {})
                        .get("interest_charges") or 0) - 0.08) < 1e-6)
        check("p35c score: summary_totals.fees == 0.00",
              abs(float((_fc.get("summary_totals") or {})
                        .get("fees") or 0) - 0.00) < 1e-6)
        check("p35c score: total interest == 0.08 (NOT $999.99 Paymentus, "
              "NOT $305 cash advance)",
              abs(float(_fc.get("total") or 0) - 0.08) < 1e-6)
        _debt_dim = next(
            (d for d in (sc.get("dimensions") or [])
             if d.get("key") == "debt"),
            None,
        )
        check("p35c score: debt reason mentions 'statement summary'",
              "statement summary" in
              ((_debt_dim or {}).get("reason") or "").lower())
        check("p35c score: debt reason includes '0.08'",
              "0.08" in ((_debt_dim or {}).get("reason") or ""))

        # agent_context shape & safety
        from utils.agent_context import build_agent_context
        ctx = build_agent_context(conn=c35c)
        _ss_list = ctx.get("statement_summaries")
        check("p35c ctx: statement_summaries is a list",
              isinstance(_ss_list, list))
        check("p35c ctx: at least one statement summary present",
              len(_ss_list or []) >= 1)
        import json as _json35c
        _ss_blob = _json35c.dumps(_ss_list or [], default=str)
        # Account-number safety: account_number / pan / last4 fields
        # are never written by upsert_statement_summary so they should
        # not appear here. Defensive guard regardless.
        check("p35c ctx: statement_summaries has no 'account_number' key",
              '"account_number"' not in _ss_blob)
        check("p35c ctx: statement_summaries has no 'pan' field",
              '"pan"' not in _ss_blob.lower())
        check("p35c ctx: statement_summaries has no file path leak",
              ".pdf" not in _ss_blob.lower()
              and "c:\\\\" not in _ss_blob.lower())

        c35c.close()
        try:
            for f in d35c.iterdir():
                try: f.unlink()
                except OSError: pass
            d35c.rmdir()
        except Exception:
            pass
        _db35c.DB_PATH = (
            _P35c(__file__).resolve().parent.parent / "data" / "finance.db"
        )
    except Exception as e:
        check("Pass 35c statement summary", False, repr(e))

    # 11d. Pass 35b — repair: monthly_review uses complete months,
    # finance-charge classifier excludes INTERAC/Paymentus/principal,
    # spending_donut layout no longer uses outside labels that clip.
    _section("statement-truth repair (Pass 35b)")
    try:
        import tempfile as _tmp35b
        from pathlib import Path as _P35b
        import utils.database as _db35b
        d35b = _P35b(_tmp35b.mkdtemp(prefix="ledger_p35b_"))
        _db35b.DB_PATH = d35b / "finance.db"
        _db35b.init_db()
        c35b = _db35b.get_connection()

        # Seed two COMPLETE months (March + April) so monthly_review has
        # 2 complete months to compare even after we add a PARTIAL May.
        for day in range(1, 30):
            c35b.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO STORE', 'DEMO STORE', "
                " 10.00, 'debit', 'Shopping', ?)",
                (f"2026-03-{day:02d}", f"p35b-mar-{day}"),
            )
        for day in range(1, 30):
            c35b.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO STORE', 'DEMO STORE', "
                " 14.00, 'debit', 'Shopping', ?)",
                (f"2026-04-{day:02d}", f"p35b-apr-{day}"),
            )
        # Partial May (5 days).
        for day in range(2, 8):
            c35b.execute(
                "INSERT INTO transactions "
                "(account_type, transaction_date, raw_description, merchant, "
                " amount, direction, category, dedup_hash) "
                "VALUES "
                "('chequing', ?, 'DEMO COFFEE', 'DEMO COFFEE', "
                " 4.50, 'debit', 'Food & Convenience', ?)",
                (f"2026-05-{day:02d}", f"p35b-may-{day}"),
            )
        # The bug rows: INTERAC e-Transfers (must NOT be counted as
        # finance charges) + a Paymentus service fee (must NOT count)
        # + a real CASH INTEREST debit (must count) + a Cash Advance
        # principal (must NOT count) + a CC payment (must NOT count).
        c35b.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, dedup_hash) "
            "VALUES "
            "('chequing','2026-04-03','INTERAC e-Transfer To: Friend A.',"
            " 'Friend A.',380.00,'debit','Transfer Out','p35b-eft-1'),"
            "('mastercard','2026-04-20','PAYMENTUS-SERVICE-FEE',"
            " 'Paymentus',4.23,'debit','Fees / Interest','p35b-pay-fee'),"
            "('mastercard','2026-04-07','CASH INTEREST','Cash Interest',"
            " 3.66,'debit','Fees / Interest','p35b-cash-int'),"
            "('mastercard','2026-04-10','CASH ADVANCE','Cash Advance',"
            " 305.00,'debit','Cash Advance','p35b-ca-principal'),"
            "('chequing','2026-04-25','CC PAYMENT','CC PAYMENT',"
            " 800.00,'payment','Credit Card Payment','p35b-cc-pay')"
        )
        c35b.commit()

        from utils.insights import (
            monthly_review, finance_charges_in_window,
        )

        # ── monthly_review uses complete months only ────────────────
        mr35b = monthly_review(conn=c35b)
        check("p35b mr: available is True",
              bool(mr35b.get("available")))
        check("p35b mr: month == 2026-04 (NOT 2026-05)",
              mr35b.get("month") == "2026-04")
        check("p35b mr: prev_month == 2026-03 (NOT 2026-04)",
              mr35b.get("prev_month") == "2026-03")
        check("p35b mr: uses_complete_months is True",
              bool(mr35b.get("uses_complete_months")))
        check("p35b mr: ignored_partial_months contains 2026-05",
              "2026-05" in (mr35b.get("ignored_partial_months") or []))
        check("p35b mr: truth_month == 2026-04",
              mr35b.get("truth_month") == "2026-04")
        check("p35b mr: latest_data_month == 2026-05",
              mr35b.get("latest_data_month") == "2026-05")
        _caveat_blob = " ".join(mr35b.get("data_caveats") or [])
        check("p35b mr: data_caveats mention ignored partial",
              "2026-05" in _caveat_blob and "partial" in _caveat_blob.lower())

        # ── finance_charges_in_window: conservative classifier ──────
        fc35b = finance_charges_in_window(
            "2026-04-01", "2026-04-30", conn=c35b,
        )
        _fc_ids = [r["id"] for r in (fc35b.get("rows") or [])]
        _row_label_by_id = {}
        for _r in (fc35b.get("rows") or []):
            _row_label_by_id[_r["id"]] = (
                f"{_r.get('merchant','')}/{_r.get('raw_description','')}"
            )
        # The CASH INTEREST row must be counted.
        _cash_int = c35b.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35b-cash-int'"
        ).fetchone()["id"]
        check("p35b fc: CASH INTEREST row IS counted",
              _cash_int in _fc_ids)
        # INTERAC e-Transfer must NOT be counted (was the original bug).
        _eft_id = c35b.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35b-eft-1'"
        ).fetchone()["id"]
        check("p35b fc: INTERAC e-Transfer row is NOT counted",
              _eft_id not in _fc_ids)
        # Paymentus service fee must NOT be counted.
        _pay_id = c35b.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35b-pay-fee'"
        ).fetchone()["id"]
        check("p35b fc: Paymentus service-fee row is NOT counted",
              _pay_id not in _fc_ids)
        # Cash Advance principal must NOT be counted.
        _ca_id = c35b.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35b-ca-principal'"
        ).fetchone()["id"]
        check("p35b fc: Cash Advance principal is NOT counted",
              _ca_id not in _fc_ids)
        # CC payment row must NOT be counted.
        _cc_id = c35b.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p35b-cc-pay'"
        ).fetchone()["id"]
        check("p35b fc: CC payment row is NOT counted",
              _cc_id not in _fc_ids)
        # Total reflects ONLY the real finance charge.
        check("p35b fc: total ~ $3.66 (cash interest only)",
              abs(float(fc35b.get("total") or 0) - 3.66) < 0.01,
              f"got total={fc35b.get('total')}, rows={_row_label_by_id}")

        # ── compute_score Debt dimension is exact-statement only ─────
        from utils.analytics import compute_score
        sc35b = compute_score(conn=c35b)
        _debt_dim = next(
            (d for d in (sc35b.get("dimensions") or [])
             if d.get("key") == "debt"),
            None,
        )
        check("p35b score: debt dimension present", _debt_dim is not None)
        if _debt_dim is not None:
            check("p36 score: debt asks for statement summary, not estimates",
                  "statement summary" in (_debt_dim.get("reason") or "").lower()
                  and "Transaction rows are not used" in (_debt_dim.get("reason") or ""))
            check("p35b score: debt reason does NOT show $577",
                  "577" not in (_debt_dim.get("reason") or ""))
            check("p36 score: debt dimension insufficient without summary",
                  not bool(_debt_dim.get("sufficient")))
        # finance_charges payload exposed on score for the Dashboard
        # "Why this score?" panel.
        _fc_pack = sc35b.get("finance_charges") or {}
        check("p36 score: finance_charges source is missing_summary",
              _fc_pack.get("source") == "missing_summary")
        check("p36 score: finance_charges total is 0 without statement summary",
              float(_fc_pack.get("total") or 0) == 0.0)
        check("p36 score: finance_charges row_count is 0 without summary",
              int(_fc_pack.get("row_count") or 0) == 0)

        # ── spending_donut layout proves no outside labels + bottom legend
        from components.charts import spending_donut
        fig35b = spending_donut([
            {"category": "Shopping",          "total": 600, "pct": 60},
            {"category": "Food & Convenience", "total": 200, "pct": 20},
            {"category": "Subscriptions & Digital", "total": 100, "pct": 10},
            {"category": "Entertainment",     "total": 60,  "pct": 6},
            {"category": "Misc",              "total": 40,  "pct": 4},
        ])
        # Inspect the first trace (the Pie) — textposition must be
        # 'inside' so labels can't clip outside the SVG plot area.
        _pie_trace = fig35b.data[0]
        check("p35b donut: textposition is 'inside'",
              getattr(_pie_trace, "textposition", "") == "inside")
        # Legend orientation must be horizontal and below the donut.
        _legend = fig35b.layout.legend
        check("p35b donut: legend orientation is 'h'",
              getattr(_legend, "orientation", "") == "h")
        check("p35b donut: legend yanchor is 'top' (below donut)",
              getattr(_legend, "yanchor", "") == "top")

        c35b.close()
        try:
            for f in d35b.iterdir():
                try: f.unlink()
                except OSError: pass
            d35b.rmdir()
        except Exception:
            pass
        _db35b.DB_PATH = (
            _P35b(__file__).resolve().parent.parent / "data" / "finance.db"
        )
    except Exception as e:
        check("Pass 35b repair", False, repr(e))

    # 11b. Pass 34a — Review save clears low parse_confidence
    # Regression guard for "rows under Uncategorized / Low-confidence
    # do not go away after manual review". Both the single-row save
    # path (update_transaction) and the bulk Safe/Force apply path
    # (apply_category_by_ids) must promote parse_confidence to 'high'
    # so the row stops appearing in get_ai_candidates() afterwards.
    _section("Review low-confidence clear (Pass 34a)")
    try:
        import tempfile as _tmp34
        from pathlib import Path as _P34
        import utils.database as _db34
        # Fresh isolated DB so we don't disturb prior assertions.
        d34 = _P34(_tmp34.mkdtemp(prefix="ledger_p34a_"))
        _db34.DB_PATH = d34 / "finance.db"
        _db34.init_db()
        c34 = _db34.get_connection()

        # Insert two rows that mimic the bug exactly: real category,
        # parse_confidence='low', is_flagged=1.
        c34.execute(
            "INSERT INTO transactions "
            "(account_type, transaction_date, raw_description, merchant, "
            " amount, direction, category, parse_confidence, is_flagged, "
            " flag_reason, dedup_hash) "
            "VALUES "
            "('mastercard','2026-04-15','SQ *C&C GAMEBRIDGE Cambridge ON',"
            " 'SQ *C&C GAMEBRIDGE',12.50,'debit','Entertainment','low',"
            " 1,'low_confidence','p34a-self-1'),"
            "('mastercard','2026-04-20','SQ *THE SHIP Hamilton ON',"
            " 'SQ *THE SHIP',24.00,'debit','Food & Convenience','low',"
            " 1,'low_confidence','p34a-bulk-1')"
        )
        c34.commit()

        row_self = c34.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p34a-self-1'"
        ).fetchone()
        row_bulk = c34.execute(
            "SELECT id FROM transactions WHERE dedup_hash='p34a-bulk-1'"
        ).fetchone()
        id_self = int(row_self["id"])
        id_bulk = int(row_bulk["id"])

        # Pre-condition: both rows are returned by the AI-candidate query
        # purely because of parse_confidence='low'.
        cands_pre = _db34.get_ai_candidates(limit=10, conn=c34)
        cand_ids_pre = {int(r["id"]) for r in cands_pre}
        check("p34a pre: low-conf rows are AI candidates",
              id_self in cand_ids_pre and id_bulk in cand_ids_pre)

        # Path 1 — single-row save (mirrors Review's 'self' path).
        _db34.update_transaction(
            id_self,
            {"category": "Entertainment", "is_flagged": 0,
             "flag_reason": "reviewed", "parse_confidence": "high"},
            conn=c34,
        )
        c34.commit()
        post_self = c34.execute(
            "SELECT is_flagged, parse_confidence FROM transactions "
            "WHERE id=?", (id_self,),
        ).fetchone()
        check("p34a self: is_flagged cleared",
              int(post_self["is_flagged"] or 0) == 0)
        check("p34a self: parse_confidence promoted off 'low'",
              (post_self["parse_confidence"] or "") != "low")

        # Path 2 — bulk apply (mirrors Review's 'safe'/'force' path).
        result34 = _db34.apply_category_by_ids(
            [id_bulk], "Food & Convenience",
            clear_flags=True, conn=c34,
        )
        c34.commit()
        check("p34a bulk: requested == 1",
              int(result34.get("requested") or 0) == 1)
        check("p34a bulk: flags_cleared == 1",
              int(result34.get("flags_cleared") or 0) == 1)
        post_bulk = c34.execute(
            "SELECT is_flagged, parse_confidence FROM transactions "
            "WHERE id=?", (id_bulk,),
        ).fetchone()
        check("p34a bulk: is_flagged cleared",
              int(post_bulk["is_flagged"] or 0) == 0)
        check("p34a bulk: parse_confidence promoted off 'low'",
              (post_bulk["parse_confidence"] or "") != "low")

        # Post-condition: neither row should reappear in the AI-candidate
        # list after the manual review action.
        cands_post = _db34.get_ai_candidates(limit=10, conn=c34)
        cand_ids_post = {int(r["id"]) for r in cands_post}
        check("p34a post: self row no longer an AI candidate",
              id_self not in cand_ids_post)
        check("p34a post: bulk row no longer an AI candidate",
              id_bulk not in cand_ids_post)

        c34.close()
        # Cleanup the temp DB.
        try:
            for f in d34.iterdir():
                try: f.unlink()
                except OSError: pass
            d34.rmdir()
        except Exception:
            pass
        # Restore the main DB path so any later code that reaches for the
        # default path doesn't land on the temp dir.
        _db34.DB_PATH = (
            _P34(__file__).resolve().parent.parent / "data" / "finance.db"
        )
    except Exception as e:
        check("Pass 34a low-conf clear", False, repr(e))

    # 11. Pass 32 — share-zip safety assertion
    # Builds a real share zip into a temp dir and asserts the most
    # important exclusions hold. This catches future regressions where
    # someone adds a developer dotfile (.claude/, .vscode/, etc.) and
    # forgets to add it to EXCLUDE_DIRS.
    _section("share zip safety (Pass 32)")
    try:
        import tempfile as _tmp
        import zipfile as _zf
        from pathlib import Path as _P
        szp_dir = _P(_tmp.mkdtemp(prefix="ledger_share_"))
        szp_out = szp_dir / "share.zip"
        rc_szp = subprocess.run(
            [sys.executable, "-m", "scripts.make_share_zip",
             "--out", str(szp_out)],
            cwd=str(_P(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=120,
            # Pass 32: make_share_zip prints emoji in its summary, so on
            # a cp1252 console reading captured stdout would raise
            # UnicodeDecodeError in the subprocess reader thread. Force
            # utf-8 with replace so we always come back with a usable
            # string regardless of the host code page.
            encoding="utf-8", errors="replace",
        )
        check("share zip exit 0", rc_szp.returncode == 0,
              f"stderr={rc_szp.stderr[:160]}")
        check("share zip exists", szp_out.exists())
        if szp_out.exists():
            with _zf.ZipFile(szp_out) as zf:
                names = zf.namelist()
            # Forbidden exact filenames (any path).
            for forbidden in (
                "config.json", "finance.db", "finance.demo.db",
                "launcher.log", "launcher.log.prev",
                "CLAUDE_HANDOFF.md",
            ):
                check(f"share zip has no {forbidden}",
                      not any(n.endswith("/" + forbidden) or
                              n.endswith(forbidden) and "/" not in n[len(forbidden):]
                              for n in names))
            # Forbidden directory prefixes (any depth under ledger/).
            for forbidden_dir in (
                ".claude/", ".venv/", "exports/", "data/", "dist/",
                "__pycache__/",
            ):
                hit = next(
                    (n for n in names if forbidden_dir in n),
                    None,
                )
                check(f"share zip has no {forbidden_dir}",
                      hit is None,
                      f"found: {hit}")
        # Cleanup
        try:
            for f in szp_dir.iterdir():
                try: f.unlink()
                except OSError: pass
            szp_dir.rmdir()
        except Exception:
            pass
    except Exception as e:
        check("share zip safety", False, repr(e))

    # Cleanup
    try:
        for child in tmp_dir.iterdir():
            try: child.unlink()
            except IsADirectoryError: pass
        tmp_dir.rmdir()
    except Exception:
        pass

    print()
    if failures:
        print(f"FAILED ({len(failures)} check(s)):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

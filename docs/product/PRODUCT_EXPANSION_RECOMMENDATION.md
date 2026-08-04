# Product Expansion Recommendation

**Date:** 2026-07-17 · Decision document, not an audit. Follows `ADOPTION_AUDIT.md`,
`NEXT_PRODUCT_RECOMMENDATION.md`, and `CODEX_CATEGORIZATION_PLAN.md` (all shipped).

## 1. Strongest current capabilities

The weekly loop is genuinely complete for a Tangerine user: import → persistent
completion panel → materiality-gated review (now with normalized merchant groups, saved
rules with provenance, and safe batch undo) → weekly check-in with one verdict, one Safe
to Spend, calendar-month plans that roll forward in one click, and one target number per
category everywhere. The deterministic engine layer and 69-test suite are the product's
moat.

## 2. Largest remaining barriers to broader use

1. **You cannot put money into Ledger except through a recognized file.** No manual
   transaction entry at all — no cash spending, no opening balances, no "my bank isn't
   supported yet" fallback. This is disqualifying for any non-Tangerine user and limiting
   even for the owner (cash, e-transfers between institutions).
2. **CSV ingestion is alias-or-nothing.** `parse_csv` auto-maps ~20 common header
   aliases; when a bank's export doesn't match, rows silently error out and the user has
   no recourse. No user-assisted mapping, no memory of a mapping that worked.
3. **No first-class accounts model.** `account_type` strings (chequing/savings/
   mastercard) work for one institution; multiple accounts of the same type, loans, or
   cash need a real `accounts` table (deferred — see §13).
4. Smaller: goals live inside Plan rather than as a visible destination; onboarding is a
   three-card info panel rather than a guided path.

## 3. Target user and core promise

Someone who wants to know, in five minutes a week, whether they're okay, what they can
spend, and one thing to do — **without giving their bank credentials to anyone**. Promise:
*"Bring your data however you can get it — statements, CSV exports, or typing it in —
and Ledger keeps the weekly answer honest."* Privacy-first, file-based, local. That
positioning matches the one durable advantage over Monarch/Copilot-class products.

## 4. Primary navigation (current five are right)

Home · **Add Data** (rename of "Update"; imports + manual entry) · Plan · Net Worth ·
Settings — with Analysis as the drill-down group. No page count changes needed; the nav
decision from the adoption pass holds.

## 5. Weekly and monthly workflows

Unchanged — they work. Weekly: freshness → verdict → Safe to Spend → changes → 1–2
actions → finish. Monthly: one-click plan confirm (rollover). The expansion must feed
these loops, never add parallel ones.

## 6. Institution-independent import architecture (layered)

```
1 file-type detection            (exists: detect.py)
2 institution/format detection   (exists for Tangerine PDFs + CSV probe)
3 parse → canonical tx model     (exists: the transaction dict everything shares)
4 preview + validation           (exists: batch preview, per-file stats)
5 user-assisted column mapping   (THIS PASS — when the CSV probe fails)
6 account selection/creation     (deferred: accounts table — Codex)
7 duplicate detection            (exists: dedup_hash)
8 deterministic classification   (exists: protected → user rules → static)
9 import confirmation            (exists: atomic persist_import_batch)
10 reusable import profiles      (THIS PASS — header-signature matched)
```

**PDFs:** keep purpose-built adapters only (Tangerine today; each new institution is a
deliberate adapter, never guesswork extraction). Unsupported PDFs get an honest path:
bank CSV export guidance → column mapping → or manual entry. **Manual entry** becomes a
first-class source: entries persist through the same atomic batch path as files, so they
get dedup, provenance, undo, and the completion panel for free.

*Convention basis:* user-driven column mapping is how Actual Budget and Lunch Money
solve the same problem ([Actual importing docs](https://actualbudget.org/docs/transactions/importing/),
[Lunch Money CSV guide](https://support.lunchmoney.app/guides/import-via-csv)); saved
reusable mappings are the requested-but-missing feature there
([actual#3886](https://github.com/actualbudget/actual/issues/3886)) — Ledger can ship
them matched automatically by header signature. Judgment: mapping UI only appears when
the automatic probe fails; a working auto-import stays zero-decision.

## 7. Budgeting and savings model

Keep the shipped model: expected income − fixed commitments − subscriptions − fixed-dollar
savings target − fee reserve − buffer = Safe to Spend, with one-click monthly rollover and
three plain stances. Next increments (not now): true-expense/sinking-fund reserve line
(one table, one subtraction), payday-aware pacing for biweekly income. Full zero-based
budgeting remains rejected — the evidence base from the adoption audit stands.

## 8. Investment scope

What exists (manual holdings, contributions, snapshots, mixed-currency warnings,
milestones) is already the right scope: visibility, not trading. Additions worth having
later: growth-vs-deposits split and registered/non-registered tagging. Nothing in this
area blocks broader use; leave it.

## 9. Restrained gamification principles

Evidence only, calm by default: check-in completion history (exists), verified-
cancellation wins ("X hasn't charged since May"), months-met-savings-target count, fees
avoided vs prior months. No streak-breaking, no confetti, no engagement mechanics. The
existing Tiny Wins/momentum engines stay retired from primary surfaces.

## 10. Merge / hide / defer / retire

No changes this pass — the previous passes already did this work. Reaffirmed: Money
Pulse stays secondary; Mission Deck/Ask Ledger stay off Home; advanced plan modes stay
behind their expander.

## 11. Three highest-value next slices

1. **Generalized Add Data** — manual entry + CSV mapping + reusable profiles + honest
   unsupported-file paths. (Unblocks every non-Tangerine user; smallest data-model risk.)
2. **Accounts model** — first-class `accounts` table, balances, per-account views,
   account assignment on import. (Schema-heavy, mechanical — ideal Codex work once
   specced.)
3. **Goals & savings surface** — promote goals out of the Plan tab; link savings-target
   progress and sinking funds to Home's Monthly Progress.

## 12. Slice implemented now: Generalized Add Data

Chosen because it is the single barrier that makes Ledger unusable for anyone else, it
reuses the atomic import path Codex just hardened (low regression risk), and it requires
only one additive table (`import_profiles`). Accounts (slice 2) is bigger and purely
mechanical after this; goals (slice 3) is valuable but no one is blocked by it.

## 13. Left for Codex

Accounts model (schema + assignment UI + per-account filters); true-expense reserve
line; growth-vs-deposits investment split; additional institution PDF adapters as
real statements become available; packaging spike per `DESKTOP_READINESS.md`.

## 14. Must be proven before desktop packaging

Real-data acceptance by the owner (Tangerine PDFs + at least one foreign CSV through
mapping), migration safety on an aged database (exists: hardened migrations + tests),
backup/restore path, and data-directory relocation (`DESKTOP_READINESS.md` §database).

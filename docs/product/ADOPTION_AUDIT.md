# Ledger Adoption Audit

**Date:** 2026-07-17
**Method:** Full code inspection (app routing, all 14 pages, database layer, planner, insights,
analytics, parsers, AI layer, demo generator, docs) + live demo-mode session driven in a browser
(every primary page visited, states and copy captured) + product research with sources.
**Goal:** answer one question — *what is the smallest, clearest version of Ledger that Ben would
actually open every week?*

---

## Executive summary

Ledger is not under-built. It is **over-built and under-decided**. The math layer (deterministic
packets, statement-truth months, transfer exclusion, exact-statement fees) is genuinely better
than most commercial apps. What's broken is the product surface on top of it:

1. **The app argues with itself.** In one demo-mode screenful the Dashboard says
   *Money Pulse 84/100 — Excellent* and *31.2% saved*, while two sections away Money Runway says
   *Safe to spend −$783 — Danger* and the plan banner says *projected net −$560 · risk: danger*,
   while Mission Options says *"No urgent fires… keep it steady."* Three verdicts, three time
   windows, no hierarchy. A user who can't tell whether they're okay stops opening the app.
2. **Same advice, four places, three different numbers.** The "cut Groceries" recommendation
   appears on Dashboard (Copilot), Money Moves, Reduce, and Plan — with three different targets
   ($432, $529, $576) computed from different windows. Repetition destroys trust in all of them.
3. **There is no finish line.** Nothing tells you the weekly review is *done*. Every page offers
   more analysis, more expanders, more CTAs. A 5-minute check-in requires the app to end.
4. **Stale data is silent.** Demo data ends 2026-06-29; today is 2026-07-17. No surface says
   "your data is 18 days old — here's the one thing to do." The freshness problem *is* the
   habit problem: the app's usefulness decays between statements and it never tells you.

The single most important product decision: **build one Home screen that renders a verdict, a
safe-to-spend number, what changed, and 1–2 actions — and declares the check-in complete.**
Everything else becomes drill-down.

---

## 1. Current Product Map

### The current end-to-end journey (as built)

Import statements → transactions auto-categorized + flagged → Review queue cleanup →
Dashboard (analysis) → Plan (monthly targets) → Reduce (cuts) → Reports/Trends/Spending/Income
(deep analysis) → Net Worth (snapshots) → Settings (rules/budgets/AI).

Fourteen sidebar destinations in three groups ("Ledger" 8, "Reports" 4, "Tools" 1, plus
Diagnostics in dev mode).

### Page-by-page assessment

| Page (file) | Purpose | Decision it serves | Verdict |
|---|---|---|---|
| **Dashboard** (`pages/1_Dashboard.py`, 1,019 lines) | Everything: KPIs, Runway, Mission Deck, Tiny Wins, category teaser, 2 chart sections, Pulse gauge + 2 score expanders, Copilot, Mission Options, Weekly Review expander, budget warnings, recs nudge, subs nudge, Insights, Ask Ledger | Supposedly "am I okay?" — actually answers 12 questions at equal volume | **Rebuild.** ~18 stacked sections, ~15 CTAs. This is a report, not a tool. |
| **Import** (`3_Import.py`) | Multi-file upload, auto-detect, dedup, batch results, watch folder, history | "Get data in" | **Keep as primary.** Genuinely good: per-file confidence, already-imported badges, statement-summary capture, removable batches. Needs only an "what changed since last import" summary. |
| **Transactions** (`2_Transactions.py`) | Search/filter list + edit | "Find and fix a transaction" | **Keep, fix editing.** Edit flow requires typing a numeric transaction ID into a form — developer UX. Filter chips are good. |
| **Review queue** (`8_Review.py`, 1,102 lines) | Flagged-transaction triage with rules learning | "Resolve uncertainty" | **Merge into Transactions** as a filter/tab. The queue mechanics (teach-rule checkbox, bulk clear) are good; the separate destination + MiniMax triage panel is overhead for a queue that had 1 item in demo. |
| **Reduce** (`11_Reduce.py`) | Cut targets, cancel candidates, 3-cut picker, challenge | "What do I cut?" | **Keep as the action drill-down**, merge Money Moves into it. Strong page buried below the fold of the app. |
| **Plan** (`12_Month_Plan.py`) | 7 plan modes, forecast, goals, bills | "Set this month's targets" | **Simplify.** 4 tabs × mode-picker × three coach blocks. Proposal numbers and saved-plan numbers render simultaneously (demo: income target $5,049 shown above Plan Coach's "target income 6,400"). Goals + Bills content is good. |
| **Net Worth** (`7_Investments.py`) | Snapshots, holdings CSV, balances | "Am I building wealth?" | **Keep as primary**, low frequency. Monthly snapshot ritual fits the model. |
| **Reports** (`13_Reports.py`) | Hub + monthly review card | Routing | **Keep the monthly-review card** (it's the best single block in the app); the hub becomes the "Explore" section landing. |
| **Trends** (`4_Trends.py`) | "What changed" lead + 5 tabs | "What changed?" | **Promote the lead block to Home**; page stays as drill-down. |
| **Spending / Income** (`5/6`) | Deep breakdowns | Analysis | **Secondary** (Explore). Fine as-is. |
| **Money Moves** (`10_Recommendations.py`) | Ranked rec cards, snooze | "What should I act on?" | **Retire as a destination; merge into Reduce.** Its 8 demo recs were ~all "reduce category X" — the same content as Reduce with different numbers. |
| **Settings** (`9_Settings.py`, 1,462 lines) | 9 sections behind a 4-card landing | Config | **Keep**, already improved. Profiles + score weights are enthusiast features that belong under Advanced (they are). |
| **Diagnostics** (`14_Diagnostics.py`) | Dev health | Dev | **Keep dev-gated** (already is). |

### Technically impressive but weakly contributing to weekly use

- **Money Pulse score machinery** (weights UI, per-dimension breakdowns ×2 expanders, confidence
  badge): a 0–100 score is a *report card*, not a decision. It also directly caused the
  Excellent-vs-Danger contradiction. Valuable as a monthly summary; harmful as a headline.
- **7 plan modes** (normal/tight/reset/aggressive_save/sub_cleanup/debt_recovery/stabilize):
  config-menu energy for a decision that is really "steady / tighter / recovering."
  (Already partially recognized — Pass 31 hid 4 behind "Advanced.")
- **Mission Deck + Mission Options + Tiny Wins + Copilot + Weekly Review + Ask Ledger** —
  six motivational/advisory surfaces on one page, each with its own framing and cache.
- **Scenario simulator, XP/momentum system** (`utils/momentum.py` — Dashboard surface already
  removed in Pass 30, engine remains), **profiles**, **score weights**: enthusiast depth with
  near-zero weekly pull.

---

## 2. Friction Audit

Ranked by (impact on actual usage × frequency), with effort and confidence. Everything below
was observed directly in the demo session or the code, not inferred from README claims.

### P0 — Reasons the app doesn't get opened

| # | Problem | Evidence | Impact | Freq | Fix effort | Confidence |
|---|---|---|---|---|---|---|
| 1 | **Contradictory verdicts above the fold.** Score (complete-month window) says Excellent; Runway (current partial month) says Danger; KPI strip (rolling 90 days incl. 18 empty days) says 31.2% saved; Missions say "no urgent fires." | Demo Dashboard, one screenful | Kills trust — the core question "am I okay?" gets three answers | Every open | Medium (one verdict source) | High |
| 2 | **No completion state.** No "review done" anywhere; every surface offers more. | All pages | The 5-minute loop can't exist without an end | Every open | Medium | High |
| 3 | **Stale data is silent.** Data ends 06-29, app rendered on 07-17 with no prominent freshness banner; anchor-date caveat lives inside a collapsed "Data caveats" expander on Plan. | Demo session | The app feels dead between imports; user opens it, sees old numbers, stops opening it | Weekly | Low | High |
| 4 | **Dashboard is a report, not a tool.** ~18 sections, ~15 CTAs, two charts of the same data (top-5 teaser + bar + donut), two score-breakdown expanders. | `1_Dashboard.py` + demo | Overwhelm → avoidance | Every open | High (rebuild) | High |

### P1 — Trust and correctness problems visible in the running app

| # | Problem | Evidence | Impact | Fix effort | Confidence |
|---|---|---|---|---|---|
| 5 | **Rendered escaping bugs.** Literal `\$` backslashes throughout Plan ("~\$0/day"), Reduce (every card), Net Worth ("Next milestone: \$20,000"); LaTeX mangling turns "$54/mo · $645/yr" into math glyphs ("54 / 𝑚 𝑜 ⋅") and "$15 lunch saved is $45" into symbol soup. Root cause: `.replace("$", r"\$")` applied inside `unsafe_allow_html` blocks (escape is for st.markdown text mode, not HTML). | Demo pages | Looks broken → "can I trust the math?" | Low | High |
| 6 | **Daily pace bug.** "Daily pace $-783/day" — when `days_left == 0`, `daily_amount = amount` (`insights.py:2875`). A per-day figure equal to the month total is nonsense. | Demo Dashboard; `money_runway()` | Headline number wrong at month-end | Low | High |
| 7 | **Nonsense mission copy.** "If daily spend is over $1, then I will pause one flexible category" — threshold clamps to $1 when safe-to-spend is negative. | Demo Mission Deck | Advice reads auto-generated → ignored | Low | High |
| 8 | **Three Groceries targets at once.** Mission: keep under $432; Reduce/Money Moves/Copilot: $529 (90-day avg $661 × 0.8); Plan: $576 (3-mo avg $720 × 0.8). Different windows, same week, no reconciliation. | Demo, cross-page | User can't tell which target is "the" target | Medium | High |
| 9 | **Mixed time frames with no labels a human would parse.** KPI strip: rolling 90 days ending today; Pulse: last complete statement month; Runway: current partial month; Trends: calendar months. | Demo + code | "Better or worse than last month?" has no single answer | Medium | High |

### P2 — Repetition, dead weight, and paper cuts

| # | Problem | Evidence | Impact | Effort |
|---|---|---|---|---|
| 10 | Same recommendation engine surfaced 4× (Copilot moves, Money Moves, Reduce, Plan next-moves) | Demo | Noise; navigation overload | Medium (merge) |
| 11 | 14 nav destinations for ~5 jobs | `app.py` | Decision fatigue at every open | Medium |
| 12 | "Enable AI in Settings" nag appears on ~6 surfaces when AI is off | Demo | Feels like an upsell inside your own app | Low |
| 13 | Transaction editing by typing a numeric ID | `2_Transactions.py` demo | Corrections feel like DB admin | Medium |
| 14 | Review triage panel narrates 1 flagged item with a 6-line "MiniMax triage" block | Demo | Ceremony around a 10-second task | Low |
| 15 | Plotly deprecation warnings (`use_container_width`) on every page load | Server logs | Future breakage debt | Low |
| 16 | Requirements unpinned (`>=`), no lockfile | `requirements.txt` | A Streamlit release can break the app | Low |
| 17 | Demo-vs-real gap: demo data is clean, complete, and regular; real Tangerine data will have partial months, refunds, e-transfer noise — exactly the states with the weakest UI (caveats hidden in expanders) | `create_demo_data.py` vs insight code paths | The app demos better than it lives | — (design constraint) |

### Language audit

Branded concepts the user must learn: Money Pulse, Money Runway, Mission Deck, Tiny Wins,
Money Moves, Found Money, Plan Coach, Forecast Coach, Goal Progress Coach, Ledger Copilot,
Ask Ledger, Trim-Spend Challenge. That's a dozen proper nouns for maybe four real ideas
(status, safe-to-spend, suggested actions, explanations). Every invented noun is a small tax
on re-opening the app after two weeks away.

---

## 3. Simplified Core Workflow

### The weekly check-in (target: under 5 minutes)

```
1. OPEN      → Home says how fresh the data is.
              If new statements are available in the watch folder → one-click import.
2. RESOLVE   → Only money-material uncertainties (≥ $25 impact or affects a bill/
              subscription classification). Everything else defers silently.
3. VERDICT   → One status line: On track / Tight / Behind / Not enough data — with
              the one sentence of why.
4. SPEND     → Safe-to-spend until month end, honest about confidence.
5. CHANGED   → Two or three deltas vs the comparable period, only if meaningful.
6. ACT       → One or two suggested actions max. Accept, snooze, or skip.
7. DONE      → "Review complete — see you next week." Timestamp saved. App visibly ends.
```

- **Landing screen** = Home (below, §5). Above the fold: freshness, verdict, safe-to-spend,
  primary action. Nothing else.
- **Primary action** = whatever unblocks the verdict: import (if stale) → resolve (if material
  uncertainties) → the top suggested action (otherwise).
- **Monthly flow** (first check-in after a month closes): the check-in gains two steps —
  month report card (this is where the Pulse-style score belongs) and plan confirmation for
  the new month (pre-filled from last month; one click to accept, edit only if desired).
- **Corrections policy:** immediate = transactions ≥ $25 miscategorized into/out of
  bills/subscriptions/income, unmatched transfers, possible duplicates. Deferred = everything
  else (persist in the queue; surface count quietly, never block the verdict).
- **Persistence:** a `review_log` row (date, data-through date, actions chosen). Home greets
  with "Last check-in: Jul 10 · data through Jun 29." Streaks can come later — the timestamp
  is the minimum.
- **No new statement:** say exactly that — "No new data since your last check-in. Nothing to
  review. Next Tangerine statement usually lands ~Aug 8" (inferable from `import_log`
  cadence). Don't re-render old analysis as if it were news.
- **Incomplete/uncertain data:** the verdict downgrades honestly ("Not enough data — last
  complete month is May") rather than emitting confident numbers from partial windows.
  The `statement_coverage` machinery for this already exists and is good; it's the display
  policy that's missing.

---

## 4. Navigation Reduction Proposal

From 14 destinations to **5 + Settings**:

| New destination | Absorbs | Role |
|---|---|---|
| **1. Home** (new) | Dashboard's verdict/runway/actions/what-changed duties; Weekly Review; Mission surfaces | The weekly check-in. Default page. |
| **2. Activity** | Transactions + Review queue (as a "Needs review (n)" tab/filter) | Find, fix, resolve. |
| **3. Plan** | Month Plan (simplified) + Goals + Bills | Monthly targets, bills, goals. |
| **4. Net Worth** | unchanged | Monthly wealth ritual. |
| **5. Explore** | Reports hub + Spending, Income, Trends, and "Save money" (= Reduce + Money Moves merged) | All deep analysis, one roof. |
| **⚙ Settings** | unchanged (4-card landing already good) + Import tools | Config. |

Per-page dispositions and tradeoffs:

- **Dashboard → retire; Home replaces it.** Tradeoff: the charts (donut/bar/top merchants)
  leave the landing page. They move to Explore→Spending, which already renders them. Loss:
  chart-lovers scroll one level deeper. Gain: the landing page answers its question.
- **Import → merges into Home's freshness step + stays reachable from Activity.** The full
  import page (batch preview, history) survives; it's just no longer a *daily* destination.
- **Review → merge into Activity.** Tradeoff: the dedicated triage narrative goes; the queue,
  rule-teaching, and bulk actions stay. One fewer place where "fix your data" lives.
- **Reduce + Money Moves → merge as Explore→"Save money".** One engine, one surface, one set
  of numbers. Tradeoff: Money Moves' snooze-state machinery must carry over (it's the better
  interaction model; Reduce's presentation is the better layout).
- **Trends/Spending/Income → Explore.** Unchanged content. The Trends "What changed" lead
  block gets promoted to Home; the page remains for depth.
- **Plan → simplify in place** (§6): 3 stances instead of 7 modes; coaches collapse to one.
- **Money Pulse → moves inside the monthly report card** (Explore→Monthly review + the
  monthly check-in step). It stops being a headline KPI. Tradeoff: the gauge leaves the front
  page — deliberately, because it was the loudest contradictory voice.
- **Diagnostics → unchanged** (dev-gated).

Progressive disclosure rule: any number on Home must expand to its evidence ("why?") in one
click, and any evidence view must link to its transactions. That's the existing
`set_transaction_search` pattern — keep it, it works.

---

## 5. Dashboard (Home) Redesign Specification

### Priority stack (answers in order, top to bottom)

```
┌──────────────────────────────────────────────────────────────┐
│ 1  FRESHNESS BAR   "Data through Jun 29 · 18 days behind"    │  ← state-dependent:
│    [Import July statement]              Last check-in Jul 10 │    hidden when fresh
├──────────────────────────────────────────────────────────────┤
│ 2  VERDICT         ● TIGHT THIS MONTH                        │  ← one status, one
│    "June closed +$1,238 saved (25%). July is projected      │    sentence, one color
│     -$560 against your plan — mostly Groceries pace."        │
├──────────────────────────────────────────────────────────────┤
│ 3  SAFE TO SPEND   $312 until Jul 31   ≈ $22/day             │  ← THE number.
│    after bills ($1,970), savings goal ($1,200), buffer       │    "why?" expands the
│    confidence: medium — 1 statement still to import          │    formula (exists)
├──────────────────────────────────────────────────────────────┤
│ 4  WHAT CHANGED    vs May:  Spending ▼ $204   Net ▲ $205     │  ← max 3 deltas,
│    Gas ▲ $30 (+13%) · Shopping ▼ $170 (−38%)     [Details →] │    material only
├──────────────────────────────────────────────────────────────┤
│ 5  THIS WEEK       1. Cancel DEMO NEWSPAPER  (~$190/yr)      │  ← 1–2 actions max,
│                    2. Groceries pace: $672 vs $530 target    │    from ONE engine
│                    [Do it] [Snooze] [Skip]                   │
├──────────────────────────────────────────────────────────────┤
│ 6  ✓ Finish check-in                                         │  ← completion; writes
│    "3 uncategorized under $25 waiting — handle anytime"      │    review_log
└──────────────────────────────────────────────────────────────┘
```

Answers, in priority order: (1) Am I okay → Verdict. (2) What can I spend → Safe to spend.
(3) On track to save → inside Verdict sentence + Plan link. (4) What changed → deltas.
(5) Where am I overspending → the pace item inside This Week. (6) Best action today → This Week.

### Branded-concept rulings

| Current | Ruling | Rationale |
|---|---|---|
| Money Runway | **Rename → "Safe to spend."** Keep the engine (`money_runway()` is the best code in the app). | Plain words beat brand. Runway status colors survive as the verdict color. |
| Money Pulse | **Demote + rename → "Monthly score" inside the monthly report card.** | A score is retrospective; it must never sit next to a forward-looking danger signal at equal weight. |
| Mission Deck / Mission Options / Tiny Wins / Found Money | **Collapse into "This week" (1–2 actions).** Retire the names. | Six motivation surfaces → one. Fewest, best actions. |
| Ledger Copilot / Weekly Review / Ask Ledger | **Fold into one optional "Explain this" affordance** on the verdict + monthly card. | AI narrates the packet on demand; it stops being three page sections. |
| Money Moves | **Merge into Explore→Save money.** | Same engine as Reduce. |
| Trim-Spend Challenge, Plan/Forecast/Goal "Coaches" | **Cut copy; keep the numbers where they land.** | Ceremony reduction. |

### States

- **Empty (no data):** current 3-step onboarding is decent; reduce to one primary CTA
  (Import) + demo-mode offer.
- **Stale:** freshness bar becomes the top element with the single Import CTA (watch-folder
  detection already exists — surface it here).
- **Partial month:** verdict says "based on data through Jun 29"; safe-to-spend shows
  confidence level (field already computed).
- **No plan saved:** safe-to-spend still renders (baseline-month income fallback already in
  `money_runway()`); a quiet "Confirm July plan (1 min)" chip appears — never a blocker.
- **Error:** any packet failure renders as "couldn't compute X — [see Diagnostics]" rather
  than a stack trace or silent omission (Dashboard's current bare `except Exception` swallows).

### Interactions, narrow windows, copy

- Every number → "why?" expander → evidence → transaction links (pattern exists; keep).
- Single-column layout; the 6 blocks stack cleanly at narrow width (Streamlit columns only
  inside blocks 4–5).
- Copy tone: descriptive, never judgmental. "Groceries is pacing $140 over target" not
  "you overspent." Research is unambiguous that guilt-framing drives abandonment (§ Sources).
- No red walls: red is reserved for the verdict dot in a genuinely negative month; deltas use
  neutral arrows + one accent.

---

## 6. Budgeting and Savings Model

### Assessment of the current model

The planner is closer to right than the UI suggests: `generate_starter_plan` already derives
income/spending/savings targets from 3-month history, `bills_and_commitments` already splits
locked (fixed + active subs) from watch-only variable merchants — the "truth layer" note on the
Bills tab is exactly correct. Problems: 7 modes where 3 stances suffice; per-category targets
generated for ~every category (micromanagement Ben says he won't do); savings target isn't
connected to a named goal; true (annual/irregular) expenses have no home; and the resulting
safe-to-spend disagrees with Runway's (two formulas: `forecast_month` vs `money_runway`).

### Recommended model: Bills + Goals + One Flexible Allowance

Matches Monarch-style flex budgeting and PocketGuard-style "in my pocket" — the two patterns
that work for people who dislike assigning every dollar (§ Sources) — while keeping Ledger's
statement-truth discipline.

**Monthly setup (once, mostly pre-filled):**

```
expected_income      = plan income (default: median of last 3 complete months' true income)
fixed_bills          = Σ locked commitments (existing bills_and_commitments: fixed + active subs)
true_expense_reserve = Σ(annual/irregular items ÷ months between occurrences)   ← NEW, small table
savings_target       = user-set number, linked to a goal (existing goals table)
debt_reserve         = statement interest+fees, exact-statement only (existing finance_charges)
buffer               = clamp(5% × expected_income, $25, $250)                    (existing)

flex_allowance       = expected_income − fixed_bills − true_expense_reserve
                       − savings_target − debt_reserve − buffer
```

**During the month (computed, no input):**

```
flex_spent      = spending MTD − bills-categorized spending MTD        (transfers, CC payments,
                                                                        refunds already excluded
                                                                        by category layer)
safe_to_spend   = flex_allowance − flex_spent
weekly_pace     = flex_allowance × (days so far ÷ days in month) vs flex_spent
daily_guardrail = safe_to_spend ÷ max(days_remaining, 1)               ← fixes the $-783/day bug
```

**Double-counting rules (mostly already enforced in `compute_cashflow` / categories — codify
as tests):** Internal transfers, CC payments, and Savings/Investments transfers are neither
income nor spending. Refunds net against their category. Reimbursements net against spending.
An e-transfer out is spending only if not matched to a known internal account. Savings-target
progress counts transfers-to-savings, not leftover net.

**Category budgets become optional:** keep targets for at most the top 3 controllable
categories as *watch pacing*, not budgets (the watchlist mechanic already does this).
Everything else is covered by the single allowance.

**One safe-to-spend formula.** `money_runway()`'s version becomes canonical; `forecast_month`'s
variant is removed or delegated. The two-formula situation is the root of the $0-vs-−$783
disagreement seen between Plan and Dashboard in the same demo second.

---

## 7. Spending Intelligence Proposal

Principle: **few, material, evidenced.** Cap the weekly feed at 3 findings + 1 positive
reinforcement; everything else queues in Explore→Save money. (Alert-fatigue research: excess
alerts train dismissal; § Sources.)

Detector set — all deterministic, most already exist and need consolidation rather than
invention (`generate_insights`, `subscription_detective`, `category_drift`,
`monthly_review`, `find_recurring`):

| Finding | Logic sketch | Fires when |
|---|---|---|
| Meaningful MoM change | complete-month category delta | ≥ $50 AND ≥ 15% vs 3-mo median |
| Unusually large purchase | tx > p95 of that category's 6-mo history AND ≥ $100 | on import |
| Merchant frequency creep | visits/mo ≥ 2× 3-mo median (min 4 visits) | weekly |
| Category creep | 3 consecutive rising complete months, cum. ≥ $75 | monthly |
| Recurring price increase | same merchant recurring amount ↑ ≥ 5% and ≥ $2 | on import |
| Possible new subscription | 2–3 same-amount monthly hits, new merchant | on import |
| Fees & interest | exact-statement only (existing rule — keep it) | statement import |
| One-off exclusion | tag tx as "unusual" → excluded from averages/targets | user action, 1 click |
| Pace vs month position | flex_spent vs (flex_allowance × day/days) | weekly, ±10% band |
| Positive reinforcement | savings streak, subscription cancelled & verified gone, category under target | weekly, max 1 |

Every card carries: the claim, the math (window, comparison base), confidence
(high/medium/low), and the transaction list behind it (existing pattern). AI may narrate a
card on demand; it never generates one.

**Verified-cancellation** is the one genuinely new high-value detector: after the user marks
"cancelled X," watch two statements and confirm "X hasn't charged since May — $16/mo is real
savings." That closes the loop that makes cancelling feel rewarding — the reward step of the
habit loop.

---

## 8. Import and Categorization Improvements

Ranked by making the *second* import easier than the first (progressive trust):

1. **Post-import diff summary (highest value, mostly assembly):** after a batch — "142 new ·
   3 need review ($480 material) · new subscription detected: X · June now complete →
   [Run check-in]". Import currently ends in per-file expanders and a `st.rerun()` that
   wipes context; it should end by *starting the check-in*.
2. **Materiality threshold on review:** only flag review-blocking items ≥ $25 or
   bill/income/transfer-affecting (threshold in Settings). Small stuff auto-defers.
   `confidence.py` + flag reasons already carry what's needed.
3. **Merchant normalization table** (`merchant_normalized` exists in learned_rules; extend
   into a first-class alias map so "TIM HORTONS #1234" and "#5678" are one merchant with one
   rule). Rules UI exists in Settings; the gap is normalization quality, not machinery.
4. **Transfer & CC-payment matching:** pair chequing→MC payments and savings↔chequing moves
   within a date window and equal amounts; auto-categorize both sides Internal (parser rules
   catch most; the matcher catches the rest and kills double-count edge cases).
5. **Bulk accept by merchant** — "12 rows look like DEMO GROCERY → Groceries · [Accept all]"
   (per-merchant apply exists in Review; make it the default presentation, not per-row).
6. **Undo per batch** — `delete_import_batch` already exists; surface "Undo this import" on
   the completion summary for 1-click rollback.
7. **Missing-period detection** — coverage gaps already computed; add expected-next-statement
   inference from `import_log` cadence to power Home's freshness bar.

Duplicate handling (file-hash + tx-hash) is already solid. Watch folder already exists —
wire it into Home rather than a tab nobody visits.

---

## 9. Scope-Controlled Roadmap

### Stage 0 — Protect and measure (½ day, Fable)

- **Outcome:** redesign can't silently change the math; the running app stops looking broken.
- **Scope:** (a) pytest harness with ~10 golden-number tests pinning `compute_cashflow`,
  `money_runway`, `forecast_month`, `subscription_detective` outputs on the deterministic demo
  DB (seeded fixture); (b) fix the three P1 rendering/copy bugs: `\$`-in-HTML escapes, LaTeX
  mangling, daily-pace ÷0, "$1 daily spend" mission copy; (c) pin requirements
  (`pip freeze > requirements.lock.txt`).
- **Non-goals:** no broad test suite, no refactor, no CI changes beyond running pytest.
- **Files:** `tests/` (new), `utils/insights.py`, `utils/momentum.py`, `pages/12_Month_Plan.py`,
  `pages/11_Reduce.py`, `pages/7_Investments.py`.
- **Risks:** golden tests over-pin (assert stable aggregates, not full dicts).
- **Acceptance:** pytest green; demo session shows zero literal `\$`/math-glyph artifacts;
  month-end daily pace ≤ total.
- **Verify:** `python -m pytest`; `python -m scripts.smoke_test`; browser pass over
  Dashboard/Plan/Reduce/Net Worth.
- **Difficulty:** low. **Fable value:** medium (Codex could; do it now because everything
  builds on it).

### Stage 1 — Adoption MVP: Home + the weekly check-in (the centerpiece, Fable)

- **Outcome:** open app → understand position → know safe-to-spend → take 1 action → done,
  in under 5 minutes.
- **Scope:** new `pages/0_Home.py` per §5 (freshness bar with watch-folder detection; verdict
  from a single new `weekly_checkin()` packet that *reconciles* score/runway/plan into one
  status; safe-to-spend from `money_runway()` as the only formula; what-changed from
  `monthly_review()`; This Week = top 1–2 from the unified rec engine; Finish button +
  `review_log` table). Navigation cut to Home/Activity/Plan/Net Worth/Explore/Settings
  (`app.py` remap — old pages keep working as Explore/Tools destinations).
- **Non-goals:** no page deletions (only nav re-homing), no import-pipeline changes, no new
  detectors, no AI changes, no theming pass.
- **Files:** `app.py`, new `pages/0_Home.py`, new `utils/checkin.py`, `utils/database.py`
  (+`review_log`), light edits to `pages/2_Transactions.py` (Needs-review tab).
- **Risks:** verdict logic is genuinely hard (the one place to spend design time);
  Streamlit page-state ceilings — accept, don't fight.
- **Acceptance:** demo-mode walkthrough completes check-in < 5 min; Home never shows two
  conflicting statuses; stale demo data produces the freshness banner + import CTA; finishing
  writes `review_log` and next open greets with it.
- **Verify:** golden tests still green; scripted browser walkthrough; fresh-DB and empty-DB
  states render sanely.
- **Difficulty:** medium-high. **Fable value: highest — this is the product-judgment work.**

### Stage 2 — Financial intelligence (Fable designs, Codex can finish)

- **Outcome:** one honest recommendation voice; the flex-allowance model live.
- **Scope:** unify rec engine (Reduce + Money Moves merge under Explore→Save money, one
  target-number source); implement §6 model (true-expense table, single formula, plan page
  reduced to 3 stances + confirm flow); verified-cancellation tracking; finding cards capped
  per §7.
- **Non-goals:** no ML, no new AI surfaces.
- **Files:** `utils/planner.py`, `utils/insights.py`, `pages/12_Month_Plan.py`,
  `pages/10_Recommendations.py` (retire), `pages/11_Reduce.py`.
- **Acceptance:** any category has exactly one target everywhere; safe-to-spend identical on
  Home and Plan; cancelled sub shows verified state after next import.
- **Difficulty:** medium. **Fable value:** high for the model/copy design; implementation is
  Codex-friendly once specced.

### Stage 3 — Friction reduction (Codex)

- §8 items 1–7: post-import diff → check-in handoff, materiality threshold, normalization,
  transfer matching, bulk accept, undo surface, expected-statement inference. Inline category
  edit in Activity (kill edit-by-ID).
- **Acceptance:** import→reviewed→check-in in one flow; second import of a similar statement
  requires ≤ ⅓ the touches of the first.
- **Difficulty:** medium, well-decomposed. **Fable value:** low — specs are clear.

### Stage 4 — Desktop readiness (Codex, later)

- Keep: SQLite, stdlib-ish deps, no server assumptions beyond localhost — already .exe-friendly.
- Add: single entry point that launches Streamlit + opens browser (launcher exists), settings
  path indirection (`LEDGER_DATA_DIR`), PyInstaller spike, version/update note.
- **Non-goals now:** no framework migration. Avoid anything that assumes a cloud.
- **Difficulty:** medium (PyInstaller+Streamlit is finicky but solved; CloakScan precedent).

### Stage 5 — Portfolio & public quality (Codex + Ben)

- Test breadth (parsers on synthetic PDFs, planner edges), README refresh with new
  screenshots (`ben-voice-polish` + `git-push` skills), onboarding polish, generalized CSV
  institution presets, release packaging, demo GIF.
- **Fable value:** low — mechanical once the product is right.

### Dependency chain

Stage 0 → Stage 1 → Stage 2 → Stage 3; Stage 4 independent after 1; Stage 5 last.

---

## 10. Two-Day Fable Execution Plan

(Full sequencing in `FABLE_TWO_DAY_PLAN.md`; summary:)

- **Day 1:** Stage 0 (morning, ~2h) + Stage 1 Home vertical slice (rest of day). End of day:
  working app, tests green, Home renders all states in demo mode.
- **Day 2:** finish check-in loop (completion + review_log + freshness/watch-folder), nav
  reduction, Stage 2 design work (verdict copy, plan-page 3-stance spec, unified-rec spec
  written as implementable specs), verification pass + screenshots. End of day: working app +
  spec docs Codex can execute.
- **Fable now:** verdict logic/copy, Home layout, state design, spec writing — judgment work.
- **Codex later:** Stages 3–5, test breadth, packaging.
- **Ben decides:** naming final call, savings target & materiality threshold values, whether
  Money Pulse survives anywhere visible, feature deletions list (§11).
- **Deliberately waits:** packaging, AI feature expansion, non-Tangerine parsers, theming.

---

## 11. Final Recommendation

**Why Ledger isn't habitual:** opening it doesn't reliably answer "am I okay and what should I
do?" — it answers everything else, in conflicting voices, with no end state, on data that has
silently gone stale.

**The one decision that matters:** one Home screen, one verdict, one safe-to-spend number, one
or two actions, an explicit finish. Everything else demotes to drill-down.

**Primary workflow:** the §3 weekly check-in; monthly variant adds report card + plan confirm.

**Top five changes, in order:**
1. Home + weekly check-in with completion state (Stage 1).
2. Single-source verdict — reconcile score/runway/plan; demote Money Pulse to monthly (Stage 1).
3. One safe-to-spend formula + flex-allowance model (Stage 2 core).
4. Unify the four recommendation surfaces into one engine, one page, one number set (Stage 2).
5. Import → check-in handoff with materiality-gated review (Stage 3 first item).

**Postpone/remove from main experience:** Mission Deck/Options, Tiny Wins, Ask Ledger,
Copilot-as-section, Weekly Review expander, Money Moves page, 4 of 7 plan modes, profiles &
score-weight editing (Advanced only), XP/momentum engine (already de-surfaced), scenario
simulator, Ledger front-page charts (→ Explore).

**First implementation slice (exact):** Stage 0, then `pages/0_Home.py` rendering freshness
bar + verdict + safe-to-spend from a new `utils/checkin.py::weekly_checkin()` packet that
composes `statement_coverage` + `money_runway` + `monthly_review` and returns *one* status —
wired as the default page, verified in demo mode.
**Acceptance:** demo session: Home shows exactly one status; stale data shows the freshness
banner; `weekly_checkin()` covered by golden tests; check-in completable start-to-finish in
under 5 minutes with no contradictory number visible anywhere on the page.

**Decisions needing Ben:**
1. Naming: "Safe to spend" and "Monthly score" as plain replacements — final call.
2. Does the Money Pulse gauge survive anywhere visible, or number-only in monthly review?
3. Monthly savings target: fixed dollar amount or % of income (pre-filled how)?
4. Materiality threshold default: $25?
5. Sign-off on the §11 postpone/remove list before Codex is unleashed on it.
6. Real-data validation: run the redesigned check-in against your actual Tangerine imports
   (never in the repo) and report where the demo-clean assumptions break.

---

## Appendix A — Inspection report

**Files/workflows inspected (code):** `app.py`; all of `pages/` (Dashboard, Import,
Transactions, Trends, Spending, Income, Investments, Review, Settings, Recommendations,
Reduce, Month_Plan, Reports, Diagnostics — full reads or structural outlines); `utils/`
(database schema + full function map, insights [money_runway, mission_deck, found_money,
subscription_detective, monthly_review, generate_insights, compute_recommendations —
signatures + key implementations], planner, analytics, momentum, categorizer/confidence,
ai_* layers by responsibility); `parsers/` (structure + detect flow); `config/categories.py`,
`config/rules.py` (roles); `scripts/create_demo_data.py`, `scripts/smoke_test.py` (role),
`scripts/doctor.py` (role); CI workflow; README, GETTING_STARTED, DEMO_DATA, gitignore.

**Application states manually tested (demo mode, live browser):** Dashboard full render;
Plan (all 4 tabs' visible content, saved tight plan present); Reduce; Transactions
(filters/edit form); Review (1-item queue); Net Worth (snapshots + mixed-currency warning);
Trends ("what changed" lead); Reports (monthly review card); Settings (4-card landing);
Money Moves (8 recs). States: data-present, stale-data (18 days), demo-mode banner,
AI-disabled fallbacks everywhere, danger-runway state, saved-plan-vs-proposal conflict.
Console + server logs checked (only deprecation warnings + my own 404 probes).

**Not verified:** real Tangerine PDF parsing (no real statements — by design); AI-enabled
surfaces (no API key configured); Windows launcher end-to-end (`Ledger_Launcher.py`);
screenshots from this browser pane (screenshot capture timed out in the embedded pane —
findings rest on DOM/text inspection, which was sufficient); long-history (>6 months)
behavior of score/trends.

**Major assumptions:** Ben's real usage will be statement-driven (monthly-ish imports,
weekly check-ins between them); Tangerine remains the primary institution; Streamlit stays
the UI through Stage 3 (packaging targets it); demo data is representative in structure but
optimistically clean vs real data.

## Appendix B — Research sources

**Abandonment / retention (evidence):**
[Strategia-X — 30-day quit data & Day-30 retention](https://www.strategia-x.com/blog/2026-04-12-why-budgeting-apps-fail-30-days-fintech-ux-data/) ·
[Financial Fitness Passport — guidance gap, guilt cycle](https://www.financialfitnesspassport.com/learn/why-budgeting-apps-fail-most-people) ·
[EconBrew — behavioral barriers, loss aversion](https://www.econbrew.com/post/why-budgeting-apps-fail-the-hidden-behavioral-aspects) ·
[Onething Design — sync/trust & retention](https://www.onething.design/post/budget-app-design) ·
[JMIR scoping review — why adults abandon lifestyle apps](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11694054/)

**Safe-to-spend & budgeting models (design patterns):**
[Wall Street Survivor — Rocket Money Safe-to-Spend](https://www.wallstreetsurvivor.com/rocket-money-vs-monarch/) ·
[x1wealth — Monarch flex budgeting](https://x1wealth.com/compare/copilot-vs-monarch) ·
[Monarch — flex vs Simplifi spending plans](https://www.monarch.com/compare/simplifi-alternative) ·
[FinCompareLab — Copilot's history-derived budget suggestions](https://www.fincomparelab.com/reviews/copilot-money-review/) ·
[Penny Hoarder — YNAB learning-curve criticism](https://www.thepennyhoarder.com/budgeting/ynab-review/) ·
[Engadget — budgeting app landscape](https://www.engadget.com/apps/best-budgeting-apps-120036303.html)

**Weekly review ritual (common practice):**
[Fidelity — money dates](https://www.fidelity.com/learning-center/personal-finance/money-dates) ·
[Mind Money Balance — routine cadence](https://www.mindmoneybalance.com/blogandvideos/money-dates-financial-routine) ·
[Christine Luken — weekly money date contents](https://www.christineluken.com/what-to-do-during-your-weekly-money-date/)

**Alerts & habit mechanics (evidence + patterns):**
[FasterCapital — spending-alert fatigue & thresholds](https://fastercapital.com/term/spending-alerts.html) ·
[Fiscify — alert cooldowns](https://www.fiscify.com/blog/proactive-ai-alerts-human-assistant/) ·
[EasyHabits — cue/routine/reward loop](https://www.easyhabits.io/blog/habit-loop-explained) ·
[Behavioural Leeway — fresh-start effect & commitment devices](https://behaviouralleeway.com/behaviour-frameworks-to-support-habit-formation/)

**Local-first precedent:**
[Actual Budget](https://actualbudget.org/) · [actualbudget/actual on GitHub](https://github.com/actualbudget/actual)

**Categorization (patterns):**
[QuadraticHQ — hybrid rules+AI+human review](https://www.quadratichq.com/blog/bank-transaction-categorization-rules-ai-review)

Labels: *evidence* = study/data-backed claims; *design patterns* = converged industry practice;
everything in §§3–11 that cites neither is my product judgment, grounded in the repo and demo
evidence above.

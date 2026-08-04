# Fable Two-Day Execution Plan

Companion to `ADOPTION_AUDIT.md`. Objective for the two days: ship the **weekly check-in
vertical** — the smallest change most likely to make Ledger a weekly habit — and leave behind
specs Codex can execute for everything else. Each day ends with a working, verified app.

**Ground rules**
- Golden tests (Stage 0) land before any product change; they stay green all two days.
- No page deletions — only re-homing. Every current capability stays reachable.
- Verify in demo mode in the browser after every slice, not just at day end.
- No pushes without the `git-push` skill; README/copy changes go through `ben-voice-polish`.

---

## Day 1 — Protect, then build Home

### Block 1 (~2h): Stage 0 — protect and fix what's visibly broken
1. `tests/` + pytest: ~10 golden-number tests on the seeded demo DB pinning
   `compute_cashflow`, `money_runway` (amount, status, formula fields),
   `forecast_month`, `subscription_detective` aggregates.
2. Bug fixes (all observed live in demo):
   - `\$` literal backslashes in HTML blocks (Plan "This month's job", Reduce cards,
     Net Worth milestone, Dashboard copilot moves) — escape only in markdown-text
     contexts, never inside `unsafe_allow_html` HTML.
   - LaTeX mangling ("$54/mo · $645/yr" → math glyphs; "$15 lunch" in Reduce first-actions).
   - Daily pace ÷0: `money_runway` shows "$-783/day" when `days_left == 0` — use
     `days_remaining ≥ 1` and cap sanely.
   - Mission copy "If daily spend is over $1…" — clamp floor produces nonsense when
     safe-to-spend ≤ 0; write a negative-runway variant instead.
3. `pip freeze > requirements.lock.txt`.

**Gate:** pytest green · smoke suite green · demo pages show zero rendering artifacts.

### Block 2 (rest of day): Stage 1 core — `Home` vertical slice
1. `utils/checkin.py::weekly_checkin()` — one packet composing `statement_coverage`,
   `money_runway`, `monthly_review`, top-1–2 recommendations. Returns exactly one
   status: `no_data | stale | needs_review | behind | tight | on_track | ahead`
   + one plain-English sentence + evidence refs. Golden-test it.
2. `pages/0_Home.py` — the six-block layout from audit §5: freshness bar → verdict →
   safe-to-spend (with "why?" formula expander) → what changed (≤3 deltas) →
   this week (1–2 actions) → finish button (stub OK on Day 1).
3. `app.py` — Home becomes default page; old Dashboard temporarily moves under Explore
   ("Detailed overview" — the Reports hub already links it that way).

**End-of-day gate:** demo walkthrough — Home renders one non-contradictory story in
data-present, stale (demo *is* stale — 18 days), and empty-DB states. Tests green.

---

## Day 2 — Close the loop, cut the noise, spec the rest

### Block 1: finish the check-in loop
1. `review_log` table + Finish button writes (date, data-through, actions chosen);
   Home greets with last check-in.
2. Freshness intelligence: watch-folder pending files surface on Home with one-click
   import; expected-next-statement inferred from `import_log` cadence.
3. "Needs review (n)" gate: only materially flagged items (≥ $25 or bill/income/transfer
   classification) block the verdict; the rest defer quietly to Activity.

### Block 2: navigation reduction (re-homing only)
- Sidebar → **Home · Activity (Transactions + Review tab) · Plan · Net Worth · Explore ·
  Settings.** Explore hosts Spending / Income / Trends / Save money (Reduce; Money Moves
  cards link here) / Detailed overview / Monthly review.
- Retire from nav (not from disk): Dashboard (replaced), Money Moves (merged),
  Review (tabbed into Activity).

### Block 3: verification + handoff specs
1. Full demo-mode browser pass: weekly check-in start→finish < 5 min; every Home number
   drills to evidence; no page dead-ends; stale/empty/no-plan states sane.
2. Fresh screenshots of Home + check-in for docs.
3. Write Codex-executable specs (in `docs/product/specs/`):
   - unified rec engine (one target number; Reduce+Money Moves merge; snooze carries over)
   - flex-allowance model (audit §6 — single safe-to-spend formula, true-expense table,
     3-stance Plan page)
   - import→check-in handoff + materiality threshold + undo surface (audit §8)
4. If time remains (stretch, in order): kill the 6× "enable AI" nags in favour of one
   Settings hint; inline category edit in Activity; verified-cancellation tracker.

**End-of-day gate:** tests green · smoke green · scripted walkthrough recorded ·
specs committed.

---

## Division of labour

| Who | What |
|---|---|
| **Fable (these 2 days)** | Stage 0; Home + check-in build; verdict logic and copy; nav reduction; state design; browser verification; the three specs |
| **Codex (after)** | Stage 2 implementation from specs; Stage 3 import/categorization friction; test breadth; Stage 4 packaging spike; Stage 5 polish |
| **Ben (decides/tests)** | Naming sign-off ("Safe to spend", "Monthly score"); savings-target form (fixed $ vs %); materiality default ($25?); Money Pulse visibility; postpone/remove list sign-off; **run the new check-in on real Tangerine data locally** and report where clean-demo assumptions break |
| **Deliberately waits** | .exe packaging; AI feature expansion; non-Tangerine PDF parsers; theming; any rewrite talk |

## Risk watch

- **Verdict logic is the hard part** — if it's mushy by mid-Day-1, simplify to
  three states (`stale / needs attention / on track`) and ship; refine states later.
- **Streamlit fights complex interactivity** — accept its ceilings; no component
  rabbit-holes (that's setup-loop territory).
- **Scope pressure on Day 2** — Block 1 (loop completion) outranks Block 2 (nav)
  outranks Block 3 stretch items. A finished check-in with old nav beats half of both.

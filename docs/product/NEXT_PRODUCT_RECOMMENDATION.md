# Next Product Recommendation — Close the Planning Loop

**Date:** 2026-07-17 · **Follows:** `ADOPTION_AUDIT.md` (Home/check-in pass, shipped as
`feature/home-weekly-checkin`). This is a decision document, not another audit.

## The largest remaining obstacle

**The weekly check-in's most common call to action leads to a dead end.**

Home now reliably tells the user their period has ended or their plan is missing, and its
primary action says *"Start or update your next plan."* But the Plan page cannot do that:

- **Verified live (demo, July 17):** the Plan page contains *zero* mention of 2026-07. It
  anchors to `analysis_anchor()`, which returns the latest **data** month (2026-06) unless
  data is more than 35 days old. Statements always lag the calendar by days-to-weeks, so in
  the app's *most common state* the only plan you can save is for a month that is already
  over — and "💾 Save plan" silently overwrites it.
- The page then asks for ~15 decisions (7 modes across two pickers, proposal-vs-saved
  numbers rendered simultaneously, three coach blocks, 4 tabs) to do a job Home promised
  would take one minute.
- Meanwhile the app gives **three different answers** to "what should Groceries be":
  the saved plan's target (via runway watchlists/missions: $432), Reduce's hardcoded
  90-day-avg × 0.8 ($529), and the Plan proposal's mode-dependent cut ($576). The number
  the user actually *agreed to* — the saved plan — is ignored by Reduce and by the
  recommendation engine, whose whole job is helping hit it.

## Behaviour being harmed

The weekly ritual's close: check in → "confirm next month's plan" → can't → give up. For a
non-technical user this is worse: the Plan page is the first place Ledger asks them to make
developer-shaped choices ("stabilize" vs "reset" mode, basis strings, proposal vs saved),
and the app's advice contradicts itself on the one number they might act on. Research from
the prior pass applies directly: apps lose users when they demand setup work each period
(YNAB's best-documented complaint) and when displayed numbers stop being trustworthy.
Monarch/YNAB both solve the first with budget **rollover** — last month's plan carries
forward by default; confirming is one click
([Monarch rollover docs](https://help.monarch.com/hc/en-us/articles/4411119762196-Rollover-Budgets),
[Rob Berger YNAB-vs-Monarch on setup burden](https://robberger.com/ynab-vs-monarch-money/)).

## The change (this session's slice)

1. **Plan the right month.** Planning decouples from the analysis anchor: the Plan page
   targets the current *calendar* month, with baselines computed from the data that exists.
2. **One-minute confirm.** When the calendar month has no plan, the page leads with a
   single card: last month's plan (or a generated steady proposal) pre-filled — income,
   spending, an editable fixed-dollar savings target — and one **Confirm plan** button.
   Adjusting (stance, numbers) is progressive disclosure, not a prerequisite.
3. **Plain stances.** "Steady / Tighter / Recovery" replace the 7-mode picker on the
   primary path; the other four modes remain reachable under Advanced, and existing saved
   modes stay valid.
4. **One target truth.** A single resolver (saved-plan target first, shared 20%-cut
   fallback second) feeds Reduce, the recommendation engine, and runway watchlists — one
   number for one category everywhere, labeled with its source.
5. **The loop actually closes.** Safe to Spend and Monthly Progress fall back to the most
   recent saved plan when the data month has none (statement lag), and the Home action
   ladder clears its "confirm a plan" action once the calendar month is planned — so next
   week's check-in is shorter than this week's.

## What stays unchanged / deferred

Unchanged: Forecast, Goals, and Bills tabs; import pipeline; Review; Home layout; all
deterministic engines' math (only *which plan* they read changes). Deferred: merging
Reduce + Money Moves into one page (the target unification removes the contradiction now;
the page merge is cosmetic after that), import-completion handoff, true-expense reserves.
Retired from the primary path (not deleted): the 7-mode picker, proposal-vs-saved dual
display, and the Plan tab's coach block ceremony.

## Why this before anything else

Every other candidate (import friction, nav merging, visual polish) makes an existing loop
smoother. This one repairs a loop that is *broken*: Home's top recommendation currently
cannot be completed. Fixing the app's ability to keep a current plan is also the
precondition for everything downstream — Safe to Spend's income expectation, Monthly
Progress's savings target, and the action ladder all read from the plan. A plan that's
always current, always one click to confirm, and respected by every advice surface is the
single highest-value change left.

# Northstar Ledger 2.5.4 — native desktop validation

Built, installed and validated on 2026-08-03. **Not pushed, not tagged, not
published, no pull request, no GitHub release.** Codex handles publication
after reviewing this.

A public-beta stabilization release. Four confirmed release blockers, no new
features.

## Blocker 1 — the forecast multiplied bills it had already counted

`forecast_month` projected month-end spending as
`mtd_spending * days_in_month / days_elapsed`. Rent lands on the 1st, so day
one multiplied the month's largest commitment by the number of days in the
month.

Reproduced on a synthetic household spending about $2,800 a month with $1,800
rent on the 1st and $2,500 payroll twice a month, against six identical
complete months of history:

| As of | Before | Risk | After | Risk |
|---|---|---|---|---|
| day 1 | **$57,350.00** | danger | **$2,800.00** | on_track |
| day 3 | $19,986.73 | danger | $2,800.00 | on_track |
| day 6 | $10,708.43 | danger | $2,800.00 | on_track |
| day 10 | $9,551.54 | danger | $2,800.00 | on_track |
| day 15 | $5,250.51 | danger | $2,800.00 | on_track |
| day 20 | $3,877.17 | on_track | $2,800.00 | on_track |
| day 28 | $3,100.00 | on_track | $2,800.00 | on_track |

The old model said **danger for the first nineteen days of every month**. An
alarm that is wrong two weeks out of three is not a warning, it is noise, and
it teaches the user to stop reading the screen.

Each kind of money is now counted exactly once:

    fixed already paid
  + reviewed fixed commitments not yet paid
  + flexible spending so far
  + projected remaining flexible spending

Only the last term is projected, because only flexible spending behaves like a
rate. A bill is an event. The remaining flexible figure prefers what this
household actually spent over the same stretch of days in comparable complete
months, and falls back to the recent daily rate with the confidence lowered.
An early month with no history reports `insufficient_data` rather than a
confident verdict.

Reuses `_flexible_spending_so_far` and the existing commitment matching. No
second classifier was added.

The verdict now reads the outlook figure, which counts income the baseline
says is still coming. Reading received-only income made every month before the
second payroll look like a catastrophe: on day two the household had paid a
full month of rent and received half a month of pay. `projected_income` stays
received-only and stays authoritative for cash questions; Safe to Spend never
reads the verdict branch.

`remaining_spending_cap` is removed. It was a second Safe to Spend, computed
differently from `safe_to_spend_summary` and read by nothing.

## Blocker 2 — a retired screen was still reserving money

`goal_plan_summary` read every active goal whose `include_in_plan` was 1, the
column default. Goals is gone from native navigation and Money Focus replaced
it, so a goal carried over from the Streamlit era kept reserving money out of
Safe to Spend and the cash cushion with no screen left to see it, pause it or
remove it.

| | Before | After |
|---|---|---|
| Visible monthly savings target | $800 | $800 |
| Hidden allocation "Invented Trip" | $600 | — |
| Hidden allocation "Invented Laptop" | $500 | — |
| **Effective reservation** | **$1,100** | **$800** |
| Cash cushion effect | −$300 | none |

A one-time recorded migration (`legacy_goals_excluded_from_plan_v1`) switches
the influence off. **Every goal row is preserved**: deleting someone's saved
goals to fix a reservation bug is a worse trade than the bug. It runs after
the goal schema migration, because `include_in_plan` is added there on an
upgrading database.

`GoalsView` was still imported and still had a screen branch, so it shipped in
the bundle while being unreachable. Removed; the bundle is 10 kB smaller.

## Blocker 3 — the weekly allowance contradicted its own contract

`safe_to_spend_summary` documents `weekly_amount` as the next seven days'
share, equal to the whole amount when fewer than seven days remain. The flow
fallback always computed `amount * 7 / 30.4`.

Reconciliation on $2,190 remaining:

| Days left | Before | After | Daily |
|---|---|---|---|
| 26 | $504.28 | **$589.62** | $84.23 |
| 8 | $504.28 | **$1,916.25** | $273.75 |
| 7 | $504.28 | **$2,190.00** | $312.86 |
| 6 | $504.28 | **$2,190.00** | $365.00 |
| 1 | $504.28 | **$2,190.00** | $2,190.00 |
| 0 | $504.28 | **none** | none |

The same figure drives "stay within $X this week", so the advice was too
cautious on a long month and dangerously loose on a short one: with one day
left it suggested a week's spending. Both paths now go through one helper, and
a finished period offers no rate at all.

## Blocker 4 — packaged GUI verification

Performed this pass. Scope and limits are stated exactly below.

## Plan rollover

When the newest statement was July and the calendar said August, Plan led with
"This planning period has ended" — true about July, useless about August. It
now reads, against a real six-month synthetic ledger through the packaged
engine:

    August plan ready to review
    No August transactions have been imported yet. July closed with $X kept.

state: `awaiting_current_data`. A month that has genuinely run out of days
still reports `period_ended`, which is the case that wording was written for.
Safe to Spend stays unavailable until current activity is imported; planning
the current month is not blocked.

## AI marked Beta

Coach, AI Settings and the README AI section now carry a Beta label and say
plainly: optional, off by default, no finance math depends on it, online
providers receive the evidence packet for the question asked, provider
compatibility varies, and AI can never edit financial records. Coach with AI
disabled returned two deterministic findings on the packaged app ("Food &
Convenience is above its usual range", "Wednesday is your most expensive
day").

## Verification

Nothing was skipped, weakened, deleted or marked expected-failure. The suite
carries no `xfail`.

| Check | Result |
|---|---|
| `pytest -q` | **1036 passed**, 0 failed, 0 skipped, 0 xfailed |
| `npm run build` | passed |
| `npm run test:charts` | 39 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` | passed, `ledger-native v2.5.4` |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `scripts.doctor` | passed |
| `git diff --check` | clean |

Two existing tests were **updated, not weakened**, because they asserted
behaviour this release deliberately changed. Both are called out here so a
reviewer can check the judgement rather than discover it:

- `test_verdict_period_ended_when_calendar_moved_on` asserted that a calendar
  month ahead of the data produced `period_ended`. That is the defect in
  blocker B. Renamed and re-pointed at the new state; every other assertion in
  it, including the pace-advice guards, is unchanged.
- The 2.5.2 net worth test asserting a full date reads as its month was
  already replaced in 2.5.3 and is unaffected here.

## The installer

| | |
|---|---|
| Path | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.4_x64-setup.exe` |
| Size | 276,950,441 bytes (264.1 MB) |
| SHA-256 | `0744E4DA5213A76E9465AD7FB841BD7D54C6F681223F92FCDC7DD4ABFB9A4F92` |
| Bundled files | 1,567 |

No 2.5.3 metadata in the artifact: the filename, `ledger-native.exe`
ProductVersion and FileVersion all read 2.5.4.

## The installed app

Installed silently over 2.5.3, exit code 0.

| | |
|---|---|
| Installed Apps `DisplayVersion` | Northstar Ledger **2.5.4** |
| Uninstall registry key | `HKCU\...\Uninstall\Northstar Ledger`, sole Ledger entry in any hive |
| `ledger-native.exe` / `uninstall.exe` | 2.5.4 / 2.5.4 |

`%LOCALAPPDATA%\Ledger` stayed at 19 files and `finance.db` kept its timestamp
(08/03/2026 07:54:24) through the build, the install, and both GUI launches.
It was never opened, read, hashed or copied.

### Packaged-engine validation — 24 of 26, both exceptions explained

Run against the installed `ledger-engine.exe` with `LEDGER_DATA_DIR` pointed
at a fresh `%TEMP%` directory. Every figure invented.

Passed: first-run state; asset account with positive and negative rows;
liability account with a payment and a purchase; ISO, MM/DD/YYYY and
DD/MM/YYYY dates; decimal comma with a thousands dot; ambiguous punctuation
failing closed; re-import skipping 6 of 6 duplicates; cross-account identical
rows surviving; an unsupported shape giving a useful path rather than a crash;
Plan loading; no second Safe to Spend on the forecast; the forecast reporting
its confidence and exposing its components; Home and Plan agreeing on Safe to
Spend; four monthly net worth readings; assets minus debts; month over month;
editing without duplicating; deleting one reading; a gap inventing no
comparison; AI off; backup created.

The two that did not pass on the first run were **both my checks, not the
product**:

1. *Two separate identical purchases both survive* reported 1. The second
   import was refused by the 2.5.1 partial-overlap guard — "Some, but not all,
   rows match an earlier import" — which is correct behaviour. Re-run with
   `import_mode="new"`: 2 inserted, both fares present.
2. *Coach returns deterministic findings with AI off* reported 0 concerns. The
   scattered fixture had **no complete month**, so an empty feed was the honest
   answer. Re-run against six complete months: 2 concerns, listed above.

### GUI validation actually performed

The installed executable was launched twice from a PowerShell process whose
`LEDGER_DATA_DIR` already pointed at a disposable directory. The ordinary
Start Menu shortcut was never used.

- Window opened and stayed up (18 seconds), titled "Northstar Ledger".
- Second launch was over a seeded six-month ledger, so the packaged frontend
  rendered real data through the packaged engine.
- `native-engine.log`: **0 error lines**. `native-shell.log`: never created,
  meaning the shell logged no failure.
- **No listening sockets** owned by the process, no localhost service.
- **No browser process** started.
- Private database untouched across both launches.

### Not verified

Stated plainly rather than implied.

- **No click-level GUI interaction was performed.** Buttons were not pressed,
  forms were not typed into, and Edit / Delete / Cancel were not exercised
  through the interface. The window renders and the data paths behind every
  screen were driven through the same packaged engine the GUI calls, but that
  is not the same as clicking. `docs/releases/NET_WORTH_GUI_CHECKLIST_2_5_3.md`
  remains the checklist for a human pass, and it still applies to 2.5.4.
- No live external AI provider call. Unchanged.
- One month-end pace test fails on Python 3.12 only. CI runs 3.14.
- **The installer is unsigned**, so SmartScreen will warn.
- **There is no updater.** A new version means installing over the previous
  one.
- A quiet and partially-imported account still cannot be told from a merely
  quiet one. Unchanged from 2.5.0.
- Net worth readings are not in the transactions CSV export. Settings says
  "Export your transactions", so the omission is transparent.

## History cleanup

Unchanged and still not executed. The plan is in
`NATIVE_DESKTOP_2.5.0_VALIDATION.md`.

# Changelog

## 3.0.0 - Unreleased

Northstar Ledger is now SignalSpace. This is a product-name and interface
release, not a change to the finance math.

### What changed

- The native app, installer, taskbar icon, update panel and current public docs
  now use the SignalSpace name and the new chart-shaped mark.
- The interface uses one navy, green, blue, violet and amber system across
  navigation, cards, charts, buttons, focus states and the Insights patterns.
- Existing installs update in place. The app identifier, program directory,
  database directory, sidecar name and environment variables keep their legacy
  values so upgrades do not strand data.
- Preferences saved under the old `northstar.*` browser-storage keys move to
  `spendshape.*` once, without overwriting a newer choice.
- Upgrade cleanup now removes both `Ledger` and `Northstar Ledger` Installed
  Apps entries and shortcuts after SignalSpace installs successfully.
- Remaining native errors and current setup guidance use the SignalSpace name.

## 2.9.1 - 2026-08-18

This patch makes Money Radar much more selective after real use showed that
regular shopping habits were being presented like bills.

### What changed

- Radar now shows only costs that Northstar's Plan logic also treats as real
  obligations. A weekly grocery shop, regular restaurant visit or repeat
  delivery merchant stays out, even when its timing is predictable.
- Steady subscriptions now stay aligned across Radar, Plan and Safe to Spend.
  They no longer need a price change or another cancellation flag before the
  plan reserves them.
- Payroll can appear as an expected payday automatically. Other regular income
  needs an explicit Use as income decision before Radar places it on a future
  date. Excluding a source now removes it from planning income too.
- The old Already due section is gone. Radar starts today, looks 28 days ahead
  and leaves missed-bill detection to a separate question.
- Goal and learned-rule updates now use the same UTC timestamp convention as
  the rest of the database. Existing timestamp rows are preserved as written.

## 2.9.0 - 2026-08-18

Northstar can show you what is coming, not just what happened.

### What changed

- Home has a new section under Safe to Spend: the next few weeks of
  recurring money Northstar recognises from your statements. Expected
  paydays sit above the line, recurring costs below it, on the dates their
  rhythm suggests.
- **These are expectations, not scheduled payments.** Northstar has no
  connection to your bank and no list of your bills. It reads what has
  repeated and says when the pattern is next due. A date can be wrong, and
  a charge can simply not arrive.
- Open any expected item to see the last three real charges behind it, when
  it was last seen, and how often it usually happens. From there you can go
  straight to the transactions.
- Every estimate carries how confident Northstar is, and the section always
  says which day your statements run to. If your import is behind, it says
  so plainly and lowers its own confidence rather than looking current.
- A recurring cost you have marked "Not recurring", and an income source you
  have marked "No", are both left out.
- Nothing here projects a balance. Northstar will not tell you what you will
  have, or that you will be short. It shows the events and what they add up
  to, and leaves the arithmetic about your bank account to your bank.
- Nothing to set up or maintain. There are no reminders to enter and no bills
  to configure; it comes from the statements you already import.

## 2.8.0 - 2026-08-17

Insights notices more, and shows its working.

### What changed

- Insights opens with what Northstar noticed. Those observations used to
  appear only on Home, well down the page, with no way to see what was
  behind them.
- Northstar now reads your finished months as a run instead of one at a
  time, so it can tell a level that moved from one expensive month. It
  notices a category that has run higher or lower for several months in a
  row, a change in what you keep each month, and what made the last finished
  month different from the one before it.
- It also notices money leaving in amounts too small to spot one at a time,
  and only mentions it when the total is a real share of what you spent that
  month.
- Improvements get the same treatment. A category that has stayed below its
  old level, or a few months of keeping more, need the same evidence as
  anything going the wrong way. No scores, streaks or congratulations.
- Every observation shows the figures it came from, how many months it used
  and what it left out. Most of them open the exact transactions behind the
  claim.
- You get the few strongest observations, not all of them. The rest are one
  click away, and the same finding can no longer show up twice under two
  different titles.
- Home now shows the two strongest headlines and a link through to the rest
  instead of repeating the whole thing.
- Fixed an observation about recurring costs that could never appear. It
  asked the engine for figures under the wrong names, so it failed every
  time it ran, and the safeguard that stops one broken observation blanking
  the page had been quietly hiding it.

## 2.7.2 - 2026-08-17

Plan saves again.

### What changed

- A second income calculation was running behind the screen and refusing
  valid plans, with an error naming an amount you could not find anywhere in
  the app. There is one definition now: Money coming in
  is the average of your complete months, the same figure Safe to Spend
  uses, and it tells you which months it averaged.
- Money coming in, the non-monthly set-aside and the everyday spending
  result are all worked out by Northstar rather than sent from the screen,
  so the number you see is the number that gets saved.
- Plan reads a little clearer: the figures Northstar works out look like
  facts instead of boxes you cannot type in, the three amounts you choose
  look editable, and what is left for everyday spending is the biggest thing
  on the page.
- A plan that really does allocate more than comes in is still refused, and
  still tells you how much to move.

## 2.7.1 - 2026-08-17

Plan is simpler and saves what you mean.

### What changed

- Plan is one clear monthly decision: what is coming in, what is committed,
  what you want to keep, and what is left for everyday spending.
- Saved plans survive a relaunch, edits replace the same monthly row, and the
  screen clearly separates saved figures from unsaved changes.
- Confirming or rejecting monthly, quarterly, and annual recurring costs now
  sticks and changes the Plan reserve correctly.
- Last Plan Result stays available as a quieter historical check.
- The experimental What-if replay and Money Focus surfaces were removed after
  real use showed they were not earning their space. Existing stored rows are
  left untouched.

## 2.7.0 - 2026-08-17

Two new ways to look back at a finished month without guessing.

### What changed

- Insights can replay a finished month after reducing one real category or
  merchant by 25%, 50% or 100%, then compare the actual and counterfactual
  results.
- Every replay shows the matched transactions behind the result and uses the
  same deterministic cash-flow rules as the rest of Northstar.
- Plan can compare what you intended to keep with what the month actually
  kept, but only when that exact plan existed in time and statement coverage
  is complete.
- Replays are read-only. They do not change transactions, plans or forecasts.

## 2.6.3 - 2026-08-12

An Insights comparison fix. No new features.

### What changed

- Finished months remain complete when normal statement exports are split
  across legitimate account files or contain quiet days.
- Truly partial or stitched-together coverage remains excluded from finished
  month comparisons.
- Prior-month comparisons and rolling averages now disclose and use the months
  that actually qualify.
- The best finished month keeps its real sign when every finished month is
  negative.

## 2.6.2 - 2026-08-12

A trust-boundary release. No new features.

### What changed

- Plan now refuses non-finite and implausibly large amounts before they can
  reach SQLite or any forecast.
- Restore checks the core Ledger schema, duplicate constraint and transaction
  rows before a selected database can replace the working one.
- Historical calculations now respect their supplied date instead of quietly
  reading the computer's current month.
- A stale import no longer shifts the starter-plan baseline into empty recent
  months and understates typical income.
- Net worth refuses future-month entries because those are projections, not
  recorded readings.
- An unavailable Safe to Spend result no longer labels itself balance-checked.
- Added regression coverage for import preview races and backups taken while a
  WAL writer has an uncommitted row.

## 2.6.1 - 2026-08-04

A stabilization release. No new features.

### What changed

- **Fixed the `database is locked` errors.** Every screen action runs in its
  own short-lived process, and each one opened a write transaction on startup
  even when the database already had the right schema and there was nothing to
  write. Reads never block reads, so those unnecessary writes were the only
  reason opening Plan or Insights could ever report a lock. A database already
  matching this build now takes a read-only path and opens no transaction at
  all.
- **Plan asks once instead of twice.** Opening an unsaved plan previewed the
  equation, then immediately previewed the same values again. It also applied
  whichever response arrived last rather than whichever was newest, so a slow
  failure from an old keystroke could replace a correct equation with an error.
  Newest wins now, and a working preview clears a stale error.
- **A negative month reads correctly.** "June closed with $-68 kept" is now
  "June closed with -$68 kept".
- **Month names keep their capital letter** in the Insights period label.
- **Coach states its Beta limits at the top**, where they are read before the
  answers rather than after them.

## 2.6.0 - 2026-08-04

Northstar Ledger 2.6.0 introduced the signed, user-triggered updater.

**Install this one manually.** 2.5.5 has no updater, so it cannot fetch this
release. Download the installer and run it over your existing copy; your data
stays where it is. From 2.6.0 onward, Settings can check for and install
updates itself.

### What changed

- Net worth means one thing again. The monthly reading you type is the only
  net-worth figure Northstar shows. The second one derived from account
  balances, along with its Assets and Liabilities cards and the quick-estimate
  form, is gone. It only ever saw the accounts you had recorded a balance for,
  so it left out the pension, the car and the mortgage and then printed the
  result as a fact.
- Current account balances moved to Settings under "Planning balances", where
  they are described as what they are: inputs to Safe to Spend and Plan. The
  choice of which accounts Safe to Spend may draw on moved with them. Nothing
  about the calculation changed.
- Settings can check for updates. It contacts GitHub only when you press the
  button, sends nothing but a request for the latest version number, and
  installs an update only if its signature matches Northstar's key. There is
  no background checking and no telemetry.
- The About section no longer claims "Connection: None". Finance processing is
  still entirely local; optional AI contacts the provider you configure, and an
  update check contacts GitHub. Both need you to act first.

## 2.5.5 - 2026-08-03

Northstar Ledger 2.5.5 is the current Windows public beta.

### What changed

- Corrected the monthly forecast so quarterly and annual bills land in the
  month they are due instead of every month.
- Stopped late-month forecasts from assuming a missed payroll deposit will
  still arrive.
- Kept Safe to Spend tied to current balances and confirmed reservations.
- Added current native screenshots and a browser product tour built entirely
  from synthetic data.
- Tightened public-tree privacy guards and multi-file import coverage.
- Verified create, edit, cancel, delete, and relaunch behavior for recorded net
  worth readings.

### Current limits

- The Windows installer is unsigned.
- Updates are manual downloads.
- Optional AI support is Beta, off by default, and not involved in finance
  calculations.
- An unfamiliar or ambiguous bank export may be refused rather than guessed.

The full engineering validation is in
[`docs/releases/NATIVE_DESKTOP_2.5.5_VALIDATION.md`](docs/releases/NATIVE_DESKTOP_2.5.5_VALIDATION.md).

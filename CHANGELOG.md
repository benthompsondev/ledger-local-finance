# Changelog

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

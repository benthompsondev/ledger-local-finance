# Changelog

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

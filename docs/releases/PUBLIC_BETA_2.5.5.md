# Northstar Ledger 2.5.5

Northstar Ledger 2.5.5 is the current Windows public beta. It is a local-first
finance app for importing bank statements, checking spending, planning the
month, and recording net worth without creating an account or sending the
database to a finance service.

## Download

Download `NorthstarLedger_2.5.5_x64-setup.exe` from the Assets section below.

- Size: 276,960,529 bytes (264.1 MB)
- SHA-256: `038FDEB848BDA5FCE35F372C4D6C4745D11377A4560878AF97EDDB2AB9DD5E38`

The installer is unsigned, so Windows SmartScreen may warn you the first time.
You can verify the download in PowerShell before running it:

```powershell
Get-FileHash .\NorthstarLedger_2.5.5_x64-setup.exe -Algorithm SHA256
```

## What is in this release

- Correct monthly placement for quarterly and annual bills.
- A late-month forecast no longer assumes missed income will still arrive.
- Safe to Spend remains tied to current balances and confirmed reservations.
- Two-file import preview and confirmation coverage.
- Stronger public-tree privacy guards.
- Verified create, edit, cancel, delete, and relaunch behavior for recorded net
  worth readings.
- A new static product tour using only synthetic data.

## What was verified

- 1,096 Python tests passed.
- 39 frontend contract tests passed.
- 5 Rust tests passed.
- The packaged engine passed the release smoke tests in a disposable data
  directory.
- The exact installer was installed and launched as version 2.5.5.
- Home, Plan, Insights, and recorded net-worth interactions were exercised in
  the installed app using generated transactions.
- The real application database was not opened during release validation.

The detailed engineering record is in
[`NATIVE_DESKTOP_2.5.5_VALIDATION.md`](https://github.com/benthompsondev/ledger-local-finance/blob/main/docs/releases/NATIVE_DESKTOP_2.5.5_VALIDATION.md).

## Public-beta limits

- Updates are manual downloads.
- The installer is not code-signed.
- Bank exports vary. An unfamiliar or ambiguous CSV may need mapping or may be
  refused rather than guessed.
- Optional AI is Beta, read-only, off by default, and not involved in finance
  calculations.
- Your bank statement remains the final source of truth.

Start with the [product tour](https://benthompsondev.github.io/ledger-local-finance/)
or read the [setup guide](https://github.com/benthompsondev/ledger-local-finance/blob/main/docs/GETTING_STARTED.md).

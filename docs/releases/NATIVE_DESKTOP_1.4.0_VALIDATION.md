# Northstar Ledger native desktop 1.4.0 validation

Release theme: **A plan that balances**

The exact 1.4.0 installer was built, installed, launched, and exercised
against a disposable 308-transaction synthetic database on July 23, 2026.
The regular `%LOCALAPPDATA%\Ledger\finance.db` was not opened by the
validation app and remained byte-for-byte unchanged through installation.

## Release artifact

- Installer: `NorthstarLedger_1.4.0_x64-setup.exe`
- Size: 270,932,088 bytes (258.38 MiB)
- SHA-256: `978FC0507D0C49A2FBA6E0B69B5B5DC55367B316710993329469F7EE1C372B5D`
- Installed product: `Northstar Ledger 1.4.0`
- Install location: `%LOCALAPPDATA%\Programs\Ledger`
- Installed Apps, executable metadata, and Settings/About: `1.4.0`
- Authenticode: unsigned

## Financial contract

- Monthly planning income is a conservative median of recurring income from
  up to six complete historical months.
- Reliable income can include payroll, regular interest, and a confirmed or
  consistently recurring incoming deposit. Refunds, reimbursements, card
  payments, investment movements, and one-off windfalls do not enlarge the
  monthly plan.
- The monthly plan has four visible buckets: fixed commitments, nonmonthly
  reserves, savings and goals, and one flexible spending amount. Nonmonthly
  reserve automation remains intentionally zero until the sinking-fund work.
- Safe to Spend is the lower of remaining flexible allowance and the cash
  cushion after complete chequing/card balances, unpaid confirmed bills,
  remaining savings contributions, nonmonthly reserves, and buffer.
- Cash cushion after bills is context. It is never presented as additional
  spending permission.

## Installed acceptance

- Home and Insights reconciled to `$3,563.26` spending and `$4,801.20`
  income for the same 30-day period.
- The spending-category total matched headline spending exactly.
- Income sources, top merchants, monthly cash flow, and the same-day
  comparison populated from synthetic data.
- Confirming synthetic payroll immediately changed reliable income from
  unconfirmed to `$4,800.00` per month.
- Current chequing and credit-card balances, the saved plan, confirmed
  payroll, Home start screen, and 30-day period persisted after relaunch.
- Stale data no longer allows Plan to say `Ready to use`. Plan instead shows
  `Import your latest transactions` and routes directly to Add Data.
- The app opened no external browser, console, or TCP listener. The packaged
  sidecar exited after each request; the only persistent child was the
  expected embedded WebView2 runtime.

## Automated checks

- `python -m pytest -q`: 262 passed.
- Focused native engine and Plan suite: 44 passed.
- React TypeScript and Vite production build: passed during packaging.
- Rust optimized release build: passed.
- Rust tests: 3 passed.
- Strict native doctor: passed.
- Installer verifier: 1,567 sidecar files; correct versioned filename; no
  private runtime filenames.
- Installed tree: 1,569 files and no statement, SQLite database, app config,
  log, spreadsheet, or private-key files. Certifi's public CA trust bundle is
  the only `.pem` dependency.

## Readiness and reconciliation

- Every active chequing and credit-card account needs a current balance.
- The interface shows each balance date and flags transactions imported after
  that date.
- An unknown card balance is not treated as a zero-dollar liability.
- A saved plan becomes stale when reviewed commitments change.
- A deliberate fixed-cost override requires a short reason and remains valid
  only while its reviewed commitment baseline is unchanged.
- Goal savings reserve only the remaining contribution for the month.
- Stale transaction data blocks the Plan readiness state even when balances,
  income, and the current calendar-month plan have been confirmed.

## Privacy and remaining release limits

- Validation used only generated synthetic data in a disposable
  `LEDGER_DATA_DIR`.
- The regular database hash and modified timestamp were unchanged before and
  after installation.
- The installer and executables are unsigned, so Windows SmartScreen can
  warn.
- A clean Windows VM acceptance pass and code-signing certificate are still
  required before treating this as a broadly distributed Windows release.
- A second launch can still open a second native window.

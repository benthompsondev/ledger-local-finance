# Northstar Ledger native desktop 0.39.1 usability pass

Version 0.39.1 makes Interac e-transfers follow the signed account movement:
an incoming e-transfer is income and an outgoing e-transfer is spending.
Only a saved rule or manual Internal Transfer category excludes one from cash
flow. Credit-card payments, savings transfers, and investment movements remain
excluded.

The Home and Insights screens now include matching spending-category and
income-source donut charts. Both are computed by the Python finance engine,
both drill into the same dated Transactions rows, and the ranked spending bars
remain available for exact comparisons. Home also uses a compact account
coverage disclosure, plainer labels, and a 3, 6, or 12 month chart preference
that survives relaunch.

## Validation

Release checks completed on July 19, 2026:

- Python: `166 passed`
- Native shell: `3 passed`
- TypeScript/Vite production build and Rust `cargo check`: passed
- Installed version: `0.39.1`
- Installer: `NorthstarLedger_0.39.1_x64-setup.exe`
- Size: `270,757,500 bytes`
- SHA-256: `CAC8323C423C9D674D9CA65751DC730D572EBAAFE4D5DA4596CAFE5810DA479A`
- Installed shell: one native window, no console child, and no listening TCP
  port
- Installed bundle: no statement, database, PDF, CSV, or log files
- Installed engine: both private source structures imported into disposable
  local data with zero sign mismatches; duplicate re-import inserted nothing
- Home, Insights, spending donut, and income donut totals reconciled exactly
- A category correction updated both charts without changing the spending
  total, and the chart period remained selected after relaunch

Private source files, transaction descriptions, account identifiers, and
financial totals are not part of the repository or installer.

## Known limits

- Net worth remains unavailable until current balances are entered.
- Income sources use the cleaned statement merchant or sender label. A user can
  refine individual transaction categories, but this release does not include
  a full source-name editor.
- Customization is intentionally limited to the useful chart period control;
  there is no dashboard layout editor.

# Northstar Ledger native desktop 0.40.0 correctness pass

Version 0.40.0 fixes the financial model exposed by real CSV testing. The
important change is not a new dashboard. It is one consistent definition of
money in, money out, spending, income, and account plumbing.

## The transaction contract

- `amount` is stored as a positive magnitude.
- `direction=credit` means money entered an asset account or reduced a
  liability.
- `direction=debit` means money left an asset account or increased a
  liability.
- `transaction_type` decides whether the movement is income, spending, a
  spending offset, internal plumbing, or an item that needs review.
- Raw descriptions are preserved locally. Clean merchant names are used for
  display and grouping.

A positive chequing amount is money in. A negative chequing amount is money
out. On a credit card, a debit is a purchase and a credit is either a payment
or a merchant refund. Description and account type separate those two cases.

## What each Home number includes

| Number | Included | Excluded | Incomplete data behaviour |
|---|---|---|---|
| Income this period | confirmed employment, interest, and other supported income types | payments, transfers, investments, reimbursements, refunds, ambiguous deposits | unexplained deposits are reviewed instead of called income |
| Spending this period | purchases, mortgage payments, fees, interest, and cash advances | income, payments, transfers, investments, and cancelled rows | card refunds reduce spending |
| Safe to Spend | the saved plan minus spending, remaining commitments, savings, fees, and buffer | all account plumbing | confidence drops when account cut-off dates do not match |
| Projected spending | current spending pace plus supported commitments not seen yet | transfers and recurring retail that is only being watched | the verdict becomes low confidence when one account is less current |
| Projected net | expected genuine income minus projected spending | unconfirmed inflows | depends on the saved plan when one exists |
| Net worth | explicit current balance snapshots, with liabilities subtracted | transaction-history guesses | shows not configured, partial, or mixed-currency instead of a confident zero |

“Where money went” accepts only the spending-category allowlist. Recurring
costs use eligible outflows with at least three months of history and a basic
amount-stability check. Income, payments, transfers, investments, refunds, and
reimbursements cannot enter either result.

## Import and review guardrails

CSV profiles now record the account type and amount convention. A legacy or
wrong-account profile stops with a reset/relearn message instead of silently
applying a stale sign rule.

Known payments and transfers are not flagged just because they are large.
Small unmatched purchases stay deferred. Repeated material uncategorized
outflows produce one confirmation item, while an unexplained inflow stays out
of income until it has deterministic evidence or the user confirms it.

## Validation

The automated suite uses invented fixtures matching both supported CSV column
shapes. It checks chequing signs, card purchases, both sides of card payments,
refund offsets, savings and investment transfers, mortgage spending,
account-aware duplicates, recurring-cost exclusions, category-chart
exclusions, plan coherence, balance snapshots, and the unconfigured net-worth
state.

The same flow was also reconciled locally with two private source files in a
disposable data directory. Those files and their contents are not part of the
repository, tests, installer, screenshots, or logs.

Release checks completed on July 18, 2026:

- Python: `162 passed`
- Native shell: `3 passed`
- TypeScript/Vite production build and Rust `cargo check`: passed
- Installed version: `0.40.0`
- Installer: `NorthstarLedger_0.40.0_x64-setup.exe`
- Size: `270,752,159 bytes`
- SHA-256: `E1C37AED2E879AA0FECD411956AA9822E691B082F17F88E4EABABCC6AA07CC0F`
- Installed shell: one native window, no console child, no listening port
- Installed engine: both private source structures imported and reconciled in
  disposable local data, duplicate reimport inserted nothing, and restart
  preserved the corrected state

## Known limits

- A bank description without a stable mortgage or loan identifier still needs
  one category confirmation before it becomes a fixed obligation.
- Foreign-currency balances are shown separately. Ledger does not combine them
  without conversion support.
- A legacy database with incorrect historical imports is not silently
  rewritten. Use batch provenance to undo the affected import and reimport the
  source file through the corrected path.

# Northstar Ledger 1.0.0 implementation note

## Recovered state

- Branch: `feature/home-weekly-checkin`
- Starting commit: `9586f22` (`Ship Northstar Ledger 0.9.0 quality pass`)
- Remote divergence: 0 ahead, 0 behind
- Starting tree: clean
- Installed product: Northstar Ledger 0.9.0
- Baseline: 212 Python tests, 3 Rust tests, and the TypeScript/Vite build pass
- Runtime check: the installed native app opens against an empty disposable data root

## Work order

1. Make Safe to Spend conservative, reconciled, and subordinate to one shared
   Home/Plan risk outcome. Consolidate the financial semantics needed for every
   summary to agree.
2. Make generic CSV import dependable: delimiter and encoding handling, native
   mapping, saved profiles, account confirmation, and preview/confirm parity.
3. Tighten the monthly Home story, change-focused Insights, payroll confirmation,
   shared-expense consistency, e-transfer handling, Quick Review refresh, Add Data
   language, and supported desktop widths.
4. Bump every product surface to 1.0.0, package the sidecar and NSIS installer,
   then validate the exact installed artifact without touching the regular data
   root.

The financial-trust work and import work are separate checkpoint commits. The
remaining product work starts only after both CORE checkpoints pass their focused
validation.

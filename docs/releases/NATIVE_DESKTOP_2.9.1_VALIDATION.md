# Northstar Ledger 2.9.1 validation

This patch tightens Money Radar after real use showed that regular shopping
habits were being presented like future bills. All verification used generated
or disposable data. No real financial data or screenshots were read, printed
or added to the repository.

## Trust boundary

Merchant cadence is evidence that something repeats. It is not evidence that
money is owed.

Radar now admits an outgoing only when the canonical Plan classifier also
treats it as an obligation. Fixed bills, active subscriptions and nonmonthly
commitments qualify. Restaurants, groceries, retail and delivery habits do
not, even when they repeat cleanly or are correctly marked recurring.

Payroll can appear as an expected payday automatically. Another regular income
source needs an explicit **Use as income** decision before Radar places it on a
future date. **Exclude** removes a source from the planning baseline as well.

Radar starts today and looks 28 days ahead. It no longer builds an **Already
due** list from old projected dates. Totals are the exact sums of the filtered
events shown on screen, and no balance or cash forecast is produced.

## Synthetic reproduction

The regression fixture includes a biweekly restaurant, weekly grocery shop,
monthly delivery habit, utility bill, subscription, mortgage, payroll, small
monthly interest credit and one-off refund.

- Restaurant, grocery and delivery rhythms are absent.
- Utility, subscription and mortgage obligations are present.
- Payroll is present.
- Small interest is absent until explicitly used as income.
- The refund is absent.
- Every expected date is today or later.
- Expected In and Expected Out equal the displayed event sums.

The same tests prove that marking discretionary spending recurring does not
turn it into a bill, while correcting its category to a real obligation can.

## Plan and persistence corrections

- Plan now reserves every active subscription. The old code used a
  cancellation-review shortlist, so a steady subscription could appear in
  Radar while Plan and Safe to Spend omitted it.
- Excluding an income source now removes it from the shared planning deposits,
  keeping Plan, Safe to Spend and expected income aligned.
- Goal and learned-rule update timestamps now use UTC, matching SQLite's
  defaults. Existing rows are preserved because their old local/UTC meaning
  cannot be reconstructed safely.

## Native verification

The current sidecar was rebuilt with PyInstaller and run through the actual
Tauri development shell against a disposable demo database:

`React -> Tauri command -> Rust shell -> packaged Python sidecar -> SQLite -> React`

The Home screen rendered 7 genuine bills and 2 payroll dates between August 18
and September 15. It showed no shopping habits and no past-due bucket. Expected
Out was the sum of the seven displayed bills. The installed production window
and real Ledger database were not used.

## Gates

| Check | Result |
| --- | --- |
| Focused Plan, forecast, Safe to Spend and Radar tests | 98 passed |
| Full Python suite | 1,352 passed |
| Frontend finance and updater tests | 95 passed |
| Frontend build | passed |
| `cargo check` | passed |
| `cargo test` | 5 passed |
| Independent read-only diff review | no remaining findings |
| Tracked-file privacy check | passed |
| `git diff --check` | passed |

## Release artifact

| Artifact | Result |
| --- | --- |
| Installer | `NorthstarLedger_2.9.1_x64-setup.exe` |
| Size | 281,237,724 bytes |
| SHA-256 | `8DE7A964DA552FAF09BE1FE8167E3C85A9266830F74F5B556F8265D835B21EF7` |
| Updater signature | 428 bytes |
| Installer verifier | 1,567 bundled files, passed |
| Updater manifest | version 2.9.1, HTTPS release URL, validated |

The installer is not Authenticode signed, so Windows may still show the normal
SmartScreen warning. The updater artifact has Northstar's detached updater
signature and is rejected if that signature does not match.

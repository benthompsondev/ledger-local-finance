# Northstar Ledger 2.7.2 — validation

A patch release. One user-visible fix: a valid monthly plan can be saved.

Every figure below comes from disposable synthetic data. No real financial
data was read at any point.

## What 2.7.1 did wrong

Plan displayed one monthly income and `save_plan` validated against another.

- The screen, Safe to Spend and the planner all read `typical_monthly_income`,
  the average of complete months.
- `save_plan` alone compared the submitted figure with
  `reliable_income_summary`, which takes the median of each income source
  across only the months that source appeared in. A source arriving in three
  months out of six is therefore counted as though it arrived in all six.

The two disagree for anyone whose deposits are not perfectly regular, and the
guard rejected any difference of one cent. In the reported failure the screen
showed one amount, the save refused with `Monthly planning income is anchored
to the reliable income floor of $X` naming a higher amount that appeared
nowhere else in the app, and nothing was persisted.

## Reproduced against the released build

A git worktree at `fdc12e2` (the 2.7.1 release commit) was driven with
synthetic data containing regular payroll plus a confirmed second source
present in only half the months.

| | 2.7.1 (`fdc12e2`) | 2.7.2 |
| --- | --- | --- |
| Displayed income | `5750.00` | `5750.00` |
| Save-time floor | `5000.00` | not consulted |
| Result | `SAVE REJECTED — anchored to the reliable income floor of $5,000.00` | `SAVE OK` |
| Row persisted | no | yes |

The reported direction — floor **above** the displayed figure — was reproduced
separately by confirming the intermittent source, which makes
`reliable_income_summary` count its full `1500` every month while
`typical_monthly_income` spreads it as `750`:

| | 2.7.1 (`fdc12e2`) | 2.7.2 |
| --- | --- | --- |
| Displayed income | `5750.00` | `5750.00` |
| Save-time floor | `6500.00` | not consulted |
| Result | `SAVE REJECTED — anchored to the reliable income floor of $6,500.00` | `SAVE OK` |

The mismatch between the two estimators still exists in 2.7.2. It no longer has
any path into the plan.

## The contract now

`typical_monthly_income` is the one planning income. It is what Safe to Spend
reads (`utils/checkin.py`), what the planner reads, and what Plan shows,
labelled with the months it averaged.

`reliable_income_summary` reports review state. Its `confirmed` flag still
drives readiness; its amount prices nothing.

Income, the non-monthly reserve and flexible spending are all derived by the
engine. None of them can be supplied by the frontend:

- `PlanSpec` and `PlanPreviewSpec` no longer carry them;
- the Rust command forwards an explicit field whitelist to the sidecar;
- `save_plan_action` and `preview_plan_action` derive them from one shared
  helper.

A submitted `income_target` of `99000` is ignored, and the stored row keeps the
engine figure.

## Save loop, packaged sidecar, disposable data

Every request runs its own process, so each reload is a genuine restart.

| Step | Result |
| --- | --- |
| Open unsaved month | income `5750.00`, left `2747.50`, coherent |
| Preview `keep 1100` | left `2510.00` |
| Save | `ok` |
| SQLite row | `income_target 5750.0, savings_target 1100.0, flexible_allowance 2510.0` |
| Restart | saved `keep 1100`, income `5750.00` |
| Preview `keep 1600` | left `2010.00` |
| Save changes | `ok` |
| Restart | saved `keep 1600`, `flexible_allowance 2010.0` |
| Save `keep 99000` | refused: needs `$95,390.00` more than it has |
| After refusal | previous row unchanged at `keep 1600` |

A legacy plan row carrying a stale `income_target` of `9123.45` reads back
correctly: the stored figure is preserved for provenance while the equation
prices the month at the current `5000.00`, and re-saving heals the row.

## Gates

| Gate | Result |
| --- | --- |
| `pytest` | 1260 passed, 0 failed |
| `npm run test:charts` | 95 passed |
| `npm run build` | clean |
| `cargo check` | clean |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `git diff --check` | no whitespace errors |

`tests/test_plan_income_contract_2_7_2.py` holds 15 tests covering the
mismatch condition, the single authoritative figure, the derived-value
boundary, and the rejections that must still happen. Nine of them fail against
the 2.7.1 engine.

## Known limitations, unchanged by this release

- Marking an income source "No" in Insights does not remove it from
  `typical_monthly_income`. That control curates `reliable_income_summary`,
  which no longer prices anything, so its remaining effect is narrower than
  its label suggests. Pre-existing; changing it would move Safe to Spend's
  income basis and is out of scope for a patch.
- A monthly plan row written before `fixed_obligations` existed reads back as
  `0.00`, which is indistinguishable from a genuine "no fixed commitments".
  It corrects itself the first time the plan is saved.
- The Windows installer is unsigned; only the updater payload is signed.

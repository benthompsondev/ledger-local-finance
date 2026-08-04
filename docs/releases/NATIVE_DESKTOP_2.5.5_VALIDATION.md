# Northstar Ledger 2.5.5 — native desktop validation

Built, installed and validated on 2026-08-03. **Not pushed, not tagged, not
published, no pull request, no GitHub release, no history rewritten.**

A correctness, privacy and release-validation pass. Two Plan defects an
independent review reproduced, a public-tree privacy cleanup, and one coverage
gap closed. No new features.

## Defect 1 — a bill was charged to every month of the year

`forecast_month` asked one question of every reviewed commitment: has this
merchant been seen since the 1st? That is the right question for rent and the
wrong one for anything slower. An annual $2,400 property-tax invoice due in
November had not been seen this month in June either, so June was charged the
full $2,400 — and so was January, February, and every other month.

Reproduced on invented data: five ordinary months, an annual $2,400 bill
expected 2026-11-20, a quarterly $540 premium expected 2026-07-16, forecasting
2026-06 on the 6th.

| | Before | After |
|---|---|---|
| `upcoming_bills_total` | **$2,940.00** | **$0.00** |
| `upcoming_bills_count` | 2 | 0 |
| `projected_spending` | **$5,331.00** | **$2,391.00** |
| `projected_net` | −$2,831.00 | +$109.00 |
| `risk_level` | **danger** | **on_track** |

Neither bill belonged to June. The quarterly premium is charged to July and the
annual bill to November, each once, each at face value.

A slow bill is now placed by the occurrence its cadence detector already
expects, through one helper, `commitment_placement`, returning `paid`, `due`,
`overdue` or `other_month`. The monthly rule is untouched: a monthly
commitment not seen yet this month is still treated as coming, because that is
what the detector can actually prove.

**The invoice and its set-aside are never both charged.** The full invoice
belongs to the forecast in the month it falls due. The monthly reserve for the
same bill answers a different question — how to save up for it — and stays in
the plan equation. A test asserts projected spending equals its own components
in every month, so neither figure can leak into the other.

**An overdue occurrence is counted but not disguised.** Money is still set
against the month, because a bill whose date slipped is usually still going to
be paid and telling someone that money is free would be worse. It is reported
in its own `overdue_bills_total` and `overdue_bills_count`, each row carries
`status: "overdue"`, and the note says the occurrence was expected before this
month and has not been imported. Describing something as "expected on the
16th" three weeks after the 16th is not a description of anything.

A slow bill whose expected date cannot be determined is charged to no month.
Guessing costs real money eleven months out of twelve.

## Defect 2 — Plan assumed a payroll that never arrived

`expected_income_forecast` was `max(mtd_income, planned_income)`: the whole
historical baseline, counted as still coming however late in the month it was,
and `expected_projected_net` drove the verdict.

Reproduced on invented data: six complete months of two $2,500 deposits, July
imported through the 28th, second deposit deliberately absent.

| | Before | After |
|---|---|---|
| `mtd_income` | $2,500.00 | $2,500.00 |
| `expected_income_forecast` | **$4,583.33** | **$2,500.00** |
| `expected_income_remaining` | $2,083.33 | $0.00 |
| `expected_projected_net` | **+$2,202.53** | **+$119.20** |
| `missed_income_total` | *(did not exist)* | **$2,500.00** |
| `risk_level` | **on_track** | **watch** |
| `confidence` | **normal** | **low** |

Control, both deposits present: `expected_income_forecast` $5,000.00,
`risk_level` on_track, `confidence` normal. Nothing about the fix punishes an
ordinary month.

`scheduled_income_outlook` reads which deposits a household is actually on a
rhythm for, using `recurring_cadence.classify_gap` — the same gap classifier
the bills side uses — rather than a second opinion about what counts as
regular. `_planning_income_deposits` is now the one definition of a planning
income row, shared by the monthly baseline and this, so the two cannot
disagree about what a payroll deposit is.

- **Still ahead:** counts. Day 2 with the first deposit in and a full month of
  rent paid is an ordinary day 2, not a danger.
- **Late by up to five days:** still counts. Payroll slips for weekends and
  bank holidays.
- **Past that:** dropped. It is not counted as received or as still coming.
- **Missing at all:** cannot report `on_track`, and cannot report `normal`
  confidence. Both are rules, not side effects of the arithmetic.

Reconciliation is done on totals, not deposit by deposit, so an employer whose
statement descriptor changes is not reported as a missed payday. What matters
is whether as much money arrived as the rhythm says it should have by now.

**Irregular income is not a payday.** A one-off windfall raises the monthly
average and promises nothing about next month; it is excluded from the
schedule entirely, and an inflated baseline can no longer invent expected
income. Where no rhythm exists at all — genuine freelance income — the
baseline still supports an early-month outlook, but its claim decays as the
month runs out, and nothing can be reported as *missed* that was never
scheduled.

`projected_income` stays received-only. Safe to Spend is unchanged: balances
minus confirmed reservations. Received and expected remain separate figures on
the payload, now alongside `expected_income_basis`, `missed_income_total` and
a plain-language `income_note`.

## Privacy — the public tree

The parser docstrings carried the account numbers, a sender's name and the
balances of the real statements they were written against, in a public
repository.

| File | Was | Now |
|---|---|---|
| `parsers/detect.py` | two account numbers, one real balance | `0000000000`, `0,000.00` |
| `parsers/tangerine_chequing.py` | account number, a real name, three balances | `0000000000`, `INVENTED PERSON`, invented figures |
| `parsers/tangerine_savings.py` | two account numbers ×4 sites, a payer name, four balances | `0000000000`, `INVENTED PAYER`, invented figures |

Every literal lived in prose. The patterns that do the work match on `\d{6,}`
and `\d{10}`, so not one parse changed, and a test asserts all three still
match lines of the shape the docstrings describe — including a ten-digit
orphan line whatever its digits. Both docstrings also claimed their examples
came from real PDFs; the layout did, the figures no longer do, and they say so.

`NATIVE_DESKTOP_2.5.0_VALIDATION.md` wrote out the artifact path, the object
identifier, the commits it is reachable from, and what it held. That is a map
to a sensitive object published in the same public repository that still
carries the object. The section now records that the cleanup is outstanding
and describes the shape of a purge without the specifics.

A content guard was added, because the existing guard only ever refused
private *files* and this leak was inside one. It refuses the published
literals by name, keeps the artifact path confined to the guard files that
have to name it in order to match it, and holds the parser comments to zeros.

**Full secret/privacy scan: 343 tracked text files of 373 tracked.** Every hit
was a false positive or an obviously invented test value — a `sk-ant-...`
literal spelling out that it is invented, `invented-secret-key` fixtures,
`.env.local` in a blocklist, an icon filename containing `@`, and a character
set of the digits 0-9. No value is reproduced here.

### History reachability

The object is **still reachable**, from exactly two refs:

- `refs/heads/feature/home-weekly-checkin`
- `refs/remotes/origin/feature/home-weekly-checkin` — so it is public

It is **not** in `HEAD`'s tree, and exactly two commits contain the path.
Contents were never read; this is object-graph metadata only.

**Nothing was run.** No `filter-repo`, no force-push, no branch deleted, no
history rewritten. A purge needs Ben's explicit authorization, and the
important part is that a rewrite alone is not sufficient: GitHub keeps the
object reachable through cached views and the pre-push reflog afterwards, and
removing it there **requires a GitHub Support request**. Nothing retracts what
has already been public, and anyone who cloned earlier keeps it regardless.

## Coverage — the two-file import workflow

The pieces had tests; the workflow a person performs did not. Twelve cases now
cover selecting two files where the second partly overlaps the first: file A
imports cleanly, file B is reported as needing a choice, confirming without
answering is refused with nothing moved in either direction, re-export skips
the shared row and takes only the new one, new activity keeps a second
identical fare, the two answers reach genuinely different outcomes, accounts
stay independent both ways, and a failed file leaves neither a partial batch of
its own nor any mark on the file before it.

No defect was reproduced, so import provenance is unchanged. One contract the
loop already keeps is now written down: **separate files are separate atomic
batches**, committed as each finishes. A failure stops the run and leaves the
files before it imported, because refusing four good statements over an
unreadable fifth would help nobody.

## Verification

Nothing was skipped, weakened, deleted or marked expected-failure. The suite
carries no `xfail`.

| Check | Result |
|---|---|
| `pytest -q` | **1098 passed**, 0 failed, 0 skipped, 0 xfailed |
| Targeted CSV / import | 277 passed |
| Targeted Plan / forecast / Safe to Spend | 151 passed |
| Targeted net worth | 88 passed |
| `npm run build` | passed |
| `npm run test:charts` | 39 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` | passed, `ledger-native v2.5.5` |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `scripts.doctor` | passed |
| `git diff --check` | clean |
| release-version consistency | passed |
| privacy / secret scan | clean, values withheld |

**One existing assertion changed**, called out here so a reviewer checks the
judgement rather than discovers it.
`test_legacy_saved_income_cannot_make_plan_disagree_with_home` asserted
`expected_income_forecast == baseline` on the 30th of an invented month that
had delivered $2,502 of a usual $5,000. That is defect 2 exactly. The
assertion now pins the corrected figure and the reason; every other assertion
in the test, including the ones it is named for, is unchanged.

The version-consistency test's expected string moved from `2.5.4` to `2.5.5`,
which is what that test is for.

## The installer

| | |
|---|---|
| Path | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.5_x64-setup.exe` |
| Size | 276,960,529 bytes (264.1 MB) |
| SHA-256 | `038FDEB848BDA5FCE35F372C4D6C4745D11377A4560878AF97EDDB2AB9DD5E38` |
| Bundled files | 1,567 |

`scripts.verify_windows_installer` passed against the real PyInstaller sidecar,
not the CI placeholder.

## The installed app

Windows was read again rather than trusting the previous pass's claim. Before
this install the machine reported **2.5.4** — a single HKCU entry, both
executables 2.5.4. Installed silently over it, exit code 0.

| | |
|---|---|
| Installed Apps `DisplayVersion` | Northstar Ledger **2.5.5** |
| Uninstall entry | `HKCU\...\Uninstall\Northstar Ledger`, **the only** Ledger entry in any hive |
| `ledger-native.exe` / `uninstall.exe` | 2.5.5 / 2.5.5 |
| `scripts.verify_installed_app` | PASS |

No 2.5.4 metadata anywhere in the artifact: filename, ProductVersion and
FileVersion all read 2.5.5.

### Packaged-engine validation — 41 of 41

Run against the installed `ledger-engine.exe` with `LEDGER_DATA_DIR` pointed at
a fresh `%TEMP%` directory. Every figure invented.

Both defects were re-proved on the packaged build, not just in pytest:

- June charged **$0.00** of upcoming bills against an annual bill due in
  November and a quarterly premium due in July; projected spending $2,572;
  risk `on_track`.
- The quarterly premium falls due in **July** and the annual bill in
  **November** at its full $2,400, each in one month only.
- Projected spending equals its own components exactly.
- July on the 28th with the second payroll absent: `risk_level` **danger**,
  `confidence` **low**, `missed_income_total` **$2,500**, expected income equal
  to what actually arrived, and the note *"$2,500 that usually arrives by now
  has not been imported"*. With both paydays present: nothing missing,
  confidence back to `normal`.

Also passed: three saved monthly plans across three months, plan drift
reporting a reason, Home and Plan agreeing on Safe to Spend, no hidden goal
reservation, four net worth readings, edit without duplicating, delete, a gap
inventing no comparison, persistence across fresh engine processes, AI off with
Coach still returning deterministic findings, and backup.

One check needed the property-tax merchant categorized before it counted. The
auto-categorizer files an invented merchant name as `Uncategorized`, and an
uncategorized merchant has never been a forecast commitment in any version.
Categorizing it the way a user would makes it one, and it then behaves exactly
as the fix intends. That is pre-existing conservative behaviour, not a
regression.

### Click-level GUI pass — performed

Windows UI Automation drives the Tauri WebView2 accessibility tree, so this was
a real pass with real clicks, not an inference from engine tests. The installed
executable was launched from a PowerShell process whose `LEDGER_DATA_DIR`
already pointed at the disposable directory. **The Start Menu shortcut was
never used.**

| Action | Result |
|---|---|
| Home, Plan, Insights, Transactions clicked | all four render (5,184 / 4,543 / 7,218 / 5,521 chars) |
| Home vs Plan Safe to Spend | **both show $1,559** |
| Plan shows the nonmonthly set-aside | "$380.00/mo set aside" — the $540 quarterly and $2,400 annual spread monthly, neither charged whole |
| Note typed, "Use this plan" clicked | plan saved, Plan still renders |
| "Review recurring costs" clicked | commitment review renders (6,376 chars) |
| Net worth enabled in Settings | toggle On |
| Form opened, four figures typed | live total renders **$36,500** (7,000 + 32,000 − 2,500) |
| Saved a reading | **3 → 4 readings** |
| Saved the same month again | **4 → 4** — corrects, never duplicates |
| Edit clicked, then Cancel | count unchanged |
| Delete clicked, confirmed with "Remove" | **4 → 3 readings** |
| Relaunched | Safe to Spend $1,559 → $1,559, readings 3 → 3, Transactions still renders |

Not driven by click: statement import, which opens a native file-picker dialog.
The import paths are covered by the twelve-case engine workflow suite and by the
packaged-engine matrix above, so this is a gap in *how* it was exercised, not in
whether it was.

**The real database was never opened.** `%LOCALAPPDATA%\Ledger` held 19 files
before and after; `finance.db` kept its timestamp (2026-08-03 07:54:24) and its
exact byte length (1,314,816) through the build, the install, and every launch.
No `native-engine.log` or `native-shell.log` was written there, no listening
socket was opened, no browser process started, and no Ledger process was left
running.

## Remaining limitations

- **The installer is unsigned**, so SmartScreen will warn.
- **There is no updater.** A new version means installing over the previous one.
- An overdue nonmonthly occurrence stays counted until the cadence detector
  calls the merchant inactive, which for an annual bill can be about a year.
  Importing the statement that carries the payment ends it immediately.
- No live external AI provider call. Unchanged.
- One month-end pace test fails on Python 3.12 only. CI runs 3.14.
- A quiet and partially-imported account still cannot be told from a merely
  quiet one. Unchanged from 2.5.0.
- Net worth readings are not in the transactions CSV export. Settings says
  "Export your transactions", so the omission is transparent.
- The historical object described above is still reachable from the public
  branch. Not this pass's to fix.

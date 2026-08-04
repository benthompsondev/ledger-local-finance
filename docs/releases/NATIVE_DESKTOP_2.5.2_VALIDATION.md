# Northstar Ledger 2.5.2 — native desktop validation

Built, installed and validated on 2026-08-03. **Not pushed, not tagged, not
published.** The branch it sits on, `feature/home-weekly-checkin`, is public,
so these commits become public the moment they are pushed.

One feature rebuilt, one defect that rebuild exposed, and an independent
re-check of the 2.5.1 import work. No other behaviour changes.

## Net worth was three half-features and none of them kept history

The Net worth section did not work, and the reason was structural rather
than cosmetic.

**The trend was a projection.** `net_worth_trend()` took one starting figure
the user typed once and added each *complete* month's kept amount from the
imported statements. It drew that as confidently as a measurement. Because
kept-per-month comes from chequing statements, the line could not see a
brokerage account move, a mortgage come down, or a car lose value. Reproduced
directly: a starting figure of $5,000 produced 5,000 → 6,740 → 8,480 →
10,220, and a brokerage account holding $48,250 changed none of it.

**Nothing ever recorded a real reading.** `net_worth_snapshots`, the only
table shaped for history, is written solely by the retired Streamlit page and
the demo-data script. On a fresh install it holds zero rows, so the "measured"
series drawn beside the projection was empty on every machine. Confirmed by
counting: 0 rows in `net_worth_snapshots`, 0 in `net_worth_estimates`.

**Three numbers competed.** A calculated-from-balances figure, a separate
quick assets-and-debts estimate, and the starting point, none feeding the
others. That is the repo's own "one canonical number per concept" rule broken
three ways.

**There were no components and no cadence.** Cash against investments against
other assets is the breakdown that makes net worth mean anything, and there
was nowhere to put it. Nothing asked for a monthly update, so nothing ever
accumulated.

### What replaces it

One reading a month, keyed by month: cash, investments, other assets, debts.
Net worth is those four figures and nothing else. Every point on the chart is
a reading someone entered, which is why there is only one line now.

The four are prefilled from whatever account balances exist, grouped by
account kind, so the monthly update is a review rather than data entry. Every
prefilled figure stays editable, because Northstar only knows the accounts it
has balances for and most people own something it has never heard of. The
prefill names the account behind each figure so a total that looks wrong can
be traced instead of guessed at.

Recording August twice corrects August. It never doubles up.

Kept-per-month was not thrown away. It is still the best explanation of *why*
net worth moved, so it now sits alongside two real measurements instead of
standing in for them: "your statements account for $1,740, your net worth
moved $2,900" makes the $1,160 gap visible, and that gap is usually the
investment growth the statements cannot see.

Components are refused when negative, because a negative debt is an asset and
a negative cash balance is a debt, and accepting either flips a sign
downstream. Net worth itself can be negative, and is. A percentage change is
absent rather than wrong when the previous month was zero or negative.

Anything typed into the retired paths is carried forward, with the
unexplained amount in other assets and a note saying the breakdown was never
recorded. The total survives exactly and the label stays honest.

The seven tests covering the projection are gone with the projection.

## The defect the rebuild exposed

Found by stress testing, not by reading the code.

**A factory reset left every net worth reading in the database.** The new
table was not in `RESET_TABLES`, so "remove my financial data" kept the most
personal figures in the app, and they are the ones that cannot be recovered by
importing a statement again because nobody typed them twice. Verified before
and after: 4 readings survived a confirmed reset.

`net_worth_entries` is now created in `init_db` rather than lazily on first
use, which is what lets it join the reset list and be counted. The reset
confirmation now says how many readings are about to go rather than listing
only transactions and accounts, and reset is reachable when readings are the
only thing recorded; it was previously gated on having transactions.

**A correction to the first stress run.** It appeared to show backup and
restore losing two readings. That was the harness reading a payload key that
does not exist (`create_backup` returns a `Path`, and the engine payload has
no `path` field), so restore was called with an empty string. Backup and
restore carried the table correctly all along: 6 readings backed up, 2
deleted, 6 returned. Now covered by a test that calls the real API.

## Independent re-check of 2.5.1

Written from the claims rather than from the tests that shipped with them.

| Claim | Verdict |
|---|---|
| Preview and confirmation report the same duplicate count | **Confirmed.** A full re-export under a new filename inserts 0 and skips 3. |
| Revised exports do not duplicate shared transactions | **Confirmed**, via an explicit choice. |
| Contained transactions are not silently discarded | **Confirmed**, via the same choice. |
| Several partial statements cannot form one complete month | **Confirmed.** Two half-covered accounts, and three fragments of a single account, both stay partial. |
| Home no longer overstates the no-network promise | Confirmed by reading the copy. |
| Empty-state month names no longer lowercased | Confirmed by reading the copy. |

Two of these needed a correction to my own first reading of them. The revised
export and the contained statement did not import, and the first interpretation
was that rows were being eaten. They were not: 2.5.1 makes a partial overlap
**fail closed** with "Some, but not all, rows match an earlier import. Choose
whether this is an updated export or separate activity before importing it."

That is the right answer to the ambiguity flagged in 2.5.0, and better than
the superset heuristic that shipped there, which decided silently. Both
resolutions were then verified end to end:

- **updated export** → 2 shared rows skipped, 1 new row inserted, 3 total;
- **separate activity** → all 3 inserted, 5 total.

The choice is reachable in the UI (`AddDataView.tsx`: "Are these updated rows
or separate activity?"), so the refusal is a question rather than a dead end.

## Verification

Nothing was skipped, weakened, or marked expected-failure. The suite carries
no `xfail`.

| Check | Result |
|---|---|
| `pytest -q` | **910 passed**, 0 skipped, 0 xfailed |
| Net worth module | 45 passed |
| `npm run build` | passed |
| `npm run test:charts` | 16 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` / `cargo test` | passed, `ledger-native v2.5.2` / 5 passed |
| `scripts.check_tracked_files` / `scripts.doctor` | passed / passed |
| `git diff --check` | clean |

### Net worth stress test — 32 of 32

| Area | Result |
|---|---|
| 120 monthly readings of one-cent steps | exact, no float drift |
| 0.1 + 0.2 | 0.30, not 0.30000000000000004 |
| $999,999,999.99 | kept exactly |
| Net worth of −$499,900 | allowed; recovery reports +$250,000 |
| Percent change from a negative base | withheld |
| Negative component (−0.01 and −1e9) | refused |
| Hostile notes (SQL, HTML, JNDI, RTL, emoji, 4,000 chars) | stored safely, no amount changed |
| Malformed months (`2026-99`, `'; --`, `\x00`, `2026-0a`) | refused |
| 480 readings across 40 years | write 0.86s, overview 0.003s |
| Out-of-order writes | returned sorted |
| A ten-year gap | no invented months |
| 8 concurrent engine processes | 8/8 succeeded, 8 rows |
| Backup and restore | every reading returned exactly |
| Factory reset | counted in the confirmation, then removed |

## The installer

| | |
|---|---|
| Path | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.2_x64-setup.exe` |
| Size | 276,962,039 bytes (264.1 MB) |
| SHA-256 | `BAE1A57D7887E403C5B95BD2E401D15A6833E7D8794221919DC1EEFD1E6C5EB4` |
| Bundled files | 1,567 |

## The installed app — 27 of 27

Installed silently over 2.5.1, exit code 0. The `%LOCALAPPDATA%\Ledger` data
directory stayed at 19 files and `finance.db` kept its timestamp
(08/03/2026 07:54:24) through the install and the launch.

| | |
|---|---|
| Installed Apps `DisplayVersion` | Northstar Ledger **2.5.2** |
| Uninstall registry key | `HKCU\...\Uninstall\Northstar Ledger`, sole Ledger entry in any hive |
| `ledger-native.exe` / `uninstall.exe` | 2.5.2 / 2.5.2 |
| Launch | window opened and stayed up, no shell errors logged |

Every check below ran against the installed `ledger-engine.exe` with
`LEDGER_DATA_DIR` pointed at a fresh `%TEMP%` directory. Every figure invented.

Prefill grouped chequing + savings into cash ($17,400), the brokerage into
investments ($48,250) and the card into debts ($1,430), netting $64,220 and
naming all four sources. Six readings recorded; month-over-month, three-month,
since-first, best, worst and the four component moves all reported, with debts
correctly falling. The first reading carries no invented change. Recording a
month twice corrected it rather than adding a seventh. A negative component was
refused with a message that says where the money belongs; a net worth of
−$89,900 was accepted. Deleting a recorded month worked; deleting one that does
not exist errored clearly. The reset preview counted 6 readings. The 2.5.1
import behaviour carried forward intact, and ambiguous thousands punctuation is
still blocked.

## Not verified

- **The GUI was not driven.** The app was launched and its window confirmed,
  and the Net worth screen was exercised through the packaged engine rather
  than by clicking. The chart, the entry form and the reading table were
  built against the payload shapes asserted above, and were type-checked, but
  no screenshot of the rendered section was taken.
- **No live external provider call.** Unchanged from 2.5.1.
- **One test fails on Python 3.12 only.** Unchanged; CI runs 3.14.
- **The installer is unsigned and there is no updater.** Unchanged.
- **A quiet and partially-imported account still cannot be told from a merely
  quiet one.** Unchanged from 2.5.0.
- **Net worth readings are not exported.** `export_transactions` covers
  transactions only, so the readings live in the database and its backups.
  Worth adding, and deliberately out of scope here.

## History cleanup

Unchanged and still not executed. The plan and its impact are in
`NATIVE_DESKTOP_2.5.0_VALIDATION.md`.

# Northstar Ledger 2.9.0 — validation

A minor release. Home gains a forward-looking section: the recurring money
Northstar recognises from imported statements, placed on the dates its rhythm
suggests.

Every figure below comes from disposable synthetic data. No real financial
data was read, printed or stored at any point.

## What the claim is, and what it is not

An expected event here is **an observation about the past stated as a date**.
"Hydro has arrived every 30 to 32 days for nine months, most recently on the
24th" becomes "expected around the 24th". It is not a prediction that the
charge will occur, and the interface never says it is.

Three boundaries hold, and most of the test file exists to keep them.

**No balance, ever.** The payload carries events and their sums. It does not
carry, and must not be used to derive, a projected balance, a future Safe to
Spend, or any claim that the user will or will not have enough. A test
serialises the whole payload and asserts the absence of `balance`,
`projected`, `shortfall`, `will_have`, `runway` and `forecast`.

**Two clocks, kept apart.** The window is measured from the real local date,
because someone asking what is coming means the calendar. Every piece of
evidence comes from imported statements, which usually stop earlier. Both
dates are reported and the gap is disclosed.

**Nothing the user has waved off.** A merchant marked "Not recurring" and an
income source marked "No" are both absent, whatever the arithmetic says.

## What already existed, and what is new

Most of the arithmetic was already written and already trusted.

| Already in the engine | Used for |
| --- | --- |
| `recurring_cadence.merchant_cadences` | cadence, amount, next date, active/stale, confidence |
| `analytics._income_schedule` / `_expected_dates` | payday rhythm and its dates |
| `analysis_period.supported_latest_date` | where the statements actually stop |
| `config.constants.FRESH_*` | the freshness bands Home already uses |
| `TxPrefill` | the drill contract |

New in this release: `utils/upcoming.py`, one engine action, one Tauri
command, one React component, and two small additions to existing functions —
`merchant_cadences` now returns `recent_charges`, and `analytics` gained a
public `expected_income_events`.

## The income-preference rule, deliberately narrow

`_planning_income_deposits` reads every planning-income transaction with no
reference to income-source preferences, so an excluded source would have been
drawn as an expected payday.

The filter was added in the new public `expected_income_events`, not in
`_planning_income_deposits`. The monthly baseline and Safe to Spend read those
same deposits, and changing what they count is a decision about the plan's
income contract rather than one a forward view should make on their behalf.
The narrow rule enforced here is only that Northstar must not draw a payday
marker for a source the user has explicitly called undependable.

The broader debt — that "Stable: No" does not reach every planning
calculation — is unchanged and remains open.

## Defects found and fixed

**A monthly bill drifted a day earlier every cycle.** `merchant_cadences`
derives its next date by adding the median gap, which is correct for weekly
and fortnightly rhythms and wrong for monthly ones: a bill that has landed on
the 24th for eight months has a median gap of 30 or 31 days, so gap
arithmetic predicts the 23rd, then the 22nd, and eventually walks out of the
month. The radar projects whole-month rhythms from the last real charge,
keeping the day of the month. `merchant_cadences` itself is unchanged, because
its date feeds the plan's reserve placement and that is a wider blast radius
than this release should take.

**An expected payday had no evidence behind it.** Income events carried no
recent amounts, so the one number a person would want to check was the one
they could not. `expected_income_events` now returns the last three deposits.

## Trust behaviour

| Situation | What the radar does |
| --- | --- |
| Statements within 7 days | normal confidence, no note |
| 8–21 days behind | discloses the gap |
| Over 21 days behind | prominent note, and **every** item's confidence drops one step |
| Merchant marked "Not recurring" | absent |
| Income source marked "No" | absent |
| Merchant gone quiet (`is_active` false) | absent |
| Two charges only | absent — two points are not a rhythm |
| Cycle due before the data cutoff, not in the data | shown as "not seen yet" |
| Cycle due after the data cutoff | shown normally; absence is unknown, not missing |

That last row is the subtle one. Between the statement cutoff and today,
nothing is knowable, so calling a charge missing there would turn a stale
import into a false alarm. A test asserts no item is flagged "not seen yet"
for a date the statements never reached.

## Verified through the real chain

The packaged sidecar was exercised over stdin/stdout, and the built frontend
was driven in a browser against recorded engine output.

| Step | Result |
| --- | --- |
| `upcoming_money` over the packaged engine | `ok`, strict JSON round trip |
| Rendered on Home, below Safe to Spend | confirmed by element geometry |
| Timeline marks | 7 (3 income above the line, 4 outgoing below) |
| Expand an event | `May 24 $94.00 · Jun 24 $94.00 · Jul 24 $94.00`, last seen, and the disclaimer |
| Drill from an event | Transactions, filtered to `HYDRO CO` |
| 1280×800 | card 640 px collapsed, no horizontal overflow |
| 1024×768 | card 658 px, no horizontal overflow, no truncated labels |

Screenshots could not be captured: the Browser pane does not composite frames
in this environment. Verification was structural — geometry, computed styles,
accessibility tree, live clicks and rendered text. That is how the row height
problem was found: the first layout used stacked two-line rows at 70 px each,
putting the card at 885 px, which buried the rest of Home.

## Gates

| Gate | Result |
| --- | --- |
| `pytest` | 1335 passed, 0 failed |
| `tests/test_money_radar_2_9_0.py` | 32 tests |
| `npm run test:charts` | 95 passed |
| `npm run build` | clean |
| `cargo check` | clean |
| `cargo test` | 5 passed, including command registration and grants |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `git diff --check` | no whitespace errors |

Of the 32 radar tests, nineteen assert that something is **absent** or
refused: a rejected merchant, a dead subscription, an excluded income source,
irregular deposits, a two-charge merchant, an event outside the window, a
duplicate identity from normalization, a quarterly bill drawn monthly, a
balance projection, the word "available", an invented payday, and a charge
called missing for a date the statements never covered.

## Known limitations

- The window is anchored to the real local date. A machine with a wrong clock
  will place the window wrongly; the statement cutoff is always shown, so the
  discrepancy is visible.
- Confidence is the cadence engine's own label, coarsened by staleness. It
  reflects how regular the history is, not how likely the merchant is to bill.
- "Stable: No" still does not reach `typical_monthly_income`; unchanged here
  and tracked separately.
- The Windows installer is unsigned; only the updater payload is signed.

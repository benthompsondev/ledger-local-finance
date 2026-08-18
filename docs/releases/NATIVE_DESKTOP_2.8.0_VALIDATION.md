# Northstar Ledger 2.8.0 — validation

A minor release. Insights gains observations drawn from finished months, and
the observation feed gets a proper home with its evidence attached.

Every figure below comes from disposable synthetic data built by the test
fixtures. No real financial data was read, printed or stored at any point.

## What changed, and why it is a minor release

2.7.x were patch releases fixing Plan. This one adds user-facing capability:
three new observations, a new surface on Insights, a reworked Home
relationship, and a ranking model that decides which few observations are
worth a person's attention.

## The observations competing for the feed

| Kind | Question it answers | History needed | Direction |
| --- | --- | --- | --- |
| `kept_trend` | has what I keep each month moved to a new level | 4 finished months | both |
| `category_drift` | has a category moved and stayed moved | 4 finished months | both |
| `month_difference` | what made the last finished month differ | 2 finished months | both |
| `small_spend` | where did money leave in amounts too small to see | 1 finished month | watch |
| `price_rise` | has a recurring charge stepped up | 4 charges | watch |
| `frequency_creep` | more trips, or bigger ones | 3 months | watch |
| `above_usual` / `improved` | is this month unusual for me so far | 3 months | both |
| `weekday_pattern` | which day actually costs most | 56 days, 21 active | watch |
| `year_ago` | how does this compare with last year | 13 months | both |

## Three defects found and fixed in this pass

**The recurring price observation had never once been shown.** It read
`change["previous"]`, `change["current"]` and `item["times_per_year"]`.
`merchant_cadences` returns `previous_amount`, `current_amount` and no
`times_per_year` at all, so `float(None)` raised on every call. The feed's
guard that stops one broken detector blanking the page swallowed it silently.
It now reads the correct keys and uses the `annual_impact` the cadence
detector already computes, which is correct for quarterly and annual billing
where multiplying by twelve was not.

**"So far this month" observations opened an unbounded list.** `above_usual`
and `improved` claim something about the first N days of the current month,
but their transaction button carried no dates, so it opened the category's
entire history. Both are now bounded to the days the claim is measured over.

**Raw database dates reached the screen.** The year-ago card read "Against
2025-07", and the weekday card ended "180 days to 2026-07-29". Both now read
as dates a person writes.

A regression test now calls every registered detector directly on a profile
that triggers all of them, so a detector that raises on every invocation
fails the suite instead of hiding behind the feed's guard.

## Ranking

Impact is normalised to dollars a month by each detector, then multiplied by
a documented weight for evidence quality and actionability. A $4-a-charge
monthly price rise is $48 a year and cannot outscore a $600 swing in what a
month kept; before this each detector applied its own hand-picked multiplier
to figures measured over different windows.

Where two observations share a subject, the better-evidenced one is shown and
the other is dropped. A three-month trend beats a one-month spike about the
same category even when the spike is the larger number, because the trend
contains the spike's claim and adds to it. The two whole-month observations
share the subject `kept`, so a run of months and a single month-on-month
comparison cannot both take a slot.

## Positive observations

Both multi-month detectors report in either direction and are routed by what
they found rather than by which function found it. One improvement is shown up
front; a second true one falls through to the collapsed list rather than being
discarded, so a working detector cannot be invisible.

Verified on synthetic data: a profile where Shopping fell for three finished
months produces both "Shopping has run lower for 3 months" and "You have kept
more for 3 months running", one shown and one behind the click.

## Transaction buttons

| Kind | Filter | Bounded |
| --- | --- | --- |
| `category_drift` | category + the recent block | yes |
| `small_spend` | category + the finished month | yes |
| `above_usual` / `improved` | category + month to date | yes |
| `month_difference` | the latest finished month | yes |
| `frequency_creep` | merchant + both comparison windows | yes |
| `price_rise` | merchant, all charges | deliberately not |
| `kept_trend`, `weekday_pattern`, `year_ago` | none offered | — |

Three claims cannot be honestly represented by one transaction filter, so they
carry no button. A test asserts that the only kinds allowed to omit it are
those three, and that every other button carries both a subject and a date
range.

Verified live in a browser against the built frontend:

| Card | Landed on |
| --- | --- |
| Shopping has run higher for 3 months | `Shopping`, spending, `2026-05-01` to `2026-07-31` |
| Small buys in Food & Convenience added up | `Food & Convenience`, spending, `2026-07-01` to `2026-07-31` |
| NORTHSIDE OUTFITTERS costs more than it used to | `NORTHSIDE OUTFITTERS`, spending, undated |

## Home and Insights

Home shows the two strongest headlines with their figures and a button
through, at 78 px per card. It no longer repeats the full claims. Insights
carries the whole experience: the figure, the claim, the working behind a
disclosure, the sample-size note and the transactions.

## Layout

Measured on the built frontend at 1280x800 and 1024x800. The observation
section spans 714 px before the first chart heading, with evidence collapsed
by default; open, four cards ran to 1554 px, which is why it is collapsed. The
lead card spans the row, the rest fall into a responsive grid, three across at
1280 and two at 1024. No horizontal overflow at either width.

Screenshots could not be captured: the Browser pane does not composite frames
in this environment, and desktop capture was unavailable. Verification was
therefore structural — element geometry, computed styles, accessibility tree
and live interaction — which is how the density problem above was found.

## Gates

| Gate | Result |
| --- | --- |
| `pytest` | 1302 passed, 0 failed |
| `npm run test:charts` | 95 passed |
| `npm run build` | clean |
| `cargo check` | clean |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `git diff --check` | no whitespace errors |
| packaged sidecar | `insight_feed` over stdin/stdout, strict JSON |

`tests/test_month_trends_2_8_0.py` holds 41 tests. Sixteen of them assert
silence rather than a finding: one expensive month is not a trend, one good
month is not a change of level, three finished months is not enough history,
moves too small in dollars or in percent, spending that only started recently,
partial months in either direction, months that finished alike, a handful of
small purchases, trivial small purchases, a big-purchase category with a few
strays, one event under two names, and a broken detector taking down the page.

## Known limitations, unchanged by this release

- Marking an income source "No" in Insights does not remove it from
  `typical_monthly_income`. Pre-existing; changing it would move Safe to
  Spend's income basis.
- A monthly plan row written before `fixed_obligations` existed reads back as
  `0.00`. It corrects itself the first time the plan is saved.
- The Windows installer is unsigned; only the updater payload is signed.

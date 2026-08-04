# Northstar Ledger 2.4.0 — native desktop validation

Not tagged, not published as a release. The branch it was built from,
`feature/home-weekly-checkin`, **is public** on GitHub; it was pushed after
this document was first written, and calling the release private was true
only for the few hours before that.

> **Corrections, 2026-08-02.** Three claims below were checked again during
> the 2.5.0 pass and two of them did not hold up. They are corrected in
> place, and the original wording is described rather than quietly replaced,
> because a validation record that edits itself is not evidence.
>
> - "Nothing skipped or weakened" was wrong: the suite carried one strict
>   `xfail` for a known defect, and an expected failure is a skipped
>   assertion however it is spelled. The line now says so. That defect is
>   fixed in 2.5.0 and the `xfail` is gone.
> - "Installed Apps: 2.4.0" was not checked. Only the executable's
>   ProductVersion was read. The uninstall registry has since been read
>   directly and does say 2.4.0, so the claim was true, but nothing at the
>   time established it. `scripts/verify_installed_app.py` now checks it.
> - The private-database note at the end stands, and is unchanged.

A focused hardening release. It packages the two commits that were tested in
source but never built into an installer, adds one behavioural fix found while
validating them, and locks the behaviour down with three new test modules.

## What is in this build

Everything in the installed 2.3.0, plus:

| Commit | Change |
|---|---|
| `7d005ef` | Make plan readiness truthful and useful |
| `739a781` | Harden rollover and CSV import trust |
| `d495b7e` | Read month completeness from one place on Home |
| `28076f3` | Prove provider failures never reach the user as an answer |
| `8cdc415` | Stop a month that is still running from counting as finished |
| `7cc779f` | Keep a provider's own words out of the engine log |

`7d005ef` and `739a781` existed at 2.3.0's validation but landed after the
release candidate was cut, so no installed build has ever contained them.

## The defects this release fixes

### 1. Home charted a half-imported month as a finished one

`utils/insights.monthly_aggregates` stamps each month with a `complete` flag
derived from `statement_coverage`. `home_dashboard_action` discarded it and
recomputed completeness as `month < current_month`, taken from the wall clock.
Any past month was therefore "complete" regardless of how little of it had
been imported.

That flag is not cosmetic. `desktop/src/charts.tsx` filters on it to choose
the best month, the weakest month, the typical savings rate and the trend
direction. A month holding eight days of data could set any of them, and the
partial marker that would have warned the user never appeared.

Reproduced on invented data: five months of history with one month imported
only to the 8th. `statement_coverage` and `monthly_aggregates` both called it
partial; the Home payload called it complete.

Fixed by reading the canonical flag. `utils.analytics.complete_month_income`
already consumes the same list for the income baseline, so Home now agrees
with the number Plan quotes.

### 2. A month still running counted as finished

Reading the canonical flag exposed a defect underneath it. `statement_coverage`
judges a month on coverage: data starting within 10 days of the 1st, ending
within 7 days of the last day, spanning at least 14 days, across at least 3
distinct days. That end-of-month tolerance exists because statements often stop
a few days early, and it cannot tell "this statement ended on the 28th" apart
from "today is the 28th".

So in the last week of every month, the current month passed all four
thresholds and was classified complete. That is a wrong number, not a cosmetic
one: on the 28th of a 31-day month holding one of its two paydays,
`typical_monthly_income` averaged the part month in and quoted **$4,166.67**
where the finished months say **$5,000**. Safe to Spend is derived from that
baseline. The same flag decides which months the Home chart compares, so the
part month also became a candidate for weakest month and pulled the typical
savings rate down.

A month that has not ended is not missing data. Those days have not happened
yet, so it cannot be complete. Fixed at the canonical source, which corrects
the income baseline and the chart together.

### 3. A provider's own words could put the API key in the engine log

Found during the independent review; described below.

## Phase 1: Home and Plan trust, validated live

Eleven disposable profiles driven through the real engine dispatch, each in
its own temporary data directory. Profiles: empty first run; one sparse
account; one current asset account; asset and liability accounts ending on
different dates; several complete months plus a partial current month; data
stale by more than three weeks; data fresh but its month ended; no balances;
current balances; reviewed commitments; commitments changed after review.

| Criterion | Result |
|---|---|
| A completed month is never offered as money available now | confirmed working |
| A partial current month still shows a clearly labelled estimate | confirmed working |
| Home gives a useful observation when account coverage differs | confirmed working |
| "Forecast confidence is limited" is context, not the whole check-in | confirmed working |
| The observation names an as-of date | confirmed working |
| Home needs no AI for a deterministic insight | confirmed working |
| Plan tasks come from real unresolved conditions | confirmed working |
| Plan drift matches the saved-versus-detected comparison | confirmed working |
| A coverage limitation is an advisory, never a blocking gate | confirmed working |
| Every task names a navigation target | confirmed working |
| All screens share one analysis period | confirmed working |
| The Home chart uses the canonical completeness flag | **defect, fixed** |

The three profiles with a 24-day gap between account cutoff dates return a
full check-in with a Safe to Spend figure. Before `7d005ef` that gap replaced
the entire check-in with "Forecast confidence is limited", and the balance
requirement sat in the blocking list rather than the advisory list.

A separate twenty-check mutation sequence confirmed each task clears the
moment its condition is met, and that the result survives a reload on a new
connection:

- saving a plan clears the plan task and raises no drift
- confirming income clears the income task
- confirming recurring costs clears the commitments task
- a new monthly obligation raises exactly one drift task naming
  `plan#commitment-review`
- re-saving clears the drift
- entering balances clears the balance advisories
- with everything resolved, `forecast_ready` becomes true

### Not reproducible

The Plan task "Confirm your recurring income sources" was suspected of being a
dead end with nothing to confirm. It is not: `income_confirmation_candidates`
offered both invented payroll sources, and confirming them through
`set_income_source_preference` cleared the task and persisted. The first
harness that suggested otherwise was reading the wrong helper.

## Phase 2: CSV ingestion

`tests/test_csv_matrix_2_4_0.py`, 68 cases, all invented files.

**Accepted shapes**, each asserted to reduce to the identical canonical
reading of −84.20, +1500.00, −6.75 summing to 1409.05: comma, semicolon and
tab delimiters; decimal comma; thousands separators; split debit and credit;
unsigned amounts with a direction column; a signed amount agreeing with debit
and credit; quoted delimiters inside descriptions; reordered headers; unknown
extra columns; trailing blank lines; timestamps on dates. Encodings UTF-8,
UTF-8 with BOM and Windows-1252, with LF and CRLF, all agree. UTF-16 LE is
read; UTF-16 BE is refused rather than guessed.

**Rejected shapes**, each asserted to commit zero rows through the engine's
`confirm_import` rather than merely returning a blocked parse: a signed amount
contradicting debit and credit; a sign flipped against the split columns; a
row carrying both a debit and a credit; a file switching layout mid-way; mixed
currencies; a blank amount; an unreadable amount; a truncated row; an unquoted
comma adding a field; a broken date; mixed decimal conventions.

**Layouts that ask instead of guessing**: duplicate amount headings, two
columns both detected as the amount, headerless files from an unknown bank,
and Excel serial dates all block with a reason the user can act on.

The sign contract holds across chequing, savings, mastercard, visa and line of
credit: account type never flips a sign. Preamble lines are skipped and
reported. An unreadable footer blocks rather than dropping a row, because
silently discarding one risks discarding a real merchant.

End to end: a statement imports three rows, associates them all with the
chosen account, produces three distinct fingerprints, and re-imports as zero
inserted and three skipped. Two genuinely identical same-day fares both land.
The same file cannot be imported into a second account.

### Suspected defect, already fixed

The task contract flagged that `csv_import.py` might accept both a signed
amount and populated debit/credit fields while preferring the signed one
without reconciling them. It does reconcile them, in
`_redundant_amount_layout_problem`. Matching representations are accepted and
the signed source amount is preserved; a contradiction names the offending row
and blocks the file before anything is committed. That protection arrived in
`739a781` and had never been exercised in a packaged build.

## Phase 3: AI provider interoperability

`tests/test_ai_provider_matrix_2_4_0.py`, 42 cases. The existing AI tests
patch `urllib.request.urlopen`; this module stands up a real HTTP server on a
loopback port so the provider code makes genuine socket requests. No key and
no network are required.

**Configurations proven**: Ollama and LM Studio as loopback endpoints, which
need no key and are sent no `Authorization` header; MiniMax, OpenAI and a
custom HTTPS endpoint, which are required to be HTTPS and do carry a bearer
token. A remote provider on plain HTTP is refused before it can be saved.

**Status codes**, each mapping to exactly one classified failure: 400, 401,
403, 404, 408, 429, 500, 503.

**Response shapes refused**: not JSON at all; empty choices; a message with no
content key; empty content; a top-level array; an object with no choices;
`finish_reason` of length, content_filter, or an unrecognised value.

**Transport**: connection refused on a closed loopback port produces guidance
naming the local runtime; a provider that accepts the body then stalls times
out rather than hanging.

**Retry policy**: a transient failure is retried once and can then succeed; a
rejected key, permission or model is never retried.

**Grounding**: the connection test carries no merchant, category or amount
from the seeded data; disabling the feature makes zero requests; a figure the
model invented is removed before display; a figure genuinely present in the
evidence is not removed and does not trigger a needless retry; a failed
question leaves Coach able to answer the next one.

**Settings under load**: twenty-five sequential read, save and connection-test
cycles, then four concurrent writers on their own connections. No "database is
locked" error was reproduced.

The stored key is never returned by an ordinary settings read and never
appears in an error string, including when the provider echoes it back inside
a 401 body.

### Local model testing

Neither Ollama nor LM Studio is installed on this machine and nothing is
listening on ports 11434 or 1234, so the loopback coverage above stands in for
a live run. To confirm manually:

1. Install Ollama and run `ollama pull llama3.1`.
2. In Settings, choose Ollama, address `http://127.0.0.1:11434/v1`, model
   `llama3.1`, leave the key empty.
3. Save and test the connection. It should report connected with no key.
4. On Home, use "Explain this" on any finding and confirm prose comes back
   with no figure that is not already in the finding.

## Independent review

A bounded outside question, carrying no private data, asked which realistic
OpenAI-compatible failure modes were missing from the error matrix. It came
back with ten ranked items. Four were worth testing, and all four turned out
to be already handled, which is now recorded in tests rather than left to
memory: streamed SSE frames returned for an unstreamed request (refused as
unreadable), a null choices list (refused, not a crash, because the existing
handler already catches `TypeError`), a provider that accepts the body then
stalls (times out), and locale or abbreviated number notations evading the
figure guard (`$1.247` parses to 1.24 and is removed as unsupported). Its
remaining suggestions were rejected: currency-symbol drift does not apply
because mixed-currency files are refused at import, and unfenced reasoning
text still has every figure checked against the evidence.

One genuine gap it raised is recorded under limitations below.

A separate read-only review of the diff at `28076f3`, given the named risks
and the test evidence, returned three confirmed defects and one suspicion.

*Accepted and fixed.* **The API key could reach `native-engine.log`.**
`_send_chat` replaced an `HTTPError` with a sanitized `AiProviderError` but
chained it with `from exc`, and the engine logs a failed action with
`logger.exception`, which formats the whole chain. A provider that reflects
the Authorization header back in its 401 reason therefore wrote the key into
the log in plain text, while the JSON returned to the screen stayed clean.
Reproduced exactly as described, fixed in `7cc779f`, and covered by a test
that drives the real engine and reads the log rather than asserting on the
raised error. The original test could not have caught this.

*Accepted, already fixed.* **A still-open month could be called finished.**
The review reached this independently while it was running. Its repro
(today 24 August, activity on the 1st, 10th and 24th) was replayed against
`8cdc415` and August is now partial. The observation that the new tests did not cover an in-progress month was
true of the reviewed commit, and is covered now.

*Accepted, not fixed here.* **Identical purchases survive only within one
file.** Reproduced: importing one genuine `$3.35` fare in each of two
statements stores one row, not two, and the second is reported as skipped.
This is pre-existing rather than a 2.4.0 regression. The obvious fix, counting
occurrences against stored rows instead of per file, would break re-import
idempotency and let a whole statement double-count silently, which is the
worse failure. Recorded as a strict `xfail` in
`tests/test_csv_matrix_2_4_0.py` so it flips to a pass when the provenance
question is settled, rather than freezing today's answer into an assertion.

*Noted, not a defect.* The Plan history task says "Add one complete month" but
clears after any 28-day span, so two sparse rows 27 days apart can clear it
while coverage still calls the data partial. Wording, not arithmetic.

The review also observed that `stable_income_summary` and
`utils/patterns.py` each apply their own notion of which month is current. Both are outside this
release's scope and neither feeds Safe to Spend, but they are the remaining
copies of a rule this release removed one of.

## Verification

Run at `HEAD`. One test was weakened, and it is the `xfail` in the table
below: `test_identical_purchases_in_two_files_both_survive` recorded a known
defect as an expected failure rather than a passing assertion. Everything
else ran normally.

| Check | Result |
|---|---|
| `git diff --check` | clean |
| `pytest -q` | **797 passed, 1 xfailed** |
| `npm run build` | passed |
| `npm run test:charts` | 6 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` | passed, `ledger-native v2.4.0` |
| `cargo test` | 5 passed |
| `scripts.doctor` | passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |

## Version surfaces

`package.json`, `package-lock.json`, `src-tauri/Cargo.toml`,
`src-tauri/Cargo.lock` (the `ledger-native` entry only), `tauri.conf.json` and
`utils/__init__.py` all read 2.4.0, and `test_release_versions_are_consistent`
enforces that they agree. Settings and About read the version from Tauri, so
`tauri.conf.json` covers them.

`packaging/windows/Ledger.iss` and `version_info.txt` remain at 1.6.0 on
purpose. They belong to the retired Inno Setup path used by
`scripts/build_windows_installer.ps1`, not to the shipping NSIS build, and the
version-consistency test pins them there deliberately.

## Artifact

| | |
|---|---|
| Path | `src-tauri\target\release\bundle\nsis\NorthstarLedger_2.4.0_x64-setup.exe` |
| Size | 276,929,594 bytes (264.1 MiB) |
| SHA-256 | `480E07286BD5DC4E600D4F83ACBC2C0CE01A8812D241DB52996ABB56675CD025` |
| Bundled files | 1,567 |
| Packaged sidecar | 194 MiB |
| Authenticode | **NotSigned** |

The SHA-256 was computed independently after the build and matches what the
in-build verifier reported.

## Installed-build validation

Installed silently over the existing per-user 2.3.0. Exit code 0, and the
`%LOCALAPPDATA%\Ledger` data directory was left at its existing 19 files.

| | |
|---|---|
| Installed Apps `DisplayVersion` | Northstar Ledger **2.4.0** (read from the uninstall registry on 2026-08-02, not at build time) |
| `ledger-native.exe` ProductVersion | 2.4.0 |
| `ledger-native.exe` FileVersion | 2.4.0 |
| Packaged engine | `ledger-engine.exe` present in the install directory, and every action below was executed through it |

Everything below ran against the installed `ledger-engine.exe` with
`LEDGER_DATA_DIR` pointed at a fresh directory under `%TEMP%`, never the
repository source and never the real database.

**Read actions.** `home_summary`, `home_dashboard`, `plan_summary`,
`insights_summary`, `ai_coaching_summary`, `list_accounts` and
`list_transactions` all responded.

**Import, signs and totals.** An invented three-row chequing statement and an
invented two-row card statement imported as 3 and 2 rows. Every sign survived
the packaged path: −120.00, −84.20, −6.75, +45.00, +1500.00, summing to
1,334.05 across an asset and a liability account.

**Duplicates.** Re-importing the chequing statement inserted 0 and skipped 3.

**Fail-closed.** A file whose signed amount contradicted its debit column was
refused, naming the offending row, and the database still held exactly 5 rows.

**Home and Plan.** With data ending in April and today in August, Safe to
Spend reported unavailable with a `stale_data` verdict rather than offering a
finished month's leftovers, and April charted as partial.

**AI.** Off by default with no key stored, and asking while disabled was
refused without a request. Pointed at a loopback fake provider, the packaged
engine connected, sent no `Authorization` header, carried no financial figures
in the connection test, surfaced a 500 as an error, recovered on the next
attempt, tried a 401 once and retried a 503 once. No bearer token appeared in
`native-engine.log`.

**Persistence.** A saved setting and the imported transactions both survived a
fresh engine process.

**Packaging.** No database, statement, export, credential or log file is
bundled. The engine is not resident between requests and owns no TCP listener.

**Launching the shell.** Started against a disposable directory: window title
"Northstar Ledger", responding, zero TCP listeners, zero outbound connections
from the shell process, and its data written to the disposable directory. The
real data directory was byte-identical before and after every step.

### Verified by a person, not by this pass

The code paths behind these were exercised through the packaged sidecar, but
the visual result was not confirmed:

- Settings and About visibly showing 2.4.0
- the six tabs rendering as Home, Add Data, Plan, Insights, Transactions, Coach
- Plan's "Review changes" scrolling to and highlighting its destination
- the Home cashflow chart drawing a partial month at reduced opacity with its
  "(partial)" tag

## Remaining limitations

- **The installer is unsigned.** SmartScreen will warn on a machine that has
  not seen it before.
- **There is no updater.** Moving to a later version means running a new
  installer by hand.
- **A model refusal is displayed as an explanation.** If a provider returns a
  non-empty refusal with a normal finish reason, Coach renders it as ordinary
  prose. It carries no invented figure, so no number is wrong, but the text is
  unhelpful. Raised by the outside review and left for a later pass, because
  detecting a refusal reliably needs heuristics that could suppress genuine
  answers.
- **Excel serial dates are refused rather than converted.** A file whose dates
  are raw serial numbers blocks with a clear reason and needs a mapping.
- **Two genuine identical purchases in two different statements collapse to
  one row.** Confirmed and reproduced. Pre-existing, not a 2.4.0 regression,
  and recorded as a strict `xfail`. The safe fix needs a provenance or
  confirmation design decision, because the alternative breaks re-import
  idempotency. This is the first thing to settle in the next pass.
- **Two accounts can combine into a month that looks complete when neither
  is.** `statement_coverage` groups every transaction by calendar month without
  regard to which account it came from, so a chequing account covering the
  first third of a month and a card covering the last third together satisfy
  the start-lag, end-lag and span thresholds. Reproduced: a May holding
  chequing days 1 to 10 and card days 21 to 31 is classified complete and
  feeds the typical-income baseline. Pre-existing, and found while planning
  the next release rather than by this one's changes. There is no cheap fix:
  requiring every account to cover a month would wrongly mark months partial
  whenever an account is simply quiet, which is the defect `7d005ef` removed.
  It needs the per-source provenance work described for the next release.
- **A private SQLite database sits in git history** at commit `f06758b`,
  reachable from the public branch. It was inspected during this pass: three
  invented fixture transactions, no accounts, no API settings. Binary-history
  hygiene rather than a privacy incident, and out of scope here.
- Analytics screens take a few seconds against a synthetic 50,000-row ledger.
  Not a concern at present data sizes.

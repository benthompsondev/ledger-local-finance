# Northstar Ledger 2.5.0 — native desktop validation

Built, installed and validated on 2026-08-02. **Not pushed, not tagged, not
published.** The branch it sits on, `feature/home-weekly-checkin`, is public,
so these commits become public the moment they are pushed.

A trust-stabilization release. Four correctness defects, one release-evidence
gap, one new provider, no new features and no screen redesigns.

## What was wrong, and what was done about it

Seven findings were handed over. Five reproduced, one reproduced as a real gap
with a wrong description, and one did not reproduce at all. Each is recorded
with what was actually observed rather than what was reported.

### 1. Ambiguous thousands punctuation — accepted, fixed

Reproduced exactly. `-1.234` parsed to `-1.234` and previewed as `-$1.23`
instead of `-$1,234.00`, a thousandfold understatement with nothing on screen
to suggest the number had been decided rather than read.

The cause is narrower than "defaults to dot-decimal". `detect_decimal_separator`
only counted a cell as evidence when it ended in one or two digits after a
separator, so a file made entirely of three-digit groups produced *no* evidence
either way and fell through to the North-American default.

Now refused, naming both readings in dollars so the size of the disagreement is
visible. Evidence anywhere in the column still settles the file, and a repeated
separator (`12.345.678`) now counts as grouping evidence, so European integer
amounts that were previously unreadable import correctly.

Regressions at three levels, because a refusal is only worth something if it
survives to the database: the detector, the preview, and the confirm path
committing zero rows. Nine tests fail without the fix.

### 2. Identical purchases collapsing — accepted, fixed

The strict `xfail` is gone and the test passes for real.

The requirement as written contains a genuine conflict, and it is worth stating
because the resolution is a design decision rather than a bug fix. "Two
identical purchases from separate files both survive" and "overlapping exports
do not duplicate" describe the *same rows*. No rule that looks only at a row can
satisfy both.

The file settles it. A re-export reproduces what an earlier import recorded
across the days the two share, because both are the bank's account of the same
days. A genuinely different statement does not, and disagrees about the rest of
the window. Files that reproduce an earlier import keep the in-file numbering,
so shared rows collide and are skipped; files that do not are new activity and
are numbered against what is stored, so every row lands.

The fingerprint itself is unchanged, including its account-awareness. This reads
provenance that `import_batch_id` was already recording.

Seven tests, four of which are guards that pass in both worlds: re-import
idempotency, an overlapping re-export, non-overlapping statements, and identical
activity on two accounts.

### 3. Cross-account coverage mosaic — accepted, fixed

Reproduced. A chequing statement covering 1–9 June and a card statement covering
22–30 June produced a combined first day of the 1st and last day of the 30th, and
June was marked complete with three of its four weeks never imported by anything.
Complete months are what the typical-income baseline reads, so this was not a
display problem.

Now asks whether any *single* account covered the month end to end. Deliberately
"some account", never "every account": a savings account with one transfer all
month is quiet, not missing, and requiring it to prove coverage is how an earlier
version of this rule punished people with more than one account.

Purely additive. With one account the new question is exactly what the existing
checks already asked, so single-account behaviour is unchanged and all 823
pre-existing tests still passed on the first run.

### 4. Category comparisons inventing a zero baseline — accepted, fixed

Confirmed by reading and by the installed app. `category_pace_breakdown` returns
an empty previous map when the previous month cannot be compared against, and
every item then read `prev.get(category, 0.0)` — so "not imported" and "spent
nothing" became the same answer, and every category rendered as up by everything
it had spent.

`comparison_available` is now on the payload; `previous` and `delta` are null
rather than zero; the bars fall back to amount and share; Biggest movers is
withheld; and the note above the bars stops promising a baseline. Zero and null
are now different answers, which is the point — a category with no spending last
month still compares against zero.

The decision moved to `desktop/src/categoryComparison.ts` so it could be tested
at all: `charts.tsx` is JSX and the test runner only strips types.

### 5. Installed Apps reporting 2.2.0 — **not reproduced**, gap fixed anyway

The defect as described does not exist on this machine. Before any 2.5.0 work,
the installed 2.4.0 read consistently:

| | |
|---|---|
| `HKCU\...\Uninstall\Northstar Ledger` `DisplayVersion` | 2.4.0 |
| `ledger-native.exe` ProductVersion | 2.4.0 |
| `uninstall.exe` ProductVersion | 2.4.0 |
| Other Ledger uninstall entries in HKCU, HKLM, WOW6432Node | none |

The most likely explanation is a leftover pre-rename `Uninstall\Ledger` key
showing its own 2.2.0 next to the current entry. `NSIS_HOOK_POSTINSTALL` already
deletes that key, so the mechanism was fixed before it was reported.

The real gap is that **nothing would have caught it either way**.
`verify_windows_installer.py` checks the artifact — name, size, bundle contents —
and never looked at the machine afterwards, so the version a user sees in
Settings › Apps was never part of any release evidence.

`scripts/verify_installed_app.py` now reads the uninstall registry and every
installed executable and requires them to agree with the repository version. More
than one Ledger entry is itself a failure, because that is what the reported
symptom looks like. `NSIS_HOOK_POSTINSTALL` also restates `DisplayVersion` on
every install, so it cannot survive an upgrade that replaced the binary.

### 6. A real Anthropic provider — accepted, added

The only route to Anthropic was "Somewhere else" with its base URL, which sent
an OpenAI chat request to a path Anthropic does not serve.

Five things differ and all five are handled: `/v1/messages`, `x-api-key` instead
of a bearer, a required `anthropic-version` header, the system prompt hoisted to
a top-level field, and a reply of content blocks rather than choices. Only text
blocks become the answer, so a thinking block cannot leak into one, and
`stop_reason` maps onto the same truncated / safety / incomplete classifications
everything else uses.

The transport is now shared rather than copied, deliberately: the unchained error
handling that keeps a provider's own words out of `native-engine.log` is exactly
what a second copy loses quietly.

Both cloud providers now say what credential they need. An Anthropic Console key
is billed per request and a Claude Pro or Max subscription is not one; the same
distinction applies to an OpenAI API key against ChatGPT Plus. Getting that wrong
looks like a broken app rather than a billing setting.

### 7. Release evidence and public instructions — accepted, corrected

Covered under *Corrections to the 2.4.0 record* below.

## Verification

Run at `HEAD`. **Nothing was skipped, weakened or marked expected-failure.** The
suite carries no `xfail` at all now; the one it had is finding 2, which is fixed.

| Check | Result |
|---|---|
| `pytest -q` | **864 passed**, 0 skipped, 0 xfailed |
| CSV suites (5 modules) | 179 passed |
| AI suites (3 modules) | 132 passed |
| `npm run build` | passed |
| `npm run test:charts` | 16 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check` | passed, `ledger-native v2.5.0` |
| `cargo test` | 5 passed |
| `scripts.check_tracked_files` | no private artifact paths tracked |
| `scripts.doctor` | passed |
| `git diff --check` | clean |

### What kind of test each thing is

Stated because the distinction was blurred in the 2.4.0 record:

- **Source tests** — the bulk of the suite, running the engine in-process.
- **Real loopback HTTP tests** — the provider matrices. A genuine `HTTPServer`
  on 127.0.0.1, with the provider code opening real sockets, so URL
  construction, headers, status handling and JSON decoding are exercised for
  real. This covers MiniMax, OpenAI-compatible hosts and now Anthropic.
- **Packaged-app tests** — the 20 checks below, driving the installed
  `ledger-engine.exe` as a subprocess.
- **Live external-provider tests** — **none were run.** Nothing in this release
  contacted api.anthropic.com, api.openai.com or api.minimax.io. See *Not
  verified*.

## Version surfaces

`package.json`, `package-lock.json`, `src-tauri/Cargo.toml`, `src-tauri/Cargo.lock`
(the `ledger-native` entry), `tauri.conf.json` and `utils/__init__.py` all read
2.5.0, and `test_release_versions_are_consistent` enforces agreement. Settings and
About read the version from Tauri, so `tauri.conf.json` covers them.

`packaging/windows/Ledger.iss` and `version_info.txt` remain at 1.6.0: they belong
to the retired Inno Setup path, not to the Tauri installer this release ships.

## The installer

| | |
|---|---|
| Path | `src-tauri/target/release/bundle/nsis/NorthstarLedger_2.5.0_x64-setup.exe` |
| Size | 276,942,656 bytes (264.1 MB) |
| SHA-256 | `1F78A3E8D8863C6BCE4B899F1AA7580E343CCE0C0E2E1A9C2780B2F112516834` |
| Bundled files | 1,567 |

Release verification now refuses the CI sidecar placeholder and an engine
executable too small to be a real PyInstaller build, so a release can no longer
be verified against a stand-in.

## The installed app

Installed silently over the existing per-user 2.4.0. Exit code 0. The
`%LOCALAPPDATA%\Ledger` data directory was left at its existing 19 files, before
and after, and again after launching.

| | |
|---|---|
| Installed Apps `DisplayVersion` | Northstar Ledger **2.5.0** |
| Uninstall registry key | `HKCU\...\Uninstall\Northstar Ledger`, and no other Ledger entry in any hive |
| `ledger-native.exe` ProductVersion | 2.5.0 |
| `uninstall.exe` ProductVersion | 2.5.0 |
| Packaged engine | `...\Programs\Ledger\engine\ledger-engine.exe`, and every check below ran through it |
| Launch | window opened and stayed up, titled "Northstar Ledger" |

Everything below ran against the installed `ledger-engine.exe` with
`LEDGER_DATA_DIR` pointed at a fresh directory under `%TEMP%`, never the
repository source and never the real database. Every figure is invented.

**20 of 20 checks passed.**

| Area | Check | Result |
|---|---|---|
| Ambiguous CSV | preview refuses the file, naming the thousands separator | pass |
| Ambiguous CSV | confirm returns an error | pass |
| Ambiguous CSV | zero rows in the database afterwards | pass |
| Duplicates | two statements each carrying one identical fare keep both | pass |
| Duplicates | an overlapping re-export adds only the new row | pass |
| Signs | money out stays negative through the packaged engine | pass |
| Coverage | two half-covered accounts do not make July complete | pass |
| Comparison | `comparison_available` is false with one month imported | pass |
| Comparison | no item carries an invented zero baseline | pass |
| Comparison | no Biggest movers without a baseline | pass |
| Plan | Plan screen loads | pass |
| Providers | Anthropic offered as a guided provider | pass |
| Providers | Anthropic points at `https://api.anthropic.com/v1` | pass |
| Providers | Anthropic note separates a Console key from a subscription | pass |
| Providers | OpenAI note mentions separate API billing | pass |
| Providers | MiniMax still offered | pass |
| Providers | Ollama and LM Studio still offered | pass |
| Local provider | unreachable local model gives actionable guidance naming Ollama and LM Studio | pass |
| HTTPS | plain-http Anthropic is refused at save time | pass |
| Persistence | a fresh engine process sees the same 14 rows | pass |

## Corrections to the 2.4.0 record

`docs/releases/NATIVE_DESKTOP_2.4.0_VALIDATION.md` has been corrected in place,
with the original wording described rather than silently replaced.

- It called the release private. The branch had been pushed and **is public**.
- It said "nothing skipped or weakened" while the suite carried a strict
  `xfail`. An expected failure is a skipped assertion however it is spelled.
- It reported an Installed Apps version that was never read. Only the
  executable's ProductVersion was checked. The registry has since been read and
  does say 2.4.0, so the claim was true and unevidenced — which is its own
  problem in a document whose entire purpose is evidence.

`AGENTS.md` said the app makes no network calls. It is the first thing an agent
reads and it was wrong: the optional AI feature makes outbound HTTPS calls to a
configured provider. It now says so, and says that widening what it sends is a
privacy change rather than a feature change.

The README, CONTRIBUTING and getting-started guide claimed Python 3.12+ and
Node 20+. Nothing tests that combination, and Node 20 provably cannot run the
frontend tests, which load TypeScript directly. Narrowed to what CI proves —
Node 24, Python 3.14, with 3.13 working locally — and the reasons are stated,
including the month-end test that fails only on Python 3.12.

## Not verified

Stated plainly rather than left to be assumed.

- **No live external provider call was made.** The Anthropic work is verified
  against a real loopback HTTP server running the shipping protocol code, and
  against the shape Anthropic documents. Whether the live service accepts these
  requests needs a Console API key and a real call, and neither exists here.
  The same is true of MiniMax and OpenAI, which is unchanged from 2.4.0.
- **The GUI was not driven.** The app was launched and its window confirmed, but
  the screens were exercised through the packaged engine rather than by
  clicking. The About screen was not read visually; it takes its version from
  `tauri.conf.json`, which is verified, and from the executable, which reads
  2.5.0.
- **One test fails on Python 3.12 only.** A month-end pace test, not reproducible
  on 3.13 or 3.14, and no 3.12 interpreter is installed here to debug it. CI runs
  3.14, the version the installer bundles. Still open, now documented in the
  README rather than left as folklore.
- **The installer is unsigned and there is no updater.** Unchanged from 2.4.0.
  SmartScreen will warn on a machine that has not seen the binary before.
- **A quiet account is still judged by its transactions, not by a declared
  statement period.** The mosaic fix reads observed activity, because CSV
  exports mostly do not carry a statement period. An account that is quiet
  *and* partially imported cannot be told apart from one that is merely quiet.
  It can no longer make a month look complete on its own, which was the harm.

## History cleanup — still outstanding, still not executed

An early commit on this branch staged a scratch database copy that should
never have been staged. It was removed from the tree two commits later and is
not part of any released build, but the repository is public, so the object
remains in history.

**Nothing has been run.** Rewriting history needs Ben's explicit approval and
has not been given.

The details that were previously written out here — the artifact path, the
object identifier, and a description of what it held — have been removed from
this file. Publishing a map to a sensitive object in the same public repository
that contains it defeats the point of removing it. The information needed to
act still exists in the reachability report handed over with the 2.5.5 release
notes, which is not tracked here.

### The shape of a purge, without the specifics

1. Install `git-filter-repo` (`pip install git-filter-repo`). Git recommends
   against `filter-branch` for this.
2. Run it against the single offending path, inverting the match, on a fresh
   clone.
3. Re-add the `origin` remote, which `filter-repo` removes by design.
4. Force-push with `--force-with-lease`, never a plain `--force`.

### What a purge does and does not achieve

- Every commit from the offending one onward is rewritten and gets a new SHA.
- Every existing clone breaks. A normal `git pull` afterwards merges two
  parallel histories and puts the object straight back.
- **GitHub keeps the object reachable through cached views and the pre-push
  reflog after the force-push.** Removing it there needs a GitHub Support
  request; a rewrite alone is not sufficient.
- Nothing retracts what has already been public, and anyone who cloned earlier
  keeps it regardless.

### The cheaper option, still worth considering first

If this branch is squash-merged into a fresh `main` before the repo is
presented as finished, that merge produces a clean history with no rewrite at
all, and the object stops being reachable from the branch anyone is pointed at.
It does not remove the object from the repository, so it is a presentation fix
rather than a purge, and it should be described as one.

Either way it is Ben's call.

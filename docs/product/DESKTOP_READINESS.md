# Desktop Readiness — Requirements and Recommendation

**Date:** 2026-07-17. Planning document only; packaging happens after the weekly
workflow, multi-source ingestion, database safety, and real-data acceptance are proven
(see `PRODUCT_EXPANSION_RECOMMENDATION.md` §14).

## Recommendation

**Keep Python + Streamlit.** Package as a PyInstaller (or briefcase) launcher that
starts the Streamlit server on a free localhost port and opens the default browser —
the model already proven by `Ledger_Launcher.py`. An embedded window (pywebview
wrapping the local URL) is a cosmetic upgrade to evaluate second, not a prerequisite.
Rewriting the UI (Tauri/Electron) is not justified: the entire product investment is
Streamlit-shaped, performance is fine locally, and the risk of a rewrite dwarfs the
benefit of a nicer window chrome.

## Requirements checklist

**Startup/shutdown** — single entry executable: find free port → start Streamlit
headless → open browser → tray/console control to stop. Must handle "already running"
(reuse tab, don't double-start) and kill the server cleanly on exit (no orphaned
python.exe).

**Database & user data — implemented 2026-07-18.** Repository mode remains
repo-relative `data/`; the packaged launcher uses
`%LOCALAPPDATA%\Ledger\` (override via `LEDGER_DATA_DIR`, which packaging sets).
Config (`config.json`), watch-folder state, and exports live under the same root.
Migration: on first packaged run, offer to copy an existing repo-mode database.

**Backups/restore — implemented 2026-07-18.** SQLite online backups run before
schema migration, import-batch undo, and restore. Settings can create, download,
upload, validate, and restore recovery points. Restore creates a pre-restore
backup and rolls it back if post-restore validation fails.

**File pickers** — Streamlit's uploader works in-browser (no OS picker needed for
import); the watch-folder path stays a text field + validation. Acceptable for v1.

**Logs** — `%LOCALAPPDATA%\Ledger\logs\` with rotation (launcher log exists; redirect
it); never log transaction contents.

**First run** — create data dir, init DB, land on Home's empty state (already guides
to Add Data); offer demo mode as a try-it path.

**Upgrades/migrations** — versioned schema (exists: `schema_version` + additive
migrations with tests); installer upgrades must never touch the data dir; run
migrations on first launch after upgrade, after the automatic backup.

**Bundled runtime — local build completed 2026-07-18.** PyInstaller one-dir
build (one-file is slower to start and worse for AV); the private spike uses
Python 3.14 + pinned `requirements.lock.txt`. Known finicky points:
Streamlit's static assets and metadata need explicit PyInstaller hooks
(`--collect-all streamlit`, plus pandas/plotly data files) — this is well-trodden but
must be spiked (see CloakScan packaging precedent).

**Icons/metadata — implemented 2026-07-18.** The deterministic multi-size icon and
Windows version resource identify the product as Ledger and the publisher as Ben
Thompson.

**Installer vs portable — implemented locally 2026-07-18.** The primary artifact is
a per-user Inno Setup EXE under `%LOCALAPPDATA%\Programs\Ledger`. The portable zip
is secondary and must be fully extracted before launch. Both keep private data under
`%LOCALAPPDATA%\Ledger`; normal uninstall leaves that folder alone.

**Code signing** — unsigned binaries will trip SmartScreen; for a portfolio/personal
tool, document the "More info → Run anyway" path honestly in the README. Revisit an
OV cert only if distribution grows.

**Antivirus false positives** — PyInstaller one-dir + no UPX compression minimizes
flags; publish SHA-256 checksums with each release.

**Crash recovery** — SQLite WAL already in use; on startup, detect and surface an
unclean-shutdown note; the pre-migration backups double as recovery points.

**Update strategy** — v1: manual download with in-app "a newer release exists" check
OFF by default (local-first promise: no phone-home without opt-in). Data dir is never
touched by updates; uninstall leaves user data unless the user deletes it explicitly.

## Release proof gates

1. Owner has run real Tangerine statements + one mapped foreign CSV locally for ≥2
   weekly check-ins without correction pain.
2. ~~`LEDGER_DATA_DIR` indirection implemented and tested (repo mode unaffected).~~
3. ~~Backup-before-migration implemented and tested.~~
4. The exact installer boots on a clean Windows VM with no Python installed.
   **Still required:** local installed-artifact testing is not a substitute for this
   final release gate.

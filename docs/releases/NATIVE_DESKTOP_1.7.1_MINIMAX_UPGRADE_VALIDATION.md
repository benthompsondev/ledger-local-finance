# Northstar Ledger 1.7.1 MiniMax upgrade validation

Validated on Windows 11 on 2026-07-25.

## Release state

- Repair commit packaged: `e2ed3d1`
- Branch: `feature/home-weekly-checkin`
- Product, package, Tauri, Cargo, Python, executable, installer, and Installed
  Apps version: `1.7.1`
- No push or publication was performed.

## Root cause

Version 1.7.0 changed the default MiniMax model to `MiniMax-M3`, but defaults
only apply when a setting does not already exist. Windows upgrades correctly
preserve the existing SQLite `app_settings` rows. An installation that had
saved Northstar's former `abab6.5s-chat` default therefore continued sending
that retired model to MiniMax and received HTTP 400.

The 1.7.0 fresh-profile test passed because it never exercised this persisted
upgrade state.

## Repair

- Reading AI settings now replaces the exact retired Northstar MiniMax default
  with `MiniMax-M3` before a connection test or financial question can be sent.
- The repair is limited to the official MiniMax endpoint and the known retired
  default. Custom endpoints and other model choices are not rewritten.
- Settings shows a one-time plain-language notice when this automatic repair
  happens.
- Future MiniMax unknown-model responses show a direct repair action rather
  than raw provider JSON.
- **Enable AI assistance** is now an accessible sliding toggle with clear
  on/off and privacy wording.

## Automated verification

| Check | Result |
| --- | --- |
| Python suite | 447 passed |
| Focused AI boundary and upgrade tests | 30 passed |
| React/TypeScript production build | passed |
| Rust/Tauri command and ACL tests | 5 passed |
| Git diff whitespace check | passed |

New regression coverage proves that:

1. A disposable database containing `abab6.5s-chat` is rewritten before its
   first AI request.
2. The request and returned metadata both use `MiniMax-M3`.
3. The SQLite setting remains repaired.
4. A custom endpoint using the same model text is not rewritten.
5. A future MiniMax unknown-model response is translated into an actionable
   message without displaying raw JSON.

## Live MiniMax verification

The user-supplied MiniMax Token Plan key was used with synthetic data only. It
was never added to source, tests, documentation, build output, or the regular
Northstar database.

The source-level disposable profile began with the exact retired model. Both
the non-financial connection check and a grounded synthetic-finance question
completed successfully using `MiniMax-M3`. The returned answer contained no
private reasoning tags and passed Northstar's local currency/percentage
grounding check.

## Exact installed-artifact verification

Installer:

`src-tauri\target\release\bundle\nsis\NorthstarLedger_1.7.1_x64-setup.exe`

- Size: 271,001,893 bytes (258.45 MiB)
- SHA-256:
  `20175A47D2174D9061C956D77A20B45FBE75F83E37D71255077469D2DEDB906B`
- Silent install exit code: 0
- Installed executable product/file version: 1.7.1
- Installed Apps entry: Northstar Ledger 1.7.1

The exact installer was installed and launched with `LEDGER_DATA_DIR` pointing
at the disposable legacy-model profile. The regular finance database was not
opened or modified.

Hands-on installed checks passed:

1. Settings displayed Northstar Ledger 1.7.1 and the disposable data path.
2. The saved `abab6.5s-chat` value automatically became `MiniMax-M3`.
3. Settings displayed the automatic-repair notice.
4. The new toggle switched AI assistance off and back on, with the displayed
   privacy state changing immediately.
5. **Save and test connection** returned
   `Connected to MiniMax using MiniMax-M3.`
6. The AI Beta workspace sent totals-only synthetic data and returned a
   grounded answer from MiniMax-M3.
7. The answer showed its provider, model, latest imported date, and successful
   local grounding check; no reasoning tags appeared.
8. After closing and relaunching, AI remained enabled and the AI Beta workspace
   was immediately ready without reconfiguration.
9. The API key remained DPAPI-protected in SQLite and did not appear in the
   native engine log. It was removed from the disposable profile after testing.
10. The running app owned no TCP listener. Its only persistent children were
    Microsoft Edge WebView processes; no browser, console, or idle Python
    sidecar remained.
11. The installed directory and installer contained no CSV, SQLite, PDF, or
    JSONL financial data, no private statement markers, and no subscription-key
    marker.

## Remaining limitations

- AI remains explanation-only and questions remain independent.
- A MiniMax M3 response can take roughly one minute for a grounded question;
  the UI remains responsive and shows progress while waiting.
- The deterministic number guard checks explicit currency and percentage
  figures, not every possible unformatted numeric claim.
- Ollama and LM Studio still need hands-on validation with real locally
  installed models.
- Some stale-data Home sections still use “this month” wording for the latest
  imported month. This is separate from the AI connection repair and remains
  the highest-priority deterministic wording fix.

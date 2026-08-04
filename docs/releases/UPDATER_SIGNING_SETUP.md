# Updater signing — the one thing 2.6.0 still needs

Northstar's updater is implemented and tested, but it cannot ship until a
signing key exists. **This is deliberate**: an updater that installs unsigned
downloads is worse than no updater, so Northstar fails closed at every step.

Nobody has generated Northstar's key yet, and CloakScan's key must not be
reused — sharing one key across two projects means one compromise is both.

## What happens right now

`scripts/build_native_desktop.ps1` builds the app and the NSIS installer, then
stops:

```
A public key has been found, but no private key.
Make sure to set `TAURI_SIGNING_PRIVATE_KEY` environment variable.
Error ... Tauri desktop build failed.
```

So a 2.6.0 release cannot be produced until the steps below are done. The
interactive installer is built before the failure, but the updater archive and
its signature are not, and `latest.json` cannot be assembled without them.

## 1. Generate the key, once

Run this on the machine that cuts releases. Choose a real password and store it
in a password manager.

```powershell
npx tauri signer generate -w "$env:USERPROFILE\.northstar\northstar-updater.key"
```

Proposed location, deliberately outside every repository:

```
%USERPROFILE%\.northstar\northstar-updater.key       <- private, never commit
%USERPROFILE%\.northstar\northstar-updater.key.pub   <- public
```

`.northstar\` is not inside any checkout, so no `.gitignore` mistake can stage
it. The private key is never printed, copied into the repo, or passed on a
command line that ends up in shell history.

**If this key is lost, the updater chain breaks.** Existing installs will
refuse every future update because nothing can be signed with the key they
trust, and the only way back is a manual reinstall. Back it up.

## 2. Commit the public key

Put the contents of the `.pub` file into `src-tauri/tauri.conf.json`:

```json
"plugins": {
  "updater": {
    "pubkey": "<contents of northstar-updater.key.pub>",
    "endpoints": ["https://github.com/benthompsondev/ledger-local-finance/releases/latest/download/latest.json"]
  }
}
```

The public key is meant to be public — it is compiled into the app so it can
verify downloads. `tests/test_updater_config_2_6_0.py` checks that whatever
lands there is a real key, is not a placeholder, and is not another project's.

## 3. Build a signed release

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content "$env:USERPROFILE\.northstar\northstar-updater.key" -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<the password>"
powershell -File scripts/build_native_desktop.ps1
```

That produces three files in `src-tauri/target/release/bundle/nsis/`:

| File | What it is |
|---|---|
| `NorthstarLedger_2.6.0_x64-setup.exe` | what a person downloads and runs |
| `NorthstarLedger_2.6.0_x64-setup.nsis.zip` | what the updater downloads |
| `NorthstarLedger_2.6.0_x64-setup.nsis.zip.sig` | the detached signature |

Clear both variables afterwards.

## 4. Build and check the manifest

```powershell
node scripts/update_manifest.mjs `
  --version 2.6.0 `
  --pub-date (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ") `
  --notes "Northstar Ledger 2.6.0" `
  --url "https://github.com/benthompsondev/ledger-local-finance/releases/download/v2.6.0/NorthstarLedger_2.6.0_x64-setup.nsis.zip" `
  --sig "src-tauri\target\release\bundle\nsis\NorthstarLedger_2.6.0_x64-setup.nsis.zip.sig" `
  --out latest.json

node scripts/update_manifest.mjs --validate latest.json --expect-version 2.6.0
```

The validator refuses a missing or empty signature, a placeholder, a non-https
URL, a version that disagrees with the release, and — the mistake that produces
an updater which appears to work and installs nothing — a URL pointing at the
interactive `.exe` instead of the `.nsis.zip`.

## 5. Upload all four files to the release

`latest.json` must be attached to the GitHub release, because the endpoint is
`/releases/latest/download/latest.json`.

## GitHub secrets, when releases move to Actions

Only two are needed:

| Secret | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | contents of the private key file |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | the password chosen in step 1 |

Nothing else changes. `latest.json` and the public key stay public.

## Why 2.6.0 has to be installed by hand

2.5.5 shipped without an updater, so there is nothing in it that can fetch
2.6.0. Everyone installs this one manually, once. Releases after 2.6.0 can be
installed from Settings, assuming they are signed with the same key.

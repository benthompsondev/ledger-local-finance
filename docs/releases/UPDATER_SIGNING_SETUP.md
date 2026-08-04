# Updater signing

Northstar signs its updater artifacts, and every step fails closed. With the
updater configured and no key available, the release build refuses to finish
rather than producing something unsigned:

```
A public key has been found, but no private key.
Make sure to set `TAURI_SIGNING_PRIVATE_KEY` environment variable.
```

An updater that installs unsigned downloads is worse than no updater, so that
refusal is the point.

Northstar's key was generated for 2.6.0. CloakScan's key is deliberately not
reused — sharing one key across two projects means one compromise is both, and
a test refuses any foreign key by fingerprint.

## 1. Generating the key — done for 2.6.0

Kept here because it has to be repeated if the key is ever lost or rotated.

```powershell
npx tauri signer generate --ci --password "" -w "$env:USERPROFILE\.northstar\northstar-updater.key"
```

The key has **no password**. The key file is itself the secret: it lives
outside every checkout and becomes a GitHub secret when releases move to
Actions. That is the ordinary Tauri release pattern and it keeps the build
non-interactive. To use a password instead, regenerate without `--password ""`
and set `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` wherever you build.

Location, deliberately outside every repository:

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

## 2. Commit the public key — already done for 2.6.0

The contents of the `.pub` file go into `src-tauri/tauri.conf.json`:

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
$env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content "$env:USERPROFILE\.northstar\northstar-updater.key" -Raw).Trim()
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
powershell -File scripts/build_native_desktop.ps1
Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY, Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD
```

That produces two files in `src-tauri/target/release/bundle/nsis/`:

| File | What it is |
|---|---|
| `NorthstarLedger_2.6.0_x64-setup.exe` | what a person downloads, and what the updater downloads |
| `NorthstarLedger_2.6.0_x64-setup.exe.sig` | the detached signature |

**Tauri 2's NSIS bundler signs the installer itself.** There is no separate
`.nsis.zip` — that was Tauri 1. So the manifest URL points at the `.exe`, and
the build script renames the signature alongside the installer so the two stay
paired.

Clear both variables afterwards.

## 4. Build and check the manifest

```powershell
node scripts/update_manifest.mjs `
  --version 2.6.0 `
  --pub-date (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ") `
  --notes "Northstar Ledger 2.6.0" `
  --url "https://github.com/benthompsondev/ledger-local-finance/releases/download/v2.6.0/NorthstarLedger_2.6.0_x64-setup.exe" `
  --sig "src-tauri\target\release\bundle\nsis\NorthstarLedger_2.6.0_x64-setup.exe.sig" `
  --out latest.json

node scripts/update_manifest.mjs --validate latest.json --expect-version 2.6.0
```

The validator refuses a missing or empty signature, a placeholder, a non-https
URL, a version that disagrees with the release, and a URL pointing at a file
the build never signed — the checksum, the release notes, the `.sig` itself —
which would otherwise reach the user as a signature error rather than as the
wrong link it actually is.

## 5. Upload the release files

`latest.json` must be attached to the GitHub release, because the endpoint is
`/releases/latest/download/latest.json`.

## GitHub secrets, when releases move to Actions

Only two are needed:

| Secret | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | contents of the private key file |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | empty, unless the key is regenerated with one |

Nothing else changes. `latest.json` and the public key stay public.

## Why 2.6.0 has to be installed by hand

2.5.5 shipped without an updater, so there is nothing in it that can fetch
2.6.0. Everyone installs this one manually, once. Releases after 2.6.0 can be
installed from Settings, assuming they are signed with the same key.

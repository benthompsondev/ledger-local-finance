# Northstar Ledger 2.6.3 validation

Validated locally on 2026-08-12 from fix commit `c96590e`. This is a focused
Insights comparison release. Finance tests used invented data and disposable
database folders. The normal database under `%LOCALAPPDATA%\Ledger` was not
opened or changed.

## What changed

- Legitimately complete months stay eligible when transactions come from
  normal multi-file or multi-account statement coverage.
- Partial and cross-account mosaic coverage remains excluded.
- Prior-month comparisons and rolling averages use and disclose their actual
  eligible months.
- Best-month cash flow keeps its real signed value when every finished month
  is negative.

## Verification

| Check | Result |
| --- | --- |
| `.venv\Scripts\python.exe -m pytest -q` | 1,221 passed |
| Focused Insights, reconciliation and updater tests | 41 passed |
| `npm run build` | passed |
| `npm run test:charts` | 95 passed |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `cargo check --manifest-path src-tauri/Cargo.toml` | passed |
| `cargo test --manifest-path src-tauri/Cargo.toml` | 5 passed |
| `.venv\Scripts\python.exe -m scripts.check_tracked_files` | passed |
| `.venv\Scripts\python.exe -m scripts.doctor` | passed |
| Packaged engine request with disposable data | passed |
| `git diff --check` | passed; only expected line-ending notices |

## Signed release artifacts

- Installer: `NorthstarLedger_2.6.3_x64-setup.exe`
- Size: 277,854,024 bytes
- SHA-256: `8B566B574F22C67E2DEB1E40B0F6173F867599CEDBA42F451D227E3EA810407D`
- Detached updater signature: 428 bytes
- Bundled files checked: 1,567
- `latest.json`: version 2.6.3, HTTPS release URL, validated
- Manifest signature matches the final `.exe.sig` byte for byte

The 2.6.3 installer was deliberately not installed or launched on this
machine. The installed 2.6.2 app is being left in place so the real test is
Settings > App updates discovering, downloading and installing this release.

## Known release limitations

- The updater signature protects the update path, but the installer is not
  Authenticode-signed. Windows SmartScreen may still warn on a new machine.
- Coach remains a beta. No external AI provider call was needed for this
  focused Insights release.

; Keep the established binary path stable across the visible Northstar Ledger
; rename. Private finance data remains separately under $LOCALAPPDATA\Ledger.
!macro NSIS_HOOK_PREINSTALL
  !if "${INSTALLMODE}" == "currentUser"
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Ledger"
    SetOutPath $INSTDIR
  !endif
!macroend

; Tauri keys Installed Apps and shortcuts by the visible product name. Remove
; the legacy visible-name artifacts only after the SpendShape install succeeds.
!macro NSIS_HOOK_POSTINSTALL
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Northstar Ledger"
  Delete "$SMPROGRAMS\Ledger.lnk"
  Delete "$SMPROGRAMS\Northstar Ledger.lnk"
  Delete "$DESKTOP\Ledger.lnk"
  Delete "$DESKTOP\Northstar Ledger.lnk"
  Delete "$INSTDIR\ledger-engine.exe"
  ; Settings > Apps reads DisplayVersion, not the executable. Restate it on
  ; every install so an upgrade can never leave a new binary sitting behind
  ; the version string of the one it replaced. scripts/verify_installed_app.py
  ; checks this after installing, which nothing did before 2.5.0.
  WriteRegStr HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}" \
    "DisplayVersion" "${VERSION}"
!macroend

; Defensive cleanup for machines that still carry pre-rename artifacts when
; SpendShape is uninstalled. The private $LOCALAPPDATA\Ledger data root
; is deliberately untouched.
!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Northstar Ledger"
  Delete "$SMPROGRAMS\Ledger.lnk"
  Delete "$SMPROGRAMS\Northstar Ledger.lnk"
  Delete "$DESKTOP\Ledger.lnk"
  Delete "$DESKTOP\Northstar Ledger.lnk"
  Delete "$INSTDIR\ledger-engine.exe"
!macroend

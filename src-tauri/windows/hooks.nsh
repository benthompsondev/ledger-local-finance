; Keep the established binary path stable across every visible rename. Private
; finance data remains separately under $LOCALAPPDATA\Ledger.
!macro NSIS_HOOK_PREINSTALL
  !if "${INSTALLMODE}" == "currentUser"
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Ledger"
    SetOutPath $INSTDIR
  !endif
!macroend

; Tauri keys Installed Apps and shortcuts by the visible product name, so every
; rename orphans the previous ones. Clean those up, then guarantee the current
; one exists.
;
; The guarantee is the important half. Tauri's CreateOrUpdateStartMenuShortcut
; first tries to retarget an existing "$SMPROGRAMS\${PRODUCTNAME}.lnk", and when
; that is absent it returns early whenever $UpdateMode is 1. After a rename the
; old shortcut is filed under the previous name, so there is nothing to
; retarget, nothing is created, and the deletions below then remove the only
; shortcuts that did exist. That is exactly how upgrading to 3.0.0 left a
; machine with no way to launch the app short of browsing AppData. This hook
; runs after that function, so it is the right place to put the shortcut back.
!macro NSIS_HOOK_POSTINSTALL
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Northstar Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpendShape"
  Delete "$SMPROGRAMS\Ledger.lnk"
  Delete "$SMPROGRAMS\Northstar Ledger.lnk"
  Delete "$SMPROGRAMS\SpendShape.lnk"
  Delete "$DESKTOP\Ledger.lnk"
  Delete "$DESKTOP\Northstar Ledger.lnk"
  Delete "$DESKTOP\SpendShape.lnk"
  Delete "$INSTDIR\ledger-engine.exe"

  ; Tauri's .onInit points $INSTDIR at "$LOCALAPPDATA\${PRODUCTNAME}" before
  ; the preinstall hook above redirects it, which leaves an empty folder named
  ; after the product on every install, and a fresh one after every rename.
  ; RMDir without /r removes a directory only when it is empty, so this can
  ; clear the abandoned shells and can never touch anything holding data.
  RMDir "$LOCALAPPDATA\${PRODUCTNAME}"
  RMDir "$LOCALAPPDATA\SpendShape"
  RMDir "$LOCALAPPDATA\Northstar Ledger"

  ; Unconditionally, because an upgrade is precisely the case Tauri skips.
  ; CreateShortcut overwrites, so doing this on a fresh install where the
  ; shortcut already exists is a no-op rather than a duplicate.
  CreateShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
  ; Without an explicit AppUserModelId a searched or pinned shortcut can be
  ; grouped under a stale identity after a rename.
  !ifmacrodef SetLnkAppUserModelId
    !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  !endif

  ; Settings > Apps reads DisplayVersion, not the executable. Restate it on
  ; every install so an upgrade can never leave a new binary sitting behind
  ; the version string of the one it replaced. scripts/verify_installed_app.py
  ; checks this after installing, which nothing did before 2.5.0.
  WriteRegStr HKCU \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}" \
    "DisplayVersion" "${VERSION}"
!macroend

; Defensive cleanup for machines still carrying pre-rename artifacts. The
; private $LOCALAPPDATA\Ledger data root is deliberately untouched: uninstalling
; the application must never remove financial history.
!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Northstar Ledger"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpendShape"
  Delete "$SMPROGRAMS\Ledger.lnk"
  Delete "$SMPROGRAMS\Northstar Ledger.lnk"
  Delete "$SMPROGRAMS\SpendShape.lnk"
  Delete "$DESKTOP\Ledger.lnk"
  Delete "$DESKTOP\Northstar Ledger.lnk"
  Delete "$DESKTOP\SpendShape.lnk"
  Delete "$INSTDIR\ledger-engine.exe"
!macroend

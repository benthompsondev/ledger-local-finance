#define AppName "Ledger"
#ifndef AppVersion
  #define AppVersion "1.6.0"
#endif
#define AppPublisher "Ben Thompson"
#define AppExeName "Ledger.exe"
#define RepoRoot SourcePath + "..\.."

[Setup]
AppId={{D61BD5D7-FEFF-4F0E-A757-1DD339516631}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/benthompsondev/ledger-local-finance
AppSupportURL=https://github.com/benthompsondev/ledger-local-finance/issues
AppUpdatesURL=https://github.com/benthompsondev/ledger-local-finance/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#RepoRoot}\dist
OutputBaseFilename=Ledger-Setup-{#AppVersion}-x64
SetupIconFile={#RepoRoot}\build\windows\Ledger.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} per-user installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
MinVersion=10.0.17763
LicenseFile={#RepoRoot}\LICENSE
InfoAfterFile={#RepoRoot}\docs\WINDOWS_INSTALL.md

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\Ledger\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--shutdown --no-dialog"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopLedgerServer"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  LedgerExe: String;
begin
  Result := '';
  LedgerExe := ExpandConstant('{app}\{#AppExeName}');
  if FileExists(LedgerExe) then
  begin
    Log('Requesting a controlled Ledger shutdown before upgrade.');
    if not Exec(LedgerExe, '--shutdown --no-dialog', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := 'Ledger could not be stopped. Close Ledger and run Setup again.'
    else if ResultCode <> 0 then
      Result := 'Ledger is still running. Close Ledger and run Setup again.';
  end;
end;

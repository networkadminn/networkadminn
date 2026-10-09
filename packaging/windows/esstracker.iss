; esstracker Windows installer (Inno Setup 6)
; Build after `python packaging\build.py exe-client`:
;   ISCC.exe /DAppVersion=0.3.0 packaging\windows\esstracker.iss
; Output: dist\releases\esstracker-Setup-<version>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7F3C9A52-5B1E-4C8D-9E21-3A6B0C4D8E11}
AppName=esstracker
AppVersion={#AppVersion}
AppVerName=esstracker {#AppVersion}
AppPublisher=Euclidee Solutions
AppPublisherURL=https://tracker.euclideesolutions.com/
AppSupportURL=https://tracker.euclideesolutions.com/
; Per-user install: no admin prompt, matches defaults.toml lookup in agent/config.py.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\esstracker
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=esstracker
OutputDir=..\..\dist\releases
OutputBaseFilename=esstracker-Setup-{#AppVersion}
SetupIconFile=esstracker.ico
UninstallDisplayIcon={app}\esstracker-Agent.exe
UninstallDisplayName=esstracker
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start esstracker automatically when I sign in to Windows"; GroupDescription: "Startup:"

[Files]
Source: "..\..\dist\windows\esstracker-Agent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "defaults.toml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\esstracker"; Filename: "{app}\esstracker-Agent.exe"; WorkingDir: "{app}"; Comment: "esstracker (ESS) - Sign in and track activity"
Name: "{userstartup}\esstracker"; Filename: "{app}\esstracker-Agent.exe"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\esstracker-Agent.exe"; WorkingDir: "{app}"; Description: "Launch esstracker now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM esstracker-Agent.exe /F"; Flags: runhidden; RunOnceId: "KillAgent"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Stop a running tray client so its exe can be replaced on upgrade. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM esstracker-Agent.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

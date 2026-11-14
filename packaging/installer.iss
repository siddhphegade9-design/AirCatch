; Inno Setup script for AirCatch (https://jrsoftware.org/isinfo.php, version 6.3 or newer).
;
; Normally run by packaging\build.ps1, which passes the version:
;   ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
; It packs the folder dist\AirCatch (made by PyInstaller) into dist\installer\AirCatch-Setup-<version>.exe.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "AirCatch"
#define AppExe "AirCatch.exe"
#define FirewallRule "AirCatch"

[Setup]
; Never change AppId: it is how Windows knows a newer installer is an upgrade of the same program.
AppId={{B47D7C69-2F66-408B-A257-8E63642A2B61}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AirCatch
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installing for everyone needs administrator rights (Program Files, firewall rule). The wizard also
; offers "only for me", which needs none; then Windows asks about the firewall on first start instead.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=aircatch.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=AirCatch-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; AirCatch holds this mutex while it runs, so Setup can ask to close it before replacing files.
AppMutex=AirCatchApplicationMutex
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "firewall"; Description: "Allow AirCatch through Windows Firewall on &private networks (needed to receive files)"; GroupDescription: "Network:"; Check: IsAdminInstallMode

[Files]
Source: "..\dist\AirCatch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Private networks only (home, hotspot). On a network Windows calls "Public", AirCatch stays blocked
; until the user changes the network to Private: that is the safe default.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#FirewallRule}"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private"; Flags: runhidden; Tasks: firewall; StatusMsg: "Allowing AirCatch through the firewall (private networks)..."
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"; Check: IsAdminInstallMode

[UninstallDelete]
; files the program itself may create next to the .exe (none are expected; settings live elsewhere)
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// Uninstalling keeps this device's identity and its paired devices by default (so that installing
// again, or upgrading, does not mean pairing everything again). The user may choose to remove them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\AirCatch');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also remove this device''s AirCatch identity, paired devices, settings and logs?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep them (recommended if you plan to install AirCatch again). ' +
                'Received files in your Downloads folder are never removed.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;

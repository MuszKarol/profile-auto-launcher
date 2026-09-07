; Inno Setup script — builds the Windows installer, palaunch-setup-<version>.exe.
;
;   iscc /DVersion=1.0.0 packaging\palaunch.iss
;
; Expects the PyInstaller build to have produced dist\palaunch.exe and
; dist\palaunchw.exe first; the release workflow does both in order.
;
; It installs per-user (no administrator prompt, no UAC shield), which is what
; a launcher that registers a login item wants — a machine-wide install cannot
; write to the user's Startup folder anyway.

#ifndef Version
  #define Version "0.0.0"
#endif

#define AppName "Profile Auto Launcher"
#define Publisher "Karol Musz"
#define Url "https://github.com/MuszKarol/profile-auto-launcher"

[Setup]
AppId={{4E1C2E4A-9B1E-4C7D-9E2F-9B3D5A7C61B2}
AppName={#AppName}
AppVersion={#Version}
AppPublisher={#Publisher}
AppPublisherURL={#Url}
AppSupportURL={#Url}/issues
DefaultDirName={autopf}\ProfileAutoLauncher
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=palaunch-setup-{#Version}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\palaunch.exe
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LicenseFile=..\LICENSE
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start the launcher when I sign in"; GroupDescription: "Startup"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts"; Flags: unchecked
Name: "addtopath"; Description: "Add palaunch to my PATH"; GroupDescription: "Command line"

[Files]
Source: "..\dist\palaunch.exe";  DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\palaunchw.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\profiles\*.yaml";    DestDir: "{app}\profiles"; Flags: ignoreversion
Source: "..\plugins\*.py";       DestDir: "{app}\plugins";  Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\CHANGELOG.md";       DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*.md";          DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\palaunchw.exe"; Parameters: "pick";     IconFilename: "{app}\icon.ico"
Name: "{group}\{#AppName} Manager";   Filename: "{app}\palaunchw.exe"; Parameters: "settings"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\palaunchw.exe"; Parameters: "pick";     IconFilename: "{app}\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";     Filename: "{app}\palaunchw.exe"; Parameters: "tray";     IconFilename: "{app}\icon.ico"; Tasks: autostart

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Check: NeedsPath(ExpandConstant('{app}')); Tasks: addtopath

[Run]
; Copy the sample profiles into the user's config directory and write the
; JSON Schema — the same first-run setup the graphical installer does.
Filename: "{app}\palaunch.exe"; Parameters: "install --cli --no-autostart"; \
    StatusMsg: "Setting up profiles…"; Flags: runhidden waituntilterminated
Filename: "{app}\palaunchw.exe"; Parameters: "tray"; \
    Description: "Start {#AppName} now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\palaunch.exe"; Parameters: "install --cli --uninstall"; \
    Flags: runhidden waituntilterminated; RunOnceId: "PalaunchUninstall"

[Code]
{ Only append to PATH when it is not already there — repeated installs
  otherwise grow the variable by one copy each time. }
function NeedsPath(Param: string): boolean;
var
  CurrentPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', CurrentPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(CurrentPath) + ';') = 0;
end;

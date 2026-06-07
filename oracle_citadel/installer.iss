; ===========================================================================
;  Oracle AI: Project Citadel - Inno Setup installer script
;  Builds OracleAI-Setup.exe. When a user runs it, Oracle is installed and a
;  Desktop icon + Start Menu entry are created so they can launch it directly.
;
;  Prereq: build dist\OracleAI.exe first (build_windows.bat), then compile this
;  script with Inno Setup (https://jrsoftware.org/isinfo.php):
;      iscc installer.iss
; ===========================================================================

#define AppName "Oracle AI Project Citadel"
#define AppVersion "24.7"
#define AppExe "OracleAI.exe"
#define Publisher "Oracle AI"

[Setup]
AppId={{B7E4F1A2-9C3D-4E61-8A0B-ORACLECITADEL}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\OracleAI
DefaultGroupName=Oracle AI
DisableProgramGroupPage=yes
OutputBaseFilename=OracleAI-Setup
SetupIconFile=assets\oracle.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The webhook listener binds port 80, which requires elevation on Windows.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\oracle.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
; Start Menu shortcut
Name: "{group}\Oracle AI"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\oracle.ico"
; Desktop shortcut (created when the user ticks the task above)
Name: "{autodesktop}\Oracle AI"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\oracle.ico"; Tasks: desktopicon
Name: "{group}\Uninstall Oracle AI"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch Oracle AI now"; Flags: nowait postinstall skipifsilent

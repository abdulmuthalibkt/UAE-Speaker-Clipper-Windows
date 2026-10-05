#define MyAppName "UAE Speaker Clipper"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "UAE Speaker Clipper"
#define MyAppExeName "UAE_Speaker_Clipper.exe"

[Setup]
AppId={{9D0F2D0D-9D8B-4A1D-9C83-2D6D7E2A1B01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\UAE Speaker Clipper
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=UAE_Speaker_Clipper_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Files]
Source: "dist\UAE_Speaker_Clipper.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

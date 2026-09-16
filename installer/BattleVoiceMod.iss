; Inno Setup script for Battle VoiceMod.
;
; Build it with Inno Setup 6 (free, https://jrsoftware.org/isdl.php):
;
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\BattleVoiceMod.iss
;
; The result lands in dist\installer\. Run build.bat first: this script packages
; whatever is in dist\BattleVoiceMod, it does not build the app itself.
;
; One elevation, once: the driver install runs inside the same UAC prompt as the
; rest of the setup, instead of being a second prompt later, at runtime.

#define AppName "Battle VoiceMod"
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#define AppPublisher "Battle VoiceMod"
#define AppExeName "BattleVoiceMod.exe"
#define PayloadDir "..\dist\BattleVoiceMod"
#define DriverExe "_internal\drivers\vbcable\VBCABLE_Setup_x64.exe"

[Setup]
AppId={{8F3A5C1E-2B47-4D6A-9E10-7C4B2A9F1D33}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\BattleVoiceMod
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=BattleVoiceMod-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
; The app installs a kernel-mode audio driver, so setup needs administrator rights.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na Area de Trabalho"; GroupDescription: "Atalhos:"
Name: "vbcable"; Description: "Instalar o cabo de audio virtual VB-CABLE (necessario para enviar o som ao TikTok LIVE Studio)"; GroupDescription: "Audio:"; Flags: checkedonce

[Files]
; The whole PyInstaller onedir output. Nothing is installed next to it: settings
; and the user's own sounds live in %APPDATA%\BattleVoiceMod.
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; VB-CABLE's own installer, run silently from the elevated setup.
Filename: "{app}\{#DriverExe}"; Parameters: "-i -h"; StatusMsg: "Instalando o cabo de audio virtual..."; Flags: runhidden waituntilterminated; Tasks: vbcable
Filename: "{app}\{#AppExeName}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent

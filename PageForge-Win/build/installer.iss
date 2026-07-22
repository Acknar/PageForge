; Inno Setup script for PageForge (Windows edition) — embedded-Python build.
;
; This packages a bundled, relocatable standalone Python (in python\) plus the
; app (in app\), so the installed app runs on a REAL Python: tool dependencies
; install on demand at runtime, exactly like running from source.
;
; The CI workflow (.github\workflows\build-windows.yml) assembles dist\PageForge\
; { python\, app\ } and then compiles this. To build locally, replicate that
; layout first. Then:  iscc build\installer.iss  → Output\PageForge-Setup-<ver>.exe

#define AppName "PageForge"
#define AppVersion "1.7.2"
#define AppPublisher "PageForge"
; The app is launched by the bundled Python (no console window).
#define PyW "{app}\python\pythonw.exe"
#define Script "{app}\app\pageforge.py"

#define BuildDir SourcePath + "..\dist\PageForge"
#if !DirExists(BuildDir)
  #error dist\PageForge is missing. Assemble python\ + app\ first (see build-windows.yml).
#endif

[Setup]
AppId={{9F1B2E7A-6C4D-4E3F-9A21-PAGEFORGEWIN01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={#SourcePath}..\Output
OutputBaseFilename=PageForge-Setup-{#AppVersion}
SetupIconFile={#SourcePath}..\icons\pageforge.ico
UninstallDisplayIcon={app}\app\icons\pageforge.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Bundle the whole assembled folder (python\ + app\).
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{#PyW}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\icons\pageforge.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{#PyW}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\icons\pageforge.ico"; Tasks: desktopicon

[Run]
Filename: "{#PyW}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

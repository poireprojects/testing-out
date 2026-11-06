; Inno Setup script — builds a normal Windows installer (per-user, no admin rights needed).
#define MyAppName "don’t know what im doing"
#define MyAppExe "dontknowwhatimdoing.exe"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{8C6B0E2A-5D1F-4C3B-9B7E-2D1F6A9E4C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=personal use
DefaultDirName={localappdata}\Programs\dontknowwhatimdoing
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=dontknowwhatimdoing-setup-{#MyAppVersion}
SetupIconFile=..\..\dkapp\assets\icons\dk.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start automatically when I log in (minimized to the tray)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\..\dist\dontknowwhatimdoing\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; IconFilename: "{app}\{#MyAppExe}"
Name: "{autoprograms}\{#MyAppName} (fresh test)"; Filename: "{app}\{#MyAppExe}"; Parameters: "--profile test --fresh"; Comment: "A separate test copy that starts from scratch every time"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Parameters: "--minimized"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Open {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Offer a truly clean uninstall: settings, trade history, logs and test copies.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if SuppressibleMsgBox('Also delete your settings, trade history, logs and test copies?' + #13#10 + #13#10 +
       'Choose Yes for a completely fresh reinstall. (Saved API keys can be removed first in the app: settings, brokerage, remove.)',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      DelTree(ExpandConstant('{localappdata}\dontknowwhatimdoing'), True, True, True);
end;

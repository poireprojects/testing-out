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

[UninstallDelete]
; anything left in the program folder (never touches user data, which lives elsewhere)
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Open {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// The app itself does the risky parts (checking for open real-money positions, shutting a
// tray-hidden copy down gracefully, removing launch-at-login entries, deleting data and saved
// keys) via dontknowwhatimdoing.exe --uninstall-prepare, BEFORE any program file is removed.
const
  RC_OK = 0;
  RC_LIVE = 3;
  RC_RUNNING = 4;
  RUN_KEY = 'Software\Microsoft\Windows\CurrentVersion\Run';

var
  DeleteUserData: Boolean;

function HasParam(const P: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), P) = 0 then Result := True;
end;

function RunApp(const ExePath, Params: String): Integer;
var
  rc: Integer;
begin
  if not FileExists(ExePath) then begin
    Result := RC_OK;
    exit;
  end;
  if Exec(ExePath, Params, '', SW_HIDE, ewWaitUntilTerminated, rc) then
    Result := rc
  else
    Result := RC_OK;
end;

procedure ForceClose();
var
  rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(1500);
end;

const
  LIVE_MSG = 'don''t know what im doing still has open REAL-MONEY positions.' + #13#10 + #13#10 +
             'Open the app and close them first (Trades -> sell now, or let it close them before 4:00 pm ET), ' +
             'then try again. Nothing was changed.';

// ---------------------------------------------------------------- install / upgrade
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  Result := '';
  rc := RunApp(ExpandConstant('{app}\{#MyAppExe}'), '--shutdown-running');
  if rc = RC_LIVE then
    Result := LIVE_MSG
  else if rc = RC_RUNNING then
    ForceClose();
end;

// ---------------------------------------------------------------- uninstall
// 1) before Windows asks "are you sure?": read-only check only, so cancelling changes nothing
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if RunApp(ExpandConstant('{app}\{#MyAppExe}'), '--check-live') = RC_LIVE then begin
    SuppressibleMsgBox(LIVE_MSG, mbError, MB_OK, IDOK);
    Result := False;   // abort: never strand live positions
  end;
end;

// 2) after confirmation, before any file is removed: choose, stop the app, clean up
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  rc: Integer;
  params: String;
begin
  if CurUninstallStep = usUninstall then begin
    if UninstallSilent() then
      DeleteUserData := HasParam('/DELETEDATA')
    else
      DeleteUserData := MsgBox('Keep your settings, trade history, logs and saved API keys?' + #13#10 + #13#10 +
        'Yes  -  keep them (reinstalling later picks up where you left off).' + #13#10 +
        'No   -  delete all of it for a completely fresh start.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO;
    params := '--uninstall-prepare';
    if DeleteUserData then params := params + ' --delete-data';
    rc := RunApp(ExpandConstant('{app}\{#MyAppExe}'), params);
    Log(Format('uninstall-prepare exit code %d', [rc]));
    if rc = RC_RUNNING then
      ForceClose();   // live positions were already ruled out, so this can't strand real money
    RegDeleteValue(HKEY_CURRENT_USER, RUN_KEY, 'dontknowwhatimdoing');
  end;
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(ExpandConstant('{localappdata}\dontknowwhatimdoing'), True, True, True);
end;

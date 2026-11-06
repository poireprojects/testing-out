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
; Windows Restart Manager would close a running copy abruptly, skipping the open-position check,
; so the app is shut down by our own code (PrepareToInstall / CurUninstallStepChanged) instead.
CloseApplications=no
RestartApplications=no

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
// Safety rules (every one of them blocks the uninstall or upgrade when it can't be satisfied):
//  * The app itself checks for open REAL-MONEY positions and shuts a tray-hidden copy down gracefully
//    (dontknowwhatimdoing.exe --check-live / --shutdown-running / --uninstall-prepare), BEFORE any file
//    is removed or replaced.
//  * A check that could not run, crashed, or returned anything unexpected is a FAILED check — never
//    success. If the app's program can't be started, the installer looks for itself: is a copy running,
//    and is there any real-money database? Uncertain = blocked.
//  * A copy that doesn't close in time is only force-closed after a fresh read-only check confirms no
//    real-money position is open AND the person agrees. In silent mode it is never force-closed.
const
  RC_OK = 0;
  RC_LIVE = 3;
  RC_RUNNING = 4;
  RC_NOT_RUN = -1;
  RUN_KEY = 'Software\Microsoft\Windows\CurrentVersion\Run';
  DATA_DIR = '{localappdata}\dontknowwhatimdoing';

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

// Runs the app's maintenance command. Returns True only if the program actually ran (rc = its exit code).
function RunApp(const ExePath, Params: String; var rc: Integer): Boolean;
begin
  Result := False;
  rc := RC_NOT_RUN;
  if not FileExists(ExePath) then begin
    Log('safety check: ' + ExePath + ' is missing');
    exit;
  end;
  if Exec(ExePath, Params, '', SW_HIDE, ewWaitUntilTerminated, rc) then begin
    Result := True;
    Log(Format('safety check %s -> exit code %d', [Params, rc]));
  end else begin
    Log(Format('safety check %s could not start (error %d)', [Params, rc]));
    rc := RC_NOT_RUN;
  end;
end;

// Is any copy of the app running? Known=False if that couldn't be determined.
function AppProcessRunning(var Known: Boolean): Boolean;
var
  tmp: String;
  rc: Integer;
  output: AnsiString;
begin
  Result := False;
  Known := False;
  tmp := AddBackslash(GetTempDir()) + 'dk-tasklist.txt';
  DeleteFile(tmp);
  if Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq {#MyAppExe}" /FO CSV /NH > "' + tmp + '"',
          '', SW_HIDE, ewWaitUntilTerminated, rc) and (rc = 0) and LoadStringFromFile(tmp, output) then begin
    Known := True;
    Result := Pos(Lowercase('"{#MyAppExe}"'), Lowercase(String(output))) > 0;
  end;
  DeleteFile(tmp);
  Log(Format('process check: known=%d running=%d', [Ord(Known), Ord(Result)]));
end;

// Any real-money database (main copy or a test copy)? Used when the app itself can't answer.
function AnyLiveDatabase(): Boolean;
var
  base: String;
  rec: TFindRec;
begin
  base := ExpandConstant(DATA_DIR);
  Result := FileExists(base + '\trading_live.db');
  if Result then exit;
  if FindFirst(base + '\profiles\*', rec) then begin
    try
      repeat
        if ((rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and (rec.Name <> '.') and (rec.Name <> '..') then
          if FileExists(base + '\profiles\' + rec.Name + '\trading_live.db') then Result := True;
      until Result or (not FindNext(rec));
    finally
      FindClose(rec);
    end;
  end;
end;

// The app's program can't run its own check: decide from what the installer can see for itself.
// Returns '' when it is certainly safe, otherwise the reason to refuse.
function FallbackCheck(): String;
var
  known, running: Boolean;
begin
  Result := '';
  running := AppProcessRunning(known);
  if not known then
    Result := 'couldn''t check whether the app is still running.'
  else if running then
    Result := 'the app is running but its safety check couldn''t run, so its positions can''t be verified.'
  else if AnyLiveDatabase() then
    Result := 'there is real-money trading history, and the app''s safety check couldn''t run to confirm that no '
            + 'real-money position is open.';
end;

function NothingRunning(): Boolean;
var
  known: Boolean;
begin
  Result := (not AppProcessRunning(known)) and known;
end;

const
  LIVE_MSG = 'don''t know what im doing still has open REAL-MONEY positions.' + #13#10 + #13#10 +
             'Open the app and close them first (Trades -> sell now, or let it close them before 4:00 pm ET), ' +
             'then try again. Nothing was changed.';
  FIX_MSG = #13#10 + #13#10 + 'Nothing was changed. Open the app and quit it from the tray icon (or restart the ' +
            'computer), then try again. If the app won''t start, reinstall it first — that keeps your data. ' +
            'Details: %TEMP%\dontknowwhatimdoing-uninstall.log and the setup log.';

// The app didn't close in time. Only force it closed if a fresh read-only check confirms that no
// real-money position is open, and the person agrees (never in silent mode).
function MayForceClose(const ExePath, What: String; Silent: Boolean): Boolean;
var
  rc: Integer;
begin
  Result := False;
  if not RunApp(ExePath, '--check-live', rc) then begin
    Log('force-close refused: the live-position check could not run');
    exit;
  end;
  if rc <> RC_OK then begin
    Log('force-close refused: the live-position check did not confirm "none open"');
    exit;
  end;
  if Silent then begin
    Log('force-close refused: silent mode never force-closes a trading process');
    exit;
  end;
  Result := MsgBox('don''t know what im doing didn''t close on its own.' + #13#10 + #13#10 +
                   'No real-money positions are open (just checked). Force it closed so ' + What + ' can continue?' +
                   #13#10 + #13#10 + 'Choose No to cancel and close it yourself from the tray icon.',
                   mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

function ForceClose(): Boolean;
var
  rc: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Sleep(1500);
  Result := NothingRunning();
end;

// ---------------------------------------------------------------- install / upgrade
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
  exe, why: String;
begin
  Result := '';
  exe := ExpandConstant('{app}\{#MyAppExe}');
  if not RunApp(exe, '--shutdown-running', rc) then begin
    // fresh install, or the old copy's program is gone/broken: nothing must be running
    why := '';
    if not NothingRunning() then
      why := 'a copy of the app is running (or that couldn''t be checked) and its safety check couldn''t run.';
    if why <> '' then Result := 'Setup can''t continue safely: ' + why + FIX_MSG;
    exit;
  end;
  if rc = RC_OK then exit;
  if rc = RC_LIVE then
    Result := LIVE_MSG
  else if rc = RC_RUNNING then begin
    // nested ifs on purpose: ForceClose must never run unless MayForceClose said yes
    if MayForceClose(exe, 'the update', WizardSilent()) then begin
      if not ForceClose() then
        Result := 'Setup can''t continue: the app couldn''t be closed.' + FIX_MSG;
    end else
      Result := 'Setup can''t continue: the running app didn''t close.' + FIX_MSG;
  end else
    Result := Format('Setup can''t continue safely: the app''s safety check failed (code %d).', [rc]) + FIX_MSG;
end;

// ---------------------------------------------------------------- uninstall
// 1) before Windows asks "are you sure?": read-only check only, so cancelling changes nothing
function InitializeUninstall(): Boolean;
var
  rc: Integer;
  why: String;
begin
  Result := True;
  if RunApp(ExpandConstant('{app}\{#MyAppExe}'), '--check-live', rc) then begin
    if rc = RC_LIVE then begin
      SuppressibleMsgBox(LIVE_MSG, mbError, MB_OK, IDOK);
      Result := False;   // never strand live positions
    end else if rc <> RC_OK then begin
      SuppressibleMsgBox(Format('Uninstall can''t continue safely: the live-position check failed (code %d).', [rc])
                         + FIX_MSG, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end else begin
    why := FallbackCheck();
    if why <> '' then begin
      SuppressibleMsgBox('Uninstall can''t continue safely: ' + why + FIX_MSG, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

procedure RefuseUninstall(const Why: String);
begin
  Log('uninstall refused: ' + Why);
  SuppressibleMsgBox('Uninstall stopped before anything was removed: ' + Why + FIX_MSG, mbError, MB_OK, IDOK);
  Abort;   // allowed in CurUninstallStepChanged(usUninstall): ends the uninstall before files are removed
end;

// 2) after confirmation, before any file is removed: choose, stop the app, clean up
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  rc: Integer;
  exe, params, why: String;
begin
  if CurUninstallStep = usUninstall then begin
    exe := ExpandConstant('{app}\{#MyAppExe}');
    if UninstallSilent() then
      DeleteUserData := HasParam('/DELETEDATA')
    else
      DeleteUserData := MsgBox('Keep your settings, trade history, logs and saved API keys?' + #13#10 + #13#10 +
        'Yes  -  keep them (reinstalling later picks up where you left off).' + #13#10 +
        'No   -  delete all of it for a completely fresh start.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO;
    params := '--uninstall-prepare';
    if DeleteUserData then params := params + ' --delete-data';
    if RunApp(exe, params, rc) then begin
      if rc = RC_LIVE then
        RefuseUninstall('the app has open REAL-MONEY positions. Close them in the app first.')
      else if rc = RC_RUNNING then begin
        // nested ifs on purpose: ForceClose must never run unless MayForceClose said yes
        if MayForceClose(exe, 'the uninstall', UninstallSilent()) then begin
          if not ForceClose() then
            RefuseUninstall('the app couldn''t be closed.');
        end else
          RefuseUninstall('the running app didn''t close.');
      end else if rc <> RC_OK then
        RefuseUninstall(Format('the app''s safety check failed (code %d).', [rc]));
    end else begin
      why := FallbackCheck();
      if why <> '' then RefuseUninstall(why);
      if DeleteUserData then
        SuppressibleMsgBox('The app''s own program couldn''t run, so saved API keys could not be removed automatically. ' +
          'To remove them: Windows Credential Manager -> Windows Credentials -> entries containing ' +
          '"dontknowwhatimdoing".', mbInformation, MB_OK, IDOK);
    end;
    if not NothingRunning() then
      RefuseUninstall('a copy of the app is still running (or that couldn''t be checked).');
    RegDeleteValue(HKEY_CURRENT_USER, RUN_KEY, 'dontknowwhatimdoing');
  end;
  if CurUninstallStep = usPostUninstall then begin
    // safety net: if anything in the program folder was still locked a moment ago, try once more
    if DirExists(ExpandConstant('{app}\_internal')) then begin
      Log('program files still present after uninstall; retrying');
      Sleep(2000);
      DelTree(ExpandConstant('{app}\_internal'), True, True, True);
      DeleteFile(ExpandConstant('{app}\{#MyAppExe}'));
    end;
    if DeleteUserData then
      DelTree(ExpandConstant(DATA_DIR), True, True, True);
  end;
end;

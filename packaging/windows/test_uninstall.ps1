# End-to-end install/uninstall test on a real Windows machine (used by CI).
#   powershell -File packaging\windows\test_uninstall.ps1 -Installer dist\installer\setup.exe [-Strict] [-SkipLiveCheck]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [switch]$Strict,
    [switch]$SkipLiveCheck
)
$ErrorActionPreference = "Continue"
$Installer = (Resolve-Path $Installer).Path
$App = Join-Path $env:LOCALAPPDATA "Programs\StockPilot"
$Exe = Join-Path $App "StockPilot.exe"
$Data = Join-Path $env:LOCALAPPDATA "StockPilot"
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C6B0E2A-5D1F-4C3B-9B7E-2D1F6A9E4C11}_is1"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Results = New-Object System.Collections.ArrayList

function Check([string]$Name, [bool]$Ok) {
    [void]$Results.Add([pscustomobject]@{ Result = $(if ($Ok) { "PASS" } else { "FAIL" }); Check = $Name })
    Write-Host ("{0}  {1}" -f $(if ($Ok) { "PASS" } else { "FAIL" }), $Name)
}
function AppRunning { [bool](Get-Process -Name "StockPilot" -ErrorAction SilentlyContinue) }
function Shortcuts { @(Get-ChildItem $StartMenu, $Desktop -Recurse -Filter "StockPilot*.lnk" -ErrorAction SilentlyContinue) }
function ShowLeftovers([string]$Label) {
    # evidence for a failed check: what is still there and what the uninstaller logged
    if (Test-Path $App) { Write-Host "   left in $App :"; Get-ChildItem $App -Recurse -ErrorAction SilentlyContinue | Select-Object -First 15 | ForEach-Object { Write-Host "     $($_.FullName)" } }
    Get-Process -Name "StockPilot" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "   still running: pid $($_.Id)" }
    $log = Join-Path $env:RUNNER_TEMP "uninstall-$Label.log"
    if (Test-Path $log) { Write-Host "   uninstall log (problems):"; Select-String -Path $log -Pattern "fail|error|delete|in use|exit code|still" | Select-Object -Last 25 | ForEach-Object { Write-Host "     $($_.Line)" } }
}
function QuitApp {
    # the normal way a person (or Windows sign-out) closes it: ask the running copy to quit
    if (Test-Path $Exe) { Start-Process $Exe "--shutdown-running" -Wait | Out-Null }
    for ($i = 0; $i -lt 20 -and (AppRunning); $i++) { Start-Sleep 1 }
}
function AppPid { (Get-Process -Name "StockPilot" -ErrorAction SilentlyContinue | Select-Object -First 1).Id }
function StillInstalled { (Test-Path $Exe) -and (Test-Path $UninstKey) }
function PyKey { python -c "import keyring; print(keyring.get_password('StockPilot','alpaca-paper-key-id') or 'NONE')" }

function Install([string]$Label) {
    Write-Host "`n== install ($Label)"
    $p = Start-Process $Installer "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS=desktopicon,startup /LOG=`"$env:RUNNER_TEMP\install-$Label.log`"" -Wait -PassThru
    Check "[$Label] installer exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
    Check "[$Label] app installed" (Test-Path $Exe)
    Check "[$Label] registered in Installed Apps" (Test-Path $UninstKey)
    Check "[$Label] Start menu, desktop and log-in shortcuts created" ((Shortcuts).Count -ge 4)
}

function Launch([string]$Label) {
    Start-Process $Exe "--minimized" | Out-Null
    $ok = $false
    for ($i = 0; $i -lt 40 -and -not $ok; $i++) { Start-Sleep 1; $ok = (AppRunning) -and (Test-Path (Join-Path $Data "app.lock")) }
    Check "[$Label] app launched and running hidden in the tray" $ok
    Start-Sleep 8
}

function Uninstall([string]$Label, [string]$Extra) {
    Write-Host "`n== uninstall ($Label)"
    if (-not (Test-Path $UninstKey)) { Check "[$Label] registered uninstall command exists" $false; return }
    $cmd = (Get-ItemProperty $UninstKey).UninstallString.Trim('"')   # the registered uninstall command
    Start-Process $cmd "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$env:RUNNER_TEMP\uninstall-$Label.log`" $Extra" | Out-Null
    # Inno's uninstaller re-launches itself from %TEMP%; wait for the real work to finish.
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep 1
        $busy = Get-Process | Where-Object { $_.Name -like "_iu*" -or $_.Name -like "unins*" }
        if (-not $busy -and $i -gt 3) { break }
    }
    Start-Sleep 2
}

# ------------------------------------------------------------------ scenario
Remove-Item $Data -Recurse -Force -ErrorAction SilentlyContinue

Install "first"
Launch "first"
# what the app writes when "launch when i log in" is on (the Run key doesn't exist on a fresh runner)
if (-not (Test-Path $RunKey)) { New-Item -Path $RunKey -Force | Out-Null }
New-ItemProperty -Path $RunKey -Name "StockPilot" -Value "`"$Exe`" --minimized" -PropertyType String -Force | Out-Null
python -c "import keyring; keyring.set_password('StockPilot','alpaca-paper-key-id','PKCITEST')"
Check "[first] test credential saved" ((PyKey) -eq "PKCITEST")
Check "[first] launch-at-login entry present" ([bool](Get-ItemProperty $RunKey -Name "StockPilot" -ErrorAction SilentlyContinue))

Write-Host "`n== upgrade while the app is running in the tray"
Install "upgrade"
Check "[upgrade] running copy was closed gracefully first" (-not (AppRunning))
Launch "upgrade"

Uninstall "keep-data" ""
Check "[keep-data] running app was shut down" (-not (AppRunning))
Check "[keep-data] program files removed" (-not (Test-Path $App))
Check "[keep-data] removed from Installed Apps" (-not (Test-Path $UninstKey))
Check "[keep-data] launch-at-login entry removed" (-not (Get-ItemProperty $RunKey -Name "StockPilot" -ErrorAction SilentlyContinue))
Check "[keep-data] shortcuts removed" ((Shortcuts).Count -eq 0)
Check "[keep-data] settings/history kept (user chose keep)" ((Test-Path (Join-Path $Data "settings.json")) -or (Test-Path (Join-Path $Data "trading_demo.db")))
Check "[keep-data] saved keys kept" ((PyKey) -eq "PKCITEST")
ShowLeftovers "keep-data"

Install "reinstall"
Check "[reinstall] previous data still there" (Test-Path (Join-Path $Data "trading_demo.db"))
Launch "reinstall"

if (-not $SkipLiveCheck) {
    Write-Host "`n== open live position must block uninstall"
    python -c "from stockpilot.storage.db import Database; from stockpilot import paths; db=Database(paths.database_file('live'),'live'); db.insert('trades', symbol='SPY', strategy='orb', mode='live', status='open', managed=1, session_date='2026-01-02', created_at='2026-01-02T15:00:00+00:00', entry_qty=0.01, entry_price=500)"
    Uninstall "blocked" "/DELETEDATA"
    Check "[blocked] uninstall refused while a live position is open" ((Test-Path $Exe) -and (Test-Path $UninstKey))
    Check "[blocked] nothing deleted" ((Test-Path (Join-Path $Data "trading_demo.db")) -and ((PyKey) -eq "PKCITEST"))
    Check "[blocked] app left running" (AppRunning)
    Remove-Item (Join-Path $Data "trading_live.db*") -Force
}

# ---- every failed safety check must block (never be read as success)
Write-Host "`n== a safety check that fails must block the uninstall"
$pidBefore = AppPid
$env:STOCKPILOT_TEST_MAINTENANCE_FAIL = "1"    # the app's maintenance commands behave as if they crashed
Uninstall "check-fails" "/DELETEDATA"
Remove-Item Env:\STOCKPILOT_TEST_MAINTENANCE_FAIL
Check "[check-fails] uninstall refused" (StillInstalled)
Check "[check-fails] nothing deleted" ((Test-Path (Join-Path $Data "trading_demo.db")) -and ((PyKey) -eq "PKCITEST"))
Check "[check-fails] app left running (same process)" ((AppPid) -eq $pidBefore)

Write-Host "`n== a safety check that fails must block an upgrade"
$env:STOCKPILOT_TEST_MAINTENANCE_FAIL = "1"
$p = Start-Process $Installer "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=`"$env:RUNNER_TEMP\install-upgrade-blocked.log`"" -Wait -PassThru
Remove-Item Env:\STOCKPILOT_TEST_MAINTENANCE_FAIL
Check "[upgrade-blocked] setup refused (exit code $($p.ExitCode))" ($p.ExitCode -ne 0)
Check "[upgrade-blocked] app left running (same process)" ((AppPid) -eq $pidBefore)
Check "[upgrade-blocked] installed copy untouched" (StillInstalled)

Write-Host "`n== an app that won't close is never force-closed in silent mode"
QuitApp
$env:STOCKPILOT_TEST_IGNORE_QUIT = "1"          # the app answers the quit request but keeps running (hung)
Launch "hung"
Remove-Item Env:\STOCKPILOT_TEST_IGNORE_QUIT
$hungPid = AppPid
$env:STOCKPILOT_SHUTDOWN_TIMEOUT = "8"
Uninstall "hung" "/DELETEDATA"
Remove-Item Env:\STOCKPILOT_SHUTDOWN_TIMEOUT
Check "[hung] uninstall refused" (StillInstalled)
Check "[hung] app NOT force-closed (same process still running)" ((AppPid) -eq $hungPid)
Check "[hung] nothing deleted" ((Test-Path (Join-Path $Data "trading_demo.db")) -and ((PyKey) -eq "PKCITEST"))
Stop-Process -Id $hungPid -Force -ErrorAction SilentlyContinue   # test cleanup only
for ($i = 0; $i -lt 10 -and (AppRunning); $i++) { Start-Sleep 1 }

Write-Host "`n== the app's program can't run and there is real-money history: refuse"
python -c "from stockpilot.storage.db import Database; from stockpilot import paths; Database(paths.database_file('live'),'live')"
Rename-Item $Exe "$Exe.bak"
Uninstall "exe-missing-live" "/DELETEDATA"
Rename-Item "$Exe.bak" $Exe
Check "[exe-missing-live] uninstall refused (positions can't be verified)" (StillInstalled)
Check "[exe-missing-live] nothing deleted" ((Test-Path (Join-Path $Data "trading_live.db")) -and ((PyKey) -eq "PKCITEST"))
Remove-Item (Join-Path $Data "trading_live.db*") -Force
Launch "before-clean"

Set-Content (Join-Path $Data "ci-marker.txt") "written before the clean uninstall"
Uninstall "clean" "/DELETEDATA"
Check "[clean] running app was shut down" (-not (AppRunning))
Check "[clean] program files removed" (-not (Test-Path $App))
Check "[clean] removed from Installed Apps" (-not (Test-Path $UninstKey))
Check "[clean] settings, databases and logs deleted" (-not (Test-Path $Data))
Check "[clean] saved keys deleted" ((PyKey) -eq "NONE")
Check "[clean] shortcuts removed" ((Shortcuts).Count -eq 0)
ShowLeftovers "clean"

Install "after-clean"
Launch "after-clean"
Check "[after-clean] starts fresh (nothing from before)" (-not (Test-Path (Join-Path $Data "ci-marker.txt")))
Write-Host "`n== the program is missing but nothing is running and there is no real-money history: proceed"
QuitApp
Rename-Item $Exe "$Exe.bak"
Uninstall "final" "/DELETEDATA"
Check "[final] fully removed" ((-not (Test-Path $App)) -and (-not (Test-Path $UninstKey)) -and (-not (AppRunning)) -and (-not (Test-Path $Data)))
ShowLeftovers "final"

Write-Host "`n== summary"
$Results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
Get-Content (Join-Path $env:TEMP "StockPilot-uninstall.log") -ErrorAction SilentlyContinue | Select-Object -Last 30 | Write-Host
$failed = @($Results | Where-Object { $_.Result -eq "FAIL" }).Count
Write-Host "$failed check(s) failed"
if ($Strict -and $failed -gt 0) { exit 1 }
exit 0

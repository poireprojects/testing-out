# End-to-end install/uninstall test on a real Windows machine (used by CI).
#   powershell -File packaging\windows\test_uninstall.ps1 -Installer dist\installer\setup.exe [-Strict] [-SkipLiveCheck]
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [switch]$Strict,
    [switch]$SkipLiveCheck
)
$ErrorActionPreference = "Continue"
$Installer = (Resolve-Path $Installer).Path
$App = Join-Path $env:LOCALAPPDATA "Programs\dontknowwhatimdoing"
$Exe = Join-Path $App "dontknowwhatimdoing.exe"
$Data = Join-Path $env:LOCALAPPDATA "dontknowwhatimdoing"
$UninstKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C6B0E2A-5D1F-4C3B-9B7E-2D1F6A9E4C11}_is1"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Desktop = [Environment]::GetFolderPath("Desktop")
$Results = New-Object System.Collections.ArrayList

function Check([string]$Name, [bool]$Ok) {
    [void]$Results.Add([pscustomobject]@{ Result = $(if ($Ok) { "PASS" } else { "FAIL" }); Check = $Name })
    Write-Host ("{0}  {1}" -f $(if ($Ok) { "PASS" } else { "FAIL" }), $Name)
}
function AppRunning { [bool](Get-Process -Name "dontknowwhatimdoing" -ErrorAction SilentlyContinue) }
function Shortcuts { @(Get-ChildItem $StartMenu, $Desktop -Filter "don*know what im doing*.lnk" -ErrorAction SilentlyContinue) }
function PyKey { python -c "import keyring; print(keyring.get_password('dontknowwhatimdoing','alpaca-paper-key-id') or 'NONE')" }

function Install([string]$Label) {
    Write-Host "`n== install ($Label)"
    $p = Start-Process $Installer "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS=desktopicon /LOG=`"$env:RUNNER_TEMP\install-$Label.log`"" -Wait -PassThru
    Check "[$Label] installer exit code 0 (got $($p.ExitCode))" ($p.ExitCode -eq 0)
    Check "[$Label] app installed" (Test-Path $Exe)
    Check "[$Label] registered in Installed Apps" (Test-Path $UninstKey)
    Check "[$Label] Start menu + desktop shortcuts created" ((Shortcuts).Count -ge 2)
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
# what the app writes when "launch when i log in" is on
New-ItemProperty -Path $RunKey -Name "dontknowwhatimdoing" -Value "`"$Exe`" --minimized" -PropertyType String -Force | Out-Null
python -c "import keyring; keyring.set_password('dontknowwhatimdoing','alpaca-paper-key-id','PKCITEST')"
Check "[first] test credential saved" ((PyKey) -eq "PKCITEST")

Uninstall "keep-data" ""
Check "[keep-data] running app was shut down" (-not (AppRunning))
Check "[keep-data] program files removed" (-not (Test-Path $App))
Check "[keep-data] removed from Installed Apps" (-not (Test-Path $UninstKey))
Check "[keep-data] launch-at-login entry removed" (-not (Get-ItemProperty $RunKey -Name "dontknowwhatimdoing" -ErrorAction SilentlyContinue))
Check "[keep-data] shortcuts removed" ((Shortcuts).Count -eq 0)
Check "[keep-data] settings/history kept (user chose keep)" ((Test-Path (Join-Path $Data "settings.json")) -or (Test-Path (Join-Path $Data "trading_demo.db")))
Check "[keep-data] saved keys kept" ((PyKey) -eq "PKCITEST")

Install "reinstall"
Check "[reinstall] previous data still there" (Test-Path (Join-Path $Data "trading_demo.db"))
Launch "reinstall"

if (-not $SkipLiveCheck) {
    Write-Host "`n== open live position must block uninstall"
    python -c "from dkapp.storage.db import Database; from dkapp import paths; db=Database(paths.database_file('live'),'live'); db.insert('trades', symbol='SPY', strategy='orb', mode='live', status='open', managed=1, session_date='2026-01-02', created_at='2026-01-02T15:00:00+00:00', entry_qty=0.01, entry_price=500)"
    Uninstall "blocked" "/DELETEDATA"
    Check "[blocked] uninstall refused while a live position is open" ((Test-Path $Exe) -and (Test-Path $UninstKey))
    Check "[blocked] nothing deleted" ((Test-Path (Join-Path $Data "trading_demo.db")) -and ((PyKey) -eq "PKCITEST"))
    Check "[blocked] app left running" (AppRunning)
    Remove-Item (Join-Path $Data "trading_live.db*") -Force
}

Uninstall "clean" "/DELETEDATA"
Check "[clean] running app was shut down" (-not (AppRunning))
Check "[clean] program files removed" (-not (Test-Path $App))
Check "[clean] removed from Installed Apps" (-not (Test-Path $UninstKey))
Check "[clean] settings, databases and logs deleted" (-not (Test-Path $Data))
Check "[clean] saved keys deleted" ((PyKey) -eq "NONE")
Check "[clean] shortcuts removed" ((Shortcuts).Count -eq 0)

Install "after-clean"
Launch "after-clean"
Check "[after-clean] starts fresh (no old history)" (-not (Test-Path (Join-Path $Data "trading_paper.db")))
Uninstall "final" "/DELETEDATA"
Check "[final] fully removed" ((-not (Test-Path $App)) -and (-not (Test-Path $UninstKey)) -and (-not (AppRunning)))

Write-Host "`n== summary"
$Results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
Get-Content (Join-Path $env:TEMP "dontknowwhatimdoing-uninstall.log") -ErrorAction SilentlyContinue | Select-Object -Last 30 | Write-Host
$failed = @($Results | Where-Object { $_.Result -eq "FAIL" }).Count
Write-Host "$failed check(s) failed"
if ($Strict -and $failed -gt 0) { exit 1 }
exit 0

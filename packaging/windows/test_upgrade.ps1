# Upgrade-in-place test on a real Windows machine (used by CI): the last release published under the app's
# previous name, installed and running with data, a saved key and launch-at-login, then upgraded by this build's
# StockPilot installer. Nothing of the previous name may be left, and nothing may be lost.
#   powershell -File packaging\windows\test_upgrade.ps1 -OldInstaller old\setup.exe -NewInstaller dist\installer\setup.exe
param(
    [Parameter(Mandatory = $true)][string]$OldInstaller,
    [Parameter(Mandatory = $true)][string]$NewInstaller
)
$ErrorActionPreference = "Continue"
$OldInstaller = (Resolve-Path $OldInstaller).Path
$NewInstaller = (Resolve-Path $NewInstaller).Path
# the previous name: used here only to prove the upgrade removes it
$OldId = "dontknowwhatimdoing"
$OldApp = Join-Path $env:LOCALAPPDATA "Programs\$OldId"
$OldExe = Join-Path $OldApp "$OldId.exe"
$OldData = Join-Path $env:LOCALAPPDATA $OldId
$NewApp = Join-Path $env:LOCALAPPDATA "Programs\StockPilot"
$NewExe = Join-Path $NewApp "StockPilot.exe"
$NewData = Join-Path $env:LOCALAPPDATA "StockPilot"
$UninstRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$Links = @((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"), [Environment]::GetFolderPath("Desktop"))
$Results = New-Object System.Collections.ArrayList

function Check([string]$Name, [bool]$Ok) {
    [void]$Results.Add([pscustomobject]@{ Result = $(if ($Ok) { "PASS" } else { "FAIL" }); Check = $Name })
    Write-Host ("{0}  {1}" -f $(if ($Ok) { "PASS" } else { "FAIL" }), $Name)
}
function Running([string]$Image) { [bool](Get-Process -Name $Image -ErrorAction SilentlyContinue) }
function LinksLike([string]$Pattern) { @(Get-ChildItem $Links -Recurse -Filter $Pattern -ErrorAction SilentlyContinue) }
function Key([string]$Service) { python -c "import keyring; print(keyring.get_password('$Service','alpaca-paper-key-id') or 'NONE')" }
function AppsEntries { @(Get-ChildItem $UninstRoot -ErrorAction SilentlyContinue | Get-ItemProperty -ErrorAction SilentlyContinue |
                         Where-Object { $_.DisplayName -like "StockPilot*" -or $_.DisplayName -like "*know what im doing*" }) }

# ---- 1) the previous version, the way a person has it: installed, set up, running in the tray
Write-Host "`n== install the previous version"
$p = Start-Process $OldInstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS=desktopicon /LOG=`"$env:RUNNER_TEMP\old-install.log`"" -Wait -PassThru
Check "[previous] installed (exit $($p.ExitCode))" (($p.ExitCode -eq 0) -and (Test-Path $OldExe))
New-ItemProperty -Path $RunKey -Name $OldId -Value "`"$OldExe`" --minimized" -PropertyType String -Force | Out-Null
python -c "import keyring; keyring.set_password('$OldId','alpaca-paper-key-id','PKUPGRADETEST')"
Start-Process $OldExe "--minimized" | Out-Null
$ok = $false
for ($i = 0; $i -lt 45 -and -not $ok; $i++) { Start-Sleep 1; $ok = (Running $OldId) -and (Test-Path (Join-Path $OldData "app.lock")) }
Check "[previous] running in the tray with its data folder" $ok
Set-Content -Path (Join-Path $OldData "upgrade-marker.txt") -Value "kept across the upgrade"
Start-Sleep 5

# ---- 2) this build's installer over it, silently (like an automatic update)
Write-Host "`n== upgrade to StockPilot"
$q = Start-Process $NewInstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=`"$env:RUNNER_TEMP\upgrade.log`"" -Wait -PassThru
Check "[upgrade] installer exit code 0 (got $($q.ExitCode))" ($q.ExitCode -eq 0)
Check "[upgrade] the previous version was closed by its own safety check" (-not (Running $OldId))
Check "[upgrade] StockPilot installed" (Test-Path $NewExe)
Check "[upgrade] previous program folder removed" (-not (Test-Path $OldApp))
Check "[upgrade] no shortcut with the previous name" ((LinksLike "*know what im doing*.lnk").Count -eq 0)
Check "[upgrade] StockPilot Start menu and desktop shortcuts" ((LinksLike "StockPilot.lnk").Count -ge 2)
Check "[upgrade] data folder moved to StockPilot (nothing lost)" ((Test-Path (Join-Path $NewData "upgrade-marker.txt")) -and -not (Test-Path $OldData))
$run = Get-ItemProperty $RunKey -ErrorAction SilentlyContinue
Check "[upgrade] launch-at-login now starts StockPilot" ((-not $run.$OldId) -and ($run.StockPilot -like "*StockPilot.exe*"))
$apps = AppsEntries
Check "[upgrade] exactly one Installed Apps entry, named StockPilot" (($apps.Count -eq 1) -and ($apps[0].DisplayName -eq "StockPilot"))

# ---- 3) StockPilot's first start moves the saved key (its safety-check command runs the same migration)
$c = Start-Process $NewExe "--check-live" -Wait -PassThru
Check "[first start] safety check ok (exit $($c.ExitCode))" ($c.ExitCode -eq 0)
Check "[first start] saved key moved to StockPilot" (((Key "StockPilot") -eq "PKUPGRADETEST") -and ((Key $OldId) -eq "NONE"))

# ---- 4) clean up
Write-Host "`n== clean up"
$u = Join-Path $NewApp "unins000.exe"
if (Test-Path $u) { Start-Process $u "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DELETEDATA" -Wait | Out-Null; Start-Sleep 6 }
Check "[cleanup] StockPilot uninstalled" (-not (Test-Path $NewExe))
python -c "import keyring
for s in ('StockPilot', '$OldId'):
    try: keyring.delete_password(s, 'alpaca-paper-key-id')
    except Exception: pass"

Write-Host "`n== upgrade log (previous version / data / launch-at-login)"
Select-String -Path "$env:RUNNER_TEMP\upgrade.log" -Pattern "previous|moved|launch-at-login|safety check|process check" -ErrorAction SilentlyContinue |
    Select-Object -Last 20 | ForEach-Object { Write-Host "  $($_.Line)" }
$Results | Format-Table -AutoSize | Out-String | Write-Host
if (@($Results | Where-Object { $_.Result -eq "FAIL" }).Count -gt 0) { throw "upgrade test failed" }

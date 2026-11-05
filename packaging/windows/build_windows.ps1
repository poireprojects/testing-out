# Builds the Windows app + installer from source. Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\pyinstaller packaging\dk.spec --noconfirm --clean
.\dist\dontknowwhatimdoing\dontknowwhatimdoing.exe --self-test | Out-Null
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { winget install --id JRSoftware.InnoSetup -e --silent; }
& $iscc packaging\windows\installer.iss
Write-Host "Installer created in dist\installer"

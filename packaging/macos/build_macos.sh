#!/bin/bash
# Builds the macOS .app and a .dmg from source. Run from anywhere:  bash packaging/macos/build_macos.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
APP="StockPilot"
PY=${PYTHON:-python3.12}
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller packaging/stockpilot.spec --noconfirm --clean
"dist/$APP.app/Contents/MacOS/StockPilot" --self-test >/dev/null
rm -rf dist/dmg && mkdir -p dist/dmg && cp -R "dist/$APP.app" dist/dmg/ && ln -s /Applications dist/dmg/Applications
hdiutil create -volname "$APP" -srcfolder dist/dmg -ov -format UDZO dist/StockPilot-macos.dmg
echo "Built dist/StockPilot-macos.dmg — open it and drag the app to Applications."

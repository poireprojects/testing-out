#!/bin/bash
# Builds a Linux binary folder and installs a desktop launcher for the current user.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller packaging/stockpilot.spec --noconfirm --clean
DEST="$HOME/.local/share/StockPilot"
rm -rf "$DEST" && mkdir -p "$DEST" && cp -R dist/StockPilot/. "$DEST/"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons"
cp stockpilot/assets/icons/stockpilot.png "$HOME/.local/share/icons/StockPilot.png"
cat > "$HOME/.local/share/applications/StockPilot.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=StockPilot
Exec=$DEST/StockPilot
Icon=$HOME/.local/share/icons/StockPilot.png
Categories=Finance;Office;
DESK
echo "Installed. Find 'StockPilot' in your applications menu."

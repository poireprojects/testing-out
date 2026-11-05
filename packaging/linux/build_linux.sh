#!/bin/bash
# Builds a Linux binary folder and installs a desktop launcher for the current user.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/pyinstaller packaging/dk.spec --noconfirm --clean
DEST="$HOME/.local/share/dontknowwhatimdoing"
rm -rf "$DEST" && mkdir -p "$DEST" && cp -R dist/dontknowwhatimdoing/. "$DEST/"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons"
cp dkapp/assets/icons/dk.png "$HOME/.local/share/icons/dontknowwhatimdoing.png"
cat > "$HOME/.local/share/applications/dontknowwhatimdoing.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=don’t know what im doing
Exec=$DEST/dontknowwhatimdoing
Icon=$HOME/.local/share/icons/dontknowwhatimdoing.png
Categories=Finance;Office;
DESK
echo "Installed. Find 'don’t know what im doing' in your applications menu."

#!/bin/bash
# Double-click in Finder to build the macOS app (needs Python 3.12 from python.org).
bash "$(dirname "$0")/build_macos.sh"
read -n 1 -s -r -p "Done. Press any key to close."

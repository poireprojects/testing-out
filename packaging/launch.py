"""PyInstaller entry point."""
import sys

from stockpilot.ui.app import main

if __name__ == "__main__":
    sys.exit(main())

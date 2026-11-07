import sys


def main() -> int:
    from stockpilot.ui.app import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())

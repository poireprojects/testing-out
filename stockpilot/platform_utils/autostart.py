"""Launch-at-login for Windows (registry Run key), macOS (LaunchAgent) and Linux (XDG autostart)."""
from __future__ import annotations

import logging
import os
import plistlib
import shlex
import sys
from pathlib import Path

from .. import APP_ID, APP_NAME, BUNDLE_ID

log = logging.getLogger(__name__)


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    return [sys.executable, "-m", "stockpilot", "--minimized"]


def is_enabled() -> bool:
    try:
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_ID)
                return True
        if sys.platform == "darwin":
            return (Path.home() / "Library/LaunchAgents" / f"{BUNDLE_ID}.plist").exists()
        return (Path.home() / ".config/autostart" / f"{APP_ID}.desktop").exists()
    except OSError:
        return False


def set_enabled(on: bool) -> tuple[bool, str]:
    try:
        cmd = _command()
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_SET_VALUE) as k:
                if on:
                    winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, " ".join(f'"{c}"' if " " in c else c for c in cmd))
                else:
                    try:
                        winreg.DeleteValue(k, APP_ID)
                    except FileNotFoundError:
                        pass
        elif sys.platform == "darwin":
            p = Path.home() / "Library/LaunchAgents" / f"{BUNDLE_ID}.plist"
            if on:
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("wb") as fh:
                    plistlib.dump({"Label": BUNDLE_ID, "ProgramArguments": cmd, "RunAtLoad": True}, fh)
            elif p.exists():
                p.unlink()
        else:
            p = Path.home() / ".config/autostart" / f"{APP_ID}.desktop"
            if on:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={shlex.join(cmd)}\n"
                             "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
            elif p.exists():
                p.unlink()
        return True, "launch at login " + ("enabled" if on else "disabled")
    except Exception as exc:
        log.warning("autostart change failed: %s", exc)
        return False, f"couldn't change launch-at-login: {exc}"


def open_folder(path: Path) -> None:
    import subprocess
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

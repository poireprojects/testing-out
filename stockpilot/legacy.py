"""Carry an existing installation over from the app's previous name to StockPilot.

Before version 1.1 the app had another name, and its data folders, saved API keys, launch-at-login entry and
broker order ids were labelled with it. This module is the only part of the app that knows that name, and it
uses it for one purpose: on the first start of StockPilot, move everything to the new name so nothing is lost.
Nothing here is ever shown in the interface.

* The data folders (settings, trade history, logs, test copies) are moved, never copied or merged: if StockPilot
  already has data, the old folder is left alone and logged.
* Saved keys move from the old keychain entries to StockPilot's; an old entry is deleted only after the new one
  reads back identical.
* A launch-at-login entry under the old name is replaced by one for StockPilot.
* On macOS the previous app (found by its bundle id in /Applications or ~/Applications) is moved to the Trash, so
  only StockPilot is left in Applications and Launchpad. The Trash keeps it recoverable.
* If the previous version is still running, nothing is moved: two copies must never trade one account, and its
  files are in use. The caller refuses to start (the app) or refuses the safety check (installer commands).
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

from . import APP_ID

log = logging.getLogger(__name__)

PREVIOUS_APP_ID = "dontknowwhatimdoing"
PREVIOUS_BUNDLE_ID = "com.dontknowwhatimdoing.app"
PREVIOUS_ORDER_TAG = "dk"        # the first letters of every broker order id the previous version placed
CREDENTIAL_NAMES = ("alpaca-paper-key-id", "alpaca-paper-secret", "alpaca-live-key-id", "alpaca-live-secret",
                    "anthropic-api-key")
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _dirs(app_id: str) -> list[Path]:
    seen: list[Path] = []
    for fn in (user_data_dir, user_config_dir, user_log_dir):
        p = Path(fn(app_id, appauthor=False))
        if p not in seen:
            seen.append(p)
    return seen


def _overridden() -> bool:
    return bool(os.environ.get("STOCKPILOT_HOME"))       # tests and test copies use their own folder


def previous_copy_running() -> bool:
    """True if the previous version holds its single-instance lock (it is open, perhaps hidden in the tray)."""
    if _overridden():
        return False
    lock_file = Path(user_data_dir(PREVIOUS_APP_ID, appauthor=False)) / "app.lock"
    if not lock_file.exists():
        return False
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(lock_file))
    lock.setStaleLockTime(0)
    if lock.tryLock(300):
        lock.unlock()
        return False
    return True


def _empty(p: Path) -> bool:
    return p.is_dir() and not any(p.iterdir())


def _move_folders() -> list[str]:
    done = []
    for old, new in zip(_dirs(PREVIOUS_APP_ID), _dirs(APP_ID)):
        if not old.exists() or old == new:
            continue
        if new.exists() and not _empty(new):
            log.warning("both %s and %s exist; the older folder was left untouched", old, new)
            continue
        if new.exists():
            new.rmdir()
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
        done.append(f"{old} -> {new}")
    return done


def _services() -> list[tuple[str, str]]:
    pairs = [(PREVIOUS_APP_ID, APP_ID)]
    prof = Path(user_data_dir(APP_ID, appauthor=False)) / "profiles"
    if prof.is_dir():
        pairs += [(f"{PREVIOUS_APP_ID}-profile-{p.name}", f"{APP_ID}-profile-{p.name}") for p in prof.iterdir() if p.is_dir()]
    return pairs


def _move_keys() -> int:
    try:
        import keyring
    except Exception:
        return 0
    moved = 0
    for old_svc, new_svc in _services():
        for name in CREDENTIAL_NAMES:
            try:
                value = keyring.get_password(old_svc, name)
                if not value:
                    continue
                current = keyring.get_password(new_svc, name)
                if current is None:
                    keyring.set_password(new_svc, name, value)
                    current = keyring.get_password(new_svc, name)
                if current == value:
                    keyring.delete_password(old_svc, name)
                    moved += 1
                else:
                    log.warning("kept the older saved key %s: StockPilot already has a different one", name)
            except Exception as exc:                     # a keychain that refuses must never stop the app
                log.warning("could not move saved key %s: %s", name, exc)
    return moved


def _move_autostart() -> bool:
    """Replace a launch-at-login entry under the old name with StockPilot's. True if one was replaced."""
    had = False
    try:
        if sys.platform == "win32":
            import winreg
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
            except FileNotFoundError:                        # no launch-at-login entries at all
                return False
            with k:
                try:
                    winreg.QueryValueEx(k, PREVIOUS_APP_ID)
                    winreg.DeleteValue(k, PREVIOUS_APP_ID)
                    had = True
                except FileNotFoundError:
                    pass
        else:
            old = (Path.home() / "Library/LaunchAgents" / f"{PREVIOUS_BUNDLE_ID}.plist" if sys.platform == "darwin"
                   else Path.home() / ".config/autostart" / f"{PREVIOUS_APP_ID}.desktop")
            if old.exists():
                old.unlink()
                had = True
        if had:
            from .platform_utils import autostart
            autostart.set_enabled(True)
    except Exception as exc:
        log.warning("could not move the launch-at-login entry: %s", exc)
    return had


def _app_dirs() -> list[Path]:
    return [Path("/Applications"), Path.home() / "Applications"]


def _to_trash(path: Path) -> bool:
    from PySide6.QtCore import QFile
    r = QFile.moveToTrash(str(path))
    return bool(r[0] if isinstance(r, tuple) else r)        # (ok, path in trash) in some PySide6 versions


def _retire_previous_mac_app() -> list[str]:
    """Move the previous version's .app bundle to the Trash (macOS only). Returns the bundles moved."""
    if sys.platform != "darwin":
        return []
    import plistlib
    done = []
    for d in _app_dirs():
        try:
            bundles = [b for b in d.iterdir() if b.suffix == ".app" and b.is_dir()] if d.is_dir() else []
        except OSError:
            continue
        for b in bundles:
            try:
                with open(b / "Contents" / "Info.plist", "rb") as f:
                    if plistlib.load(f).get("CFBundleIdentifier") != PREVIOUS_BUNDLE_ID:
                        continue
                if _to_trash(b):
                    done.append(str(b))
                else:
                    log.warning("could not move the previous app %s to the Trash", b)
            except Exception as exc:
                log.warning("could not check %s: %s", b, exc)
    return done


def migrate() -> dict:
    """Move an installation of the previous version to StockPilot. Cheap and safe to call on every start."""
    if _overridden():
        return {"skipped": "custom data folder"}
    if previous_copy_running():
        return {"skipped": "the previous version is still running"}
    out: dict = {}
    folders = _move_folders()
    if folders:
        out["folders"] = folders
    keys = _move_keys()
    if keys:
        out["keys"] = keys
    if _move_autostart():
        out["autostart"] = True
    apps = _retire_previous_mac_app()
    if apps:
        out["previous_app_to_trash"] = apps
    return out


def order_prefix(mode: str, tag: str) -> str:
    """The client_order_id prefix the previous version used for this database's orders."""
    return f"{PREVIOUS_ORDER_TAG}{mode[0]}{tag}-"

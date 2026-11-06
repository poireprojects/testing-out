"""Install/uninstall maintenance commands, run by the Windows installer and uninstaller.

    dontknowwhatimdoing.exe --check-live                         (read-only)
    dontknowwhatimdoing.exe --uninstall-prepare [--delete-data]
    dontknowwhatimdoing.exe --shutdown-running

Exit codes (read by packaging/windows/installer.iss):
    0  done
    3  blocked: open LIVE positions must be resolved in the app first (nothing was changed)
    4  a running copy didn't shut down in time
    5  unexpected error (details in %TEMP%\\dontknowwhatimdoing-uninstall.log)

Order matters: everything here runs *before* any program file is removed, so the app is
never left running from deleted files and data is never deleted while still open.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import APP_ID, credentials, paths

OK, BLOCKED_LIVE, STILL_RUNNING, ERROR = 0, 3, 4, 5
COMMANDS = ("--uninstall-prepare", "--shutdown-running", "--check-live")


def _log(msg: str) -> None:
    try:
        with open(Path(tempfile.gettempdir()) / f"{APP_ID}-uninstall.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def open_live_positions(data_dir: Path | None = None) -> list[str]:
    """Symbols the app is managing with REAL money (read straight from the live database, read-only)."""
    db = (data_dir or paths.data_dir()) / "trading_live.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT symbol FROM trades WHERE managed=1 AND status IN ('pending_entry','open','closing')"
                           ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except sqlite3.Error as exc:
        _log(f"could not read live database: {exc}")
        return ["(unreadable live database)"]  # be safe: treat as open


def _lock_free(timeout: float) -> bool:
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(paths.data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if lock.tryLock(200):
            lock.unlock()
            return True
        time.sleep(0.5)
    return False


def shutdown_running(timeout: float = 45.0) -> int:
    """Ask a running copy (e.g. hidden in the tray) to quit gracefully, then wait for it to exit."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    QCoreApplication.instance() or QCoreApplication([])
    if _lock_free(0.5):
        return OK  # nothing running
    sock = QLocalSocket()
    sock.connectToServer(APP_ID)
    if not sock.waitForConnected(3000):
        _log("app lock is held but the app didn't answer")
        return OK if _lock_free(timeout) else STILL_RUNNING
    sock.write(b"quit")
    sock.flush()
    sock.waitForBytesWritten(2000)
    reply = b""
    if sock.waitForReadyRead(10000):
        reply = bytes(sock.readAll().data())
    _log(f"running app replied: {reply!r}")
    if reply.startswith(b"busy"):
        return BLOCKED_LIVE
    return OK if _lock_free(timeout) else STILL_RUNNING


def _all_credential_services() -> list[str]:
    services = [APP_ID]
    prof = paths.data_dir() / "profiles"
    if prof.exists():
        services += [f"{APP_ID}-profile-{p.name}" for p in prof.iterdir() if p.is_dir()]
    return services


def delete_saved_keys() -> None:
    original = credentials.SERVICE
    try:
        for svc in _all_credential_services():
            credentials.SERVICE = svc
            for env in ("paper", "live"):
                credentials.delete_alpaca_keys(env)
            credentials.delete_anthropic_key()
    finally:
        credentials.SERVICE = original


def delete_user_data() -> list[str]:
    left = []
    for folder in {paths.config_dir(), paths.data_dir(), paths.log_dir()}:
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            left.append(str(folder))
    # platformdirs puts everything under one folder on Windows; remove it if now empty
    root = paths.data_dir().parent if sys.platform == "win32" else None
    if root and root.name == APP_ID and root.exists():
        shutil.rmtree(root, ignore_errors=True)
    return left


def uninstall_prepare(delete_data: bool) -> int:
    live = open_live_positions()
    if live:
        _log(f"blocked: open live positions {live}")
        return BLOCKED_LIVE
    code = shutdown_running()
    if code != OK:
        return code
    from .platform_utils import autostart
    ok, msg = autostart.set_enabled(False)
    _log(f"autostart: {msg}")
    if delete_data:
        delete_saved_keys()
        left = delete_user_data()
        _log(f"data deleted (left: {left})")
    return OK


def run(argv: list[str]) -> int | None:
    """Returns an exit code if argv is a maintenance command, else None (normal app start)."""
    if not any(c in argv for c in COMMANDS):
        return None
    try:
        if "--check-live" in argv:  # read-only: used before the uninstall is confirmed
            return BLOCKED_LIVE if open_live_positions() else OK
        if "--shutdown-running" in argv:
            if open_live_positions():
                return BLOCKED_LIVE
            return shutdown_running()
        return uninstall_prepare("--delete-data" in argv)
    except Exception:
        _log(traceback.format_exc())
        return ERROR

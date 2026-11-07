"""Keep the computer from going to sleep while the market is open (optional).

Closing a laptop lid usually forces sleep regardless; this only prevents *idle* sleep.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)


class KeepAwake:
    def __init__(self):
        self.active = False
        self._proc: subprocess.Popen | None = None

    def set(self, on: bool) -> None:
        if on == self.active:
            return
        try:
            if sys.platform == "win32":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
                ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                if on:
                    self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
                elif self._proc:
                    self._proc.terminate()
                    self._proc = None
            else:
                if on:
                    self._proc = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=StockPilot",
                                                   "--why=market open", "sleep", "infinity"],
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif self._proc:
                    self._proc.terminate()
                    self._proc = None
            self.active = on
        except Exception as exc:  # best effort
            log.info("keep-awake unavailable: %s", exc)
            self.active = False

"""Is the computer running on battery? (Laptops on battery sleep sooner and die mid-trade.)

Returns True / False, or None when it can't be told (desktop, unknown platform, error).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional


def on_battery() -> Optional[bool]:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                            ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                            ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
            st = SYSTEM_POWER_STATUS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):  # type: ignore[attr-defined]
                return None
            if st.BatteryFlag & 0xFF == 128:  # no system battery
                return False
            return {0: True, 1: False}.get(st.ACLineStatus & 0xFF)
        if sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            if "Battery Power" in out:
                return True
            if "AC Power" in out:
                return False
            return None
        supplies = list(Path("/sys/class/power_supply").glob("*"))
        mains = [s for s in supplies if (s / "type").exists() and (s / "type").read_text().strip() == "Mains"]
        if not mains:
            return None
        return not any((s / "online").exists() and (s / "online").read_text().strip() == "1" for s in mains)
    except Exception:
        return None

"""Filesystem locations. Everything is stored locally on this computer.

Set the environment variable ``STOCKPILOT_HOME`` to redirect all data (used by tests).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

from . import APP_ID


def _root_override() -> Path | None:
    v = os.environ.get("STOCKPILOT_HOME")
    return Path(v) if v else None


def config_dir() -> Path:
    root = _root_override()
    p = root / "config" if root else Path(user_config_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    root = _root_override()
    p = root / "data" if root else Path(user_data_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    root = _root_override()
    p = root / "logs" if root else Path(user_log_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_file() -> Path:
    return config_dir() / "settings.json"


def database_file(mode: str) -> Path:
    """Separate database per trading mode so demo/paper/live never mix."""
    if mode not in ("demo", "paper", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    return data_dir() / f"trading_{mode}.db"


def resource_dir() -> Path:
    """Bundled read-only resources (QML, fonts, icons). Works in PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "stockpilot"
    return Path(__file__).resolve().parent


def assets_dir() -> Path:
    return resource_dir() / "assets"


def qml_dir() -> Path:
    return resource_dir() / "ui" / "qml"

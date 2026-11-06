"""Separate test copies ("profiles") and factory reset.

Windows Home has no Windows Sandbox, so repeatable testing is built into the app:

* ``--profile test`` runs a completely separate copy with its own settings, history and
  saved keys (keys live under their own name in the credential store).
* ``--fresh`` wipes that profile before starting, so every launch is a brand-new install.
  It only ever touches a profile, never your main data.
* :func:`factory_reset` erases the current copy's settings, history and keys in place.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from . import APP_ID, credentials, paths

log = logging.getLogger(__name__)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
active_profile: Optional[str] = None


def parse_args(argv: list[str]) -> tuple[Optional[str], bool]:
    name = None
    for i, a in enumerate(argv):
        if a.startswith("--profile="):
            name = a.split("=", 1)[1]
        elif a == "--profile" and i + 1 < len(argv):
            name = argv[i + 1]
    if name is not None:
        name = name.strip().lower()
        if not _NAME.match(name):
            raise ValueError(f"invalid profile name {name!r} (use letters, numbers and dashes)")
    return name, "--fresh" in argv


def _forget_keys() -> None:
    for env in ("paper", "live"):
        credentials.delete_alpaca_keys(env)
    credentials.delete_anthropic_key()


def activate(name: str, fresh: bool) -> Path:
    """Point all storage (files and credential store) at a separate profile."""
    global active_profile
    root = paths.data_dir() / "profiles" / name
    credentials.SERVICE = f"{APP_ID}-profile-{name}"
    if fresh:
        shutil.rmtree(root, ignore_errors=True)
        _forget_keys()
        log.info("profile %s wiped for a fresh start", name)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DK_HOME"] = str(root)
    active_profile = name
    return root


def factory_reset() -> list[str]:
    """Delete this copy's settings, databases, caches and saved keys. Returns anything that couldn't be removed."""
    _forget_keys()
    leftovers: list[str] = []
    for folder in {paths.config_dir(), paths.data_dir()}:
        for item in folder.iterdir():
            if item.name in ("profiles", "app.lock"):
                continue
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            except OSError as exc:
                leftovers.append(f"{item.name}: {exc}")
    return leftovers

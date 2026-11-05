"""Secrets live in the operating system's secure credential store
(Windows Credential Manager, macOS Keychain, Linux Secret Service) via `keyring`.
They are never written to settings.json, logs, or the database."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from . import APP_ID

log = logging.getLogger(__name__)
SERVICE = APP_ID


@dataclass(frozen=True)
class AlpacaKeys:
    key_id: str
    secret: str


def secure_backend_available() -> tuple[bool, str]:
    try:
        kr = keyring.get_keyring()
    except Exception as exc:  # pragma: no cover - platform specific
        return False, f"no credential store: {exc}"
    name = f"{type(kr).__module__}.{type(kr).__name__}"
    insecure = ("fail", "null", "PlaintextKeyring")
    try:
        priority = float(getattr(kr, "priority", 1))
    except Exception:  # pragma: no cover
        priority = 1
    if any(s in name for s in insecure) or priority <= 0:
        return False, f"no secure credential store available ({name})"
    return True, name


def _set(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


_warned = False


def _get(name: str) -> Optional[str]:
    global _warned
    try:
        return keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        if not _warned:
            _warned = True
            log.warning("credential store unavailable: %s", exc)
        return None


def _delete(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except (PasswordDeleteError, KeyringError):
        pass


def _alpaca_names(env: str) -> tuple[str, str]:
    if env not in ("paper", "live"):
        raise ValueError("env must be 'paper' or 'live'")
    return f"alpaca-{env}-key-id", f"alpaca-{env}-secret"


def save_alpaca_keys(env: str, key_id: str, secret: str) -> None:
    key_id, secret = key_id.strip(), secret.strip()
    if not key_id or not secret:
        raise ValueError("both the API key ID and secret are required")
    k, s = _alpaca_names(env)
    _set(k, key_id)
    _set(s, secret)


def load_alpaca_keys(env: str) -> Optional[AlpacaKeys]:
    k, s = _alpaca_names(env)
    key_id, secret = _get(k), _get(s)
    if key_id and secret:
        return AlpacaKeys(key_id, secret)
    return None


def delete_alpaca_keys(env: str) -> None:
    for n in _alpaca_names(env):
        _delete(n)


def save_anthropic_key(api_key: str) -> None:
    if not api_key.strip():
        raise ValueError("API key is empty")
    _set("anthropic-api-key", api_key.strip())


def load_anthropic_key() -> Optional[str]:
    return _get("anthropic-api-key")


def delete_anthropic_key() -> None:
    _delete("anthropic-api-key")


def mask(secret: Optional[str]) -> str:
    if not secret:
        return ""
    return secret[:4] + "•" * 6 + secret[-2:] if len(secret) > 8 else "•" * 6

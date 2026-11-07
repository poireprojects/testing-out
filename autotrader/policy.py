"""Owner policy: the boundaries of the autonomous trader's authority.

The owner sets it; the AI never can. The AI's only output is a JSON decision whose schema has no field for
size, limits, mode or instruments outside the event, and the risk controller computes the size itself.
Risk limits are the desktop app's own defaults, so this prototype runs under the same $100 / $20 / 2 positions /
$2 daily loss as the app. Phase 5 permits simulation only: any other mode is refused when the policy loads.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from dkapp.config import AppSettings

POLICY_PATH = Path(__file__).resolve().parent / "owner_policy.json"
ALLOWED_MODES = ("simulate",)
# Credentials that were exposed and must not be reused (identified by key-ID prefix only).
EXPOSED_KEY_PREFIXES = ("PKJB7U",)


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnerPolicy:
    mode: str
    long_only: bool
    decision_time: str
    exit_by: str
    max_consecutive_model_failures: int
    max_consecutive_data_failures: int
    kill_switch_file: str
    sha256: str

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> "OwnerPolicy":
        raw = path.read_bytes()
        d = json.loads(raw)
        if d.get("mode") not in ALLOWED_MODES:
            raise PolicyError(f"mode {d.get('mode')!r} is not permitted in phase 5 (simulation only)")
        if d.get("long_only") is not True:
            raise PolicyError("phase 5 is long-only: shorting is not authorized")
        return cls(mode=d["mode"], long_only=True, decision_time=d["decision_time"], exit_by=d["exit_by"],
                   max_consecutive_model_failures=int(d["max_consecutive_model_failures"]),
                   max_consecutive_data_failures=int(d["max_consecutive_data_failures"]),
                   kill_switch_file=d["kill_switch_file"], sha256=hashlib.sha256(raw).hexdigest())

    @staticmethod
    def risk_settings() -> AppSettings:
        """A fresh copy of the app's default settings: the AI has no reference to it and cannot change it."""
        s = AppSettings()
        s.broker.mode = "paper"                 # the risk engine's non-live rules; nothing is ever sent to a broker
        s.automation.autopilot = True           # autonomous: no human approval per trade
        return s


def refuse_exposed_key(key_id: str | None) -> None:
    """Raise if a market-data or broker key is one that was exposed earlier."""
    if key_id and key_id.upper().startswith(EXPOSED_KEY_PREFIXES):
        raise PolicyError("refusing: this API key was exposed earlier and must be rotated before any use")
    if key_id and key_id.upper().startswith("AK"):
        raise PolicyError("refusing: that looks like a live-trading key")

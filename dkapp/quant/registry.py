"""Versioned statistical models with explicit stages, so every decision names the exact model behind it.

Stages (nothing is ever promoted automatically):
* experimental — exists, not used by the app
* shadow       — the app records its prediction for every candidate; it never changes a trade
* paper        — may gate PAPER entries, only if its model card passed the pre-registered checks AND the
                 owner turned gating on in Settings
* retired      — kept for the record / rollback, not used

There is deliberately no "live" stage: real-money use needs forward evidence, a code change and the
owner's explicit approval. Retraining writes a NEW version; the active one changes only via `activate`,
and `rollback` restores the previous one.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .model import QuantModel

STAGES = ("experimental", "shadow", "paper", "retired")
BUNDLED_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"


@dataclass
class ModelCard:
    id: str                       # e.g. "strategy-filter-v1"
    kind: str                     # "strategy_filter" (H5) or "dense30" (H4)
    feature_version: str
    stage: str
    trained_on: list[str]         # [first day, last day]
    evidence: dict = field(default_factory=dict)   # holdout / development verdicts with numbers
    decision_rule: str = "take only if expected net > 0 and P(win) > 0.5"
    paper_eligible: bool = False  # set only from a passing pre-registered evaluation
    fingerprint: str = ""
    created: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ModelRegistry:
    def __init__(self, root: Path, bundled: Path = BUNDLED_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.bundled = bundled
        self.index_path = self.root / "registry.json"
        self.index = self._load()
        self._seed_bundled()

    # ------------------------------------------------------------ storage
    def _load(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text())
            except ValueError:
                pass
        return {"models": {}, "active": {}, "history": []}

    def _save(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.index, indent=1))
        tmp.replace(self.index_path)

    def _seed_bundled(self) -> None:
        """Models shipped with the app are registered once (in their card's stage), never re-staged."""
        if not self.bundled.exists():
            return
        changed = False
        for p in sorted(self.bundled.glob("*.json")):
            try:
                raw = json.loads(p.read_text())
            except ValueError:
                continue
            card = raw.get("card")
            if not card or card["id"] in self.index["models"]:
                continue
            (self.root / p.name).write_text(p.read_text())
            self.index["models"][card["id"]] = {"card": card, "file": p.name}
            if card["stage"] in ("shadow", "paper") and card["kind"] not in self.index["active"]:
                self.index["active"][card["kind"]] = card["id"]
            changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------ queries
    def card(self, model_id: str) -> Optional[ModelCard]:
        m = self.index["models"].get(model_id)
        return ModelCard(**m["card"]) if m else None

    def active_card(self, kind: str) -> Optional[ModelCard]:
        mid = self.index["active"].get(kind)
        return self.card(mid) if mid else None

    def load(self, model_id: str) -> Optional[QuantModel]:
        m = self.index["models"].get(model_id)
        if not m:
            return None
        raw = json.loads((self.root / m["file"]).read_text())
        model = QuantModel.from_dict(raw["model"])
        if m["card"].get("fingerprint") and model.fingerprint() != m["card"]["fingerprint"]:
            raise ValueError(f"model file {m['file']} doesn't match its card (fingerprint changed)")
        return model

    def cards(self) -> list[ModelCard]:
        return [ModelCard(**m["card"]) for m in self.index["models"].values()]

    # ------------------------------------------------------------ changes (explicit only)
    def register(self, card: ModelCard, model: QuantModel) -> None:
        if card.id in self.index["models"]:
            raise ValueError(f"{card.id} already exists — a retrained model needs a new version id")
        if card.stage not in STAGES:
            raise ValueError(f"unknown stage {card.stage}")
        card.fingerprint = model.fingerprint()
        card.created = card.created or datetime.now(timezone.utc).isoformat()
        name = f"{card.id}.json"
        (self.root / name).write_text(json.dumps({"card": card.to_dict(), "model": model.to_dict()}))
        self.index["models"][card.id] = {"card": card.to_dict(), "file": name}
        self._save()

    def set_stage(self, model_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage}")
        m = self.index["models"][model_id]
        if stage == "paper" and not m["card"].get("paper_eligible"):
            raise PermissionError(f"{model_id} did not pass the pre-registered checks; it can't gate paper trades")
        m["card"]["stage"] = stage
        self._save()

    def activate(self, model_id: str) -> None:
        card = self.card(model_id)
        if card is None:
            raise KeyError(model_id)
        if card.stage not in ("shadow", "paper"):
            raise PermissionError(f"{model_id} is {card.stage}; only shadow or paper models can be active")
        prev = self.index["active"].get(card.kind)
        self.index["history"].append({"kind": card.kind, "from": prev, "to": model_id,
                                      "at": datetime.now(timezone.utc).isoformat()})
        self.index["active"][card.kind] = model_id
        self._save()

    def rollback(self, kind: str) -> Optional[str]:
        """Restore the model that was active before the last change (None = no model)."""
        for h in reversed(self.index["history"]):
            if h["kind"] == kind and h["to"] == self.index["active"].get(kind):
                self.index["history"].append({"kind": kind, "from": h["to"], "to": h["from"],
                                              "at": datetime.now(timezone.utc).isoformat(), "rollback": True})
                if h["from"]:
                    self.index["active"][kind] = h["from"]
                else:
                    self.index["active"].pop(kind, None)
                self._save()
                return h["from"]
        return self.index["active"].get(kind)

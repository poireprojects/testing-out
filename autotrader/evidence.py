"""Evidence: the only information a decision may use. Every item carries its source and the time it became
available; a pack refuses any item that was not available at the decision time (no look-ahead)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class LookAheadError(ValueError):
    """An evidence item became available after the decision time."""


@dataclass(frozen=True)
class Evidence:
    id: str                       # E1, E2, ...
    kind: str                     # filing | vendor_headline | financials | price | research
    source: str                   # human-readable provenance (accession number, data set, ...)
    available_at: datetime        # when it was public or computable (tz-aware)
    text: str
    url: Optional[str] = None
    redistributable: bool = True  # vendor text is used locally but never committed

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def record(self, redact: bool = False) -> dict:
        d = {"id": self.id, "kind": self.kind, "source": self.source, "available_at": self.available_at.isoformat(),
             "url": self.url, "sha256": self.sha256}
        d["text"] = self.text if (self.redistributable or not redact) else f"[vendor text not redistributed; sha256 {self.sha256[:16]}]"
        return d


@dataclass
class EvidencePack:
    event_id: str
    symbol: str
    sector_etf: str
    decision_time: datetime
    items: list[Evidence]
    # deterministic, source-derived fields the baselines use (never written by a model)
    structured: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        ids = [e.id for e in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence ids")
        for e in self.items:
            if e.available_at.tzinfo is None:
                raise ValueError(f"{e.id}: available_at must be timezone-aware")
            if e.available_at > self.decision_time:
                raise LookAheadError(f"{e.id} ({e.source}) became available at {e.available_at.isoformat()}, "
                                     f"after the decision time {self.decision_time.isoformat()}")

    def get(self, eid: str) -> Optional[Evidence]:
        return next((e for e in self.items if e.id == eid), None)

    @property
    def sha256(self) -> str:
        body = json.dumps({"event": self.event_id, "symbol": self.symbol, "t": self.decision_time.isoformat(),
                           "items": [[e.id, e.sha256, e.available_at.isoformat()] for e in self.items],
                           "structured": self.structured}, sort_keys=True, default=str)
        return hashlib.sha256(body.encode()).hexdigest()

    def record(self, redact: bool = False) -> dict:
        return {"event_id": self.event_id, "symbol": self.symbol, "sector_etf": self.sector_etf,
                "decision_time": self.decision_time.isoformat(), "sha256": self.sha256,
                "structured": self.structured, "items": [e.record(redact) for e in self.items]}

    def prompt_text(self) -> str:
        out = [f"EVENT {self.event_id}: {self.symbol} (sector ETF {self.sector_etf}). Decision time "
               f"{self.decision_time.strftime('%Y-%m-%d %H:%M %Z')}. Everything below was available by then."]
        for e in self.items:
            out.append(f"\n[{e.id}] {e.kind} | {e.source} | available {e.available_at.strftime('%Y-%m-%d %H:%M %Z')}\n{e.text}")
        return "\n".join(out)


def plain_text(html: str) -> str:
    """Filing HTML -> readable text (tags dropped, entities for common symbols decoded, whitespace collapsed)."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    for a, b in (("&nbsp;", " "), ("&#160;", " "), ("&amp;", "&"), ("&#36;", "$"), ("&#8217;", "'"),
                 ("&#8220;", '"'), ("&#8221;", '"'), ("&#8212;", "-"), ("&#8211;", "-"), ("&#x2019;", "'"),
                 ("&rsquo;", "'"), ("&ldquo;", '"'), ("&rdquo;", '"'), ("&mdash;", "-"), ("&ndash;", "-")):
        t = t.replace(a, b)
    t = re.sub(r"&#\d+;|&[a-z]+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()

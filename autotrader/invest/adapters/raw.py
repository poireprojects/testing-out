"""The raw store: saved source responses with provenance, verified before any parser reads them.

    RAW_DIR/MANIFEST.json  {"synthetic": bool, "files": {relpath: {"source_url", "retrieved_at", "sha256", "kind"}}}

A file whose bytes no longer match its recorded SHA-256 is refused (`RawError`): an altered raw input can never
reach a pack. Transports are injectable so a later collector can be tested offline; the only transport that can
serve data here is `OfflineTransport`, which reads this store. `RefusingTransport` stands in for any live source and
raises: network fetching is not activated in this version.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

KINDS = ("edgar_submissions", "edgar_header", "edgar_document", "edgar_companyfacts", "edgar_tickers",
         "nport_xml", "universe_review", "price_bars", "corporate_actions")


class RawError(RuntimeError):
    pass


class NetworkDisabled(RuntimeError):
    """A live fetch was attempted: collection is not activated (no network, no credentials)."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class RawFile:
    relpath: str
    source_url: str
    retrieved_at: str
    sha256: str
    kind: str
    data: bytes

    def provenance(self) -> dict:
        return {"raw_file": self.relpath, "source_url": self.source_url, "retrieved_at": self.retrieved_at,
                "raw_sha256": self.sha256}

    def json(self):
        return json.loads(self.data)

    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


class Transport(Protocol):
    def fetch(self, url: str) -> RawFile: ...


class RefusingTransport:
    """The default for anything live: refuses. No request leaves the machine."""

    def fetch(self, url: str) -> RawFile:
        raise NetworkDisabled(f"network collection is not activated in this version: refused {url}")


class RawStore:
    def __init__(self, d: Path):
        self.d = Path(d)
        man = self.d / "MANIFEST.json"
        if not man.exists():
            raise RawError(f"{man}: no raw manifest")
        self.manifest = json.loads(man.read_text())
        self.synthetic = bool(self.manifest.get("synthetic"))
        self.by_url: dict[str, str] = {}
        for rel, m in self.manifest["files"].items():
            if m.get("kind") not in KINDS:
                raise RawError(f"{rel}: unknown kind {m.get('kind')!r}")
            for k in ("source_url", "retrieved_at", "sha256"):
                if not m.get(k):
                    raise RawError(f"{rel}: missing {k}")
            if datetime.fromisoformat(m["retrieved_at"]).tzinfo is None:
                raise RawError(f"{rel}: retrieval time without a time zone")
            self.by_url[m["source_url"]] = rel

    def get(self, rel: str) -> RawFile:
        m = self.manifest["files"].get(rel)
        if m is None:
            raise RawError(f"{rel}: not in the raw manifest")
        data = (self.d / rel).read_bytes()
        if sha256(data) != m["sha256"]:
            raise RawError(f"{rel}: bytes do not match the recorded SHA-256 (altered raw input)")
        return RawFile(rel, m["source_url"], m["retrieved_at"], m["sha256"], m["kind"], data)

    def of_kind(self, kind: str) -> list[RawFile]:
        return [self.get(rel) for rel in sorted(self.manifest["files"]) if self.manifest["files"][rel]["kind"] == kind]

    def find(self, url: str) -> Optional[RawFile]:
        rel = self.by_url.get(url)
        return self.get(rel) if rel else None


class OfflineTransport:
    """Serves saved responses by URL from a verified raw store; a URL that was never saved is refused."""

    def __init__(self, store: RawStore):
        self.store = store

    def fetch(self, url: str) -> RawFile:
        f = self.store.find(url)
        if f is None:
            raise NetworkDisabled(f"not in the raw store, and network collection is not activated: {url}")
        return f


def write_raw_store(d: Path, files: dict[str, tuple[bytes, str, str, str]], synthetic: bool) -> str:
    """Write raw files {relpath: (bytes, source_url, retrieved_at, kind)} and their manifest; returns its SHA-256."""
    d = Path(d)
    man = {"synthetic": synthetic, "files": {}}
    for rel, (data, url, at, kind) in sorted(files.items()):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        man["files"][rel] = {"source_url": url, "retrieved_at": at, "sha256": sha256(data), "kind": kind}
    body = json.dumps(man, indent=1, sort_keys=True) + "\n"
    (d / "MANIFEST.json").write_text(body)
    return sha256(body.encode())

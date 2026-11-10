"""The fixed-cost development evaluation as one frozen experiment (research only; nothing here calls a model).

research/runs/2026-11-07-phase5/dev-eval/PLAN.md fixes the sample, the model, the contract, the token limits, the
spending ceiling and the controls. This module holds those values in one place, checks the sample before anything
else is built, and derives the experiment identity that the budget journal is bound to (autotrader/journal.py).

Proposed amendment 1 (dev-eval/AMENDMENT-1.md) is versioned here explicitly:
* evidence version 2: the retrospective research note is removed from every historical prompt and a fixed,
  neutral statement of the simulator's cost conventions is added to every prompt (same text on every date);
* execution version 2: proxy quotes are labelled, a missed 10:00:30 deadline means no fill, early exits and
  incomplete bars are handled explicitly, historical fees are used (autotrader/execution.py, fees_hist.py);
* the EPS tolerance is exactly $0.01 in decimal arithmetic (the evaluator used 0.011 in floats).
Nothing here changes the registered sample, model, contract, effort, token limits, ceiling or controls.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_EVAL = ROOT / "research" / "runs" / "2026-11-07-phase5" / "dev-eval"
SAMPLE_PATH = DEV_EVAL / "sample.json"

FROZEN: dict = {
    "experiment": "phase5-dev-eval",
    "registration": "research/runs/2026-11-07-phase5/dev-eval/PLAN.md",
    "amendment": "research/runs/2026-11-07-phase5/dev-eval/AMENDMENT-1.md (proposed; needs owner approval)",
    "sample": {"path": "research/runs/2026-11-07-phase5/dev-eval/sample.json",
               "sha256": "e825d6d63dc824801fb9ce4bae18e92af293ba50bae081469dbb211bf738f68b",
               "encoding": "sha256(json.dumps(events, sort_keys=True).encode()) over the ordered event list",
               "events": 160, "dates": 40, "per_date": 4, "per_fold": {"dev": 80, "eval": 80},
               "dev_fold_last_day": "2024-10-31"},
    "model": "claude-opus-5-5",
    "contract": "2",
    "effort": "low",
    "decisions_per_event": 1,
    "max_input_tokens": 4500,
    "max_output_tokens": 3000,
    "ceiling_micro_usd": 12_500_000,                   # $12.50 for the entire experiment
    "controls": ["no_trade", "price_only", "rule_p4h1"],
    "arms": ["llm", "no_trade", "price_only", "rule_p4h1"],
    "price_sheet": {
        "model": "claude-opus-5-5",
        "input_micro_usd_per_token": 4,                # $4 / MTok, base input
        "output_micro_usd_per_token": 20,              # $20 / MTok, output including all thinking
        "source": "platform.claude.com/docs/en/about-claude/pricing (read 2026-11-10)",
        "priced_categories": ["input_tokens", "output_tokens"],
        "refused_categories": ["cache writes and reads", "fast mode (speed=fast)", "US-only inference (1.1x)",
                               "priority or batch tier", "server tools", "refusal fallbacks to another model"],
    },
    "sdk": {"package": "anthropic", "version": "2.7.0", "api_version": "2026-10-21"},
    "endpoint": "https://api.anthropic.com/v1/messages",
    "evidence_version": "2",
    "execution_version": "2",
    "base_rate": 0.490,
    "base_rate_historical": 0.4904,
    "eps_tolerance": "0.01",
    "training_cutoff_last_day": "2026-06-30",          # Claude Opus 5.5: June 2026
    "entry": {"asof": "10:00:00", "entry": "10:00:30", "deadline": "10:00:30"},
    "ordering": "within a date: ascending vendor flash time, then symbol (the registered replay order); the same "
                "order is the model's request queue and the order of contested risk slots in every arm",
    "account": {"capital_usd": 100, "position_cap_usd": 20, "slots": 2, "daily_loss_limit_usd": 2,
                "long_only": True, "stops_pct": [0.4, 3.0]},
    "costs": {"slippage_bps_per_side": 5.0, "stop_first_on_ambiguity": True, "fees": "per decider per day per type, "
              "each daily total rounded up to a cent", "sensitivity": "2x spreads and 2x slippage"},
    "bootstrap": {"replicates": 10_000, "seed": 20261110, "level": 0.90, "unit": "whole dates, all arms together"},
}

# Files whose content defines the model input, the paid path and the execution. Any change gives a new identity,
# so a journal written under other code is refused on resume instead of being silently mixed.
IDENTITY_FILES = {
    "evidence": ["autotrader/evidence.py", "autotrader/sources.py", "autotrader/decision.py", "autotrader/deciders.py"],
    "paid_path": ["autotrader/paid.py", "autotrader/journal.py", "autotrader/experiment.py"],
    "execution": ["autotrader/devrun.py", "autotrader/execution.py", "autotrader/risk.py", "autotrader/fees_hist.py",
                  "autotrader/owner_policy.json", "stockpilot/risk/engine.py", "stockpilot/config.py"],
}

REQUIRED_KEYS = {"day", "symbol", "accession", "fold"}


class SampleError(ValueError):
    """The sample is not exactly the registered one, or does not match the data cache."""


def canonical_sha256(events: list[dict]) -> str:
    """The generator's encoding (autotrader/__main__.py cmd_sample): order-sensitive."""
    return hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()


def load_sample(path: Path = SAMPLE_PATH) -> dict:
    return verify_sample(json.loads(Path(path).read_text()))


def verify_sample(body: dict) -> dict:
    """Every structural property the plan states, checked before any data is read or any client is built."""
    spec = FROZEN["sample"]
    ev = body.get("events")
    if not isinstance(ev, list):
        raise SampleError("sample has no event list")
    h = canonical_sha256(ev)
    if h != spec["sha256"]:
        raise SampleError(f"sample digest {h} is not the registered {spec['sha256']}")
    if body.get("sha256") != spec["sha256"]:
        raise SampleError("the digest stored in the sample file is not the registered one")
    problems = []
    for i, e in enumerate(ev):
        if set(e) != REQUIRED_KEYS:
            problems.append(f"event {i}: keys {sorted(e)}")
        elif e["fold"] not in ("dev", "eval"):
            problems.append(f"event {i}: fold {e['fold']!r}")
        elif (e["fold"] == "dev") != (e["day"] <= spec["dev_fold_last_day"]):
            problems.append(f"event {i}: fold {e['fold']} does not match its day {e['day']}")
    keys = [(e["day"], e["symbol"], e["accession"], e["fold"]) for e in ev]
    if len(ev) != spec["events"]:
        problems.append(f"{len(ev)} events, not {spec['events']}")
    if len(set(keys)) != len(keys) or len({e["accession"] for e in ev}) != len(ev):
        problems.append("duplicate events or accessions")
    per_day = Counter(e["day"] for e in ev)
    if len(per_day) != spec["dates"] or set(per_day.values()) != {spec["per_date"]}:
        problems.append(f"{len(per_day)} dates with {sorted(set(per_day.values()))} events each")
    if dict(Counter(e["fold"] for e in ev)) != spec["per_fold"]:
        problems.append(f"folds {dict(Counter(e['fold'] for e in ev))}")
    if problems:
        raise SampleError("; ".join(problems[:5]))
    return body


def event_key(e: dict) -> tuple[str, str, str, str]:
    return (e["day"], e["symbol"], e["accession"], e["fold"])


def event_id(e: dict) -> str:
    return f"{e['symbol']}:{e['accession']}"


def select_rows(sample: dict, plan: list[dict]) -> list[dict]:
    """The plan rows of the sample, by exact equality of (date, ticker, accession, fold). A missing, duplicated or
    differently labelled row stops everything (the earlier replay matched ticker/accession only and silently
    dropped the rest). Returned in the frozen order."""
    want = {event_key(e) for e in sample["events"]}
    found: dict[tuple, list[dict]] = {}
    for r in plan:
        k = (r.get("day"), r.get("symbol"), r.get("accession"), r.get("fold"))
        if k in want:
            found.setdefault(k, []).append(r)
    missing = sorted(want - set(found))
    dup = sorted(k for k, v in found.items() if len(v) > 1)
    if missing or dup:
        raise SampleError(f"{len(missing)} sample event(s) missing from the cache plan"
                          + (f" (first: {missing[0]})" if missing else "")
                          + (f"; {len(dup)} matched more than one row" if dup else ""))
    rows = [found[k][0] for k in want]
    return frozen_order(rows)


def frozen_order(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["day"], r.get("flash") or "", r["symbol"]))


def post_cutoff(day: str) -> bool:
    return day > FROZEN["training_cutoff_last_day"]


def _sha_file(rel: str) -> str:
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing"


def canonical_json(x) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass(frozen=True)
class ExperimentIdentity:
    body: dict

    @property
    def experiment_id(self) -> str:
        return hashlib.sha256(canonical_json(self.body)).hexdigest()

    @classmethod
    def build(cls, sample: dict, prompt_sha256: str, schema_sha256: str) -> "ExperimentIdentity":
        return cls({
            "experiment": FROZEN["experiment"],
            "sample_sha256": canonical_sha256(sample["events"]),
            "config_sha256": hashlib.sha256(canonical_json(FROZEN)).hexdigest(),
            "prompt_sha256": prompt_sha256,                 # system prompt + fixed instructions
            "schema_sha256": schema_sha256,                 # the schema builder rendered on a fixed reference pack
            "evidence_version": FROZEN["evidence_version"],
            "execution_version": FROZEN["execution_version"],
            "price_sheet_sha256": hashlib.sha256(canonical_json(FROZEN["price_sheet"])).hexdigest(),
            "model": FROZEN["model"],
            "sdk": FROZEN["sdk"],
            "code": {k: {f: _sha_file(f) for f in v} for k, v in IDENTITY_FILES.items()},
        })


def fold_of(day: str) -> str:
    return "dev" if date.fromisoformat(day) <= date.fromisoformat(FROZEN["sample"]["dev_fold_last_day"]) else "eval"

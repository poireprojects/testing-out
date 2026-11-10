"""The fixed-cost development evaluation, end to end, in four phases that never overlap:

A  inputs     the 160 sample rows are matched exactly; every event's evidence pack (evidence version 2) and the
              exact request bytes are built, persisted and hashed before any decision or outcome exists. An event
              whose release or financial history is absent is `input_incomplete`: not sent, never price-only.
B  decisions  every arm decides every event once, in the frozen order. The model arm goes through the budget
              journal (one generation per event, never resent); a dry run records `not_run` for it.
C  outcomes   only now: market labels for all 160 events, then three executions of the same frozen decisions
              (executable with the 10:00:30 deadline, idealized fixed time, executable with 2x costs).
D  report     coverage, reconciliation, the registered gates and the descriptive economics (autotrader/devreport.py).

A rerun of the same output directory resumes: recorded decisions are kept, paid answers are read back from the
journal, and phases C and D are recomputed deterministically from the ledger.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Callable, Optional

from stockpilot.clock import ET
from stockpilot.data.universe import curated_universe

from . import labels
from .decision import no_trade
from .deciders import COST_CONVENTIONS, INSTRUCTION, SYSTEM_V2, LLMResearcherV2, baselines
from .evidence import Evidence, EvidencePack, LookAheadError
from .execution import RULE_CLOCK, VARIANTS, Clock, DevExecutor, ProxyQuotes, model_clocks
from .experiment import FROZEN, ExperimentIdentity, event_id, load_sample, select_rows
from .ledger import Ledger
from .paid import PaidCaller, PaidPathRefused, budget_proof, render_request, request_params
from .policy import OwnerPolicy
from .providers import api_schema
from .risk import RiskController
from .sources import CachedEdgar, InputIncomplete, NotCached, ReplaySource

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"]
ASOF = dtime(10, 0)


class RunIncomplete(RuntimeError):
    pass


def reference_pack() -> EvidencePack:
    t = datetime(2026, 1, 2, 10, 0, tzinfo=ET)
    return EvidencePack("REF:0000000000-00-000000", "REF", "XLK", t,
                        [Evidence("E1", "filing", "reference", t, "Reference Corp reports results. Diluted EPS was $1.00."),
                         Evidence("E2", "price", "reference", t, "REF prior close $10.00.")], {})


def identity(sample: dict) -> ExperimentIdentity:
    prompt = hashlib.sha256((SYSTEM_V2 + "\n" + COST_CONVENTIONS + "\n" + INSTRUCTION).encode()).hexdigest()
    rp = reference_pack()
    r = LLMResearcherV2(None, cost_note=COST_CONVENTIONS)
    _, _, schema, _ = r.request(rp)
    sch = hashlib.sha256(json.dumps(api_schema(schema), sort_keys=True).encode()).hexdigest()
    return ExperimentIdentity.build(sample, prompt, sch)


def _write_json(path: Path, obj) -> str:
    data = json.dumps(obj, indent=1, sort_keys=True, default=str)
    path.write_text(data)
    return hashlib.sha256(data.encode()).hexdigest()


# ------------------------------------------------------------------ phase A
def build_inputs(data_dir: Path, rows: list[dict]) -> tuple[dict, dict, dict]:
    """(model packs or None, control packs, manifest)."""
    full = ReplaySource(Path(data_dir), CachedEdgar(Path(data_dir) / "sec_cache"))
    ctl = ReplaySource(Path(data_dir), None)
    researcher = LLMResearcherV2(None, cost_note=COST_CONVENTIONS)
    model_packs, control_packs, manifest = {}, {}, {}
    for r in rows:
        eid = event_id(r)
        cp = ctl.pack(r, evidence_version="2")
        cp.structured["session"] = r["day"]
        control_packs[eid] = cp
        entry = {"day": r["day"], "fold": r["fold"], "control_pack_sha256": cp.sha256}
        try:
            mp = full.pack(r, evidence_version="2", require_release=True)
            mp.structured["session"] = r["day"]
            system, user, schema, _ = researcher.request(mp)
            model_packs[eid] = mp
            entry |= {"input_status": "complete", "model_pack_sha256": mp.sha256,
                      "prompt_sha256": hashlib.sha256((system + user).encode()).hexdigest(),
                      "items": [i.kind for i in mp.items]}
            try:
                body, _ = render_request(request_params(system, user, schema))
                entry |= {"request_sha256": hashlib.sha256(body).hexdigest(), "request_bytes": len(body)}
            except PaidPathRefused as exc:          # e.g. another SDK release: the exact bytes are unknown
                entry["request_not_rendered"] = str(exc)[:200]
        except (InputIncomplete, NotCached, LookAheadError, KeyError, FileNotFoundError) as exc:
            model_packs[eid] = None
            entry |= {"input_status": "incomplete", "why": f"{type(exc).__name__}: {str(exc)[:200]}"}
        manifest[eid] = entry
    return model_packs, control_packs, manifest


# ------------------------------------------------------------------ phase B
def _not_sent(name: str, why: str, skip: str) -> dict:
    return no_trade(name, why, invalid=True, problems=[why], answered=False, skip=skip)


def _held_decision(res) -> dict:
    return no_trade("llm", f"model failure ({res.outcome}): no trade", invalid=True, problems=[res.outcome],
                    failure=True, answered=False, journal={"state": res.state, "outcome": res.outcome,
                                                          "charge_micro": res.charge_micro,
                                                          "request_id": res.provider_request_id})


def decide_all(led: Ledger, rows: list[dict], model_packs: dict, control_packs: dict, caller: Optional[PaidCaller],
               policy: OwnerPolicy) -> None:
    controls = baselines()
    researcher = LLMResearcherV2(caller, cost_note=COST_CONVENTIONS) if caller is not None else None
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    for day, day_rows in by_day.items():
        if not any((event_id(r), "llm") not in led.decisions for r in day_rows):
            continue
        led.append("cycle", {"label": f"dev-eval session {day}", "policy_sha256": policy.sha256,
                             "deciders": {d.name: d.version for d in controls} | {"llm": "2"},
                             "models": {"llm": FROZEN["model"] if caller else "not run"}})
        fails, queue, acc_ms = 0, 0, 0
        for r in day_rows:
            eid = event_id(r)
            mp, cp = model_packs.get(eid), control_packs[eid]
            pack = mp or cp
            if not any(rec["type"] == "evidence" and rec["payload"]["event_id"] == eid for rec in led.records):
                led.append("evidence", pack.record(redact=True))
            for c in controls:
                if (eid, c.name) not in led.decisions:
                    d = c.decide(cp) | {"session": day, "clock": {"ready_offset_ms": 0}}
                    led.record_decision(eid, c.name, cp.sha256, cp.decision_time, d)
            if (eid, "llm") in led.decisions:
                prev = led.decisions[(eid, "llm")]["payload"]["decision"].get("clock") or {}
                if prev.get("own_latency_ms") is not None:
                    queue, acc_ms = queue + 1, acc_ms + prev["own_latency_ms"]
                fails = fails + 1 if led.decisions[(eid, "llm")]["payload"]["decision"].get("failure") else 0
                continue
            clock = {"queue_position": None, "own_latency_ms": None, "ready_offset_ms": None}
            if researcher is None:
                d = _not_sent("llm", "not run: no model in this run (dry run)", "not_run")
            elif mp is None:
                d = _not_sent("llm", "input incomplete: the release or financial history is absent; not sent", "input_incomplete")
            elif fails >= policy.max_consecutive_model_failures:
                d = _not_sent("llm", "model switched off for the rest of the day after consecutive failures", "circuit_breaker")
            else:
                system, user, schema, sents = researcher.request(mp)
                res = caller.call(eid, system, user, schema)
                queue += 1
                if res.latency_ms is not None:
                    acc_ms += res.latency_ms
                clock = {"queue_position": queue, "own_latency_ms": res.latency_ms,
                         "ready_offset_ms": acc_ms if res.latency_ms is not None else None}
                if res.state == "settled":
                    d = researcher.interpret(mp, user, sents, res.text, {"latency_ms": res.latency_ms,
                                                                         "charge_micro": res.charge_micro})
                    d |= {"answered": True, "journal": {"state": res.state, "outcome": res.outcome,
                                                        "charge_micro": res.charge_micro,
                                                        "request_id": res.provider_request_id,
                                                        "resumed": res.resumed}}
                    fails = 0
                else:
                    d = _held_decision(res)
                    fails += 1
            d |= {"session": day, "clock": clock}
            led.record_decision(eid, "llm", pack.sha256, pack.decision_time, d)


# ------------------------------------------------------------------ phase C
def outcomes(led: Ledger, data_dir: Path, rows: list[dict], model_packs: dict, control_packs: dict) -> dict:
    src = ReplaySource(Path(data_dir), None)
    quotes = ProxyQuotes(src.prices)
    have = {m["payload"]["event_id"] for m in led.of_type("market_outcome")}
    market = {}
    for r in rows:
        eid = event_id(r)
        lab = labels.market_label(quotes, r["symbol"], date.fromisoformat(r["day"]))
        market[eid] = lab
        if lab is not None and eid not in have:
            led.record_market_outcome(eid, r["day"], r["symbol"], lab["entry_mid"], lab["exit_mid"],
                                      datetime.fromisoformat(lab["data_from"]))
    policy = OwnerPolicy.load()
    universe = set(curated_universe()) | set(SECTOR_ETFS) | {r["symbol"] for r in rows}
    decs = {(r["payload"]["event_id"], r["payload"]["decider"]): r for r in led.of_type("decision")}
    results = {}
    for v in VARIANTS:
        ex = DevExecutor(RiskController(policy, universe), src.prices, quotes, v)
        disp = []
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            by_day.setdefault(r["day"], []).append(r)
        daily_equity = {}
        for day, day_rows in by_day.items():
            entries = []
            for arm in FROZEN["arms"]:
                for r in day_rows:
                    eid = event_id(r)
                    rec = decs.get((eid, arm))
                    if rec is None:
                        continue
                    c = rec["payload"]["decision"].get("clock") or {}
                    clock = RULE_CLOCK if arm != "llm" else Clock(c.get("queue_position"), c.get("own_latency_ms"),
                                                                  c.get("ready_offset_ms"))
                    entries.append((rec, model_packs.get(eid) or control_packs[eid], clock))
            disp += ex.execute_day(date.fromisoformat(day), entries)
            daily_equity[day] = ex.equity()
        results[v.name] = {"variant": v.__dict__, "dispositions": [d.__dict__ for d in disp], "equity": daily_equity}
    return {"market": market, "executions": results, "eps_labels": eps_labels(data_dir, rows)}


def eps_labels(data_dir: Path, rows: list[dict]) -> dict:
    """Checkability and labels from the cached company facts only (computed after all decisions; never shown to a
    decider). A missing companyfacts file makes the event uncheckable, it is not fetched."""
    d = Path(data_dir)
    companies = json.loads((d / "companies.json").read_text())
    acc = {e["accession"]: e for e in json.loads((d / "events.json").read_text())}
    edgar = CachedEdgar(d / "sec_cache")
    out = {}
    for r in rows:
        eid = event_id(r)
        ev = acc.get(r["accession"])
        if ev is None:
            out[eid] = {"checkable": False, "reason": "no SEC acceptance record for the release"}
            continue
        release_day = datetime.fromisoformat(ev["acceptance"]).astimezone(ET).date()
        cik = int(companies[r["symbol"]]["cik"])
        try:
            facts = json.loads(edgar.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
        except NotCached:
            out[eid] = {"checkable": False, "reason": "company facts not in the cache"}
            continue
        out[eid] = labels.eps_label(facts, release_day) | {"release_day": release_day.isoformat()}
    return out


# ------------------------------------------------------------------ the run
def prepare(data_dir: Path, out_dir: Path) -> dict:
    """Phase A only: match the sample exactly, build, persist and hash every input, and compute the budget proof.
    Reads no outcome. The run command calls this before any paid client exists."""
    sample = load_sample()
    plan = json.loads((Path(data_dir) / "plan.json").read_text())
    rows = select_rows(sample, plan)                         # fail closed before anything else, paid or not
    out = Path(out_dir)
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    ident = identity(sample)
    model_packs, control_packs, manifest = build_inputs(data_dir, rows)
    bodies = {}
    for i, r in enumerate(rows):
        eid = event_id(r)
        mp = model_packs[eid]
        rec = {"event_id": eid, "control_pack": control_packs[eid].record(redact=True),
               "model_pack": mp.record(redact=True) if mp is not None else None}
        if mp is not None and "request_sha256" in manifest[eid]:
            system, user, schema, _ = LLMResearcherV2(None, cost_note=COST_CONVENTIONS).request(mp)
            bodies[eid] = render_request(request_params(system, user, schema))[0]
            rec["request_sha256"] = hashlib.sha256(bodies[eid]).hexdigest()
        manifest[eid]["file_sha256"] = _write_json(out / "inputs" / f"{i:03d}.json", rec)
    proof = budget_proof(bodies)
    inputs_sha = _write_json(out / "inputs_manifest.json", {"experiment_id": ident.experiment_id,
                                                            "identity": ident.body, "events": manifest})
    _write_json(out / "budget_proof.json", proof.record())
    return {"rows": rows, "identity": ident, "model_packs": model_packs, "control_packs": control_packs,
            "manifest": manifest, "proof": proof, "inputs_sha256": inputs_sha}


def run(data_dir: Path, out_dir: Path, caller_factory: Optional[Callable] = None, *,
        require_paid_proof: bool = False) -> dict:
    """Dry run when `caller_factory` is None (the model arm is `not_run`). A paid run passes a factory that is
    called only after the inputs are fixed and the budget proof holds (`require_paid_proof`)."""
    from .devreport import build_report
    out = Path(out_dir)
    prep = prepare(data_dir, out)
    rows, ident, model_packs, control_packs = prep["rows"], prep["identity"], prep["model_packs"], prep["control_packs"]
    manifest, proof, inputs_sha = prep["manifest"], prep["proof"], prep["inputs_sha256"]
    if caller_factory is not None and require_paid_proof and not proof.proven:
        raise RunIncomplete("BLOCKED: the budget proof does not hold: " + "; ".join(proof.reasons))
    caller = caller_factory(ident, manifest) if caller_factory is not None else None
    led = Ledger(out / "ledger.jsonl", "replay")
    opened = [r for r in led.of_type("experiment")]
    if opened and opened[0]["payload"]["experiment_id"] != ident.experiment_id:
        raise RunIncomplete("this ledger belongs to another experiment identity")
    if not opened:
        led.append("experiment", {"experiment_id": ident.experiment_id, "inputs_manifest_sha256": inputs_sha,
                                  "model": FROZEN["model"] if caller else "not run", "dry_run": caller is None})
    decide_all(led, rows, model_packs, control_packs, caller, OwnerPolicy.load())
    res = outcomes(led, data_dir, rows, model_packs, control_packs)
    res["synthetic_data"] = (Path(data_dir) / "SYNTHETIC").exists()
    exec_sha = _write_json(out / "executions.json", res)
    journal = caller.j.summary() if caller is not None else None
    _write_json(out / "journal_summary.json", journal)
    report = build_report(out, rows=rows, manifest=manifest, ledger=led, results=res, journal=journal, proof=proof)
    led.append("report", {"executions_sha256": exec_sha, "complete": report["reconciliation"]["complete"],
                          "status": report["status"]})
    return report

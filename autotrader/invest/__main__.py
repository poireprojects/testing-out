"""Command line for the investing pilot (research only; nothing here sends a request or places an order).

    python -m autotrader.invest dryrun OUT_DIR                 # end to end on FIXTURES: raw files -> pipeline ->
                                                               # data -> fixture decisions -> ledger -> report
    python -m autotrader.invest build-data RAW_DIR DATA_DIR --freeze ISO --series-id S######### [--series NAME]
    python -m autotrader.invest trial-pack DATA_DIR OUT_DIR --first-fill-month YYYY-MM --slot N
                                                               # input inspection: pack + exact request + digest
    python -m autotrader.invest budget OUT_DIR                 # maximum-size request: bytes, token estimates, budget
    python -m autotrader.invest real-check OUT.json CACHE_DIR... [--raw-index DIR]  # parsers on real saved files
    python -m autotrader.invest report STATE_DIR
    python -m autotrader.invest verify STATE_DIR
    python -m autotrader.invest request STATE_DIR SLOT         # the exact request for a frozen pack (offline)
    python -m autotrader.invest reconcile STATE_DIR SLOT USD EVIDENCE
    python -m autotrader.invest review-packet STATE_DIR OUT.json
    python -m autotrader.invest forward ...                    # refuses: proposed, not approved
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import pipeline, synthetic, synthetic_raw
from .accounting import Book
from .budget import max_size_budget
from .claims import packet
from .data import MarketData
from .engine import FixtureDecider, ModelDecider, reconcile, run
from .ledger import read_records
from .pack import build_pack, pack_from_record
from .registration import CAPITAL, FROZEN, canonical_json, code_sha256, config_sha256, schedule
from .report import write_report


def render(pack, symbols) -> tuple[dict, bytes, str]:
    params = ModelDecider(symbols).request(pack)
    body = canonical_json(params)
    return params, body, hashlib.sha256(body).hexdigest()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_dryrun(a) -> int:
    """FIXTURES end to end: raw files -> pipeline -> data -> fixture decisions -> ledger -> report -> the exact request
    for slot 1. Writes summary.json with every hash; nothing is sent, bought or downloaded."""
    out = Path(a.out_dir)
    if (out / "ledger.jsonl").exists() and not a.resume:
        print(f"{out}/ledger.jsonl exists: pass --resume to rerun into it (nothing is duplicated)", file=sys.stderr)
        return 2
    raw, data = out / "raw", out / "data"
    if not (raw / "MANIFEST.json").exists():
        synthetic_raw.build_raw(raw)
    built = pipeline.build_data(raw, data, freeze=synthetic_raw.FREEZE, series_id=synthetic_raw.SERIES_ID,
                                series_name=synthetic_raw.SERIES)
    dec = FixtureDecider(synthetic.FIXTURE_SCRIPT, synthetic.FIXTURE_COST_USD)
    res = run(out, data, dec, mode="dry_run", first_fill_month=synthetic.FIRST_FILL_MONTH, now=synthetic.end_of_pilot())
    rep = write_report(out)
    rec = json.loads((out / "packs" / "slot-01.json").read_text())
    params, body, digest = render(pack_from_record(rec), [c["symbol"] for c in rec["candidates"]])
    (out / "request-slot-01.json").write_text(json.dumps(params, indent=1, sort_keys=True) + "\n")
    for name, src in (("raw-MANIFEST.json", raw / "MANIFEST.json"), ("data-MANIFEST.json", data / "MANIFEST.json")):
        (out / name).write_bytes(src.read_bytes())
    fc = rep["diagnostics"]["forecasts"]
    summary = {
        "what": "offline end-to-end demonstration of INVEST-PILOT-1 with the proposed amendment 1: synthetic RAW source "
                "files -> offline pipeline -> data -> scripted FIXTURE decisions -> ledger -> report -> rendered "
                "request; simulated 2031 calendar; NOT A RESULT",
        "command": "python -m autotrader.invest dryrun OUT_DIR",
        "config_sha256": config_sha256(), "code_sha256": code_sha256(),
        "raw_manifest_sha256": _sha(raw / "MANIFEST.json"), "data_manifest_sha256": built["data_manifest_sha256"],
        "ledger_sha256": _sha(out / "ledger.jsonl"), "records": res["records"], "appended_this_run": res["appended"],
        "packs_sha256": {p.name: _sha(p) for p in sorted((out / "packs").glob("*.json"))},
        "report_json_sha256": _sha(out / "report.json"), "report_md_sha256": _sha(out / "REPORT.md"),
        "request_slot_01": {"bytes": len(body), "sha256": digest, "sent": False},
        "verdict": rep["verdict"], "complete": rep["complete"],
        "decisions": {"fixture": rep["decisions_fixture"], "genuine": rep["decisions_genuine"],
                      "status_counts": rep["decision_status_counts"]},
        "forecasts": {k: fc[k] for k in ("scored", "pending", "missing")},
        "shadow_ending_wealth": rep["diagnostics"]["shadow"]["ending_wealth"],
        "voo_end": rep.get("voo_end"), "ai_end_before_model": rep.get("ai_end_before_model"),
        "ai_end_all_in": rep.get("ai_end_all_in"),
        "model_calls": 0, "network": "none", "spend_usd": "0",
        "not_committed": "raw/, data/ and packs/ (regenerated byte for byte by the command; manifests and pack hashes "
                         "here)"}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: summary[k] for k in ("raw_manifest_sha256", "data_manifest_sha256", "ledger_sha256", "records",
                                              "appended_this_run", "config_sha256", "verdict", "request_slot_01",
                                              "forecasts")}, indent=1, default=str))
    return 0


def cmd_build_data(a) -> int:
    r = pipeline.build_data(Path(a.raw_dir), Path(a.data_dir), freeze=datetime.fromisoformat(a.freeze),
                            series_id=a.series_id, series_name=a.series or "")
    print(json.dumps(r, indent=1, default=str))
    return 0


def cmd_trial_pack(a) -> int:
    """A trial evidence pack: an inspection of the inputs a month would show the model. Not a backtest, not a decision."""
    data, out = MarketData(Path(a.data_dir)), Path(a.out_dir)
    slot = schedule(a.first_fill_month)[int(a.slot) - 1]
    pack = build_pack(slot, Book("ai", CAPITAL), data, slot.cutoff.close + timedelta(minutes=20), [])
    params, body, digest = render(pack, [m["symbol"] for m in data.members()])
    out.mkdir(parents=True, exist_ok=True)
    (out / "TRIAL-PACK.txt").write_text("TRIAL EVIDENCE PACK: an inspection of inputs. Not a backtest, not a decision, "
                                        "not sent anywhere.\n")
    (out / "pack.json").write_text(json.dumps(pack.record(), indent=1, sort_keys=True) + "\n")
    (out / "request.json").write_text(json.dumps(params, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pack_sha256": pack.sha256, "request_bytes": len(body), "request_sha256": digest,
                      "coverage": {c["status"]: sum(1 for x in pack.coverage if x["status"] == c["status"])
                                   for c in pack.coverage}, "sent": False}, indent=1))
    return 0


def cmd_budget(a) -> int:
    b = max_size_budget()
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    Path(a.out_dir, "budget.json").write_text(json.dumps(b, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: b[k] for k in ("request_bytes", "request_sha256", "paid_dispatch", "missing_guarantee")},
                     indent=1, default=str))
    return 0


def cmd_real_check(a) -> int:
    from .real_check import check, check_raw_index
    r = check([Path(x) for x in a.cache_dirs])
    if a.raw_index:
        r["raw_index"] = [check_raw_index(Path(x)) for x in a.raw_index]
    Path(a.out).write_text(json.dumps(r, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps(r["totals"] | ({"raw_index": [x["totals"] for x in r["raw_index"]]} if a.raw_index else {}),
                     indent=1))
    return 0


def cmd_report(a) -> int:
    rep = write_report(Path(a.state_dir))
    print(rep["verdict"])
    return 0


def cmd_verify(a) -> int:
    recs = read_records(Path(a.state_dir) / "ledger.jsonl")
    print(f"ok: {len(recs)} records, hash chain and keys verified")
    return 0


def cmd_request(a) -> int:
    rec = json.loads((Path(a.state_dir) / "packs" / f"slot-{int(a.slot):02d}.json").read_text())
    params, body, digest = render(pack_from_record(rec), [c["symbol"] for c in rec["candidates"]])
    print(json.dumps(params, indent=1))
    print(f"\n# {len(body):,} bytes, SHA-256 {digest}; nothing was sent. Model {FROZEN['model']['id']}, effort "
          f"{FROZEN['model']['effort']}, max_tokens {FROZEN['model']['max_tokens']}.", file=sys.stderr)
    return 0


def cmd_reconcile(a) -> int:
    rec = reconcile(Path(a.state_dir), int(a.slot), a.usd, a.evidence, datetime.now(timezone.utc))
    print(f"recorded {rec['key']}: {rec['payload']['usd']} USD")
    return 0


def cmd_review_packet(a) -> int:
    pk = packet(Path(a.state_dir))
    Path(a.out).write_text(json.dumps(pk, indent=1, sort_keys=True) + "\n")
    print(f"{len(pk['items'])} claim(s) written to {a.out} (blinded: no action, weight, later price or outcome)")
    return 0


def cmd_forward(a) -> int:
    print("refusing: the investing pilot's registration is proposed, not approved, and the forward clock has not "
          "started (research/runs/2026-11-11-invest-pilot/LAUNCH-MANIFEST.md)", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autotrader.invest")
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("dryrun")
    d.add_argument("out_dir")
    d.add_argument("--resume", action="store_true")
    d.set_defaults(fn=cmd_dryrun)
    b = sp.add_parser("build-data")
    b.add_argument("raw_dir")
    b.add_argument("data_dir")
    b.add_argument("--freeze", required=True)
    b.add_argument("--series-id", required=True)
    b.add_argument("--series", default="")
    b.set_defaults(fn=cmd_build_data)
    t = sp.add_parser("trial-pack")
    t.add_argument("data_dir")
    t.add_argument("out_dir")
    t.add_argument("--first-fill-month", required=True)
    t.add_argument("--slot", default="1")
    t.set_defaults(fn=cmd_trial_pack)
    g = sp.add_parser("budget")
    g.add_argument("out_dir")
    g.set_defaults(fn=cmd_budget)
    rc = sp.add_parser("real-check")
    rc.add_argument("out")
    rc.add_argument("cache_dirs", nargs="+")
    rc.add_argument("--raw-index", action="append", default=[], help="a saved raw directory with index.jsonl")
    rc.set_defaults(fn=cmd_real_check)
    r = sp.add_parser("report")
    r.add_argument("state_dir")
    r.set_defaults(fn=cmd_report)
    v = sp.add_parser("verify")
    v.add_argument("state_dir")
    v.set_defaults(fn=cmd_verify)
    q = sp.add_parser("request")
    q.add_argument("state_dir")
    q.add_argument("slot")
    q.set_defaults(fn=cmd_request)
    c = sp.add_parser("reconcile")
    for x in ("state_dir", "slot", "usd", "evidence"):
        c.add_argument(x)
    c.set_defaults(fn=cmd_reconcile)
    rp = sp.add_parser("review-packet")
    rp.add_argument("state_dir")
    rp.add_argument("out")
    rp.set_defaults(fn=cmd_review_packet)
    f = sp.add_parser("forward")
    f.add_argument("rest", nargs="*")
    f.set_defaults(fn=cmd_forward)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""Command line for the investing pilot (research only; nothing here sends a request or places an order).

    python -m autotrader.invest dryrun OUT_DIR           # synthetic data + fixture decisions, simulated 2031 calendar
    python -m autotrader.invest report STATE_DIR         # rebuild REPORT.md and report.json from the ledger alone
    python -m autotrader.invest verify STATE_DIR         # check the ledger's hash chain and keys
    python -m autotrader.invest request STATE_DIR SLOT   # print the exact model request a genuine decision would
                                                         # send for a frozen pack (offline; nothing is sent)
    python -m autotrader.invest reconcile STATE_DIR SLOT USD EVIDENCE   # resolve an unreconciled charge (append-only)
    python -m autotrader.invest review-packet STATE_DIR OUT.json        # blinded claim-review packet
    python -m autotrader.invest forward ...              # refuses: the registration is proposed, not approved
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from datetime import timezone

from . import synthetic
from .claims import packet
from .engine import FixtureDecider, ModelDecider, reconcile, run
from .ledger import read_records
from .pack import pack_from_record
from .registration import FROZEN, config_sha256
from .report import write_report


def cmd_dryrun(a) -> int:
    out = Path(a.out_dir)
    if (out / "ledger.jsonl").exists() and not a.resume:
        print(f"{out}/ledger.jsonl exists: pass --resume to rerun into it (nothing is duplicated)", file=sys.stderr)
        return 2
    data = out / "data"
    if not (data / "MANIFEST.json").exists():
        synthetic.build(data)
    dec = FixtureDecider(synthetic.FIXTURE_SCRIPT, synthetic.FIXTURE_COST_USD)
    res = run(out, data, dec, mode="dry_run", first_fill_month=synthetic.FIRST_FILL_MONTH, now=synthetic.end_of_pilot())
    rep = write_report(out)
    sha = hashlib.sha256((out / "ledger.jsonl").read_bytes()).hexdigest()
    print(json.dumps({"ledger_sha256": sha, "records": res["records"], "appended_this_run": res["appended"],
                      "config_sha256": config_sha256(), "verdict": rep["verdict"],
                      "decisions": {"fixture": rep["decisions_fixture"], "genuine": rep["decisions_genuine"]},
                      "voo_end": rep.get("voo_end"), "ai_end_before_model": rep.get("ai_end_before_model"),
                      "ai_end_all_in": rep.get("ai_end_all_in")}, indent=1))
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
    pack = pack_from_record(rec)
    params = ModelDecider([c["symbol"] for c in rec["candidates"]]).request(pack)
    body = json.dumps(params, sort_keys=True).encode()
    print(json.dumps(params, indent=1))
    print(f"\n# {len(body):,} bytes; nothing was sent. Model {FROZEN['model']['id']}, effort "
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
          "started (research/runs/2026-11-11-invest-pilot/REGISTRATION.md, Approvals)", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autotrader.invest")
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("dryrun")
    d.add_argument("out_dir")
    d.add_argument("--resume", action="store_true")
    d.set_defaults(fn=cmd_dryrun)
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

"""Command line for the autonomous research prototype (simulation only).

    python -m autotrader replay DATA_DIR OUT_DIR [--start D --end D --last N] [--llm ollama] [--vendor-news]
    python -m autotrader forward OUT_DIR [--llm ollama]          # observe live SEC filings, decide, record
    python -m autotrader daemon OUT_DIR [--llm ollama]           # the same, unattended, until the kill switch
    python -m autotrader report LEDGER [--day YYYY-MM-DD]
    python -m autotrader verify LEDGER

SEC text needs a declared contact (SEC fair access): SEC_USER_AGENT="name email". It is never stored.
--llm ollama uses a local model through Ollama's API (OLLAMA_URL, OLLAMA_MODEL); no paid service is called.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dkapp.data.universe import curated_universe

from .deciders import LLMResearcher, NoTrade, baselines
from .evaluate import base_rate, evaluate
from .ledger import Ledger, LedgerError
from .policy import OwnerPolicy
from .report import daily_report
from .risk import RiskController
from .runner import Autonomy

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"]


def _provider(a):
    if a.llm != "ollama":
        return None
    from dkapp.ai.providers import OllamaProvider
    return OllamaProvider(os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
                          os.environ.get("OLLAMA_MODEL", "llama3.2:3b"), timeout=float(os.environ.get("OLLAMA_TIMEOUT", "900")))


def _edgar(cache: Path, live: bool = False):
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        return None
    from .sources import Edgar, LiveEdgar
    return (LiveEdgar if live else Edgar)(ua, cache)


def cmd_replay(a) -> int:
    from .sources import ReplaySource
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    policy = OwnerPolicy.load()
    src = ReplaySource(Path(a.data), _edgar(Path(a.data) / "sec_cache"))
    rows = src.select(a.start, a.end, a.last)
    universe = set(curated_universe()) | set(SECTOR_ETFS) | {r["symbol"] for r in rows}
    deciders = baselines()
    prov = _provider(a)
    if prov is not None:
        deciders.append(LLMResearcher(prov))
    led = Ledger(out / "ledger.jsonl", "replay")
    auto = Autonomy(led, policy, deciders, RiskController(policy, universe, out), src.prices)
    build = src.pack if a.vendor_news else (lambda r: _without_vendor(src.pack(r)))
    days = sorted({r["day"] for r in rows})
    for d in days:
        auto.run_session(date.fromisoformat(d), [r for r in rows if r["day"] == d], build)
        print(f"{d}: done", flush=True)
    res = evaluate(led, src, src.edgar, base_rate(src, "dev"))
    res["events"] = len(rows)
    res["window"] = [a.start, a.end, a.last]
    (out / "results.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "REPORT-daily.md").write_text(daily_report(led))
    print(json.dumps(res, indent=1, default=str))
    return 0


def _without_vendor(pack):
    """Drop vendor headlines (not redistributable) and renumber the remaining items E1..En."""
    from dataclasses import replace
    from .evidence import EvidencePack
    items = [e for e in pack.items if e.kind != "vendor_headline"]
    items = [replace(e, id=f"E{i + 1}") for i, e in enumerate(items)]
    return EvidencePack(pack.event_id, pack.symbol, pack.sector_etf, pack.decision_time, items, pack.structured)


def _forward_setup(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    edgar = _edgar(out / "sec_cache", live=True)
    if edgar is None:
        raise SystemExit("SEC_USER_AGENT is required (SEC fair access: declare a contact)")
    from .sources import EdgarObserver
    policy = OwnerPolicy.load()
    uni = curated_universe()
    obs = EdgarObserver(edgar, uni)
    deciders = [NoTrade()]
    prov = _provider(a)
    if prov is not None:
        deciders.append(LLMResearcher(prov))
    led = Ledger(out / "ledger.jsonl", "forward")
    auto = Autonomy(led, policy, deciders, RiskController(policy, set(uni) | set(SECTOR_ETFS), out), prices=None)
    return auto, obs, led, out


def cmd_forward(a) -> int:
    auto, obs, led, out = _forward_setup(a)
    n = auto.forward_cycle(obs)
    (out / "REPORT-daily.md").write_text(daily_report(led))
    print(f"forward cycle: {n} new filing(s) decided; ledger {led.path}")
    return 0


def cmd_daemon(a) -> int:
    auto, obs, led, out = _forward_setup(a)
    auto.run_forever(obs, poll_seconds=a.poll)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autotrader")
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("replay")
    r.add_argument("data")
    r.add_argument("out")
    r.add_argument("--start", default="2026-10-01")
    r.add_argument("--end", default="2026-10-30")
    r.add_argument("--last", type=int, default=None)
    r.add_argument("--llm", choices=["none", "ollama"], default="none")
    r.add_argument("--vendor-news", action="store_true")
    for name in ("forward", "daemon"):
        f = sp.add_parser(name)
        f.add_argument("out")
        f.add_argument("--llm", choices=["none", "ollama"], default="none")
        f.add_argument("--poll", type=int, default=300)
    rp = sp.add_parser("report")
    rp.add_argument("ledger")
    rp.add_argument("--day")
    v = sp.add_parser("verify")
    v.add_argument("ledger")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "replay":
            return cmd_replay(a)
        if a.cmd == "forward":
            return cmd_forward(a)
        if a.cmd == "daemon":
            return cmd_daemon(a)
        mode = json.loads(Path(a.ledger).read_text().splitlines()[0])["payload"]["mode"]
        led = Ledger(Path(a.ledger), mode)
        if a.cmd == "report":
            print(daily_report(led, a.day))
            return 0
        ok, problems = led.verify()
        print("verified" if ok else "FAILED: " + "; ".join(problems))
        return 0 if ok else 1
    except LedgerError as exc:
        print(f"stopped (unsafe): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

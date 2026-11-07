"""Command line for the autonomous research prototype (simulation only).

    python -m autotrader replay DATA_DIR OUT_DIR [--start D --end D --last N] [--llm ollama] [--vendor-news]
    python -m autotrader forward OUT_DIR [--llm ollama]          # observe live SEC filings, decide, record
    python -m autotrader daemon OUT_DIR [--llm ollama]           # the same, unattended, until the kill switch
    python -m autotrader score OUT_DIR                           # simulate finished sessions (needs a rotated key)
    python -m autotrader evaluate LEDGER                         # Q1-Q3 from the ledger (protocol v2)
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

from .deciders import LLMResearcher, LLMResearcherV2, NoTrade, baselines
from .evaluate import base_rate, evaluate
from .ledger import Ledger, LedgerError
from .policy import OwnerPolicy
from .report import daily_report
from .risk import RiskController
from .runner import Autonomy

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "SPY"]


def _provider(a, n_calls: int = 0, out: Path | None = None):
    if a.llm == "anthropic":
        from .providers import budgeted_anthropic, worst_case_usd
        worst = worst_case_usd(a.model, n_calls)
        if worst > a.budget_usd:
            raise SystemExit(f"refusing: {n_calls} calls could cost up to ${worst:.2f}, above the ${a.budget_usd:.2f} "
                             "ceiling; reduce the sample or raise the ceiling (an owner decision)")
        print(f"paid model {a.model}: {n_calls} calls, worst case ${worst:.2f}, hard ceiling ${a.budget_usd:.2f}")
        return budgeted_anthropic(a.model, (out or Path(".")) / "budget.json", a.budget_usd)
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
    if a.sample:
        want = {(e["symbol"], e["accession"]) for e in json.loads(Path(a.sample).read_text())["events"]}
        rows = sorted((r for r in src.plan if (r["symbol"], r["accession"]) in want),
                      key=lambda r: (r["day"], r["flash"], r["symbol"]))
    else:
        rows = src.select(a.start, a.end, a.last)
    universe = set(curated_universe()) | set(SECTOR_ETFS) | {r["symbol"] for r in rows}
    deciders = baselines()
    prov = _provider(a, len(rows), out)
    if prov is not None:
        deciders.append((LLMResearcherV2 if a.contract == "2" else LLMResearcher)(prov))
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
        deciders.append((LLMResearcherV2 if a.contract == "2" else LLMResearcher)(prov))
    led = Ledger(out / "ledger.jsonl", "forward")
    auto = Autonomy(led, policy, deciders, RiskController(policy, set(uni) | set(SECTOR_ETFS), out), prices=None)
    return auto, obs, led, out


def _prices_from_env():
    """A market-data key that was never exposed: ALPACA_DATA_KEY_ID and ALPACA_DATA_SECRET. None if absent."""
    kid, sec = os.environ.get("ALPACA_DATA_KEY_ID"), os.environ.get("ALPACA_DATA_SECRET")
    if not kid or not sec:
        return None
    from .sources import AlpacaPrices
    return AlpacaPrices(kid, sec)


def cmd_sample(a) -> int:
    """A pre-specified development sample: the examined period is split into `dates` equal spans of trading days;
    in each span one day is drawn (seeded) among days with enough scorable events, then `per_date` events.
    Written before any model sees it. Previously examined data: development only, never evidence."""
    import hashlib
    import random
    from datetime import date as _d, datetime as _dt, time as _t
    from dkapp.clock import ET
    from .sources import ReplaySource
    src = ReplaySource(Path(a.data))
    exclude = {"2026-10-30"}                                   # the one-day demonstration replay
    ok = []
    for r in src.plan:
        if r["day"] in exclude:
            continue
        d = _d.fromisoformat(r["day"])
        if src.prices.quote(r["symbol"], _dt.combine(d, _t(10, 0, 30), ET)) and \
                src.prices.quote(r["symbol"], _dt.combine(d, _t(15, 45), ET)):
            ok.append(r)
    days = sorted({r["day"] for r in ok})
    rng = random.Random(a.seed)
    chosen = []
    span = len(days) / a.dates
    for i in range(a.dates):
        block = [d for d in days[int(i * span):int((i + 1) * span)]
                 if sum(r["day"] == d for r in ok) >= a.per_date]
        if not block:
            continue
        day = rng.choice(block)
        evs = sorted((r for r in ok if r["day"] == day), key=lambda r: (r["symbol"], r["accession"]))
        chosen += rng.sample(evs, a.per_date)
    events = [{"day": r["day"], "symbol": r["symbol"], "accession": r["accession"], "fold": r["fold"]} for r in chosen]
    body = {"about": "pre-specified development sample for the phase-5 model evaluation (examined data: not evidence)",
            "rule": f"{a.dates} equal spans of trading days, one seeded day per span with >= {a.per_date} scorable "
                    f"events, {a.per_date} seeded events per day; 2026-10-30 excluded (used by the demonstration)",
            "seed": a.seed, "events": events}
    body["sha256"] = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(body, indent=1))
    print(f"{len(events)} events on {len({e['day'] for e in events})} days, "
          f"{events[0]['day']} .. {events[-1]['day']}; sha256 {body['sha256'][:16]}")
    return 0


def cmd_score(a) -> int:
    from .scoring import ForwardScorer
    prices = _prices_from_env()
    out = Path(a.out)
    led = Ledger(out / "ledger.jsonl", "forward")
    if prices is None:
        print("no price source (set ALPACA_DATA_KEY_ID and ALPACA_DATA_SECRET for a rotated key): "
              "decisions stay recorded and pending")
        return 0
    policy = OwnerPolicy.load()
    uni = set(curated_universe()) | set(SECTOR_ETFS)
    auto = Autonomy(led, policy, [], RiskController(policy, uni, out), prices)
    done = ForwardScorer(auto, prices).run()
    (out / "REPORT-daily.md").write_text(daily_report(led))
    print(json.dumps(done, indent=1))
    return 0


def cmd_forward(a) -> int:
    auto, obs, led, out = _forward_setup(a)
    n = auto.forward_cycle(obs)
    (out / "REPORT-daily.md").write_text(daily_report(led))
    print(f"forward cycle: {n} new filing(s) decided; ledger {led.path}")
    return 0


def cmd_daemon(a) -> int:
    auto, obs, led, out = _forward_setup(a)
    prices = _prices_from_env()
    scorer = None
    if prices is not None:
        from .scoring import ForwardScorer
        scorer = ForwardScorer(auto, prices)
    auto.run_forever(obs, poll_seconds=a.poll, scorer=scorer)
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
    r.add_argument("--llm", choices=["none", "ollama", "anthropic"], default="none")
    r.add_argument("--vendor-news", action="store_true")
    r.add_argument("--contract", choices=["1", "2"], default="2", help="model output contract (1 = original)")
    r.add_argument("--sample", help="a pre-specified event sample (python -m autotrader sample)")
    r.add_argument("--model", default="claude-opus-5-5", help="with --llm anthropic")
    r.add_argument("--budget-usd", type=float, default=0.0, help="hard spending ceiling with --llm anthropic")
    for name in ("forward", "daemon"):
        f = sp.add_parser(name)
        f.add_argument("out")
        f.add_argument("--llm", choices=["none", "ollama"], default="none")
        f.add_argument("--poll", type=int, default=300)
        f.add_argument("--contract", choices=["1", "2"], default="2")
    sm = sp.add_parser("sample")
    sm.add_argument("data")
    sm.add_argument("out")
    sm.add_argument("--dates", type=int, default=40)
    sm.add_argument("--per-date", type=int, default=4)
    sm.add_argument("--seed", type=int, default=20261107)
    sc = sp.add_parser("score")
    sc.add_argument("out")
    ev = sp.add_parser("evaluate")
    ev.add_argument("ledger")
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
        if a.cmd == "score":
            return cmd_score(a)
        if a.cmd == "sample":
            return cmd_sample(a)
        mode = json.loads(Path(a.ledger).read_text().splitlines()[0])["payload"]["mode"]
        led = Ledger(Path(a.ledger), mode)
        if a.cmd == "report":
            print(daily_report(led, a.day))
            return 0
        if a.cmd == "evaluate":
            print(json.dumps(evaluate(led), indent=1, default=str))
            return 0
        ok, problems = led.verify()
        print("verified" if ok else "FAILED: " + "; ".join(problems))
        return 0 if ok else 1
    except LedgerError as exc:
        print(f"stopped (unsafe): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

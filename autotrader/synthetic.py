"""A synthetic stand-in for the phase-4 cache, with the 160 real sample event identities and invented market data,
filings and company facts, written in the cache's own file formats. It drives the dry run and the regression
tests through the real code paths (ReplaySource, CachedEdgar, the executor) without any licensed data and without
the network. Nothing in it is evidence of anything.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import random
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

from stockpilot.clock import ET

from .experiment import load_sample

SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]


def _bars(day: date, start_px: float, rng: random.Random, n: int = 26) -> list[dict]:
    out, px = [], start_px
    for i in range(n):
        t = datetime.combine(day, dtime(9, 30), ET) + timedelta(minutes=15 * i)
        o = px
        c = max(1.0, o * (1 + rng.gauss(0, 0.003)))
        hi, lo = max(o, c) * (1 + abs(rng.gauss(0, 0.0015))), min(o, c) * (1 - abs(rng.gauss(0, 0.0015)))
        out.append({"t": t, "open": o, "high": hi, "low": lo, "close": c, "volume": float(rng.randint(50_000, 500_000))})
        px = c
    return out


def _prev_day(d: date) -> date:
    p = d - timedelta(days=1)
    while p.weekday() >= 5:
        p -= timedelta(days=1)
    return p


def _key(cache: Path, url: str) -> Path:
    return cache / hashlib.sha256(url.encode()).hexdigest()[:24]


def make_cache(data: Path, seed: int = 7, drop_plan_rows: int = 0, drop_quotes_for: tuple = (),
               missing_bars_for: tuple = (), no_filing_for: tuple = ()) -> dict:
    """Write a synthetic cache under `data`. The optional arguments break it on purpose for the regression tests."""
    rng = random.Random(seed)
    sample = load_sample()
    data = Path(data)
    (data / "bars15").mkdir(parents=True, exist_ok=True)
    sec = data / "sec_cache"
    sec.mkdir(exist_ok=True)
    companies, plan, events, quotes = {}, [], [], {}
    bars: dict[str, list[dict]] = {}
    for i, e in enumerate(sample["events"]):
        sym, day = e["symbol"], date.fromisoformat(e["day"])
        sector = SECTORS[int(hashlib.sha256(sym.encode()).hexdigest(), 16) % len(SECTORS)]
        cik = companies.setdefault(sym, {"cik": 1_000_000 + len(companies), "name": f"{sym} Synthetic Corp",
                                         "sic": "9999", "sector": sector})["cik"]
        sector = companies[sym]["sector"]
        acc_t = datetime.combine(day, dtime(7, 0), ET)
        flash = acc_t + timedelta(minutes=rng.randint(1, 40))
        for s in (sym, sector, "SPY"):
            for d, px in ((_prev_day(day), 50 + rng.random() * 100), (day, None)):
                start = px if px is not None else bars[s][-1]["close"] * (1 + rng.gauss(0, 0.01))
                if any(b["t"].date() == d for b in bars.get(s, [])):
                    continue
                bs = _bars(d, start, rng)
                if s == sym and d == day and sym in missing_bars_for:
                    bs = [b for b in bs if b["t"].time() != dtime(11, 0)]
                bars.setdefault(s, []).extend(bs)
        react = rng.gauss(0, 0.01)
        sur = {"eps": rng.choice(["beat", "miss", "inline"]), "sales": rng.choice(["beat", "miss", "inline"])}
        row = {"symbol": sym, "day": e["day"], "fold": e["fold"], "accession": e["accession"],
               "flash": flash.isoformat(), "surprise": sur, "H1": False, "react": react, "sector": sector,
               "prevClose": 100.0, "adv20": 5e7, "volRatio": round(0.1 + rng.random() * 0.4, 3), "spyReact": 0.0}
        if i >= drop_plan_rows:
            plan.append(row)
        events.append({"symbol": sym, "accession": e["accession"], "acceptance": acc_t.isoformat(),
                       "items": "2.02,9.01", "flash": flash.isoformat(), "tradeDay": e["day"], "preOpen": True})
        nod = e["accession"].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}"
        stamp = acc_t.strftime("%Y%m%d%H%M%S")
        doc = f"ex991-{i}.htm"
        hdr = (f"<ACCEPTANCE-DATETIME>{stamp}\nITEM INFORMATION: Results of Operations and Financial Condition\n"
               f"<TYPE>8-K\n<SEQUENCE>1\n<FILENAME>form8k-{i}.htm\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>{doc}\n")
        if sym not in no_filing_for:
            _key(sec, f"{base}/{e['accession']}-index-headers.html").write_text(hdr)
            eps = round(rng.uniform(0.2, 3.0), 2)
            _key(sec, f"{base}/{doc}").write_text(
                f"<html><body><p>{sym} Synthetic Corp reports results for the quarter. Revenue was $1,234 million. "
                f"GAAP diluted EPS was ${eps:.2f}. The company reaffirmed its outlook.</p></body></html>")
        qe = date(day.year, ((day.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        fpath = _key(sec, facts_url)
        rows = json.loads(fpath.read_text())["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"] \
            if fpath.exists() else []
        start = qe - timedelta(days=90)
        rows += [{"start": (start - timedelta(days=91)).isoformat(), "end": (start - timedelta(days=1)).isoformat(),
                  "val": 1.0, "form": "10-Q", "filed": (start - timedelta(days=60)).isoformat(),
                  "accn": f"prior-{i}", "fy": day.year, "fp": "Q"},
                 {"start": start.isoformat(), "end": qe.isoformat(), "val": 1.11, "form": "10-Q",
                  "filed": (day + timedelta(days=20)).isoformat(), "accn": f"q-{i}", "fy": day.year, "fp": "Q"}]
        fpath.write_text(json.dumps({"facts": {"us-gaap": {
            "EarningsPerShareDiluted": {"units": {"USD/shares": rows}},
            "Revenues": {"units": {"USD": [{"start": r["start"], "end": r["end"], "val": 1.2e9, "form": "10-Q",
                                            "filed": r["filed"], "accn": r["accn"]} for r in rows]}}}}}))
        for win in ("entry", "exit"):
            for s in (sym, sector):
                if s == sym and sym in drop_quotes_for:
                    continue
                last = [b for b in bars[s] if b["t"].date() == day]
                mid = last[2]["open"] if win == "entry" else last[-1]["open"]
                quotes.setdefault(f"{e['day']}|{win}", {})[s] = {"mid": mid, "halfSpreadBps": round(1 + rng.random() * 6, 3),
                                                                 "n": 50, "askSize": 300.0}
    for s, bs in bars.items():
        df = pd.DataFrame(bs).set_index("t").sort_index()
        df.index = pd.DatetimeIndex(df.index)
        with open(data / "bars15" / f"{s.replace('.', '_')}.pkl", "wb") as f:
            pickle.dump(df[["open", "high", "low", "close", "volume"]], f)
    (data / "plan.json").write_text(json.dumps(plan, indent=1))
    (data / "events.json").write_text(json.dumps(events, indent=1))
    (data / "companies.json").write_text(json.dumps(companies, indent=1))
    (data / "quotes.json").write_text(json.dumps(quotes))
    (data / "SYNTHETIC").write_text("synthetic data for regression tests; not market data\n")
    return {"events": len(events), "plan_rows": len(plan), "symbols": len(bars)}

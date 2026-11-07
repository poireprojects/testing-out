"""Phase 4 data collection for P4-H1 (research/PREREGISTRATION-2026-11-07-phase4.md). Computes no outcomes.

    SEC_USER_AGENT="name contact@email" ALPACA_PAPER_KEY=PK... python scripts/phase4_collect.py STAGE DATA_DIR
    STAGE: events | bars | plan | quotes

events  SEC Item 2.02 8-Ks for the universe, then the verified vendor flash for each (strict match), parsed.
bars    consolidated 15-minute bars, 2022-11-01 .. 2026-10-30, regular hours only.
plan    fixes every trade and every baseline pairing (B1..B4) using information up to 10:00 only.
quotes  NBBO at 10:00:30-10:01:30 and 15:45:00-15:46:00 for each planned (symbol, day).
Every request is checked against the ledger: nothing touches the vault (2020-07-27 .. 2022-10-31).
"""
from __future__ import annotations

import json
import os
import pickle
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.clock import ET, MarketCalendar  # noqa: E402
from dkapp.data.universe import curated_universe  # noqa: E402
from dkapp.research.events import Edgar, json_acceptance, match_release, parse_flash, sector_etf  # noqa: E402
from dkapp.research.ledger import EXAMINED, DataLedger  # noqa: E402

BARS_FROM = date(2022, 11, 1)
EVENTS_FROM, EVENTS_TO = date(2022, 12, 1), date(2026, 10, 30)
VAULT_END = date(2022, 10, 31)
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
SEED = 20261107
_last = [0.0]


def pace(s=None):
    s = float(os.environ.get("PACE_S", "0.35")) if s is None else s
    w = s - (time.monotonic() - _last[0])
    if w > 0:
        time.sleep(w)
    _last[0] = time.monotonic()


def guard(d: date) -> None:
    if d <= VAULT_END:
        raise SystemExit(f"refusing: {d} is in the vault")


def clients():
    key = os.environ["ALPACA_PAPER_KEY"]
    if key.upper().startswith("AK"):
        raise SystemExit("refusing: that looks like a live key")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.news import NewsClient
    sec = os.environ.get("ALPACA_PAPER_SECRET", "proxy-injected")
    return StockHistoricalDataClient(key, sec), NewsClient(key, sec)


def all_filings(sec: Edgar, sub: dict) -> list[dict]:
    """recent + older pages, as flat dicts (only pages that can reach back to EVENTS_FROM)."""
    def flat(block):
        n = len(block.get("form", []))
        return [{k: block[k][i] for k in ("accessionNumber", "acceptanceDateTime", "form", "items") if k in block} for i in range(n)]
    out = flat(sub["filings"]["recent"])
    for f in sub["filings"].get("files", []):
        if f.get("filingTo", "9999") >= str(EVENTS_FROM - timedelta(days=5)):
            out += flat(json.loads(sec.get(f"https://data.sec.gov/submissions/{f['name']}")))
    return out


# ------------------------------------------------------------------ stage: events
def stage_events(data: Path) -> None:
    sec = Edgar(os.environ.get("SEC_USER_AGENT", ""), data / "sec_cache")
    hc, nc = clients()
    cal = MarketCalendar()
    ledger = DataLedger.load()
    tick = sec.ticker_map()
    from alpaca.data.requests import NewsRequest
    companies = {}
    for s in sorted(curated_universe()):
        cik = tick.get(s.upper().replace(".", "-"))
        if cik is None:
            continue
        sub = sec.submissions(cik)
        if not sub.get("sic"):
            continue                                   # funds/ETFs: no operating-company SIC code
        companies[s] = {"cik": cik, "name": sub.get("name"), "sic": sub.get("sic"), "sector": sector_etf(sub.get("sic"))}
        cands = []
        for f in all_filings(sec, sub):
            if f["form"] != "8-K" or "2.02" not in (f.get("items") or ""):
                continue
            acc = json_acceptance(f["acceptanceDateTime"])
            if EVENTS_FROM <= acc.date() <= EVENTS_TO:
                cands.append({"accession": f["accessionNumber"], "acceptance": acc.isoformat(), "items": f["items"]})
        companies[s]["filings"] = sorted({c["accession"]: c for c in cands}.values(), key=lambda c: c["acceptance"])
    (data / "companies.json").write_text(json.dumps(companies, indent=1))
    print(f"{len(companies)} operating companies; {sum(len(c['filings']) for c in companies.values())} Item 2.02 8-Ks", flush=True)
    events, k = [], 0
    for s, c in companies.items():
        for f in c["filings"]:
            acc = datetime.fromisoformat(f["acceptance"])
            start = acc - timedelta(hours=18)
            guard(start.date())
            pace()
            try:
                res = nc.get_news(NewsRequest(symbols=s, start=start, end=acc + timedelta(minutes=15), limit=50, include_content=False))
                items = res.data.get("news", [])
            except Exception as exc:
                print("  news error", s, str(exc)[:100], flush=True)
                continue
            news = sorted([{"created": n.created_at.astimezone(ET).isoformat(), "headline": n.headline} for n in items],
                          key=lambda x: x["created"])
            rel, src, _ = match_release(news, acc, f["items"], c["name"], s)
            ev = {"symbol": s, **f, "flash": None}
            if rel is not None:
                head = src.split(": ", 1)[1] if ": " in src else src
                full = next((n["headline"] for n in news if n["created"] == rel.isoformat()), head)
                nxt = cal.next_session(rel)
                prev = cal.previous_session(nxt.open)
                eligible = prev.close <= rel <= nxt.open - timedelta(minutes=5)
                ev.update({"flash": rel.isoformat(), "headline": full, "surprise": parse_flash(full),
                           "tradeDay": str(nxt.day), "preOpen": eligible, "ledger": ledger.status(nxt.day)})
            events.append(ev)
            k += 1
            if k % 200 == 0:
                print(f"  {k} filings checked", flush=True)
    (data / "events.json").write_text(json.dumps(events, indent=1))
    v = [e for e in events if e["flash"]]
    print(f"events: {len(events)} filings, {len(v)} verified flashes, {sum(e['preOpen'] for e in v)} released before the open", flush=True)


# ------------------------------------------------------------------ stage: bars
def stage_bars(data: Path) -> None:
    hc, _ = clients()
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    companies = json.loads((data / "companies.json").read_text())
    syms = sorted(set(companies) | set(SECTORS) | {"SPY"})
    out = data / "bars15"
    out.mkdir(exist_ok=True)
    guard(BARS_FROM)
    start = datetime.combine(BARS_FROM, datetime.min.time(), ET)
    end = datetime.combine(EVENTS_TO, datetime.min.time(), ET) + timedelta(hours=20)
    for i, s in enumerate(syms):
        p = out / f"{s.replace('.', '_')}.pkl"
        if p.exists():
            continue
        pace()
        try:
            df = hc.get_stock_bars(StockBarsRequest(symbol_or_symbols=s, start=start, end=end, feed=DataFeed.SIP,
                                                    timeframe=TimeFrame(15, TimeFrameUnit.Minute))).df
        except Exception as exc:
            print("  bars error", s, str(exc)[:120], flush=True)
            continue
        if df is None or df.empty:
            pickle.dump(pd.DataFrame(), open(p, "wb"))
            continue
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(s, level=0)
        df.index = pd.to_datetime(df.index).tz_convert(ET)
        t = df.index.hour * 60 + df.index.minute
        df = df[(t >= 570) & (t < 960)][["open", "high", "low", "close", "volume"]].astype(float)
        pickle.dump(df, open(p, "wb"))
        if i % 50 == 0:
            print(f"  bars {i + 1}/{len(syms)} {s} rows={len(df)}", flush=True)


def load_bars(data: Path, s: str) -> pd.DataFrame:
    p = data / "bars15" / f"{s.replace('.', '_')}.pkl"
    return pickle.load(open(p, "rb")) if p.exists() else pd.DataFrame()


def day_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per session: prior regular-hours close, price at 10:00 (close of the 09:45 bar), 09:30-10:00 volume,
    20-day average dollar volume (all known at 10:00)."""
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["day"] = d.index.date
    g = d.groupby("day")
    last = g["close"].last()
    bar945 = d[(d.index.hour == 9) & (d.index.minute == 45)].groupby("day")["close"].last()
    vol30 = d[(d.index.hour == 9)].groupby("day")["volume"].sum()
    dollar = (d["close"] * d["volume"]).groupby(d["day"]).sum()
    out = pd.DataFrame({"close": last, "p1000": bar945, "vol30": vol30, "dollarVol": dollar})
    out["prevClose"] = out["close"].shift(1)
    out["adv20"] = out["dollarVol"].shift(1).rolling(20, min_periods=15).mean()
    out["advShares20"] = (d["volume"].groupby(d["day"]).sum()).shift(1).rolling(20, min_periods=15).mean()
    return out


# ------------------------------------------------------------------ stage: plan (no outcomes)
def stage_plan(data: Path) -> None:
    companies = json.loads((data / "companies.json").read_text())
    events = json.loads((data / "events.json").read_text())
    frames = {s: day_frame(load_bars(data, s)) for s in list(companies) + SECTORS + ["SPY"]}
    flash_days: dict[str, set] = {}
    for e in events:
        if e.get("flash"):
            flash_days.setdefault(e["symbol"], set()).add(e["tradeDay"])
    cal_days = sorted({d for f in frames.values() if not f.empty for d in f.index})
    pos = {d: i for i, d in enumerate(cal_days)}

    def near_event(s, d):
        i = pos.get(d)
        if i is None:
            return True
        for td in flash_days.get(s, ()):
            j = pos.get(date.fromisoformat(td))
            if j is not None and abs(j - i) <= 3:
                return True
        return False

    def info(s, d):
        f = frames.get(s)
        if f is None or f.empty or d not in f.index:
            return None
        r = f.loc[d]
        sec = companies[s]["sector"] if s in companies else "SPY"
        fs = frames[sec]
        if d not in fs.index or any(pd.isna(x) for x in (r.prevClose, r.p1000, r.adv20)):
            return None
        rs = fs.loc[d]
        react = r.p1000 / r.prevClose - 1 - (rs.p1000 / rs.prevClose - 1)
        spy = frames["SPY"].loc[d] if d in frames["SPY"].index else None
        return {"react": float(react), "sector": sec, "prevClose": float(r.prevClose), "adv20": float(r.adv20),
                "volRatio": float(r.vol30 / r.advShares20) if r.advShares20 else None,
                "spyReact": float(spy.p1000 / spy.prevClose - 1) if spy is not None else None}

    rng = random.Random(SEED)
    plan, seen = [], {}
    for e in events:
        if not e.get("flash") or not e.get("preOpen") or e.get("ledger") != EXAMINED:
            continue
        s, d = e["symbol"], date.fromisoformat(e["tradeDay"])
        if not (EVENTS_FROM <= d <= EVENTS_TO):
            continue
        prior = seen.get(s)
        if prior is not None and pos.get(d, 0) - pos.get(prior, -99) <= 3:
            continue                                    # duplicate flash within 3 sessions
        seen[s] = d
        x = info(s, d)
        if x is None or x["prevClose"] < 5 or x["adv20"] < 20e6:
            continue
        sur = e["surprise"]
        h1 = sur.get("eps") == "beat" and sur.get("sales") == "beat" and x["react"] > 0
        row = {"symbol": s, "day": str(d), "fold": "dev" if d <= date(2024, 10, 31) else "eval", "accession": e["accession"],
               "flash": e["flash"], "surprise": sur, "H1": bool(h1), **x}
        if h1:
            # B3: same day, eligible stock with no earnings flash within +-3 sessions, nearest reaction
            best = None
            for s2 in companies:
                if s2 == s or near_event(s2, d):
                    continue
                y = info(s2, d)
                if y is None or y["prevClose"] < 5 or y["adv20"] < 20e6:
                    continue
                dist = abs(y["react"] - x["react"])
                if best is None or dist < best[0]:
                    best = (dist, s2, y)
            if best is not None:
                row["B3"] = {"symbol": best[1], "react": best[2]["react"], "sector": best[2]["sector"]}
            # B4: same stock, random non-event day within +-60 sessions
            i = pos[d]
            cands = [cal_days[j] for j in range(max(0, i - 60), min(len(cal_days), i + 61))
                     if j != i and not near_event(s, cal_days[j]) and cal_days[j] >= EVENTS_FROM and info(s, cal_days[j])]
            if cands:
                row["B4"] = {"day": str(rng.choice(cands))}
        plan.append(row)
    (data / "plan.json").write_text(json.dumps(plan, indent=1, default=str))
    print(f"plan: {len(plan)} eligible events, {sum(r['H1'] for r in plan)} H1 trades "
          f"(dev {sum(r['H1'] and r['fold'] == 'dev' for r in plan)}, eval {sum(r['H1'] and r['fold'] == 'eval' for r in plan)})", flush=True)


# ------------------------------------------------------------------ stage: quotes
def stage_quotes(data: Path) -> None:
    hc, _ = clients()
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockQuotesRequest
    plan = json.loads((data / "plan.json").read_text())
    need: dict[str, set] = {}
    for r in plan:
        need.setdefault(r["day"], set()).update({r["symbol"], r["sector"]})
        if "B3" in r:
            need[r["day"]].update({r["B3"]["symbol"], r["B3"]["sector"]})
        if "B4" in r:
            need.setdefault(r["B4"]["day"], set()).update({r["symbol"], r["sector"]})
    qpath = data / "quotes.json"
    got = json.loads(qpath.read_text()) if qpath.exists() else {}
    days = sorted(need)
    for k, dstr in enumerate(days):
        d = date.fromisoformat(dstr)
        guard(d)
        for win, (h, m, s0, dur) in {"entry": (10, 0, 30, 60), "exit": (15, 45, 0, 60)}.items():
            key = f"{dstr}|{win}"
            if key in got:
                continue
            t0 = datetime(d.year, d.month, d.day, h, m, s0, tzinfo=ET)
            pace()
            try:
                q = hc.get_stock_quotes(StockQuotesRequest(symbol_or_symbols=sorted(need[dstr]), start=t0,
                                                           end=t0 + timedelta(seconds=dur), feed=DataFeed.SIP)).df
            except Exception as exc:
                print("  quotes error", dstr, win, str(exc)[:100], flush=True)
                continue
            res = {}
            if q is not None and not q.empty:
                for sym, g in q.groupby(level=0):
                    b, a = g["bid_price"].astype(float), g["ask_price"].astype(float)
                    ok = (b > 0) & (a > b) & ((a - b) / ((a + b) / 2) < 0.2)
                    if ok.sum() == 0:
                        continue
                    mid = ((a + b) / 2)[ok]
                    res[sym] = {"mid": float(mid.median()), "halfSpreadBps": float((((a - b) / 2)[ok] / mid).median() * 1e4),
                                "n": int(ok.sum()), "askSize": float(g["ask_size"][ok].median())}
            got[key] = res
        if k % 25 == 0:
            qpath.write_text(json.dumps(got))
            print(f"  quotes {k + 1}/{len(days)} {dstr}", flush=True)
    qpath.write_text(json.dumps(got))
    print("quotes done:", len(got), "windows", flush=True)


if __name__ == "__main__":
    st, data = sys.argv[1], Path(sys.argv[2])
    data.mkdir(parents=True, exist_ok=True)
    {"events": stage_events, "bars": stage_bars, "plan": stage_plan, "quotes": stage_quotes}[st](data)

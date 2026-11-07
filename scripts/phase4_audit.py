"""Phase 4 feasibility audit: can SEC disclosures be reconstructed and acted on, honestly, at real prices?

    SEC_USER_AGENT="name contact@email" ALPACA_PAPER_KEY=PK... python scripts/phase4_audit.py OUT_DIR CACHE_DIR

The sample is about 50 Form 8-K filings, accepted 2025-09-02 .. 2026-10-30. Those days are development data
in research/ledger.json; the vault is never touched. Companies are a seeded random draw from the app's
universe, stratified into earnings 8-Ks (Item 2.02) and all other 8-Ks.

Per event the script records:
* the SEC acceptance time, from the filing header, cross-checked against data.sec.gov
* the earliest matching news-vendor headline (Alpaca/Benzinga created_at), as the public release time
* the receipt and actionable times
* consolidated (SIP) and IEX minute bars, NBBO and IEX quotes at the actionable minute
* how much of the price reaction happened before an order could have been placed

Market data is read-only. No order endpoint is called.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.clock import ET, MarketCalendar  # noqa: E402
from stockpilot.data.universe import curated_universe  # noqa: E402
from stockpilot.research.events import (Edgar, EventTimes, json_acceptance, match_release, recent_filings,  # noqa: E402
                                   sector_etf, session_phase, stated_times)
from stockpilot.research.ledger import EXAMINED, DataLedger  # noqa: E402

WINDOW = (date(2025, 9, 2), date(2026, 10, 30))
SEED = 20261107
N_COMPANIES = 120
PER_STRATUM = 25
def alpaca():
    key = os.environ["ALPACA_PAPER_KEY"]
    if key.upper().startswith("AK"):
        raise SystemExit("refusing: that looks like a live key")
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.news import NewsClient
    sec = os.environ.get("ALPACA_PAPER_SECRET", "proxy-injected")
    return StockHistoricalDataClient(key, sec), NewsClient(key, sec)


_last = [0.0]


def pace():
    w = 0.35 - (time.monotonic() - _last[0])          # stay well under 200 calls/minute
    if w > 0:
        time.sleep(w)
    _last[0] = time.monotonic()


def news_for(nc, sym: str, start: datetime, end: datetime) -> list[dict]:
    from alpaca.data.requests import NewsRequest
    pace()
    try:
        res = nc.get_news(NewsRequest(symbols=sym, start=start, end=end, limit=50, include_content=False))
    except Exception as exc:
        return [{"error": str(exc)[:200]}]
    items = getattr(res, "data", {}).get("news", []) if hasattr(res, "data") else []
    out = []
    for n in items:
        out.append({"created": n.created_at.astimezone(ET).isoformat(), "headline": n.headline, "source": n.source,
                    "symbols": list(n.symbols or [])})
    return sorted(out, key=lambda x: x["created"])


def bars(hc, syms: list[str], start: datetime, end: datetime, feed: str) -> dict[str, pd.DataFrame]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    pace()
    try:
        df = hc.get_stock_bars(StockBarsRequest(symbol_or_symbols=syms, start=start, end=end, timeframe=TimeFrame.Minute,
                                                feed=DataFeed.SIP if feed == "sip" else DataFeed.IEX)).df
    except Exception as exc:
        print("  bars error", feed, syms, str(exc)[:120], flush=True)
        return {}
    out = {}
    if df is None or df.empty:
        return out
    for s in syms:
        try:
            d = df.xs(s, level=0).copy()
        except KeyError:
            continue
        d.index = pd.to_datetime(d.index).tz_convert(ET)
        out[s] = d
    return out


def quotes(hc, sym: str, start: datetime, end: datetime, feed: str) -> pd.DataFrame:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockQuotesRequest
    pace()
    try:
        q = hc.get_stock_quotes(StockQuotesRequest(symbol_or_symbols=sym, start=start, end=end, limit=20000,
                                                   feed=DataFeed.SIP if feed == "sip" else DataFeed.IEX)).df
    except Exception as exc:
        print("  quotes error", feed, sym, str(exc)[:120], flush=True)
        return pd.DataFrame()
    if q is None or q.empty:
        return pd.DataFrame()
    q = q.xs(sym, level=0) if isinstance(q.index, pd.MultiIndex) else q
    return q


def spread_stats(q: pd.DataFrame) -> dict:
    if q.empty:
        return {"quotes": 0}
    b, a = q["bid_price"].astype(float), q["ask_price"].astype(float)
    ok = (b > 0) & (a > b)
    if not ok.any():
        return {"quotes": int(len(q)), "valid": 0}
    mid = (a + b)[ok] / 2
    bps = ((a - b)[ok] / mid) * 1e4
    return {"quotes": int(len(q)), "valid": int(ok.sum()), "medianSpreadBps": float(bps.median()),
            "p90SpreadBps": float(bps.quantile(0.9)), "medianAskSize": float(q["ask_size"][ok].median())}


def price_at(d: pd.DataFrame, t: datetime, field: str = "close", before: bool = True):
    """Price from bars that ENDED by t (before=True) or the bar that STARTS at t (before=False: its open)."""
    if d is None or d.empty:
        return None
    if before:
        x = d[d.index + pd.Timedelta(minutes=1) <= t]
        return float(x[field].iloc[-1]) if len(x) else None
    x = d[d.index >= t]
    return float(x["open"].iloc[0]) if len(x) and x.index[0] == t else None


def main(out: Path, cache: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    ua = os.environ.get("SEC_USER_AGENT", "")
    sec = Edgar(ua, cache / "sec")
    hc, nc = alpaca()
    cal = MarketCalendar()
    ledger = DataLedger.load()
    tick = sec.ticker_map()
    rng = random.Random(SEED)
    pool = [s for s in curated_universe() if s.upper().replace(".", "-") in tick]
    companies = rng.sample(sorted(pool), min(N_COMPANIES, len(pool)))
    pop = {"earnings": [], "other": []}
    meta = {}
    for s in companies:
        cik = tick[s.upper().replace(".", "-")]
        sub = sec.submissions(cik)
        meta[s] = {"cik": cik, "name": sub.get("name"), "sic": sub.get("sic"), "tickers": sub.get("tickers"),
                   "formerNames": sub.get("formerNames", [])}
        for f in recent_filings(sub, ("8-K",)):
            acc = json_acceptance(f["acceptanceDateTime"])
            if not (WINDOW[0] <= acc.date() <= WINDOW[1]):
                continue
            (pop["earnings"] if "2.02" in (f.get("items") or "") else pop["other"]).append(f | {"symbol": s, "cik": cik})
    print(f"{len(companies)} companies, {len(pop['earnings'])} earnings 8-Ks, {len(pop['other'])} other 8-Ks in window", flush=True)
    sample = []
    for stratum, fs in pop.items():
        seen, picked = set(), []
        for f in rng.sample(fs, len(fs)):
            if f["symbol"] in seen:
                continue
            seen.add(f["symbol"])
            picked.append(f | {"stratum": stratum})
            if len(picked) == PER_STRATUM:
                break
        sample += picked
    events = []
    for k, f in enumerate(sample):
        s, cik, accn = f["symbol"], f["cik"], f["accessionNumber"]
        h = sec.header(cik, accn)
        acc_json = json_acceptance(f["acceptanceDateTime"])
        acc = h["acceptance"] or acc_json
        ex99 = [d for d in h["documents"] if d["type"].upper().startswith("EX-99")]
        stated = []
        if ex99:
            try:
                stated = stated_times(sec.document(cik, accn, ex99[0]["filename"]))
            except Exception as exc:
                stated = [f"error: {str(exc)[:60]}"]
        news = news_for(nc, s, acc - timedelta(hours=24), acc + timedelta(hours=2))
        release, rsrc, n_near = match_release(news, acc, f.get("items") or "", meta[s]["name"], s)
        et = EventTimes.build(acc, release, cal, rsrc)
        ev = {"symbol": s, "name": meta[s]["name"], "cik": cik, "accession": accn, "stratum": f["stratum"],
              "items": f.get("items"), "acceptance": acc.isoformat(), "acceptanceJsonUtcCheckSeconds": (acc_json - acc).total_seconds(),
              "acceptancePhase": session_phase(acc, cal), "pressRelease": bool(ex99), "statedTimes": stated[:4],
              "release": release.isoformat() if release else None, "releaseSource": rsrc, "newsNearby": n_near,
              "releaseMinusAcceptanceMin": (release - acc).total_seconds() / 60 if release else None,
              "receipt": et.receipt.isoformat(), "actionable": et.actionable.isoformat(),
              "delayFromFirstPublicMin": ((et.actionable - (release or acc)).total_seconds() / 60),
              "sector": sector_etf(meta[s]["sic"]), "formerNames": [n for n in meta[s]["formerNames"]
                                                                   if (n.get("to") or "") >= str(WINDOW[0])]}
        day = et.actionable.astimezone(ET).date()
        ev["ledgerStatus"] = ledger.status(day)
        if ev["ledgerStatus"] != EXAMINED:
            ev["excluded"] = f"day {day} is {ev['ledgerStatus']} in the ledger"
            events.append(ev)
            continue
        first = release or acc
        start = datetime.combine((first - timedelta(days=4)).date(), datetime.min.time(), ET).replace(hour=4)
        end = datetime.combine(day, datetime.min.time(), ET).replace(hour=20)
        syms = list(dict.fromkeys([s, "SPY", ev["sector"]]))
        sip = bars(hc, syms, start, end, "sip")
        iex = bars(hc, [s], start, end, "iex")
        d, di = sip.get(s), iex.get(s)
        t0, ta = first, et.actionable
        ref = price_at(d, t0)                                # last trade before the first public release
        entry = price_at(d, ta, before=False)                # the actionable minute's open
        sess = cal.session(day)
        p60 = price_at(d, ta + timedelta(minutes=61))
        pclose = price_at(d, sess.close - timedelta(minutes=10)) if sess else None
        def rel(a, b):
            return (a / b - 1) * 100 if a is not None and b else None
        ev["sip"] = {"barsAround": int(len(d)) if d is not None else 0, "refPrice": ref, "entryPrice": entry,
                     "moveBeforeEntryPct": rel(entry, ref), "move60AfterEntryPct": rel(p60, entry),
                     "moveToCloseAfterEntryPct": rel(pclose, entry)}
        for b_ in ("SPY", ev["sector"]):
            db = sip.get(b_)
            ev["sip"][f"{b_}60Pct"] = rel(price_at(db, ta + timedelta(minutes=61)), price_at(db, ta, before=False))
        w = (d.index >= ta) & (d.index < ta + timedelta(minutes=30)) if d is not None else None
        sv = float(d["volume"][w].sum()) if d is not None else 0.0
        iv = float(di["volume"][(di.index >= ta) & (di.index < ta + timedelta(minutes=30))].sum()) if di is not None else 0.0
        ev["volume30"] = {"sip": sv, "iex": iv, "iexShare": iv / sv if sv else None,
                          "iexBarAtEntry": bool(di is not None and ta in di.index), "sipBarAtEntry": bool(d is not None and ta in d.index)}
        ev["iexEntryPrice"] = price_at(di, ta, before=False)
        ev["spreads"] = {"sip": spread_stats(quotes(hc, s, ta, ta + timedelta(seconds=60), "sip")),
                         "iex": spread_stats(quotes(hc, s, ta, ta + timedelta(seconds=60), "iex"))}
        events.append(ev)
        print(f"{k + 1:2d}/{len(sample)} {s:6s} {f['stratum']:8s} items={ev['items']:12s} acc={acc:%Y-%m-%d %H:%M} "
              f"{ev['acceptancePhase']:13s} release={'—' if not release else f'{release:%H:%M}'} "
              f"before={ev['sip']['moveBeforeEntryPct']} spread={ev['spreads']['sip'].get('medianSpreadBps')}", flush=True)
    (out / "audit_events.json").write_text(json.dumps({"window": [str(WINDOW[0]), str(WINDOW[1])], "seed": SEED,
                                                       "companiesDrawn": len(companies), "population": {k: len(v) for k, v in pop.items()},
                                                       "secRequests": sec.requests, "events": events}, indent=1, default=str))
    print("SEC requests:", sec.requests)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))

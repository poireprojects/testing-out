"""Where the autonomous trader's information comes from, with the time each piece became available.

Replay (development only; previously examined data, never evidence):
  the phase-4 cache of earnings events (2022-12 -> 2026-10): SEC acceptance times, vendor headlines with their
  timestamps, consolidated 15-minute bars and NBBO quotes at 10:00:30 and 15:45; plus SEC EDGAR text fetched
  under the fair-access rules (the press release exhibit and XBRL company facts filed before the decision).
Forward:
  SEC EDGAR's live 8-K feed for companies in the app's universe, the filing text and XBRL history. Prices need a
  market-data key that was never exposed (AlpacaPrices refuses the exposed one); without it, decisions are
  recorded and scored later.
"""
from __future__ import annotations

import json
import pickle
import re
import statistics
import urllib.request
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from dkapp.clock import ET
from dkapp.research.events import Edgar, FairAccessError, sector_etf  # noqa: F401  (re-exported)

from .evidence import Evidence, EvidencePack, plain_text
from .policy import refuse_exposed_key
from .risk import Quote

EXCERPT_CHARS = 3500
RESEARCH_NOTE = (
    "This project's own research on its development data (Dec 2022 - Oct 2024, 2,650 earnings mornings, computed "
    "with costs): buying at 10:00:30 and selling at 15:45 averaged -0.25% per trade after costs. Earnings mornings "
    "with an EPS beat, a sales beat and a positive price reaction by 10:00 averaged +0.03% before costs and -0.23% "
    "after. A stock with the same price reaction but no news did as well. Typical cost of a round trip in these "
    "stocks: about 0.24% in spread and slippage plus about 0.03% in fees. Most earnings mornings offer no edge after "
    "costs.")
RESEARCH_AVAILABLE = datetime(2024, 10, 31, 16, 0, tzinfo=ET)     # uses only data up to the end of the dev fold


# ------------------------------------------------------------------ SEC helpers
class LiveEdgar(Edgar):
    def fresh(self, url: str) -> bytes:
        """Same pacing and declared contact as Edgar.get, but never served from the cache (live feeds)."""
        import time as _t
        wait = 0.25 - (_t.monotonic() - self._last)
        if wait > 0:
            _t.sleep(wait)
        self._last = _t.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.requests += 1
            return r.read()


def filing_item(edgar: Edgar, cik: int, accession: str, eid: str, decision_time: datetime) -> Optional[Evidence]:
    """The press-release exhibit (or the 8-K itself) as evidence, if it was accepted by the decision time."""
    h = edgar.header(cik, accession)
    acc = h["acceptance"]
    if acc is None or acc > decision_time:
        return None
    docs = h["documents"]
    doc = next((d for d in docs if d["type"].upper().startswith("EX-99")), None) or \
        next((d for d in docs if d["type"].upper().startswith("8-K")), None)
    if doc is None:
        return None
    text = plain_text(edgar.document(cik, accession, doc["filename"]))[:EXCERPT_CHARS]
    nod = accession.replace("-", "")
    return Evidence(eid, "filing", f"SEC EDGAR 8-K {accession}, {doc['type']} (items {', '.join(h['items']) or 'n/a'})",
                    acc, text, url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{doc['filename']}")


_EPS = ("EarningsPerShareDiluted",)
_REV = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")


def _quarterly(facts: dict, tags, unit: str) -> list[dict]:
    """Three-month facts under any of the tags (companies switch revenue tags over the years)."""
    g = facts.get("facts", {}).get("us-gaap", {})
    out = []
    for tag in tags:
        rows = g.get(tag, {}).get("units", {}).get(unit, [])
        out += [r for r in rows if r.get("start")
                and 75 <= (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days <= 100]
    return out


def xbrl_history(edgar: Edgar, cik: int, before: date) -> tuple[list[dict], Optional[date]]:
    """Last four quarters of diluted EPS and revenue from filings made before `before` (point in time)."""
    facts = json.loads(edgar.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
    out: dict[str, dict] = {}
    last_filed = None
    for key, rows, scale in (("eps", _quarterly(facts, _EPS, "USD/shares"), 1.0),
                             ("rev_m", _quarterly(facts, _REV, "USD"), 1e6)):
        for r in rows:
            f = date.fromisoformat(r["filed"])
            if f >= before:
                continue
            cur = out.setdefault(r["end"], {"end": r["end"]})
            if key not in cur or f > cur[f"{key}_filed"]:
                cur[key], cur[f"{key}_filed"], cur["form"] = r["val"] / scale, f, r.get("form")
                last_filed = max(last_filed or f, f)
    hist = sorted(out.values(), key=lambda x: x["end"])[-4:]
    return hist, last_filed


def xbrl_truth(edgar: Edgar, cik: int, event_day: date) -> dict:
    """Scoring only (after the fact): the quarter the release reported, as later filed in a 10-Q/10-K."""
    facts = json.loads(edgar.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
    out = {}
    for key, rows, scale in (("eps_diluted", _quarterly(facts, _EPS, "USD/shares"), 1.0),
                             ("revenue_usd_millions", _quarterly(facts, _REV, "USD"), 1e6)):
        cand = [r for r in rows if date.fromisoformat(r["end"]) < event_day <= date.fromisoformat(r["filed"])
                and (event_day - date.fromisoformat(r["end"])).days <= 120]
        if cand:
            r = max(cand, key=lambda r: r["end"])
            out[key], out["period_end"] = r["val"] / scale, r["end"]
    return out


def history_item(edgar: Edgar, cik: int, eid: str, decision_time: datetime) -> Optional[Evidence]:
    hist, last = xbrl_history(edgar, cik, decision_time.astimezone(ET).date())
    if not hist or last is None:
        return None
    lines = []
    for h in hist:
        eps = f"diluted EPS {h['eps']:.2f}" if "eps" in h else "diluted EPS not reported"
        rev = f"revenue ${h['rev_m']:,.0f} million" if "rev_m" in h else "revenue not reported"
        lines.append(f"Quarter ended {h['end']}: {eps}; {rev} (form {h.get('form')}).")
    return Evidence(eid, "financials", f"SEC XBRL company facts, CIK {cik}, filed before {decision_time.date()}",
                    datetime.combine(last, dtime(23, 59), ET), " ".join(lines),
                    url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def research_item(eid: str) -> Evidence:
    return Evidence(eid, "research", "this project's phase-4 development results (data through 2024-10-31)",
                    RESEARCH_AVAILABLE, RESEARCH_NOTE)


# ------------------------------------------------------------------ replay (phase-4 cache)
class ReplayPrices:
    """Quotes exist only at 10:00:30 (entry window) and 15:45 (exit window); bars are 15-minute SIP bars."""

    def __init__(self, data_dir: Path):
        self.d = Path(data_dir)
        self.q: dict = {}
        for f in sorted(self.d.glob("quotes*.json")):
            self.q.update(json.loads(f.read_text()))
        self._bars: dict[str, pd.DataFrame] = {}

    def _all(self, s: str) -> pd.DataFrame:
        if s not in self._bars:
            p = self.d / "bars15" / f"{s.replace('.', '_')}.pkl"
            self._bars[s] = pickle.load(open(p, "rb")) if p.exists() else pd.DataFrame()
        return self._bars[s]

    def bars15(self, s: str, day: date) -> pd.DataFrame:
        b = self._all(s)
        return b[b.index.date == day] if not b.empty else b

    def prev_close(self, s: str, day: date) -> Optional[float]:
        b = self._all(s)
        if b.empty:
            return None
        before = b[b.index.date < day]
        return float(before["close"].iloc[-1]) if not before.empty else None

    def quote(self, s: str, t: datetime) -> Optional[Quote]:
        t = t.astimezone(ET)
        win = {dtime(10, 0, 30): "entry", dtime(15, 45): "exit"}.get(t.time().replace(microsecond=0))
        if win is None:
            return None
        r = self.q.get(f"{t.date().isoformat()}|{win}", {}).get(s)
        if not r:
            return None
        hs = r["halfSpreadBps"] / 1e4
        return Quote(s, r["mid"] * (1 - hs), r["mid"] * (1 + hs), t, self.prev_close(s, t.date()))

    def move_by(self, s: str, day: date, until: dtime) -> Optional[float]:
        """% move from the prior close to the last 15-minute bar that closed by `until`."""
        b, pc = self.bars15(s, day), self.prev_close(s, day)
        if b.empty or pc is None:
            return None
        done = b[b.index + timedelta(minutes=15) <= datetime.combine(day, until, ET)]
        return (float(done["close"].iloc[-1]) / pc - 1) * 100 if not done.empty else None


class ReplaySource:
    def __init__(self, data_dir: Path, edgar: Optional[Edgar] = None):
        self.d = Path(data_dir)
        self.plan = json.loads((self.d / "plan.json").read_text())
        self.events = {e["accession"]: e for e in json.loads((self.d / "events.json").read_text())}
        self.companies = json.loads((self.d / "companies.json").read_text())
        self.prices = ReplayPrices(self.d)
        self.edgar = edgar

    def select(self, start: str, end: str, last_n: Optional[int] = None) -> list[dict]:
        rows = sorted((r for r in self.plan if start <= r["day"] <= end), key=lambda r: (r["day"], r["flash"], r["symbol"]))
        return rows[-last_n:] if last_n else rows

    def pack(self, r: dict, decision_hhmm: str = "10:00") -> EvidencePack:
        day = date.fromisoformat(r["day"])
        h, m = map(int, decision_hhmm.split(":"))
        t = datetime.combine(day, dtime(h, m), ET)
        cik = int(self.companies[r["symbol"]]["cik"])
        items: list[Evidence] = []
        news_p = self.d / "news_cache" / f"{r['accession']}.json"
        heads = [x for x in (json.loads(news_p.read_text()) if news_p.exists() else [])
                 if datetime.fromisoformat(x["created"]) <= t]
        if heads:
            items.append(Evidence(f"E{len(items) + 1}", "vendor_headline", "news vendor headlines (Benzinga via Alpaca)",
                                  max(datetime.fromisoformat(x["created"]) for x in heads),
                                  " ".join(f"[{datetime.fromisoformat(x['created']).strftime('%H:%M')}] {x['headline']}"
                                           for x in heads), redistributable=False))
        missing = []
        if self.edgar is not None:
            for builder in (lambda eid: filing_item(self.edgar, cik, r["accession"], eid, t),
                            lambda eid: history_item(self.edgar, cik, eid, t)):
                it = builder(f"E{len(items) + 1}")
                if it is None:
                    missing.append("filing or financial history not available by the decision time")
                else:
                    items.append(it)
        else:
            missing.append("SEC text not fetched (no declared contact)")
        P = self.prices
        mv, sec_mv, spy_mv = (P.move_by(s, day, dtime(h, m)) for s in (r["symbol"], r["sector"], "SPY"))
        pc = P.prev_close(r["symbol"], day)
        f = lambda x: "n/a" if x is None else f"{x:+.2f}%"  # noqa: E731
        items.append(Evidence(f"E{len(items) + 1}", "price", "consolidated 15-minute bars (SIP), completed by the decision time",
                              t, f"{r['symbol']} prior close ${pc:.2f}. Move from the prior close to {decision_hhmm}: "
                                 f"{f(mv)}. Sector ETF {r['sector']}: {f(sec_mv)}. SPY: {f(spy_mv)}. Volume 09:30-"
                                 f"{decision_hhmm} was {r['volRatio']:.2f} times the 20-day average daily volume."
                              if pc else "price history unavailable"))
        items.append(research_item(f"E{len(items) + 1}"))
        return EvidencePack(event_id=f"{r['symbol']}:{r['accession']}", symbol=r["symbol"], sector_etf=r["sector"],
                            decision_time=t, items=items,
                            structured={"surprise": r["surprise"], "react": r["react"], "move_pct": mv,
                                        "sector_move_pct": sec_mv, "prev_close": pc, "missing": missing,
                                        "day": r["day"], "fold": r["fold"]})


# ------------------------------------------------------------------ forward (live EDGAR)
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include"
        "&start={start}&count=100&output=atom")
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def parse_feed(xml: str) -> list[dict]:
    out = []
    for e in _ENTRY.findall(xml):
        title = re.search(r"<title>(.*?)</title>", e, re.S)
        link = re.search(r'<link[^>]*href="([^"]+)"', e)
        upd = re.search(r"<updated>(.*?)</updated>", e)
        summ = re.search(r"<summary[^>]*>(.*?)</summary>", e, re.S)
        cik = re.search(r"\((\d{10})\)", title.group(1) if title else "")
        acc = re.search(r"(\d{10}-\d{2}-\d{6})", link.group(1) if link else "")
        if not (title and cik and acc and upd):
            continue
        out.append({"form": title.group(1).split(" - ")[0].strip(), "cik": int(cik.group(1)),
                    "accession": acc.group(1), "updated": datetime.fromisoformat(upd.group(1)).astimezone(ET),
                    "items": re.findall(r"Item (\d\.\d\d)", summ.group(1) if summ else "")})
    return out


class EdgarObserver:
    def __init__(self, edgar: LiveEdgar, universe: list[str]):
        self.edgar = edgar
        tm = edgar.ticker_map()
        self.by_cik = {tm[s]: s for s in universe if s in tm}

    def new_filings(self, seen: set[str], pages: int = 3) -> list[dict]:
        out = []
        for p in range(pages):
            for f in parse_feed(self.edgar.fresh(FEED.format(start=100 * p)).decode("latin-1")):
                if f["form"] == "8-K" and f["cik"] in self.by_cik and f["accession"] not in seen:
                    out.append(f | {"symbol": self.by_cik[f["cik"]]})
        uniq = {f["accession"]: f for f in out}
        return sorted(uniq.values(), key=lambda f: f["updated"])

    def pack(self, f: dict, now: datetime) -> EvidencePack:
        cik = f["cik"]
        items = []
        it = filing_item(self.edgar, cik, f["accession"], "E1", now)
        if it is None:
            raise FairAccessError(f"filing {f['accession']} has no readable document yet")
        items.append(it)
        h = history_item(self.edgar, cik, f"E{len(items) + 1}", now)
        if h is not None:
            items.append(h)
        items.append(research_item(f"E{len(items) + 1}"))
        sic = self.edgar.submissions(cik).get("sic")
        return EvidencePack(event_id=f"{f['symbol']}:{f['accession']}", symbol=f["symbol"], sector_etf=sector_etf(sic),
                            decision_time=now, items=items,
                            structured={"items": f["items"], "price_available": False,
                                        "missing": ["no price feed: needs a market-data key that was never exposed"]})


class AlpacaPrices:
    """Historical consolidated (SIP) prices with the owner's own, rotated Alpaca key: read-only market data, free
    on the Basic plan for data older than 15 minutes. The exposed key is refused. Results are cached per run."""

    def __init__(self, key_id: str, secret: str, client=None):
        refuse_exposed_key(key_id)
        if client is None:
            from alpaca.data.historical import StockHistoricalDataClient
            client = StockHistoricalDataClient(key_id, secret)
        self.c = client
        self._cache: dict = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def quote(self, s: str, t: datetime) -> Optional[Quote]:
        def fetch():
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockQuotesRequest
            df = self.c.get_stock_quotes(StockQuotesRequest(symbol_or_symbols=s, start=t, end=t + timedelta(seconds=60),
                                                            feed=DataFeed.SIP)).df
            if df is None or df.empty:
                return None
            ok = df[(df["bid_price"] > 0) & (df["ask_price"] > df["bid_price"])]
            if ok.empty:
                return None
            return Quote(s, float(statistics.median(ok["bid_price"])), float(statistics.median(ok["ask_price"])), t,
                         self.prev_close(s, t.astimezone(ET).date()))
        return self._memo(("q", s, t.isoformat()), fetch)

    def bars15(self, s: str, day: date) -> pd.DataFrame:
        def fetch():
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            st = datetime.combine(day, dtime(9, 30), ET)
            df = self.c.get_stock_bars(StockBarsRequest(symbol_or_symbols=s, start=st,
                                                        end=st + timedelta(hours=6, minutes=30),
                                                        timeframe=TimeFrame(15, TimeFrameUnit.Minute),
                                                        feed=DataFeed.SIP)).df
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.xs(s, level=0) if isinstance(df.index, pd.MultiIndex) else df
            df.index = pd.to_datetime(df.index).tz_convert(ET)
            return df[["open", "high", "low", "close", "volume"]].astype(float)
        return self._memo(("b", s, day.isoformat()), fetch)

    def prev_close(self, s: str, day: date) -> Optional[float]:
        """The last regular-session close before `day` (daily bars over the previous two weeks)."""
        def fetch():
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            df = self.c.get_stock_bars(StockBarsRequest(symbol_or_symbols=s, timeframe=TimeFrame.Day, feed=DataFeed.SIP,
                                                        start=datetime.combine(day - timedelta(days=14), dtime(0), ET),
                                                        end=datetime.combine(day, dtime(0), ET))).df
            if df is None or df.empty:
                return None
            df = df.xs(s, level=0) if isinstance(df.index, pd.MultiIndex) else df
            before = df[pd.to_datetime(df.index).tz_convert(ET).date < day]
            return float(before["close"].iloc[-1]) if not before.empty else None
        return self._memo(("pc", s, day.isoformat()), fetch)

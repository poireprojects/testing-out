"""Company-disclosure events for research. SEC EDGAR access follows the fair-access rules.

SEC fair access:
* Automated requests must declare a contact (a name and an email address) in the User-Agent header.
* At most 10 requests per second; this client stays at 4.
* No scraping around access limits.

The contact is passed in by the caller (environment variable SEC_USER_AGENT in the scripts). It is never
stored in the repository.

Timestamps kept for every event. They are never inferred from price movement:
* release_time: earliest verifiable public release (a news vendor headline), or None if unverified
* acceptance_time: SEC EDGAR acceptance (from the filing header, US/Eastern)
* receipt_time: when the app could first have had the information, given polling and stream latency
* actionable_time: the first regular-session minute in which an order could be placed
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..clock import ET, MarketCalendar

SEC_MIN_INTERVAL = 0.25          # seconds between requests (4/s; SEC allows 10/s)
EDGAR_POLL_SECONDS = 60          # assumed production polling interval for new filings
NEWS_STREAM_SECONDS = 5          # assumed latency of a news websocket push
DECISION_SECONDS = 30            # the app's scan/decision loop before an order can go out


class FairAccessError(RuntimeError):
    pass


class Edgar:
    def __init__(self, user_agent: str, cache_dir: Path):
        if not user_agent or "@" not in user_agent:
            raise FairAccessError("SEC fair access: the User-Agent must declare a contact email (SEC_USER_AGENT)")
        self.ua = user_agent
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._last = 0.0
        self.requests = 0

    def get(self, url: str) -> bytes:
        key = self.cache / hashlib.sha256(url.encode()).hexdigest()[:24]
        if key.exists():
            return key.read_bytes()
        for attempt in range(4):
            wait = SEC_MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                self.requests += 1
                key.write_bytes(data)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise FairAccessError(f"SEC refused the request (403): {url}") from e
                if e.code in (429, 503):
                    time.sleep(2 * 2 ** attempt)        # back off; never retry faster than allowed
                    continue
                raise
            except urllib.error.URLError:
                time.sleep(2 * 2 ** attempt)
        raise RuntimeError(f"EDGAR request failed: {url}")

    def ticker_map(self) -> dict[str, int]:
        d = json.loads(self.get("https://www.sec.gov/files/company_tickers.json"))
        return {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}

    def submissions(self, cik: int) -> dict:
        return json.loads(self.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))

    def header(self, cik: int, accession: str) -> dict:
        nod = accession.replace("-", "")
        txt = self.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{accession}-index-headers.html").decode("latin-1")
        return parse_header(txt)

    def document(self, cik: int, accession: str, filename: str) -> str:
        nod = accession.replace("-", "")
        return self.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{filename}").decode("latin-1", "ignore")


def parse_header(txt: str) -> dict:
    """EDGAR -index-headers page: acceptance time (Eastern), items, and the attached documents."""
    t = txt.replace("&lt;", "<").replace("&gt;", ">")
    m = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", t)
    acc = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=ET) if m else None
    items = re.findall(r"ITEM INFORMATION:\s*([^\n<]+)", t)
    docs = [{"type": a.strip(), "filename": b.strip()} for a, b in
            re.findall(r"<TYPE>([^\n<]+)\s*<SEQUENCE>[^\n<]*\s*<FILENAME>([^\n<]+)", t)]
    if not docs:
        types = re.findall(r"<TYPE>([^\n<]+)", t)
        files = re.findall(r"<FILENAME>([^\n<]+)", t)
        docs = [{"type": a.strip(), "filename": b.strip()} for a, b in zip(types[1:], files)]
    return {"acceptance": acc, "items": [i.strip() for i in items], "documents": docs}


def recent_filings(sub: dict, forms=("8-K",)) -> list[dict]:
    r = sub.get("filings", {}).get("recent", {})
    out = []
    for i, f in enumerate(r.get("form", [])):
        if f in forms:
            out.append({k: r[k][i] for k in ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items",
                                              "primaryDocument") if k in r})
    return out


def json_acceptance(s: str) -> datetime:
    """data.sec.gov acceptanceDateTime ('...Z') is UTC — checked against the Eastern filing header."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET)


TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*([ap])\.?m\.?\s*(?:\(?\s*(ET|EST|EDT|Eastern|CT|CST|CDT|PT|PST|PDT)\b)?", re.I)


def stated_times(text: str, limit: int = 6000) -> list[str]:
    """Times of day mentioned near the top of a press release (usually the conference-call time, NOT the
    release time; reported only so the audit can say how rarely a release states its own time)."""
    plain = re.sub(r"<[^>]+>", " ", text[: limit * 4])
    plain = re.sub(r"\s+", " ", plain)[:limit]
    return [m.group(0) for m in TIME_RE.finditer(plain)]


def session_phase(t: datetime, cal: MarketCalendar) -> str:
    s = cal.session(t.astimezone(ET).date())
    if s is None:
        return "closed day"
    if t < s.open:
        return "pre-market"
    if t < s.close:
        return "regular hours"
    return "after hours"


def actionable_time(receipt: datetime, cal: MarketCalendar) -> datetime:
    """First regular-session minute at which an order could be placed after the information arrived.
    Inside regular hours (until 10 minutes before the close): the next minute after the decision delay.
    Before the open: the first full minute after the opening auction. Later: the next session's first minute."""
    t = (receipt + timedelta(seconds=DECISION_SECONDS)).astimezone(ET)
    s = cal.session(t.date())
    if s is not None and s.open <= t < s.close - timedelta(minutes=10):
        return t.replace(second=0, microsecond=0) + timedelta(minutes=1)
    if s is not None and t < s.open:
        return s.open + timedelta(minutes=1)
    after = s.close + timedelta(seconds=1) if s is not None else t
    return cal.next_session(max(after, t)).open + timedelta(minutes=1)


@dataclass
class EventTimes:
    release: Optional[datetime]
    acceptance: datetime
    receipt: datetime
    actionable: datetime
    release_source: str

    @classmethod
    def build(cls, acceptance: datetime, release: Optional[datetime], cal: MarketCalendar,
              release_source: str = "") -> "EventTimes":
        via_sec = acceptance + timedelta(seconds=EDGAR_POLL_SECONDS)
        via_news = release + timedelta(seconds=NEWS_STREAM_SECONDS) if release is not None else None
        receipt = min(via_sec, via_news) if via_news is not None else via_sec
        return cls(release, acceptance, receipt, actionable_time(receipt, cal), release_source)


SIC_SECTOR = [((1300, 1399), "XLE"), ((2900, 2999), "XLE"), ((2830, 2836), "XLV"), ((3840, 3851), "XLV"),
              ((8000, 8099), "XLV"), ((2800, 2899), "XLB"), ((1000, 1099), "XLB"), ((3570, 3579), "XLK"),
              ((3660, 3699), "XLK"), ((7370, 7379), "XLK"), ((3820, 3829), "XLK"), ((4800, 4899), "XLC"),
              ((7800, 7999), "XLC"), ((4900, 4999), "XLU"), ((6798, 6798), "XLRE"), ((6500, 6599), "XLRE"),
              ((6000, 6799), "XLF"), ((2000, 2199), "XLP"), ((5400, 5499), "XLP"), ((2840, 2844), "XLP"),
              ((5000, 5999), "XLY"), ((3710, 3716), "XLY"), ((7000, 7099), "XLY"), ((5800, 5899), "XLY"),
              ((3720, 3729), "XLI"), ((4000, 4799), "XLI"), ((1500, 1799), "XLI"), ((3400, 3569), "XLI")]


def sector_etf(sic) -> str:
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return "SPY"
    for (a, b), etf in SIC_SECTOR:
        if a <= s <= b:
            return etf
    return "SPY"


# ------------------------------------------------------------------ matching a disclosure to its first public release
# A release headline must look like the vendor's own earnings flash ("X Q1 Adj. EPS $0.75 Beats $0.72 Estimate")
# or a "reports Q3 results" line, and must name the company. v1 of this audit matched loose keywords and
# picked up previews and market wrap-ups; those are rejected here.
EARN_FLASH = re.compile(r"\b(adj\.?\s+)?EPS\b.*\b(beats?|miss(es)?|in-line|estimate|vs\.?|up|down)\b"
                        r"|\b(reports?|posts?|announces?)\b.*\b(Q[1-4]|first|second|third|fourth|quarter|fiscal)\b.*\b(results|earnings)\b", re.I)
NOT_RELEASE = re.compile(r"\b(ahead|preview|insights|expect|what to|scheduled|slate|futures|stock market|market reaction|"
                         r"implied move|options|could|will report|to report|set to|earnings date|whisper)\b", re.I)
ITEM_WORDS = {
    "5.02": r"\b(CEO|CFO|chief|president|director|board|resign|retire|steps? down|appoint|names?|depart|successor)\b",
    "1.01": r"\b(agreement|deal|contract|partnership|acquire|acquisition|merger|credit|facility|notes?)\b",
    "2.01": r"\b(acquisition|acquires?|completes?|closes?|divest|sale)\b",
    "2.03": r"\b(notes?|offering|debt|credit|loan|bonds?)\b",
    "8.01": r"\b(announces?)\b",
    "7.01": r"\b(announces?|presentation|update)\b",
    "5.07": r"\b(shareholders?|stockholders?|vote|annual meeting)\b",
    "3.02": r"\b(shares|offering|private placement)\b",
}




def _names_company(headline: str, name: str, symbol: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'.-]+", name or "") if len(w) >= 3 and w.lower() not in
             {"inc", "inc.", "corp", "corp.", "corporation", "company", "co.", "holdings", "group", "the", "ltd", "plc", "class"}]
    h = headline.lower()
    return (bool(words) and words[0].lower().strip(".") in h) or re.search(rf"\b{re.escape(symbol)}\b", headline) is not None


def match_release(news: list[dict], acceptance: datetime, items: str, name: str, symbol: str) -> tuple:
    """Earliest headline that IS this disclosure in [acceptance-18h, acceptance+15m]; None = unverified."""
    lo, hi = acceptance - timedelta(hours=18), acceptance + timedelta(minutes=15)
    cands = [n for n in news if "created" in n and lo <= datetime.fromisoformat(n["created"]) <= hi]
    own = [n for n in cands if _names_company(n["headline"], name, symbol) and not NOT_RELEASE.search(n["headline"])]
    if "2.02" in items:
        hits = [n for n in own if EARN_FLASH.search(n["headline"])]
        kind = "earnings flash"
    else:
        pats = [ITEM_WORDS[i] for i in items.split(",") if i in ITEM_WORDS and i not in ("8.01", "7.01")]
        hits = [n for n in own if pats and any(re.search(p, n["headline"], re.I) for p in pats)]
        kind = "item headline"
    if not hits:
        return None, "unverified (no headline that is clearly this disclosure)", len(cands)
    first = hits[0]
    return datetime.fromisoformat(first["created"]), f"{kind}: {first['headline'][:90]}", len(cands)


FLASH_EPS = re.compile(r"\bEPS\b[^,]*?\b(beats?|miss(?:es)?|in-line)\b", re.I)
FLASH_SALES = re.compile(r"\b(sales|revenue)\b[^,]*?\b(beats?|miss(?:es)?|in-line)\b", re.I)


def parse_flash(headline: str) -> dict:
    """Surprise direction as the vendor states it ("EPS $0.75 Beats $0.72 Estimate, Sales $14.4B Miss ...").
    None when the headline gives no comparison with an estimate."""
    def norm(m):
        if not m:
            return None
        w = m.group(m.lastindex).lower()
        return "beat" if w.startswith("beat") else "miss" if w.startswith("miss") else "inline"
    return {"eps": norm(FLASH_EPS.search(headline)), "sales": norm(FLASH_SALES.search(headline))}

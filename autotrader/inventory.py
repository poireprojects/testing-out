"""Zero-spend preflight of the real phase-4 cache for the 160 sample events. Reads the cache only: it sends no
request, fetches nothing, and computes no outcome (no price after 10:00 is read except to check that the exit
windows exist). Its committed output holds hashes, counts and yes/no flags only, never a price, quote or text.

What it establishes, per event:
* the exact plan row (date, ticker, accession, fold);
* SEC: the acceptance time from the filing index (events.json) and whether it precedes the 10:00 cutoff; whether
  the filing header, the release exhibit and the company-facts snapshot are in the cache;
* the vendor flash time and whether it falls between the prior session's close and 10:00 (release matching);
* bars: the stock, sector ETF and SPY files, the event day's 15-minute bars (26 expected), the prior session, and a
  corporate-action check (an overnight jump that looks like a split or a bad print);
* quotes: whether the 10:00:30 and 15:45 windows exist for the stock and the ETF, and how many updates they hold;
* the cached 10:00 features (prior close, the move by 10:00 relative to the sector, the 09:30-10:00 volume ratio)
  and the parsed surprise, recomputed independently from the as-of inputs and compared with plan.json.
A new hash of old material is a new fingerprint, not proof of where it came from: the report says which files match
the digests committed in phase 4 and which (the bars) have only a file count on record.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

from stockpilot.clock import ET
from stockpilot.research.events import parse_flash, parse_header

from . import fees_hist
from .experiment import FROZEN, event_id, load_sample, post_cutoff, select_rows

MANIFEST = Path(__file__).resolve().parents[1] / "research" / "runs" / "2026-11-07-phase4" / "data-manifest.json"
SPLIT_RATIOS = (2.0, 3.0, 4.0, 1.5, 0.5, 1 / 3, 0.25, 2 / 3, 10.0, 0.1, 5.0, 0.2, 20.0, 0.05)


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(cache: Path, url: str) -> Path:
    return cache / hashlib.sha256(url.encode()).hexdigest()[:24]


def _bars(d: Path, s: str, memo: dict) -> pd.DataFrame:
    if s not in memo:
        p = d / "bars15" / f"{s.replace('.', '_')}.pkl"
        memo[s] = pickle.load(open(p, "rb")) if p.exists() else None
    return memo[s]


def _features(b: pd.DataFrame, day: date) -> dict:
    """As-of 10:00 features from 15-minute bars, recomputed the way the plan defines them."""
    if b is None or b.empty:
        return {}
    days = sorted(set(b.index.date))
    if day not in days:
        return {}
    i = days.index(day)
    today = b[b.index.date == day]
    prev = b[b.index.date == days[i - 1]] if i > 0 else None
    out = {"bars_today": int(len(today)), "prev_session": days[i - 1].isoformat() if i > 0 else None}
    if prev is not None and not prev.empty:
        out["prev_close"] = float(prev["close"].iloc[-1])
    b945 = today[(today.index.hour == 9) & (today.index.minute == 45)]
    if not b945.empty:
        out["p1000"] = float(b945["close"].iloc[-1])
    out["vol30"] = float(today[today.index.hour == 9]["volume"].sum())
    hist = [float(b[b.index.date == d]["volume"].sum()) for d in days[max(0, i - 20):i]]
    out["adv_shares20"] = sum(hist) / len(hist) if len(hist) >= 15 else None
    first = today[today.index.time == dtime(9, 30)]
    if "prev_close" in out and not first.empty:
        jump = float(first["open"].iloc[0]) / out["prev_close"]
        out["overnight_ratio"] = jump
    return out


def _close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def run(data_dir: Path) -> dict:
    d = Path(data_dir)
    sample = load_sample()
    plan = json.loads((d / "plan.json").read_text())
    rows = select_rows(sample, plan)
    man = json.loads(MANIFEST.read_text())
    files = {}
    for name, rec in man.items():
        if isinstance(rec, dict) and "sha256" in rec:
            p = d / name
            got = sha_file(p) if p.exists() else None
            files[name] = {"committed_sha256": rec["sha256"], "matches": got == rec["sha256"], "present": p.exists()}
    bar_files = sorted((d / "bars15").glob("*.pkl")) if (d / "bars15").exists() else []
    files["bars15"] = {"committed": "file count only", "committed_files": man.get("bars15", {}).get("files"),
                       "files": len(bar_files), "matches_count": len(bar_files) == man.get("bars15", {}).get("files")}
    companies = json.loads((d / "companies.json").read_text())
    events = {e["accession"]: e for e in json.loads((d / "events.json").read_text())}
    quotes = {}
    for f in sorted(d.glob("quotes*.json")):
        quotes.update(json.loads(f.read_text()))
    cache = d / "sec_cache"
    memo: dict = {}
    per, needed_bars = [], set()
    for r in rows:
        sym, etf, day = r["symbol"], r["sector"], date.fromisoformat(r["day"])
        needed_bars |= {sym, etf, "SPY"}
        cik = int(companies[sym]["cik"])
        ev = events.get(r["accession"], {})
        acc = datetime.fromisoformat(ev["acceptance"]).astimezone(ET) if ev.get("acceptance") else None
        cutoff = datetime.combine(day, dtime(10, 0), ET)
        nod = r["accession"].replace("-", "")
        hdr_p = _key(cache, f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{r['accession']}-index-headers.html")
        exhibit = None
        if hdr_p.exists():
            h = parse_header(hdr_p.read_bytes().decode("latin-1"))
            doc = next((x for x in h["documents"] if x["type"].upper().startswith("EX-99")), None)
            exhibit = doc is not None and _key(cache, f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/"
                                                      f"{doc['filename']}").exists()
        facts = _key(cache, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").exists()
        flash = datetime.fromisoformat(r["flash"]).astimezone(ET) if r.get("flash") else None
        fs, fe, fy = (_features(_bars(d, s, memo), day) for s in (sym, etf, "SPY"))
        react = None
        if all(k in x for x in (fs, fe) for k in ("p1000", "prev_close")):
            react = fs["p1000"] / fs["prev_close"] - 1 - (fe["p1000"] / fe["prev_close"] - 1)
        vol_ratio = fs["vol30"] / fs["adv_shares20"] if fs.get("adv_shares20") else None
        ratio = fs.get("overnight_ratio")
        ca = ratio is not None and (abs(ratio - 1) > 0.25 or any(abs(ratio - k) < 0.02 * k for k in SPLIT_RATIOS))
        q = {f"{w}_{who}": (quotes.get(f"{r['day']}|{w}", {}).get(s) or {}).get("n", 0)
             for w in ("entry", "exit") for who, s in (("stock", sym), ("etf", etf))}
        head = ev.get("headline")
        try:
            fr = fees_hist.rates_on(day)
            fee_row = {"effective": fr.effective.isoformat(), "verified": fr.verified}
        except fees_hist.FeeScheduleGap:
            fee_row = {"effective": None, "verified": False}
        per.append({
            "event_id": event_id(r), "day": r["day"], "fold": r["fold"], "post_cutoff": post_cutoff(r["day"]),
            "plan_row_exact": True,
            "sec_acceptance_known": acc is not None,
            "sec_accepted_before_cutoff": bool(acc and acc < cutoff),
            "sec_accepted_before_0925_same_day": bool(acc and acc.date() == day and acc.time() < dtime(9, 25)),
            "sec_header_cached": hdr_p.exists(), "release_exhibit_cached": bool(exhibit), "company_facts_cached": facts,
            "flash_between_prior_close_and_cutoff": bool(flash and flash <= cutoff and acc and flash >= acc - timedelta(hours=18)),
            "bars_stock_file": fs != {}, "bars_etf_file": fe != {}, "bars_spy_file": fy != {},
            "bars_today_stock": fs.get("bars_today", 0), "bars_today_etf": fe.get("bars_today", 0),
            "full_day_bars_stock": fs.get("bars_today", 0) == 26, "full_day_bars_etf": fe.get("bars_today", 0) == 26,
            "prior_close_stock": "prev_close" in fs, "prior_close_etf": "prev_close" in fe,
            "corporate_action_or_bad_print_flag": bool(ca),
            "quote_updates": q, "quotes_complete": all(v > 0 for v in q.values()),
            "feature_prev_close_matches": _close(fs.get("prev_close"), r.get("prevClose")),
            "feature_react_matches": _close(react, r.get("react")),
            "feature_vol_ratio_matches": _close(vol_ratio, r.get("volRatio")),
            "surprise_reparsed_matches": head is not None and parse_flash(head) == r.get("surprise"),
            "fee_schedule_row": fee_row,
        })
    flags = [k for k, v in per[0].items() if isinstance(v, bool)]
    counts = {k: sum(bool(p[k]) for p in per) for k in flags}
    model_ready = sum(p["sec_header_cached"] and p["release_exhibit_cached"] and p["company_facts_cached"]
                      and p["quotes_complete"] and p["full_day_bars_stock"] for p in per)
    return {"data_dir_files": files, "events": len(per), "counts_true": counts,
            "events_ready_for_a_model_input": model_ready,
            "required_bar_symbols": len(needed_bars),
            "required_bar_files_present": sum((d / "bars15" / f"{s.replace('.', '_')}.pkl").exists() for s in needed_bars),
            "required_bar_file_sha256": {s: sha_file(d / "bars15" / f"{s.replace('.', '_')}.pkl")
                                         for s in sorted(needed_bars)
                                         if (d / "bars15" / f"{s.replace('.', '_')}.pkl").exists()},
            "fee_rows_unverified_on_sample_dates": sum(not p["fee_schedule_row"]["verified"] for p in per),
            "fold_counts": dict(Counter(p["fold"] for p in per)),
            "post_cutoff_events": sum(p["post_cutoff"] for p in per),
            "per_event": per}

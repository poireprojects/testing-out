"""Prices and corporate actions: normalize a vendor's unadjusted daily bars and its corporate-action responses, and
prove which securities and dates the action collection covers.

Raw formats (one bars file per symbol; one file per corporate-action response page):

    {"symbol": "ABC", "adjustment": "raw", "bars": [{"t": "YYYY-MM-DD", "o": "12.34", "c": "12.56"}, ...]}

    source_url  <endpoint>?symbols=A,B,...&start=YYYY-MM-DD&end=YYYY-MM-DD[&page_token=T][&limit=N]
    body        {"corporate_actions": {"cash_dividends": [{symbol, ex_date, payable_date, rate}],
                 "forward_splits": [{symbol, ex_date, old_rate, new_rate}], "reverse_splits": [...],
                 "name_changes": [{old_symbol, new_symbol, process_date}], <other kinds>},
                 "next_page_token": null | "T"}

The request is the page's recorded source URL (raw-store provenance), so what was asked for is evidence, not a claim.
The page shape is modelled on the vendor's paginated corporate-actions response; it has not been checked against a
real response in this version (none is cached).

Coverage (`action_coverage`). A query is identified by its endpoint, symbols, start, end and type filter. Its pages
form a chain: the first has no page token, each next page's token is the previous page's `next_page_token`, and the
last has `next_page_token: null`. Only a complete chain covers anything: its symbols, from its start to the earlier
of its end and the day before its first page was retrieved (an action dated after the retrieval may not be known
yet: stale). A missing response, an unfinished or broken chain, an error body, a type filter (the query must ask for
every kind, or unsupported actions could be missed) or a symbol nobody queried leaves those securities and dates
UNCOVERED. A complete chain whose pages list nothing is verified absence of actions; an empty local list by itself is
never coverage. When a registered symbol changed from an old symbol, its dates before the change are covered only if
the old symbol was queried for them too. Coverage is recorded per symbol as date intervals with the chains that
prove them; uses (accounting, outcomes, decision inputs) check the dates they need (`data.MarketData.actions_gap`).

Rules for the actions themselves:
* only unadjusted bars are accepted (`adjustment` must be "raw"). A split inside a series must show up as a price
  step at its ex-date; a series that moves across a split ex-date by less than half the split's ratio already looks
  split-adjusted and is rejected, because the ledger applies splits itself and would otherwise adjust twice;
* a symbol change merges the old and new symbols' bars under the registered symbol; two different bars for one day
  are refused;
* dividends keep ex and pay dates (a pay date before its ex-date is refused); splits keep their ratio;
* rows repeated by overlapping queries are kept once (by vendor id, or by content); one id with two contents is
  refused;
* any other action kind (mergers, spin-offs, delistings, redemptions, ...) is not supported: it is listed in
  `unsupported`, and the pilot treats it as a data gap for a held stock and as a reproducible exclusion for a
  candidate. An unsupported action without a date leaves its symbol uncovered. Nothing is modelled that the code does
  not implement.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .raw import RawFile

SUPPORTED = ("cash_dividends", "forward_splits", "reverse_splits", "name_changes")


class MarketError(ValueError):
    pass


# ------------------------------------------------------------------ intervals (inclusive calendar dates)
def merge(intervals: list) -> list[list[str]]:
    out: list[list[date]] = []
    for s, e in sorted((date.fromisoformat(str(a)), date.fromisoformat(str(b))) for a, b in intervals):
        if e < s:
            continue
        if out and s <= out[-1][1] + timedelta(days=1):
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [[s.isoformat(), e.isoformat()] for s, e in out]


def covers(intervals: list, first: date, last: date) -> bool:
    """Whether the merged `intervals` contain every date from `first` to `last` (True when last < first)."""
    if last < first:
        return True
    return any(date.fromisoformat(s) <= first and last <= date.fromisoformat(e) for s, e in merge(intervals))


def _intersect(a: list, b: list) -> list:
    out = []
    for s1, e1 in merge(a):
        for s2, e2 in merge(b):
            s, e = max(s1, s2), min(e1, e2)
            if s <= e:
                out.append([s, e])
    return merge(out)


# ------------------------------------------------------------------ corporate-action pages and coverage
def _query(f: RawFile) -> dict:
    u = urlsplit(f.source_url)
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    syms = sorted({s.strip().upper() for s in q.get("symbols", "").split(",") if s.strip()})
    return {"endpoint": f"{u.scheme}://{u.netloc}{u.path}", "vendor": u.netloc, "symbols": syms,
            "start": q.get("start"), "end": q.get("end"), "types": q.get("types") or None,
            "page_token": q.get("page_token") or None}


def _body(f: RawFile) -> tuple[Optional[dict], str]:
    try:
        j = f.json()
    except (ValueError, UnicodeDecodeError):
        return None, "the response is not JSON"
    if not isinstance(j, dict) or not isinstance(j.get("corporate_actions"), dict):
        why = j.get("message") if isinstance(j, dict) and j.get("message") else "no corporate_actions object"
        return None, f"not a corporate-actions response ({str(why)[:80]})"
    if "next_page_token" not in j:
        return None, "the response does not state its pagination (no next_page_token)"
    return j, ""


def action_coverage(pages: list[RawFile], registered: list[str], renames: dict[str, tuple[str, str]]) -> dict:
    """The coverage record (see the module docstring). `renames`: {registered: (old_symbol, process_date)}."""
    groups: dict[tuple, list] = {}
    for f in pages:
        q = _query(f)
        key = (q["endpoint"], tuple(q["symbols"]), q["start"], q["end"], q["types"])
        groups.setdefault(key, []).append((f, q))
    chains, intervals = [], {s: [] for s in registered}
    for (endpoint, syms, start, end, types), items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        c = {"endpoint": endpoint, "vendor": items[0][1]["vendor"], "symbols": list(syms), "start": start, "end": end,
             "types": types, "pages": [], "status": "complete", "reason": "", "retrieved_at": None,
             "covered_until": None}
        chains.append(c)
        bodies = {}
        for f, q in items:
            b, why = _body(f)
            c["pages"].append({"raw_file": f.relpath, "raw_sha256": f.sha256, "retrieved_at": f.retrieved_at,
                               "page_token": q["page_token"], "next_page_token": (b or {}).get("next_page_token")})
            if b is None:
                c |= {"status": "failed", "reason": f"{f.relpath}: {why}"}
            bodies.setdefault(q["page_token"], []).append(b)
        if c["status"] != "complete":
            continue
        if any(len(v) > 1 for v in bodies.values()):
            c |= {"status": "partial", "reason": "two pages answer the same page token"}
            continue
        tok, used = None, 0
        while True:
            if tok not in bodies:
                c |= {"status": "partial", "reason": "the first page is missing" if tok is None else
                      f"pagination unfinished: page token {tok!r} was not collected"}
                break
            used += 1
            tok = bodies[tok][0]["next_page_token"]
            if tok is None:
                break
            if used > len(bodies):
                c |= {"status": "partial", "reason": "pagination loops"}
                break
        if c["status"] == "complete" and used != len(bodies):
            c |= {"status": "partial", "reason": f"{len(bodies) - used} page(s) outside the pagination chain"}
        if c["status"] != "complete":
            continue
        if types:
            c |= {"status": "partial", "reason": f"filtered by type ({types}): unsupported kinds could be missed"}
            continue
        if not (start and end) or not syms:
            c |= {"status": "partial", "reason": "the query states no symbols or no date range"}
            continue
        retrieved = min(datetime.fromisoformat(p["retrieved_at"]) for p in c["pages"])
        until = min(date.fromisoformat(end), retrieved.date() - timedelta(days=1))
        c |= {"retrieved_at": retrieved.isoformat(), "covered_until": until.isoformat()}
        if until < date.fromisoformat(start):
            c |= {"status": "partial", "reason": f"stale: retrieved {retrieved.isoformat()}, before the query's dates"}
            continue
        for s in syms:
            intervals.setdefault(s, []).append([start, until.isoformat()])
    symbols = {}
    for s in registered:
        own = merge(intervals.get(s, []))
        note = ""
        if s in renames:                      # before the change, the old symbol's actions must be covered too
            old, when = renames[s]
            own = _intersect(own, merge(intervals.get(old, []) + [[when, "9999-12-31"]]))
            note = f"dates before the change from {old} on {when} are covered only where {old} was queried"
        symbols[s] = {"intervals": own, "note": note or ("" if own else "no complete corporate-action query covers it")}
    return {"chains": chains, "symbols": symbols,
            "status": "collected" if any(c["status"] == "complete" for c in chains) else
                      ("missing" if not pages else "incomplete"),
            "rule": "covered = complete pagination chains of untyped queries naming the symbol, from the query start "
                    "to the earlier of its end and the day before retrieval"}


def _rows(pages: list[RawFile]) -> dict[str, list[dict]]:
    """Action rows of every readable page, kind by kind, with rows repeated across queries kept once."""
    out: dict[str, dict] = {}
    for f in pages:
        b, _ = _body(f)
        if b is None:
            continue
        for kind, rows in b["corporate_actions"].items():
            for r in rows or []:
                rid = r.get("id") or json.dumps(r, sort_keys=True)
                old = out.setdefault(kind, {}).get(rid)
                if old is not None and old != r:
                    raise MarketError(f"{kind} {rid}: two different records with one id")
                out[kind][rid] = r
    return {k: [v[i] for i in sorted(v)] for k, v in out.items()}


# ------------------------------------------------------------------ normalize
def normalize(bars_files: list[RawFile], action_pages: list[RawFile], registered: list[str]) -> dict:
    acts = _rows(action_pages)
    renames = {r["old_symbol"]: r for r in acts.get("name_changes", [])}
    canon = {}
    for sym in registered:
        canon[sym] = sym
        for old, r in renames.items():
            if r["new_symbol"] == sym:
                canon[old] = sym
            if old == sym:
                canon[r["new_symbol"]] = sym

    def reg(s: str) -> Optional[str]:
        return canon.get(s)

    prices: dict[str, dict] = {}
    prov = []
    for f in bars_files:
        j = f.json()
        if j.get("adjustment") != "raw":
            raise MarketError(f"{f.relpath}: bars are '{j.get('adjustment')}', not unadjusted ('raw')")
        sym = reg(j["symbol"])
        if sym is None:
            continue
        series = prices.setdefault(sym, {})
        for b in j["bars"]:
            row = {"open": str(Decimal(str(b["o"]))), "close": str(Decimal(str(b["c"])))}
            old = series.get(b["t"])
            if old is not None and old != row:
                raise MarketError(f"{sym} {b['t']}: two different bars (symbol change overlap)")
            series[b["t"]] = row
        prov.append(f.provenance() | {"symbol": j["symbol"], "registered_as": sym})
    splits, dividends, unsupported = [], [], []
    for d in acts.get("cash_dividends", []):
        sym = reg(d["symbol"])
        if sym is None:
            continue
        if d["payable_date"] < d["ex_date"]:
            raise MarketError(f"{sym} dividend pays {d['payable_date']} before its ex-date {d['ex_date']}")
        dividends.append({"symbol": sym, "ex_date": d["ex_date"], "pay_date": d["payable_date"],
                          "amount": str(Decimal(str(d["rate"])))})
    for kind in ("forward_splits", "reverse_splits"):
        for s in acts.get(kind, []):
            sym = reg(s["symbol"])
            if sym is None:
                continue
            splits.append({"symbol": sym, "ex_date": s["ex_date"], "num": int(s["new_rate"]), "den": int(s["old_rate"])})
    for kind, rows in acts.items():
        if kind in SUPPORTED:
            continue
        for r in rows:
            sym = reg(r.get("symbol") or r.get("acquiree_symbol") or r.get("old_symbol") or "")
            if sym is not None:
                unsupported.append({"symbol": sym, "kind": kind,
                                    "date": r.get("effective_date") or r.get("ex_date") or r.get("process_date"),
                                    "detail": {k: v for k, v in r.items() if k != "symbol"}})
    for s in splits:                                          # double-adjustment check
        series = prices.get(s["symbol"], {})
        days = sorted(series)
        before = [d for d in days if d < s["ex_date"]]
        if s["ex_date"] in series and before:
            observed = Decimal(series[s["ex_date"]]["open"]) / Decimal(series[before[-1]]["close"])
            expected = Decimal(s["den"]) / Decimal(s["num"])
            if abs(math.log(float(observed))) < abs(math.log(float(expected))) / 2:
                raise MarketError(f"{s['symbol']}: no price step at the {s['num']}:{s['den']} split on {s['ex_date']} "
                                  "(the series looks already split-adjusted; refusing double adjustment)")
    changes = [{"old_symbol": o, "new_symbol": r["new_symbol"], "process_date": r.get("process_date"),
                "registered_as": reg(r["new_symbol"]) or reg(o)} for o, r in sorted(renames.items())
               if reg(r["new_symbol"]) or reg(o)]
    into = {c["registered_as"]: (c["old_symbol"], c["process_date"]) for c in changes
            if c["new_symbol"] == c["registered_as"] and c["process_date"]}
    coverage = action_coverage(action_pages, registered, into)
    for u in unsupported:                                     # an undated action cannot be placed in time
        if not u["date"]:
            coverage["symbols"][u["symbol"]] = {"intervals": [], "note": f"an undated {u['kind']} record: its dates "
                                                                         "cannot be placed, so nothing is covered"}
    return {"prices": prices, "actions": {"splits": sorted(splits, key=lambda x: (x["ex_date"], x["symbol"])),
                                          "dividends": sorted(dividends, key=lambda x: (x["ex_date"], x["symbol"])),
                                          "unsupported": sorted(unsupported, key=lambda x: (str(x["date"]), x["symbol"])),
                                          "symbol_changes": changes, "coverage": coverage},
            "provenance": prov + [f.provenance() for f in action_pages]}

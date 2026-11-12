"""Raw store -> verified pilot data directory (offline; no network, no credentials).

    build_data(RAW_DIR, DATA_DIR, freeze=..., series_id=..., series_name=...)

1. Universe: the fund's submissions index, its N-PORT-P files and any collected filing headers ->
   `nport.select_report`, which proves the selected report is the target series' latest one public before the
   freeze (every NPORT-P and NPORT-P/A in the index is reconciled; see `adapters.nport`) -> `nport.review_table` ->
   `nport.frozen_universe` from the saved mapping review (exactly 12 rows, no duplicate mapping). The review table and
   the reconciliation are written out for people to check; an unapproved or unresolved row stops the build.
2. For each member: its submissions index, filing headers, documents and company facts.
   * Every 10-K, 10-Q and 8-K Item 2.02 in the index is a filing the pack may need. It enters `filings.json` only when
     its header reconciles with the index (accession, issuer CIK, acceptance time) and its document was collected;
     otherwise the reason is recorded in `index.json` and coverage reports it as a collection failure.
   * Passages come from the document by the registered rule (`edgar.passages`); 8-K text from its EX-99.1.
   * Facts for a periodic filing are computed as of that filing's acceptance time, from facts public by then
     (`edgar.facts_as_of`), with fact-level provenance; its share count comes from that filing's own cover page
     (inline XBRL contexts of the collected document). If the collected facts cannot support a trailing value, the
     filing says so (`facts_unavailable`), which coverage treats as unavailable, not as a collection failure. A share
     count or trailing value that exists but is not established (class structure, reporting basis) is recorded as
     `unresolved` in `facts_status` with its reason, and is left out of `facts`. Amended facts count from their own
     acceptance time, so they enter at the next periodic filing.
3. Prices and actions: `market.normalize` over the saved bars and every corporate-action response page; the
   coverage record (which securities and dates the complete responses prove) goes into `actions.json`. With no
   response, nothing is covered, and every use of an action blocks until one is collected.
4. `data.write_data_dir` with `provenance.json` (every raw file used, with URL, retrieval time and SHA-256) and
   `review_table.json` (the N-PORT selection with its reconciliation, and the mapping proposals).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .adapters import edgar, market, nport
from .adapters.raw import RawStore
from .data import write_data_dir
from .registration import BENCHMARK

CIK_RE = re.compile(r"CIK(\d{10})")
ACC_RE = re.compile(r"/(\d{10}-\d{2}-\d{6})-index-headers\.html$")
DOC_RE = re.compile(r"/edgar/data/(\d+)/(\d{18})/([^/]+)$")
M = Decimal(1_000_000)
TWO = Decimal("0.01")
NPORT_KEYS = ("accession", "accepted_at", "report_date", "form", "series_id", "series_name", "public_by", "raw_sha256")


class PipelineError(RuntimeError):
    pass


def _submissions(store: RawStore) -> dict[int, dict]:
    pages: dict[int, list] = {}
    for f in store.of_kind("edgar_submissions"):
        m = CIK_RE.search(f.source_url)
        if not m:
            raise PipelineError(f"{f.relpath}: no CIK in {f.source_url}")
        pages.setdefault(int(m.group(1)), []).append(f)
    out = {}
    for cik, ps in pages.items():
        ps.sort(key=lambda f: ("filings" not in f.json(), f.source_url))
        out[cik] = edgar.parse_submissions(ps)
    return out


def _facts_record(fx: dict) -> tuple[dict, dict, dict, str]:
    """(facts strings for filings.json, their status, provenance, reason if unavailable). A trailing value that no
    method supports makes the filing unavailable; a value that exists but is unresolved (or an unresolved share
    count) is left out of the facts and named in the status."""
    prov = {k: (None if v is None else json.loads(json.dumps(v, default=str))) for k, v in fx.items()}
    missing = [k for k in ("revenue_ttm", "net_income_ttm", "eps_diluted_ttm") if fx[k] is None]
    if missing:
        return {}, {}, prov, "the collected XBRL facts do not support: " + ", ".join(missing)
    facts, status = {}, {}
    out = {"revenue_ttm": ("revenue_ttm_musd", lambda v: str((v / M).quantize(TWO))),
           "net_income_ttm": ("net_income_ttm_musd", lambda v: str((v / M).quantize(TWO))),
           "eps_diluted_ttm": ("eps_diluted_ttm", lambda v: str(v.quantize(TWO))),
           "shares_outstanding": ("shares_outstanding", lambda v: str(v.to_integral_value()))}
    for k, (name, fmt) in out.items():
        v = fx[k]
        if v["status"] == "resolved":
            facts[name] = fmt(v["value"])
        else:
            status[name] = {"status": "unresolved", "reason": v.get("unresolved_reason") or v.get("reason")}
    if "shares_outstanding" in facts:
        facts["share_classes"] = str(fx["shares_outstanding"]["share_classes"])
    return facts, status, prov, ""


def build_data(raw_dir: Path, data_dir: Path, *, freeze: datetime, series_id: str, series_name: str = "") -> dict:
    store = RawStore(raw_dir)
    subs = _submissions(store)
    headers = {ACC_RE.search(f.source_url).group(1): f for f in store.of_kind("edgar_header")}
    docs = {}
    for f in store.of_kind("edgar_document"):
        m = DOC_RE.search(f.source_url)
        docs[(m.group(2), m.group(3))] = f
    facts = {int(CIK_RE.search(f.source_url).group(1)): f for f in store.of_kind("edgar_companyfacts")}
    used = []

    # 1. universe
    reports = {}
    for f in store.of_kind("nport_xml"):
        acc = re.search(r"/(\d{18})/", f.source_url).group(1)
        reports[f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"] = nport.parse_nport(f)
        used.append(f.provenance())
    funds = [s for s in subs.values() if any(e["form"].startswith("NPORT-P") for e in s["entries"])]
    if not funds:
        raise PipelineError("no fund submissions index with an NPORT-P")
    if len(funds) > 1:
        raise PipelineError(f"{len(funds)} submissions indexes list NPORT-P filings: the fund registrant is ambiguous")
    fund = funds[0]
    header_series = {a: edgar.parse_header(h)["series_ids"] for a, h in headers.items()}
    selected = nport.select_report(fund, reports, {"series_id": series_id, "series_name": series_name}, freeze,
                                   {a: v for a, v in header_series.items() if v})
    tick = store.of_kind("edgar_tickers")
    if not tick:
        raise PipelineError("EDGAR's company_tickers.json was not collected")
    table = nport.review_table(selected, tick[0].json(), 12)
    review_files = store.of_kind("universe_review")
    if not review_files:
        raise PipelineError("no mapping review: the universe cannot be frozen (see review_table.json)")
    universe = nport.frozen_universe(table, review_files[0].json(), 12)
    used += [tick[0].provenance(), review_files[0].provenance()] + fund["provenance"]

    # 2. filings, index and facts per member
    filings, index = [], {"symbols": {}, "price_exceptions": []}
    for m in universe["members"]:
        cik, sym = int(m["cik"]), m["symbol"]
        sub = subs.get(cik)
        if sub is None:
            continue                                          # no index: coverage reports a collection failure
        used += sub["provenance"]
        want = [e for e in sub["entries"] if e["form"] in ("10-K", "10-Q")
                or (e["form"] == "8-K" and "2.02" in (e["items"] or ""))]
        problems = {}
        cf = None
        if cik in facts:
            cf = edgar.parse_companyfacts(facts[cik], sub)
            used.append(cf["provenance"])
        for e in want:
            h = headers.get(e["accession"])
            if h is None:
                problems[e["accession"]] = "its filing header was not collected"
                continue
            hd = edgar.parse_header(h)
            bad = edgar.reconcile(e, hd, cik)
            if bad:
                problems[e["accession"]] = "header does not reconcile with the index: " + "; ".join(bad)
                continue
            nod = e["accession"].replace("-", "")
            fname = e["primary_document"]
            if e["form"] == "8-K":
                ex = next((d["filename"] for d in hd["documents"] if d["type"].upper().startswith("EX-99")), None)
                fname = ex or fname
            doc = docs.get((nod, fname))
            if doc is None:
                problems[e["accession"]] = f"document {fname} was not collected"
                continue
            rec = {"symbol": sym, "cik": str(cik), "accession": e["accession"], "form": e["form"],
                   "items": e["items"], "period_end": e["report_date"], "accepted_at": e["accepted_at"],
                   "passages": [{"section": p["section"], "text": p["text"]} for p in edgar.passages(doc.text(), e["form"])],
                   "provenance": {"header": hd["provenance"], "document": doc.provenance(), "index": e["index_page"]}}
            used += [hd["provenance"], doc.provenance()]
            if e["form"] in ("10-K", "10-Q"):
                if cf is None:
                    problems[e["accession"]] = "company facts were not collected"
                    continue
                cover = edgar.inline_facts(doc.text())
                vals, status, prov, why = _facts_record(edgar.facts_as_of(
                    cf, datetime.fromisoformat(e["accepted_at"]), cover, e["accession"]))
                rec |= {"facts": vals, "facts_provenance": prov}
                if status:
                    rec["facts_status"] = status
                if why:
                    rec["facts_unavailable"] = why
            filings.append(rec)
        index["symbols"][sym] = {"cik": str(cik), "retrieved_at": sub["provenance"][0]["retrieved_at"],
                                 "source": sub["provenance"][0]["source_url"],
                                 "filings": [{"accession": e["accession"], "form": e["form"],
                                              "accepted_at": e["accepted_at"], "items": e["items"]} for e in want],
                                 "problems": problems}

    # 3. prices and actions
    bars = store.of_kind("price_bars")
    mk = market.normalize(bars, store.of_kind("corporate_actions"),
                          [BENCHMARK] + [m["symbol"] for m in universe["members"]])
    used += mk["provenance"]

    # 4. write
    uniq = {json.dumps(u, sort_keys=True): u for u in used}
    extra = {"provenance.json": {"raw_manifest_sha256": hashlib.sha256(
                 (Path(raw_dir) / "MANIFEST.json").read_bytes()).hexdigest(),
                 "synthetic": store.synthetic, "files": sorted(uniq.values(), key=lambda u: u["raw_file"])},
             "review_table.json": {"nport": {k: selected[k] for k in NPORT_KEYS},
                                   "reconciliation": selected["reconciliation"], "rules": selected["rule"],
                                   "rows": table}}
    sha = write_data_dir(data_dir, synthetic=store.synthetic,
                         sources={"all": "built by autotrader.invest.pipeline from the raw store",
                                  "raw_synthetic": store.synthetic},
                         prices=mk["prices"], actions=mk["actions"], universe=universe,
                         filings=sorted(filings, key=lambda f: (f["symbol"], f["accepted_at"], f["accession"])),
                         index=index, extra=extra)
    cov = mk["actions"]["coverage"]
    return {"data_manifest_sha256": sha, "members": len(universe["members"]), "filings": len(filings),
            "problems": {s: v["problems"] for s, v in index["symbols"].items() if v["problems"]},
            "unresolved_facts": {f"{f['symbol']} {f['accession']}": sorted(f["facts_status"]) for f in filings
                                 if f.get("facts_status")},
            "unsupported_actions": mk["actions"]["unsupported"], "nport": extra["review_table.json"]["nport"],
            "actions_coverage": {"status": cov["status"],
                                 "uncovered": sorted(s for s, v in cov["symbols"].items() if not v["intervals"]),
                                 "chains": [{k: c[k] for k in ("status", "reason", "start", "end", "covered_until")}
                                            for c in cov["chains"]]}}

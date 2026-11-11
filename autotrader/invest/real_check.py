"""Real-source validation of the EDGAR adapters on files already cached locally (offline; nothing is fetched).

The project's phase-4 collector cached public SEC responses under `sha256(url)[:24]` (`stockpilot.research.events.
Edgar.get`), so each cached file's source URL can be proven by recomputing that key. For every company with both a
cached company-facts file and a cached main submissions page, this check:
* parses the submissions index and the company facts with the pipeline's own functions;
* for as-of times at each 10-K/10-Q acceptance from 2023 on, computes trailing four quarters and compares the two
  independent methods (fiscal year + YTD - prior YTD, and four discrete quarters) wherever both apply;
* checks that no fact becomes public before its own period ends;
* counts excluded facts (accession not in the cached index; wrong unit) and restatements seen;
* reconciles every cached `-index-headers` page it can match by URL against the index (accession, issuer,
  acceptance time).
It reports what agreed and what did not. It is a consistency check of the parsers on real files, not an independent
verification of the numbers against a third source.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .adapters import edgar
from .adapters.raw import RawFile


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _raw(path: Path, url: str, kind: str) -> RawFile:
    data = path.read_bytes()
    at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    return RawFile(str(path), url, at, hashlib.sha256(data).hexdigest(), kind, data)


def check(cache_dirs: list[Path], since: str = "2023-01-01") -> dict:
    files = {f.name: f for d in cache_dirs for f in Path(d).iterdir() if f.is_file()}
    out = {"companies": [], "totals": {"companies": 0, "asof_points": 0, "cross_method_checks": 0,
                                       "cross_method_agree": 0, "facts_public_before_period_end": 0,
                                       "headers_reconciled": 0, "headers_disagree": 0, "restatements_seen": 0},
           "note": "consistency of the parsers on real cached SEC files; not a third-party verification of values"}
    for name, path in sorted(files.items()):
        if path.read_bytes()[:1] != b"{":
            continue
        try:
            j = json.loads(path.read_bytes())
        except ValueError:
            continue
        if not isinstance(j, dict) or "facts" not in j or "cik" not in j:
            continue
        cik = int(j["cik"])
        cf_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        if _key(cf_url) != name:
            continue                                         # provenance not provable: skip
        sub_url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        sp = files.get(_key(sub_url))
        if sp is None:
            continue
        pages = [_raw(sp, sub_url, "edgar_submissions")]
        for extra in json.loads(sp.read_bytes())["filings"].get("files", []):
            u = f"https://data.sec.gov/submissions/{extra['name']}"
            if _key(u) in files:
                pages.append(_raw(files[_key(u)], u, "edgar_submissions"))
        idx = edgar.parse_submissions(pages)
        cf = edgar.parse_companyfacts(_raw(path, cf_url, "edgar_companyfacts"), idx)
        early = sum(1 for f in cf["facts"] if datetime.fromisoformat(f["public_at"]).date().isoformat() < f["end"])
        row = {"cik": cik, "name": j.get("entityName"), "facts_kept": len(cf["facts"]), "excluded": cf["excluded"],
               "facts_public_before_period_end": early, "asof_points": 0, "cross_method": [], "methods_used": {},
               "headers": {"reconciled": 0, "disagree": []}}
        restated = set()
        for e in idx["entries"]:
            if e["form"] not in ("10-K", "10-Q") or e["accepted_at"][:10] < since:
                continue
            t = datetime.fromisoformat(e["accepted_at"])
            row["asof_points"] += 1
            for concept in edgar.REVENUE + edgar.NET_INCOME:
                c = edgar.ttm_candidates(cf["facts"], concept, "USD", t)
                for r in c.values():
                    for x in r["restatements_seen"]:
                        restated.add((concept, tuple(x["period"]), x["by"]))
                if "fiscal_year_plus_ytd_minus_prior_ytd" in c and "four_quarters" in c:
                    a, b = c["fiscal_year_plus_ytd_minus_prior_ytd"]["value"], c["four_quarters"]["value"]
                    agree = abs(a - b) <= max(Decimal(1), abs(a) * Decimal("0.005"))
                    row["cross_method"].append({"asof": e["accepted_at"], "concept": concept, "ytd_method": str(a),
                                                "four_quarters": str(b), "agree": agree})
            m = edgar.ttm(cf["facts"], edgar.REVENUE, "USD", t)
            key = m["method"] if m else "unavailable"
            row["methods_used"][key] = row["methods_used"].get(key, 0) + 1
        for e in idx["entries"]:                         # every cached header this index can name, any form
            nod = e["accession"].replace("-", "")
            hu = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{e['accession']}-index-headers.html"
            if _key(hu) in files:
                hd = edgar.parse_header(_raw(files[_key(hu)], hu, "edgar_header"))
                bad = edgar.reconcile(e, hd, cik)
                if bad:
                    row["headers"]["disagree"].append({"accession": e["accession"], "problems": bad})
                else:
                    row["headers"]["reconciled"] += 1
        row["restatements_seen"] = len(restated)
        n_agree = sum(1 for x in row["cross_method"] if x["agree"])
        row["cross_method_summary"] = {"checks": len(row["cross_method"]), "agree": n_agree}
        row["cross_method"] = [x for x in row["cross_method"] if not x["agree"]][:5]   # keep only disagreements
        out["companies"].append(row)
        t_ = out["totals"]
        t_["companies"] += 1
        t_["asof_points"] += row["asof_points"]
        t_["cross_method_checks"] += row["cross_method_summary"]["checks"]
        t_["cross_method_agree"] += n_agree
        t_["facts_public_before_period_end"] += early
        t_["headers_reconciled"] += row["headers"]["reconciled"]
        t_["headers_disagree"] += len(row["headers"]["disagree"])
        t_["restatements_seen"] += row["restatements_seen"]
    return out

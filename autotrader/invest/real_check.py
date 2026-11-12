"""Real-source validation on files already saved locally (offline; nothing is fetched).

1. SEC company facts and submissions (`check`). The project's phase-4 collector cached public SEC responses under
   `sha256(url)[:24]` (`stockpilot.research.events.Edgar.get`), so each cached file's source URL can be proven by
   recomputing that key. For every company with both a cached company-facts file and a cached main submissions
   page, this check:
   * parses the submissions index and the company facts with the pipeline's own functions;
   * for as-of times at each 10-K/10-Q acceptance from 2023 on, computes trailing four quarters, records each
     concept's status (resolved, or unresolved with the reporting-basis reason and its components and accessions),
     and compares the two independent methods (fiscal year + YTD - prior YTD, and four discrete quarters) wherever
     both apply;
   * checks that no fact becomes public before its own period ends;
   * counts excluded facts (accession not in the cached index; not a periodic report; wrong unit) and restatements;
   * reconciles every cached `-index-headers` page it can match by URL against the index.
   This is a consistency check of the parsers on real files, not an independent verification of the numbers.
2. A saved raw directory with an `index.jsonl` (the public-data feasibility fetcher: one line per response with its
   URL, retrieval time and SHA-256; file name `sha256(url)[:32]`), `check_raw_index`. Each file's URL and bytes are
   proven first. Then:
   * N-PORT XML: `nport.parse_nport` is compared field by field with an independent parse of the raw XML (regular
     expressions over the bytes, not the parser's output): holdings count, value total, asset categories, `N/A`
     identifiers, series ID, registrant CIK and report period; the filing's own index page (acceptance time, period,
     series and class IDs) is compared with them; the universe rule's issuer grouping and top twelve are compared
     with the same independent parse, and the rule as audited (an `N/A` LEI taken as an issuer) is shown;
   * filing documents (8-K and exhibits): `edgar.passages` is checked against an independent text extraction
     (`html.parser`): every passage is verbatim (ignoring whitespace), at least 100 characters, and carries what its
     rule requires (a figure, or its adverse keyword); inline XBRL cover facts read by `edgar.inline_facts` (entity,
     dimensions per trading symbol) are compared with a separate regular-expression reading of the same markup.
Every compared field, its expected value and any discrepancy is written out with the file's SHA-256.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from stockpilot.clock import ET

from .adapters import edgar, nport
from .adapters.raw import RawFile
from .universe import issuer_key, select_universe


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _raw(path: Path, url: str, kind: str) -> RawFile:
    data = path.read_bytes()
    at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    return RawFile(path.name, url, at, hashlib.sha256(data).hexdigest(), kind, data)


def check(cache_dirs: list[Path], since: str = "2023-01-01") -> dict:
    files = {f.name: f for d in cache_dirs for f in Path(d).iterdir() if f.is_file()}
    out = {"companies": [], "totals": {"companies": 0, "asof_points": 0, "cross_method_checks": 0,
                                       "cross_method_agree": 0, "facts_public_before_period_end": 0,
                                       "headers_reconciled": 0, "headers_disagree": 0, "restatements_seen": 0,
                                       "ttm_resolved": 0, "ttm_unresolved": 0, "ttm_unavailable": 0,
                                       "excluded_not_periodic_report": 0},
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
               "ttm_status": {}, "unresolved": [], "headers": {"reconciled": 0, "disagree": []}}
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
            for label, concepts, unit in (("revenue", edgar.REVENUE, "USD"), ("net_income", edgar.NET_INCOME, "USD"),
                                          ("eps_diluted", edgar.EPS_DILUTED, "USD/shares")):
                r = m if label == "revenue" else edgar.ttm(cf["facts"], concepts, unit, t)
                st = r["status"] if r else "unavailable"
                row["ttm_status"].setdefault(label, {}).setdefault(st, 0)
                row["ttm_status"][label][st] += 1
                if st == "unresolved":
                    row["unresolved"].append({
                        "asof": e["accepted_at"], "filing": e["accession"], "concept": label, "method": r["method"],
                        "value": str(r["value"]), "basis": r["basis"]["status"], "reason": r["basis"]["reason"],
                        "components": [{k: c[k] for k in ("start", "end", "value", "accession", "form")}
                                       for c in r["components"]]})
        row["documents"] = []
        for e in idx["entries"]:                         # cached primary documents this index can name (URL proven)
            if not e.get("primary_document"):
                continue
            nod = e["accession"].replace("-", "")
            du = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nod}/{e['primary_document']}"
            if _key(du) in files and e["form"].startswith(("8-K", "10-K", "10-Q")):
                row["documents"].append(check_document(_raw(files[_key(du)], du, "edgar_document"),
                                                       "8-K" if e["form"].startswith("8-K") else e["form"]))
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
        t_["excluded_not_periodic_report"] += cf["excluded"]["not_periodic_report"]
        docs = row["documents"]
        t_["documents"] = t_.get("documents", 0) + len(docs)
        t_["document_passages"] = t_.get("document_passages", 0) + sum(len(x["passages"]) for x in docs)
        t_["document_passage_problems"] = t_.get("document_passage_problems", 0) + sum(len(x["passage_problems"])
                                                                                        for x in docs)
        ixd = [x for x in docs if "inline_xbrl" in x]
        t_["inline_xbrl_documents"] = t_.get("inline_xbrl_documents", 0) + len(ixd)
        t_["inline_xbrl_disagreements"] = t_.get("inline_xbrl_disagreements", 0) + sum(
            1 for x in ixd if not x["inline_xbrl"]["agree"] or not x["inline_xbrl"]["entity_matches_url_cik"])
        for v in row["ttm_status"].values():
            for st, n in v.items():
                t_[f"ttm_{st}"] += n
    return out


# ------------------------------------------------------------------ saved raw directory (index.jsonl)
def _verified(d: Path) -> tuple[dict, list[dict]]:
    """{url: (path, record)} for files whose name is sha256(url)[:32] and whose bytes match the recorded SHA-256."""
    ok, bad = {}, []
    for line in (d / "index.jsonl").read_text().splitlines():
        r = json.loads(line)
        p = d / r["file"]
        if not p.exists():
            why = "missing"
        elif hashlib.sha256(r["url"].encode()).hexdigest()[:32] != r["file"]:
            why = "name is not sha256(url)[:32]"
        elif hashlib.sha256(p.read_bytes()).hexdigest() != r["sha256"]:
            why = "bytes do not match the recorded SHA-256"
        else:
            why = "" if r.get("status") == 200 else f"HTTP {r.get('status')}"
        if why:
            bad.append({"url": r["url"], "why": why})
        else:
            ok[r["url"]] = (p, r)
    return ok, bad


def _rx(pattern: str, text: str, flags=re.S) -> list[str]:
    return [m.strip() for m in re.findall(pattern, text, flags)]


def _cmp(fields: list, name: str, parsed, independent, note: str = "") -> None:
    fields.append({"field": name, "parsed": parsed, "independent": independent, "agree": parsed == independent,
                   **({"note": note} if note else {})})


def check_nport(xml: RawFile, index_page: RawFile = None) -> dict:
    """`parse_nport` and the universe rule against an independent reading of the raw XML and its index page."""
    t = xml.text()
    r = nport.parse_nport(xml)
    secs = _rx(r"<invstOrSec>(.*?)</invstOrSec>", t)
    one = lambda tag, s: (_rx(rf"<{tag}>(.*?)</{tag}>", s) or [None])[0]  # noqa: E731
    gen = one("genInfo", t) or ""
    f: list = []
    _cmp(f, "holdings", len(r["holdings"]), len(secs))
    _cmp(f, "value_usd_total", str(sum(Decimal(h["value_usd"] or 0) for h in r["holdings"])),
         str(sum(Decimal(one("valUSD", s) or 0) for s in secs)))
    _cmp(f, "asset_categories", dict(Counter(h["asset_cat"] for h in r["holdings"])),
         dict(Counter(one("assetCat", s) for s in secs)))
    _cmp(f, "lei_absent (N/A or empty)", sum(1 for h in r["holdings"] if not h["lei"]),
         sum(1 for s in secs if (one("lei", s) or "N/A").upper() == "N/A"))
    _cmp(f, "cusip_absent (N/A)", sum(1 for h in r["holdings"] if not h["cusip"]),
         sum(1 for s in secs if (one("cusip", s) or "N/A").upper() == "N/A"))
    _cmp(f, "ticker_identifiers", sum(1 for h in r["holdings"] if h["ticker_identifier"]),
         sum(1 for s in secs if "<ticker" in s))
    _cmp(f, "series_id", r["series_id"], one("seriesId", gen))
    _cmp(f, "registrant_cik", r["reg_cik"], one("regCik", gen))
    _cmp(f, "report_date", r["report_date"], one("repPdDate", gen))
    _cmp(f, "submission_type", r["submission_type"], one("submissionType", t))
    out = {"raw_file": xml.relpath, "source_url": xml.source_url, "raw_sha256": xml.sha256,
           "retrieved_at": xml.retrieved_at, "series_name": r["series_name"], "fields": f}
    if index_page is not None:
        txt = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", index_page.text())))
        acc = (re.findall(r"SEC Accession No\. ([0-9-]{20})", txt) or [None])[0]
        accepted = (re.findall(r"Accepted (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", txt) or [None])[0]
        period = (re.findall(r"Period of Report (\d{4}-\d\d-\d\d)", txt) or [None])[0]
        sids = sorted(set(re.findall(r"\b(S\d{9})\b", txt)))
        cik = (re.findall(r"CIK\W+(\d{10})", txt) or [None])[0]
        _cmp(f, "index_page: series", [r["series_id"]], sids)
        _cmp(f, "index_page: period of report", r["report_date"], period)
        _cmp(f, "index_page: filer CIK", r["reg_cik"], cik)
        at = datetime.strptime(accepted, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET) if accepted else None
        pub = nport.certainly_public_by(at, r["report_date"]) if at else None
        out["filing"] = {"accession": acc, "accepted_at_eastern": at.isoformat() if at else None,
                         "index_page_sha256": index_page.sha256, "index_url": index_page.source_url,
                         "certainly_public_by": pub.isoformat() if pub else None,
                         "classes": sorted(set(re.findall(r"\b(C\d{9})\b", txt)))}
    eq = [h for h in r["holdings"] if h["asset_cat"] == "EC"]
    cmap = {h["cusip"]: {"symbol": None, "cik": None} for h in r["holdings"]}
    issuers = {issuer_key(h) for h in eq}
    audited = {(h.get("lei") or "").strip() or " ".join((h["name"] or "").upper().split()) for h in
               ({**h, "lei": "N/A" if not h["lei"] else h["lei"]} for h in eq)}
    top = select_universe(r["holdings"], cmap, 12)
    ind_best: dict = {}                                  # independent: raw rows, LEI or (N/A) name, largest value
    for s_ in secs:
        if one("assetCat", s_) != "EC":
            continue
        lei = one("lei", s_) or ""
        key = lei if lei.upper() not in ("N/A", "") else "name:" + " ".join((one("name", s_) or "").upper().split())
        row = (-Decimal(one("valUSD", s_)), one("cusip", s_))
        ind_best[key] = min(ind_best.get(key, row), row)
    ind_top = [c for _, c in sorted(ind_best.values())[:12]]
    _cmp(f, "universe: distinct common-equity issuers", len(issuers), len(ind_best))
    _cmp(f, "universe: top twelve CUSIPs", [m["cusip"] for m in top], ind_top)
    aud_best: dict = {}                                  # the audited rule: "N/A" taken as one issuer's LEI
    for h in eq:
        key = (h["lei"] or "N/A") or " ".join((h["name"] or "").upper().split())
        row = (-Decimal(h["value_usd"]), h["cusip"] or "")
        aud_best[key] = min(aud_best.get(key, row), row)
    aud_top = [c for _, c in sorted(aud_best.values())[:12]]
    out["universe"] = {"common_equity_rows": len(eq), "issuers": len(issuers),
                       "issuers_if_N/A_were_an_LEI (audited rule)": len(audited),
                       "top12_under_audited_rule_differs": aud_top != [m["cusip"] for m in top],
                       "top12_lei_absent": sum(1 for m in top if not next(
                           h for h in r["holdings"] if h["cusip"] == m["cusip"])["lei"]),
                       "top12": [{k: m[k] for k in ("rank", "name", "cusip", "value_usd")} for m in top],
                       "ticker_identifiers_in_top12": sum(1 for m in top if next(
                           h for h in r["holdings"] if h["cusip"] == m["cusip"])["ticker_identifier"])}
    out["discrepancies"] = [x for x in f if not x["agree"]]
    return out


class _Text(HTMLParser):
    """An independent text extraction: character data outside script, style and the hidden ix:header."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "ix:header"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "ix:header") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _nows(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("\xa0", " "))


FIG_TOKENS = re.compile(r"\$\s?\d|\d\s?%|\d\s+(million|billion)", re.I)


def check_document(doc: RawFile, form: str = "8-K") -> dict:
    """Passages and inline XBRL cover facts of one real filing document."""
    t = doc.text()
    ex = _Text()
    ex.feed(t)
    whole = _nows("".join(ex.parts))
    ps = edgar.passages(t, form)
    rows = []
    for p in ps:
        kw = re.search(r"adverse disclosure \((.+)\)", p["section"])
        need = (kw.group(1) in p["text"].lower()) if kw else bool(FIG_TOKENS.search(p["text"]))
        rows.append({"section": p["section"], "chars": len(p["text"]), "verbatim": _nows(p["text"]) in whole,
                     "at_least_100_chars": len(p["text"]) >= edgar.MIN_PARA, "carries_its_rule": need})
    out = {"raw_file": doc.relpath, "source_url": doc.source_url, "raw_sha256": doc.sha256, "passages": rows,
           "passage_problems": [r for r in rows if not (r["verbatim"] and r["at_least_100_chars"]
                                                        and r["carries_its_rule"])]}
    if edgar.IX_NS in t:
        ix = edgar.inline_facts(t, ("TradingSymbol", "EntityCentralIndexKey", "DocumentType",
                                    edgar.SHARES))
        parsed = sorted((f["value"], f["dims"].get(edgar.CLASS_AXIS)) for f in ix["facts"]
                        if f["name"] == "TradingSymbol")
        ctxs = dict(re.findall(r'<xbrli:context\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</xbrli:context>', t, re.S))
        indep = []
        for tag in re.findall(r"<ix:nonNumeric\b[^>]*>.*?</ix:nonNumeric>", t, re.S):
            if not re.search(r'name="dei:TradingSymbol"', tag):
                continue
            c = re.search(r'contextRef="([^"]+)"', tag).group(1)
            val = " ".join(re.sub(r"<[^>]+>", "", tag.split(">", 1)[1].rsplit("</ix:nonNumeric", 1)[0]).split())
            mem = re.findall(r'dimension="[^"]*StatementClassOfStockAxis"[^>]*>\s*[^:<]*:([^<\s]+)', ctxs.get(c, ""))
            indep.append((html.unescape(val), mem[0] if mem else None))
        cik_url = (re.findall(r"/edgar/data/(\d+)/", doc.source_url) or [None])[0]
        ents = {f["entity"] for f in ix["facts"] if f.get("entity")}
        out["inline_xbrl"] = {"status": ix["status"], "contexts": ix["contexts"], "facts": len(ix["facts"]),
                              "trading_symbols_parsed": parsed, "trading_symbols_independent": sorted(indep),
                              "agree": parsed == sorted(indep),
                              "entity_matches_url_cik": bool(cik_url) and all(int(e) == int(cik_url) for e in ents
                                                                              if e.isdigit()),
                              "share_count_facts": sum(1 for f in ix["facts"] if f["name"] == edgar.SHARES)}
    return out


def check_raw_index(d: Path) -> dict:
    d = Path(d)
    ok, bad = _verified(d)

    def raw(url: str, kind: str) -> RawFile:
        path, rec = ok[url]
        return RawFile(path.name, url, rec["retrieved_at"], rec["sha256"], kind, path.read_bytes())

    out = {"dir": d.name, "files_verified": len(ok), "files_refused": bad, "nport": [], "documents": []}
    for url in sorted(ok):
        if url.endswith("/primary_doc.xml") and b"<edgarSubmission" in ok[url][0].read_bytes()[:4000]:
            acc_dir = url.rsplit("/", 2)[-2]
            acc = f"{acc_dir[:10]}-{acc_dir[10:12]}-{acc_dir[12:]}"
            idx_url = url.rsplit("/", 1)[0] + f"/{acc}-index.htm"
            page = raw(idx_url, "edgar_document") if idx_url in ok else None
            out["nport"].append(check_nport(raw(url, "nport_xml"), page))
        elif "/Archives/edgar/data/" in url and not url.endswith(("-index.htm", ".xml", ".txt")) and \
                url.lower().endswith((".htm", ".html")):
            out["documents"].append(check_document(raw(url, "edgar_document")))
    docs = out["documents"]
    out["totals"] = {"nport_reports": len(out["nport"]),
                     "nport_fields_checked": sum(len(n["fields"]) for n in out["nport"]),
                     "nport_discrepancies": sum(len(n["discrepancies"]) for n in out["nport"]),
                     "documents": len(docs), "passages": sum(len(x["passages"]) for x in docs),
                     "passage_problems": sum(len(x["passage_problems"]) for x in docs),
                     "inline_xbrl_documents": sum(1 for x in docs if "inline_xbrl" in x),
                     "inline_xbrl_disagreements": sum(1 for x in docs if "inline_xbrl" in x
                                                      and not x["inline_xbrl"]["agree"]),
                     "inline_xbrl_share_count_facts": sum(x["inline_xbrl"]["share_count_facts"] for x in docs
                                                          if "inline_xbrl" in x)}
    return out

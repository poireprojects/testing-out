"""EDGAR: submissions index, filing headers, reconciliation, XBRL company facts, trailing values, share counts,
passages.

Pure functions over saved raw responses (`raw.RawFile`); nothing here fetches.

Submissions index. `data.sec.gov/submissions/CIK##########.json` and its older pages list every filing with its
`acceptanceDateTime`, which is UTC (checked locally against an Eastern filing header; see
`stockpilot.research.events.json_acceptance`). The index is the independent record that collected documents are
reconciled against.

Reconciliation. A collected filing counts only if its `-index-headers` page agrees with the index on accession,
issuer CIK and acceptance time (to the second). Presence of an accession alone is not enough.

Company facts. Every fact keeps its reporting period (start, end) separately from when it became public: the
acceptance time of the filing (accession) that reported it, taken from the index. A fact whose accession is not in
the index has no known public time and is excluded, and so is a fact whose accession the index lists as anything but a
periodic report (10-K, 10-Q, 10-K/A, 10-Q/A). A value reported again by a later filing for the same period is a
duplicate context; if the later value differs by more than the coarser figure's rounding, it is a restatement, and
it counts only from its own public time.
So a restatement filed after a cutoff can never enter an earlier pack.

Trailing four quarters (`ttm`), as of a time T, from facts public by T, for the latest period end E:
1. a reported ~12-month period ending at E (annual, 350-380 days);
2. otherwise the fiscal-year-to-date period ending at E plus the prior fiscal year minus the prior year's
   comparable year-to-date period (dates matched within 7 days, for 52/53-week calendars);
3. otherwise four reported ~3-month periods chained back from E.
Cumulative year-to-date values are never summed as if they were quarters. Diluted EPS by this arithmetic is an
APPROXIMATION (share counts differ by period) and is labelled so.

Reporting basis. Methods 2 and 3 combine figures from different filings, and a company can recast prior periods
between them (the real Dominion Energy case: its fiscal 2024 annual figure, as first reported, combined with 2024
comparatives recast in 2025 10-Qs). Each combination is checked (`basis`):
* method 2 is consistent when the annual report that supplied the fiscal-year figure also reports the prior-year
  comparable period (directly, or as quarters) at the comparative's value; when it reports a different value the
  basis is MIXED. Without such a figure, the basis is not established if any prior-year period was revised after the
  annual report, if the annual figure is itself a restatement, or if the comparative does not come from the same
  filing as the current year-to-date figure; otherwise it is taken as consistent (no revision seen);
* method 3 is mixed when a reported multi-quarter period disagrees with the sum of the chained quarters it spans,
  and not established when any chained quarter was ever revised;
* method 1 is a single reported figure.
A mixed or unestablished basis makes the value `unresolved`: it keeps its components for review and never enters a
pack as an established figure. No other method is substituted for it. Figures agree when they differ by no more than
the rounding of the figures involved (half a reporting unit per figure).

Shares outstanding (`shares_outstanding`) come from the periodic filing's own cover page, read from its inline XBRL
with every fact's context (entity, date, dimensions), because company facts carry no dimensions: in the real cached
files, companies with several share classes (Reddit, Roblox, Rivian, Pinterest) have no company-facts share count at
all. Facts are deduplicated by context, never by value. An undimensioned count is the company total (or the single
class); counts on `StatementClassOfStockAxis` are classes. A total and class counts are never added together: the
total is used when the classes sum to it, and the value is unresolved when they do not. Other legal entities'
counts (`LegalEntityAxis`, or another CIK) are excluded. Conflicting counts for one context, unknown dimensions, a
document that is not inline XBRL, or company facts that disagree with the cover page give an explicit `unresolved`
status. Nothing falls back to another filing.

Passages. Deterministic selection from the primary document's own paragraphs, never paraphrased (`passages`).
"""
from __future__ import annotations

import html as htmllib
import io
import re
import xml.etree.ElementTree as ET_XML
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.entities import name2codepoint
from typing import Optional

from stockpilot.clock import ET
from stockpilot.research.events import json_acceptance, parse_header as _parse_header

from .raw import RawFile

PERIODIC = ("10-K", "10-Q", "10-K/A", "10-Q/A")
REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenueFromContractWithCustomerIncludingAssessedTax")
NET_INCOME = ("NetIncomeLoss",)
EPS_DILUTED = ("EarningsPerShareDiluted",)
SHARES = "EntityCommonStockSharesOutstanding"
UNITS = {"revenue": "USD", "net_income": "USD", "eps_diluted": "USD/shares", "shares": "shares"}
DAY_TOL = 7


class EdgarError(ValueError):
    pass


# ------------------------------------------------------------------ submissions index
def parse_submissions(pages: list[RawFile]) -> dict:
    """The main submissions page first, then any older pages it lists. Returns cik, name, tickers and one entry per
    accession with its acceptance time in Eastern time and the page it came from."""
    if not pages:
        raise EdgarError("no submissions pages")
    main = pages[0].json()
    if "filings" not in main:
        raise EdgarError(f"{pages[0].relpath}: not a main submissions page")
    cik = int(main["cik"])
    tables = [(pages[0], main["filings"]["recent"])] + [(p, p.json()) for p in pages[1:]]
    entries: dict[str, dict] = {}
    for page, t in tables:
        n = len(t["accessionNumber"])
        for k in ("form", "acceptanceDateTime", "filingDate"):
            if len(t.get(k, [])) != n:
                raise EdgarError(f"{page.relpath}: column {k} has {len(t.get(k, []))} rows, not {n}")
        for i in range(n):
            acc = t["accessionNumber"][i]
            e = {"accession": acc, "form": t["form"][i], "filing_date": t["filingDate"][i],
                 "report_date": (t.get("reportDate") or [""] * n)[i],
                 "accepted_at": json_acceptance(t["acceptanceDateTime"][i]).isoformat(),
                 "items": (t.get("items") or [""] * n)[i], "primary_document": (t.get("primaryDocument") or [""] * n)[i],
                 "index_page": page.relpath}
            old = entries.get(acc)
            if old is not None and old["accepted_at"] != e["accepted_at"]:
                raise EdgarError(f"{acc}: two different acceptance times in the index")
            entries.setdefault(acc, e)
    return {"cik": cik, "name": main.get("name"), "tickers": main.get("tickers", []),
            "entries": sorted(entries.values(), key=lambda e: (e["accepted_at"], e["accession"])),
            "provenance": [p.provenance() for p in pages]}


# ------------------------------------------------------------------ headers and reconciliation
def parse_header(f: RawFile) -> dict:
    txt = f.text()
    t = txt.replace("&lt;", "<").replace("&gt;", ">")
    base = _parse_header(t)
    acc = re.search(r"<ACCESSION-NUMBER>\s*([0-9-]+)|ACCESSION NUMBER:\s*([0-9-]+)", t)
    cik = re.search(r"<CIK>\s*(\d+)|CENTRAL INDEX KEY:\s*(\d+)", t)
    form = re.search(r"<TYPE>\s*([^\n<]+)|CONFORMED SUBMISSION TYPE:\s*([^\n<]+)", t)
    items = re.findall(r"<ITEMS>\s*([0-9.]+)", t) or base["items"]
    return {"accession": next(g for g in acc.groups() if g) if acc else None,
            "cik": int(next(g for g in cik.groups() if g)) if cik else None,
            "form": next(g for g in form.groups() if g).strip() if form else None,
            "accepted_at": base["acceptance"].isoformat() if base["acceptance"] else None,
            "items": [i.strip() for i in items], "documents": base["documents"],
            "series_ids": sorted(set(re.findall(r"<SERIES-ID>\s*(S\d{9})", t))), "provenance": f.provenance()}


def reconcile(entry: dict, header: dict, cik: int) -> list[str]:
    """Disagreements between a collected filing's header and the independent index (empty: reconciled)."""
    out = []
    if header["accession"] != entry["accession"]:
        out.append(f"header accession {header['accession']} is not {entry['accession']}")
    if header["cik"] != cik:
        out.append(f"header issuer CIK {header['cik']} is not {cik}")
    if header["accepted_at"] is None:
        out.append("header has no acceptance time")
    elif datetime.fromisoformat(header["accepted_at"]) != datetime.fromisoformat(entry["accepted_at"]):
        out.append(f"header acceptance {header['accepted_at']} differs from the index {entry['accepted_at']}")
    if header["form"] and header["form"] != entry["form"]:
        out.append(f"header form {header['form']} is not {entry['form']}")
    return out


# ------------------------------------------------------------------ company facts
def parse_companyfacts(f: RawFile, index: dict) -> dict:
    """Fact-level records with provenance and public time. Facts from accessions missing from the index, from
    accessions the index lists as anything other than a periodic report (10-K, 10-Q or their amendments: company
    facts also carry, for example, DEF 14A pay-versus-performance figures rounded to millions), or in units other
    than the accepted ones, are excluded and counted."""
    j = f.json()
    if int(j["cik"]) != index["cik"]:
        raise EdgarError(f"{f.relpath}: company facts are for CIK {j['cik']}, the index for {index['cik']}")
    public = {e["accession"]: e for e in index["entries"]}
    facts, excluded = [], {"accession_not_in_index": 0, "not_periodic_report": 0, "unit": 0}
    wanted = {**{c: "revenue" for c in REVENUE}, **{c: "net_income" for c in NET_INCOME},
              **{c: "eps_diluted" for c in EPS_DILUTED}}
    for tax, concepts in (("us-gaap", wanted), ("dei", {SHARES: "shares"})):
        for concept, role in concepts.items():
            for unit, rows in ((j.get("facts", {}).get(tax, {}).get(concept) or {}).get("units") or {}).items():
                for r in rows:
                    if unit != UNITS[role]:
                        excluded["unit"] += 1
                        continue
                    e = public.get(r["accn"])
                    if e is None:
                        excluded["accession_not_in_index"] += 1
                        continue
                    if e["form"] not in PERIODIC:
                        excluded["not_periodic_report"] += 1
                        continue
                    facts.append({"concept": concept, "role": role, "unit": unit, "start": r.get("start"),
                                  "end": r["end"], "value": str(r["val"]), "accession": r["accn"], "form": r.get("form"),
                                  "filed": r.get("filed"), "public_at": e["accepted_at"]})
    return {"cik": index["cik"], "facts": facts, "excluded": excluded, "provenance": f.provenance()}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _near(a: date, b: date) -> bool:
    return abs((a - b).days) <= DAY_TOL


def _days(f: dict) -> int:
    return (_d(f["end"]) - _d(f["start"])).days + 1


def _known(facts: list[dict], concept: str, unit: str, asof: datetime) -> tuple[dict, list[dict]]:
    """{(start, end): fact} with the latest value public by `asof`, and the restatements seen."""
    best, restated = {}, []
    for f in sorted((f for f in facts if f["concept"] == concept and f["unit"] == unit and f["start"]
                     and datetime.fromisoformat(f["public_at"]) <= asof),
                    key=lambda f: (f["public_at"], f["accession"])):
        k = (f["start"], f["end"])
        old = best.get(k)
        if old is not None and not _same_figure(old["value"], f["value"]):
            restated.append({"period": k, "was": old["value"], "now": f["value"], "by": f["accession"]})
        best[k] = f
    return best, restated


def _same_figure(a, b) -> bool:
    """Two reports of one figure agree when they differ by no more than the rounding of the coarser one (a
    figure first reported in thousands and later in millions is not a restatement)."""
    a, b = Decimal(str(a)), Decimal(str(b))
    units = [_unit([v]) for v in (a, b) if v != 0]
    return a == b if not units else abs(a - b) <= max(units) / 2


def _unit(vals) -> Decimal:
    """The reporting precision shared by `vals`: the largest power of ten, up to a million, that divides each and
    still leaves every non-zero value at least three significant digits (a round 4,000 is not "in thousands")."""
    ds = [Decimal(str(v)) for v in vals]
    small = min((abs(v) for v in ds if v != 0), default=Decimal(0))
    for k in range(6, -7, -1):
        u = Decimal(10) ** k
        if (small == 0 or u * 100 <= small) and all(v % u == 0 for v in ds):
            return u
    return Decimal(10) ** -6


def _agree(a, b, vals, terms: int) -> bool:
    """Equal up to the rounding of `terms` figures reported at the precision of `vals` (half a unit each)."""
    return abs(Decimal(str(a)) - Decimal(str(b))) <= _unit(vals) * terms / 2


def _chain(rows: list[dict], start: date, end: date) -> Optional[list[dict]]:
    """Consecutive ~3-month periods from `rows` running from `start` to `end` (dates within 7 days), or None."""
    out, s = [], start
    for _ in range(5):
        q = next((f for f in rows if 80 <= _days(f) <= 100 and _near(_d(f["start"]), s)), None)
        if q is None:
            return None
        out.append(q)
        if _near(_d(q["end"]), end):
            return out
        s = _d(q["end"]) + timedelta(days=1)
    return None


def _public(facts: list[dict]) -> dict:
    return {f["accession"]: datetime.fromisoformat(f["public_at"]) for f in facts}


def _ytd_basis(own_rows: list[dict], fy: dict, ytd: dict, prior: dict, restated: list[dict], pub: dict) -> dict:
    """Whether fiscal year + YTD - prior YTD combines figures on one reporting basis (see the module docstring)."""
    a = fy["accession"]
    ps, pe = _d(prior["start"]), _d(prior["end"])
    same = next((f for f in own_rows if f["accession"] == a and _near(_d(f["start"]), ps) and _near(_d(f["end"]), pe)
                 and abs(_days(f) - _days(prior)) <= DAY_TOL), None)
    quarters = None if same else _chain([f for f in own_rows if f["accession"] == a], ps, pe)
    if same is not None or quarters:
        figs = [same] if same is not None else quarters
        on_fy = sum((Decimal(f["value"]) for f in figs), Decimal(0))
        vals = [f["value"] for f in figs] + [prior["value"]]
        detail = {"annual_report": a, "annual_report_prior_period_value": str(on_fy),
                  "annual_report_figures": [{"start": f["start"], "end": f["end"], "value": f["value"]} for f in figs],
                  "comparative": prior["accession"], "comparative_value": prior["value"]}
        if _agree(on_fy, prior["value"], vals, len(figs) + 1):
            return {"status": "consistent", "test": "annual report's own prior-period figures", **detail,
                    "reason": f"{a} reports the prior-year comparable period at the comparative's value"}
        return {"status": "mixed", "test": "annual report's own prior-period figures", **detail,
                "reason": f"mixed reporting basis: on the basis of {a} (fiscal year {fy['value']}) the prior-year "
                          f"period {prior['start']}..{prior['end']} is {on_fy}, but the comparative from "
                          f"{prior['accession']} is {prior['value']}"}
    t_fy = pub.get(a)
    fs, fe = _d(fy["start"]), _d(fy["end"])
    later = [r for r in restated if fs - timedelta(days=DAY_TOL) <= _d(r["period"][0]) and
             _d(r["period"][1]) <= fe + timedelta(days=DAY_TOL) and t_fy is not None and pub.get(r["by"], t_fy) > t_fy]
    if later:
        r = later[0]
        return {"status": "not_established", "test": "revisions after the annual report", "revisions": later,
                "reason": f"prior-year figures were revised after {a} (e.g. {r['period'][0]}..{r['period'][1]}: "
                          f"{r['was']} -> {r['now']} by {r['by']}), and {a} does not report that period, so the "
                          "fiscal-year figure's basis cannot be matched"}
    if any(tuple(r["period"]) == (fy["start"], fy["end"]) and r["by"] == a for r in restated):
        return {"status": "not_established", "test": "restated annual figure",
                "reason": f"the fiscal-year figure is a restatement by {a}, which reports no prior-year comparable "
                          "period, so whether the comparative reflects it cannot be established"}
    if prior["accession"] != ytd["accession"]:
        return {"status": "not_established", "test": "comparative source",
                "reason": f"the prior-year comparative comes from {prior['accession']}, not from the filing of the "
                          f"current year-to-date figure ({ytd['accession']})"}
    return {"status": "consistent", "test": "revisions after the annual report",
            "reason": "no prior-year figure was revised after the annual report (no same-basis figure to compare)"}


def _quarters_basis(rows: list[dict], chain: list[dict], restated: list[dict]) -> dict:
    qs = sorted(chain, key=lambda f: f["start"])
    for f in rows:
        if any(f is q for q in qs) or _days(f) <= 100:
            continue
        i = next((n for n, q in enumerate(qs) if _near(_d(q["start"]), _d(f["start"]))), None)
        j = next((n for n, q in enumerate(qs) if _near(_d(q["end"]), _d(f["end"]))), None)
        if i is None or j is None or j <= i:
            continue
        part = qs[i:j + 1]
        tot = sum((Decimal(q["value"]) for q in part), Decimal(0))
        if not _agree(tot, f["value"], [q["value"] for q in part] + [f["value"]], len(part) + 1):
            return {"status": "mixed", "test": "reported multi-quarter periods",
                    "reason": f"mixed reporting basis: {f['start']}..{f['end']} is {f['value']} in {f['accession']}, "
                              f"but the chained quarters it spans sum to {tot}"}
    periods = {(q["start"], q["end"]) for q in qs}
    rev = [r for r in restated if tuple(r["period"]) in periods]
    if rev:
        return {"status": "not_established", "test": "revised quarters", "revisions": rev,
                "reason": f"chained quarter {rev[0]['period'][0]}..{rev[0]['period'][1]} was revised "
                          f"({rev[0]['was']} -> {rev[0]['now']} by {rev[0]['by']}); the other quarters' basis is "
                          "unknown"}
    return {"status": "consistent", "test": "reported multi-quarter periods",
            "reason": "no reported multi-quarter period disagrees and no chained quarter was revised"}


def ttm_candidates(facts: list[dict], concept: str, unit: str, asof: datetime) -> dict:
    """Every method that applies for `concept` at its latest period end E as of `asof`: {method: result}, each with
    its reporting-basis check (`basis`). Used by `ttm` (which picks one by priority) and by consistency checks."""
    per, restated = _known(facts, concept, unit, asof)
    if not per:
        return {}
    rows = list(per.values())
    public_rows = [f for f in facts if f["concept"] == concept and f["unit"] == unit and f["start"]
                   and datetime.fromisoformat(f["public_at"]) <= asof]
    pub = _public(public_rows)
    E = max(_d(f["end"]) for f in rows)
    ending = [f for f in rows if _d(f["end"]) == E]
    out = {}
    annual = [f for f in ending if 350 <= _days(f) <= 380]
    if annual:
        out["annual"] = {"value": Decimal(annual[0]["value"]), "components": [annual[0]],
                         "basis": {"status": "consistent", "test": "single figure", "reason": "one reported figure"}}
    for ytd in sorted((f for f in ending if 80 <= _days(f) < 350), key=_days, reverse=True):
        S = _d(ytd["start"])
        fy = [f for f in rows if 350 <= _days(f) <= 380 and _near(_d(f["end"]), S - timedelta(days=1))]
        prior = [f for f in rows if abs(_days(f) - _days(ytd)) <= DAY_TOL
                 and _near(_d(f["start"]), S - timedelta(days=364)) and _near(_d(f["end"]), E - timedelta(days=364))]
        if fy and prior:
            out["fiscal_year_plus_ytd_minus_prior_ytd"] = {
                "value": Decimal(fy[0]["value"]) + Decimal(ytd["value"]) - Decimal(prior[0]["value"]),
                "components": [fy[0], ytd, prior[0]],
                "basis": _ytd_basis(public_rows, fy[0], ytd, prior[0], restated, pub)}
            break
    chain, end = [], E
    for _ in range(4):
        q = next((f for f in rows if 80 <= _days(f) <= 100 and _near(_d(f["end"]), end)), None)
        if q is None:
            break
        chain.append(q)
        end = _d(q["start"]) - timedelta(days=1)
    if len(chain) == 4:
        out["four_quarters"] = {"value": sum((Decimal(q["value"]) for q in chain), Decimal(0)), "components": chain,
                                "basis": _quarters_basis(rows, chain, restated)}
    for v in out.values():
        v |= {"concept": concept, "unit": unit, "period_end": E.isoformat(), "restatements_seen": restated}
    return out


METHODS = ("annual", "fiscal_year_plus_ytd_minus_prior_ytd", "four_quarters")


def ttm(facts: list[dict], concepts: tuple, unit: str, asof: datetime) -> Optional[dict]:
    """Trailing-four-quarter value as of `asof` (see the module docstring), choosing among `concepts` the one with
    the latest period end (list order breaks ties) and among methods the first in METHODS. None when none applies.
    `status` is `resolved`, or `unresolved` when the chosen method's reporting basis is mixed or not established
    (`unresolved_reason`); an unresolved value is kept for review but never used as an established figure."""
    best = None
    for concept in concepts:
        c = ttm_candidates(facts, concept, unit, asof)
        m = next((m for m in METHODS if m in c), None)
        if m is None:
            continue
        out = c[m] | {"method": m}
        if best is None or out["period_end"] > best["period_end"]:
            best = out
    if best is None:
        return None
    if best["basis"]["status"] == "consistent":
        best["status"] = "resolved"
    else:
        best |= {"status": "unresolved", "unresolved_reason": f"{best['method']}: {best['basis']['reason']}"}
    if unit == "USD/shares":
        best["approximation"] = ("diluted EPS combined arithmetically across periods: share counts differ by period, "
                                 "so this is an approximation, not a reported trailing EPS")
    return best


# ------------------------------------------------------------------ inline XBRL cover page and share counts
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
CLASS_AXIS = "StatementClassOfStockAxis"
ENTITY_AXIS = "LegalEntityAxis"
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _as_xml(text: str) -> bytes:
    """The document as parseable XML: HTML named entities become character references; no declaration or DTD."""
    t = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", lambda m: m.group(0) if m.group(1) in _XML_ENTITIES
               or m.group(1) not in name2codepoint else f"&#{name2codepoint[m.group(1)]};", text)
    t = re.sub(r"^\s*<\?xml[^>]*\?>", "", t)
    t = re.sub(r"(?is)<!DOCTYPE[^>]*>", "", t)
    return t.encode("utf-8")


def _ix_number(el) -> Optional[Decimal]:
    """An ix:nonFraction's value: its displayed number transformed by `format`, `scale` and `sign` (nil: None)."""
    if el.get(XSI_NIL) in ("true", "1"):
        return None
    txt = "".join(el.itertext()).strip()
    fmt = (el.get("format") or "").rsplit(":", 1)[-1].lower()
    if "zero" in fmt or txt in ("-", "—", "–"):
        v = Decimal(0)
    else:
        if "comma-decimal" in fmt or "numcommadecimal" in fmt:
            txt = re.sub(r"[.\s\xa0]", "", txt).replace(",", ".")
        else:
            txt = re.sub(r"[,\s\xa0]", "", txt)
        v = Decimal(txt)
    v = v.scaleb(int(el.get("scale") or 0))
    return -v if el.get("sign") == "-" else v


def inline_facts(doc: str, names: tuple = (SHARES,)) -> dict:
    """DEI facts named `names` from an inline XBRL document, each with its resolved context: entity identifier,
    instant or start/end, explicit dimensions {axis: member} (local names) and typed dimensions. Never raises:
    `status` is `ok`, `not_inline_xbrl` or `unparseable` (with `reason`)."""
    if IX_NS not in doc:
        return {"status": "not_inline_xbrl", "reason": "the document declares no inline XBRL namespace", "facts": [],
                "contexts": 0}
    data = _as_xml(doc)
    try:
        nsmap: dict[str, str] = {}
        for _, (prefix, uri) in ET_XML.iterparse(io.BytesIO(data), events=("start-ns",)):
            nsmap.setdefault(prefix, uri)
        root = ET_XML.fromstring(data)
    except ET_XML.ParseError as exc:
        return {"status": "unparseable", "reason": f"not well-formed XML ({exc})"[:160], "facts": [], "contexts": 0}

    def qname(q: str) -> tuple[str, str]:
        prefix, _, local = q.strip().rpartition(":")
        return nsmap.get(prefix, prefix), local

    contexts = {}
    for el in root.iter():
        if _local(el.tag) != "context":
            continue
        ident = next(((x.text or "").strip() for x in el.iter() if _local(x.tag) == "identifier"), None)
        dims = {qname(x.get("dimension", ""))[1]: qname(x.text or "")[1] for x in el.iter()
                if _local(x.tag) == "explicitMember"}
        typed = [qname(x.get("dimension", ""))[1] for x in el.iter() if _local(x.tag) == "typedMember"]
        per = {_local(x.tag): (x.text or "").strip() for x in el.iter()
               if _local(x.tag) in ("instant", "startDate", "endDate")}
        contexts[el.get("id")] = {"entity": ident, "dims": dims, "typed": typed, "instant": per.get("instant"),
                                  "start": per.get("startDate"), "end": per.get("endDate")}
    facts = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("nonFraction", "nonNumeric") or not el.tag.startswith("{" + IX_NS):
            continue
        uri, local = qname(el.get("name", ""))
        if local not in names or "xbrl.sec.gov/dei" not in uri:
            continue
        ctx = contexts.get(el.get("contextRef"))
        f = {"name": local, "kind": kind, "context": el.get("contextRef"), "unit": el.get("unitRef"),
             "id": el.get("id"), "error": None, "value": None}
        if ctx is None:
            f["error"] = f"context {el.get('contextRef')!r} is not defined"
        else:
            f |= ctx
        if kind == "nonFraction":
            try:
                f["value"] = _ix_number(el)
            except (InvalidOperation, ValueError):
                f["error"] = f"number {''.join(el.itertext()).strip()!r} cannot be read (format {el.get('format')!r})"
        else:
            f["value"] = " ".join("".join(el.itertext()).split())
        facts.append(f)
    return {"status": "ok", "reason": "", "facts": facts, "contexts": len(contexts)}


def shares_outstanding(cover: Optional[dict], cik: int, accession: str, facts: list[dict] = ()) -> dict:
    """The cover-page shares outstanding of periodic filing `accession` (see the module docstring). `cover` is
    `inline_facts` of that filing's primary document (None: not available); `facts` are the parsed company facts,
    used as a cross-check. Returns `status` `resolved` (value, as_of_date, share_classes, basis, classes) or
    `unresolved` (reason); never None and never a value from another filing."""
    base = {"accession": accession, "concept": f"dei:{SHARES}"}

    def unresolved(why: str, **kw) -> dict:
        return base | {"status": "unresolved", "reason": why} | kw

    if cover is None:
        return unresolved("the filing's primary document was not available for its cover-page contexts")
    if cover["status"] != "ok":
        return unresolved(f"cover-page contexts unavailable: {cover['reason']}")
    rows = [f for f in cover["facts"] if f["name"] == SHARES]
    if not rows:
        return unresolved("the filing's inline XBRL has no cover-page share count")
    own, other = [], []
    for f in rows:
        if f["error"] or f["value"] is None:
            return unresolved(f"a cover-page share count cannot be read: {f['error'] or 'nil value'}")
        if not f["instant"]:
            return unresolved(f"cover-page share count {f['id'] or f['context']} has no instant date")
        if ENTITY_AXIS in f["dims"] or (f["entity"] and f["entity"].isdigit() and int(f["entity"]) != cik):
            other.append(f)
            continue
        if f["typed"] or set(f["dims"]) - {CLASS_AXIS}:
            return unresolved(f"cover-page share count on an unrecognized dimension: "
                              f"{sorted(set(f['dims']) - {CLASS_AXIS}) or f['typed']}")
        own.append(f)
    if not own:
        return unresolved(f"the cover page reports share counts only for other legal entities ({len(other)})")
    inst = max(f["instant"] for f in own)
    use = [f for f in own if f["instant"] == inst]
    by_ctx: dict = {}
    for f in use:                                         # semantic identity: (entity, instant, class member)
        key = f["dims"].get(CLASS_AXIS)
        old = by_ctx.get(key)
        if old is not None and old["value"] != f["value"]:
            return unresolved(f"two different counts for the same context ({key or 'no class dimension'}, {inst}): "
                              f"{old['value']} and {f['value']}")
        by_ctx.setdefault(key, f)
    total = by_ctx.pop(None, None)
    classes = sorted(by_ctx.items())
    cf_rows = [f for f in facts if f["concept"] == SHARES and f["accession"] == accession]
    for r in cf_rows:
        if r["end"] == inst and (total is None or Decimal(r["value"]) != total["value"]):
            return unresolved(f"company facts report {r['value']} for {accession} on {inst}, but the cover page's "
                              f"undimensioned count is {total['value'] if total else 'absent'}")
    sum_cls = sum((f["value"] for _, f in classes), Decimal(0))
    if total is not None and classes and sum_cls != total["value"]:
        return unresolved(f"the undimensioned count {total['value']} and the {len(classes)} class count(s) (sum "
                          f"{sum_cls}) disagree: which is the company total cannot be established")
    if total is not None and not classes:
        value, n, basis = total["value"], 1, "one undimensioned count (a single class of common stock)"
    elif total is None:
        value, n = sum_cls, len(classes)
        basis = "one share class" if n == 1 else f"{n} share classes, each reported on the class axis; summed"
    else:
        value, n, basis = total["value"], len(classes), "company total; its class counts agree and are not added again"
    return base | {"status": "resolved", "value": value, "as_of_date": inst, "share_classes": n, "basis": basis,
                   "classes": [{"member": m, "value": str(f["value"])} for m, f in classes],
                   "repeated_facts_merged": len(use) - len(classes) - (1 if total is not None else 0),
                   "other_entities_excluded": len(other), "company_facts_rows": len(cf_rows)}


def facts_as_of(cf: dict, asof: datetime, cover: Optional[dict] = None, accession: Optional[str] = None) -> dict:
    """The four values a valuation row needs, as of `asof`, with full provenance. Trailing values are None when no
    method applies and carry `status` otherwise; the share count is the cover page of `accession` (the periodic
    filing accepted at `asof`), resolved or explicitly unresolved."""
    return {"revenue_ttm": ttm(cf["facts"], REVENUE, "USD", asof),
            "net_income_ttm": ttm(cf["facts"], NET_INCOME, "USD", asof),
            "eps_diluted_ttm": ttm(cf["facts"], EPS_DILUTED, "USD/shares", asof),
            "shares_outstanding": shares_outstanding(cover, cf["cik"], accession or "", cf["facts"])}


# ------------------------------------------------------------------ passages
ADVERSE = ("going concern", "material weakness", "restatement", "impairment", "litigation", "decline", "decrease",
           "loss", "risk")
_FIG = re.compile(r"\$\s?\d|\d(?:\.\d+)?\s?%|\d[\d,]*\s+(?:million|billion)")
MIN_PARA = 100


def paragraphs(doc_html: str) -> list[str]:
    """The document's own paragraphs, in order: split at block-level tags, tags dropped, entities decoded,
    whitespace collapsed. No text is added or reworded. Scripts, styles and the hidden inline XBRL header
    (`ix:header`: contexts and hidden facts) are not document text."""
    t = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", " ", doc_html)
    t = re.sub(r"(?i)</?(p|div|br|tr|li|h[1-6]|table)\b[^>]*>", "\n\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t).replace("\xa0", " ")
    out = []
    for block in re.split(r"\n\s*\n", t):
        s = re.sub(r"\s+", " ", block).strip()
        if s:
            out.append(s)
    return out


def _section_start(paras: list[str], heading: re.Pattern) -> Optional[int]:
    """The heading occurrence that opens real text (skips a table of contents): the first match whose next
    paragraph is at least 200 characters long."""
    for i, p in enumerate(paras):
        if heading.search(p) and len(p) < 200 and i + 1 < len(paras) and len(paras[i + 1]) >= 200:
            return i + 1
    return None


MDA = re.compile(r"(?i)management.s discussion and analysis")
RISK = re.compile(r"(?i)risk factors")


def passages(doc_html: str, form: str) -> list[dict]:
    """Deterministic selection (registered rule):
    P1, P2  the first two paragraphs of at least 100 characters with a dollar amount, percentage or "million/billion"
            after the MD&A heading (8-K: anywhere in the release);
    P3      the first paragraph of at least 100 characters containing the highest-priority adverse-disclosure
            keyword present (going concern, material weakness, restatement, impairment, litigation, decline,
            decrease, loss, risk);
    P4      the first paragraph of at least 100 characters after the Risk Factors heading (10-K) or, failing that,
            the next figure paragraph.
    A section starts after the first occurrence of its heading (a paragraph under 200 characters) that is followed by
    a paragraph of at least 200 characters; this skips a table of contents. Without such an occurrence, P1 and P2
    are searched from the document start and P4 falls back to the next figure paragraph.
    Each is the paragraph's own text; the pack cuts it to 1,200 characters at a word boundary."""
    paras = paragraphs(doc_html)
    chosen: list[tuple[int, str]] = []
    start = 0 if form.startswith("8-K") else (_section_start(paras, MDA) or 0)
    figs = [i for i in range(start, len(paras)) if len(paras[i]) >= MIN_PARA and _FIG.search(paras[i])]
    for i in figs[:2]:
        chosen.append((i, "MD&A: figures" if not form.startswith("8-K") else "earnings release: figures"))
    taken = {i for i, _ in chosen}
    for kw in ADVERSE:
        hit = next((i for i, p in enumerate(paras) if i not in taken and len(p) >= MIN_PARA and kw in p.lower()), None)
        if hit is not None:
            chosen.append((hit, f"adverse disclosure ({kw})"))
            taken.add(hit)
            break
    r = _section_start(paras, RISK)
    nxt = next((i for i in range(r, len(paras)) if i not in taken and len(paras[i]) >= MIN_PARA), None) if r else None
    if nxt is None:
        nxt = next((i for i in figs if i not in taken), None)
        label = "MD&A: figures"
    else:
        label = "risk factors"
    if nxt is not None:
        chosen.append((nxt, label))
    return [{"section": label, "text": paras[i], "paragraph": i} for i, label in chosen]


def accepted_at_et(entry: dict) -> datetime:
    return datetime.fromisoformat(entry["accepted_at"]).astimezone(ET)

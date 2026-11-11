"""EDGAR: submissions index, filing headers, reconciliation, XBRL company facts, trailing values, passages.

Pure functions over saved raw responses (`raw.RawFile`); nothing here fetches.

Submissions index. `data.sec.gov/submissions/CIK##########.json` and its older pages list every filing with its
`acceptanceDateTime`, which is UTC (checked locally against an Eastern filing header; see
`stockpilot.research.events.json_acceptance`). The index is the independent record that collected documents are
reconciled against.

Reconciliation. A collected filing counts only if its `-index-headers` page agrees with the index on accession,
issuer CIK and acceptance time (to the second). Presence of an accession alone is not enough.

Company facts. Every fact keeps its reporting period (start, end) separately from when it became public: the
acceptance time of the filing (accession) that reported it, taken from the index. A fact whose accession is not in
the index has no known public time and is excluded. A value reported again by a later filing for the same period is
a duplicate context; if the later value differs, it is a restatement, and it counts only from its own public time.
So a restatement filed after a cutoff can never enter an earlier pack.

Trailing four quarters (`ttm`), as of a time T, from facts public by T, for the latest period end E:
1. a reported ~12-month period ending at E (annual, 350-380 days);
2. otherwise the fiscal-year-to-date period ending at E plus the prior fiscal year minus the prior year's
   comparable year-to-date period (dates matched within 7 days, for 52/53-week calendars);
3. otherwise four reported ~3-month periods chained back from E.
Cumulative year-to-date values are never summed as if they were quarters. Diluted EPS by this arithmetic is an
APPROXIMATION (share counts differ by period) and is labelled so. Shares outstanding are the cover-page
`dei:EntityCommonStockSharesOutstanding` at the latest date public by T, summed across the share classes reported
for that date by the same filing (labelled when summed; company facts carry no class dimension, so rows are told
apart only by value and two classes with identical counts would be counted once). Only USD (and USD/shares, shares) units are accepted.

Passages. Deterministic selection from the primary document's own paragraphs, never paraphrased (`passages`).
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
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
            "items": [i.strip() for i in items], "documents": base["documents"], "provenance": f.provenance()}


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
    """Fact-level records with provenance and public time. Facts from accessions missing from the index, or in
    units other than the accepted ones, are excluded and counted."""
    j = f.json()
    if int(j["cik"]) != index["cik"]:
        raise EdgarError(f"{f.relpath}: company facts are for CIK {j['cik']}, the index for {index['cik']}")
    public = {e["accession"]: e for e in index["entries"]}
    facts, excluded = [], {"accession_not_in_index": 0, "unit": 0}
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
        if old is not None and Decimal(old["value"]) != Decimal(f["value"]):
            restated.append({"period": k, "was": old["value"], "now": f["value"], "by": f["accession"]})
        best[k] = f
    return best, restated


def ttm_candidates(facts: list[dict], concept: str, unit: str, asof: datetime) -> dict:
    """Every method that applies for `concept` at its latest period end E as of `asof`: {method: result}. Used by
    `ttm` (which picks one by priority) and by consistency checks (which compare independent methods)."""
    per, restated = _known(facts, concept, unit, asof)
    if not per:
        return {}
    rows = list(per.values())
    E = max(_d(f["end"]) for f in rows)
    ending = [f for f in rows if _d(f["end"]) == E]
    out = {}
    annual = [f for f in ending if 350 <= _days(f) <= 380]
    if annual:
        out["annual"] = {"value": Decimal(annual[0]["value"]), "components": [annual[0]]}
    for ytd in sorted((f for f in ending if 80 <= _days(f) < 350), key=_days, reverse=True):
        S = _d(ytd["start"])
        fy = [f for f in rows if 350 <= _days(f) <= 380 and _near(_d(f["end"]), S - timedelta(days=1))]
        prior = [f for f in rows if abs(_days(f) - _days(ytd)) <= DAY_TOL
                 and _near(_d(f["start"]), S - timedelta(days=364)) and _near(_d(f["end"]), E - timedelta(days=364))]
        if fy and prior:
            out["fiscal_year_plus_ytd_minus_prior_ytd"] = {
                "value": Decimal(fy[0]["value"]) + Decimal(ytd["value"]) - Decimal(prior[0]["value"]),
                "components": [fy[0], ytd, prior[0]]}
            break
    chain, end = [], E
    for _ in range(4):
        q = next((f for f in rows if 80 <= _days(f) <= 100 and _near(_d(f["end"]), end)), None)
        if q is None:
            break
        chain.append(q)
        end = _d(q["start"]) - timedelta(days=1)
    if len(chain) == 4:
        out["four_quarters"] = {"value": sum((Decimal(q["value"]) for q in chain), Decimal(0)), "components": chain}
    for v in out.values():
        v |= {"concept": concept, "unit": unit, "period_end": E.isoformat(), "restatements_seen": restated}
    return out


METHODS = ("annual", "fiscal_year_plus_ytd_minus_prior_ytd", "four_quarters")


def ttm(facts: list[dict], concepts: tuple, unit: str, asof: datetime) -> Optional[dict]:
    """Trailing-four-quarter value as of `asof` (see the module docstring), choosing among `concepts` the one with
    the latest period end (list order breaks ties) and among methods the first in METHODS. None when none applies."""
    best = None
    for concept in concepts:
        c = ttm_candidates(facts, concept, unit, asof)
        m = next((m for m in METHODS if m in c), None)
        if m is None:
            continue
        out = c[m] | {"method": m}
        if best is None or out["period_end"] > best["period_end"]:
            best = out
    if best is not None and unit == "USD/shares":
        best["approximation"] = ("diluted EPS combined arithmetically across periods: share counts differ by period, "
                                 "so this is an approximation, not a reported trailing EPS")
    return best


def shares_outstanding(facts: list[dict], asof: datetime) -> Optional[dict]:
    rows = [f for f in facts if f["concept"] == SHARES and datetime.fromisoformat(f["public_at"]) <= asof]
    if not rows:
        return None
    end = max(f["end"] for f in rows)
    latest = [f for f in rows if f["end"] == end]
    accn = max(latest, key=lambda f: (f["public_at"], f["accession"]))["accession"]
    same = {f["value"]: f for f in latest if f["accession"] == accn}     # one row per class (values deduplicated)
    total = sum((Decimal(v) for v in same), Decimal(0))
    out = {"value": total, "as_of_date": end, "components": list(same.values())}
    if len(same) > 1:   # company facts carry no class dimension: rows are told apart only by value
        out["approximation"] = (f"summed across {len(same)} share-class rows reported by {accn} for {end}; rows are "
                                "deduplicated by value, so two classes with identical counts would count once")
    return out


def facts_as_of(cf: dict, asof: datetime) -> dict:
    """The four values a valuation row needs, as of `asof`, with full provenance; None entries are unavailable."""
    return {"revenue_ttm": ttm(cf["facts"], REVENUE, "USD", asof),
            "net_income_ttm": ttm(cf["facts"], NET_INCOME, "USD", asof),
            "eps_diluted_ttm": ttm(cf["facts"], EPS_DILUTED, "USD/shares", asof),
            "shares_outstanding": shares_outstanding(cf["facts"], asof)}


# ------------------------------------------------------------------ passages
ADVERSE = ("going concern", "material weakness", "restatement", "impairment", "litigation", "decline", "decrease",
           "loss", "risk")
_FIG = re.compile(r"\$\s?\d|\d(?:\.\d+)?\s?%|\d[\d,]*\s+(?:million|billion)")
MIN_PARA = 100


def paragraphs(doc_html: str) -> list[str]:
    """The document's own paragraphs, in order: split at block-level tags, tags dropped, entities decoded,
    whitespace collapsed. No text is added or reworded."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", doc_html)
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

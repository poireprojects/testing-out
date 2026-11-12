"""N-PORT-P: the registered universe rule from the latest report public before the freeze, and the mapping review.

* `parse_nport` reads the XML (namespace-agnostic): registrant CIK, series ID and name, report period date, the
  submission type, and every holding's name, LEI, title, CUSIP, ISIN, ticker identifier (when the filer gave one),
  value in USD and asset category. N-PORT writes `N/A` for a missing LEI or CUSIP; both are read as absent (in the
  real iShares Russell 1000 report 273 holdings have `<lei>N/A</lei>`).
* `select_report` proves that the report it returns is the latest eligible one. Every NPORT-P and NPORT-P/A in the
  fund's submissions index (retrieved after the freeze) is reconciled against what was collected:
  - the target series is identified by its SEC series ID (`S#########`): from the report's own XML, or from the
    filing header's series list; a filing of another series is excluded with that evidence. A registrant (trust)
    files for many series, so no accession is assumed to belong to the target;
  - a filing accepted at or after the freeze is excluded;
  - public availability: acceptance does not by itself establish it. Form N-PORT (General Instruction F, with rule
    30b1-9) lets the Commission make a report public up to 60 days after the end of its reporting period; under the
    2024 amendments every monthly report becomes public 60 days after the month ends. So a report is taken as public
    before the freeze only when both its acceptance and the 60-day date (the day after period end + 60 days, 00:00
    ET) are before the freeze; one accepted before the freeze whose 60-day date is not is UNCERTAIN. (The rule text
    was not re-read in this session: ecfr.gov and federalregister.gov were unreachable. The test only widens the
    uncertain window and never treats a filing as public before its acceptance.);
  - the latest report is the one for the latest reporting period among target-series reports public before the
    freeze. Interpretation of "the most recent N-PORT-P report" for amendments: an NPORT-P/A for that same period
    that is public before the freeze supersedes the original (the report as last corrected publicly); an amendment
    for an older period never makes that period the latest. Two original reports for one period with different
    holdings, two versions accepted at the same second, or a superseding amendment without holdings: blocked;
  - blocked (`NportError`, nothing is guessed) when any pre-freeze filing that could be that latest report (its
    period is not before the selected one, or unknown) is missing, cannot be classified, is uncertain as to
    publication, or belongs to the target with incomplete holdings; when the index was retrieved before the freeze;
    or when the report's registrant CIK is not the index's.
  The selection records every filing's disposition with its evidence (accession, series ID and its source, report
  period, acceptance, the publication test, raw SHA-256).
* `review_table` applies the existing rule (`universe.select_universe`) and proposes, for each selected holding, a
  ticker (only from the filing's own identifier) and a CIK (only from EDGAR's ticker list by that ticker), with a name
  check. Anything that cannot be resolved stays `unresolved`; nothing is guessed. The current EDGAR ticker list is not
  point-in-time, so every row needs a person's review against the report date before the universe can be frozen.
  A report with fewer than `size` distinct eligible issuers, or a selected holding without a CUSIP, or CUSIPs that
  repeat, is refused.
* `frozen_universe` builds `universe.json` only from a review file that approves every row, with exactly `size`
  members and no symbol, CIK or CUSIP mapped twice.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET_XML
from datetime import date, datetime, time, timedelta
from typing import Optional

from stockpilot.clock import ET

from ..universe import UniverseError, issuer_key, select_universe
from .raw import RawFile

HOLDINGS_FORMS = ("NPORT-P", "NPORT-P/A")
PUBLIC_DAYS = 60
SERIES_RE = re.compile(r"^S\d{9}$")


class NportError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name: str):
    return next((c for c in el.iter() if _local(c.tag) == name), None)


def _text(el, name: str) -> Optional[str]:
    c = _find(el, name)
    return c.text.strip() if c is not None and c.text else None


def _absent(v: Optional[str]) -> Optional[str]:
    return None if v is None or v.strip().upper() in ("", "N/A", "NA", "NONE") else v.strip()


def parse_nport(f: RawFile) -> dict:
    root = ET_XML.fromstring(f.data)
    gen = _find(root, "genInfo")
    if gen is None:
        raise NportError(f"{f.relpath}: no genInfo")
    hdr = _find(root, "headerData")
    rows = []
    for sec in (c for c in root.iter() if _local(c.tag) == "invstOrSec"):
        ids = _find(sec, "identifiers")
        ticker = isin = None
        if ids is not None:
            t = next((c for c in ids.iter() if _local(c.tag) == "ticker"), None)
            ticker = (t.get("value") or (t.text or "")).strip() or None if t is not None else None
            i = next((c for c in ids.iter() if _local(c.tag) == "isin"), None)
            isin = (i.get("value") or (i.text or "")).strip() or None if i is not None else None
        rows.append({"name": _text(sec, "name"), "lei": _absent(_text(sec, "lei")) or "", "title": _text(sec, "title"),
                     "cusip": _absent(_text(sec, "cusip")), "isin": _absent(isin), "ticker_identifier": ticker,
                     "value_usd": _text(sec, "valUSD"), "asset_cat": _text(sec, "assetCat")})
    series_id = _text(gen, "seriesId") or (_text(hdr, "seriesId") if hdr is not None else None)
    return {"series_name": _text(gen, "seriesName"), "series_id": series_id, "reg_cik": _text(gen, "regCik"),
            "reg_name": _text(gen, "regName"), "report_date": _text(gen, "repPdDate"),
            "submission_type": _text(hdr, "submissionType") if hdr is not None else None,
            "holdings": rows, "provenance": f.provenance()}


def certainly_public_by(accepted_at: datetime, report_date: Optional[str]) -> Optional[datetime]:
    """The time by which a report is public under the 60-day rule (None: the period is unknown)."""
    if not report_date:
        return None
    rule = datetime.combine(date.fromisoformat(report_date) + timedelta(days=PUBLIC_DAYS + 1), time(0), ET)
    return max(accepted_at, rule)


def _holdings_digest(r: dict) -> tuple:
    return tuple(sorted((h.get("cusip") or "", h.get("value_usd") or "", h.get("asset_cat") or "")
                        for h in r["holdings"]))


def select_report(fund_index: dict, reports: dict[str, dict], target: dict, freeze: datetime,
                  header_series: Optional[dict[str, list[str]]] = None) -> dict:
    """The latest eligible N-PORT-P of `target` ({"series_id", "series_name"}) public before `freeze`, with a
    reconciliation of every NPORT-P and NPORT-P/A in `fund_index`. `reports`: {accession: parse_nport(...)};
    `header_series`: {accession: series IDs from its collected filing header}. Raises `NportError` when the latest
    eligible report cannot be proven (see the module docstring)."""
    sid = (target.get("series_id") or "").strip()
    name = (target.get("series_name") or "").strip()
    if not SERIES_RE.match(sid):
        raise NportError(f"the target series has no SEC series ID ({sid!r}): it cannot be identified by a stable "
                         "identifier")
    header_series = header_series or {}
    retrieved = [datetime.fromisoformat(p["retrieved_at"]) for p in fund_index.get("provenance", [])]
    if not retrieved or min(retrieved) < freeze:
        when = min(retrieved).isoformat() if retrieved else "at an unknown time"
        raise NportError(f"the fund's submissions index was retrieved {when}, before the freeze {freeze.isoformat()}: "
                         "filings accepted in between are unknown")
    rows, blocking = [], []
    for e in fund_index["entries"]:
        if not e["form"].startswith("NPORT"):
            continue
        acc, at = e["accession"], datetime.fromisoformat(e["accepted_at"])
        r = reports.get(acc)
        row = {"accession": acc, "form": e["form"], "accepted_at": e["accepted_at"],
               "index_report_date": e.get("report_date") or None, "series_id": None, "series_source": None,
               "report_date": None, "public_by": None, "raw_sha256": r["provenance"]["raw_sha256"] if r else None,
               "status": "", "why": ""}
        rows.append(row)
        if r is not None:
            row |= {"series_id": r.get("series_id"), "series_source": "report XML" if r.get("series_id") else None,
                    "report_date": r.get("report_date")}
        if e["form"] not in HOLDINGS_FORMS:
            row |= {"status": "excluded", "why": f"{e['form']} is not an N-PORT-P holdings report"}
            continue
        if at >= freeze:
            row |= {"status": "excluded", "why": "accepted at or after the freeze"}
            continue
        if r is not None:
            if r.get("reg_cik") and int(r["reg_cik"]) != int(fund_index["cik"]):
                row |= {"status": "blocked", "why": f"the report's registrant CIK {r['reg_cik']} is not the index's "
                                                   f"{fund_index['cik']}"}
                blocking.append(row)
                continue
        if not row["series_id"] and header_series.get(acc):
            ids = header_series[acc]
            row |= {"series_id": ids[0] if len(ids) == 1 else ",".join(ids), "series_source": "filing header"}
        rd = row["report_date"] or row["index_report_date"]
        if rd:
            pb = certainly_public_by(at, rd)
            row["public_by"] = pb.isoformat()
        if not row["series_id"]:
            row |= {"status": "unclassified", "why": ("its report XML names no series ID" if r is not None else
                                                      "neither its report XML nor a filing header was collected")
                    + ", so its series cannot be established"}
        elif row["series_id"] != sid:
            row |= {"status": "excluded",
                    "why": f"another series ({row['series_id']}, from the {row['series_source']})"}
        elif r is None:
            row |= {"status": "uncollected", "why": "a target-series filing whose report XML was not collected"}
        elif row["public_by"] is None:
            row |= {"status": "uncertain", "why": "no reporting period: publication before the freeze cannot be tested"}
        elif datetime.fromisoformat(row["public_by"]) >= freeze:
            row |= {"status": "uncertain", "why": f"accepted before the freeze, but not certainly public before it: "
                                                  f"the 60-day date is {row['public_by']}"}
        elif not r["holdings"]:
            row |= {"status": "incomplete", "why": "the report XML lists no holdings"}
        else:
            row |= {"status": "eligible", "why": "target series, public before the freeze"}
    eligible = [x for x in rows if x["status"] == "eligible"]
    latest = max((x["report_date"] for x in eligible), default=None)

    def could_be_latest(x: dict) -> bool:
        rd = x["report_date"] or x["index_report_date"]
        return latest is None or rd is None or rd >= latest

    for x in rows:
        if x["status"] in ("unclassified", "uncollected", "uncertain", "incomplete") and could_be_latest(x):
            blocking.append(x)
        elif x["status"] in ("unclassified", "uncollected", "uncertain", "incomplete"):
            x["why"] += f" (not needed: its period precedes the selected period {latest})"
    if blocking:
        raise NportError("the latest eligible report cannot be proven: " + "; ".join(
            f"{x['form']} {x['accession']} ({x['status']}: {x['why']})" for x in blocking[:5]))
    if latest is None:
        raise NportError(f"no {name or sid} NPORT-P public before {freeze.isoformat()} in the collected files")
    same = sorted((x for x in eligible if x["report_date"] == latest), key=lambda x: (x["accepted_at"], x["accession"]))
    originals = [x for x in same if x["form"] == "NPORT-P"]
    if len({_holdings_digest(reports[x["accession"]]) for x in originals}) > 1:
        raise NportError(f"{len(originals)} original NPORT-P reports for {latest} with different holdings: ambiguous")
    if len(same) > 1 and same[-1]["accepted_at"] == same[-2]["accepted_at"]:
        raise NportError(f"two versions of the {latest} report accepted at the same time: no latest version")
    chosen = same[-1]
    for x in same[:-1]:
        x |= {"status": "superseded",
              "why": f"superseded by {chosen['form']} {chosen['accession']} for the same period"}
    chosen |= {"status": "selected"}
    for x in eligible:
        if x["status"] == "eligible":
            x |= {"status": "excluded", "why": f"an earlier period than the selected {latest}"}
    r = reports[chosen["accession"]]
    if name and (r.get("series_name") or "").strip().lower() != name.lower():
        chosen["note"] = f"series name in the report is {r.get('series_name')!r} (identified by series ID {sid})"
    return {"accession": chosen["accession"], "accepted_at": chosen["accepted_at"], "report_date": r["report_date"],
            "form": chosen["form"], "series_id": sid, "series_name": r.get("series_name"),
            "public_by": chosen["public_by"], "raw_sha256": chosen["raw_sha256"], "report": r,
            "reconciliation": sorted(rows, key=lambda x: (x["accepted_at"], x["accession"])),
            "rule": {"public": f"accepted before the freeze and period end + {PUBLIC_DAYS} days before the freeze",
                     "latest": "latest reporting period; a public same-period NPORT-P/A supersedes the original"}}


def _norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(inc|corp|corporation|co|company|ltd|plc|the|class [a-z]|cl [a-z])\b", " ", s)
    return " ".join(s.split())


def review_table(selected: dict, tickers_json: dict, size: int) -> list[dict]:
    """Proposed mappings for the rule's top holdings; `tickers_json` is EDGAR's company_tickers.json (current)."""
    by_ticker = {v["ticker"].upper(): v for v in tickers_json.values()}
    rows = selected["report"]["holdings"]
    eligible = [h for h in rows if h.get("asset_cat") == "EC"]
    issuers = {issuer_key(h) for h in eligible}
    if len(issuers) < size:
        raise NportError(f"the report lists {len(issuers)} distinct common-equity issuer(s), fewer than {size}: "
                         "incomplete holdings")
    seen: dict = {}
    for h in eligible:
        if h["cusip"] and h["cusip"] in seen and seen[h["cusip"]] is not h:
            raise NportError(f"CUSIP {h['cusip']} appears on two common-equity holdings: the mapping key is ambiguous")
        seen[h["cusip"]] = h
    proposals = {}
    for h in rows:
        t = (h.get("ticker_identifier") or "").upper()
        hit = by_ticker.get(t) if t else None
        proposals[h["cusip"]] = {"symbol": t or None, "cik": str(hit["cik_str"]) if hit else None,
                                 "edgar_title": hit["title"] if hit else None}
    placeholder = {h["cusip"]: {"symbol": None, "cik": None} for h in rows}   # the rule ranks; mapping is reviewed
    try:
        picked = select_universe([dict(h, value_usd=h["value_usd"] or "0") for h in rows], placeholder, size)
    except UniverseError as exc:
        raise NportError(str(exc)) from exc
    out = []
    for m in picked:
        if not m["cusip"]:
            raise NportError(f"selected holding {m['name']} (rank {m['rank']}) has no CUSIP: it cannot be mapped")
        h = next(x for x in rows if x["cusip"] == m["cusip"])
        p = proposals[m["cusip"]]
        status, why = "needs_review", []
        if not p["symbol"]:
            status, why = "unresolved", ["the report gives no ticker identifier for this CUSIP"]
        elif not p["cik"]:
            status, why = "unresolved", [f"ticker {p['symbol']} is not in EDGAR's ticker list"]
        elif _norm_name(p["edgar_title"]) != _norm_name(h["name"]):
            why = [f"names differ: N-PORT '{h['name']}' vs EDGAR '{p['edgar_title']}'"]
        out.append({"rank": m["rank"], "name": h["name"], "lei": h["lei"], "cusip": h["cusip"],
                    "value_usd": h["value_usd"], "proposed_symbol": p["symbol"], "proposed_cik": p["cik"],
                    "edgar_title": p["edgar_title"], "status": status, "notes": why,
                    "source": {"nport_accession": selected["accession"], "nport_accepted_at": selected["accepted_at"],
                               "nport_report_date": selected["report_date"],
                               "nport_series_id": selected.get("series_id")}})
    return out


def frozen_universe(table: list[dict], review: dict, size: Optional[int] = None) -> dict:
    """universe.json from a review that approves every row (reviewer, date, and per-CUSIP symbol and CIK): exactly
    `size` rows when given, and no symbol, CIK or CUSIP mapped twice."""
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise NportError("the mapping review names no reviewer or date")
    approved = {r["cusip"]: r for r in review.get("rows", []) if r.get("approved")}
    missing = [t["cusip"] for t in table if t["cusip"] not in approved]
    if missing:
        raise NportError(f"{len(missing)} selected holding(s) lack an approved mapping: {', '.join(missing)}")
    if size is not None and len(table) != size:
        raise NportError(f"the review table has {len(table)} row(s), not {size}")
    members = []
    for t in table:
        a = approved[t["cusip"]]
        if not a.get("symbol") or not a.get("cik"):
            raise NportError(f"{t['cusip']}: approved without a symbol and CIK")
        members.append({"rank": t["rank"], "symbol": a["symbol"], "name": t["name"], "cik": a["cik"],
                        "cusip": t["cusip"], "value_usd": t["value_usd"]})
    for key in ("symbol", "cik", "cusip"):
        vals = [str(m[key]).upper() for m in members]
        dup = sorted({v for v in vals if vals.count(v) > 1})
        if dup:
            raise NportError(f"duplicate issuer mapping: {key} {', '.join(dup)} is mapped to more than one holding")
    src = table[0]["source"] if table else {}
    return {"rule": "registration FROZEN['universe']", "source": src,
            "review": {"reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
                       "fixture": bool(review.get("fixture"))}, "members": members}

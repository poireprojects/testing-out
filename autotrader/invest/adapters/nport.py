"""N-PORT-P: the registered universe rule from a report public before the freeze, and the mapping review table.

* `parse_nport` reads the XML (namespace-agnostic): series name, report period date, and every holding's name, LEI,
  title, CUSIP, ticker identifier (when the filer gave one), value in USD and asset category.
* `select_report` picks the most recent N-PORT-P for the registered series whose acceptance time (from the fund's
  submissions index) is before the freeze, and keeps its accession, acceptance (publication) time and report date.
* `review_table` applies the existing rule (`universe.select_universe`) and proposes, for each selected holding, a
  ticker (only from the filing's own identifier) and a CIK (only from EDGAR's ticker list by that ticker), with a name
  check. Anything that cannot be resolved stays `unresolved`; nothing is guessed. The current EDGAR ticker list is not
  point-in-time, so every row needs a person's review against the report date before the universe can be frozen.
* `frozen_universe` builds `universe.json` only from a review file that approves every row.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET_XML
from datetime import datetime
from typing import Optional

from ..universe import select_universe
from .raw import RawFile


class NportError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name: str):
    return next((c for c in el.iter() if _local(c.tag) == name), None)


def _text(el, name: str) -> Optional[str]:
    c = _find(el, name)
    return c.text.strip() if c is not None and c.text else None


def parse_nport(f: RawFile) -> dict:
    root = ET_XML.fromstring(f.data)
    gen = _find(root, "genInfo")
    if gen is None:
        raise NportError(f"{f.relpath}: no genInfo")
    rows = []
    for sec in (c for c in root.iter() if _local(c.tag) == "invstOrSec"):
        ids = _find(sec, "identifiers")
        ticker = None
        if ids is not None:
            t = next((c for c in ids.iter() if _local(c.tag) == "ticker"), None)
            ticker = (t.get("value") or (t.text or "")).strip() or None if t is not None else None
        rows.append({"name": _text(sec, "name"), "lei": _text(sec, "lei") or "", "title": _text(sec, "title"),
                     "cusip": _text(sec, "cusip"), "ticker_identifier": ticker, "value_usd": _text(sec, "valUSD"),
                     "asset_cat": _text(sec, "assetCat")})
    return {"series_name": _text(gen, "seriesName"), "report_date": _text(gen, "repPdDate"), "holdings": rows,
            "provenance": f.provenance()}


def select_report(fund_index: dict, reports: dict[str, dict], series_name: str, freeze: datetime) -> dict:
    """`reports`: {accession: parsed N-PORT}. The most recent NPORT-P of `series_name` accepted before `freeze`."""
    cands = []
    for e in fund_index["entries"]:
        if e["form"] != "NPORT-P" or datetime.fromisoformat(e["accepted_at"]) >= freeze:
            continue
        r = reports.get(e["accession"])
        if r is None or (r["series_name"] or "").strip().lower() != series_name.strip().lower():
            continue
        cands.append((e["accepted_at"], e["accession"], e, r))
    if not cands:
        raise NportError(f"no {series_name} NPORT-P accepted before {freeze.isoformat()} in the collected files")
    _, acc, e, r = max(cands, key=lambda x: (datetime.fromisoformat(x[0]), x[1]))
    return {"accession": acc, "accepted_at": e["accepted_at"], "report_date": r["report_date"], "report": r}


def _norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(inc|corp|corporation|co|company|ltd|plc|the|class [a-z]|cl [a-z])\b", " ", s)
    return " ".join(s.split())


def review_table(selected: dict, tickers_json: dict, size: int) -> list[dict]:
    """Proposed mappings for the rule's top holdings; `tickers_json` is EDGAR's company_tickers.json (current)."""
    by_ticker = {v["ticker"].upper(): v for v in tickers_json.values()}
    rows = selected["report"]["holdings"]
    proposals = {}
    for h in rows:
        t = (h.get("ticker_identifier") or "").upper()
        hit = by_ticker.get(t) if t else None
        proposals[h["cusip"]] = {"symbol": t or None, "cik": str(hit["cik_str"]) if hit else None,
                                 "edgar_title": hit["title"] if hit else None}
    placeholder = {h["cusip"]: {"symbol": None, "cik": None} for h in rows}   # the rule ranks; mapping is reviewed
    picked = select_universe([dict(h, value_usd=h["value_usd"] or "0") for h in rows], placeholder, size)
    out = []
    for m in picked:
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
                               "nport_report_date": selected["report_date"]}})
    return out


def frozen_universe(table: list[dict], review: dict) -> dict:
    """universe.json from a review that approves every row (reviewer, date, and per-CUSIP symbol and CIK)."""
    if not review.get("reviewer") or not review.get("reviewed_at"):
        raise NportError("the mapping review names no reviewer or date")
    approved = {r["cusip"]: r for r in review.get("rows", []) if r.get("approved")}
    missing = [t["cusip"] for t in table if t["cusip"] not in approved]
    if missing:
        raise NportError(f"{len(missing)} selected holding(s) lack an approved mapping: {', '.join(missing)}")
    members = []
    for t in table:
        a = approved[t["cusip"]]
        if not a.get("symbol") or not a.get("cik"):
            raise NportError(f"{t['cusip']}: approved without a symbol and CIK")
        members.append({"rank": t["rank"], "symbol": a["symbol"], "name": t["name"], "cik": a["cik"],
                        "cusip": t["cusip"], "value_usd": t["value_usd"]})
    src = table[0]["source"] if table else {}
    return {"rule": "registration FROZEN['universe']", "source": src,
            "review": {"reviewer": review["reviewer"], "reviewed_at": review["reviewed_at"],
                       "fixture": bool(review.get("fixture"))}, "members": members}

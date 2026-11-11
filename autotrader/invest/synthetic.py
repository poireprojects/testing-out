"""Synthetic data and the scripted fixture for the offline dry run. Nothing here is market data.

* Calendar: the NYSE rules for a simulated pilot with fill months 2031-01 to 2031-12, so no dry-run record can be
  mistaken for real history or for a real forward record.
* Universe: 14 invented N-PORT rows (one two-class issuer, one preferred share) through the real selection rule.
* Prices: a seeded random walk for VOO and twelve invented companies (SYNTH01..SYNTH12); a 2-for-1 SYNTH01 split;
  quarterly VOO and SYNTH03 dividends.
* Filings: invented quarterly 10-Q/10-K text and facts, accepted at 16:05 ET (after that day's cutoff, so a filing
  on a cutoff day waits a month: the look-ahead rule in action).
* Fixture: twelve scripted months covering buy, increase, hold, reduce, sell, 100% VOO, a missing answer, a late
  answer and a rejected (four-stock) answer.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from stockpilot.clock import MarketCalendar

from .data import write_data_dir
from .registration import BENCHMARK, et, schedule
from .universe import select_universe

SEED = 20261111
FIRST_FILL_MONTH = "2031-01"
START, END = date(2030, 11, 3), date(2031, 12, 31)
FIXTURE_COST_USD = "0.20"           # the registration's central monthly estimate, labelled as an estimate

FIXTURE_SCRIPT: dict[int, dict] = {
    1: {"kind": "decision", "label": "buy two stocks", "positions": [("SYNTH01", "buy", 8), ("SYNTH02", "buy", 6)]},
    2: {"kind": "decision", "label": "hold one, increase one",
        "positions": [("SYNTH01", "hold", 0), ("SYNTH02", "increase", 9)]},
    3: {"kind": "decision", "label": "hold, sell, buy",
        "positions": [("SYNTH01", "hold", 0), ("SYNTH02", "sell", 0), ("SYNTH03", "buy", 7)]},
    4: {"kind": "missing", "label": "no answer: holdings kept"},
    5: {"kind": "decision", "label": "four stocks: rejected, holdings kept",
        "positions": [("SYNTH01", "hold", 0), ("SYNTH03", "hold", 0), ("SYNTH04", "buy", 5), ("SYNTH05", "buy", 5)]},
    6: {"kind": "late", "label": "frozen after the open: holdings kept",
        "positions": [("SYNTH01", "reduce", 4), ("SYNTH03", "hold", 0)]},
    7: {"kind": "decision", "label": "reduce one, hold one",
        "positions": [("SYNTH01", "reduce", 4), ("SYNTH03", "hold", 0)]},
    8: {"kind": "decision", "label": "hold both", "positions": [("SYNTH01", "hold", 0), ("SYNTH03", "hold", 0)]},
    9: {"kind": "decision", "label": "sell both: 100% VOO",
        "positions": [("SYNTH01", "sell", 0), ("SYNTH03", "sell", 0)]},
    10: {"kind": "decision", "label": "stay 100% VOO", "positions": []},
    11: {"kind": "decision", "label": "buy one at the cap", "positions": [("SYNTH04", "buy", 10)]},
    12: {"kind": "decision", "label": "hold it", "positions": [("SYNTH04", "hold", 0)]},
}

C2 = Decimal("0.01")


def _q(x: float) -> str:
    return str(Decimal(repr(x)).quantize(C2, rounding=ROUND_HALF_EVEN))


def sessions(start: date = START, end: date = END) -> list[date]:
    return MarketCalendar().trading_days_between(start, end)


def nport_rows() -> tuple[list[dict], dict[str, dict]]:
    rows, cmap = [], {}
    for i in range(1, 13):
        cusip = f"99SYN{i:02d}00"
        rows.append({"name": f"Synthetic Company {i:02d}", "lei": f"SYNLEI{i:02d}", "cusip": cusip, "asset_cat": "EC",
                     "value_usd": str(1_000_000_000 * (40 - 2 * i))})
        cmap[cusip] = {"symbol": f"SYNTH{i:02d}", "cik": f"99000000{i:02d}"}
    rows.append({"name": "Synthetic Company 01 Class C", "lei": "SYNLEI01", "cusip": "99SYN01C0", "asset_cat": "EC",
                 "value_usd": "1000000000"})          # second class of issuer 01: the smaller one is dropped
    rows.append({"name": "Synthetic Preferred Trust", "lei": "SYNLEI99", "cusip": "99SYNPF00", "asset_cat": "EP",
                 "value_usd": "90000000000"})          # not common equity: excluded however large
    rows.append({"name": "Synthetic Company 13", "lei": "SYNLEI13", "cusip": "99SYN1300", "asset_cat": "EC",
                 "value_usd": "500000000"})            # thirteenth: outside the size limit
    return rows, cmap


INDEX_RETRIEVED_AT = "2032-01-05T12:00:00+00:00"     # simulated: one index collected after the whole pilot


def index_for(filings: list[dict], retrieved_at: str = INDEX_RETRIEVED_AT, symbols=None) -> dict:
    """A synthetic EDGAR submission index listing `filings` (in real use the collector writes it from EDGAR)."""
    out: dict = {}
    for f in filings:
        out.setdefault(f["symbol"], {"cik": f["cik"], "retrieved_at": retrieved_at, "source": "SYNTHETIC index",
                                     "filings": []})["filings"].append(
            {"accession": f["accession"], "form": f["form"], "accepted_at": f["accepted_at"],
             "items": f.get("items", "")})
    for sym in symbols or []:
        out.setdefault(sym, {"cik": "", "retrieved_at": retrieved_at, "source": "SYNTHETIC index", "filings": []})
    return {"symbols": out, "price_exceptions": []}


def build(d: Path, *, voo_split: tuple[str, int, int] | None = None, drop_prices: list[tuple[str, str, str]] = (),
          late_filing: bool = True, omit_filings: tuple[str, ...] = (), omit_index: tuple[str, ...] = (),
          no_index: bool = False) -> str:
    """Write the synthetic data directory; returns the manifest's SHA-256. For tests: `voo_split`; `drop_prices`
    ((symbol, date, field)); `omit_filings` (accessions listed in the index but not collected); `omit_index`
    (symbols with no collected index); `no_index` (no index file at all)."""
    rng = random.Random(SEED)
    days = sessions()
    rows, cmap = nport_rows()
    members = select_universe(rows, cmap, size=12)
    syms = [m["symbol"] for m in members]
    splits = [{"symbol": "SYNTH01", "ex_date": "2031-02-18", "num": 2, "den": 1}]
    if voo_split:
        splits.append({"symbol": BENCHMARK, "ex_date": voo_split[0], "num": voo_split[1], "den": voo_split[2]})
    prices: dict[str, dict] = {}
    for k, sym in enumerate([BENCHMARK] + syms):
        px = 500.0 if sym == BENCHMARK else 20.0 + 23.0 * k
        vol = 0.008 if sym == BENCHMARK else 0.010
        series = {}
        for day in days:
            for sp in splits:
                if sp["symbol"] == sym and day.isoformat() == sp["ex_date"]:
                    px = px * sp["den"] / sp["num"]
            o = px * (1 + rng.gauss(0, 0.002))
            c = o * (1 + rng.gauss(0.0003, vol))
            series[day.isoformat()] = {"open": _q(o), "close": _q(c)}
            px = c
        prices[sym] = series
    for sym, day, field in drop_prices:
        prices[sym][day].pop(field, None)
    dividends = []
    for ex, pay in (("2031-03-27", "2031-03-31"), ("2031-06-26", "2031-06-30"), ("2031-09-26", "2031-09-30"),
                    ("2031-12-19", "2031-12-23")):
        dividends.append({"symbol": BENCHMARK, "ex_date": ex, "pay_date": pay, "amount": "1.70"})
        dividends.append({"symbol": "SYNTH03", "ex_date": ex, "pay_date": pay, "amount": "0.35"})
    filings = []
    for k, m in enumerate(members):
        sym = m["symbol"]
        base_rev = 2000 + 700 * k
        shares = 900_000_000 + 37_000_000 * k
        for q, (acc_day, form, period) in enumerate(((date(2030, 11, 5), "10-Q", "2030-09-30"),
                                                       (date(2031, 2, 10), "10-K", "2030-12-31"),
                                                       (date(2031, 5, 6), "10-Q", "2031-03-31"),
                                                       (date(2031, 8, 5), "10-Q", "2031-06-30"),
                                                       (date(2031, 11, 4), "10-Q", "2031-09-30"))):
            g = 4 + (k * 3 + q * 2) % 11
            rev_q = base_rev * (1 + 0.02 * q)
            rev_ttm = round(rev_q * 4 * (1 - g / 400))
            margin = 9 + (k + q) % 8
            ni_ttm = round(rev_ttm * margin / 100)
            eps = Decimal(ni_ttm * 1_000_000) / Decimal(shares)
            acc = f"99000000{k + 1:02d}-{acc_day.year % 100:02d}-{q + 1:06d}"
            accepted = et(acc_day, 16, 5)
            filings.append({
                "symbol": sym, "cik": m["cik"], "accession": acc, "form": form, "period_end": period,
                "accepted_at": accepted.isoformat(), "synthetic": True,
                "facts": {"shares_outstanding": str(shares), "revenue_ttm_musd": str(rev_ttm),
                          "net_income_ttm_musd": str(ni_ttm), "eps_diluted_ttm": str(eps.quantize(C2))},
                "passages": [
                    {"section": "MD&A: results", "text":
                        f"Revenue for the quarter was ${round(rev_q):,} million, an increase of {g}% from the same "
                        f"quarter a year earlier. Growth came mainly from existing customers."},
                    {"section": "MD&A: margins", "text":
                        f"Operating margin was {margin + 4}% for the quarter. Higher input costs reduced gross "
                        f"margin by {1 + q % 3} percentage points."},
                    {"section": "Risk factors", "text":
                        f"Our largest customer accounted for {10 + k}% of revenue. The loss of this customer would "
                        f"materially reduce our results."},
                ]})
    if late_filing:     # accepted after the 2031-03-31 cutoff (16:30 ET on the cutoff day): must wait for April's pack
        filings.append({
            "symbol": "SYNTH02", "cik": "9900000002", "accession": "9900000002-31-900001", "form": "8-K", "items": "2.02",
            "period_end": "2031-03-31", "accepted_at": et(date(2031, 3, 31), 16, 30).isoformat(), "synthetic": True,
            "facts": {}, "passages": [{"section": "Item 2.02", "text":
                                       "Preliminary revenue for the quarter was $3,100 million, above guidance."}]})
    index = None if no_index else index_for(filings, symbols=syms)
    for sym in omit_index:
        index["symbols"].pop(sym)
    filings = [f for f in filings if f["accession"] not in set(omit_filings)]
    universe = {"rule": "registration FROZEN['universe'] applied to synthetic N-PORT rows",
                "source": "SYNTHETIC (autotrader/invest/synthetic.py)", "members": members}
    return write_data_dir(d, synthetic=True, sources={"all": "SYNTHETIC: generated by autotrader/invest/synthetic.py, "
                                                               f"seed {SEED}; not market data"},
                          prices=prices, actions={"splits": splits, "dividends": dividends}, universe=universe,
                          filings=filings, index=index)


def end_of_pilot() -> datetime:
    return schedule(FIRST_FILL_MONTH)[-1].period_end.close + timedelta(hours=2)

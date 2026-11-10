"""Historical regulatory fees for research replays (never used by the desktop app).

The desktop schedule (stockpilot/risk/fees.py) starts on 2025-05-14 and uses its first row for every earlier date:
SEC fee $0 and the 2025 TAF. In the development sample, 100 of 160 events are earlier than that row. This module
gives research replays the schedule in force on each date, so the app's own cost model and published results stay
unchanged.

**Verification status.** The dates and rates below up to 2025-05-13 are from the SEC's annual and mid-year Section
31 fee rate advisories and FINRA's TAF schedule as recalled for this change; www.sec.gov and www.finra.org could
not be reached from the session that wrote it. Each row says whether it was checked against a primary source.
`unverified()` lists the rows that must be checked (with the cited source) before any paid run is approved; the
preflight reports them as unresolved findings. Rows from 2025-05-14 are copied from the app's schedule, which
was checked against primary sources on 2026-11-06 (research/COSTS.md).

Broker conventions (Alpaca): no commission; SEC fee and TAF on sales; CAT fee on buys and sells, per executed
equivalent share; each fee type summed per account per day and rounded up to a cent once. The CAT pass-through
start date and whether the daily rounding rule applied before 2026 are not verified; this schedule charges CAT on
every date (the conservative choice), and the preflight lists both as unresolved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from stockpilot.risk.fees import CostModel, FEE_SCHEDULE as APP_SCHEDULE


@dataclass(frozen=True)
class HistRates:
    effective: date
    sec_per_million: float          # Section 31, $ per $1,000,000 of sale proceeds
    taf_per_share: float
    taf_max: float
    cat_per_share: float
    source: str
    verified: bool


CAT = 0.000003                      # Alpaca fee schedule (2026-11-06); start date of the pass-through unverified

SCHEDULE: tuple[HistRates, ...] = (
    HistRates(date(2022, 5, 16), 22.90, 0.000130, 6.49, CAT,
              "SEC FY2022 Section 31 rate $22.90/M; FINRA TAF 2022 $0.000130/share, max $6.49", False),
    HistRates(date(2023, 1, 1), 22.90, 0.000145, 7.27, CAT,
              "FINRA TAF 2023 $0.000145/share, max $7.27 (SEC rate unchanged)", False),
    HistRates(date(2023, 2, 27), 8.00, 0.000145, 7.27, CAT,
              "SEC Fee Rate Advisory, FY2023: $8.00/M effective 2023-02-27", False),
    HistRates(date(2024, 1, 1), 8.00, 0.000166, 8.30, CAT,
              "FINRA TAF 2024 $0.000166/share, max $8.30 (SEC rate unchanged)", False),
    HistRates(date(2024, 5, 22), 27.80, 0.000166, 8.30, CAT,
              "SEC Fee Rate Advisory, FY2024 mid-year: $27.80/M effective 2024-05-22", False),
    HistRates(date(2025, 1, 1), 27.80, 0.000166, 8.30, CAT,
              "FINRA TAF 2025 $0.000166/share, max $8.30 (SEC rate unchanged)", False),
) + tuple(
    HistRates(r.effective, r.sec_rate * 1e6, r.taf_per_share, r.taf_max, r.cat_per_share, "app schedule: " + r.source,
              True) for r in APP_SCHEDULE)

FIRST_COVERED = SCHEDULE[0].effective


class FeeScheduleGap(ValueError):
    pass


def rates_on(day: date) -> HistRates:
    if day < FIRST_COVERED:
        raise FeeScheduleGap(f"no historical fee schedule before {FIRST_COVERED}")
    out = SCHEDULE[0]
    for r in SCHEDULE:
        if day >= r.effective:
            out = r
    return out


def cost_model(day: date, slippage_bps: float = 5.0) -> CostModel:
    r = rates_on(day)
    return CostModel(sec_rate=r.sec_per_million / 1e6, taf_per_share=r.taf_per_share, taf_max=r.taf_max,
                     cat_per_share=r.cat_per_share, slippage_bps=slippage_bps)


def unverified() -> list[dict]:
    return [{"effective": r.effective.isoformat(), "source_to_check": r.source} for r in SCHEDULE if not r.verified]


def fee_parts(day: date, qty: float, sale_notional: float) -> dict:
    """Unrounded fees of one round trip: SEC and TAF on the sale, CAT on the buy and the sale."""
    r = rates_on(day)
    return {"sec": sale_notional * r.sec_per_million / 1e6, "taf": min(qty * r.taf_per_share, r.taf_max),
            "cat": 2 * qty * r.cat_per_share}


def ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 9)) / 100 if x > 0 else 0.0


def daily_fees(parts: list[dict]) -> tuple[list[float], dict]:
    """Per-trade dollar fees for one decider's trades on one day: each fee type's daily total rounded up to a cent
    once and shared pro rata. Returns (per trade, the day's rounded totals by type)."""
    tot = {k: sum(p[k] for p in parts) for k in ("sec", "taf", "cat")}
    rounded = {k: ceil_cent(v) for k, v in tot.items()}
    out = [sum(rounded[k] * p[k] / tot[k] for k in tot if tot[k] > 0) for p in parts]
    return out, rounded


def app_rates_on(day: Optional[date]):
    """What the desktop schedule would have used (for the before/after table in the amendment)."""
    from stockpilot.risk.fees import rates_on as app
    return app(day)

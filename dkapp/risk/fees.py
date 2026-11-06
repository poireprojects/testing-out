"""Trading cost model — regulatory fees, spread and slippage.

Alpaca charges no commission on U.S. stocks. It passes through regulatory fees. The rates and the
rounding rule below were checked on 2026-11-06 against these primary sources (research/COSTS.md lists
the SHA-256 of the copies that were read):

* Alpaca Securities Brokerage Fee Schedule (files.alpaca.markets/disclosures/library/BrokFeeSched.pdf):
  SEC fee $0.0000206 × trade value (sells), FINRA TAF $0 per share (sells), FINRA CAT
  $0.000003 per executed equivalent share (buys and sells). "Fees are calculated on the exact executed
  quantity, including fractional shares ... Each fee type is aggregated separately at the daily,
  per-account level. After aggregation, each fee total is rounded up to the nearest cent."
* FINRA Information Notice 2026-03-17: Section 31 rate $20.60 per million from 2026-04-04 (was $0.00).
* FINRA SR-FINRA-2026-021: TAF paused at $0.00 from 2026-10-01 to 2026-12-31; it resumes on
  2027-01-01 at $0.000195 per share sold (max $9.79 per trade).

Build 20 rounded each sale's SEC fee and TAF up to a cent ($0.02 per $20 sale ≈ 0.10% of the trade).
The broker actually rounds once per fee type per DAY, so on small trades the real fee cost is far
lower. `CostModel.legacy_build20()` keeps the old rule so the frozen baseline stays reproducible.

Spread and slippage are MODELED assumptions (the history has no quotes): half the quoted spread plus
`slippage_bps` per side. They dominate the cost of a $20 trade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable, Optional


def _ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100 if x > 0 else 0.0


@dataclass(frozen=True)
class FeeRates:
    effective: date
    sec_rate: float        # $ per $ of sale proceeds
    taf_per_share: float   # $ per share sold
    taf_max: float         # $ per trade
    cat_per_share: float   # $ per share bought or sold
    source: str


# Dated schedule (oldest first). Dates before the first entry use the first entry.
FEE_SCHEDULE: tuple[FeeRates, ...] = (
    FeeRates(date(2026, 1, 1), 0.0, 0.000195, 9.79, 0.000003,
             "SEC $0.00/M since 2025-05-14; FINRA TAF 2026 schedule; CAT per Alpaca fee schedule"),
    FeeRates(date(2026, 4, 4), 20.60 / 1_000_000, 0.000195, 9.79, 0.000003,
             "FINRA Information Notice 2026-03-17: Section 31 $20.60/M"),
    FeeRates(date(2026, 10, 1), 20.60 / 1_000_000, 0.0, 0.0, 0.000003,
             "SR-FINRA-2026-021: TAF $0.00 for Oct-Dec 2026"),
    FeeRates(date(2027, 1, 1), 20.60 / 1_000_000, 0.000195, 9.79, 0.000003,
             "SR-FINRA-2026-021: TAF resumes 2027-01-01 (the SEC rate may change with the FY2027 order)"),
)

# Defaults are the forward-looking (2027) rates: the TAF pause is temporary, so planning ignores it.
SEC_FEE_RATE = FEE_SCHEDULE[-1].sec_rate
FINRA_TAF_PER_SHARE = FEE_SCHEDULE[-1].taf_per_share
FINRA_TAF_MAX = FEE_SCHEDULE[-1].taf_max
FINRA_CAT_PER_SHARE = FEE_SCHEDULE[-1].cat_per_share


def rates_on(day: Optional[date]) -> FeeRates:
    if day is None:
        return FEE_SCHEDULE[-1]
    out = FEE_SCHEDULE[0]
    for r in FEE_SCHEDULE:
        if day >= r.effective:
            out = r
    return out


@dataclass(frozen=True)
class FeeBreakdown:
    sec: float = 0.0
    taf: float = 0.0
    cat: float = 0.0

    @property
    def total(self) -> float:
        return self.sec + self.taf + self.cat

    def __add__(self, o: "FeeBreakdown") -> "FeeBreakdown":
        return FeeBreakdown(self.sec + o.sec, self.taf + o.taf, self.cat + o.cat)


@dataclass(frozen=True)
class CostModel:
    sec_rate: float = SEC_FEE_RATE
    taf_per_share: float = FINRA_TAF_PER_SHARE
    taf_max: float = FINRA_TAF_MAX
    cat_per_share: float = FINRA_CAT_PER_SHARE
    slippage_bps: float = 5.0  # per side, on top of half the quoted spread (modeled, not a broker charge)
    rounding: str = "daily"    # "daily": the broker rounds each fee type's DAILY total up (Alpaca's rule)
                               # "per_trade": every sale's SEC fee and TAF rounded up to a cent (build 20)
    expected_sales_per_day: float = 4.0  # the daily rounding is spread over this many sales in estimates

    # ------------------------------------------------------------------ presets
    @classmethod
    def legacy_build20(cls) -> "CostModel":
        """The exact cost rule of the frozen build 20 study."""
        return cls(sec_rate=27.80 / 1_000_000, taf_per_share=0.000166, taf_max=8.30, cat_per_share=0.0,
                   slippage_bps=5.0, rounding="per_trade")

    @classmethod
    def on(cls, day: Optional[date], **kw) -> "CostModel":
        r = rates_on(day)
        return cls(sec_rate=r.sec_rate, taf_per_share=r.taf_per_share, taf_max=r.taf_max,
                   cat_per_share=r.cat_per_share, **kw)

    def with_slippage(self, bps: float) -> "CostModel":
        return replace(self, slippage_bps=bps)

    # ------------------------------------------------------------------ fees
    def sale_breakdown(self, notional: float, qty: float) -> FeeBreakdown:
        """Unrounded regulatory fees of one sale."""
        return FeeBreakdown(notional * self.sec_rate, min(qty * self.taf_per_share, self.taf_max),
                            qty * self.cat_per_share)

    def buy_breakdown(self, qty: float) -> FeeBreakdown:
        return FeeBreakdown(0.0, 0.0, qty * self.cat_per_share)

    def sell_fees(self, notional: float, qty: float) -> float:
        """Fee charged to one sale in per-trade accounting.

        per_trade: SEC fee and TAF each rounded up to a cent (build 20's rule).
        daily: the exact accrual (the once-a-day rounding is added by `daily_rounding`)."""
        b = self.sale_breakdown(notional, qty)
        if self.rounding == "per_trade":
            return _ceil_cent(b.sec) + _ceil_cent(b.taf) + b.cat
        return b.total

    def buy_fees(self, qty: float) -> float:
        return self.buy_breakdown(qty).total

    def daily_rounding(self, fees: Iterable[FeeBreakdown]) -> float:
        """Extra charge from rounding each fee type's DAILY total up to a cent (0 in per_trade mode)."""
        if self.rounding == "per_trade":
            return 0.0
        tot = FeeBreakdown()
        for f in fees:
            tot = tot + f
        return sum(_ceil_cent(x) - x for x in (tot.sec, tot.taf, tot.cat))

    def rounding_allowance(self) -> float:
        """Expected share of the daily rounding per sale, for estimates made before a trade."""
        if self.rounding == "per_trade":
            return 0.0
        kinds = sum(1 for r in (self.sec_rate, self.taf_per_share, self.cat_per_share) if r > 0)
        return 0.01 * kinds / max(1.0, self.expected_sales_per_day)

    def round_trip_fees(self, notional: float, price: float) -> float:
        qty = notional / price if price > 0 else 0.0
        return self.sell_fees(notional, qty) + self.buy_fees(qty) + self.rounding_allowance()

    # ------------------------------------------------------------------ prices
    def fill_price(self, side: str, ref_price: float, spread_bps: float = 0.0) -> float:
        """Pessimistic fill: pay half the spread plus slippage."""
        adj = (spread_bps / 2 + self.slippage_bps) / 10_000
        return ref_price * (1 + adj) if side == "buy" else ref_price * (1 - adj)

    def round_trip_cost_pct(self, notional: float, price: float, spread_bps: float = 2.0) -> float:
        fees = self.round_trip_fees(notional, price)
        return (spread_bps + 2 * self.slippage_bps) / 100 + (fees / notional * 100 if notional > 0 else 0)

    def breakdown_pct(self, notional: float, price: float, spread_bps: float) -> dict:
        """Where a round trip's cost comes from, in % of the position (for the UI and reports)."""
        qty = notional / price if price > 0 else 0.0
        s, b = self.sale_breakdown(notional, qty), self.buy_breakdown(qty)
        pct = (lambda x: x / notional * 100) if notional > 0 else (lambda x: 0.0)
        if self.rounding == "daily":
            rounding = self.rounding_allowance()
        else:
            rounding = _ceil_cent(s.sec) - s.sec + _ceil_cent(s.taf) - s.taf
        return {"spreadPct": spread_bps / 100, "slippagePct": 2 * self.slippage_bps / 100,
                "secPct": pct(s.sec), "tafPct": pct(s.taf), "catPct": pct(s.cat + b.cat), "roundingPct": pct(rounding),
                "totalPct": self.round_trip_cost_pct(notional, price, spread_bps)}

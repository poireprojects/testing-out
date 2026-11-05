"""Trading cost estimates.

Alpaca charges no commission on U.S. stocks, but regulatory fees are passed through on
*sales*: the SEC Section 31 fee (a rate per dollar sold, adjusted by the SEC each
fiscal year) and FINRA's Trading Activity Fee (per share sold, capped). Brokers round
these up to the cent, which matters a lot on $20 trades: roughly $0.01–$0.02 per sale.
The rates below are configurable estimates; actual fees are read from the broker when
available and the UI labels which is which.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SEC_FEE_RATE = 27.80 / 1_000_000   # dollars per dollar of sale proceeds (estimate)
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_MAX = 8.30


def _ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100 if x > 0 else 0.0


@dataclass(frozen=True)
class CostModel:
    sec_rate: float = SEC_FEE_RATE
    taf_per_share: float = FINRA_TAF_PER_SHARE
    taf_max: float = FINRA_TAF_MAX
    slippage_bps: float = 5.0  # per side, on top of half the quoted spread
    round_up_to_cent: bool = True

    def sell_fees(self, notional: float, qty: float) -> float:
        sec = notional * self.sec_rate
        taf = min(qty * self.taf_per_share, self.taf_max)
        if self.round_up_to_cent:
            return _ceil_cent(sec) + _ceil_cent(taf)
        return sec + taf

    def fill_price(self, side: str, ref_price: float, spread_bps: float = 0.0) -> float:
        """Pessimistic fill: pay half the spread plus slippage."""
        adj = (spread_bps / 2 + self.slippage_bps) / 10_000
        return ref_price * (1 + adj) if side == "buy" else ref_price * (1 - adj)

    def round_trip_cost_pct(self, notional: float, price: float, spread_bps: float = 2.0) -> float:
        qty = notional / price if price > 0 else 0
        fees = self.sell_fees(notional, qty)
        return (spread_bps + 2 * self.slippage_bps) / 100 + (fees / notional * 100 if notional > 0 else 0)

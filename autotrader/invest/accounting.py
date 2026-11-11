"""Exact paper accounting for both portfolios (Decimal, whole cents, 9-decimal shares). Research only.

One set of functions serves both portfolios, so a 100%-VOO AI portfolio is the benchmark to the cent before model
costs. At a session open, in this order (registration FROZEN["execution"]):
1. corporate actions: splits multiply shares; ex-dividend entitlements become receivables; receivables whose pay
   date has come become cash;
2. stock sales, then stock purchases (only on the AI portfolio's fill session, from a valid, on-time decision);
3. VOO: sold just enough to fund the purchases and fees, or bought with every remaining dollar (the sweep), when at
   least $1.00 would be invested;
4. fees: each type summed for the session and rounded up to a cent once.
Fills are the session's opening price adjusted by the frozen slippage (5 bps VOO, 10 bps stocks). Nothing is ever
filled without an opening price: the caller checks the prices first and records a data gap instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional

from stockpilot.risk.fees import rates_on

from .registration import BENCHMARK, MIN_ORDER, MIN_SWEEP, dec, slippage_bps

CENT = Decimal("0.01")
QTY = Decimal("0.000000001")
MICRO = Decimal("0.000001")
ZERO = Decimal("0")


class AccountingError(RuntimeError):
    pass


def floor_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_FLOOR)


def ceil_cent(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_CEILING)


def floor_qty(x: Decimal) -> Decimal:
    return x.quantize(QTY, rounding=ROUND_FLOOR)


def ceil_qty(x: Decimal) -> Decimal:
    return x.quantize(QTY, rounding=ROUND_CEILING)


@dataclass
class Book:
    name: str
    cash: Decimal
    qty: dict[str, Decimal] = field(default_factory=dict)
    receivables: list[dict] = field(default_factory=list)   # {symbol, ex_date, pay_date, amount}

    def held(self) -> list[str]:
        return sorted(s for s, q in self.qty.items() if q > 0)

    def stocks(self) -> list[str]:
        return [s for s in self.held() if s != BENCHMARK]

    def receivable(self) -> Decimal:
        return sum((dec(r["amount"]) for r in self.receivables), ZERO)

    def investable(self, prices: dict[str, Decimal]) -> Decimal:
        """Cash plus positions at `prices` (what target weights are shares of)."""
        return self.cash + sum((self.qty[s] * prices[s] for s in self.held()), ZERO)

    def wealth(self, prices: dict[str, Decimal]) -> Decimal:
        """Investable value plus declared, unpaid dividends."""
        return self.investable(prices) + self.receivable()

    def snapshot(self) -> dict:
        return {"cash": str(self.cash), "qty": {s: str(self.qty[s]) for s in self.held()},
                "receivables": [dict(r, amount=str(r["amount"])) for r in self.receivables]}


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str            # buy | sell
    qty: Decimal
    ref: Decimal         # the opening price
    fill: Decimal        # ref x (1 +/- slippage)
    amount: Decimal      # buy: cash paid; sell: proceeds
    reason: str

    @property
    def slippage_usd(self) -> Decimal:
        """The modeled spread and slippage cost against the opening price (including rounding)."""
        at_ref = self.qty * self.ref
        return (self.amount - at_ref if self.side == "buy" else at_ref - self.amount).quantize(MICRO)

    def record(self) -> dict:
        return {"symbol": self.symbol, "side": self.side, "qty": str(self.qty), "ref_open": str(self.ref),
                "fill": str(self.fill), "amount": str(self.amount), "slippage_usd": str(self.slippage_usd),
                "reason": self.reason}


def fill_price(symbol: str, side: str, ref: Decimal) -> Decimal:
    s = slippage_bps(symbol) / Decimal(10000)
    return ref * (1 + s) if side == "buy" else ref * (1 - s)


def buy(symbol: str, amount: Decimal, ref: Decimal, reason: str) -> Order:
    amount = floor_cent(amount)
    f = fill_price(symbol, "buy", ref)
    return Order(symbol, "buy", floor_qty(amount / f), ref, f, amount, reason)


def sell(symbol: str, qty: Decimal, ref: Decimal, reason: str) -> Order:
    f = fill_price(symbol, "sell", ref)
    return Order(symbol, "sell", qty, ref, f, floor_cent(qty * f), reason)


def _rate(x: float) -> Decimal:
    return dec(round(x, 12))          # the app stores rates as floats (20.60 / 1e6): drop the binary noise


def session_fees(day: date, orders: list[Order]) -> dict:
    r = rates_on(day)
    sec_rate, taf, taf_max, cat_rate = _rate(r.sec_rate), _rate(r.taf_per_share), _rate(r.taf_max), _rate(r.cat_per_share)
    sells = [o for o in orders if o.side == "sell"]
    sec = ceil_cent(sum((o.amount for o in sells), ZERO) * sec_rate)
    taf_fee = ceil_cent(sum((min(o.qty * taf, taf_max) for o in sells), ZERO))
    cat = ceil_cent(sum((o.qty for o in orders), ZERO) * cat_rate)
    return {"sec": sec, "taf": taf_fee, "cat": cat, "total": sec + taf_fee + cat,
            "rates": {"effective": r.effective.isoformat(), "sec_rate": str(sec_rate), "taf_per_share": str(taf),
                      "taf_max": str(taf_max), "cat_per_share": str(cat_rate)}}


# ------------------------------------------------------------------ corporate actions
def apply_actions(book: Book, after: Optional[date], day: date, splits: list[dict], dividends: list[dict]) -> list[dict]:
    """Actions with an ex-date in (after, day], and payments due by `day`. Mutates `book`."""
    events = []

    def due(ex: str) -> bool:
        x = date.fromisoformat(ex)
        return x <= day and (after is None or x > after)

    for sp in sorted(splits, key=lambda s: (s["ex_date"], s["symbol"])):
        q = book.qty.get(sp["symbol"], ZERO)
        if due(sp["ex_date"]) and q > 0:
            new = floor_qty(q * dec(sp["num"]) / dec(sp["den"]))
            book.qty[sp["symbol"]] = new
            events.append({"kind": "split", "symbol": sp["symbol"], "ex_date": sp["ex_date"],
                           "ratio": f"{sp['num']}:{sp['den']}", "qty_before": str(q), "qty_after": str(new)})
    for dv in sorted(dividends, key=lambda s: (s["ex_date"], s["symbol"])):
        q = book.qty.get(dv["symbol"], ZERO)
        if dv["pay_date"] < dv["ex_date"]:
            raise AccountingError(f"{dv['symbol']} dividend pays before its ex-date")
        if due(dv["ex_date"]) and q > 0:
            amt = floor_cent(q * dec(dv["amount"]))
            if amt > 0:
                book.receivables.append({"symbol": dv["symbol"], "ex_date": dv["ex_date"], "pay_date": dv["pay_date"],
                                         "amount": amt})
            events.append({"kind": "dividend_entitlement", "symbol": dv["symbol"], "ex_date": dv["ex_date"],
                           "pay_date": dv["pay_date"], "per_share": str(dec(dv["amount"])), "qty": str(q),
                           "amount": str(amt)})
    for r in list(book.receivables):
        if date.fromisoformat(r["pay_date"]) <= day:
            book.cash += dec(r["amount"])
            book.receivables.remove(r)
            events.append({"kind": "dividend_paid", "symbol": r["symbol"], "ex_date": r["ex_date"],
                           "pay_date": r["pay_date"], "amount": str(r["amount"])})
    return events


# ------------------------------------------------------------------ trading
def stock_orders(book: Book, opens: dict[str, Decimal], targets: dict[str, dict]) -> list[Order]:
    """Orders for a validated decision's stock targets ({symbol: {action, target_pct}}), sales first."""
    V = book.investable(opens)
    sells, buys = [], []
    for sym in sorted(targets):
        t = targets[sym]
        q, p = book.qty.get(sym, ZERO), opens[sym]
        if t["action"] == "sell":
            if q > 0:
                sells.append(sell(sym, q, p, "sell"))
            continue
        if t["action"] == "hold":
            continue
        tv = floor_cent(V * dec(t["target_pct"]) / 100)
        if t["action"] in ("buy", "increase"):
            amt = floor_cent(tv - q * p)
            if amt >= MIN_ORDER:
                buys.append(buy(sym, amt, p, t["action"]))
        elif t["action"] == "reduce":
            sq = q - floor_qty(tv / p)
            if sq > 0 and sq * p >= MIN_ORDER:
                sells.append(sell(sym, sq, p, "reduce"))
    return sells + buys


def settle(book: Book, day: date, opens: dict[str, Decimal], orders: list[Order]) -> dict:
    """Apply `orders`, fund or sweep with VOO, charge fees. Mutates `book`; returns the session's trading record."""
    sells = [o for o in orders if o.side == "sell"]
    buys = [o for o in orders if o.side == "buy"]
    base = book.cash + sum((o.amount for o in sells), ZERO) - sum((o.amount for o in buys), ZERO)
    f0 = session_fees(day, orders)["total"]
    voo: Optional[Order] = None
    if base - f0 >= MIN_SWEEP:
        x = base - f0
        for _ in range(20):
            o = buy(BENCHMARK, x, opens[BENCHMARK], "sweep")
            f = session_fees(day, orders + [o])["total"]
            if o.amount + f <= base:
                voo = o if o.amount >= MIN_SWEEP and o.qty > 0 else None
                break
            x = base - f
        else:
            raise AccountingError("the VOO sweep did not converge")
    elif base - f0 < 0:
        held, y = book.qty.get(BENCHMARK, ZERO), f0 - base
        for _ in range(20):
            q = min(held, ceil_qty(y / fill_price(BENCHMARK, "sell", opens[BENCHMARK])))
            o = sell(BENCHMARK, q, opens[BENCHMARK], "fund")
            short = session_fees(day, orders + [o])["total"] - (base + o.amount)
            if short <= 0:
                voo = o
                break
            if q == held:
                raise AccountingError(f"{book.name} {day}: not enough VOO to fund the purchases and fees")
            y += short
        else:
            raise AccountingError("the VOO funding sale did not converge")
    every = orders + ([voo] if voo else [])
    fees = session_fees(day, every)
    for o in every:
        q = book.qty.get(o.symbol, ZERO)
        q = q + o.qty if o.side == "buy" else q - o.qty
        if q < 0:
            raise AccountingError(f"{o.symbol}: a sale larger than the position")
        if q == 0:
            book.qty.pop(o.symbol, None)
        else:
            book.qty[o.symbol] = q
    book.cash = book.cash + sum((o.amount for o in every if o.side == "sell"), ZERO) \
        - sum((o.amount for o in every if o.side == "buy"), ZERO) - fees["total"]
    if book.cash < 0:
        raise AccountingError(f"{book.name} {day}: cash would be negative")
    return {"orders": [o.record() for o in every],
            "fees": {k: str(v) for k, v in fees.items() if k != "rates"} | {"rates": fees["rates"]},
            "slippage_usd": str(sum((o.slippage_usd for o in every), ZERO)),
            "purchases": str(sum((o.amount for o in every if o.side == "buy"), ZERO)),
            "sales": str(sum((o.amount for o in every if o.side == "sell"), ZERO))}

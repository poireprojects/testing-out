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

Sizing quantities from the opening price and assuming a fill at that same opening price is a paper-model
convention. It does not show that fractional orders could have been executed in the opening auction at that price.

Limits versus drift. The 10% cap applies when a target is set. Targets are shares of the portfolio's value at the
fill session's opening prices, before trades; fees and slippage then shrink the portfolio slightly, so a resulting
weight can sit a few hundredths of a point above its target. A position more than `CAP_TOLERANCE_PP` above the cap
at the opening prices must come down: a reduce of such a position is risk-required and sells regardless of the $1
discretionary minimum. A hold never trades (weights drift with prices between decisions), and a missed or rejected
decision never trades. Every planned stock trade records its cutoff, opening and resulting weight and why any order
was or was not placed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional

from stockpilot.risk.fees import rates_on

from .registration import BENCHMARK, FROZEN, MIN_ORDER, MIN_SWEEP, dec, slippage_bps

CENT = Decimal("0.01")
CAP_TOLERANCE_PP = dec(FROZEN["execution"]["cap_tolerance_pp"])
PCT = Decimal("0.0001")
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
def weights(book: Book, prices: dict[str, Decimal]) -> dict[str, Decimal]:
    """Each position's share of cash plus positions at `prices`, in percent."""
    v = book.investable(prices)
    return {s: (book.qty[s] * prices[s] / v * 100).quantize(PCT) for s in book.held()} if v > 0 else {}


def stock_orders(book: Book, opens: dict[str, Decimal], targets: dict[str, dict],
                 cutoff_weights: Optional[dict[str, str]] = None) -> tuple[list[Order], list[dict]]:
    """Orders for a validated decision's stock targets ({symbol: {action, target_pct}}), sales first, and the plan:
    one entry per target with its cutoff and opening weights, the order placed (if any) and why."""
    V = book.investable(opens)
    cap = dec(FROZEN["limits"]["stock_max_pct"])
    w_open = weights(book, opens)
    sells, buys, plan = [], [], []
    for sym in sorted(targets):
        t = targets[sym]
        q, p = book.qty.get(sym, ZERO), opens[sym]
        e = {"symbol": sym, "action": t["action"], "target_pct": t["target_pct"],
             "cutoff_weight_pct": (cutoff_weights or {}).get(sym), "opening_weight_pct": str(w_open.get(sym, ZERO)),
             "order": None, "risk_required": False, "note": ""}
        plan.append(e)
        if t["action"] == "sell":
            if q > 0:
                sells.append(o := sell(sym, q, p, "sell"))
                e |= {"order": o.record(), "note": "full sale (no minimum applies)"}
            continue
        if t["action"] == "hold":
            e["note"] = "hold: shares unchanged, no trade"
            if w_open.get(sym, ZERO) > cap + CAP_TOLERANCE_PP:
                e["note"] += "; above the cap at the open through drift after the cutoff (a hold never trades)"
            continue
        tv = floor_cent(V * dec(t["target_pct"]) / 100)
        if t["action"] in ("buy", "increase"):
            amt = floor_cent(tv - q * p)
            if amt >= MIN_ORDER:
                buys.append(o := buy(sym, amt, p, t["action"]))
                e |= {"order": o.record(), "note": f"buy {amt} USD to reach the target at the opening price"}
            elif amt > 0:
                e["note"] = f"skipped: {amt} USD is below the {MIN_ORDER} USD discretionary minimum"
            else:
                e["note"] = "no purchase: the opening value is already at or above the target"
        elif t["action"] == "reduce":
            sq = q - floor_qty(tv / p)
            risky = w_open.get(sym, ZERO) > cap + CAP_TOLERANCE_PP
            e["risk_required"] = risky
            if sq <= 0:
                e["note"] = "no sale: the opening weight is already at or below the target"
            elif sq * p >= MIN_ORDER or risky:
                sells.append(o := sell(sym, sq, p, "reduce"))
                e |= {"order": o.record(), "note": "risk-required reduction: above the cap at the open, sold "
                      "regardless of the discretionary minimum" if risky and sq * p < MIN_ORDER
                      else "reduce to the target at the opening price"}
            else:
                e["note"] = f"skipped: a {(sq * p).quantize(CENT)} USD sale is below the {MIN_ORDER} USD " \
                            "discretionary minimum (not above the cap)"
    return sells + buys, plan


def resulting(plan: list[dict], book: Book, opens: dict[str, Decimal]) -> dict:
    """Fill in each plan entry's resulting weight (after trades and fees, at the opening prices) and its deviation
    from the target; any risk-required reduction left more than the tolerance above the cap is flagged."""
    w = weights(book, opens)
    cap = dec(FROZEN["limits"]["stock_max_pct"])
    for e in plan:
        r = w.get(e["symbol"], ZERO)
        e["resulting_weight_pct"] = str(r)
        if e["target_pct"] is not None:
            e["deviation_pp"] = str((r - dec(e["target_pct"])).quantize(PCT))
        e["within_cap_tolerance"] = e["action"] == "hold" or r <= cap + CAP_TOLERANCE_PP
        if not e["within_cap_tolerance"]:
            e["note"] += f"; RESIDUAL: {r}% remains above the cap"
    return {"stocks_pct": str(sum((v for s, v in w.items() if s != BENCHMARK), ZERO)),
            "voo_pct": str(w.get(BENCHMARK, ZERO)), "cash_usd": str(book.cash)}


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

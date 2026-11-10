"""Execution version 2 for the development evaluation (proposed amendment 1). Research only.

What the cache can and cannot support:
* **Quotes are medians, not quotes.** scripts/phase4_collect.py stores, for 10:00:30-10:01:30 and 15:45:00-15:46:00,
  the median mid and the median half-spread of the NBBO updates in that minute. The earlier replay
  (ReplayPrices.quote) relabelled them as quotes *at* 10:00:30 and 15:45. Here they are `ProxyQuote`s, labelled
  with their window: an entry fill, a spread condition and a move condition evaluated on them are a proxy
  (diagnostic), never an executable price at the start of the minute. `EXECUTABLE_VERIFIED` is False for this
  cache, and every report says so.
* **The decision clock.** Every arm decides from the same 10:00:00 inputs. The language model's four requests on a
  date are sent one after another in the frozen order, so an event's decision is ready at 10:00:00 plus the
  latencies of its own request and every request before it. In the executable comparison a decision that is not
  ready by 10:00:30 gets no fill at 10:00:30 (`missed_deadline`). The idealized fixed-time result (every decision
  treated as ready) is computed separately and labelled. The rules are ready at 10:00:00.
* **Exits.** Stop and target on 15-minute bars that start at or after 10:00, stop first when a bar reaches both,
  and the 10:00 bar (which straddles the 10:00:30 entry) can stop the trade but never credit a target. A stop
  fills at the lower of the stop and the bar's open. A 15:45 time exit uses the 15:45:00-15:46:00 median (proxy).
  An earlier time exit (11:00 to 15:00, allowed by contract v2) has no quote in the cache: it uses the open of the
  bar starting at that time with the *entry* half-spread, flagged as a proxy (this is what the earlier simulator
  did silently). If any bar between entry and exit is missing, the stop and target cannot be evaluated and the
  trade is unscorable (never filled in).
* **Costs.** Half the (proxy) spread plus 5 bps slippage per side; fees from the historical research schedule
  (autotrader/fees_hist.py), summed per decider per day per fee type and rounded up once. The 2x sensitivity
  doubles spreads and slippage and reruns fill eligibility, sizing and outcomes from the same frozen decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from stockpilot.clock import ET

from . import fees_hist
from .evidence import EvidencePack
from .risk import Health, Quote, RiskController, SimPortfolio, SimPosition

EXECUTABLE_VERIFIED = False
ASOF = dtime(10, 0)
ENTRY = dtime(10, 0, 30)
DEADLINE = dtime(10, 0, 30)
EXIT_QUOTE = dtime(15, 45)
SLIP_BPS = 5.0
PROXY = "median of NBBO updates in the following 60 seconds (proxy, not executable at the window start)"


@dataclass(frozen=True)
class ProxyQuote:
    symbol: str
    mid: float
    half_spread_bps: float
    n_updates: int
    window_start: datetime
    window_end: datetime
    convention: str = PROXY

    def as_quote(self, prev_close: Optional[float], spread_scale: float = 1.0) -> Quote:
        hs = self.half_spread_bps * spread_scale / 1e4
        return Quote(self.symbol, self.mid * (1 - hs), self.mid * (1 + hs), self.window_start, prev_close)


class ProxyQuotes:
    """Read-only access to the cache's minute medians, with their real windows."""

    def __init__(self, prices):
        self.p = prices                     # autotrader.sources.ReplayPrices (or a synthetic equivalent)

    def _get(self, sym: str, day: date, win: str, start: dtime) -> Optional[ProxyQuote]:
        r = self.p.q.get(f"{day.isoformat()}|{win}", {}).get(sym)
        if not r or not r.get("mid") or r.get("halfSpreadBps") is None or (r.get("n") or 0) < 1:
            return None
        t0 = datetime.combine(day, start, ET)
        return ProxyQuote(sym, float(r["mid"]), float(r["halfSpreadBps"]), int(r.get("n") or 0), t0,
                          t0 + timedelta(seconds=60))

    def entry(self, sym: str, day: date) -> Optional[ProxyQuote]:
        return self._get(sym, day, "entry", ENTRY)

    def exit(self, sym: str, day: date) -> Optional[ProxyQuote]:
        return self._get(sym, day, "exit", EXIT_QUOTE)


@dataclass
class Clock:
    """When a decision was ready, relative to the common 10:00:00 cutoff."""
    queue_position: Optional[int]
    own_latency_ms: Optional[int]
    ready_offset_ms: Optional[int]          # None: no decision (no answer): it cannot be filled anyway

    def ready_at(self, day: date) -> Optional[datetime]:
        if self.ready_offset_ms is None:
            return None
        return datetime.combine(day, ASOF, ET) + timedelta(milliseconds=self.ready_offset_ms)


def model_clocks(latencies_ms: list[Optional[int]]) -> list[Clock]:
    """Serial requests in queue order from the common cutoff. A request that failed still took its time."""
    out, acc = [], 0
    for i, ms in enumerate(latencies_ms):
        if ms is None:
            out.append(Clock(i + 1, None, None))
            continue
        acc += int(ms)
        out.append(Clock(i + 1, int(ms), acc))
    return out


RULE_CLOCK = Clock(None, 0, 0)


@dataclass
class Variant:
    name: str
    enforce_deadline: bool
    spread_scale: float = 1.0
    slip_scale: float = 1.0


VARIANTS = (Variant("executable", True), Variant("idealized_fixed_time", False),
            Variant("executable_2x_costs", True, 2.0, 2.0))


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _bars_between(bars, day: date, first: dtime, last_excl: dtime):
    """The 15-minute bars from `first` up to (not including) `last_excl`, and the starts that are missing."""
    starts, t = [], datetime.combine(day, first, ET)
    end = datetime.combine(day, last_excl, ET)
    while t < end:
        starts.append(t)
        t += timedelta(minutes=15)
    have = set(bars.index) if bars is not None and not bars.empty else set()
    missing = [s for s in starts if s not in have]
    rows = [(s, bars.loc[s]) for s in starts if s in have]
    return rows, missing


def simulate_exit(day: date, entry_px_ref: float, stop: float, target: Optional[float], exit_hhmm: str, bars,
                  exit_quote: Optional[ProxyQuote], entry_hs_bps: float) -> dict:
    exit_t = _hhmm(exit_hhmm)
    rows, missing = _bars_between(bars, day, ASOF, exit_t)
    if missing:
        return {"scored": False, "reason": "unscorable", "flags": [
            f"incomplete intraday bars: {len(missing)} missing between 10:00 and {exit_hhmm} "
            f"(first {missing[0].strftime('%H:%M')}): stop and target cannot be evaluated"]}
    flags: list[str] = []
    entry_t = datetime.combine(day, ENTRY, ET)
    for t, r in rows:
        straddles = t < entry_t
        if r["low"] <= stop:
            if straddles:
                flags.append("stop reached in the 10:00 bar that straddles the entry: counted as stopped at the stop")
                return {"scored": True, "reason": "stop", "exit_ref": stop, "exit_at": entry_t, "hs_bps": entry_hs_bps,
                        "flags": flags + ["proxy: stop fill uses the entry half-spread"]}
            return {"scored": True, "reason": "stop", "exit_ref": min(stop, float(r["open"])), "exit_at": t,
                    "hs_bps": entry_hs_bps, "flags": flags + ["proxy: stop fill uses the entry half-spread"]}
        if target and not straddles and r["high"] >= target:
            return {"scored": True, "reason": "target", "exit_ref": max(target, float(r["open"])), "exit_at": t,
                    "hs_bps": entry_hs_bps, "flags": flags + ["proxy: target fill uses the entry half-spread"]}
    if exit_t == EXIT_QUOTE:
        if exit_quote is None:
            return {"scored": False, "reason": "unscorable", "flags": flags + ["no 15:45 exit quote window"]}
        return {"scored": True, "reason": "time", "exit_ref": exit_quote.mid, "exit_at": exit_quote.window_start,
                "hs_bps": exit_quote.half_spread_bps, "flags": flags + ["proxy: 15:45:00-15:46:00 median exit quote"]}
    at = datetime.combine(day, exit_t, ET)
    if bars is None or bars.empty or at not in set(bars.index):
        return {"scored": False, "reason": "unscorable", "flags": flags + [f"no bar starting at {exit_hhmm}"]}
    return {"scored": True, "reason": "time", "exit_ref": float(bars.loc[at]["open"]), "exit_at": at,
            "hs_bps": entry_hs_bps,
            "flags": flags + [f"proxy: {exit_hhmm} exit at that bar's open with the entry half-spread (no quote)"]}


@dataclass
class Disposition:
    decision_seq: int
    decider: str
    event_id: str
    action: str
    status: str                          # no_order | rejected | missed_deadline | filled
    reasons: list[str] = field(default_factory=list)
    clock: Optional[dict] = None
    outcome: Optional[dict] = None


class DevExecutor:
    """Executes recorded decisions for one variant. One simulated $100 account per arm, carried across dates."""

    def __init__(self, controller: RiskController, prices, quotes: ProxyQuotes, variant: Variant):
        self.controller, self.prices, self.quotes, self.v = controller, prices, quotes, variant
        self.portfolios: dict[str, SimPortfolio] = {}

    def _quote(self, sym: str, day: date) -> tuple[Optional[Quote], Optional[ProxyQuote]]:
        pq = self.quotes.entry(sym, day)
        if pq is None:
            return None, None
        return pq.as_quote(self.prices.prev_close(sym, day), self.v.spread_scale), pq

    def execute_day(self, day: date, entries: list[tuple[dict, EvidencePack, Clock]]) -> list[Disposition]:
        """`entries` in the frozen order (the order of contested slots in every arm)."""
        entry_t = datetime.combine(day, ENTRY, ET)
        deadline = datetime.combine(day, DEADLINE, ET)
        out: list[Disposition] = []
        opened: dict[str, list[dict]] = {}
        for rec, pack, clock in entries:
            p = rec["payload"]
            d, name = p["decision"], p["decider"]
            pf = self.portfolios.setdefault(name, SimPortfolio())
            pf.roll(day)
            disp = Disposition(rec["seq"], name, pack.event_id, d.get("action", "no_trade"), "no_order",
                               clock=None if clock is None else {
                                   "input_cutoff": datetime.combine(day, ASOF, ET).isoformat(),
                                   "queue_position": clock.queue_position, "own_latency_ms": clock.own_latency_ms,
                                   "ready_at": None if clock.ready_at(day) is None else clock.ready_at(day).isoformat(),
                                   "eligible_fill_time": entry_t.isoformat()})
            out.append(disp)
            if d.get("action") != "long":
                continue
            ready = clock.ready_at(day) if clock is not None else None
            if self.v.enforce_deadline and (ready is None or ready > deadline):
                disp.status, disp.reasons = "missed_deadline", [
                    f"decision ready at {ready.time() if ready else 'never'}, after the 10:00:30 deadline: no fill"]
                continue
            sym = str(d.get("instrument") or "").upper()
            q, pq = self._quote(sym, day)
            cm = lambda dd: fees_hist.cost_model(dd, SLIP_BPS * self.v.slip_scale)  # noqa: E731
            self.controller.cost_model_fn = cm
            v = self.controller.check(d, pack, q, pf, Health(), entry_t)
            if not v["approved"]:
                disp.status, disp.reasons = "rejected", list(v.get("reasons") or [])
                continue
            hs = pq.half_spread_bps * self.v.spread_scale
            slip = SLIP_BPS * self.v.slip_scale
            px = pq.mid * (1 + (hs + slip) / 1e4)
            qty = v["notional"] / px
            pf.open.append(SimPosition(sym, qty, px, v["stop_price"], entry_t))
            pf.trades_today += 1
            disp.status = "filled"
            opened.setdefault(name, []).append({"disp": disp, "symbol": sym, "qty": qty, "entry_px": px,
                                                "entry_mid": pq.mid, "hs_in": hs, "slip": slip,
                                                "notional": v["notional"], "stop": v["stop_price"],
                                                "target": v.get("target_price"), "exit_time": d["exit"]["exit_time"],
                                                "entry_window": [pq.window_start.isoformat(), pq.window_end.isoformat()]})
        for name, ops in opened.items():
            pf = self.portfolios[name]
            scored = []
            for o in ops:
                res = simulate_exit(day, o["entry_mid"], o["stop"], o["target"], o["exit_time"],
                                    self.prices.bars15(o["symbol"], day), self.quotes.exit(o["symbol"], day),
                                    o["hs_in"] / self.v.spread_scale)
                if not res["scored"]:
                    pf.open = [p for p in pf.open if p.symbol != o["symbol"]]
                    o["disp"].outcome = {"scored": False, "flags": res["flags"]}
                    continue
                hs_out = res["hs_bps"] * self.v.spread_scale
                exit_px = res["exit_ref"] * (1 - (hs_out + o["slip"]) / 1e4)
                o |= {"res": res, "hs_out": hs_out, "exit_px": exit_px,
                      "fee_parts": fees_hist.fee_parts(day, o["qty"], o["qty"] * exit_px)}
                scored.append(o)
            fees, rounded = fees_hist.daily_fees([o["fee_parts"] for o in scored]) if scored else ([], {})
            for o, fee in zip(scored, fees):
                gross = o["notional"] * (o["res"]["exit_ref"] / o["entry_mid"] - 1)
                before_fees = o["qty"] * (o["exit_px"] - o["entry_px"])
                friction = gross - before_fees
                bps_total = o["hs_in"] + o["hs_out"] + 2 * o["slip"]
                spread_cost = friction * (o["hs_in"] + o["hs_out"]) / bps_total if bps_total else 0.0
                pnl = before_fees - fee
                pf.close(o["symbol"], pnl, o["res"]["exit_at"])
                o["disp"].outcome = {
                    "scored": True, "symbol": o["symbol"], "reason": o["res"]["reason"],
                    "exit_at": o["res"]["exit_at"].isoformat(), "entry_window": o["entry_window"],
                    "notional": o["notional"], "entry_mid": o["entry_mid"], "entry_px": o["entry_px"],
                    "exit_ref": o["res"]["exit_ref"], "exit_px": o["exit_px"],
                    "gross_usd": gross, "spread_usd": spread_cost, "slippage_usd": friction - spread_cost,
                    "fees_usd": fee, "fee_parts": o["fee_parts"], "pnl_usd": pnl,
                    "net_pct": pnl / o["notional"] * 100, "flags": o["res"]["flags"],
                    "reconciles": abs(gross - spread_cost - (friction - spread_cost) - fee - pnl) < 1e-9}
        return out

    def equity(self) -> dict[str, float]:
        return {k: p.equity for k, p in self.portfolios.items()}

"""Bar-by-bar intraday backtester.

Realism rules (to avoid flattering results):
* No look-ahead: a signal on bar i (using only bars <= i) is filled at the OPEN of bar i+1.
* Every buy pays half the spread plus slippage; every sale pays half the spread, slippage
  and regulatory fees (rounded up to the cent, like the broker).
* If a bar touches both the stop and the target, the stop is assumed to hit first.
* A target (limit) only counts as filled when the price trades THROUGH it by `limit_through_bps`
  (touching a limit price doesn't guarantee a fill).
* The ranker decides with the signal bar's close — never the next bar's open it is filled at.
* Fees follow the broker's dated schedule; the once-a-day rounding is charged per day (`with_daily_rounding`).
* If price gaps through the stop, the fill is at the (worse) open, not the stop.
* Positions are force-closed before the close (same rule as live trading).
* Strategies use fixed parameters — nothing is tuned on the test period.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..analysis.features import Baseline, feature_frame
from ..risk.fees import CostModel, FeeBreakdown
from ..strategies import ALL_STRATEGIES, Context


class _Col:
    __slots__ = ("a",)

    def __init__(self, a: np.ndarray):
        self.a = a

    @property
    def iat(self):
        return self.a

    @property
    def iloc(self):
        return self.a


class FrameView:
    """numpy-backed stand-in for a feature DataFrame (same `f[col].iat[i]` / `.iloc[a:b]` API, ~20× faster)."""

    def __init__(self, df: pd.DataFrame):
        self.index = df.index
        self._n = len(df)
        self.cols = {c: _Col(df[c].to_numpy(dtype=float)) for c in df.columns}

    def __getitem__(self, c: str) -> _Col:
        return self.cols[c]

    def __len__(self) -> int:
        return self._n

    @property
    def empty(self) -> bool:
        return self._n == 0


@dataclass
class BTTrade:
    strategy: str
    symbol: str
    day: date
    entry_i: int
    exit_i: int
    entry_px: float
    exit_px: float
    notional: float
    pnl: float
    ret_pct: float
    reason: str
    random_baseline: bool = False
    ref_entry: float = 0.0          # prices before costs, so results can be re-costed (stress tests)
    ref_exit: float = 0.0
    minute: int = 0                 # entry minute of the session (for portfolio replay)
    exit_minute: int = 0
    score: float = 0.0
    stop_pct: float = 0.0
    target_pct: float = 0.0
    agreeing: int = 1
    widened: bool = False
    signal_px: float = 0.0          # close of the signal bar (what was known when deciding)
    fee_sec: float = 0.0            # unrounded fee accruals (for the broker's daily rounding)
    fee_taf: float = 0.0
    fee_cat: float = 0.0

    @property
    def gross_ret_pct(self) -> float:
        return (self.ref_exit / self.ref_entry - 1) * 100 if self.ref_entry else 0.0


@dataclass
class BTConfig:
    notional: float = 20.0
    spread_bps: float = 3.0
    flatten_minutes_before_close: int = 10
    last_entry_minutes_before_close: int = 30
    no_entries_first_minutes: int = 5
    min_stop_pct: float = 0.4
    max_stop_pct: float = 3.0
    min_signal_score: float = 0.55
    costs: CostModel = field(default_factory=CostModel)
    random_baseline_draws: int = 3
    seed: int = 11
    max_positions: int = 2
    cooldown_minutes: int = 20
    cost_aware_brackets: bool = True   # same rule the live ranker uses
    dated_fees: bool = True            # use the fee schedule in force on each day
    limit_through_bps: float = 1.0     # a target fills only if the high exceeds it by this much
    ranker_price: str = "signal"       # "signal": decide on the signal close; "entry_open": build 20's rule

    @classmethod
    def baseline_build20(cls) -> "BTConfig":
        """Exactly the frozen build 20 study (per-sale fee rounding, touch = fill, ranker saw the fill bar's open)."""
        return cls(costs=CostModel.legacy_build20(), dated_fees=False, limit_through_bps=0.0,
                   ranker_price="entry_open")

    def costs_on(self, day: date) -> CostModel:
        if not self.dated_fees:
            return self.costs
        return CostModel.on(day, slippage_bps=self.costs.slippage_bps, rounding=self.costs.rounding,
                            expected_sales_per_day=self.costs.expected_sales_per_day)


@dataclass
class DayInput:
    day: date
    session_open: pd.Timestamp
    session_minutes: int
    bars: dict[str, pd.DataFrame]          # symbol -> 1-min bars for the day
    baselines: dict[str, Baseline]         # computed from PRIOR days only
    spy_trend: Optional[np.ndarray] = None  # per-minute "SPY above VWAP" (known at each bar close)


def _simulate_exit(fv: FrameView, strat, start_i: int, entry_px: float, stop: float, target: float,
                   max_hold: int, flatten_i: int, cfg: BTConfig, use_strategy_exit: bool = True) -> tuple[int, float, str]:
    o, h, l = fv["open"].a, fv["high"].a, fv["low"].a
    n = len(fv)
    entry_i = start_i
    stop_now = stop
    risk_r = max(entry_px - stop, entry_px * 0.002)
    hi = entry_px
    pending_exit = None
    for j in range(start_i, n):
        if pending_exit is not None:  # strategy exit decided at previous close -> next open
            return j, o[j], pending_exit
        if j >= flatten_i:
            return j, o[j], "end of day"
        if j - entry_i >= max_hold:
            return j, o[j], "time"
        if o[j] <= stop_now:
            return j, o[j], "stop (gap)"
        if l[j] <= stop_now:
            return j, stop_now, "stop"
        if h[j] >= target * (1 + cfg.limit_through_bps / 10_000):
            return j, target, "target"
        hi = max(hi, h[j])
        if hi >= entry_px + risk_r:
            stop_now = max(stop_now, entry_px * 1.0005)
        if hi >= entry_px + 1.5 * risk_r:
            stop_now = max(stop_now, hi - risk_r)
        if use_strategy_exit and j > entry_i:
            why = strat.exit(fv, j, entry_px, entry_i)
            if why:
                pending_exit = "strategy: " + why
    j = n - 1
    return j, fv["close"].a[j], "data end"


def _fill_trade(strategy: str, symbol: str, day: date, entry_i: int, exit_i: int, ref_entry: float, ref_exit: float,
                reason: str, cfg: BTConfig, rb: bool = False, costs: Optional[CostModel] = None) -> BTTrade:
    c = costs or cfg.costs_on(day)
    buy = c.fill_price("buy", ref_entry, cfg.spread_bps)
    sell = c.fill_price("sell", ref_exit, cfg.spread_bps)
    qty = cfg.notional / buy
    fees = c.sell_fees(qty * sell, qty) + c.buy_fees(qty)
    acc = c.sale_breakdown(qty * sell, qty) + c.buy_breakdown(qty)
    pnl = qty * (sell - buy) - fees
    return BTTrade(strategy, symbol, day, entry_i, exit_i, buy, sell, cfg.notional, pnl, pnl / cfg.notional * 100,
                   reason, rb, ref_entry, ref_exit, fee_sec=acc.sec, fee_taf=acc.taf, fee_cat=acc.cat)


def recost(t: BTTrade, cfg: BTConfig, slippage_multiplier: float) -> float:
    """Return % of the same trade under heavier slippage (cost stress test)."""
    base = cfg.costs_on(t.day)
    harsher = base.with_slippage(base.slippage_bps * slippage_multiplier)
    return _fill_trade(t.strategy, t.symbol, t.day, t.entry_i, t.exit_i, t.ref_entry, t.ref_exit, t.reason, cfg,
                       costs=harsher).ret_pct


def run_day(day: DayInput, strategies: list[str], cfg: BTConfig, rng: random.Random) -> list[BTTrade]:
    """Every signal of every strategy, each traded independently (no capital limit)."""
    from ..decision import cost_aware_bracket
    out: list[BTTrade] = []
    m = day.session_minutes
    day_costs = cfg.costs_on(day.day)
    flatten_i = m - cfg.flatten_minutes_before_close
    last_entry_i = m - cfg.last_entry_minutes_before_close
    for sym, bars in day.bars.items():
        if bars is None or len(bars) < 40:
            continue
        f = feature_frame(bars, day.session_open.to_pydatetime(), day.baselines.get(sym), m)
        fv = FrameView(f)
        # bar index -> minute-of-session index (bars can be missing on thin IEX data)
        mins = (fv["minute"].a - 1).astype(int)
        close, vwap = fv["close"].a, fv["vwap_s"].a
        n = len(fv)
        fl_i = int(np.searchsorted(mins, flatten_i))
        fired_at: dict[int, int] = {}
        sym_trades: list[BTTrade] = []
        for sid in strategies:
            strat = ALL_STRATEGIES[sid]
            busy_until = -1
            for i in range(n - 1):
                mi = mins[i]
                if i <= busy_until or mi < cfg.no_entries_first_minutes or mi >= last_entry_i:
                    continue
                if not close[i] > vwap[i]:  # every strategy requires price above VWAP — cheap pre-filter
                    continue
                trend = None
                if day.spy_trend is not None and 0 <= mi < len(day.spy_trend):
                    trend = bool(day.spy_trend[mi])
                idea = strat.entry(fv, i, Context(sym, trend, m))
                if idea is None or idea.score < cfg.min_signal_score:
                    continue
                ref = close[i]
                stop_pct = (ref - idea.stop_price) / ref * 100
                if stop_pct > cfg.max_stop_pct:
                    continue
                stop = min(idea.stop_price, ref * (1 - cfg.min_stop_pct / 100))
                target = idea.target_price
                widened = False
                if cfg.cost_aware_brackets:
                    cost = day_costs.round_trip_cost_pct(cfg.notional, ref, cfg.spread_bps)
                    stop, target, widened = cost_aware_bracket(ref, stop, target, cost)
                    if (ref - stop) / ref * 100 > cfg.max_stop_pct:
                        continue
                entry_i = i + 1
                entry_ref = fv["open"].a[entry_i]
                ex_i, ex_px, why = _simulate_exit(fv, strat, entry_i, entry_ref, stop, target,
                                                  idea.max_hold_minutes, fl_i, cfg)
                t = _fill_trade(sid, sym, day.day, entry_i, ex_i, entry_ref, ex_px, why, cfg)
                t.minute, t.exit_minute, t.score = int(mins[entry_i]), int(mins[min(ex_i, n - 1)]), idea.score
                t.stop_pct, t.target_pct, t.widened = (ref - stop) / ref * 100, (target / ref - 1) * 100, widened
                t.signal_px = float(ref)
                fired_at[i] = fired_at.get(i, 0) + 1
                sym_trades.append(t)
                # random-entry baseline: same symbol/day, same stop/target distance and hold, random time
                eligible = np.nonzero((mins >= cfg.no_entries_first_minutes) & (mins < last_entry_i))[0]
                eligible = eligible[eligible < n - 2]
                for _ in range(cfg.random_baseline_draws if len(eligible) else 0):
                    ri = int(eligible[rng.randrange(len(eligible))]) + 1
                    rref = fv["open"].a[ri]
                    rs = rref * (1 - t.stop_pct / 100)
                    rt = rref * (1 + t.target_pct / 100)
                    rx_i, rx_px, rwhy = _simulate_exit(fv, strat, ri, rref, rs, rt, idea.max_hold_minutes, fl_i, cfg,
                                                       use_strategy_exit=False)
                    out.append(_fill_trade(sid, sym, day.day, ri, rx_i, rref, rx_px, rwhy, cfg, rb=True))
                busy_until = ex_i
        for t in sym_trades:
            t.agreeing = fired_at.get(t.entry_i - 1, 1)
        out.extend(sym_trades)
    return out


def replay_portfolio(trades: list[BTTrade], cfg: BTConfig, use_ranker: bool, spy_by_day: dict,
                     gate: Optional[Callable[[BTTrade], bool]] = None,
                     chooser: Optional[Callable[[date, int, list, int], list[BTTrade]]] = None) -> list[BTTrade]:
    """Replay a day the way the live engine trades it: limited slots, one position per symbol,
    cooldown after exits. With use_ranker, candidates are compared by the live OpportunityRanker
    (and refused when nothing clears its bar); without it, first come first served.

    gate(trade) -> bool is asked about every pick before it is taken (the "ai confirms" mode);
    chooser(day, minute, [(candidate, trade), ...], free_slots) replaces the ranker's own choice with
    a pick among its shortlist (the "ai picks" mode). Both see only what was known at that minute."""
    from ..decision import Evidence, OpportunityRanker, classify_regime
    from ..models import Signal
    ranker = OpportunityRanker(cfg.costs, max_stop_pct=cfg.max_stop_pct)
    taken: list[BTTrade] = []
    by_day: dict[date, list[BTTrade]] = {}
    for t in trades:
        if not t.random_baseline:
            by_day.setdefault(t.day, []).append(t)
    for day, ts in sorted(by_day.items()):
        open_until: dict[str, int] = {}
        last_exit: dict[str, int] = {}
        minutes = sorted({t.minute for t in ts})
        trend = spy_by_day.get(day)
        for mi in minutes:
            active = {s: e for s, e in open_until.items() if e > mi}
            for s, e in open_until.items():
                if e <= mi:
                    last_exit[s] = e
            open_until = active
            free = cfg.max_positions - len(open_until)
            if free <= 0:
                continue
            here = [t for t in ts if t.minute == mi and t.symbol not in open_until
                    and mi - last_exit.get(t.symbol, -10_000) >= cfg.cooldown_minutes]
            best: dict[str, BTTrade] = {}
            for t in here:  # one candidate per symbol (strongest), like the live scanner
                if t.symbol not in best or t.score > best[t.symbol].score:
                    best[t.symbol] = t
            cands = list(best.values())
            if use_ranker:
                above = bool(trend[mi]) if trend is not None and 0 <= mi < len(trend) else None
                regime = classify_regime(above, None, None, None)
                scored = []
                for t in cands:
                    price = t.signal_px if cfg.ranker_price == "signal" and t.signal_px else t.ref_entry
                    sig = Signal(t.symbol, t.strategy, None, price, price * (1 - t.stop_pct / 100),
                                 price * (1 + t.target_pct / 100), t.score, [], 60, {},
                                 agreeing=[t.strategy] * t.agreeing)
                    scored.append((ranker.score(sig, regime, Evidence(), cfg.notional, cfg.spread_bps), t))
                decision = ranker.decide([c for c, _ in scored], regime, free)
                if chooser is not None:
                    slot_only = ["a better opportunity took the last free slot"]
                    shortlist = sorted([(c, t) for c, t in scored if not c.blockers or c.blockers == slot_only],
                                       key=lambda ct: ct[0].score, reverse=True)[:3]
                    picks = chooser(day, mi, shortlist, free)[:free] if shortlist else []
                else:
                    chosen_syms = {c.signal.symbol for c in decision.chosen}
                    picks = [t for c, t in scored if c.signal.symbol in chosen_syms]
            else:
                picks = sorted(cands, key=lambda t: t.score, reverse=True)[:free]
            for t in picks:
                if gate is not None and not gate(t):
                    continue
                taken.append(t)
                open_until[t.symbol] = max(t.exit_minute, mi + 1)
    return taken


def with_daily_rounding(trades: list[BTTrade], cfg: BTConfig) -> list[BTTrade]:
    """Copies of one account's trades with the broker's once-a-day fee rounding charged to the last
    trade of each day (each fee type's daily total is rounded up to a cent). No-op for per-trade rounding."""
    import copy
    by_day: dict[date, list[BTTrade]] = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t)
    out: list[BTTrade] = []
    for day, ts in sorted(by_day.items()):
        c = cfg.costs_on(day)
        extra = c.daily_rounding(FeeBreakdown(t.fee_sec, t.fee_taf, t.fee_cat) for t in ts)
        ts = [copy.copy(t) for t in ts]
        if extra > 0:
            last = max(ts, key=lambda t: (t.exit_minute, t.minute))
            last.pnl -= extra
            last.ret_pct = last.pnl / last.notional * 100
        out.extend(ts)
    return out


# ------------------------------------------------------------------ statistics

def bootstrap_p_value(returns: list[float], draws: int = 2000, seed: int = 3) -> float:
    """P(mean <= 0) under resampling. Small values = evidence the average trade is positive."""
    if len(returns) < 5:
        return 1.0
    rng = np.random.default_rng(seed)
    arr = np.asarray(returns, dtype=float)
    idx = rng.integers(0, len(arr), size=(draws, len(arr)))
    means = arr[idx].mean(axis=1)
    return float((means <= 0).mean())


def t_stat(returns: list[float]) -> float:
    n = len(returns)
    if n < 3:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    sd = arr.std(ddof=1)
    return float(arr.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0


def summarize(trades: list[BTTrade]) -> dict:
    rets = [t.ret_pct for t in trades]
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gl = -sum(losses)
    eq = np.cumsum(pnls) if pnls else np.array([0.0])
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))
    dd = float((peak[1:] - eq).max()) if len(eq) else 0.0
    return {
        "trades": len(trades),
        "winRate": len(wins) / len(trades) if trades else 0.0,
        "avgRetPct": float(np.mean(rets)) if rets else 0.0,
        "totalPnl": float(sum(pnls)),
        "profitFactor": float(sum(wins) / gl) if gl > 0 else (999.0 if wins else 0.0),
        "avgWin": float(np.mean(wins)) if wins else 0.0,
        "avgLoss": float(np.mean(losses)) if losses else 0.0,
        "maxDrawdown": dd,
        "tStat": t_stat(rets),
        "pValue": bootstrap_p_value(rets),
    }


@dataclass
class Verdict:
    verdict: str  # evidence | no_edge | insufficient
    explanation: str


def judge(in_sample: dict, out_sample: dict, oos_random: dict, min_oos_trades: int = 30) -> Verdict:
    if out_sample["trades"] < min_oos_trades:
        return Verdict("insufficient", f"only {out_sample['trades']} out-of-sample trades (need {min_oos_trades}) — "
                                       "not enough to tell luck from skill")
    checks = [
        (in_sample["avgRetPct"] > 0, "lost money on the training period"),
        (out_sample["avgRetPct"] > 0, "lost money on the unseen test period after costs"),
        (out_sample["profitFactor"] >= 1.15, f"test-period profit factor {out_sample['profitFactor']:.2f} < 1.15"),
        (out_sample["pValue"] < 0.10, f"test-period results could easily be luck (p={out_sample['pValue']:.2f})"),
        (out_sample["avgRetPct"] > oos_random["avgRetPct"],
         "did not beat random entries with the same stops and targets"),
    ]
    failed = [msg for ok, msg in checks if not ok]
    if failed:
        return Verdict("no_edge", "; ".join(failed))
    return Verdict("evidence", f"positive after costs on unseen data ({out_sample['avgRetPct']:.3f}% per trade, "
                               f"p={out_sample['pValue']:.2f}) and better than random entries")


def risk_adjusted(trades: list[BTTrade], day_list: list[date], capital: float = 100.0) -> dict:
    """Daily-P/L based metrics on a $100 account (days without trades count as 0)."""
    pnl_by_day = {d: 0.0 for d in day_list}
    for t in trades:
        pnl_by_day[t.day] = pnl_by_day.get(t.day, 0.0) + t.pnl
    daily = np.array([pnl_by_day[d] for d in sorted(pnl_by_day)]) / capital * 100
    if len(daily) < 2:
        return {"sharpe": 0.0, "sortino": 0.0, "maxDrawdownPct": 0.0, "totalReturnPct": 0.0, "calmar": 0.0}
    eq = capital * np.cumprod(1 + daily / 100)
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))
    dd = float(((peak[1:] - eq) / peak[1:]).max() * 100)
    sd = daily.std(ddof=1)
    down = daily[daily < 0]
    dsd = down.std(ddof=1) if len(down) > 1 else 0.0
    total = float((eq[-1] / capital - 1) * 100)
    years = len(daily) / 252
    ann = ((1 + total / 100) ** (1 / years) - 1) * 100 if years > 0 and total > -100 else 0.0
    return {"sharpe": float(daily.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "sortino": float(daily.mean() / dsd * math.sqrt(252)) if dsd > 0 else 0.0,
            "maxDrawdownPct": dd, "totalReturnPct": total, "calmar": float(ann / dd) if dd > 0 else 0.0}


def split_days(day_list: list[date], train: float = 0.5, validation: float = 0.25) -> tuple[list, list, list]:
    n = len(day_list)
    a, b = int(n * train), int(n * (train + validation))
    return day_list[:a], day_list[a:b], day_list[b:]


def walk_forward(trades: list[BTTrade], day_list: list[date], folds: int = 4) -> list[dict]:
    out = []
    size = max(1, len(day_list) // folds)
    for k in range(folds):
        chunk = set(day_list[k * size:(k + 1) * size] if k < folds - 1 else day_list[k * size:])
        ts = [t for t in trades if t.day in chunk]
        out.append({"fold": k + 1, "days": len(chunk), "trades": len(ts),
                    "avgRetPct": float(np.mean([t.ret_pct for t in ts])) if ts else 0.0})
    return out


def day_regimes(days: list[DayInput]) -> dict[date, str]:
    """Label each day by what SPY did (known only after the fact — used for reporting, never for trading)."""
    out, ranges = {}, {}
    for d in days:
        spy = d.bars.get("SPY")
        if spy is None or spy.empty:
            continue
        o, c = float(spy["open"].iat[0]), float(spy["close"].iat[-1])
        ranges[d.day] = (float(spy["high"].max()) - float(spy["low"].min())) / o * 100
        out[d.day] = "up day" if c >= o else "down day"
    if ranges:
        med = float(np.median(list(ranges.values())))
        for day, r in ranges.items():
            out[day] += ", volatile" if r > 1.5 * med else ", calm"
    return out


def judge_full(train: dict, val: dict, test: dict, rand_test: dict, folds: list[dict], stress_test_avg: float,
               p_adjusted: float, synthetic: bool, min_test_trades: int = 30) -> Verdict:
    if test["trades"] < min_test_trades:
        return Verdict("insufficient", f"only {test['trades']} trades in the unseen test period (need "
                                       f"{min_test_trades}) — not enough to tell luck from skill")
    positive_folds = sum(1 for f in folds if f["trades"] and f["avgRetPct"] > 0)
    used_folds = sum(1 for f in folds if f["trades"])
    checks = [
        (train["avgRetPct"] > 0, "lost money on the training period after costs"),
        (val["avgRetPct"] > 0, "lost money on the validation period after costs"),
        (test["avgRetPct"] > 0, "lost money on the unseen test period after costs"),
        (test["profitFactor"] >= 1.15, f"test profit factor {test['profitFactor']:.2f} is below 1.15"),
        (p_adjusted < 0.10, f"could easily be luck once you account for testing several strategies "
                            f"(adjusted p={p_adjusted:.2f})"),
        (test["avgRetPct"] > rand_test["avgRetPct"], "did not beat random entries with the same stops and targets"),
        (used_folds and positive_folds / used_folds >= 0.6,
         f"only {positive_folds} of {used_folds} walk-forward periods were profitable (unstable)"),
        (stress_test_avg > 0, "turns unprofitable if slippage doubles (too fragile to costs)"),
        (train["avgRetPct"] <= 0 or test["avgRetPct"] >= 0.3 * train["avgRetPct"],
         "results collapse from training to testing — a sign of overfitting"),
    ]
    failed = [msg for ok, msg in checks if not ok]
    if failed:
        return Verdict("no_edge", "; ".join(failed))
    if synthetic:
        return Verdict("synthetic_only", "passed every check, but on synthetic demo data — this proves nothing "
                                         "about real markets")
    return Verdict("evidence", f"profitable after costs on unseen data ({test['avgRetPct']:.3f}% per trade), stable "
                               f"across {positive_folds}/{used_folds} periods, robust to doubled slippage, beats random "
                               f"entries (adjusted p={p_adjusted:.2f})")


def run_backtest(days: list[DayInput], strategies: list[str], cfg: BTConfig, oos_fraction: float = 0.4,
                 progress: Optional[Callable[[float, str], None]] = None, synthetic: bool = False,
                 expected_symbols: Optional[list[str]] = None) -> dict:
    rng = random.Random(cfg.seed)
    days = sorted(days, key=lambda d: d.day)
    day_list = [d.day for d in days]
    all_trades: list[BTTrade] = []
    for k, d in enumerate(days):
        all_trades.extend(run_day(d, strategies, cfg, rng))
        if progress:
            progress((k + 1) / len(days), f"simulated {d.day.isoformat()}")
    tr_days, va_days, te_days = split_days(day_list)
    tr_set, va_set, te_set = set(tr_days), set(va_days), set(te_days)
    regimes = day_regimes(days)

    def part(ts, which):
        return [t for t in ts if t.day in which]

    raw = {}
    for sid in strategies:
        real = [t for t in all_trades if t.strategy == sid and not t.random_baseline]
        rand = [t for t in all_trades if t.strategy == sid and t.random_baseline]
        raw[sid] = (real, rand, summarize(part(real, te_set))["pValue"])
    n_tests = max(1, len(strategies))
    results = {}
    for sid, (real, rand, p_test) in raw.items():
        s_tr, s_va, s_te = summarize(part(real, tr_set)), summarize(part(real, va_set)), summarize(part(real, te_set))
        s_rand = summarize(part(rand, te_set))
        folds = walk_forward(real, day_list)
        stress = [recost(t, cfg, 2.0) for t in part(real, te_set)]
        stress_avg = float(np.mean(stress)) if stress else 0.0
        p_adj = min(1.0, p_test * n_tests)
        v = judge_full(s_tr, s_va, s_te, s_rand, folds, stress_avg, p_adj, synthetic)
        by_regime = {}
        for label in sorted(set(regimes.values())):
            ts = [t for t in real if regimes.get(t.day) == label]
            by_regime[label] = {"trades": len(ts), "avgRetPct": float(np.mean([t.ret_pct for t in ts])) if ts else 0.0}
        curve = list(np.round(np.cumsum([t.pnl for t in real]), 4)) if real else []
        results[sid] = {
            "name": ALL_STRATEGIES[sid].name, "train": s_tr, "validation": s_va, "test": s_te,
            "inSample": summarize(part(real, tr_set | va_set)), "outOfSample": s_te, "randomBaseline": s_rand,
            "all": summarize(real), "walkForward": folds, "stressAvgRetPct": stress_avg, "pAdjusted": p_adj,
            "riskAdjusted": risk_adjusted(part(real, te_set), te_days), "regimes": by_regime,
            "widenedShare": (sum(t.widened for t in real) / len(real)) if real else 0.0,
            "verdict": v.verdict, "explanation": v.explanation, "curve": [float(x) for x in curve[-400:]]}

    # ---- decision approaches on the unseen test period, with the live engine's capital limits
    spy_by_day = {d.day: d.spy_trend for d in days}
    test_trades = part(all_trades, te_set)
    every = [t for t in test_trades if not t.random_baseline]
    approaches = {}
    for key, label, ts in [
        ("every_signal", "every signal (unlimited money)", every),
        ("first_come", "first come, first served (2 slots)", replay_portfolio(test_trades, cfg, False, spy_by_day)),
        ("ranked", "ranked — what the app does (2 slots)", replay_portfolio(test_trades, cfg, True, spy_by_day)),
        ("random", "random entries (same stops/targets)", [t for t in test_trades if t.random_baseline]),
    ]:
        s_ = summarize(ts)
        approaches[key] = {"label": label, **{k: s_[k] for k in ("trades", "winRate", "avgRetPct", "totalPnl",
                                                                  "profitFactor", "maxDrawdown")},
                           "riskAdjusted": risk_adjusted(ts, te_days)}
    spy_bh = spy_oc = None
    spy_days = [d for d in days if d.day in te_set and d.bars.get("SPY") is not None and not d.bars["SPY"].empty]
    if spy_days:
        first, last = spy_days[0].bars["SPY"], spy_days[-1].bars["SPY"]
        spy_bh = (float(last["close"].iat[-1]) / float(first["open"].iat[0]) - 1) * 100
        spy_oc = float(np.mean([(float(d.bars["SPY"]["close"].iat[-1]) / float(d.bars["SPY"]["open"].iat[0]) - 1) * 100
                                for d in spy_days]))
    benchmarks = {"spyBuyHoldPct": spy_bh, "spyOpenToCloseAvgPct": spy_oc, "doNothingPct": 0.0}

    # ---- data coverage: report gaps, never fill them
    expected = expected_symbols or sorted({s for d in days for s in d.bars})
    have = sum(1 for d in days for s in expected if d.bars.get(s) is not None and not d.bars[s].empty)
    coverage = {"symbols": len(expected), "days": len(days), "symbolDaysExpected": len(expected) * len(days),
                "symbolDaysWithData": have,
                "missing": [f"{s} {d.day}" for d in days for s in expected
                            if d.bars.get(s) is None or d.bars[s].empty][:25]}
    return {"days": len(days), "firstDay": day_list[0].isoformat() if days else "",
            "lastDay": day_list[-1].isoformat() if days else "",
            "splitDay": te_days[0].isoformat() if te_days else "",
            "splits": {"train": [tr_days[0].isoformat(), tr_days[-1].isoformat()] if tr_days else [],
                       "validation": [va_days[0].isoformat(), va_days[-1].isoformat()] if va_days else [],
                       "test": [te_days[0].isoformat(), te_days[-1].isoformat()] if te_days else []},
            "strategies": results, "approaches": approaches, "benchmarks": benchmarks, "coverage": coverage,
            "aiNote": "AI decisions can't be backtested honestly (there is no record of what a model would have said "
                      "on past days). AI vs rules is measured going forward with shadow trades instead.",
            "synthetic": synthetic,
            "config": {"notional": cfg.notional, "spreadBps": cfg.spread_bps, "slippageBps": cfg.costs.slippage_bps,
                       "maxPositions": cfg.max_positions}}

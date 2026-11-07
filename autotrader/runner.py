"""The autonomous loop: observation -> investigation -> decision -> risk check -> simulated execution -> outcome.

No step asks a person. Failure handling (the safe state):
* a model that fails (unreachable, timeout, refusal, crash) gives "no trade" for that event; after the owner's
  limit of consecutive failures it is switched off for the rest of the day, and a safety event is recorded;
* data that fails (filing, prices) skips that event; after the owner's limit of consecutive failures no new
  decisions are made that day;
* the risk controller rejects on any internal error;
* if the ledger cannot be written, the run stops: nothing is done that cannot be recorded;
* the kill switch file stops everything at the next step.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

from dkapp.clock import ET, MarketCalendar

from .decision import no_trade
from .deciders import ModelFailure
from .evidence import EvidencePack
from .ledger import Ledger, LedgerError, utcnow
from .policy import OwnerPolicy
from .risk import Health, RiskController, SimPortfolio, SimPosition
from .simulate import complete_day, fill_entry

ENTRY_LATENCY = timedelta(seconds=30)
ENTRY_TIME = dtime(10, 0, 30)
PRE_OPEN_CUTOFF = dtime(9, 25)


def first_entry_session(accepted: datetime, cal: MarketCalendar) -> date:
    """The first session whose 10:00:30 entry comes after the filing: the same day if it was accepted before
    09:25 on a trading day, otherwise the next session."""
    a = accepted.astimezone(ET)
    if cal.session(a.date()) and a.time() < PRE_OPEN_CUTOFF:
        return a.date()
    return cal.next_session(datetime.combine(a.date(), dtime(23, 59), ET)).day


class Autonomy:
    def __init__(self, ledger: Ledger, policy: OwnerPolicy, deciders: list, controller: RiskController,
                 prices=None, redact: bool = True):
        self.ledger, self.policy, self.deciders, self.controller = ledger, policy, deciders, controller
        self.prices, self.redact = prices, redact
        self.health = Health()
        self.portfolios = {d.name: SimPortfolio() for d in deciders}
        self.model_fail = {d.name: 0 for d in deciders}
        self.model_down: set[str] = set()
        self.stopped = False

    # ------------------------------------------------------------------ helpers
    def safety(self, kind: str, detail: str, **kw) -> None:
        self.ledger.append("safety_event", {"kind": kind, "detail": detail[:500], **kw})

    def start_cycle(self, label: str) -> None:
        self.ledger.append("cycle", {"label": label, "policy_sha256": self.policy.sha256,
                                     "deciders": {d.name: getattr(d, "version", "?") for d in self.deciders},
                                     "models": {d.name: d.model_id for d in self.deciders if hasattr(d, "model_id")}})

    def decide(self, dec, pack: EvidencePack) -> dict:
        if dec.name in self.model_down:
            return no_trade(dec.name, "model switched off for the rest of the day (safe state)", safe_state=True)
        try:
            d = dec.decide(pack)
            self.model_fail[dec.name] = 0
            return d
        except Exception as exc:                       # ModelFailure or a bug in the decider: never a trade
            self.model_fail[dec.name] += 1
            self.safety("model", f"{dec.name}: {exc}", event_id=pack.event_id,
                        consecutive=self.model_fail[dec.name])
            if self.model_fail[dec.name] >= self.policy.max_consecutive_model_failures:
                self.model_down.add(dec.name)
                self.safety("safe_state", f"{dec.name} switched off after {self.model_fail[dec.name]} consecutive failures")
            return no_trade(dec.name, "model failure: no trade", invalid=True, problems=[str(exc)[:300]],
                            failure=isinstance(exc, ModelFailure))

    def halted(self) -> bool:
        if self.controller.kill_switch():
            if not self.stopped:
                self.safety("kill_switch", "kill switch present: stopping")
            self.stopped = True
        reasons = self.health.safe_state_reasons(self.policy)
        if reasons and not self.stopped:
            self.safety("safe_state", "; ".join(reasons))
            self.stopped = True
        return self.stopped

    # ------------------------------------------------------------------ one session (replay, or forward scoring)
    def run_session(self, day: date, rows: list, build: Callable[[object], EvidencePack]) -> list[dict]:
        """Decide every event of one session at its decision time, enter at decision + 30 s, exit the same day."""
        self.stopped = False
        self.health.data_failures = 0
        self.model_down.clear()
        self.start_cycle(f"session {day.isoformat()}")
        opened: dict[str, list[dict]] = {d.name: [] for d in self.deciders}
        for row in rows:
            if self.halted():
                break
            try:
                pack = build(row)
                self.health.data_failures = 0
            except LedgerError:
                raise
            except Exception as exc:
                self.health.data_failures += 1
                self.safety("data", f"{type(exc).__name__}: {exc}", consecutive=self.health.data_failures)
                continue
            self.ledger.append("evidence", pack.record(redact=self.redact))
            for dec in self.deciders:
                d = self.decide(dec, pack)
                rec = self.ledger.record_decision(pack.event_id, dec.name, pack.sha256, pack.decision_time, d)
                entry_t = pack.decision_time + ENTRY_LATENCY
                pf = self.portfolios[dec.name]
                pf.roll(day)
                q = self.prices.quote(d["instrument"], entry_t) if d["action"] == "long" and self.prices else None
                v = self.controller.check(d, pack, q, pf, self.health, entry_t)
                self.ledger.append("risk", {"decision_seq": rec["seq"], "decider": dec.name, "event_id": pack.event_id,
                                            **v})
                if v.get("error"):
                    self.safety("risk_controller", "; ".join(v["reasons"]), event_id=pack.event_id)
                if not v["approved"]:
                    continue
                px = fill_entry(q)
                qty = v["notional"] / px
                pf.open.append(SimPosition(v["symbol"], qty, px, v["stop_price"], entry_t))
                pf.trades_today += 1
                exit_t = datetime.combine(day, datetime.strptime(d["exit"]["exit_time"], "%H:%M").time(), ET)
                opened[dec.name].append({"decision_seq": rec["seq"], "decider": dec.name, "event_id": pack.event_id,
                                         "symbol": v["symbol"], "entry_time": entry_t, "entry_mid": q.mid,
                                         "entry_px": px, "entry_hs_bps": q.spread_bps / 2, "notional": v["notional"],
                                         "stop_price": v["stop_price"], "target_price": v.get("target_price"),
                                         "exit_time": exit_t})
        results = []
        for name, ops in opened.items():
            if not ops:
                continue
            pf = self.portfolios[name]
            for o in complete_day(ops, self.prices, lambda dd, t: t):
                if o["scored"]:
                    pf.close(o["symbol"], o["pnl_usd"], o["exit_at"])
                    out = {k: o[k] for k in ("decider", "event_id", "symbol", "entry_mid", "entry_px", "exit_ref",
                                             "exit_px", "reason", "notional", "gross_pct", "net_pct", "fees_usd",
                                             "pnl_usd", "flags")}
                    out["exit_at"] = o["exit_at"].isoformat() if o["exit_at"] is not None else None
                else:
                    pf.open = [p for p in pf.open if p.symbol != o["symbol"]]
                    out = {"decider": name, "event_id": o["event_id"], "symbol": o["symbol"], "unscorable": True,
                           "flags": o["flags"]}
                self.ledger.record_outcome(o["decision_seq"], out, data_from=o["entry_time"])
                results.append(out)
        self.ledger.append("session_end", {"day": day.isoformat(),
                                           "equity": {k: round(p.equity, 4) for k, p in self.portfolios.items()}})
        return results

    # ------------------------------------------------------------------ forward: observe and decide now
    def forward_cycle(self, observer, now: Optional[datetime] = None, calendar: Optional[MarketCalendar] = None) -> int:
        """Observe new filings and record each decider's decision now, before the session they apply to."""
        now = now or utcnow()
        cal = calendar or MarketCalendar()
        if self.halted():
            return 0
        self.start_cycle(f"forward {now.astimezone(ET).isoformat(timespec='minutes')}")
        seen = {r["payload"]["event_id"].split(":")[1] for r in self.ledger.of_type("evidence")}
        try:
            filings = observer.new_filings(seen)
            self.health.data_failures = 0
        except Exception as exc:
            self.health.data_failures += 1
            self.safety("data", f"EDGAR feed: {type(exc).__name__}: {exc}", consecutive=self.health.data_failures)
            return 0
        n, stale = 0, []
        for f in filings:
            if self.halted():
                break
            if f["accession"] in seen:                 # defence in depth: the observer filters these too
                continue
            session = first_entry_session(f["updated"], cal)
            if datetime.combine(session, ENTRY_TIME, ET) <= now:
                stale.append(f["accession"])           # the market already traded on it: not a fresh decision
                continue
            try:
                pack = observer.pack(f, now)
            except Exception as exc:
                self.health.data_failures += 1
                self.safety("data", f"{f['accession']}: {type(exc).__name__}: {exc}", consecutive=self.health.data_failures)
                continue
            pack.structured["session"] = session.isoformat()
            self.ledger.append("evidence", pack.record(redact=self.redact))
            for dec in self.deciders:
                d = self.decide(dec, pack) | {"session": session.isoformat()}
                self.ledger.record_decision(pack.event_id, dec.name, pack.sha256, pack.decision_time, d, now=utcnow())
            n += 1
        self.ledger.append("heartbeat", {"filings_seen": len(filings), "decided": n, "skipped_already_traded": stale,
                                         "model_down": sorted(self.model_down)})
        return n

    def run_forever(self, observer, poll_seconds: int = 300, max_cycles: Optional[int] = None,
                    sleep: Callable[[float], None] = _time.sleep) -> None:
        """Unattended forward observation. Ordinary errors back off and retry; unsafe conditions stop the loop."""
        cycles, backoff = 0, poll_seconds
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            if self.halted():
                return
            try:
                self.forward_cycle(observer)
                backoff = poll_seconds
            except LedgerError:
                raise                                   # cannot record: stop
            except Exception as exc:
                self.safety("cycle", f"{type(exc).__name__}: {exc}")
                backoff = min(backoff * 2, 3600)
            sleep(backoff)

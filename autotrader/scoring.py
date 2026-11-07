"""Unattended forward scoring (evaluation protocol v2).

Forward decisions are recorded before their session; this module completes the session once its prices exist:

1. finds sessions whose decisions are unscored and whose prices are available (historical consolidated data is
   available 15 minutes after the fact, so a session can be scored from 16:20 ET);
2. computes the pre-registered mechanical baselines **as of 10:00** of that session (`price_only`, `rule_p4h1`),
   from 15-minute bars that ended by 10:00 and the prior close only. Each input's end time is written with the
   decision and the ledger refuses any input that ended later. They are labelled as-of, never real-time;
3. records what each event's stock did from 10:00:30 to 15:45 (scores every prediction, traded or not);
4. puts every decision, real-time and as-of, through the same risk controller at 10:00:30 and simulates it;
5. writes a `session_scored` marker, so a session is never scored twice.

The scorer holds no AI decider and cannot call one: a recorded AI decision is executed exactly as written, its
entry conditions are checked mechanically at 10:00:30, and the ledger refuses an outcome that uses prices from
before the decision was written. Missing prices leave the session pending (retried on the next run); after
`give_up_days` the session is recorded as unscorable rather than silently dropped.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from dkapp.clock import ET, MarketCalendar

from .deciders import PriceOnly, RuleP4H1
from .evidence import Evidence, EvidencePack
from .ledger import LedgerError, utcnow
from .runner import Autonomy

ASOF = dtime(10, 0)
READY_AFTER = dtime(16, 20)
PROTOCOL = "2"


def _pack_from_record(p: dict) -> EvidencePack:
    items = [Evidence(i["id"], i["kind"], i["source"], datetime.fromisoformat(i["available_at"]), i["text"], i.get("url"))
             for i in p["items"]]
    return EvidencePack(p["event_id"], p["symbol"], p["sector_etf"], datetime.fromisoformat(p["decision_time"]), items,
                        structured=dict(p.get("structured") or {}))


class ForwardScorer:
    def __init__(self, auto: Autonomy, prices, calendar: Optional[MarketCalendar] = None, give_up_days: int = 5,
                 asof_baselines: tuple = (PriceOnly(), RuleP4H1())):
        for b in asof_baselines:
            if hasattr(b, "provider"):
                raise TypeError("as-of baselines must be mechanical rules, never a model")
        self.auto, self.prices, self.cal = auto, prices, calendar or MarketCalendar()
        self.give_up_days, self.asof_baselines = give_up_days, asof_baselines
        auto.prices = prices

    # ------------------------------------------------------------------ what is pending
    def _done(self) -> set[str]:
        led = self.auto.ledger
        return {r["payload"]["day"] for r in led.of_type("session_scored")} | \
               {r["payload"]["day"] for r in led.of_type("session_unscorable")}

    def pending(self, now: datetime) -> list[date]:
        done, out = self._done(), set()
        for r in self.auto.ledger.of_type("evidence"):
            s = (r["payload"].get("structured") or {}).get("session")
            if not s or s in done:
                continue
            d = date.fromisoformat(s)
            if datetime.combine(d, READY_AFTER, ET) <= now:
                out.add(d)
        return sorted(out)

    # ------------------------------------------------------------------ as-of baselines
    def _asof_inputs(self, sym: str, etf: str, day: date) -> tuple[dict, list[dict]]:
        cut = datetime.combine(day, ASOF, ET)
        inputs, moves = [], {}
        prev = self.cal.previous_session(datetime.combine(day, dtime(12, 0), ET))
        for s in (sym, etf):
            pc = self.prices.prev_close(s, day)
            bars = self.prices.bars15(s, day)
            done = bars[bars.index + timedelta(minutes=15) <= cut] if bars is not None and not bars.empty else None
            if pc is None or done is None or done.empty:
                moves[s] = None
                continue
            moves[s] = float(done["close"].iloc[-1]) / pc - 1
            inputs.append({"source": f"{s} prior close", "ended": prev.close.isoformat()})
            inputs.append({"source": f"{s} 15-minute bars", "ended": (done.index[-1] + timedelta(minutes=15)).isoformat()})
        react = None if moves.get(sym) is None or moves.get(etf) is None else moves[sym] - moves[etf]
        return {"react": react, "move_pct": None if moves.get(sym) is None else moves[sym] * 100,
                "sector_move_pct": None if moves.get(etf) is None else moves[etf] * 100}, inputs

    def _record_asof(self, pack: EvidencePack, day: date) -> list[tuple[dict, EvidencePack]]:
        cut = datetime.combine(day, ASOF, ET)
        structured, inputs = self._asof_inputs(pack.symbol, pack.sector_etf, day)
        structured["surprise"] = (pack.structured or {}).get("surprise") or {}    # forward: no consensus data
        text = (f"As of {ASOF.strftime('%H:%M')}: {pack.symbol} move from the prior close "
                f"{structured['move_pct'] if structured['move_pct'] is not None else 'n/a'}%, sector ETF "
                f"{pack.sector_etf} {structured['sector_move_pct'] if structured['sector_move_pct'] is not None else 'n/a'}%.")
        asof_pack = EvidencePack(pack.event_id, pack.symbol, pack.sector_etf, cut,
                                 [Evidence("E1", "price", "15-minute bars ended by 10:00, prior close", cut, text)],
                                 structured=structured | {"session": day.isoformat()})
        out = []
        for b in self.asof_baselines:
            if (pack.event_id, b.name) in self.auto.ledger.decisions:
                continue
            d = b.decide(asof_pack) | {"session": day.isoformat(), "protocol": PROTOCOL}
            rec = self.auto.ledger.record_asof_decision(pack.event_id, b.name, asof_pack.sha256, cut, d, inputs)
            out.append((rec, asof_pack))
        return out

    def _preflight(self, day: date, packs) -> None:
        t_in, t_out = datetime.combine(day, dtime(10, 0, 30), ET), datetime.combine(day, dtime(15, 45), ET)
        for p in packs:
            for s in (p.symbol, p.sector_etf):
                self.prices.prev_close(s, day)
                self.prices.bars15(s, day)
                self.prices.quote(s, t_in)
                self.prices.quote(s, t_out)

    # ------------------------------------------------------------------ one session
    def score_session(self, day: date) -> dict:
        led = self.auto.ledger
        if day.isoformat() in self._done():
            return {"day": day.isoformat(), "skipped": "already scored"}
        evid = [r["payload"] for r in led.of_type("evidence")
                if (r["payload"].get("structured") or {}).get("session") == day.isoformat()]
        packs = {p["event_id"]: _pack_from_record(p) for p in evid}
        self._preflight(day, packs.values())            # fetch every price first: a failure here writes nothing
        entries = []
        for r in led.of_type("decision"):
            p = r["payload"]
            if p["event_id"] in packs and not p.get("asof") and p["decision"].get("session") == day.isoformat():
                entries.append((r, packs[p["event_id"]]))
        for pk in packs.values():
            entries += self._record_asof(pk, day)
        self.auto.start_cycle(f"score session {day.isoformat()} (protocol {PROTOCOL})")
        results = self.auto.execute(day, entries)
        scored = {m["payload"]["event_id"] for m in led.of_type("market_outcome")}
        summary = {"day": day.isoformat(), "protocol": PROTOCOL, "events": len(packs), "decisions": len(entries),
                   "trades": len([x for x in results if not x.get("unscorable")]),
                   "eventsWithMarketOutcome": len(scored & set(packs))}
        led.append("session_scored", summary)
        return summary

    def run(self, now: Optional[datetime] = None) -> list[dict]:
        """Score every ready session. Price failures leave it pending; after give_up_days it is marked unscorable."""
        now = now or self.auto.clock()
        out = []
        for day in self.pending(now):
            try:
                out.append(self.score_session(day))
            except LedgerError:
                raise                                   # a record could not be written safely: stop
            except Exception as exc:
                self.auto.safety("data", f"scoring {day.isoformat()}: {type(exc).__name__}: {exc}")
                if now - datetime.combine(day, READY_AFTER, ET) > timedelta(days=self.give_up_days):
                    self.auto.ledger.append("session_unscorable", {"day": day.isoformat(), "reason": str(exc)[:300]})
        return out

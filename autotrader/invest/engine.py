"""The pilot loop: sessions in order, both portfolios, one decision per month, everything through the keyed ledger.

Each run recomputes both portfolios from the first fill session with the frozen rules and writes every record
through `InvestLedger.put`, so:
* a rerun with the same data and the same clock adds nothing (the ledger file is unchanged byte for byte);
* a frozen decision is read back, never asked again;
* a price that changed after a fill was recorded stops the run (`LedgerConflict`) instead of rewriting history;
* missing data is recorded as a gap and that portfolio stops there; nothing is filled in, and later sessions wait
  until the data exists (a later run resolves the gap and continues).
Decision timing: the pack is built at or after the cutoff close; a decision frozen at or after the next session's
open is late; no decision by then is missed. Late, missed and invalid decisions keep the existing holdings.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .accounting import Book, apply_actions, settle, stock_orders
from .contract import INSTRUCTIONS, json_schema, validate
from .data import MarketData
from .ledger import InvestLedger, LedgerError
from .pack import InputsIncomplete, MonthPack, build_pack
from .registration import (BENCHMARK, CAPITAL, FROZEN, MIN_SWEEP, Slot, code_sha256, config_sha256, schedule,
                           sessions_between)


class NotApproved(RuntimeError):
    """A genuine decision needs approvals that do not exist (see the registration)."""


@dataclass
class Decided:
    decision: Optional[dict]              # None: no answer
    frozen_at: Optional[datetime]
    attempted: bool                       # a request was made (and is charged) even if there is no answer
    cost: dict = field(default_factory=dict)   # {"usd", "basis", "reconciled"}
    note: str = ""


# ------------------------------------------------------------------ deciders
class FixtureDecider:
    """A scripted sequence of actions for the offline dry run. NOT A MODEL: citations are picked mechanically from
    the pack (the first passage and the valuation row of the stock), so the decisions say nothing about stock
    selection. Every record it produces is labelled decider=fixture, genuine=false."""

    name, genuine = "fixture", False

    def __init__(self, script: dict, est_cost_usd: str):
        self.script, self.est = script, est_cost_usd
        self.calls: list[int] = []

    @staticmethod
    def _citation(pack: MonthPack, sym: str, valuation: bool) -> list[dict]:
        out = []
        p = next((e for e in pack.items if e.id.startswith(f"{sym}/")), None)
        if p is not None:
            first = re.split(r"(?<=[.!?])\s+", p.text)[0]
            nums = re.findall(r"\d[\d,]*(?:\.\d+)?", first)
            out.append({"id": p.id, "quote": first, "figures": nums[:1]})
        v = pack.get(f"V.{sym}")
        if valuation and v is not None:
            m = re.search(r"P/E [\d.]+", v.text) or re.search(r"price [\d.]+ USD", v.text)
            out.append({"id": v.id, "quote": m.group(0), "figures": re.findall(r"\d[\d.]*", m.group(0))[:1]})
        return out

    def decide(self, slot: Slot, pack: MonthPack) -> Decided:
        self.calls.append(slot.index)
        spec = self.script.get(slot.index) or {"kind": "missing"}
        cost = {"usd": self.est, "basis": "fixture_estimate", "reconciled": True}
        if spec["kind"] == "missing":
            return Decided(None, None, True, cost, "fixture: the scripted month has no answer")
        positions, after = [], Decimal(0)
        for sym, act, w in spec["positions"]:
            h = pack.holding(sym)
            text = f"FIXTURE placeholder text for {act} {sym}; scripted for the dry run, not an investment view."
            positions.append({"symbol": sym, "action": act, "target_weight_pct": w,
                              "why_outperform_voo": text if act in ("buy", "increase") else "",
                              "main_downside": text if act in ("buy", "increase") else "",
                              "invalidation": text if act in ("buy", "increase") else "",
                              "weight_rationale": text if act in ("buy", "increase") else "",
                              "what_changed": text if act in ("reduce", "sell") else "",
                              "thesis_still_holds": text if act == "hold" else "",
                              "evidence": [] if act in ("reduce", "sell")
                              else self._citation(pack, sym, act in ("buy", "increase"))})
            if act == "hold":
                after += Decimal(str(h["weight_pct"])) if h else 0     # not held: validation rejects it
            elif act != "sell":
                after += Decimal(str(w))
        d = {"month": slot.month, "summary": f"FIXTURE decision {slot.index}: {spec.get('label', '')}",
             "voo_weight_pct": float(Decimal(100) - after), "positions": positions}
        frozen = slot.deadline + timedelta(minutes=5) if spec["kind"] == "late" else pack.built_at + timedelta(minutes=40)
        return Decided(d, frozen, True, cost)


class ModelDecider:
    """The genuine decider: one Claude Opus 5.5 request per month, exactly as frozen in the registration.
    `request()` renders it offline (for review, hashing and size estimates). `decide()` refuses: sending needs the
    approvals listed in the registration, and no client, key or network call exists in this version."""

    name, genuine = "model", True

    def __init__(self, universe: list[str]):
        self.universe = universe

    def request(self, pack: MonthPack) -> dict:
        m = FROZEN["model"]
        return {"model": m["id"], "max_tokens": m["max_tokens"], "system": INSTRUCTIONS,
                "messages": [{"role": "user", "content": pack.prompt_text()}],
                "output_config": {"effort": m["effort"],
                                  "format": {"type": "json_schema", "schema": json_schema(self.universe)}},
                "service_tier": "standard_only", "inference_geo": "global"}

    def decide(self, slot: Slot, pack: MonthPack) -> Decided:
        raise NotApproved("a genuine decision needs the approvals listed in REGISTRATION.md (owner approval, "
                          "registration freeze, model spend, data sources); nothing was sent")


# ------------------------------------------------------------------ run
def _prior_theses(ledger: InvestLedger, book: Book) -> list[dict]:
    out = []
    for sym in book.stocks():
        for r in reversed(ledger.of_type("decision")):
            p = r["payload"]
            if p["status"] != "valid":
                continue
            pos = next((x for x in p["decision"]["positions"] if x["symbol"] == sym and x["action"] in ("buy", "increase")), None)
            if pos:
                out.append({"symbol": sym, "month": p["month"], "why_outperform_voo": pos["why_outperform_voo"],
                            "invalidation": pos["invalidation"]})
                break
    return out


def run(state_dir: Path, data_dir: Path, decider, *, mode: str, first_fill_month: str, now: datetime) -> dict:
    state_dir = Path(state_dir)
    data = MarketData(data_dir)
    if mode == "dry_run" and (decider.genuine or not data.synthetic):
        raise LedgerError("a dry run takes synthetic data and fixture decisions only")
    if mode == "forward":
        now = datetime.now(timezone.utc)                # a forward run lives on the wall clock, never a given time
        if not decider.genuine or data.synthetic:
            raise LedgerError("a forward run takes genuine decisions and real data only")
        raise NotApproved("the forward clock has not started: the registration is proposed, not approved")
    slots = schedule(first_fill_month)
    stamp = (lambda t: t) if mode == "dry_run" else (lambda t: datetime.now(timezone.utc))
    identity = {"experiment": FROZEN["experiment"], "config_sha256": config_sha256(), "code_sha256": code_sha256(),
                "mode": mode,
                "first_fill_month": first_fill_month, "universe_sha256": data.universe_sha256,
                "decider": decider.name, "genuine": decider.genuine, "synthetic_data": data.synthetic}
    led = InvestLedger(state_dir / "ledger.jsonl", mode, identity, stamp(slots[0].cutoff.open))
    (state_dir / "packs").mkdir(exist_ok=True)
    books = {"voo": Book("voo", CAPITAL), "ai": Book("ai", CAPITAL)}
    halted: dict[str, str] = {}
    by_fill = {s.fill.day: s for s in slots}
    by_cutoff = {s.cutoff.day: s for s in slots}

    def gap(book: str, day, phase: str, missing: list[str]):
        led.put(f"gap|{book}|{day}|{phase}", "gap", {"book": book, "session": str(day), "phase": phase,
                                                     "missing": sorted(missing)}, stamp(now))
        halted[book] = f"{day} {phase}"

    def resolved(book: str, day, phase: str):
        if led.get(f"gap|{book}|{day}|{phase}") is not None:
            led.put(f"gap_resolved|{book}|{day}|{phase}", "gap_resolved",
                    {"book": book, "session": str(day), "phase": phase}, stamp(now))

    def decide(slot: Slot):
        key = f"decision|{slot.index}"
        if led.get(key) is not None or now < slot.cutoff.close:
            return
        base = {"slot": slot.index, "month": slot.month, "decider": decider.name, "genuine": decider.genuine,
                "cutoff": slot.cutoff.close.isoformat(), "deadline": slot.deadline.isoformat()}
        try:
            built = slot.cutoff.close + timedelta(minutes=20) if mode == "dry_run" else stamp(now)
            pack = build_pack(slot, books["ai"], data, built, _prior_theses(led, books["ai"]))
        except InputsIncomplete as exc:
            if now >= slot.deadline:
                led.put(key, "decision", base | {"status": "missed", "problems": [f"inputs incomplete: {exc}"],
                                                 "decision": None}, stamp(slot.deadline))
                led.put(f"cost|{slot.index}", "cost", {"slot": slot.index, "month": slot.month, "usd": "0",
                                                       "basis": "not_requested", "reconciled": True,
                                                       "genuine": decider.genuine}, stamp(slot.deadline))
            return
        rec = pack.record()
        pf = state_dir / "packs" / f"slot-{slot.index:02d}.json"
        body = json.dumps(rec, sort_keys=True, indent=1) + "\n"
        if pf.exists() and pf.read_text() != body:
            raise LedgerError(f"{pf.name}: a different pack is already frozen for this month")
        pf.write_text(body)
        d = decider.decide(slot, pack)
        if not d.attempted and d.decision is None and now < slot.deadline:
            return                                      # nothing asked yet; the deadline has not passed
        if d.frozen_at is not None and d.frozen_at > now:   # not frozen yet as of this run
            if now < slot.deadline:
                return
            d = Decided(None, None, d.attempted, d.cost, "no decision was frozen by the deadline")
        if d.frozen_at is not None and d.frozen_at < pack.built_at:
            raise LedgerError(f"{slot.month}: a decision cannot be frozen before its pack was built")
        base |= {"pack_sha256": pack.sha256, "pack_file": f"packs/{pf.name}", "built_at": pack.built_at.isoformat(),
                 "frozen_at": d.frozen_at.isoformat() if d.frozen_at else None, "decision": d.decision}
        when = d.frozen_at or slot.deadline
        if d.decision is None:
            out = base | {"status": "missed", "problems": [d.note or "no answer"]}
        elif d.frozen_at >= slot.deadline:
            out = base | {"status": "late", "problems": ["frozen at or after the fill session's open"]}
        else:
            targets, problems, voo = validate(d.decision, pack)
            out = base | {"status": "valid" if targets is not None else "invalid", "problems": problems,
                          "targets": targets, "voo_pct": str(voo) if voo is not None else None}
        led.put(key, "decision", out, stamp(when))
        led.put(f"cost|{slot.index}", "cost", {"slot": slot.index, "month": slot.month, "genuine": decider.genuine,
                                               **d.cost}, stamp(when))

    def open_step(name: str, s, prev_day) -> None:
        b = copy.deepcopy(books[name])
        events = apply_actions(b, prev_day, s.day, data.splits, data.dividends)
        slot = by_fill.get(s.day) if name == "ai" else None
        dec_rec, targets = None, None
        if slot is not None:
            dec_rec = led.get(f"decision|{slot.index}")
            if dec_rec is None:
                raise LedgerError(f"{slot.month}: fill session reached with no decision record")
            if dec_rec["payload"]["status"] == "valid":
                targets = dec_rec["payload"]["targets"]
        need = set()
        if targets:                                     # a 100%-VOO decision with nothing to sell needs no price
            need |= set(b.held()) | set(targets) | {BENCHMARK}
        if b.cash >= MIN_SWEEP:
            need.add(BENCHMARK)
        opens = {x: data.price(x, s.day, "open") for x in sorted(need)}
        missing = [x for x, v in opens.items() if v is None]
        if missing:
            gap(name, s.day, "open", missing)
            return
        resolved(name, s.day, "open")
        orders = stock_orders(b, opens, targets) if targets else []
        trade = settle(b, s.day, opens, orders) if (orders or b.cash >= MIN_SWEEP) else None
        if events or trade or slot is not None:
            led.put(f"session|{name}|{s.day}|open", "session_open",
                    {"book": name, "session": str(s.day), "actions": events, "trade": trade,
                     "decision": None if slot is None else {"slot": slot.index, "status": dec_rec["payload"]["status"]},
                     "after": b.snapshot()}, stamp(s.open))
        books[name] = b

    def close_step(name: str, s) -> None:
        b = books[name]
        closes = {x: data.price(x, s.day, "close") for x in b.held()}
        missing = [x for x, v in closes.items() if v is None]
        if missing:
            gap(name, s.day, "close", missing)
            return
        resolved(name, s.day, "close")
        led.put(f"session|{name}|{s.day}|close", "mark",
                {"book": name, "session": str(s.day), "wealth": str(b.wealth(closes)), "cash": str(b.cash),
                 "receivable": str(b.receivable()),
                 "positions": {x: {"qty": str(b.qty[x]), "close": str(closes[x]),
                                   "value": str(b.qty[x] * closes[x])} for x in b.held()}}, stamp(s.close))

    decide(slots[0])
    prev_day = slots[0].cutoff.day
    for s in sessions_between(slots[0].fill.day, slots[-1].period_end.day):
        if s.open > now:
            break
        for name in ("voo", "ai"):
            if name not in halted:
                open_step(name, s, prev_day)
        if s.close <= now:
            for name in ("voo", "ai"):
                if name not in halted:
                    close_step(name, s)
            nxt = by_cutoff.get(s.day)
            if nxt is not None and "ai" not in halted:
                decide(nxt)
        prev_day = s.day
    for slot in slots:                                   # e.g. the AI portfolio stopped at a gap before the cutoff
        if led.get(f"decision|{slot.index}") is None and now >= slot.deadline:
            led.put(f"decision|{slot.index}", "decision",
                    {"slot": slot.index, "month": slot.month, "decider": decider.name, "genuine": decider.genuine,
                     "cutoff": slot.cutoff.close.isoformat(), "deadline": slot.deadline.isoformat(),
                     "status": "missed", "decision": None,
                     "problems": [f"no decision before the deadline (portfolio stopped at {halted.get('ai', 'n/a')})"]},
                    stamp(slot.deadline))
            led.put(f"cost|{slot.index}", "cost", {"slot": slot.index, "month": slot.month, "usd": "0",
                                                   "basis": "not_requested", "reconciled": True,
                                                   "genuine": decider.genuine}, stamp(slot.deadline))
    return {"ledger": str(led.path), "records": len(led.records), "appended": led.appended, "halted": halted,
            "identity": identity}

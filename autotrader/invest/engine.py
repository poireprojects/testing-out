"""The pilot loop: sessions in order, both portfolios, one decision per month, everything through the keyed ledger.

Each run recomputes both portfolios from the first fill session with the frozen rules and writes every record
through `InvestLedger.put`, so:
* a rerun with the same data and the same clock adds nothing (the ledger file is unchanged byte for byte);
* a price that changed after a fill was recorded stops the run (`LedgerConflict`) instead of rewriting history;
* missing prices are recorded as a gap and that portfolio stops there; nothing is filled in, and later sessions wait
  until the data exists (a later run resolves the gap and continues).

A month's decision moves through append-only records, each written (and fsync'd) before the next step starts:

    pack|k      the frozen pack's SHA-256 (its file is written atomically first). Once recorded, the pack is read
                back from the file and never rebuilt, so newer data cannot rewrite it.
    request|k   written BEFORE the decider is asked. A request is never sent twice: if a run finds request|k with
                no answer|k, it crashed with the request in flight, and the month is a missed answer whose charge is
                unreconciled until provider evidence arrives.
    answer|k    the decider's raw result and its charge evidence, exactly as returned.
    decision|k  the status derived from the frozen pack and the answer (pure; recomputed identically on resume).
    cost|k      the charge derived from the answer: reconciled, unreconciled (unknown billing, never zero) or
                not_requested (no request record exists, so nothing can have been billed).
    reconcile|k a later, separate record that supplies provider evidence for an unreconciled charge.

Resume repairs any missing derived record (decision, cost) from the durable ones without asking again. Statuses:
valid, invalid_answer, missed_answer (no answer, late, or lost in flight) and missing_input (research inputs
incomplete: no request is made at all). Late, missed, invalid and missing-input months keep the holdings and never
trade; a missed decision is never backfilled after its deadline.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .accounting import Book, apply_actions, resulting, settle, stock_orders
from .contract import INSTRUCTIONS, json_schema, validate
from .data import MarketData
from .ledger import InvestLedger, LedgerError, atomic_write
from .pack import InputsIncomplete, MonthPack, build_pack, load_pack
from .registration import (BENCHMARK, CAPITAL, FROZEN, MIN_SWEEP, Slot, canonical_json, code_sha256, config_sha256,
                           schedule, sessions_between)

STATUSES = ("valid", "invalid_answer", "missed_answer", "missing_input")


class NotApproved(RuntimeError):
    """A genuine decision needs approvals that do not exist (see the registration)."""


@dataclass
class Decided:
    decision: Optional[dict]              # None: no answer
    frozen_at: Optional[datetime]         # when the answer was frozen (None without an answer)
    responded_at: datetime                # when the request came back, answered or not
    cost: Optional[dict] = None           # charge evidence {"usd", "basis"}; None: billing unknown
    note: str = ""


# ------------------------------------------------------------------ deciders
class FixtureDecider:
    """A scripted sequence of actions for the offline dry run. NOT A MODEL: citations are picked mechanically from
    the pack (the first passage and the valuation row of the stock), so the decisions say nothing about stock
    selection. Every record it produces is labelled decider=fixture, genuine=false. Simulated timing: the request
    leaves when the pack is built and returns 40 minutes later (a `late` month: 5 minutes after the deadline)."""

    name, genuine = "fixture", False

    def __init__(self, script: dict, est_cost_usd: Optional[str]):
        self.script, self.est = script, est_cost_usd
        self.calls: list[int] = []

    def _spec(self, slot: Slot) -> dict:
        return self.script.get(slot.index) or {"kind": "missing"}

    def responded_at(self, slot: Slot, pack: MonthPack) -> datetime:
        if self._spec(slot)["kind"] == "late":
            return slot.deadline + timedelta(minutes=5)
        return pack.built_at + timedelta(minutes=40)

    def ready(self, slot: Slot, pack: MonthPack, now: datetime) -> bool:
        """Whether, by `now`, the simulated request has completed (it is sent and answered in one step)."""
        return now >= self.responded_at(slot, pack)

    def request_digest(self, slot: Slot, pack: MonthPack) -> str:
        return hashlib.sha256(canonical_json({"fixture": self._spec(slot), "pack": pack.sha256})).hexdigest()

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
        spec = self._spec(slot)
        cost = None if (self.est is None or spec.get("cost") == "unknown") else {"usd": self.est,
                                                                                "basis": "fixture_estimate"}
        at = self.responded_at(slot, pack)
        if spec["kind"] == "missing":
            return Decided(None, None, at, cost, "fixture: the scripted month has no answer")
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
        return Decided(d, at, at, cost)


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

    def ready(self, slot: Slot, pack: MonthPack, now: datetime) -> bool:
        return True

    def request_digest(self, slot: Slot, pack: MonthPack) -> str:
        return hashlib.sha256(canonical_json(self.request(pack))).hexdigest()

    def decide(self, slot: Slot, pack: MonthPack) -> Decided:
        raise NotApproved("a genuine decision needs the approvals listed in REGISTRATION.md (owner approval, "
                          "registration freeze, model spend, data sources); nothing was sent")


# ------------------------------------------------------------------ helpers
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


def derive_decision(base: dict, pack: MonthPack, answer: dict) -> dict:
    """The decision record implied by a frozen pack and a stored answer (pure: the same on every resume)."""
    deadline = datetime.fromisoformat(base["deadline"])
    out = base | {"pack_sha256": pack.sha256, "built_at": pack.built_at.isoformat(),
                  "frozen_at": answer["frozen_at"], "decision": answer["decision"],
                  "cutoff_weights": {h["symbol"]: h["weight_pct"] for h in pack.holdings}}
    if answer.get("lost_in_flight"):
        return out | {"status": "missed_answer", "reason": "lost_in_flight", "problems": [answer["note"]]}
    if answer["decision"] is None:
        return out | {"status": "missed_answer", "reason": "no_answer", "problems": [answer["note"] or "no answer"]}
    if datetime.fromisoformat(answer["frozen_at"]) >= deadline:
        return out | {"status": "missed_answer", "reason": "late",
                      "problems": ["frozen at or after the fill session's open"]}
    targets, problems, voo = validate(answer["decision"], pack)
    return out | {"status": "valid" if targets is not None else "invalid_answer",
                  "reason": "" if targets is not None else "rules",
                  "problems": problems, "targets": targets, "voo_pct": str(voo) if voo is not None else None}


def derive_cost(slot: Slot, genuine: bool, answer: Optional[dict], requested: bool) -> dict:
    base = {"slot": slot.index, "month": slot.month, "genuine": genuine}
    if not requested:
        return base | {"status": "not_requested", "usd": "0", "basis": "no request record: nothing was sent"}
    ev = (answer or {}).get("cost")
    if ev and ev.get("usd") is not None:
        return base | {"status": "reconciled", "usd": str(ev["usd"]), "basis": ev["basis"]}
    return base | {"status": "unreconciled", "usd": None,
                   "basis": "no charge evidence: unresolved until a reconciliation record (never assumed zero)"}


# ------------------------------------------------------------------ run
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
    with InvestLedger(state_dir / "ledger.jsonl", mode, identity, stamp(slots[0].cutoff.open)) as led:
        return _run(led, state_dir, data, decider, slots, mode, now, stamp, identity)


def _run(led, state_dir, data, decider, slots, mode, now, stamp, identity) -> dict:
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

    def base(slot: Slot) -> dict:
        return {"slot": slot.index, "month": slot.month, "decider": decider.name, "genuine": decider.genuine,
                "cutoff": slot.cutoff.close.isoformat(), "deadline": slot.deadline.isoformat()}

    def finish_without_request(slot: Slot, status: str, reason: str, problems: list[str], extra: dict, at: datetime):
        led.put(f"decision|{slot.index}", "decision",
                base(slot) | {"status": status, "reason": reason, "problems": problems, "decision": None} | extra,
                stamp(at))
        led.put(f"cost|{slot.index}", "cost", derive_cost(slot, decider.genuine, None, False), stamp(at))

    def finish(slot: Slot, pack: MonthPack):
        """decision|k and cost|k from the durable answer (a no-op when both exist)."""
        ans = led.get(f"answer|{slot.index}")["payload"]
        at = datetime.fromisoformat(ans["recorded_for"])
        out = derive_decision(base(slot) | {"pack_file": f"packs/slot-{slot.index:02d}.json"}, pack, ans)
        led.put(f"decision|{slot.index}", "decision", out, stamp(at))
        led.put(f"cost|{slot.index}", "cost", derive_cost(slot, decider.genuine, ans, True), stamp(at))

    def decide(slot: Slot):
        k = slot.index
        if now < slot.cutoff.close:
            return
        dec_rec = led.get(f"decision|{k}")
        if dec_rec is not None and led.get(f"cost|{k}") is not None:
            return
        pk = led.get(f"pack|{k}")
        pf = state_dir / "packs" / f"slot-{k:02d}.json"
        if dec_rec is not None and pk is None:          # decided without a request: repair the $0 charge only
            led.put(f"cost|{k}", "cost", derive_cost(slot, decider.genuine, None, False),
                    stamp(datetime.fromisoformat(dec_rec["recorded_at"])))
            return
        if pk is not None:                              # frozen: read back, never rebuilt from newer data
            pack = load_pack(pf, pk["payload"]["pack_sha256"])
        else:
            built = slot.cutoff.close + timedelta(minutes=20) if mode == "dry_run" else stamp(now)
            if built > now:                             # by this run's clock the pack is not built yet
                return
            if built >= slot.deadline:
                finish_without_request(slot, "missed_answer", "not_requested",
                                       ["no request was made before the deadline"], {}, slot.deadline)
                return
            try:
                pack = build_pack(slot, books["ai"], data, built, _prior_theses(led, books["ai"]))
            except InputsIncomplete as exc:
                if now >= slot.deadline:
                    finish_without_request(slot, "missing_input", "research_inputs", [str(exc)],
                                           {"coverage": exc.coverage}, slot.deadline)
                return
            atomic_write(pf, json.dumps(pack.record(), sort_keys=True, indent=1) + "\n")
            led.put(f"pack|{k}", "pack", {"slot": k, "pack_sha256": pack.sha256, "file": f"packs/{pf.name}",
                                          "coverage": [{x: c[x] for x in ("symbol", "status", "reason")}
                                                       for c in pack.coverage]}, stamp(pack.built_at))
        if led.get(f"answer|{k}") is None:
            req = led.get(f"request|{k}")
            if req is None:
                if not decider.ready(slot, pack, now):
                    if now < slot.deadline:
                        return
                    finish_without_request(slot, "missed_answer", "not_requested",
                                           ["no request was made before the deadline"], {}, slot.deadline)
                    return
                led.put(f"request|{k}", "request", {"slot": k, "pack_sha256": pack.sha256, "decider": decider.name,
                                                    "request_sha256": decider.request_digest(slot, pack)},
                        stamp(pack.built_at))
                d = decider.decide(slot, pack)
                if d.responded_at > now:                # by this run's clock the answer does not exist yet
                    if now < slot.deadline:
                        raise LedgerError(f"{slot.month}: the request was sent but its answer lies after this run's "
                                          "clock; run again later (it will not be resent)")
                    d = Decided(None, None, now, d.cost, "no answer was frozen by the deadline")
                if d.frozen_at is not None and d.frozen_at < pack.built_at:
                    raise LedgerError(f"{slot.month}: a decision cannot be frozen before its pack was built")
                answer = {"slot": k, "decision": d.decision, "frozen_at": d.frozen_at.isoformat() if d.frozen_at else None,
                          "cost": d.cost, "note": d.note, "lost_in_flight": False,
                          "recorded_for": (d.frozen_at or d.responded_at).isoformat()}
                led.put(f"answer|{k}", "answer", answer, stamp(d.frozen_at or d.responded_at))
            else:                                       # crashed with the request in flight: never resend
                at = max(slot.deadline, datetime.fromisoformat(req["recorded_at"]))
                led.put(f"answer|{k}", "answer",
                        {"slot": k, "decision": None, "frozen_at": None, "cost": None, "lost_in_flight": True,
                         "note": "the run stopped with this request in flight; it was never resent, and its "
                                 "charge is unknown until reconciled", "recorded_for": at.isoformat()}, stamp(at))
        finish(slot, pack)

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
        orders, plan = (stock_orders(b, opens, targets, dec_rec["payload"].get("cutoff_weights"))
                        if targets else ([], None))
        trade = settle(b, s.day, opens, orders) if (orders or b.cash >= MIN_SWEEP) else None
        after_w = resulting(plan, b, opens) if plan is not None else None
        if events or trade or slot is not None:
            led.put(f"session|{name}|{s.day}|open", "session_open",
                    {"book": name, "session": str(s.day), "actions": events, "trade": trade,
                     "decision": None if slot is None else {"slot": slot.index, "status": dec_rec["payload"]["status"]},
                     "plan": plan, "weights_after": after_w, "after": b.snapshot()}, stamp(s.open))
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
    for slot in slots:                                   # the AI portfolio stopped at a price gap before the cutoff
        if led.get(f"decision|{slot.index}") is None and now >= slot.deadline and led.get(f"pack|{slot.index}") is None:
            finish_without_request(slot, "missing_input", "portfolio_not_valued",
                                   [f"the AI portfolio could not be valued at the cutoff (stopped at "
                                    f"{halted.get('ai', 'n/a')}); no request was made"], {}, slot.deadline)
    return {"ledger": str(led.path), "records": len(led.records), "appended": led.appended, "halted": halted,
            "identity": identity}


def reconcile(state_dir: Path, slot: int, usd: str, evidence: str, at: datetime) -> dict:
    """Resolve an unreconciled charge with provider evidence, as its own append-only record."""
    path = Path(state_dir) / "ledger.jsonl"
    with InvestLedger.open_existing(path, at) as led:
        c = led.get(f"cost|{slot}")
        if c is None or c["payload"]["status"] != "unreconciled":
            raise LedgerError(f"slot {slot} has no unreconciled charge")
        if Decimal(str(usd)) < 0 or not evidence.strip():
            raise LedgerError("a reconciliation needs a non-negative amount and the provider evidence it rests on")
        rec, _ = led.put(f"reconcile|{slot}", "reconcile", {"slot": slot, "usd": str(Decimal(str(usd))),
                                                            "evidence": evidence}, at)
    return rec

"""Check a language model's explanation against numbers the app computed itself.

The model never computes anything the app relies on (stop distance, reward/risk, costs, RSI). Python
computes them, they are given to the model as facts, and its explanation is checked against them. An
explanation that contradicts the facts makes the whole answer unusable — it is rejected, not trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class Facts:
    rsi: Optional[float] = None
    stop_pct: Optional[float] = None
    target_pct: Optional[float] = None
    reward_risk: Optional[float] = None
    cost_pct: Optional[float] = None

    @classmethod
    def from_signal(cls, signal, cost_pct: Optional[float] = None) -> "Facts":
        feats = getattr(signal, "features", {}) or {}
        rsi = feats.get("rsi14", feats.get("rsi"))
        stop, target = float(signal.stop_pct), float(signal.target_pct)
        rr = (target - (cost_pct or 0)) / (stop + (cost_pct or 0)) if stop > 0 else None
        return cls(float(rsi) if rsi is not None else None, stop, target, rr, cost_pct)


def rsi_zone(rsi: Optional[float]) -> str:
    if rsi is None:
        return "unknown"
    if rsi >= 70:
        return "overbought (70 or more)"
    if rsi <= 30:
        return "oversold (30 or less)"
    return "neutral (between 30 and 70)"


def derived_facts(signal, cost_pct: Optional[float]) -> dict:
    """Pre-computed numbers handed to the model so it never has to do arithmetic."""
    f = Facts.from_signal(signal, cost_pct)
    return {"stop_distance_pct": round(f.stop_pct, 2), "target_distance_pct": round(f.target_pct, 2),
            "reward_to_risk_after_costs": round(f.reward_risk, 2) if f.reward_risk is not None else None,
            "round_trip_cost_pct": round(cost_pct, 3) if cost_pct is not None else None,
            "rsi14": round(f.rsi, 1) if f.rsi is not None else None, "rsi_zone": rsi_zone(f.rsi)}


@dataclass
class Check:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * abs(b))


def check_explanation(text: str, facts: Facts) -> Check:
    c = Check()
    t = (text or "").lower()
    if not t.strip():
        c.problems.append("no explanation")
        return c
    # RSI: zone words must match the computed value; quoted values / comparisons must hold
    if facts.rsi is not None:
        for word, wrong in (("oversold", facts.rsi > 50), ("overbought", facts.rsi < 50)):
            for m in re.finditer(word, t):
                before, after = t[max(0, m.start() - 25):m.start()], t[m.end():m.end() + 15]
                if re.search(r"\b(not|no longer|above|below|under|over|away from|out of|approach|near)\b", before) or \
                        re.search(r"^\s*(threshold|level|line|territory\?)", after):
                    continue  # "above the oversold threshold", "not overbought": not a claim that it IS
                if wrong:
                    c.problems.append(f"calls it {word} but RSI is {facts.rsi:.0f}")
                break
        for m in re.finditer(r"rsi(?:\s*\(14\))?[^0-9.%]{0,25}?" + _NUM, t):
            v = float(m.group(1))
            between = t[m.start():m.start(1)]
            if v == 14 and not re.search(r"\b(above|over|below|under|at|of|is)\b", between):
                continue  # "RSI (14)" names the indicator's period, not its value
            if re.search(r"\b(above|over|greater|more than|higher than|exceed)", between):
                ok = facts.rsi > v - 1
            elif re.search(r"\b(below|under|less than|lower than)", between):
                ok = facts.rsi < v + 1
            else:
                ok = _close(v, facts.rsi, 3.0, 0.0)
            if not ok:
                c.problems.append(f"says RSI {between.split('rsi')[-1].strip(' ,:(')} {v:g} but it is {facts.rsi:.1f}")
                break
    # stop / target distances
    for word, val in (("stop", facts.stop_pct), ("target", facts.target_pct), ("take-profit", facts.target_pct),
                      ("take profit", facts.target_pct)):
        if val is None:
            continue
        for m in re.finditer(re.escape(word) + r"[^.%$]{0,30}?" + _NUM + r"\s*%", t):
            v = float(m.group(1))
            if not _close(v, val, 0.15, 0.25):
                c.problems.append(f"says the {word} is {v:g}% but it is {val:.2f}%")
                break
    # reward/risk ratio claims like "2:1", "2 to 1", "reward/risk of 1.5"
    if facts.reward_risk is not None:
        for m in re.finditer(r"(?:reward[^.]{0,20}risk|risk[- /]?reward|ratio)[^0-9.]{0,15}?" + _NUM +
                             r"(?:\s*(?::|to)\s*" + _NUM + r")?", t):
            a = float(m.group(1))
            b = float(m.group(2)) if m.group(2) else 1.0
            claimed = a / b if b else a
            if "risk" in t[m.start():m.start() + 6] and m.group(2):  # "risk:reward 1:2" -> reward/risk 2
                claimed = b / a if a else claimed
            if not _close(claimed, facts.reward_risk, 0.35, 0.3):
                c.problems.append(f"claims reward/risk ≈{claimed:.2g} but after costs it is {facts.reward_risk:.2f}")
                break
    # an indicator dump is not reasoning
    words = re.findall(r"[a-z]+", t)
    numbers = re.findall(_NUM, t)
    connectors = re.search(r"\b(because|since|but|however|so|which|suggest|indicat|means|while|although|despite|"
                           r"therefore|given|risk|likely|unlikely)\b", t)
    if not connectors and len(numbers) >= 4 and len(numbers) >= 0.3 * max(1, len(words)):
        c.problems.append("lists indicator values without any reasoning")
    return c

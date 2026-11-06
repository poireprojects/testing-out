"""Statistics that respect how trading data is correlated: trades on the same day share the market's
moves, so uncertainty is estimated by resampling whole DAYS, not individual trades."""
from __future__ import annotations

import math
from datetime import date
from typing import Iterable, Optional, Sequence

import numpy as np


def day_block_bootstrap(values: Sequence[float], days: Sequence[date], draws: int = 5000, seed: int = 7,
                        alpha: float = 0.10) -> dict:
    """Mean per trade with a (1-alpha) CI and a one-sided p-value for mean <= 0, resampling days."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"n": 0, "days": 0, "mean": 0.0, "lo": 0.0, "hi": 0.0, "p": 1.0}
    uniq = sorted(set(days))
    idx = {d: i for i, d in enumerate(uniq)}
    g = np.array([idx[d] for d in days])
    sums = np.bincount(g, weights=v, minlength=len(uniq))
    cnts = np.bincount(g, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(uniq), size=(draws, len(uniq)))
    s, c = sums[pick].sum(axis=1), cnts[pick].sum(axis=1)
    means = np.where(c > 0, s / np.maximum(c, 1), 0.0)
    return {"n": int(len(v)), "days": len(uniq), "mean": float(v.mean()),
            "lo": float(np.quantile(means, alpha / 2)), "hi": float(np.quantile(means, 1 - alpha / 2)),
            "p": float((means <= 0).mean())}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm–Bonferroni adjusted p-values (step-down, monotone)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for k, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - k) * p))
        out[name] = running
    return out


def brier(p: Sequence[float], y: Sequence[int]) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else 0.0


def log_loss(p: Sequence[float], y: Sequence[int]) -> float:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else 0.0


def calibration(p: Sequence[float], y: Sequence[int], bins: int = 10) -> dict:
    """Expected calibration error and the reliability table (equal-width bins)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0:
        return {"ece": 0.0, "table": []}
    edges = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    table, ece = [], 0.0
    for b in range(bins):
        m = which == b
        if not m.any():
            continue
        pm, ym = float(p[m].mean()), float(y[m].mean())
        ece += m.mean() * abs(pm - ym)
        table.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()), "predicted": pm, "observed": ym})
    return {"ece": float(ece), "table": table}


def auc(score: Sequence[float], y: Sequence[int]) -> float:
    """Area under the ROC curve (rank formulation; ties get half credit)."""
    s, y = np.asarray(score, float), np.asarray(y, int)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.concatenate([pos, neg])[order]
    # average ranks for ties
    i = 0
    r = np.arange(1, len(allv) + 1, dtype=float)
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        r[i:j + 1] = (i + j + 2) / 2.0
        i = j + 1
    ranks[order] = r
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def top_share(pnls: Iterable[float], k: int = 5) -> Optional[float]:
    """Share of total profit made by the k best trades (None when total profit ≤ 0)."""
    arr = sorted(pnls, reverse=True)
    tot = sum(arr)
    if tot <= 0:
        return None
    return float(sum(arr[:k]) / tot)


def min_detectable_effect(sd: float, n_eff: float, z_alpha: float = 1.645, z_power: float = 0.84) -> float:
    """Smallest true mean a one-sided test at 5% would detect with 80% power."""
    return (z_alpha + z_power) * sd / math.sqrt(n_eff) if n_eff > 0 else float("inf")


def effective_n(values: Sequence[float], days: Sequence[date]) -> float:
    """Design-effect corrected sample size for trades clustered by day."""
    v = np.asarray(values, float)
    if len(v) < 3:
        return float(len(v))
    uniq = sorted(set(days))
    m = len(v) / len(uniq)
    by = {}
    for x, d in zip(v, days):
        by.setdefault(d, []).append(x)
    means = np.array([np.mean(by[d]) for d in uniq])
    within = np.var(v)
    between = np.var(means)
    icc = 0.0 if within <= 0 else max(0.0, min(1.0, (between - within / max(m, 1)) / within))
    return float(len(v) / (1 + (m - 1) * icc))

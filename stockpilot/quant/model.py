"""Small, interpretable, dependency-free prediction models (numpy only).

* `Standardizer` — fitted on TRAINING rows only; missing values become the training mean.
* `LogisticL2` — regularised logistic regression for P(net result > 0).
* `RidgeL2` — regularised linear regression for the expected net return (% of the position).
* `QuantModel` — both, plus a day-bootstrap ensemble for uncertainty, serialisable to JSON so the exact
  version behind every decision can be stored and restored.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np


@dataclass
class Standardizer:
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> "Standardizer":
        mu = np.nanmean(X, axis=0)
        mu = np.where(np.isfinite(mu), mu, 0.0)
        Xf = np.where(np.isfinite(X), X, mu)
        sd = Xf.std(axis=0)
        return cls(mu, np.where(sd > 1e-12, sd, 1.0))

    def transform(self, X: np.ndarray) -> np.ndarray:
        X = np.where(np.isfinite(X), X, self.mean)
        Z = (X - self.mean) / self.scale
        return np.clip(Z, -6.0, 6.0)  # one wild value can't dominate a prediction


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


@dataclass
class LogisticL2:
    lam: float = 1.0
    w: Optional[np.ndarray] = None
    b: float = 0.0

    def fit(self, Z: np.ndarray, y: np.ndarray, iters: int = 50) -> "LogisticL2":
        n, k = Z.shape
        w, b = np.zeros(k), float(np.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6)))
        for _ in range(iters):  # Newton / IRLS
            p = _sigmoid(Z @ w + b)
            r = p * (1 - p) + 1e-9
            g_w = Z.T @ (p - y) / n + self.lam * w / n
            g_b = float(np.mean(p - y))
            H = (Z.T * r) @ Z / n + np.eye(k) * self.lam / n
            Hb = (Z.T @ r) / n
            Hbb = float(r.mean())
            # solve the joint (k+1) system
            A = np.block([[H, Hb[:, None]], [Hb[None, :], np.array([[Hbb]])]])
            step = np.linalg.solve(A, np.concatenate([g_w, [g_b]]))
            w, b = w - step[:-1], b - step[-1]
            if np.max(np.abs(step)) < 1e-8:
                break
        self.w, self.b = w, b
        return self

    def predict(self, Z: np.ndarray) -> np.ndarray:
        return _sigmoid(Z @ self.w + self.b)


@dataclass
class RidgeL2:
    lam: float = 1.0
    w: Optional[np.ndarray] = None
    b: float = 0.0

    def fit(self, Z: np.ndarray, y: np.ndarray) -> "RidgeL2":
        n, k = Z.shape
        b = float(y.mean())
        A = Z.T @ Z + self.lam * np.eye(k)
        self.w = np.linalg.solve(A, Z.T @ (y - b))
        self.b = b
        return self

    def predict(self, Z: np.ndarray) -> np.ndarray:
        return Z @ self.w + self.b


@dataclass
class QuantModel:
    """P(win) + expected net return + uncertainty. Everything needed to reproduce a prediction is kept."""
    features: list[str]
    lam: float = 1.0
    std: Optional[Standardizer] = None
    clf: Optional[LogisticL2] = None
    reg: Optional[RidgeL2] = None
    ensemble: list[tuple[np.ndarray, float]] = field(default_factory=list)  # ridge (w, b) per bootstrap
    base_rate: float = 0.0
    train_rows: int = 0
    train_days: list[str] = field(default_factory=list)
    label: str = ""

    def fit(self, X: np.ndarray, ret: np.ndarray, groups: Optional[Sequence] = None, n_boot: int = 20,
            seed: int = 5) -> "QuantModel":
        y = (ret > 0).astype(float)
        self.std = Standardizer.fit(X)
        Z = self.std.transform(X)
        self.clf = LogisticL2(self.lam).fit(Z, y)
        self.reg = RidgeL2(self.lam * max(1.0, len(Z) / 1000)).fit(Z, ret)
        self.base_rate = float(y.mean())
        self.train_rows = int(len(X))
        self.ensemble = []
        if groups is not None and n_boot > 0:
            g = np.asarray(groups)
            uniq = np.unique(g)
            rng = np.random.default_rng(seed)
            for _ in range(n_boot):
                take = rng.choice(uniq, size=len(uniq), replace=True)
                rows = np.concatenate([np.nonzero(g == d)[0] for d in take])
                r = RidgeL2(self.reg.lam).fit(Z[rows], ret[rows])
                self.ensemble.append((r.w, r.b))
        return self

    def predict(self, X: np.ndarray) -> dict[str, np.ndarray]:
        Z = self.std.transform(np.atleast_2d(X))
        out = {"p_win": self.clf.predict(Z), "exp_net": self.reg.predict(Z)}
        if self.ensemble:
            preds = np.stack([Z @ w + b for w, b in self.ensemble])
            out["exp_net_sd"] = preds.std(axis=0)
        else:
            out["exp_net_sd"] = np.full(len(Z), np.nan)
        return out

    def explain(self, x: np.ndarray, top: int = 4) -> list[tuple[str, float]]:
        """Largest contributions (standardized feature × ridge weight) to the expected-net prediction."""
        z = self.std.transform(np.atleast_2d(x))[0]
        contrib = z * self.reg.w
        order = np.argsort(-np.abs(contrib))[:top]
        return [(self.features[i], float(contrib[i])) for i in order]

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {"features": self.features, "lam": self.lam, "label": self.label,
                "std": {"mean": self.std.mean.tolist(), "scale": self.std.scale.tolist()},
                "clf": {"w": self.clf.w.tolist(), "b": self.clf.b, "lam": self.clf.lam},
                "reg": {"w": self.reg.w.tolist(), "b": self.reg.b, "lam": self.reg.lam},
                "ensemble": [{"w": w.tolist(), "b": b} for w, b in self.ensemble],
                "baseRate": self.base_rate, "trainRows": self.train_rows, "trainDays": self.train_days}

    @classmethod
    def from_dict(cls, d: dict) -> "QuantModel":
        m = cls(list(d["features"]), d.get("lam", 1.0))
        m.std = Standardizer(np.array(d["std"]["mean"]), np.array(d["std"]["scale"]))
        m.clf = LogisticL2(d["clf"]["lam"], np.array(d["clf"]["w"]), d["clf"]["b"])
        m.reg = RidgeL2(d["reg"]["lam"], np.array(d["reg"]["w"]), d["reg"]["b"])
        m.ensemble = [(np.array(e["w"]), e["b"]) for e in d.get("ensemble", [])]
        m.base_rate, m.train_rows = d.get("baseRate", 0.0), d.get("trainRows", 0)
        m.train_days, m.label = d.get("trainDays", []), d.get("label", "")
        return m

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

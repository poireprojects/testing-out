"""Academic daily return series (Ken French data library) with the data-use ledger enforced.

`scripts/download_phase3.py` splits every file into dev/ and holdA/ and never writes vault rows. This loader
adds a second guard: development code only ever gets EXAMINED days; holdout A needs purpose="holdout" and
leaves a one-time marker, exactly like the intraday holdouts.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .ledger import EXAMINED, RESERVED, DataLedger, ReservedData

ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_P3_MARKER = ROOT / "research" / "HOLDOUT_P3_USED.json"
VAULT = (date(2020, 7, 27), date(2022, 10, 31))


def _read(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    df = df.mask(df <= -99.0)                       # -99.99 / -999 = missing in the library's files
    return df / 100.0                               # percent -> decimal


def load_series(data_dir: Path, key: str, purpose: str = "development", ledger: Optional[DataLedger] = None,
                marker: Path = HOLDOUT_P3_MARKER, allow_reuse: bool = False) -> pd.DataFrame:
    """Daily decimal returns. purpose='development' -> dev rows only; 'holdout' -> holdout A rows only."""
    ledger = ledger or DataLedger.load()
    if purpose == "development":
        df = _read(Path(data_dir) / "dev" / f"{key}.csv")
        bad = [d for d in df.index.date if ledger.status(d) != EXAMINED]
        if bad:
            raise ReservedData(f"{len(bad)} rows of {key} are not development days ({bad[0]}..{bad[-1]})")
        return df
    if purpose == "holdout":
        df = _read(Path(data_dir) / "holdA" / f"{key}.csv")
        if any(ledger.status(d) != RESERVED for d in df.index.date):
            raise ReservedData("holdout A rows must all be reserved days")
        _mark(marker, df, allow_reuse)
        return df
    raise ValueError(purpose)


def _mark(marker: Path, df: pd.DataFrame, allow_reuse: bool) -> None:
    if marker.exists() and not allow_reuse:
        raise ReservedData(f"phase-3 holdout A was already used ({marker.read_text()[:200]})")
    if not marker.exists():
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except Exception:
            commit = ""
        marker.write_text(json.dumps({"firstUsed": datetime.now(timezone.utc).isoformat(), "commit": commit,
                                      "days": [str(df.index[0].date()), str(df.index[-1].date())]}, indent=1) + "\n")


def segments(idx: pd.DatetimeIndex, max_gap_days: int = 40) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Contiguous stretches of a series (development data has a hole where holdout A and the vault are)."""
    out, start = [], idx[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if (b - a).days > max_gap_days:
            out.append((start, a))
            start = b
    out.append((start, idx[-1]))
    return out


def price_index(returns: pd.DataFrame) -> pd.DataFrame:
    """Cumulative total-return index (1.0 at the start); NaN returns keep the previous level."""
    return (1.0 + returns.fillna(0.0)).cumprod()


def market_total_return(ff3: pd.DataFrame) -> pd.Series:
    return (ff3["Mkt-RF"] + ff3["RF"]).rename("market")


def annualize(daily: pd.Series) -> dict:
    """CAGR, volatility, Sharpe (excess of nothing — callers subtract the risk-free rate first if wanted)."""
    daily = daily.dropna()
    if daily.empty:
        return {"days": 0}
    n = len(daily)
    growth = float((1.0 + daily).prod())
    eq = (1.0 + daily).cumprod()
    dd = float((eq / eq.cummax() - 1.0).min())
    sd = float(daily.std(ddof=1)) if n > 1 else 0.0
    return {"days": n, "cagrPct": (growth ** (252.0 / n) - 1.0) * 100.0, "volPct": sd * np.sqrt(252) * 100.0,
            "sharpe": float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0, "maxDrawdownPct": dd * 100.0}

"""Freeze the build 20 research baseline so it can be reproduced and never silently overwritten.

    python scripts/freeze_baseline.py REALHIST_DIR [--model PATH_TO_GGUF]

REALHIST_DIR is the folder the build 20 study ran in (backtest.json, ai_modes.json, ai_replay.db, cache/).
Writes research/baselines/build20/: MANIFEST.json (commit, source and result hashes, parameters, splits,
data fingerprints, model identity), ai_decisions.jsonl (every recorded model answer with its timestamp and
latency) and data_fingerprints.csv (one SHA-256 per symbol-day of bars). Raw market data is NOT copied into
the repository (Alpaca's market-data terms don't allow redistributing it); the fingerprints prove a
re-download is the same data.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pickle
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUILD20_COMMIT = "a5a4f988e3a255a63c30ab1ff486bc68933ac752"
INSTALLER_SHA256 = "87ec4539dba1dfc6b670117c8566f743d82d904c021c3a04680951c33914da90"
SOURCES = ["dkapp/backtest/engine.py", "dkapp/backtest/runner.py", "dkapp/backtest/ai_replay.py",
           "dkapp/risk/fees.py", "dkapp/decision.py", "dkapp/strategies/library.py", "dkapp/strategies/base.py",
           "dkapp/analysis/features.py", "dkapp/analysis/indicators.py", "dkapp/ai/engine.py", "dkapp/ai/schema.py",
           "dkapp/config.py", "scripts/real_history_report.py", "scripts/ai_modes_from_recorded.py"]
RESULTS = ["REPORT.md", "LIVE_SESSION.md", "REVIEW_FIXES.md", "backtest.json", "ai_modes.json"]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def git_show(rev: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, check=True, capture_output=True).stdout


def bars_fingerprint(df) -> str:
    """Stable hash of the OHLCV values and timestamps (independent of pickle/pandas versions)."""
    cols = [c for c in ("open", "high", "low", "close", "volume", "vwap") if c in df.columns]
    lines = [",".join([ts.isoformat()] + [repr(float(v)) for v in row])
             for ts, row in zip(df.index, df[cols].itertuples(index=False, name=None))]
    return sha256_bytes(("|".join(cols) + "\n" + "\n".join(lines)).encode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("realhist")
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    src = Path(a.realhist)
    out = ROOT / "research" / "baselines" / "build20"
    out.mkdir(parents=True, exist_ok=True)

    from dkapp.backtest.engine import BTConfig
    from dkapp.backtest.runner import DEFAULT_BACKTEST_SYMBOLS
    bt = json.loads((src / "backtest.json").read_text())
    cfg = BTConfig.baseline_build20()

    # ---- data fingerprints (one per symbol-day, sorted)
    rows = []
    for p in sorted((src / "cache").glob("*.pkl")):
        sym, day = p.stem.rsplit("_", 1)
        with open(p, "rb") as f:  # our own cache files from the build 20 run
            df = pickle.load(f)
        rows.append((sym, day, len(df), bars_fingerprint(df) if len(df) else ""))
    (out / "data_fingerprints.csv").write_text("symbol,day,bars,sha256\n" +
                                               "\n".join(f"{s},{d},{n},{h}" for s, d, n, h in rows) + "\n")

    # ---- every recorded AI answer, with timing (prompts were anonymised; nothing secret in here)
    con = sqlite3.connect(f"file:{src / 'ai_replay.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    dec = [dict(r) for r in con.execute("SELECT * FROM ai_decisions ORDER BY id")]
    with open(out / "ai_decisions.jsonl", "w") as f:
        for r in dec:
            f.write(json.dumps(r) + "\n")

    results_dir = ROOT / "docs" / "results" / "2026-11-06"
    manifest = {
        "frozen": "build 20 baseline — never edit; new research goes in new versioned folders",
        "commit": BUILD20_COMMIT,
        "release": {"tag": "build-20", "installer": "dontknowwhatimdoing-windows-setup.exe",
                    "installerSha256": INSTALLER_SHA256},
        "sourcesAtCommit": {p: sha256_bytes(git_show(BUILD20_COMMIT, p)) for p in SOURCES},
        "results": {f"docs/results/2026-11-06/{n}": sha256_file(results_dir / n) for n in RESULTS
                    if (results_dir / n).exists()},
        "backtestConfig": {k: (dataclasses.asdict(v) if dataclasses.is_dataclass(v) else v)
                           for k, v in dataclasses.asdict(cfg).items()},
        "costModelNote": ("build 20 rounded the SEC fee and the FINRA TAF up to $0.01 on EVERY sale "
                          "(SEC $27.80/M, TAF $0.000166/share) — reproduced by CostModel.legacy_build20()"),
        "data": {"source": "Alpaca market data v2, IEX feed (one exchange, a few % of U.S. volume)",
                 "bars": "1-minute", "symbols": DEFAULT_BACKTEST_SYMBOLS[:40],
                 "studyDays": [bt["firstDay"], bt["lastDay"]], "tradingDays": bt["days"],
                 "warmupDaysBefore": "prior-day baselines use the days before the first study day",
                 "symbolDays": len(rows), "coverage": bt["coverage"]},
        "splits": bt["splits"],
        "strategies": {sid: {"name": s["name"], "verdict": s["verdict"], "testAvgRetPct": s["test"]["avgRetPct"],
                             "testTrades": s["test"]["trades"], "allTrades": s["all"]["trades"],
                             "allAvgRetPct": s["all"]["avgRetPct"]} for sid, s in bt["strategies"].items()},
        "approachesTest": {k: {"trades": v["trades"], "totalPnl": v["totalPnl"], "avgRetPct": v["avgRetPct"]}
                           for k, v in bt["approaches"].items()},
        "benchmarks": bt["benchmarks"],
        "ai": {"model": "Llama-3.2-3B-Instruct-Q4_K_M.gguf (bartowski build), llama.cpp via llama-cpp-python 0.3.16",
               "modelSha256": sha256_file(Path(a.model)) if a.model else None,
               "temperature": 0.1, "seedOfShim": 11, "promptsAnonymised": True,
               "recordedAnswers": len(dec), "firstAnswerAt": dec[0]["time"] if dec else None,
               "lastAnswerAt": dec[-1]["time"] if dec else None},
        "paperSession": {"notes": "docs/results/2026-11-06/LIVE_SESSION.md", "date": "2026-11-06",
                         "integrationTests": "tests/test_alpaca_integration.py — 8 passed on the real paper account"},
        "dataUse": "see research/ledger.json — every study day here counts as EXAMINED from now on",
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, default=str) + "\n")
    print("wrote", out, len(rows), "symbol-days,", len(dec), "AI answers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

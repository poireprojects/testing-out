# Research

* `baselines/build20/` — the frozen build 20 study (commit a5a4f98): manifest with source/result hashes,
  parameters, splits and model identity, every recorded AI answer, and a SHA-256 per symbol-day of bars.
  `python scripts/reproduce_baseline.py REALHIST_DIR` re-runs it with `BTConfig.baseline_build20()` and
  fails unless every number matches `docs/results/2026-11-06/backtest.json`.
* `ledger.json` — which market days have already influenced development (`dkapp/research/ledger.py`).
  Every day of the build 20 study is EXAMINED: it can be used for development, never called unseen.
* `COSTS.md` — verified fees and the economics of small accounts.
* `runs/` — later research runs, each in its own dated folder. Nothing here is overwritten.

Raw market data is not committed (Alpaca's market-data terms don't allow redistribution); the
fingerprints let anyone check that a re-download is identical.

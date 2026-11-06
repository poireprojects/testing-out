# Real-history backtest — 2026-09-11 → 2026-11-05

40 trading days · 40 symbols · Alpaca IEX 1-minute bars · $20 positions · spread 3.0 bps + slippage 5.0 bps each way + SEC/FINRA fees. Generated 2026-11-06 16:06 UTC.

Data coverage: 1600 of 1600 symbol-days (missing ones skipped, never filled in). Splits: train 2026-09-11 → 2026-10-08, validation 2026-10-09 → 2026-10-22, unseen test 2026-10-23 → 2026-11-05.

## Each strategy, after costs (average return per trade)

| strategy | test trades | train | validation | unseen test | random entries | 2× slippage | stable periods | adj. p | verdict |
|---|---|---|---|---|---|---|---|---|---|
| intraday momentum | 123 | -0.207% | -0.293% | -0.214% | -0.170% | -0.314% | 0/4 | 1.00 | **no_edge** |
| trend following | 245 | -0.260% | -0.224% | -0.319% | -0.261% | -0.418% | 0/4 | 1.00 | **no_edge** |
| opening-range breakout | 82 | -0.307% | -0.190% | -0.339% | -0.324% | -0.439% | 0/4 | 1.00 | **no_edge** |
| VWAP reclaim | 272 | -0.243% | -0.254% | -0.244% | -0.263% | -0.343% | 0/4 | 1.00 | **no_edge** |
| volume-confirmed breakout | 139 | -0.214% | -0.168% | -0.283% | -0.182% | -0.383% | 0/4 | 1.00 | **no_edge** |

### Before vs after costs (all 40 days)

A $20 round trip costs about **0.23%**: half the spread each side, 5 bps slippage each side, and SEC/FINRA fees rounded up to the cent ($0.02 per sale — a big share of a small trade).

| strategy | trades | before costs | after costs | closed by its own exit rule |
|---|---|---|---|---|
| intraday momentum | 285 | -0.000% | -0.230% | 94% |
| trend following | 705 | -0.042% | -0.271% | 91% |
| opening-range breakout | 302 | -0.052% | -0.282% | 55% |
| VWAP reclaim | 1237 | -0.015% | -0.245% | 81% |
| volume-confirmed breakout | 466 | +0.008% | -0.222% | 97% |
| random entries (same stops/targets) | 8985 | +0.042% | -0.188% | 0% |

Why:

* **intraday momentum** — lost money on the training period after costs; lost money on the validation period after costs; lost money on the unseen test period after costs; test profit factor 0.35 is below 1.15; could easily be luck once you account for testing several strategies (adjusted p=1.00); did not beat random entries with the same stops and targets; only 0 of 4 walk-forward periods were profitable (unstable); turns unprofitable if slippage doubles (too fragile to costs)
* **trend following** — lost money on the training period after costs; lost money on the validation period after costs; lost money on the unseen test period after costs; test profit factor 0.26 is below 1.15; could easily be luck once you account for testing several strategies (adjusted p=1.00); did not beat random entries with the same stops and targets; only 0 of 4 walk-forward periods were profitable (unstable); turns unprofitable if slippage doubles (too fragile to costs)
* **opening-range breakout** — lost money on the training period after costs; lost money on the validation period after costs; lost money on the unseen test period after costs; test profit factor 0.18 is below 1.15; could easily be luck once you account for testing several strategies (adjusted p=1.00); did not beat random entries with the same stops and targets; only 0 of 4 walk-forward periods were profitable (unstable); turns unprofitable if slippage doubles (too fragile to costs)
* **VWAP reclaim** — lost money on the training period after costs; lost money on the validation period after costs; lost money on the unseen test period after costs; test profit factor 0.26 is below 1.15; could easily be luck once you account for testing several strategies (adjusted p=1.00); only 0 of 4 walk-forward periods were profitable (unstable); turns unprofitable if slippage doubles (too fragile to costs)
* **volume-confirmed breakout** — lost money on the training period after costs; lost money on the validation period after costs; lost money on the unseen test period after costs; test profit factor 0.11 is below 1.15; could easily be luck once you account for testing several strategies (adjusted p=1.00); did not beat random entries with the same stops and targets; only 0 of 4 walk-forward periods were profitable (unstable); turns unprofitable if slippage doubles (too fragile to costs)

## The whole app on the unseen test period (2 positions, $20 each)

| approach | trades | win rate | avg/trade | total $ | worst drop $ |
|---|---|---|---|---|---|
| every signal (unlimited money) | 861 | 17% | -0.276% | -47.55 | 47.55 |
| first come, first served (2 slots) | 190 | 16% | -0.282% | -10.73 | 10.73 |
| ranked — what the app does (2 slots) | 26 | 27% | -0.494% | -2.57 | 2.57 |
| random entries (same stops/targets) | 2583 | 29% | -0.242% | -125.00 | 125.00 |

Benchmarks over the test days: SPY buy & hold -3.48%, SPY open→close average -0.418% per day, doing nothing $0.00.

## AI decision modes vs rules-only (same candidates, same slots, same costs)

Model: Llama-3.2-3B-Instruct-Q4_K_M via llama.cpp (local) · cost $0.00 · prompts anonymised (no tickers, no dates, prices rescaled) so the model can't recall what happened.

| mode | trades | win rate | avg/trade | total $ | worst drop $ |
|---|---|---|---|---|---|
| rules + ranker (no AI) | 72 | 31% | -0.230% | -3.31 | 3.31 |
| ai confirms each pick | 72 | 31% | -0.230% | -3.31 | 3.31 |
| ai picks one of the top 3 (or none) | 69 | 33% | -0.185% | -2.55 | 2.55 |
| rules + ranker on the same 39 days as "ai picks" | 71 | 31% | -0.213% | -3.02 | 3.02 |

In "ai picks" mode the AI chose the ranker's own top pick in 69 of 69 decisions and never chose "none" (4 decisions had more than one option). Any difference from rules-only comes from trades it didn't take (unusable answers, timing), not from judgment.

_Note: rebuilt from recorded answers; the run was stopped before the extra random sample; "ai picks" covers 2026-09-11 → 2026-11-04._

Does its yes/no separate good setups from bad ones? Asked about 72 candidates (every ranked pick): it approved 72 (avg -0.230% per trade after costs) and turned down none. Every rules signal averaged -0.250%.

AI calls: confirm 72 (0 unusable answers, 53 s each on this 4-core machine) · pick 69 (0 unusable, 20 s each).

## Caveats

* Past results don't predict future ones; the test period is short. Every verdict uses the same gate the app uses before it would allow real money.
* IEX is a single exchange (a few % of U.S. volume): volumes are relative, and fills in the backtest assume the next bar's open plus spread and slippage.
* The AI result is for this one local model on this one period. A larger or cloud model could behave differently; the app keeps measuring it going forward with shadow trades.

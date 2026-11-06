# Pre-registration — research round 1 (2026-11-06)

Written and committed **before** any result for these hypotheses was computed. Parameters below are
fixed; they are not tuned on any data. Anything changed later becomes a new hypothesis in a new
pre-registration and counts as another test.

## Data and its status

* 40 trading days, 2026-09-11 → 2026-11-05, 40 symbols, Alpaca IEX 1-minute bars (frozen baseline,
  fingerprints in `baselines/build20/`). Every day is **examined** (ledger): results here are
  development evidence only, never "unseen".
* No new history could be downloaded (no rotated paper keys). The only untouched evidence will come
  from forward shadow/paper observation from 2026-11-09 (`ledger.json: forwardFrom`).

## Costs (all tests)

`BTConfig()` as committed with this file: dated Alpaca fee schedule with daily rounding, 3 bps
quoted spread, 5 bps slippage per side, buy at the open of the bar after the signal, stop-first
inside ambiguous bars, gaps fill at the open, targets fill only when traded through by 1 bp, flat 10
minutes before the close, $20 positions, at most 2 at a time, $100 account. Stress: 2× slippage.

## Hypotheses (long only, intraday)

**H1 — intraday mean reversion.** Mechanism: short-horizon overreaction / liquidity provision.
Entry at the close of a bar between 10:00 and 15:00 ET when: price ≥ 1.0% below session VWAP,
30-minute return ≤ −1.0%, RSI(14) ≤ 30, SPY down less than 1.0% since the open. Stop
max(1.5×ATR(14), 0.4%) below; exit at the first close above EMA(20), else after 45 minutes; no target.

**H2 — relative strength continuation, low turnover.** Mechanism: stocks "in play" keep moving.
Once a day at 10:30 ET: among the 36 non-index-ETF symbols, buy the two with the highest return since
the open minus SPY's, if that excess is ≥ +1.0% and price is above VWAP. Stop 2.0% below; hold to the
flatten time.

**H3 — market intraday momentum (Gao, Han, Li & Zhou, 2018).** Mechanism: late-day trading by
hedgers/rebalancers continues the morning's direction. Buy SPY at 15:30 ET if SPY's return from the
previous close to 10:00 ET is > 0; sell at the flatten time (15:50). Expected sample ≤ 40 trades:
pre-declared **insufficient** unless it reaches the minimum below.

**H4 — statistical model on a dense sample.** Every symbol every 30 minutes from 10:00 to 15:00 ET
(no overlapping holds per symbol). Label: net return of buying at the next bar's open and selling 30
minutes later (or at flatten), after costs. Features (all known at the decision bar): 5/15/30-minute
returns, distance from VWAP, RSI, relative volume, last-bar volume ratio, 30-minute realized
volatility, change since open and since the prior close, return minus SPY's, SPY above VWAP, SPY
change since open, breadth (share of the 40 symbols up since the open), minute of the session.
Models: L2-regularised logistic regression (P[net > 0]) and ridge regression (expected net return),
λ = 1.0 on standardized features, standardization fitted on training days only. Walk-forward:
expanding window by day, first test day after 15 training days, 1-day embargo. Decision rule: at each
decision time take at most the free slots, only candidates with predicted expected net > 0 **and**
P[net > 0] > 0.5, best expected net first. Baselines on the same slots: no trade, random pick, the
highest 30-minute return.

**H5 — statistical filter on the existing strategies' signals.** Same features plus strategy,
stop/target distance and number of agreeing strategies; label = the strategy trade's own net
result; same models, walk-forward and decision rule, replayed through the app's slots and ranker.

**H6 — language-model review.** The local Llama 3.2 3B (recorded build 20 answers) as a filter on the
rules (already measured), and on whatever H4/H5 select if any; measured on numerical consistency,
approval rate, latency and incremental value.

## Primary metric and multiple comparisons

Average net return per trade of the $100-account replay (2 slots), with a one-sided p-value from a
day-block bootstrap (resampling whole days, 5,000 draws). Holm–Bonferroni across H1–H5 at α = 0.05.

## Go / no-go (a candidate is rejected if ANY holds)

1. average net return per trade ≤ 0, or its 90% day-block CI includes 0 after Holm adjustment;
2. fewer than 100 trades (→ "insufficient", not "rejected");
3. fewer than 60% of walk-forward folds positive (4 folds);
4. the 5 best trades make more than 50% of total profit;
5. negative with 2× slippage;
6. not better than the best simple baseline on the same slots;
7. models only: Brier score not better than predicting the training base rate, or expected
   calibration error > 0.05;
8. LLM only: median decision latency > 30 s (the signal is stale by then) or ≥ 5% numerically
   inconsistent answers.

## What passing would and wouldn't mean

Passing on examined data makes a candidate **eligible for forward shadow/paper testing only**.
Live eligibility needs forward evidence (≥ 100 forward trades over ≥ 30 forward trading days,
positive after costs with Holm-adjusted p < 0.05), operational checks, and the owner's explicit
approval. Nothing is promoted automatically.

## Amendment A (2026-11-06, before any download of the new history)

The owner allowed the old paper keys for read-only market data, so more history is downloaded:
40 symbols, IEX 1-minute bars, 2025-09-02 → 2026-08-26. Registered in `ledger.json` **before**
downloading:

* **Development**: 2025-09-02 → 2026-04-30 (first 10 sessions are baseline warm-up) plus the already
  examined 2026-09-11 → 2026-11-05. Walk-forward evaluation and the go/no-go criteria above run here.
* **Reserved holdout**: 2026-05-01 → 2026-08-26. Evaluated exactly once, after all rules, models
  and thresholds are frozen and committed, for **every** pre-registered hypothesis (not only the ones
  that pass development). Models for the holdout are trained only on development days before
  2026-05-01 (1-day embargo). The holdout verdict is the one reported; development numbers are
  supporting evidence.
* Fee schedule for 2025 days: SEC $0.00/M (since 2025-05-14), TAF $0.000166/share (max $8.30).

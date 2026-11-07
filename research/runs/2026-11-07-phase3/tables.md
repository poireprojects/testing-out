# Phase 3 tables (generated)

Regenerate: `python scripts/research_report_phase3.py research/runs/2026-11-07-phase3 P3_DATA_DIR`

## Factor review (development data; IC = rank correlation with the next period's return)

| universe · period | factor | horizon | months/weeks | mean IC | t | top − all per period |
|---|---|---|---:|---:|---:|---:|
| 49 industries · 1926-2005 | mom_12_1 | 21d | 939 | +0.074 | +7.57 | +0.395% |
| 49 industries · 1926-2005 | mom_6_1 | 21d | 939 | +0.047 | +5.02 | +0.196% |
| 49 industries · 1926-2005 | mom_1m | 21d | 939 | +0.044 | +5.07 | +0.176% |
| 49 industries · 1926-2005 | vol_63 (low first) | 21d | 939 | +0.020 | +2.27 | -0.082% |
| 49 industries · 1926-2005 | trend_200 | 21d | 939 | +0.068 | +6.96 | +0.293% |
| 49 industries · 1926-2005 | mom_12_1 | 63d | 937 | +0.115 | +6.85 | +1.266% |
| 49 industries · 1926-2005 | mom_6_1 | 63d | 937 | +0.077 | +4.74 | +0.610% |
| 49 industries · 1926-2005 | mom_1m | 63d | 937 | +0.047 | +3.14 | +0.303% |
| 49 industries · 1926-2005 | vol_63 (low first) | 63d | 937 | +0.012 | +0.74 | -0.445% |
| 49 industries · 1926-2005 | trend_200 | 63d | 937 | +0.099 | +5.99 | +0.783% |
| 49 industries · 1926-2005 | rev_1w (losers first) | 5d | 4086 | +0.013 | +3.33 | +0.070% |
| 49 industries · 2022-2026 | mom_12_1 | 21d | 32 | +0.025 | +0.53 | -0.002% |
| 49 industries · 2022-2026 | mom_6_1 | 21d | 32 | -0.045 | -0.96 | -0.587% |
| 49 industries · 2022-2026 | mom_1m | 21d | 32 | +0.013 | +0.34 | +0.521% |
| 49 industries · 2022-2026 | vol_63 (low first) | 21d | 32 | -0.114 | -2.07 | -1.020% |
| 49 industries · 2022-2026 | trend_200 | 21d | 32 | -0.016 | -0.37 | -0.031% |
| 49 industries · 2022-2026 | mom_12_1 | 63d | 31 | +0.025 | +0.36 | +0.064% |
| 49 industries · 2022-2026 | mom_6_1 | 63d | 31 | -0.021 | -0.30 | -0.789% |
| 49 industries · 2022-2026 | mom_1m | 63d | 31 | -0.046 | -0.59 | -0.358% |
| 49 industries · 2022-2026 | vol_63 (low first) | 63d | 31 | -0.120 | -1.19 | -2.207% |
| 49 industries · 2022-2026 | trend_200 | 63d | 31 | -0.035 | -0.51 | -0.427% |
| 49 industries · 2022-2026 | rev_1w (losers first) | 5d | 143 | -0.016 | -0.80 | -0.097% |
| app's 505 stocks · 2022-2026 (survivors) | mom_12_1 | 21d | 34 | +0.024 | +0.70 | +0.779% |
| app's 505 stocks · 2022-2026 (survivors) | mom_1m | 21d | 34 | +0.032 | +1.19 | +0.720% |
| app's 505 stocks · 2022-2026 (survivors) | vol_63 (low first) | 21d | 34 | -0.032 | -0.91 | -0.765% |
| app's 505 stocks · 2022-2026 (survivors) | trend_200 | 21d | 34 | +0.017 | +0.56 | +0.548% |
| app's 505 stocks · 2022-2026 (survivors) | liquidity (log $vol) | 21d | 34 | +0.023 | +1.14 | +0.391% |
| app's 505 stocks · 2022-2026 (survivors) | rev_1w (losers first) | 5d | 148 | -0.006 | -0.45 | -0.019% |

Long-only top 10 of 49 industries vs equal weight (1926–2005): gross +4.74%/yr, turnover 22%/month, cost 0.34%/yr, net +4.40%/yr.
alphalens cross-check (12-1 momentum, 21 days, same timing): alphalens IC +0.0764, ours +0.0764.

## Development verdicts (pre-registered)

| hypothesis | pass | reasons |
|---|---|---|
| P3-H1 | no | deflated Sharpe 0.00 <= 0.95 |
| P3-H2 | yes | — |

Trials counted for the deflated Sharpe ratio: 44 (review 36 + 8 strategy runs).

## Results by period

| | period | n | primary (net) | 90% range | p | p (12-mo blocks) | 2× costs | positive decades / 3-yr blocks | top-5 share |
|---|---|---:|---:|---|---:|---:|---:|---|---:|
| H1 vs equal weight (per month) | 1927–2005 | 943 | +0.130% | +0.01% … +0.25% | 0.031 | 0.020 | +0.102% | 6/8 · 19/27 | 0.34 |
| H1 vs equal weight (per month) | 2023–2026 | 34 | +0.127% | -0.68% … +0.98% | 0.409 | 0.427 | +0.101% | 1/1 · 2/2 | 5.98 |
| H2 per window (cash 0%) | 1926–2005 | 953 | +0.516% | +0.41% … +0.62% | 0.000 | 0.000 | +0.385% | 8/8 · 26/27 | 0.08 |
| H2 per window (cash 0%) | 2022–2026 | 46 | +0.212% | -0.23% … +0.65% | 0.211 | 0.531 | +0.082% | 1/1 · 2/2 | 1.80 |
| H2 per window (cash 0%) · HOLDOUT A | 2006–2020 | 174 | +0.038% | -0.24% … +0.31% | 0.403 | 0.434 | -0.092% | 2/3 · 4/5 | 4.30 |

## Strategy vs benchmarks (net, $10,000, standard costs)

| | period | CAGR | volatility | Sharpe (vs T-bills) | max drawdown | exposure | turnover/yr | worst 2 days |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| H1: H1 | 1927–2005 | +11.69% | 17.3% | 0.53 | -80.9% | 1.00 | 5.2 | -28.7% |
| H1: equalWeight | 1927–2005 | +10.19% | 16.1% | 0.47 | -83.7% | 1.00 | 0.3 | -22.9% |
| H1: market | 1927–2005 | +9.32% | 16.5% | 0.42 | -84.1% | nan | nan | -21.5% |
| H1: cash | 1927–2005 | +3.39% | 0.2% | 16.47 | 0.0% | nan | nan | 0.0% |
| H1: H1 | 2023–2026 | +16.32% | 19.4% | 0.64 | -23.0% | 1.00 | 5.7 | -11.1% |
| H1: equalWeight | 2023–2026 | +15.42% | 13.2% | 0.80 | -16.5% | 1.00 | 0.7 | -9.9% |
| H1: market | 2023–2026 | +21.45% | 15.6% | 1.03 | -19.6% | nan | nan | -10.7% |
| H1: cash | 2023–2026 | +4.70% | 0.1% | 75.86 | 0.0% | nan | nan | 0.0% |
| H2: H2 (cash 0%) | 1926–2005 | +5.78% | 6.7% | 0.37 | -24.7% | 0.18 | 22.8 | -13.5% |
| H2: H2 (cash at T-bill) | 1926–2005 | +8.67% | 6.7% | 0.77 | -24.0% | 0.18 | 22.8 | -13.5% |
| H2: market | 1926–2005 | +9.46% | 16.4% | 0.43 | -84.1% | nan | nan | -21.5% |
| H2: H2 (cash 0%) | 2022–2026 | +2.34% | 6.9% | -0.31 | -11.2% | 0.19 | 23.6 | -5.2% |
| H2: H2 (cash at T-bill) | 2022–2026 | +6.28% | 6.9% | 0.24 | -9.9% | 0.19 | 23.6 | -5.2% |
| H2: market | 2022–2026 | +20.67% | 15.7% | 0.98 | -19.6% | nan | nan | -10.7% |
| H2 HOLDOUT A: H2 (cash 0%) | 2006–2020 | +0.17% | 8.4% | -0.08 | -22.3% | 0.19 | 23.9 | -8.0% |
| H2 HOLDOUT A: H2 (cash at T-bill) | 2006–2020 | +1.14% | 8.4% | 0.04 | -20.1% | 0.19 | 23.9 | -8.0% |
| H2 HOLDOUT A: market | 2006–2020 | +9.21% | 20.5% | 0.48 | -54.6% | nan | nan | -14.2% |

## Capital scenarios (CAGR, cost per year)

| | period | $100 | $500 | $1,000 | $5,000 | $10,000 | $100 T+1 | $10,000 T+1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| H1 | 1927–2005 | +11.62% (0.40%) | +11.67% (0.36%) | +11.68% (0.35%) | +11.69% (0.34%) | +11.69% (0.34%) | +11.28% (0.42%) | +11.36% (0.34%) |
| H2 | 1926–2005 | +5.69% (1.58%) | +5.76% (1.52%) | +5.77% (1.51%) | +5.77% (1.51%) | +5.78% (1.51%) |
| H2 HOLDOUT A | 2006–2020 | -0.31% (2.07%) | +0.09% (1.67%) | +0.13% (1.62%) | +0.17% (1.59%) | +0.17% (1.59%) |

## Regimes (primary metric, % per month/window)

| | period | up years | down years | calm months | normal | volatile |
|---|---|---:|---:|---:|---:|---:|
| H1 | 1927–2005 | +0.151% | +0.073% | +0.440% | +0.077% | -0.128% |
| H2 | 1926–2005 | +0.630% | +0.197% | +0.313% | +0.479% | +0.755% |
| H2 HOLDOUT A | 2006–2020 | +0.095% | -0.317% | -0.142% | -0.116% | +0.372% |

## H1 extras (development)

* Excess over the market (not just equal weight): +0.199%/month, p=0.002.
* Random top-3 picks: H1's monthly picks ranked at the 54% percentile on average (gross +1.165% vs +1.006%/month).
* Sensitivity variants (counted as trials, not chosen): top2 +0.221% (p=0.007); top4 +0.153% (p=0.006); 6-1 +0.067% (p=0.161); 12-0 +0.274% (p=0.000)
* Deflated Sharpe: 0.002 (per-month Sharpe 0.059 vs threshold 0.156).

## H2 extras

* TOM-day vs other-day mean excess: development 14.8 vs 0.2 bps/day; holdout A 3.95 vs 3.89; 2022–26 6.8 vs 5.9.
* Random 4-day windows (gross excess per window): development +0.052% vs TOM +0.593%; holdout A +0.142% vs TOM +0.152%.
* Realistic SPY costs (0.5 bps spread, 1 bp slippage): development +0.622%, holdout A +0.143% (p=0.197).
* Deflated Sharpe (development): 0.997.

## Machine learning (H2-ML, development walk-forward 1946–2005)

* Windows tested: 719; model held 100% of them.
* Net per month: all windows +0.478%, model +0.478%; improvement p=1.000.
* Directional skill: AUC 0.522; Brier 0.2304 vs base rate 0.2293.
* Kept: False.

## Tradable replication on Alpaca ETF bars (2022-11 → 2026-11, price only, $1,000)

* H1 on 11 SPDR sectors from 2023-12-01: 36 months, excess over equal weight +0.172%/month (p=0.33); CAGR +15.4% vs equal weight +13.5% vs SPY +17.5%.
* H2 on SPY: 48 windows, +0.215%/window (p=0.19); CAGR +2.4% vs SPY +18.3%.

## Holdout A verdict

* **P3-H2: rejected** — primary +0.038%/month, Holm p=0.403; negative with 2x costs; Sharpe -0.08 below half of buy-and-hold's 0.48

# Phase 2 tables (generated)

Regenerate: `python scripts/research_report_phase2.py research/runs/2026-11-07-phase2 DEV_CANDIDATES_PKL`

## Verdicts (pre-registered rules, Holm across H1–H5)

| hypothesis | dev trades | dev net/trade | holdout trades | holdout net/trade | holdout gross | 90% range | p | Holm p | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| P2-H1 | 677 | -0.169% | 1180 | -0.187% | -0.016% | -0.26% … -0.11% | 1.000 | 1.000 | **rejected** |
| P2-H2 | 194 | -0.032% | 278 | -0.184% | +0.092% | -0.47% … +0.11% | 0.850 | 1.000 | **rejected** |
| P2-H3 | 657 | -0.182% | 1135 | -0.158% | -0.052% | -0.23% … -0.08% | 0.999 | 1.000 | **rejected** |
| P2-H4 | 1188 | -0.065% | 1996 | -0.078% | +0.052% | -0.13% … -0.03% | 0.995 | 1.000 | **rejected** |
| P2-H5 | 198 | +0.014% | 500 | +0.060% | — | +0.01% … +0.11% | 0.025 | 0.123 | **rejected** |

Reasons (holdout):

* **P2-H1** — loses -0.187% per trade after costs; could be luck after testing several ideas (Holm-adjusted p=1.00); profitable in only 0 of 4 periods; negative if slippage doubles; not better than the best simple baseline (+0.000% per trade)
* **P2-H2** — loses -0.184% per trade after costs; could be luck after testing several ideas (Holm-adjusted p=1.00); profitable in only 2 of 4 periods; negative if slippage doubles; not better than the best simple baseline (+0.000% per trade); negative with a 20 bps spread
* **P2-H3** — loses -0.158% per trade after costs; could be luck after testing several ideas (Holm-adjusted p=1.00); profitable in only 0 of 4 periods; negative if slippage doubles; not better than the best simple baseline (+0.000% per trade)
* **P2-H4** — loses -0.078% per trade after costs; could be luck after testing several ideas (Holm-adjusted p=1.00); profitable in only 0 of 4 periods; negative if slippage doubles; not better than the best simple baseline (+0.000% per trade)
* **P2-H5** — could be luck after testing several ideas (Holm-adjusted p=0.12)

## Baselines

| baseline | dev trades | dev net | holdout trades | holdout net | holdout gross |
|---|---:|---:|---:|---:|---:|
| H1-random | 624 | +0.099% | 1125 | -0.002% | +0.171% |
| H2-at-9:35 | 234 | +0.048% | 349 | -0.124% | +0.146% |

## Robustness (holdout)

| | positive quarters | top-5 share of profit | slippage ×2 | other stress | max drawdown $ | Sharpe |
|---|---:|---:|---:|---|---:|---:|
| P2-H1 | 0/4 | — | -0.246% |  | 45.45 | -2.95 |
| P2-H2 | 2/4 | — | -0.208% | 20 bps spread -0.208% | 15.63 | -0.74 |
| P2-H3 | 0/4 | — | -0.167% |  | 36.50 | -2.39 |
| P2-H4 | 0/4 | — | -0.178% | tight costs +0.022% | 31.35 | — |
| H2-at-9:35 | 2/4 | — | -0.154% |  | 17.66 | -0.60 |

## Opportunity profiles (every candidate, before slot limits)

| | period | n | gross | cost | net | avg MFE | avg MAE | reach +0.5% | +1% | +2% | capture | win rate | avg hold |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P2-H1 | dev | 1774 | +0.032% | 0.131% | -0.099% | 0.96% | -0.64% | 54% | 33% | 13% | -0.06 | 39% | 206 min |
| P2-H1 | holdout | 3251 | +0.018% | 0.130% | -0.113% | 1.01% | -0.67% | 55% | 33% | 14% | -0.08 | 39% | 200 min |
| P2-H3 | dev | 1347 | -0.015% | 0.066% | -0.081% | 0.96% | -0.67% | 54% | 32% | 14% | -0.16 | 39% | 195 min |
| P2-H3 | holdout | 2413 | -0.025% | 0.065% | -0.090% | 1.02% | -0.71% | 54% | 32% | 14% | -0.10 | 39% | 187 min |
| P2-H2 | dev | 194 | +0.260% | 0.202% | +0.059% | 2.22% | -1.41% | 79% | 63% | 38% | -0.13 | 43% | 252 min |
| P2-H2 | holdout | 278 | +0.092% | 0.200% | -0.109% | 2.07% | -1.44% | 80% | 60% | 39% | -0.34 | 41% | 236 min |
| H2-at-9:35 | dev | 234 | +0.334% | 0.202% | +0.133% | 2.19% | -1.14% | 74% | 61% | 36% | -0.43 | 37% | 203 min |
| H2-at-9:35 | holdout | 349 | +0.146% | 0.200% | -0.054% | 1.92% | -1.13% | 71% | 55% | 34% | -0.64 | 35% | 183 min |

## Holding period (net per candidate if closed at each time)

| | period | 30 min | 60 min | 120 min | 180 min | 15:50 |
|---|---|---:|---:|---:|---:|---:|
| P2-H1 | dev | -0.092% | -0.087% | -0.072% | -0.075% | -0.099% |
| P2-H1 | holdout | -0.150% | -0.127% | -0.115% | -0.113% | -0.113% |
| P2-H2 | dev | -0.074% | -0.038% | -0.025% | +0.038% | +0.059% |
| P2-H2 | holdout | -0.320% | -0.351% | -0.226% | -0.216% | -0.109% |
| H2-at-9:35 | dev | +0.031% | +0.057% | +0.100% | +0.146% | +0.133% |
| H2-at-9:35 | holdout | -0.241% | -0.260% | -0.134% | -0.164% | -0.054% |

## Exits

| | period | exit | n | net/trade |
|---|---|---|---:|---:|
| P2-H1 | dev | close cutoff | 1207 | +0.419% |
| P2-H1 | dev | stop | 567 | -1.203% |
| P2-H1 | holdout | close cutoff | 2061 | +0.521% |
| P2-H1 | holdout | data end | 3 | +0.678% |
| P2-H1 | holdout | stop | 1187 | -1.215% |
| P2-H2 | dev | close cutoff | 116 | +1.596% |
| P2-H2 | dev | stop | 78 | -2.228% |
| P2-H2 | holdout | close cutoff | 158 | +1.445% |
| P2-H2 | holdout | data end | 1 | +4.026% |
| P2-H2 | holdout | stop | 119 | -2.206% |

## Passive entry (H3): fills and adverse selection

| period | signals | filled | fill rate | H1 net when the limit filled | H1 net when it did not |
|---|---:|---:|---:|---:|---:|
| dev | 1774 | 1347 | 75.9% | -0.192% | +0.191% |
| holdout | 3251 | 2413 | 74.2% | -0.195% | +0.123% |

## Regimes (label from SPY's whole day — reporting only, not knowable at entry)

| | period | up calm | up volatile | down calm | down volatile |
|---|---|---|---|---|---|
| P2-H1 | dev | +0.02% (n=749) | +0.43% (n=268) | -0.31% (n=531) | -0.63% (n=226) |
| P2-H1 | holdout | +0.03% (n=1541) | +0.57% (n=392) | -0.42% (n=927) | -0.64% (n=391) |
| P2-H2 | dev | +0.75% (n=91) | +1.54% (n=15) | -0.60% (n=63) | -1.69% (n=25) |
| P2-H2 | holdout | +0.60% (n=127) | +0.32% (n=12) | -0.74% (n=108) | -1.01% (n=31) |

## Overnight (H4) and trend (H5)

| | period | nights/days | gross | net | worst night | 1st pct | nights ≤ −2% |
|---|---|---:|---:|---:|---:|---:|---:|
| H4 | dev | 1188 | +0.067% | -0.065% | -3.07% | -1.65% | 5 |
| H4 | holdout | 1996 | +0.052% | -0.078% | -5.38% | -2.54% | 25 |

| H5 | period | days | strategy total | Sharpe | max drawdown | buy & hold total | Sharpe | max drawdown | switches |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SPY > 100-day SMA | dev | 198 | +2.3% | 0.30 | 6.7% | +6.3% | 0.63 | 9.1% | 9 |
| SPY > 100-day SMA | holdout | 500 | +33.3% | 1.38 | 8.4% | +43.2% | 1.21 | 19.0% | 9 |

## Deterministic filters (development candidates only — descriptive, not a test)

| | n | gross | net |
|---|---:|---:|---:|
| H1 unfiltered | 1774 | +0.032% | -0.099% |
| H1 + relative volume ≥ 2 and SPY above VWAP | 348 | +0.102% | -0.029% |
| H2 unfiltered | 194 | +0.260% | +0.059% |
| H2 + rvol5 ≥ 4 | 79 | +0.078% | -0.123% |

## Coverage

| | dev | holdout |
|---|---:|---:|
| h1Days | 288 | 501 |
| h2Days | 284 | 501 |
| h4Nights | 1188 | 1996 |
| h5Days | 198 | 500 |

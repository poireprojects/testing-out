# Phase 5 development evaluation: DRY RUN report

**Synthetic data.** Every price, quote, filing and company fact in this run was invented by `autotrader/synthetic.py`; only the 160 event identities are real. The numbers below test the pipeline and mean nothing about any strategy or model.

Status: **complete**. Executable quotes verified: **False** (the cache holds following-minute medians only, so every fill is a labelled proxy).
Budget proof: **does not hold (paid path BLOCKED)**.

* 160 of 160 requests have no demonstrated upper bound on billable input tokens (structured-output system prompt of undocumented size; tokenizer not public; token counts are estimates)
* no separate, verifiable, non-overshooting provider limit at or below $12.50 has been demonstrated (a prepaid balance or a workspace limit cannot be read or tested without a key, and the plan's $15 balance is above the ceiling)

## Reconciliation

| check | value |
|---|---|
| ledger_verified | True |
| events | 160 |
| market_outcomes | 160 |
| proposed_without_disposition | 0 |
| filled_without_outcome | 0 |
| unscorable_trades | 0 |
| unreconciled_trades | 0 |
| complete | True |
| decisions per arm | llm 160, no_trade 160, price_only 160, rule_p4h1 160 |

## Registered gates (PLAN.md)

| gate | value | pass |
|---|---|---|
| valid grounded answers ≥ 152 | 0 | False |
| model failures ≤ 3 | 0 | False |
| GAAP diluted EPS ≥ 90% on ≥ 30 checked | n/a on 0 | False |
| every event scored, every proposal disposed | | True |
| spend ≤ $12.50 (committed) | None µ$ | False |

Model-arm status counts: {'not_run': 160}

## Prediction (descriptive)

Pinned base rate 0.49 (historical 0.4904); invalid-but-parseable 0, missing 160.

| set | n | Brier model | Brier base | AUC |
|---|---:|---:|---:|---:|
| valid_only | 0 | n/a | n/a | n/a |
| all_issued | 0 | n/a | n/a | n/a |
| missing_as_baseline | 160 | 0.2492 | 0.2492 | 0.500 |

## Economics (descriptive, proxy fills)

### executable

| arm | proposed | trades | total P&L | per event | per trade | fees | max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| llm | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| no_trade | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| price_only | 82 | 66 | -5.5279 | -0.0345 | -0.0838 | 1.0400 | 5.6350 |
| rule_p4h1 | 11 | 11 | -1.9408 | -0.0121 | -0.1764 | 0.2600 | 2.2400 |

### executable_2x_costs

| arm | proposed | trades | total P&L | per event | per trade | fees | max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| llm | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| no_trade | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| price_only | 82 | 66 | -7.6963 | -0.0481 | -0.1166 | 1.0400 | 7.6963 |
| rule_p4h1 | 11 | 11 | -2.3375 | -0.0146 | -0.2125 | 0.2600 | 2.5687 |

### idealized_fixed_time

| arm | proposed | trades | total P&L | per event | per trade | fees | max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| llm | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| no_trade | 0 | 0 | 0 | 0.0000 | n/a | 0 | 0.0000 |
| price_only | 82 | 66 | -5.5279 | -0.0345 | -0.0838 | 1.0400 | 5.6350 |
| rule_p4h1 | 11 | 11 | -1.9408 | -0.0121 | -0.1764 | 0.2600 | 2.2400 |

## Proposed advancement screen (not frozen)

not evaluated: no model decisions in this run


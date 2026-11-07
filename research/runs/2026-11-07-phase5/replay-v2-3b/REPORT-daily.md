# Autonomous trader — daily report (all days)

Mode: **replay** (simulation only; no order was sent anywhere). Replay runs use previously examined history: **development only, not evidence.**
Ledger integrity: **verified** (277 records)

## Decisions

| decider | events | proposed trades | no trade | invalid / failed answers |
|---|---:|---:|---:|---:|
| no_trade | 30 | 0 | 30 | 0 |
| rule_p4h1 | 30 | 11 | 19 | 0 |
| price_only | 30 | 15 | 15 | 0 |
| llm | 30 | 0 | 30 | 28 |

## Simulated trades (after spreads, slippage and fees)

| decider | instrument | exit | net per trade | P/L |
|---|---|---|---:|---:|
| rule_p4h1 | RIVN | stop | -3.20% | $-0.639 |
| rule_p4h1 | GILD | time | -0.39% | $-0.079 |
| price_only | RIVN | stop | -3.20% | $-0.639 |
| price_only | GILD | time | -0.39% | $-0.079 |

| decider | trades | mean net | total P/L | winners |
|---|---:|---:|---:|---:|
| rule_p4h1 | 2 | -1.79% | $-0.718 | 0 |
| price_only | 2 | -1.79% | $-0.718 | 0 |

## Avoided opportunities

* llm: invalid or ungrounded model answer: treated as no trade (28)
* rule_p4h1: rule conditions not met (19)
* price_only: not beating its sector by 10:00 (15)
* llm: The company's revenue growth is not necessarily a guarantee of future growth. (1)
* llm: The forecast for fourth-quarter 2026 revenue is lower than the previous quarter, which may indicate a slowdown (1)

Rejected by the risk controller (no one was asked):
* price_only: already holding 2 of 2 allowed positions (9)
* rule_p4h1: already holding 2 of 2 allowed positions (7)
* rule_p4h1: entry condition: spread 74 bps exceeds the decision's 30.0 bps (1)
* price_only: entry condition: spread 74 bps exceeds the decision's 30.0 bps (1)
* price_only: entry condition: spread 44 bps exceeds the decision's 30.0 bps (1)
* price_only: entry condition: spread 32 bps exceeds the decision's 30.0 bps (1)
* rule_p4h1: entry condition: spread 36 bps exceeds the decision's 30.0 bps (1)
* price_only: entry condition: spread 36 bps exceeds the decision's 30.0 bps (1)

## Reliability

* model answers: 30; mean latency 117.4 s
* invalid or ungrounded answers: 28
* model cost: $0.0000
* data failures: 0
* model failures: 0

## Safety events

None.

First decision recorded at 2026-11-07T19:37:45.371248+00:00 (UTC).

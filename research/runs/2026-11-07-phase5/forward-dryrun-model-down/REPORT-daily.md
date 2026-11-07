# Autonomous trader — daily report (all days)

Mode: **forward** (simulation only; no order was sent anywhere). Forward: every decision was written before its outcome existed.
Ledger integrity: **verified** (21 records)

## Decisions

| decider | events | proposed trades | no trade | invalid / failed answers |
|---|---:|---:|---:|---:|
| no_trade | 5 | 0 | 5 | 0 |
| llm | 5 | 0 | 5 | 3 |

## Simulated trades (after spreads, slippage and fees)

No simulated trades.

## Avoided opportunities

* llm: model failure: no trade (3)
* llm: model switched off for the rest of the day (safe state) (2)

## Reliability

* model answers: 0
* invalid or ungrounded answers: 0
* model cost: $0.0000
* data failures: 0
* model failures: 3

## Safety events

* 2026-11-07T18:02:56 model: llm: AIUnavailable: Ollama unreachable: [Errno 111] Connection refused
* 2026-11-07T18:02:58 model: llm: AIUnavailable: Ollama unreachable: [Errno 111] Connection refused
* 2026-11-07T18:02:59 model: llm: AIUnavailable: Ollama unreachable: [Errno 111] Connection refused
* 2026-11-07T18:02:59 safe_state: llm switched off after 3 consecutive failures

First decision recorded at 2026-11-07T18:02:56.628535+00:00 (UTC).


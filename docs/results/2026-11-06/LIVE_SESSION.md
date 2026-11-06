# Live paper session and broker integration tests — Nov 6, 2026

All on the Alpaca **paper** account (fake money). No live account or live keys were used.

## Real paper-account integration tests (`tests/test_alpaca_integration.py`) — 8 passed

Run during market hours (9:32 ET) against `paper-api.alpaca.markets` / `data.alpaca.markets`:

| test | what it proved |
|---|---|
| paper account & sizing | account is a "PA" paper account; the app never sizes above the cash in the account (no leverage) |
| calendar, assets, positions | real calendar and asset list; SPY is fractionable |
| unknown order lookups | Alpaca answers "not found" (404) for unknown ids — the stop-protection logic depends on this |
| market data | real IEX snapshots and 1-minute bars |
| fractional limit order | 0.013 SPY limit order accepted (`new`/`accepted`), found by its client id, a duplicate id refused, cancel confirmed |
| rejections | an invalid symbol is rejected and explained in plain English |
| fractional buy + broker stop | 0.013 SPY bought, a DAY **stop order acknowledged by Alpaca**, raised (cancel → confirm → new stop), sold back to exactly the starting position |
| the app's own order paths | the engine's order manager + `_ensure_protection` + exit against the real broker: entry filled, broker-confirmed stop counted as protection, safe exit (stop canceled first) |

## The app running live with the AI (9:47–10:37 ET)

Real app, paper mode, autopilot on, decision mode "ai confirms", local model `llama3.2:3b` ($0).
Screenshots are in `live-paper/`.

| time (ET) | event |
|---|---|
| 10:02 | ranker picked XLE (trend following); AI approved (99%); **bought $19.98** with a broker stop at $61.51 |
| 10:03 | ranker picked AAPL; AI said buy but took 98 s and the price moved 0.24% → **skipped: "the moment passed"** |
| 10:07 | ranker picked XLB; AI approved (98%); **bought $19.98** with a broker stop at $51.51 |
| 10:08 | both position slots in use — further setups passed over |
| 10:12 | **sold XLE −$0.05** (strategy exit: trend broke) |
| 10:14 | AI approved PFE (92%); **bought $14.97** (the $1 risk-per-trade cap made it smaller) |
| 10:37 | session ended; the remaining PFE and XLB positions were closed afterwards (stops canceled first) |

Found and fixed during this session (before any order was placed): on the $100,000 paper account the
limits had scaled to $20,000 per trade / $2,000 daily loss. Sizing now follows the app's own capital
($100 + its realized P/L), so the screenshots show $20 / $2.00 / $1.00.

What the screenshots also show about the local 3B model: very high confidence on everything (92–99%),
some numerically wrong reasoning (e.g. "RSI above 50 … overbought", a stop described as "94% of the
entry price"), and slow answers (25–100 s on this 4-core test machine).

# Trading costs — verified 2026-11-06

| item | value | source (copy read on 2026-11-06, SHA-256) |
|---|---|---|
| Commission | $0 | Alpaca disclosures |
| SEC Section 31 fee | $20.60 per $1,000,000 of sales, from 2026-04-04 (was $0.00 from 2025-05-14) | FINRA Information Notice 2026-03-17 (`02bfd685…c9db`); Alpaca Brokerage Fee Schedule (`814767d3…ed94`): "$0.0000206 × trade value" |
| FINRA Trading Activity Fee | $0.00 for 2026-10-01 → 2026-12-31; $0.000195/share sold (max $9.79 per trade) from 2027-01-01 | SR-FINRA-2026-021 (`57f7cf54…e6e`); Alpaca fee schedule "TAF $0 per share" |
| FINRA CAT fee | $0.000003 per executed equivalent share, buys and sells | Alpaca Brokerage Fee Schedule |
| Rounding | "Each fee type is aggregated separately at the daily, per-account level. After aggregation, each fee total is rounded up to the nearest cent." | Alpaca Brokerage Fee Schedule |
| Spread | 3 bps quoted (MODELED — the history has no quotes) | assumption, stress-tested |
| Slippage | 5 bps per side (MODELED) | assumption; 2× tested |
| Market data | $0 (IEX); full SIP feed ≈ $99/month (third-party reports; not purchased) | |
| AI | $0 (local model) | |

Build 20 rounded each sale's SEC fee and TAF up to a cent ($0.02 per $20 sale = 0.10%). With Alpaca's daily
rounding, a $20 round trip costs ≈0.17% (2027 rates, ~4 sales a day), of which spread + slippage are 0.13%.
Implemented in `stockpilot/risk/fees.py` (dated schedule; `CostModel.legacy_build20()` keeps the old rule for the
frozen baseline). Economics scenarios: `stockpilot/research/economics.py`, `runs/2026-11-06-round1/economics.json`.

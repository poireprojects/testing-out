# Proposed advancement screen (research director's proposal, 2026-11-10)

**Status: proposed for the owner (Riley) to review and freeze before any paid call. Not frozen, not registered.**

This screen is a **spending decision for further research**, not a claim of statistical proof. It sits on top of
the registered reliability gates and the descriptive development economics in `PLAN.md`, and it does not replace
them. Once frozen, it cannot be changed after any paid call; no new result may decide its rules.

It is computed by `advancement_screen()` in `autotrader/devreport.py`, and only for a complete paid run.

## 1. Integrity and reliability

* The run must be complete and integrity-valid:
  * the ledger verifies;
  * every arm has 160 decisions and all 160 market outcomes exist;
  * every proposal has a disposition, and every fill has an outcome that reconciles;
  * the journal is within the ceiling.
* Every registered reliability gate must pass:
  * at least 152 valid grounded answers;
  * at most 3 model failures;
  * GAAP diluted EPS at least 90% correct on at least 30 checked events;
  * end to end complete;
  * at most $12.50 spent.
* Two further findings block advancement:
  * a material unsupported claim (from the blinded review) that drove an approved trade;
  * any breach of a risk limit.

## 2. Prediction and economics

All of these must hold:

* **Brier:** lower than the fixed base rate of 0.490.
* **Account P&L:** positive after execution and inference costs. Inference costs are the actual charges, settled
  plus held, across all 160 researched events.
* **Against each active control:** positive same-day paired P&L excess over `price_only` and over `rule_p4h1`.
* **Doubled costs:** positive net under 2× spreads and 2× slippage, inference costs included.
* **Best day:** positive base-case profit after dropping the best day. The code computes this all-in, with inference costs included, which is the stricter reading; confirm it when freezing.
* **Confidence intervals:** for each mean, a paired 90% interval from resampling whole dates with all four arms
  together, 10,000 replicates, seed 20261110.
  * To advance, two lower bounds must be above zero: mean all-in AI P&L per date, and the paired all-in excess
    over `price_only` per date.
  * The other contrasts are descriptive.

Forty selected dates give limited precision. No trades, or too few days to establish the result, is not enough to
fund another intraday round.

## 3. If an integrity-valid run fails the screen

* Close the autonomous intraday trading direction and archive the result.
* Move StockPilot research to a long-term AI-assisted portfolio benchmarked against diversified ETFs.
* No indicator retuning, and no rerun of these dates.

An implementation failure or missing necessary data is an **inconclusive experiment and a spending stop**. It is
not evidence about the model, and it does not justify a second paid run.

## 4. If it clears every check

Propose only a **new, separately approved forward simulation registration**:

* immutable decisions recorded before any outcome;
* the same evidence, decision clock and controls as the tested workflow;
* starting requirements from the prior design: at least six months, 200 scored forecasts and 100 completed
  trades, a multiplicity correction, at most 24 months, and an explicit operating budget.

A later broker-paper fill study is possible only after forward success, and only with separate approval. Nothing
moves to the next stage automatically. No historical or forward simulation authorizes real money.

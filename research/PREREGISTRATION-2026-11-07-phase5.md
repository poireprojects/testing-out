# Pre-registration: phase 5 forward evaluation of the autonomous decision process (2026-11-07)

Registered before any forward decision is recorded. Changing anything below starts a new registration and a new
clock; nothing is tuned on forward data.

## What is evaluated

**The autonomous research-and-decision process `autotrader` v1**, simulation only (`autotrader/`):

    observation (SEC EDGAR live 8-K feed) -> evidence pack -> decision -> risk controller -> simulated execution
    -> outcome -> daily report

**Deciders, compared on the same events:**
* `llm` v1: the language-model researcher (`autotrader/deciders.py`, prompt and schema as committed).
  * **Model:** Llama 3.2 3B Instruct (Q4_K_M, local, free).
  * Any other model, including a paid one the owner authorizes, is a **new registration**, never a mid-stream
    swap.
* `no_trade`: never trades.
* `rule_p4h1` and `price_only`: mechanical rules, already rejected in phase 4.
  * They are computed from data as of 10:00 on the session day, with no discretion.
  * They are labelled "computed as of", because they cannot decide before the open.

**Events:** 8-K filings by companies in the app's curated universe.

**Timing:**
* The decision is recorded when the filing is observed, before the session it applies to.
* Entry is at 10:00:30 on the session day, only if the decision's entry conditions hold and the risk controller
  approves.
* Exit follows the decision's stop, target or exit time, never later than 15:45. Nothing is held overnight.

**Boundaries** (owner policy, unchanged):
* $100 simulated account per decider.
* $20 maximum position, 2 positions, $2 daily loss limit, the app's stop bounds of 0.4–3%.
* Long only. U.S. stocks and ETFs only: the event's stock or its sector ETF.

## Data rules

* **Forward only.** Evidence counts only from decisions recorded on or after **2026-11-09**, the ledger's
  `forwardFrom`.
* The forward ledger is append-only and hash-chained, and it is committed to git before each session opens. The
  commit time is independent proof that no decision saw its outcome.
* Replays on 2022-12 → 2026-10 data are **development only**: that data was examined in phase 4.
* The vault (2020-07-27 → 2022-10-31) is not used.
* **Prices:**
  * Scoring needs a market-data credential that was never exposed. The exposed paper key is refused in code.
  * Until a rotated key is provided, decisions are recorded and scored later from historical quotes at the
    registered times. That is still forward, because the decisions were committed first.

## The three questions and their pass criteria (all on forward data)

**Q1: understanding** (`llm` only)
* At least 95% of answers are valid: schema-valid and grounded.
* Every fact quotes its evidence verbatim, every number in a claim appears in its quote, and every reported
  figure appears in the release.
* No trade may rest on an ungrounded answer. This is enforced: an invalid answer is recorded as no trade.
* Reported GAAP diluted EPS matches the XBRL value filed later within $0.01 on at least 90% of at least 50
  checked events.

**Q2: prediction** (`llm`)
* Measure: the Brier score of `p_up_by_close`, the probability that the stock is higher at 15:45 than at 10:00:30,
  scored on every event the model answered, traded or not.
* Comparison: a constant base rate fixed now from the development fold, **0.490** (1,299 of 2,649; the share of 2022-12 →
  2024-10 earnings mornings that rose from 10:00:30 to 15:45).
* Pass: a lower Brier than the base rate, with a paired day-block bootstrap p < 0.05, on at least 200 events.

**Q3: trading** (after spreads, slippage, fees aggregated per day, and 30 s of latency)
* At least 100 simulated `llm` trades.
* Mean net per trade > 0, with a day-block bootstrap p < 0.05. Holm correction covers three tests: Q2, Q3 net > 0,
  and Q3 versus `price_only`.
* Paired net excess over `price_only` on the same events > 0.
* Still positive with 2× spreads and slippage.
* The 5 best trades contribute less than 50% of total profit.

**Reliability**
* At least 99% of scheduled cycles complete.
* No approved order violates any owner limit. Any violation is a failure of the whole phase.
* Every safety event is resolved by the next session.

## Duration and stopping

* **Duration:** at least 6 months **and** at least 100 `llm` trades; at most 24 months.
* **Futility stop:** after 100 trades, if the mean net per trade is below −0.10% with p(> 0) < 0.05, stop and
  report.
* **Changes:** a change to the model, prompt, schema, evidence sources or risk policy closes this registration and
  opens a new one. Its evidence does not carry over.

## What passing would permit

**Only** a proposal to move to **paper trading**, which needs the owner's explicit approval:
* the same decisions and risk controller, placing paper orders through the app's existing Alpaca integration;
* at least 3 months, with fill quality measured against the simulated costs.

Real money is a separate, later decision. It is not reachable from this registration.

## What failing means

The process stays a research tool. The repository's reopening criteria (`research/REOPENING-CRITERIA.md`) apply.

---

## Amendment 1 (2026-11-07, later the same day): registration v1 suspended

Appended; nothing above has been edited.

**Status: suspended before any evidence was collected.** No forward result exists under v1, and none will be
claimed.

**Why:**
* **The scoring process didn't exist.** The forward command recorded decisions but nothing executed and scored a
  finished session.
* **Evaluation protocol v1 scored predictions on the date of the decision timestamp.** That is wrong for any
  decision written after the close, before the open, or on a weekend or holiday: a decision written on Saturday
  2026-11-07 targets Monday 2026-11-09. No v1 forward decision was ever scored, so no result was affected.
* **The registered model failed the understanding test badly enough** that the prediction and trading questions
  could not be answered. In development, 8 of 30 answers were valid; in the first forward cycle, 2 of 10.

**What stays:**
* The v1 forward ledger (`research/runs/2026-11-07-phase5/forward/`) and the aborted dry run are kept, unchanged
  and verifiable, as records.

**What changed** (all labelled version 2):
* **Evaluation protocol v2** (`autotrader/evaluate.py`, `autotrader/scoring.py`):
  * predictions are scored on the recorded target session;
  * an unattended scorer executes recorded decisions once a session's prices exist;
  * the mechanical baselines are computed as of 10:00 from inputs whose end times are recorded and checked.
* **Output contract v2** (`autotrader/decision.py`):
  * facts cite numbered sentences instead of copying text;
  * reported figures cite the company's release, with a unit;
  * trade fields are fixed choices.
  * Verification is unchanged or stricter (`tests/test_autotrader_v2.py`).

**What must happen before a new registration:** a new forward registration (v2, with a new six-month clock) will
be written only after the fixed-cost development evaluation in
`research/runs/2026-11-07-phase5/dev-eval/PLAN.md` passes its reliability gate, and the owner has approved the
model, the price data and the host.

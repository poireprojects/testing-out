# Phase 5: an autonomous research-and-decision prototype (2026-11-07)

**What exists now:** a working, simulation-only autonomous trader. In a single loop it:
* observes market events (SEC filings);
* investigates them from sourced, time-stamped evidence;
* forms a thesis, decides to trade or not, and passes its proposal through a deterministic risk controller;
* simulates execution with realistic costs;
* records everything in a tamper-evident ledger, then reports.

No person approves anything along the way.

**What does not exist:** any evidence that its decisions make money. Sophisticated reasoning is not a
demonstrated trading advantage, and the 13 hypotheses this project already rejected are the base rate.

**Live trading stays disabled.** The prototype cannot place an order. The desktop app is unchanged, so there is no
new installer.

## Results so far

### Development replay

**This is development, not evidence.** The data was examined in phase 4. The window was pre-specified as the last
30 earnings events in the cache, which all fell on **one day, 2026-10-30**. One day of data, on which SPY fell
1.1% by 10:00, can show how the system behaves. It says nothing about whether it makes money.

Files: `research/runs/2026-11-07-phase5/replay/` (`ledger.jsonl`, `results.json`, `REPORT-daily.md`).

| | `llm` (Llama 3.2 3B, local) | `rule_p4h1` | `price_only` | `no_trade` |
|---|---:|---:|---:|---:|
| events seen | 30 | 30 | 30 | 30 |
| trades proposed | **0** | 11 | 15 | 0 |
| trades approved by the risk controller | 0 | 2 | 2 | 0 |
| mean net per simulated trade | — | −1.79% | −1.79% | — |
| simulated P/L | $0.00 | −$0.72 | −$0.72 | $0.00 |

**Q1: understanding.**
* **8 of 30 answers (27%) were valid.**
* The other 22 were recorded as invalid and became no trade:
  * 8 quoted text that is not in the cited evidence;
  * 6 broke the schema;
  * 4 ran out of output tokens mid-answer;
  * 4 reported a figure that does not appear in the company's release.
* Reported EPS was checked against the XBRL filed later: correct in 2 of 2. Revenue: 1 of 2. Too few to measure.

**Q2: prediction.** On the 20 answers that gave a probability, its Brier score was **0.369**, against **0.249**
for the constant base rate. That is worse than always saying 49%. Its probabilities cluster at 0.03, 0.5 and 0.9.

**Q3: trading.**
* All 8 valid answers chose **no trade**, mostly reasoning that the expected move was smaller than the round-trip
  cost.
* The two baselines bought RIVN and GILD. RIVN hit its stop (−3.2%) and GILD closed lower (−0.4%).
* The 2-position cap rejected 16 further proposals. Quotes wider than 30 bps rejected 6.

**Operation.**
* A mean of **143 s** per model answer, 317 s at worst, on 4 CPU cores. Fine for a handful of decisions before
  the open; far too slow for many events during the session.
* In the replay, no safety event occurred.
* In the first forward attempt (below), the model server really did go down. The system fell to the safe state as
  designed: no trades, the model switched off after 3 failures, and all of it recorded.

**What this shows:**
* The architecture works, and its guards work.
* **This small model is not a usable researcher.** Nearly three-quarters of its answers fail grounding, and its
  probabilities are worse than a constant.
* Q1 needs a much stronger model before Q2 or Q3 can be tested meaningfully. Whether any model passes Q2 and Q3
  is unknown.
* An example of a valid, grounded answer (Republic Services) is in the replay ledger. It quotes the release
  ("EPS was $1.93 per share, an increase of 9.7 percent over the prior year"), notes the stock was already up
  3.22% by 10:00, and declines the trade because the expected move does not cover the cost.


## Version 2 (2026-11-07, later): corrections before any paid model or new forward evaluation

The v1 ledgers are unchanged. Registration v1 is suspended (amendment in the pre-registration).

1. **Scoring by the target session** (`evaluate.py`, evaluation protocol v2).
   * Predictions are scored on the decision's recorded session: a decision written on Saturday for Monday is
     scored on Monday.
   * The score comes from the ledger's own market-outcome records, so it can be recomputed from the ledger
     alone.
   * Regression tests cover weekend, after-hours, pre-open and holiday decisions.
2. **Unattended forward scoring** (`scoring.py`; the `score` command; the `daemon` scores finished sessions).
   * Once a session's prices exist, the scorer:
     * computes the pre-registered mechanical baselines **as of 10:00**, recording each input's end time (the
       ledger refuses any input that ended later);
     * records each event's 10:00:30 → 15:45 outcome;
     * puts every recorded decision through the same risk controller at 10:00:30 and simulates it.
   * It holds no model and cannot call one: a recorded AI decision is executed exactly as written.
   * Missing prices leave a session pending, with nothing half-written; after five days it is marked
     unscorable.
   * Prices need the owner's rotated key (`ALPACA_DATA_KEY_ID` / `ALPACA_DATA_SECRET`); the exposed key is
     refused.
3. **Answer audit and output contract v2** (`decision.py`, `research/runs/2026-11-07-phase5/audit/AUDIT.md`).
   * Facts cite numbered sentences and the system inserts the text. Reported figures cite the release with a
     unit. Trade fields are fixed choices.
   * Verification is unchanged or stricter.
   * With the 3B model, v2 removed the formatting failures but valid answers fell from 8 to 2 of 30. The small
     model is ruled out as the researcher.
4. **A capable model, under a fixed budget** (`providers.py`, `research/runs/2026-11-07-phase5/dev-eval/PLAN.md`).
   * Recommended: Claude Opus 5.5 at effort `low`, structured outputs, no refusal fallback.
   * Sample: 160 pre-specified development events on 40 dates.
   * **Hard ceiling $12.50** (worst case $12.48; expected $5–7), enforced by the program, the account's
     prepaid credit and a dedicated key.
   * **Nothing paid has been activated.**

| replay of the same 30 events, local 3B model | contract v1 | contract v2 |
|---|---:|---:|
| valid answers | 8 | 2 |
| format-only failures | 12 | 1 |
| genuine factual errors (all caught) | 1 | 7 |
| Brier score (constant base rate: 0.249) | 0.369 | 0.333 |

Files: `research/runs/2026-11-07-phase5/replay-v2-3b/`; each run's `results_protocol_v2.json`.

### Dev-evaluation preflight (2026-11-10): the paid path stays blocked

An independent audit of the paid path found that the "hard ceiling" in point 4 above was not a guarantee. The
program compared floats, forgot reservations on restart, and let a new output directory start a fresh budget. One
timed-out request could be sent twice. Characters / 3 did not bound tokens. Version 2 of the paid path (a
transactional budget journal, a pinned single-attempt client, exact request bytes) and the proposed amendment 1
(evidence, execution and measurement corrections) are in `research/runs/2026-11-07-phase5/dev-eval/`:
`AMENDMENT-1.md`, `ADVANCEMENT-SCREEN.md` and `PREFLIGHT-2026-11-10.md`.

The budget proof does not hold: no upper bound on billable input tokens can be demonstrated offline. So
`devrun --paid` stops before any client exists. Nothing paid has been activated.

## 1. Architecture

```
 SEC EDGAR live 8-K feed (forward) ─┐
 phase-4 event cache (replay)      ─┼─▶ Observer ─▶ Investigation: evidence pack
 XBRL financial history            ─┤      every item has a source, the time it became available, and a hash;
 price context (when a feed exists)─┘      anything later than the decision time is refused
                                                   │
                                                   ▼
             Deciders (same events, same evidence): llm researcher · no_trade · rule_p4h1 · price_only
                                                   │  structured decision: facts with verbatim quotes,
                                                   │  interpretations, uncertainties, what is priced in,
                                                   │  invalidation, entry/exit conditions, P(up), E[net return]
                                                   ▼
                     Validation and grounding (invalid or ungrounded answer → recorded, becomes no trade)
                                                   ▼
          Risk controller: owner policy + the desktop app's own RiskEngine on a simulated $100 account
              rejected → recorded, nobody asked │ approved → the controller sets the size (≤ $20)
                                                   ▼
             Simulated execution: NBBO + half spread + 5 bps, 30 s latency, stops never triggered by
             pre-entry prices, fees rounded per day
                                                   ▼
             Outcome tracking ─▶ append-only, hash-chained ledger ─▶ daily report
```

**Reused, not rebuilt:**

| piece | from |
|---|---|
| position sizing, $20 cap, 2 positions, $2 daily loss, stop bounds, quote age, spread, trading hours, cooldowns | `stockpilot.risk.engine.RiskEngine.check_entry` (unchanged) |
| fee schedule (SEC, TAF, CAT by date) | `stockpilot.risk.fees.CostModel` |
| market calendar and session phases | `stockpilot.clock` |
| local and paid model access, with cost tracking and budget cap | `stockpilot.ai.providers` (Ollama, Anthropic) |
| SEC fair-access client and filing-header parsing | `stockpilot.research.events` |
| permitted universe and symbol safety | `stockpilot.data.universe` |
| cost conventions and the phase-4 integrity fixes | `scripts/phase4_corrected.py` |

New code is in `autotrader/`:

| file | role |
|---|---|
| `policy.py` | owner policy |
| `evidence.py` | evidence packs |
| `decision.py` | schema and grounding |
| `deciders.py` | the deciders |
| `risk.py` | the risk controller |
| `simulate.py` | simulated execution |
| `sources.py` | data sources |
| `runner.py` | the loop and its safe states |
| `ledger.py` | the ledger |
| `report.py` | the daily report |
| `evaluate.py` | the three questions |
| `__main__.py` | the command line |

The desktop app never imports this package and the installer does not bundle it. A test enforces both.

## 2. The autonomous researcher

**It receives** an evidence pack:
* the company's own release (the 8-K exhibit) with its SEC acceptance time;
* its last four quarters of diluted EPS and revenue from XBRL filings made before the decision;
* the price context available at the decision time;
* this project's own earlier findings, from data that predates the event.

**It must produce:**
* facts, each quoting an evidence item **verbatim** by id;
* interpretations and uncertainties, kept separate;
* what the market has already priced in, and what would invalidate the idea;
* the reported figures;
* a probability that the stock rises to 15:45, and an expected net return;
* a decision: buy the stock, buy its sector ETF, or **no trade**;
* entry conditions (maximum spread, a band for the move so far) and exit conditions (stop, optional target,
  exit time);
* reasons to avoid the trade.

**It is not limited to a list of patterns.** It reads the news and decides.

**It cannot invent anything.** An answer is recorded as invalid and becomes "no trade" if:
* a quote is not in the cited evidence;
* a claim states a number that is not in its quote;
* a reported figure does not appear in the release;
* it names an instrument other than the event's stock or sector ETF;
* it adds any field the schema doesn't have, such as a position size.

**Baselines on the same events:**
* `no_trade`: the benchmark after costs.
* `rule_p4h1`: the phase-4 earnings rule, already rejected.
* `price_only`: buys whatever is beating its sector by 10:00.

## 3. Intelligence is separated from financial safety

| boundary | enforced by | can the AI change it? |
|---|---|---|
| simulation only | `owner_policy.json` (any other mode refuses to load) | no |
| capital, $20 position, 2 positions, $2 daily loss, 3 losses in a row, 10 trades a day, cooldown | the app's `RiskEngine` with the app's default settings | no: the AI's schema has no size or limit field |
| long only; only the event's stock or its sector ETF, in the app's universe; plain U.S. symbols | `RiskController` | no |
| stop required, 0.4–3%; exit no later than 15:45; no overnight holding | `RiskController` and `RiskEngine` | no |
| quote freshness (90 s), spread (30 bps, or tighter if the AI asks), trading hours | `RiskEngine` and the decision's own entry conditions | it can only make them stricter |
| kill switch | a `KILL` file in the run folder | no |
| exposed credentials | `refuse_exposed_key` | no |

**Safe states** (no person is asked):
* **The model fails:** that event becomes no trade. After 3 consecutive failures the model is switched off for
  the day.
* **Data fails 3 times in a row:** no new decisions that day.
* **The risk controller errors:** the order is rejected.
* **The ledger can't be written:** the run stops.
* **The kill switch is present:** everything stops.

Each of these is recorded as a safety event and appears in the daily report. Changing any boundary means editing
the owner policy, an owner action whose hash is written into every cycle.

## 4. Making it scientific

Three separate questions (`autotrader/evaluate.py`):
* **Q1, understanding:** are its answers valid and grounded, and are the figures it reports correct?
* **Q2, prediction:** does its probability beat a constant base rate (Brier score)?
* **Q3, trading:** after every cost, does acting on it beat no trade and the price-only baseline?

**The ledger** is append-only and hash-chained:
* a second decision on the same event is refused;
* an outcome cannot use prices from before its decision;
* in forward mode, an outcome must use prices that did not exist when the decision was written;
* a replay ledger can never be reopened as a forward ledger.

**Replays** run on data examined in phase 4. They are development only. The vault is not touched.

**The forward evaluation** is pre-registered in `research/PREREGISTRATION-2026-11-07-phase5.md`:
* one model version;
* fixed criteria for each question;
* at least 6 months and at least 100 trades;
* a futility stop.

Passing it permits only a *proposal* to move to paper trading.

### Forward evidence

**Registration:** `research/PREREGISTRATION-2026-11-07-phase5.md` was committed before any forward decision.

**First attempt, 2026-11-07, aborted:** `research/runs/2026-11-07-phase5/forward-dryrun-model-down/`.
* The observer found the 8-K filings accepted after Friday's close (first tradable on Monday 2026-11-09).
* The local model server had stopped, so the system did what it should: no trade on each event, the model
  switched off after 3 failures, all of it recorded.
* No outcome existed, and none could have: Monday had not happened.
* It is kept as the record of that safe state, not as evidence.

**First forward cycle, 2026-11-07, model running:** `research/runs/2026-11-07-phase5/forward/` (ledger and
daily report), committed before Monday's open.
* The SEC feed showed 19 recent 8-Ks from universe companies.
* 9 were skipped because the market had already traded on them.
* 10 were decided for the session of **2026-11-09**: KLAC, PSA, O, TRV, WEC, PEG, ROKU, MOH, DAL, ASTS.
* `llm`: 2 valid answers (KLAC, PEG), both no trade. 8 invalid answers, recorded as no trade:
  * 6 broke the schema (quotes over 400 characters, too many list items, an out-of-range spread);
  * 2 quoted text that is not in the filing.
* **No trade was proposed, so there is nothing to execute on Monday.** Scoring the recorded probabilities (Q2)
  needs Monday's prices, from a market-data key that was never exposed.

The forward record confirms the replay: with this model, Q1 fails long before trading can be judged.


## 5. Can it run without routine intervention?

**What it does alone today:**
* observes, investigates, decides, applies risk limits, simulates, records and reports;
* backs off and retries on ordinary errors;
* stops itself on unsafe conditions.

This is the `daemon` command, and tests cover each failure path.

**What it still needs:**
* **A host that stays on during market hours.** This cloud workspace is temporary and is reclaimed when idle, so
  it can't be the long-running host. The options are the owner's computer (the desktop app, or the daemon), or a
  scheduled cloud run. The owner has to choose.
* **A market-data credential that was never exposed.** Without it, decisions are recorded on time but scored
  later from historical prices.

## 6. What information it can actually access

| information | in this cloud workspace | on the owner's computer | cost |
|---|---|---|---|
| SEC filings: 8-K text and acceptance time, live feed, XBRL financials | yes (declared contact) | yes | free |
| consolidated prices and quotes | no: needs a rotated key, and the exposed one is refused | yes, with the app's (rotated) Alpaca key | free historical SIP; $99/month for real-time SIP |
| vendor news headlines (with consensus beat/miss) | no | yes, with the Alpaca key | free |
| economic data (FRED) | reachable, not wired in | — | free |
| analyst estimates, transcripts, order flow | no | no | paid; not authorized |
| a capable language model | only a small local model (Llama 3.2 3B, CPU, about 2–3 minutes per event) | Ollama locally, or Claude through the owner's own API key and budget cap | local: free; Claude: paid, needs authorization |

## 7. What is proven and what is not

**Proven by tests and the demonstration:**
* the loop runs end to end without a person;
* the model's answers are checked against their sources;
* invented quotes and figures are caught and never traded;
* risk limits hold whatever the model says;
* failures fall to a safe state;
* records are tamper-evident and free of look-ahead;
* the daily report is produced from the ledger alone.

**Not proven:**
* that any model, small or advanced, understands filings reliably enough;
* that its probabilities beat a base rate;
* that acting on it beats doing nothing after costs.

The development replay is far too small, and on examined data, to say anything about the last two. Only the
forward evaluation can.

## 8. Evidence needed before trusting it with capital

All of the following are needed, in order:
1. The pre-registered forward criteria pass on forward data: Q1 ≥ 95% valid and ≥ 90% correct EPS; Q2 Brier
   below the base rate with p < 0.05 over ≥ 200 events; Q3 positive net per trade with p < 0.05 over ≥ 100
   trades, beating price-only and surviving 2× costs; 99% reliability; no limit breaches.
2. The owner approves paper trading: real paper orders through the app with the same controller for at least
   3 months, with fills measured against the simulation.
3. A separate owner decision on any real money, within the existing $100 / $20 limits.

The project's 13 rejected hypotheses and the regulators' population data on retail day traders (section A of
`research/runs/2026-11-07-reset/REPORT.md`) set the prior: most attempts fail at step 1.

## How to run

```
# development replay on the phase-4 cache (examined data: not evidence)
SEC_USER_AGENT="name email" python -m autotrader replay DATA_DIR OUT_DIR --last 30 --llm ollama
# one forward cycle: observe new 8-K filings, decide, record (prices scored later)
SEC_USER_AGENT="name email" python -m autotrader forward OUT_DIR --llm ollama
# unattended forward loop (stops on the kill switch: create OUT_DIR/KILL); scores finished sessions when a
# rotated market-data key is set (ALPACA_DATA_KEY_ID, ALPACA_DATA_SECRET)
SEC_USER_AGENT="name email" python -m autotrader daemon OUT_DIR --llm ollama
python -m autotrader score OUT_DIR                     # score finished sessions once
python -m autotrader evaluate OUT_DIR/ledger.jsonl     # Q1-Q3 from the ledger (protocol v2)
python -m autotrader report OUT_DIR/ledger.jsonl [--day YYYY-MM-DD]
python -m autotrader verify OUT_DIR/ledger.jsonl
```

`--llm ollama` uses a local model through Ollama's API (`OLLAMA_URL`, `OLLAMA_MODEL`). No paid service is called.

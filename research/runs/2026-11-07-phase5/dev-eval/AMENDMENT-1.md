# Amendment 1 to the fixed-cost development evaluation (proposed, 2026-11-10)

**Status: proposed. Nothing in it is active until the owner approves it in writing** (`APPROVAL.json`, format
below). `PLAN.md`, `sample.json` and every earlier result stay exactly as they are. No result of this experiment
exists, and none was looked at to write this: the only runs were a dry run on synthetic data and a zero-spend
preflight that reads inputs and no outcomes.

## What does not change

* The 160 events on 40 dates (`sample.json`, SHA-256 `e825d6d6…f738f68b`), in the registered replay order.
* One Claude Opus 5.5 (`claude-opus-5-5`) decision per event, output contract v2, effort `low`.
* 4,500 maximum input tokens, 3,000 maximum output tokens, and **$12.50 for the entire experiment**.
* The three controls: `no_trade` (cash), `price_only`, `rule_p4h1`.
* An independent $100 account per arm. Long only, $20 position cap, two slots, $2 daily loss limit, and the
  registered stop and exit rules.
* The registered reliability gates, and the descriptive results named in `PLAN.md`.

No prompt tuning. No replacement dates, extra paid conditions, second attempts or model substitutions.

## 1. Paid-path measurement (version: paid path 2)

| | before (audited revision `5893800`) | after |
|---|---|---|
| budget record | floats in `OUT_DIR/budget.json`; a reservation is forgotten on restart; a new output directory starts a new budget | one SQLite journal per **experiment identity**, outside the output directory; integer micro-dollars; one writer (OS lock); one attempt per event (primary key); per-row digests; refused if missing, inconsistent, damaged, or opened from another output directory (`autotrader/journal.py`) |
| before a send | worst case checked against the ceiling, nothing persisted | the whole run's per-event maxima are stored and must fit before the first send; the event's maximum is written (`reserved`), then `dispatched` immediately before the request leaves |
| retries | SDK `max_retries=1`: one timed-out request was sent twice under one settled call | `max_retries=0`, no middleware, no fallback; a request is sent once or not at all and is never resent |
| endpoint and options | SDK defaults; environment and profile could change endpoint or headers; workspace defaults apply | pinned `https://api.anthropic.com/v1/messages`, API version `2026-10-21`, `anthropic==2.7.0`; refuses `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, profiles, other credential sources and debug logging; sends `service_tier: standard_only` and `inference_geo: global` (a US-only workspace default would cost 1.1×) |
| after the response | cost from usage (treated as zero if missing); refusal or cut-off charged from usage | **settled** only for `end_turn` from the requested model with valid usage; everything else is a **hold** at the maximum. Unpriced categories (cache, fast mode, US-only, priority or batch tier, server tools) or contradictory usage stop the run |
| input size | `characters / 3`, which is not a bound | exact request bytes rendered by the pinned SDK; the budget proof needs a *demonstrated* upper bound on billable input tokens (see §6) |
| command line | could pass another ceiling, model, contract or sample | `devrun --paid` takes none of these; the replay command refuses a paid model |

## 2. Sample and inputs (version: evidence 2)

| | before | after |
|---|---|---|
| sample matching | ticker and accession only; unmatched rows silently dropped | exact `(date, ticker, accession, fold)`; registered digest; 160 events, 4 per date, 40 dates, 80 per fold; any mismatch stops the run before inputs are built and before any client exists |
| research note | `autotrader/sources.py` appended a note stamped 2024-10-31 to every pack. Its figures were computed by this project in November 2026. The look-ahead guard rejects every pack before that date (the first 80 events), so those events could not be built at all | the note is **removed from every historical prompt** (not backdated). The look-ahead guard is unchanged |
| cost information | inside the note: data-derived cost figures | the same neutral statement of the simulator's conventions on every date, outside the evidence (`COST_CONVENTIONS` in `autotrader/deciders.py`); it contains no figure computed from data |
| missing filing | the pack was built anyway from prices only | `input_incomplete`: not sent to the model, never replaced by a price-only pack, counted as unanswered |
| inputs before outcomes | built per session, interleaved with outcomes | all 160 packs and exact request bytes are built, persisted and hashed first (`inputs/`, `inputs_manifest.json`); then every decision; then outcomes |

**Exact input diff for approval** (a synthetic event after 2024-10-31; the real diff differs only in the evidence
numbering):

```diff
-E4: research | this project's phase-4 development results (data through 2024-10-31) | available 2024-10-31 16:00 EDT
-[E4.S1] This project's own research on its development data (Dec 2022 - Oct 2024, 2,650 earnings mornings, computed with costs): buying at 10:00:30 and selling at 15:45 averaged -0.25% per trade after costs.
-[E4.S2] Earnings mornings with an EPS beat, a sales beat and a positive price reaction by 10:00 averaged +0.03% before costs and -0.23% after.
-[E4.S3] A stock with the same price reaction but no news did as well.
-[E4.S4] Typical cost of a round trip in these stocks: about 0.24% in spread and slippage plus about 0.03% in fees.
-[E4.S5] Most earnings mornings offer no edge after costs.
+Cost conventions (the same for every event; not evidence): a simulated entry pays half the quoted bid-ask spread plus 0.05% slippage, and a simulated exit pays the same again, so a round trip costs at least the full quoted spread plus 0.10%. Regulatory fees are charged on sales and the CAT fee on every share bought or sold. A trade must expect to earn more than these costs.
```

The system prompt, the schema, the contract and the closing instruction are unchanged.

## 3. Timing and costs (version: execution 2)

| | before | after |
|---|---|---|
| quotes | `ReplayPrices.quote` presented the median of 10:00:30–10:01:30 (and of 15:45:00–15:46:00) as a quote *at* 10:00:30 (15:45) | the same medians, labelled `ProxyQuote` with their real window. Fills, spread and move conditions on them are a **proxy**. `EXECUTABLE_VERIFIED = False`: executable profitability cannot be verified from this cache, and no new quotes are acquired |
| decision clock | the model's latency was ignored | all arms decide from the same 10:00:00 inputs. The model's four requests per date run in the frozen order. An event is ready at 10:00:00 plus its own and all earlier latencies. **Executable:** not ready by 10:00:30 means no fill. **Idealized fixed-time:** reported separately. The rules are ready at 10:00:00. Cutoff, queue position, latency and ready time are logged |
| contested slots | the order of the decider loop | the frozen order (vendor flash time, then symbol) in every arm; fixed here, before any score |
| early time exits (11:00–15:00) | no quote exists; silently the next bar's open with the entry half-spread | the same proxy, flagged on every trade |
| missing bars between entry and exit | stop and target checked on whatever bars existed | the trade is **unscorable** (never filled in) |
| move condition without a prior close | condition silently skipped and the trade allowed | rejected (fail closed); this also applies to every other user of the risk controller |
| fees | the app schedule: before 2025-05-14 its first row (SEC fee $0, 2025 TAF), which affects 100 of the 160 events | the historical research schedule (`autotrader/fees_hist.py`); the desktop app's schedule and published results are untouched; see the unresolved findings for its verification status |
| sensitivity | none | 2× spreads and 2× slippage, rerunning fill eligibility, sizing and outcomes from the same frozen decisions |
| kept | | half the spread plus 5 bps slippage per side, stop first on ambiguity, the pessimistic 10:00 straddle-bar rule, per-decider daily fee aggregation |

## 4. Measurement of the model (version: measurement 2)

| | before | after |
|---|---|---|
| coverage | answers counted; a circuit-breaker skip had `valid=true` with no model response | a 160-row table (`coverage.csv`) with separate dimensions: input status; model status (grounded valid, invalid schema, invalid grounding, refusal, cut-off, wrong model, provider error, infrastructure failure, circuit-breaker skip, input incomplete, not run); decision; probability present; charge state; market outcome; and each arm's disposition in each execution variant. A skip is never valid |
| success | exit 0 once the input rows matched | complete only if the ledger verifies, every arm has 160 decisions, 160 market outcomes exist, every proposal has a disposition, every fill has an outcome that reconciles, and the journal is within the ceiling |
| EPS tolerance | `abs(a - b) <= 0.011` in floats | `|a - b| <= 0.01` in decimal arithmetic, as registered |
| EPS label | any fact with `end < day <= filed`, including later comparatives | the first 10-Q/10-K filed on or after the release whose own current period is the release quarter, within 100 days; accession, form, period and filing date recorded; checkability decided from the filings alone (`autotrader/labels.py`, never imported by an input builder) |
| claims | numbers in a claim had to appear in the cited sentences | the same automatic check, plus a **blinded review** of every claim and reported figure under a frozen rubric (subject, direction, period, GAAP vs adjusted, sign, unit; `autotrader/review.py`). Automatic validity and verified accuracy are reported separately |
| base rate | 0.490 registered, but a standalone evaluation silently used 0.5 | pinned 0.490; the historical 0.4904 disclosed |
| probability scoring | valid answers only | valid-only, all-issued (including invalid but parseable), and missing-as-baseline; paired Brier difference by date block; AUC with ties; calibration in fixed bins (sparse bins labelled); confidence counts |
| post-cutoff events | not separated | the 16 events after the model's June 2026 training cutoff are reported separately, as descriptive only |

## 5. Attribution and limits (stated before any result)

* One full-input model against two deterministic rules tests **incremental system performance**. The financial
  inputs, the instrument choice, the timing and the exits all differ between arms. This design cannot isolate the
  value of reading the filings, and no second paid arm is added to try.
* The training cutoff overlaps 144 of the 160 events, and the prompts name the ticker and the date. The project
  has also examined this data before. This is not an independent historical test.
* Sample construction required end-of-day quote availability and busy earnings days. Coverage and universe bias
  follow (large, liquid U.S. companies with a vendor flash before the open). Sector mappings use current SIC
  codes. Missing events stay failures; the sample is never backfilled.
* For 3 events the SEC 8-K was accepted after 10:00 on the event day, though the vendor flash came before the
  open: SRE 2023-02-28, C 2024-01-12 and C 2025-04-15. Their releases cannot be in a 10:00 input, so they will be
  `input_incomplete` whatever else happens. They are not replaced.

## 6. The budget proof, and why the paid path stays blocked

At base rates the registered maximum is $0.078 per event ($0.018 input + $0.060 output) and $12.48 for 160
events. That leaves $0.02 for the whole run. A guarantee therefore needs a *demonstrated* upper bound on the
billable tokens of every one of the 160 exact requests. The output side is bounded: `max_tokens` is a strict limit
that includes all thinking. The input side is not:

* Structured outputs add a system prompt that is billed as input. Its size is not documented.
* The tokenizer is not public, and the token-counting endpoint is documented as an estimate.
* Even assuming at least one byte per token and a free structured-output prompt, the synthetic requests alone add
  up to $13.03. Real filings are longer.
* No separate, verifiable, non-overshooting provider limit at or below $12.50 has been demonstrated. The plan's
  $15 prepaid balance is above the ceiling.

So `budget_proof()` does not hold and `devrun --paid` stops before any client is built. Unblocking it is an owner
decision that needs one of:

* a demonstrated per-request input bound that fits;
* a provider-side hard limit at or below $12.50 that is shown not to overshoot;
* a new registration with different limits.

Nothing here weakens the check.

## Owner approval (format)

`research/runs/2026-11-07-phase5/dev-eval/APPROVAL.json`, written by the owner, never by code:

```json
{"approved_by": "<name>", "approved_on": "<date>", "experiment_id": "<from the preflight>",
 "amendment_sha256": "<sha256 of this file>", "ceiling_usd": "12.50", "model": "claude-opus-5-5"}
```

Separately, before any paid call, the owner also freezes (or rejects) the proposed advancement screen
(`ADVANCEMENT-SCREEN.md`).

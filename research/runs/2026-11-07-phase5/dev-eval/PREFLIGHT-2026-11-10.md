# Phase 5 development evaluation: zero-spend preflight (2026-11-10)

**Outcome: the paid path stays BLOCKED.** Nothing was sent to any model, no key was used or requested, no money
was spent, no market data was acquired, and no outcome of the 160 events was computed or looked at. This
change is research-only: the desktop app's behaviour, fee schedule and published results are unchanged, and the
release gate classifies every changed path as non-app.

* **Audited revision:** `58938000f00288d226acb1ee551731155599384e`. The working branch was at that exact commit
  when this work started, so there was nothing else to inspect.
* **This change:** the commits after `5893800` (`git log 5893800..`; the diff is `git diff 5893800`) on `claude/ai-day-trading-app-i8p65e`.

## What was demonstrated where

| | with fixtures and synthetic data (no licensed data) | with the 160 real events (owner's phase-4 cache, read only) |
|---|---|---|
| sample identity, exact `(date, ticker, accession, fold)` match | tamper tests; a missing row stops the run before anything is built | **160/160 rows match**; digest `e825d6d6…f738f68b` verified |
| cache provenance | — | **7/7 committed phase-4 digests match** (companies, events, plan, four quote shards); bars: 457 files, the same as the committed file count (no digests were committed for them); new fingerprints of the 141 required bar files recorded |
| 10:00 inputs | look-ahead tests | prior close, the move by 10:00 against the sector, the 09:30–10:00 volume ratio and the parsed surprise were **recomputed independently for 160/160** and match `plan.json` |
| bars and quotes | unscorable and rejection paths | full-day bars for stock and sector ETF 160/160; prior close 160/160; entry and exit quote windows for stock and ETF 160/160 (following-minute medians: a proxy) |
| SEC material | complete synthetic filings drive the whole pipeline | acceptance times known 160/160, before 10:00 for 157; **filing headers 0/160, release exhibits 0/160, company facts 11/160 in the cache**: no model input can be built (BLOCKED) |
| paid path | real `anthropic` 2.7.0 with an in-memory transport: pinned request, no retries, holds, stops, crashes at every step, a second runner, a changed directory, a tampered journal | not run: no key, and the budget proof does not hold |
| end to end | dry run: **160 events × 4 arms × 3 execution variants reconciled; ledger verified; report rebuilt offline and identical**; fail-closed on a missing row and a missing quote; resume after a crash with no request sent twice | inputs persisted and hashed for 160 control packs; 0 model packs (SEC material absent); no outcome computed |

A synthetic pass does not authorize spending.

## Commands (all offline)

```
.venv/bin/python -m pytest -q tests/test_phase5_paid_path.py tests/test_phase5_preflight.py     # 102 passed
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/                                  # 606 collected: 598 passed, 8 skipped (need Alpaca paper keys), 0 failed
.venv/bin/python -m autotrader synthetic SYN_DIR                    # synthetic cache, 160 real identities
.venv/bin/python -m autotrader devrun SYN_DIR DRY_OUT                # dry run: no model, exit 0 when reconciled
.venv/bin/python -m autotrader devreport DRY_OUT                     # rebuild the report from files, no call
.venv/bin/python -m autotrader preflight PHASE4_CACHE REAL_OUT       # real cache: inventory, inputs, budget proof
```

**The run command (only after the owner's approval; today it refuses):**

```
ANTHROPIC_API_KEY=… .venv/bin/python -m autotrader devrun PHASE4_CACHE OUT_DIR --paid
```

It is pinned to anthropic 2.7.0. With any other release, requests are recorded as not rendered and the proof
cannot hold. It takes no model, ceiling, contract or sample option, and it stops at three gates:
1. Without `dev-eval/APPROVAL.json` (owner-written, naming this experiment id and the amendment's digest) it
   refuses.
2. It matches the sample and builds every input before any client exists. With missing inputs it cannot pass the
   budget proof.
3. The budget proof itself cannot hold today (below).

**Dependencies:** Python 3.12.3 (project venv), anthropic 2.7.0 (pinned; API version 2026-10-21), httpx2 2.16.0,
pydantic 2.14.3, pandas 3.1.1, numpy 2.5.4, pytest 9.2.0.

## Budget proof

* **Registered maximum at base rates:** $0.078 per event and $12.48 for 160 events, leaving $0.02.
  * The output side is bounded: `max_tokens` 3,000 is a strict limit that includes all thinking, priced at $20/MTok.
  * The input side is not:
    * structured outputs inject a billed system prompt of undocumented size;
    * the tokenizer is private;
    * token counts are documented as estimates;
    * no verifiable, non-overshooting provider limit at or below $12.50 has been shown (the plan's $15 prepaid
      balance is above the ceiling).
* **Illustration on the synthetic requests:** counting one token per byte and treating the injected prompt as free,
  the run could cost up to **$13.03**, above the ceiling. Real filings are longer.
* **Real cache:** 0 of 160 requests can be built, so the run cannot be priced at all.
* **Proof:** `budget_proof.json`, `proven: false`. Experiment identity `0bb1808464011bd8aa56f10daef0d0b4b69270e049227d4a314744e3b9306e3f`.

## Inputs and hashes (real cache)

* Inputs manifest: `preflight/real-cache/inputs_manifest.json`, SHA-256 `9dee6d891533d80025d0b33a42bc8a0eb1e4b5b3eb7ebec567581bb3a11cf25e`.
  * For each event: input status, the control pack's SHA-256, and the digest of the persisted input file.
  * The persisted inputs stay on the host; they hold prices derived from licensed data.
* Inventory: `preflight/real-cache/preflight.json`, with yes/no flags, counts and hashes per event, and no prices.

## Findings

**Signed off (demonstrated here)**

1. The sample is exactly the registered one, and it matches the cache exactly. The replay now fails closed.
2. The cache's committed digests match. The 10:00 features and surprises reproduce from the as-of inputs.
3. The retrospective research note is removed from every historical prompt by the evidence amendment. With it, the
   look-ahead guard made the first 80 events unbuildable. The exact input diff is in `AMENDMENT-1.md`.
4. The budget journal, the pinned single-attempt client and their failure handling behave as specified under
   every tested fault.
5. The dry run reconciles 160 events in every arm and execution variant. It fails closed, and it reproduces offline.
6. A circuit-breaker skip is no longer recorded as a valid answer. A move condition without a prior close is
   rejected.

**Unresolved (each blocks or limits the paid run)**

1. **Budget proof does not hold** (above): **paid path BLOCKED**. Unblocking needs a demonstrated per-request
   bound, a demonstrated non-overshooting provider limit at or below $12.50, or a new registration. That is the
   owner's decision.
2. **SEC material is not in the cache:**
   * release exhibits 0/160;
   * headers 0/160;
   * company facts 11/160.

   Fetching it from EDGAR costs nothing but needs the owner's declared SEC contact. It would be a new acquisition
   with a new fingerprint, not the original phase-4 material. Until then the real-data preflight is BLOCKED for
   model inputs, and no price-only substitute is used.
3. **Three filings were accepted after 10:00** on the event day: SRE 2023-02-28, C 2024-01-12 and C 2025-04-15.
   These three will be `input_incomplete` whatever else happens, and count as unanswered against the 152 gate.
4. **No executable quotes:** the cache holds following-minute medians. Every fill, spread check and move check is a
   labelled proxy, so executable profitability cannot be verified without new data.
5. **Historical fees are unverified** for dates before 2025-05-14 (100 events): the SEC Section 31 rates and dates,
   the FINRA TAF by year, the start of Alpaca's CAT pass-through, and whether daily rounding applied then.
   www.sec.gov and www.finra.org could not be reached from this session. Six schedule rows carry `verified: false`.
6. Two overnight jumps of more than 25% (APP 2023-08-10 and AEO 2025-09-04) match no split ratio. They are treated
   as earnings moves and left unadjusted; they need review.
7. **Early exits, and stop and target fills, reuse the entry half-spread**, because the cache has no quote then.
   They are flagged on every trade.
8. **The model's training cutoff overlaps 144 events,** and the prompts name ticker and date: this is not an
   independent test. The 16 later events are reported separately, as descriptive only. Sector mappings use current
   SIC codes.
9. **The serial request queue is registered and kept.** With four requests per date, a later request in the queue
   may miss the 10:00:30 deadline: in the executable comparison it gets no fill. This follows from the design, not
   from any result, and concurrency is not changed.
10. The blinded claim review needs a person (rubric frozen; tooling and tests ready). The advancement screen needs
    the owner's freeze.

## Files

* `AMENDMENT-1.md`: the proposed amendment, with before and after for every change.
* `ADVANCEMENT-SCREEN.md`: the director's proposed screen, for the owner to freeze.
* `preflight/real-cache/`: the real-cache inventory and the inputs manifest (no prices, quotes or text).
* `preflight/synthetic-dry-run/`: the dry run's report, coverage table, results and budget proof (synthetic data
  only).
* Code:
  * `autotrader/experiment.py`, `journal.py`, `paid.py`, `execution.py`, `fees_hist.py`, `labels.py`, `review.py`,
    `devrun.py`, `devreport.py`, `inventory.py`, `synthetic.py`;
  * changes to `sources.py`, `deciders.py`, `risk.py`, `runner.py`, `providers.py` and `__main__.py`.
* Tests: `tests/test_phase5_paid_path.py`, `tests/test_phase5_preflight.py`.

# INVEST-PILOT-1: research capability readiness (2026-11-11)

**Verdict: READY FOR INDEPENDENT REVIEW** of the offline research capability. Genuine launch is a separate matter:
it is **not approved**, and **paid dispatch is BLOCKED** (`LAUNCH-MANIFEST.md`).

Started from `b116601edffbf63df94b65b0b2283ab72a7c4e36`. This report is committed last; the final commit SHA is
given in the hand-off message.

**Spend and access:** $0. No model call, paid API call, broker call, market-data download, SEC download, credential
read, order, approval file or forward clock. The provider's documentation was read without authentication. Every
test runs with sockets refused (`tests/invest_helpers.py`, module-scoped).

## What now exists

The pipeline can prepare a research request, record a fixture decision and score its defined outcomes, all without
external services:

| deliverable | where |
|---|---|
| offline pipeline: raw store with provenance, EDGAR, XBRL trailing values, passages, N-PORT and mapping review, prices and actions | `autotrader/invest/adapters/`, `autotrader/invest/pipeline.py` |
| decision contract v2: every candidate assessed, typed claims, scenarios, measurable invalidation, abstention, caps | `autotrader/invest/contract.py` |
| diagnostic scorer and shadow portfolio (secondary; cannot change the verdict) | `autotrader/invest/diagnostics.py`, `engine.py` (`forecast_outcome` records, shadow book), `report.py` |
| proposed registration amendment | `AMENDMENT-1.md` (`FROZEN` in `registration.py`) |
| launch manifest, runbook, documentation checks, budget | `LAUNCH-MANIFEST.md`, `autotrader/invest/budget.py` |
| end-to-end fixture demonstration | `dry-run/` (`python -m autotrader.invest dryrun OUT_DIR`) |
| real-source parser check | `real-source-check.json` (`python -m autotrader.invest real-check`) |

## Exact commands

```
python -m autotrader.invest dryrun OUT_DIR       # raw fixtures -> pipeline -> data -> fixture decisions -> ledger -> report -> request
python -m autotrader.invest budget OUT_DIR       # maximum-size request, token estimates, BLOCKED status
python -m autotrader.invest real-check OUT.json CACHE_DIR...
python -m pytest -o addopts="" -q tests/test_invest_pilot.py tests/test_invest_contract.py \
    tests/test_invest_adapters.py tests/test_invest_pipeline.py tests/test_invest_diagnostics.py --junitxml=JUNIT.xml
python -m pytest -o addopts="" -q -p no:cacheprovider --junitxml=JUNIT.xml        # full suite
```

## Hashes

| item | SHA-256 |
|---|---|
| proposed configuration (`config_sha256`) | `d2e857b796bca155afe773abdf714432c58f564905ff5abefb349f1ad535e080` |
| raw fixture manifest (`dry-run/raw-MANIFEST.json`) | `d96cec75a84645155c7a34e6748c0147a02d3f4156e2219da7f3d164b6c3f35c` |
| data manifest built by the pipeline | `bac5594ad646f0f946b4ae87a8a155fa6f8a088c19764826afcb69eff62e51ee` |
| demonstration ledger (`dry-run/ledger.jsonl`, 956 records) | `db0ce57aaa5b82351b38ad8b4ba081ab3278fe86b5360bf495b2dc3488a687d4` |
| `dry-run/report.json` | `bb0a32b543ac79563fe0640a3f43761cf44b77ea2f96b8a89d52daa96f7b96a2` |
| `dry-run/REPORT.md` | `5bfb155f2148ff547ab8517fdbf8fc83e1b5bed7650415b08e499516b10c6150` |
| rendered request, slot 1 (29,858 bytes; not sent) | `80324ac713e604e521a85feff31671787fb50ee3da53576a3d33869623da2286` |
| maximum-size request (81,796 bytes; not sent) | `f2fd1ffd960f560bf5f57e1cdc390d4fb1ac3e5595b241cc6d913dca86c0d919` |
| `real-source-check.json` | `e79bb562cfa18b4e8a9092a7cab6b7fc537e5b38595def250c16b1c912f39714` |

The code hash of every identity file and each pack's hash are in `dry-run/summary.json`.

## Demonstration (fixtures; NOT A RESULT)

* Verdict: **NOT A RESULT: dry run with synthetic data and fixture decisions.** The record is complete.
* 12 fixture decisions, 0 genuine:
  * 9 valid;
  * 1 invalid (four stocks);
  * 2 missed (no answer; late).
* Month 2 carries a claim that misstates its source ("Revenue fell N%" while quoting "up N%"). It passes the
  mechanical checks and is listed for claim review.
* Forecasts: 84 scored, 24 pending (slots 11–12), 0 missing.
  * The scores are meaningless by construction: fixture forecasts are placeholders. The Brier score is 0.2503,
    against 0.25 for a constant 0.5.
* Shadow book: ending wealth 89.39. Its VOO weight went from 70.01% at launch to 64.57% at its lowest, with 131
  rebalancing trades skipped under the $1 minimum.
* Ending wealth (synthetic prices): VOO 84.62; AI 85.40 before model costs and 83.12 all in.

## Deterministic replay and frozen records

* Two fresh runs into empty directories gave byte-identical results across every file: ledger, reports, summary,
  request, manifests, all 12 packs, the data directory and the raw store.
* The committed `dry-run/` matches a fresh run byte for byte. This is tested in
  `test_replay_is_deterministic_resume_appends_nothing_and_matches_the_committed_run`.
* `dryrun --resume` appends 0 records and leaves the ledger and packs unchanged. A changed price after a fill stops
  the run instead of rewriting it, and a frozen decision cannot be replaced (`test_invest_pilot.py`).
* Untouched: the desktop app (`stockpilot/`) and the archived Phase 5 code and records. Two `stockpilot` modules
  (`clock.py`, `research/events.py`) are now included in the run's code hash, but they were not edited. Only
  `autotrader/invest/`, its tests, this directory and the README changed.

## Real-source validation: what was and was not checked

* **Parsed and checked (offline):** SEC files that the earlier collectors had saved locally. Each file's URL is
  proven by its cache key, `sha256(url)[:24]`. No new download was made. Over 40 real companies:
  * 632 as-of points (every 10-K and 10-Q acceptance from 2023 on);
  * 0 facts public before their own period ends;
  * 43 filing headers reconciled with the submissions index, 0 disagreeing;
  * 175 of 177 independent cross-method checks agree. The two disagreements are Dominion Energy net income in
    2025, consistent with recast prior quarters: a mixed-vintage limitation of the four-quarter method, which the
    registered priority uses only when no annual or year-to-date path exists;
  * trailing values unavailable at 6 points (Reddit and Kenvue, just after their listing): labelled, not invented;
  * 323 restatements seen, each counted only from its own acceptance.
* **Committed real excerpts:** Starbucks (CIK 829224) company facts and submissions rows, `tests/fixtures/invest_real/`.
  Tested values:
  * the fiscal 2023 10-K gives revenue $35,975.6 million, net income $4,124.5 million and diluted EPS $3.58, which
    are the 10-K's own XBRL values;
  * the first-quarter fiscal 2024 trailing value is the fiscal year plus the quarter minus the prior quarter,
    $36,687.0 million, from the excerpt's rows;
  * the fiscal 2024 10-K is excluded one second before its acceptance.
* **Not checked:**
  * real filing documents for passage selection (only synthetic documents and rule tests);
  * real N-PORT XML;
  * any real price or corporate-action vendor file;
  * any comparison of values against a third-party source.
  * The real check is a consistency check of the parsers, not an independent verification of the numbers.

## Tests

* **Focused (investing pilot), 177 passed, 0 failed, 0 errors, 0 skipped** (JUnit):
  * `test_invest_pilot.py`: 42;
  * `test_invest_contract.py`: 81;
  * `test_invest_adapters.py`: 30;
  * `test_invest_pipeline.py`: 13;
  * `test_invest_diagnostics.py`: 11.
* **Full suite:** 783 tests collected (8 need Alpaca paper keys and are skipped without them). The run's result was still in progress when this file was first committed; it is recorded in the next commit of this file.

No test implies investment skill. Fixture decisions are scripted placeholders, and every record and report labels
them.

## Open items for the reviewer

* Paid dispatch is BLOCKED for want of a documented input-token bound or a non-overshooting spend limit
  (`LAUNCH-MANIFEST.md` §3). Choosing a mitigation is the owner's decision.
* At $100 the shadow portfolio barely rebalances (the $1 minimum). It is reported as it is.
* Section starts need a following paragraph of at least 200 characters, which may miss very short real sections.
  It falls back to the documented rule. Untested on real documents.
* Share counts summed across classes are deduplicated by value, so two classes with identical counts would be
  counted once (labelled).
* The four-quarter method can mix restated and unrestated quarters (the Dominion case). It is last in priority.
* Live collectors, the paid transport, the external timestamp and the host do not exist. See the manifest.

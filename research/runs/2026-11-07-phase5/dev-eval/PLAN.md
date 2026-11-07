# Fixed-cost model evaluation on development dates (proposed; nothing paid is active)

**Purpose:** find out whether a capable model can reliably produce valid, source-grounded decisions under output
contract v2, and whether the whole system scores them end to end, **before** any new forward evaluation.

The data is the phase-4 cache, which was examined in phase 4. The results are development, never evidence of a
trading edge.

## Recommended model

**Claude Opus 5.5**, model ID `claude-opus-5-5`.
* **Price:** $4 per million input tokens and $20 per million output tokens (Claude API table, October 2026; the
  same figures are in the app's `dkapp/ai/providers.py`).
* **Settings:**
  * structured outputs (`output_config.format`, a JSON schema);
  * effort `low` (thinking is always on for this model, and effort sets how much of it);
  * `max_tokens` 3,000;
  * at most 4,500 input tokens per call (a longer prompt is refused before any call).
* **No refusal fallback, on purpose.** A fallback would answer some events with a different model, and the
  evaluation must keep one model. A refusal, a cut-off answer or a different serving model counts as a model
  failure: the event becomes no trade and the cost is still counted.
* **Why this model:** the local 3B model failed the understanding test, and most of its failures were mechanical
  (`../audit/`). Opus 5.5 is the most capable general model offered at standard prices. A cheaper alternative
  (Claude Sonnet 5.5 at $2/$10) is the owner's call; any switch is a different registration.

## The sample (fixed before any model sees it)

`sample.json`:
* **160 earnings events on 40 trading days, 2023-01-13 → 2026-10-16.**
  * 80 from the development fold and 80 from the evaluation fold.
  * 40, 44, 40 and 36 events in 2023, 2024, 2025 and 2026.
* **Rule:** the examined period is cut into 40 equal spans of trading days. One day is drawn per span, then 4
  events on that day.
* Seed 20261107; SHA-256 `e825d6d63dc82480…` (full hash in the file).
* 2026-10-30 is excluded, because the demonstration already used it.
* Every event has executable quotes at 10:00:30 and 15:45, so each one can be scored.

## Spending ceiling: $12.50, enforced three ways

1. **By the program** (`autotrader/providers.py`, tested):
   * Before each call it reserves that call's worst case: 4,500 input tokens × $4/M + 3,000 output tokens × $20/M
     = **$0.078**.
   * It refuses any call that could cross $12.50.
   * It refuses to start at all if the whole sample's worst case exceeds the ceiling: 160 × $0.078 = **$12.48**.
   * The running total is stored on disk and survives restarts. The program cannot raise the ceiling of an
     existing budget file.
2. **By the account:**
   * Fund the Claude Console account with **at most $15 of prepaid credit, with auto-reload off**, so calls stop
     when the credit runs out.
   * If the Console offers a workspace spend limit, set it to $15 as well.
3. **By the key:** use a key for a dedicated workspace and revoke it after the run.

**Expected cost:** about 3,000–3,500 input and 800–1,200 output tokens per event, so **$0.03–0.04 per event and
about $5–7 in total.** The worst case is $12.48.

## API requirements

| requirement | detail |
|---|---|
| account | a Claude Console (API) account with prepaid credit, **separate from any Claude subscription** |
| credential | an API key for that account, held only on the machine that runs the evaluation, as `ANTHROPIC_API_KEY` in that shell or via `ant auth login`; never in a file, the repository or chat |
| endpoint | `api.anthropic.com`, Messages API with `output_config.format` (structured outputs) and `output_config.effort` |
| SDK | the `anthropic` Python package, already in `requirements.txt` (`>=2.0`; 2.6.0 installed here) |
| rate limits | 160 sequential calls; the entry usage tier is enough |
| other data | the SEC contact (`SEC_USER_AGENT`) and the phase-4 cache; **no market-data key is needed** |
| where it can run | anywhere the key stays private. In this cloud workspace the Anthropic API host bypasses the credential proxy, so a key saved as a network secret may not be applied here; check that before choosing this workspace |

## Command (to be run only after the owner approves the spend)

```
SEC_USER_AGENT="name email" python -m autotrader replay DATA_DIR OUT_DIR \
    --sample research/runs/2026-11-07-phase5/dev-eval/sample.json \
    --llm anthropic --model claude-opus-5-5 --budget-usd 12.50
```

## Reliability gate

All of these are required before proposing any new forward evaluation:

| check | pass |
|---|---|
| valid, grounded answers under contract v2 | ≥ 95% (≥ 152 of 160) |
| model failures (refusals, cut-offs, errors) | ≤ 2% (≤ 3) |
| reported GAAP EPS against the XBRL filed later | ≥ 90% correct, on ≥ 30 checkable events |
| end-to-end scoring | every event has a market outcome; every proposed trade is either rejected with a reason or simulated with an outcome |
| spend | ≤ $12.50 (guaranteed by construction) |

**Reported but not gated** (development only):
* the Brier score of its probabilities against the 0.490 base rate;
* simulated net return per trade against `no_trade`, `price_only` and the phase-4 rule.

## What happens next

* **If the gate passes,** the owner decides on:
  * a rotated market-data key;
  * where the unattended daemon runs;
  * a new forward registration (v2), which starts a new six-month clock.
* **If it fails,** the forward evaluation does not start, and the finding is recorded.

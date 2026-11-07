# Audit of the local model's answers: fabrication vs. formatting (2026-11-07)

**Model:** Llama 3.2 3B, local. Development data only.

**Method:** every answer is re-read leniently (`scripts/phase5_audit_answers.py`), including answers that failed
the schema and were therefore never grounding-checked. Each problem gets one class:

| class | meaning |
|---|---|
| **factual error** | a figure or quote that matches nothing in the evidence, a misread figure, or a figure from the wrong quarter |
| **misattributed** | real information under the wrong label: the wrong evidence item or sentence cited, billions entered as millions, a correct figure taken from the XBRL history instead of the release |
| **format only** | copying slips, reordered words, escaped apostrophes, length or range limits, truncated output |

The automatic classes were spot-checked by hand. The first pass overstated fabrication, and the checks were
corrected; the remaining limits are listed at the end. Files: `answers_audit_v1.json`, `answers_audit_v2.json`.

## Contract v1 (the model copies quotes)

All 40 answers in the committed ledgers: 30 replay, 10 forward.
* **10 valid. Of the 30 invalid:**
  * **2 genuine factual errors**, both a revenue figure from an earlier quarter reported as the current one
    (Kenvue, WEC);
  * 11 misattributed;
  * **16 format only**;
  * **1 correct answer wrongly rejected** by the v1 check (Mondelez: the figure was in a table without the word
    "million").
* **Most common v1 failures:**
  * 15 near-verbatim quotes;
  * 19 claim numbers taken from another passage;
  * 7 out-of-range fields in no-trade answers;
  * 5 quotes over 400 characters;
  * 4 answers truncated at the token limit.

## Contract v2 (the model cites numbered sentences; fixed trade choices)

Same 30 replay events as v1, same model:

| | v1 | v2 |
|---|---:|---:|
| valid | 8 | **2** |
| correct but wrongly rejected | 1 | 0 |
| format only (schema, truncation, length) | 12 | **1** |
| misattributed | 8 | 20 |
| genuine factual error | 1 | **7** |
| answers giving a probability | 20 | 29 |
| Brier score (constant base rate: 0.249) | 0.369 | 0.333 |

**What changed:**
* **v2 removed almost all formatting failures** (12 → 1), and fabricated quotes are impossible by construction.
* **The model then failed the stricter rules instead:**
  * claims citing the sentence next to the one with the number (44 instances);
  * revenue written as raw dollars or at the wrong scale (13 instances);
  * figures reported where the release excerpt states none (7 answers).

Hand-checked factual errors:

| company | reported | evidence |
|---|---|---|
| Roblox | revenue of $1,300 million | the excerpt states no revenue |
| Digital Realty | revenue of $1.587 billion | the excerpt states no revenue |
| Chevron | 9,240 and 12,272 | the release's 9,924 and 12,072, which are earnings, not revenue |

Asking for structured figures led this small model to supply figures it could not find. **Every one was caught;
no invalid answer can become a trade.**

## Recommendation

* **Keep contract v2 for a capable model.**
  * It is simpler to generate: no copying, fixed choices.
  * It is simpler and stricter to verify: every fact is a sentence id, every number is checked against the cited
    sentences, and every figure against a release sentence and its unit.
  * It never accepts a reworded quote.
  * Its effect on a capable model is **not yet measured**; the fixed-cost development evaluation
    (`../dev-eval/PLAN.md`) measures exactly that.
* **Do not use the 3B model as the researcher under either contract.** Its valid-answer rate (27% under v1, 7%
  under v2) and its probabilities, worse than a constant, rule it out.
* **Possible v2.1 adjustments if a capable model shows the same patterns:**
  * an explicit "not stated" option for each reported figure;
  * a narrow exemption for calendar numbers (a year, or a quarter number when the cited sentence names that
    quarter).
  * Neither is adopted now: each would be a new version, decided on evidence, not on one small model.

## Audit limits

* The classifier is heuristic.
* "Derived" numbers (sums, rescaling, rounding, date parts) are treated as format, not fabrication.
* A figure absent from the 3,500-character release excerpt may exist later in the full release. It is still
  ungrounded, because the model only saw the excerpt.

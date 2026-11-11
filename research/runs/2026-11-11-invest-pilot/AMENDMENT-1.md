# INVEST-PILOT-1, amendment 1 (proposed, 2026-11-11)

**Status: proposed for independent review. Not approved, not frozen; the forward clock has not started; no spend is
approved.** Registration revision 2 (`REGISTRATION.md`) is itself still proposed. Nothing in it is approved, and
this amendment does not change what it says. Each change below is listed against revision 2 and takes effect only
if the owner approves this amendment together with the registration.

The values live in `autotrader/invest/registration.py` (`FROZEN["contract"]`, `FROZEN["pipeline"]`,
`FROZEN["diagnostics"]`, `FROZEN["model"]["request_fields"]`), so they are part of each ledger's identity
(`config_sha256` `d2e857b796bca155afe773abdf714432c58f564905ff5abefb349f1ad535e080` at this amendment).

## Unchanged

* The model: Claude Opus 5.5 (`claude-opus-5-5`). One request a month, no retries, no fallbacks, no tools, no
  debates, no critic call and no browsing.
* Twelve months. Two $100 paper portfolios.
* The AI portfolio: 70–100% VOO, at most three stocks, each with a target of at most 10%. The universe rule: 12
  companies, frozen before launch.
* The primary outcome rule and every verdict. After 12 complete months, all-in AI wealth at or below VOO means
  **stop this version**.
* The project's $12.50 overall ceiling. No spend is authorized.

## What the amendment does not do

* It adds no technical-indicator triggers, no ranking written in code, and no scripted "AI approval". Every stock
  choice, weight and assessment stays the model's own. Code only accepts or rejects an answer whole.
* It mines no historical performance. Code was tested only on synthetic fixtures and, for parsing, on cached SEC
  files.
* It adds secondary diagnostics that **cannot change the primary verdict**. They cannot rescue a failed primary
  result, they select no subperiods, and none of them can be promoted to the outcome.

## A1. Decision contract v2 (`autotrader/invest/contract.py`)

Revision 2 asked for a reason, a downside and an invalidation for each pick. Version 2 makes the model's judgment
testable:

* **Every eligible candidate and held stock is assessed, rejected names included.** Each assessment gives:
  * `rank`: an integer, 1 = most attractive, ties allowed;
  * `expected_excess_pct_3m`: expected total return minus VOO's over the next three monthly holding periods, in
    percentage points, within [-100, 100];
  * `p_positive_excess`: the probability that this excess is positive, as a fraction in [0, 1]. A percentage
    such as 55 is rejected.
* **Every stock kept (buy, increase, hold or reduce) states:**
  * the perceived mispricing;
  * how the current price and valuation relate to it;
  * the strongest contrary evidence;
  * the downside;
  * a measurable invalidation: metric, comparator, number, unit, and a check date within 400 days;
  * the weight rationale;
  * base, upside and downside three-month returns, with downside <= base <= upside and a reason for each.
  * A hold also says why the thesis still holds. A reduce or a sale says what changed.
* **Claims are typed, and the types are kept apart:**
  * `fact`: verbatim quotes; every number in a figure must appear in its quote;
  * `calculation`: inputs taken from a cited item whose text contains the value (or labelled `assumption`), an
    expression using only + - * / and parentheses, and a result that code recomputes;
  * `assumption` and `forecast`: the model's own; they may not carry citations;
  * `inferred_expectation`: what the price seems to imply. It is the model's inference: it may cite only
    valuation rows, and it may never be presented as an analyst or consensus view.
  * A buy or increase must use its valuation row in a calculation.
* **Abstention:** when no stock remains, `prefer_voo_reason` is required.
* **Compact, to fit the single request's output allowance:** text fields at most 240 characters; scenario
  reasons and claim text 160; quotes 200; notes 100. At most 4 claims per stock, 4 inputs per calculation and 2
  citations per claim.
* **Consistency notes:** a kept stock that the model's own assessment rates at or below VOO is recorded as a
  signal. It is never enforced.

These checks verify form and quotation mechanics only. A claim can pass every check and still misstate its source:
the fixture's month-2 "Revenue fell N%" claim quotes "up N%". So the blinded claim review stays, and it reviews
facts, calculations and inferred expectations.

## A2. Offline data pipeline (`autotrader/invest/adapters/`, `pipeline.py`)

* **Raw store:** every saved source response keeps its URL, retrieval time and SHA-256. A file whose bytes changed
  is refused. Transports are injectable. The only working transport reads the store; any live transport refuses
  (no network collection exists in this version).
* **EDGAR:**
  * submissions pages are merged, and acceptance times are converted from UTC to Eastern time;
  * a filing counts only when its header agrees with the index on accession, issuer CIK, acceptance time (to the
    second) and form;
  * documents come from the header; an 8-K Item 2.02 is read from its EX-99.
* **XBRL facts:** each fact keeps its period apart from its public time, which is the acceptance time of the
  accession that reported it. A fact whose accession is not in the index, or whose unit is wrong, is excluded and
  counted.
* **Trailing four quarters, from facts public by the time:**
  1. the annual value; else
  2. the fiscal year plus year-to-date minus the prior year-to-date; else
  3. four reported quarters.
  * Dates match within 7 days, for 52/53-week years.
  * A cumulative year-to-date value is never treated as a quarter.
  * A restatement counts only from its own acceptance, so it can never enter an earlier pack.
  * Diluted EPS by this arithmetic, and share counts summed across classes, are labelled as approximations.
  * When the facts cannot support a value, the filing is labelled unavailable (not invented).
* **Passages:** chosen by a fixed rule from the document's own paragraphs, never reworded:
  * two MD&A paragraphs with figures;
  * the first adverse disclosure, found by a keyword priority (going concern, material weakness, restatement,
    impairment, litigation, decline, decrease, loss, risk);
  * the first risk-factor paragraph.
  * A section starts at a heading followed by a paragraph of at least 200 characters, which skips a table of
    contents. A shorter section start falls back as documented.
* **Universe:**
  * the latest NPORT-P of the series accepted before the freeze;
  * a review table that proposes a symbol only from the filing's own ticker identifier, and a CIK only from
    EDGAR's ticker list. Anything else is `unresolved`;
  * `universe.json` is written only from a review that approves every row.
* **Prices and actions:**
  * unadjusted bars only. A split must show a price step at its ex-date; a series without one looks already
    adjusted and is refused (no double adjustment);
  * symbol changes are merged;
  * any other action kind (merger, spin-off, delisting...) is listed as unsupported. It becomes a data gap for a
    held stock and a reproducible exclusion for a candidate.

## A3. Secondary diagnostics (`autotrader/invest/diagnostics.py`; frozen before any genuine answer)

* **Forecast outcomes:**
  * For each valid decision in slot k, every assessed candidate is scored from the open of slot k's fill session
    to the close of slot k+2's last session.
  * Total return per share bought at that open: `(close x splits + dividends after the fill day x splits at
    ex-date) / open - 1`, with no costs and dividends not reinvested.
  * Excess is in percentage points over VOO on the same dates.
  * A missing price leaves the forecast missing. Slots 11 and 12 mature after the fixed end and stay pending.
* **Scores, computed in code:**
  * outcome y = 1, 0, or 0.5 for an exact tie;
  * the Brier score, against the constant-0.5 reference of 0.25;
  * calibration in fixed bins [0, .2) [.2, .4) [.4, .6) [.6, .8) [.8, 1];
  * a Spearman rank correlation per decision (average ranks; at least 3 scored forecasts);
  * selected versus rejected mean excess;
  * forecast error, with sample counts throughout.
  * Horizons overlap and companies recur, so observations are not independent and no significance is claimed.
* **Shadow portfolio:** 70% VOO and 2.5% in each of the 12 candidates, rebalanced toward those targets at each fill
  under the same execution convention. At $100 a 2.5% position is $2.50, so most rebalancing trades fall under the
  $1 minimum and are skipped. The shadow drifts (66.4% VOO by June in the fixture), and the report shows its VOO
  weight after each fill. A shadow data gap never touches the primary record.
* **Three results reported separately:** factual accuracy (the blinded claim review), forecast accuracy (the
  scores above) and wealth (the primary outcome). Only wealth decides the verdict.

## A4. The request

* `service_tier` is removed. The provider's service-tiers page calls the tiers legacy, and it lists no Priority Tier
  for Claude Opus 5.5. `standard_only` only matters with a Priority Tier commitment, and the page does not list Opus
  5.5 for the parameter (read 2026-11-11).
* The request sends only these fields: `model`, `max_tokens` 16,000, `system`, `messages`,
  `output_config {effort: "medium", format: json_schema}` and `inference_geo: "global"`.
  * There is no `thinking` field: adaptive thinking is always on for this model.
  * There are no tools, no fallbacks and no retries.
* A refusal (`stop_reason: "refusal"`) or a cut-off (`max_tokens`) can break the schema. Either one is a missed or
  rejected month, and its charge still counts.

## A5. Run identity

* The ledger identity's code hash now also covers `autotrader/invest/adapters/*.py`, `stockpilot/clock.py` and
  `stockpilot/research/events.py`.
* The offline demonstration now runs raw source-format fixtures through the pipeline before the run:
  `python -m autotrader.invest dryrun OUT_DIR`.

## Revised cost estimates (estimates, not bounds)

At every cap, the rendered request is **81,796 bytes**, an estimated **20,449–32,718 input tokens** (2.5–4 bytes
per token). Output is bounded by `max_tokens`: 16,000 tokens, which is **$0.32** at $20 per million. So a request at
the caps is estimated at **$0.40–0.45**, and 12 such requests at **$4.82–5.41**. A typical month is smaller (the
fixture month-1 request is 29,858 bytes). These are estimates: nothing bounds billable input, so paid dispatch stays
**BLOCKED** (`LAUNCH-MANIFEST.md`).

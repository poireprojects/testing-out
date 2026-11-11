# INVEST-PILOT-1: forward paper-only investing pilot (proposed registration, revision 2, 2026-11-11)

**Status: proposed. Not approved, not frozen; the forward clock has not started. No spend is approved.** Nothing
here places an order, calls a model, reads a credential or spends money. The frozen values live in
`autotrader/invest/registration.py` (`FROZEN`); their SHA-256 (`config_sha256()`) and the SHA-256 of every file of
the pilot's code are part of each ledger's identity, so a change after launch cannot be resumed silently.

## Changes in revision 2 (proposed for independent review; not approval)

Revision 1 was audited at `7862166`. These corrections change proposed rules; none is approved. The model, the
universe rule, capital, stock limits, monthly frequency, duration and outcome threshold are unchanged.

| # | revision 1 | revision 2 (proposed) |
|---|---|---|
| 1 | A candidate with no collected filing was just "not eligible", so missing downloads could look like a complete record. | Every candidate and held stock gets a coverage record against a collected EDGAR index. A collection failure means no pack, no model request, a `missing_input` month and an inconclusive record. Exclusion needs a reproducible reason. |
| 2 | Every reduction below $1 was skipped, even when the stock was above the 10% cap. | A reduction of a stock more than 0.05 points above the cap at the open is risk-required and sells regardless of the $1 minimum. Intended, executed and resulting weights are recorded. |
| 3 | A crash between the decision and cost records lost the charge, and a crash after asking could ask again. | Append-only `pack`, `request`, `answer`, `decision`, `cost` steps; resume repairs derived records without asking again. Unknown billing stays unresolved until a separate `reconcile` record. One writer per state directory (lock); fsync on every append. |
| 4 | Cost "ceiling about $0.42" and "at most about $5". | Removed: nothing enforces a bound. Estimates only; the project's $12.50 overall ceiling stays in force unless Riley replaces it. |
| 5 | "Fresh approval each time" for the SEC contact. | A scoped authorization that lasts until revoked or until its scope changes. |
| 6 | Citation checks were described as evidence checks. | Stated as quotation mechanics only, plus a blinded human claim review for genuine decisions. |
| 7 | Statuses `valid`, `invalid`, `late`, `missed`. | `valid`, `invalid_answer`, `missed_answer` (no answer, late, lost in flight, not requested), `missing_input`; charges `reconciled`, `not_requested`, `unreconciled`. A favorable verdict needs at least one valid genuine decision. |

## Question

Do monthly AI stock selection and position sizing leave Riley with more money than buying VOO with the same
dollars on the same dates?

## Portfolios

* **`voo`:** $100 on the first fill session; buys and holds VOO.
* **`ai`:** $100 on the same session; 70–100% VOO and at most three individual stocks, each set to at most 10% of
  the portfolio when its target is set. Long only, fractional shares, no further deposits.
* **Both:** residual cash, dividends, splits and fees are accounted to the cent (Decimal; 9-decimal shares).
* **Parity:** both portfolios run through one set of functions, so a 100%-VOO AI portfolio equals the benchmark to
  the cent before model costs. This is tested, with dividends and a VOO split.

## Candidate universe (selected once, before launch)

* **Rule:** the 12 largest common-stock positions by value in the most recent N-PORT-P report of Vanguard 500 Index
  Fund (VOO is its ETF share class) that is public on EDGAR before the freeze.
* **One class per issuer:** an issuer with two classes keeps the one with the larger position.
* **Ties:** broken by CUSIP.
* **Mapping:** N-PORT lists CUSIPs, so each CUSIP's symbol and CIK is checked by a person against EDGAR's company
  list as of the N-PORT report date (point in time, not today's list).
* **Frozen:** the list is fixed for all 12 months, and its file's SHA-256 is in the ledger identity
  (`autotrader/invest/universe.py`).

## Monthly decision

**Schedule** (12 slots; `registration.schedule`):

| step | when |
|---|---|
| cutoff | close of the last NYSE session of the month before each fill month |
| freeze deadline | open of the next session (the fill session) |
| fill | that open |

**Research inputs and coverage** (`data.MarketData.coverage`, `pack.build_pack`). For every universe member and every
held stock, the pack records:
* what EDGAR's submission index (collected at or after the cutoff, never derived from the downloaded filings) says
  was required: the newest 10-K or 10-Q accepted by the cutoff, and any later 8-K Item 2.02;
* whether each was collected, with its text and, for the periodic report, its XBRL facts;
* whether the cutoff close was collected.

Each symbol is `complete`, `unavailable` or `collection_failure`:
* `unavailable` needs a reproducible reason: the index lists no 10-K or 10-Q by the cutoff, or the price source
  records no trading that session;
* any `collection_failure` stops the month: no pack is frozen, **no model is asked**, the month is `missing_input`,
  and the record stays inconclusive. Portfolio marks and the benchmark continue.

A month that misses its deadline for want of data is never backfilled. A frozen pack is read back from its file
and never rebuilt from data downloaded later.

**The pack.** Built at or after the cutoff, and it refuses any item that became public after the cutoff:
* the AI portfolio at the cutoff close, and the thesis recorded for each stock it holds;
* for every complete candidate, up to 4 passages (each at most 1,200 characters) from its newest filings accepted
  by the cutoff;
* a valuation row computed by code: cutoff close, shares outstanding, trailing-four-quarter revenue, net income and
  diluted EPS from the newest periodic report's XBRL facts, market value, P/E, price/sales.

**The decision.** The AI chooses, for every stock, buy, increase, hold, reduce or sell, or 100% VOO. For each pick it
records:
* why it should outperform VOO at the current price;
* the filing passages and figures supporting that view, plus its valuation row;
* the main downside;
* what would invalidate the thesis;
* why the chosen weight makes sense.

A hold says why the thesis still holds; a reduce or sale says what changed. **A valid 100%-VOO answer from a fully
collected pack is a legitimate decision** (an abstention from stocks), unlike a `missing_input` month, where nothing
was asked.

**Code enforces** (`contract.py`), accepting or rejecting the decision whole and never editing it:
* the limits;
* every held stock is addressed;
* only eligible candidates are bought;
* quotes are verbatim, and every number in a figure appears in its quote.

**These checks verify quotation mechanics, not financial correctness.** A thesis can quote "revenue rose 5%" and argue
from a decline, mix quarters with years, confuse millions with billions, or misstate its own valuation row, and still
pass. So in a genuine run every claim cited by a valid decision gets a **blinded claim review** (`claims.py`):
* a person sees the cited text, the quote, the figures and the thesis statements, and nothing about the action, the
  weights, later prices or outcomes;
* the rubric is subject, direction, period, GAAP vs adjusted, sign, unit and valuation;
* each verdict (supported, unsupported, cannot judge, with the failed criteria) is its own append-only ledger record;
* a review never edits the frozen decision, never changes its trades and never removes its trading outcome, and
  unsupported claims stay listed in the report;
* the record is complete only when every such claim is reviewed.

## Statuses (the same in the ledger and every report)

| decision | meaning | trades |
|---|---|---|
| `valid` | an answer frozen before the deadline that passed every check (100% VOO included) | as decided |
| `invalid_answer` | an answer that broke a rule | none; holdings kept |
| `missed_answer` | no usable answer by the deadline: none, late, lost with the request in flight, or never requested | none; holdings kept |
| `missing_input` | research inputs incomplete: no request was made | none; holdings kept; record inconclusive |

| charge | meaning |
|---|---|
| `reconciled` | the amount is known from charge evidence (in the dry run, the fixture's labelled estimate) |
| `not_requested` | no request record exists, so nothing can have been billed |
| `unreconciled` | a request was made but its charge is unknown. Never counted as zero; the record stays inconclusive until a separate `reconcile` record supplies provider evidence. |

## Timing, fills and costs (both portfolios)

**Freeze, timestamps and recovery** (`engine.py`, `ledger.py`). Each month writes append-only records, each
fsync'd before the next step:
1. `pack|k`, after the pack file is written atomically;
2. `request|k`, written *before* the decider is asked;
3. `answer|k`, exactly as returned, with its charge evidence;
4. `decision|k` and `cost|k`, derived from the frozen pack and the answer.

**After a crash:**
* resume derives any missing decision or cost record and never asks again;
* a `request|k` with no `answer|k` means the run stopped with the request in flight: the month is a `missed_answer`
  (lost in flight), it is not resent, and its charge stays `unreconciled`;
* a final line torn by a crash was never acknowledged, so its bytes are set aside and a `recovery` record says so;
* any other malformed or altered record refuses the ledger.

**Other rules:**
* only one run may write a state directory at a time (an exclusive lock that is released when the process ends);
* a decision frozen at or after the open is late; none by then is missed;
* late, missed, invalid and missing-input months keep the existing holdings, and a missing month-1 decision means
  100% VOO;
* in forward mode the run uses the wall clock, and each month's pack and decision hashes are committed and pushed
  before the open, so GitHub gives an independent timestamp.

**Fill and cost convention:**

| item | rule |
|---|---|
| fill price | the session's official opening price; buy at open × (1 + s), sell at open × (1 − s) |
| s | 5 bps for VOO, 10 bps for stocks |
| commission | $0 |
| fees | the app's schedule (`stockpilot/risk/fees.py`): SEC Section 31 and FINRA TAF on sales, CAT on every share; each type summed per portfolio per session and rounded up to a cent once. The rates used are written into every fill. |
| buys | whole-cent cash amounts |
| sales | share quantities, with proceeds truncated to a cent |
| small trades | discretionary trades below $1 are skipped and recorded as skipped; full sales and risk-required reductions never are |

**Paper-model convention, not executability.** Quantities are sized from the opening price and filled at that price
plus slippage. This does not show that fractional orders could have been executed in the opening auction at that
price. Every report states this limitation.

**Limits versus drift:**
* **Cutoff weights** (what the AI saw) decide whether a hold is allowed. A stock above 10% at the cutoff cannot be
  held.
* **Targets** are shares of the portfolio's value at the fill session's opening prices, before trades.
* **Resulting weights** are measured at the opening prices after trades and fees, so they may sit above a target by
  the trading costs' share. That deviation is recorded.
* **Drift:** between decisions weights move with prices. A hold never trades, and a missed, late, invalid or
  missing-input month never trades, even when drift has taken a stock above the cap. The report shows the largest
  weight at any close and the number of sessions above the cap.
* **Risk-required reductions:** a reduce of a stock more than **0.05 percentage points** above the cap at the open
  sells to its target whatever the amount. The 0.05 is the rounding tolerance. The resulting weight must be at most
  10.05%, and anything left above is flagged.
  * Example: 0.21 shares at $50 in a $100 portfolio is 10.5%. Reducing to 10% sells 0.01 shares for $0.49, pays
    $0.03 in fees and leaves 10.004%.
* **Recorded per trade:** the cutoff, opening and resulting weights, the order (or why none), and the deviation.

**Order within a session:**
1. corporate actions;
2. stock sales;
3. stock purchases;
4. VOO, which is sold to fund the purchases or bought with all remaining cash of at least $1 (the one sweep rule
   that also invests the launch money and dividends in both portfolios);
5. fees.

**Corporate actions:**
* **Dividends:** the entitlement is set on the ex-date from the shares held before that session's trades, and the
  cash is credited before the open of the first session on or after the pay date. Unpaid dividends count in
  wealth.
* **Splits:** shares are multiplied by the ratio before the ex-date's open.

**Never invented or dropped:**
* a missing price is recorded as a data gap, and that portfolio stops there until the price exists;
* a later run then resolves the gap and fills at the actual open;
* a gap is never filled in or skipped.

A price that changed after a fill was recorded stops the run (`LedgerConflict`) instead of rewriting history.

## Model and cost

| | |
|---|---|
| model | Claude Opus 5.5, `claude-opus-5-5` (not the local 3B model, which failed earlier tests) |
| request | one per month to `api.anthropic.com/v1/messages`; structured output (`output_config.format`, `contract.json_schema`); effort `medium`; `max_tokens` 16,000; `service_tier` standard only; `inference_geo` global; no tools, no fallback model, no retries |
| failure | a refusal, cut-off, error or invalid answer is a missed or rejected month, and its charge is still counted |
| price | $4 per million input tokens, $20 per million output tokens, thinking included (provider pricing page, read 2026-11-10) |
| charge | from the provider's reported usage, to the AI portfolio, **every month including months it buys nothing**; unknown billing stays `unreconciled` |

**Spending: none is approved.**
* This pilot has no budget of its own.
* The only spending ceiling on record is the project's **$12.50 overall ceiling**, which stays in force unless Riley
  explicitly replaces it.
* No per-request or per-month bound is enforced by code today. `max_tokens` limits output tokens only, and nothing
  demonstrates a bound on billable input tokens. A paid client must enforce the ceiling before it may exist.

**Estimates only (not bounds, not approvals):**
* **Input:** about 17,000–26,000 tokens per month, or $0.07–0.10. At the caps the pack is about 70,000 characters,
  plus the undocumented structured-output prompt.
* **Output:** usually 3,500–11,000 tokens, or $0.07–0.22.
* **Total:** about $0.14–0.32 a month (central $0.20); about $1.70–3.90 over 12 months (central $2.40).

That is a hurdle of roughly 2–4% of the $100. With at most 30% in stocks, the stock sleeve must beat VOO by roughly 8
percentage points a year just to cover a $2.40 research bill.

The rendered request for any frozen pack is printed by `python -m autotrader.invest request STATE_DIR SLOT`, offline.

## Data sources (for a genuine run)

* **Filings, acceptance times and the submission index:** SEC EDGAR, as submissions JSON (`acceptanceDateTime`, UTC,
  converted to ET), filing documents and XBRL company facts.
  * EDGAR asks for a declared contact. Riley's authorization to use their contact for EDGAR access is **scoped**: it
    covers this pilot's EDGAR collection and lasts until Riley revokes it or the scope changes (another site,
    purpose or project).
* **Universe:** the same fund's N-PORT-P on EDGAR.
* **Prices and corporate actions:** an owner-approved source of official daily opens and closes, dividends (ex and
  pay dates) and splits. One candidate is Alpaca market data with a **newly issued** key; the exposed key that
  starts with PKJB7U is never used.
  * Vendor terms may forbid committing prices. In that case the full ledger stays local and only the monthly pack
    and decision hashes are committed.
* **Fees:** the app's schedule. It must be checked against primary sources and updated when the SEC's FY2027 rate is
  published; the fill records show which rates applied.

## Outcome and stop rule (fixed before launch)

**Reported separately:**
* ending wealth;
* dollar and percentage-point excess return over VOO;
* maximum drawdown (daily closes, and all-in for the AI portfolio);
* turnover (the lesser of purchases and sales over average month-end wealth);
* trading costs (fees, and the modeled spread and slippage);
* model costs;
* decision and charge statuses, coverage problems, drift above the cap, risk-required reductions, skipped trades,
  and claim-review results.

**All-in wealth** is AI wealth minus every known model charge grown at the voo portfolio's return from the charge's
cutoff to the end. Performance is shown before and after model costs.

**Verdicts, in order:**

| record | verdict |
|---|---|
| fixture decisions or synthetic data | **not a result** |
| incomplete: a gap, pending fill, `missing_input` month, unreconciled or missing charge, missing month, frozen-pack problem or unreviewed genuine claim | **inconclusive** |
| 12 complete months, all-in wealth at or below VOO | **stop this version** |
| above VOO but no valid genuine decision | **inconclusive** (zero genuine decisions never support a favorable conclusion) |
| 12 complete months, all-in wealth above VOO, at least one valid genuine decision | **independent review of a longer paper test** |

A positive 12-month result from one path is not evidence of skill.

## Limitations

* Twelve monthly decisions on one path have no statistical power. Luck dominates.
* At $100, cent rounding of fees is material: a single small rebalance can cost $0.03 in fees.
* Fills follow a paper-model convention at the opening price; they do not show executable fractional opening-auction
  trades.
* The candidates are VOO's largest holdings, so picks are overweights of stocks VOO already holds.
* Taxes, withholding and settlement are ignored.
* A single model, with no fallback.
* If the run stops while a request is in flight, that month's answer is lost. It is never resent, and its charge
  must be reconciled by hand.

## Prelaunch checklist (nothing here is verified by offline tests or mocks)

| item | status |
|---|---|
| independent review of this registration and the code | pending |
| EDGAR adapter: submission index, filing documents, passage selection, XBRL facts, with provenance (URLs, retrieval times, hashes) | not written, not verified |
| filing and XBRL provenance checked against EDGAR for a sample of filings | not verified |
| N-PORT universe builder and a point-in-time CUSIP→symbol/CIK map, reviewed by a person | not written, not verified |
| price and corporate-action adapter (official opens and closes, dividends, splits, no-trading exceptions) | not written, not verified |
| fee schedule checked against SEC and FINRA primary sources for the pilot dates | not verified for 2027 onward |
| paid client: pinned request, one request per month, journal before dispatch, never resent, usage capture, reconciliation, enforcement of the approved ceiling | not written, not verified |
| owner approval and freeze: first fill month, `FROZEN["status"]`, new `config_sha256`, dated approval written by the owner | not given |
| model spend: an account, a key kept on the job's machine only, and explicit approval of an amount within the $12.50 ceiling | not given |
| market-data source and a newly issued key; whether prices may be committed | not chosen |
| a host that runs between the cutoff close and the next open and pushes the freeze commit | not chosen |

## Offline dry run (synthetic data, fixture decisions)

```
python -m autotrader.invest dryrun OUT_DIR
```

**What it uses:**
* invented companies and prices on a simulated 2031 calendar;
* a synthetic EDGAR index;
* twelve scripted fixture months: buy, increase, hold, reduce, sell, 100% VOO, a missing answer, a late answer and
  a rejected four-stock answer.

Its record is in `dry-run/`. The verdict is always **NOT A RESULT**: fixture decisions are labelled in every record
and report, and say nothing about AI stock selection.

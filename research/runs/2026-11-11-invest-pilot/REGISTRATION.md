# INVEST-PILOT-1: forward paper-only investing pilot (proposed registration, 2026-11-11)

**Status: proposed. Not approved, not frozen; the forward clock has not started.** Nothing here places an order,
calls a model, reads a credential or spends money. The frozen values live in `autotrader/invest/registration.py`
(`FROZEN`); their SHA-256 (`config_sha256()`) and the SHA-256 of every file of the pilot's code are part of each
ledger's identity, so a change after launch cannot be resumed silently.

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
  list.
* **Frozen:** the list is fixed for all 12 months, and its file's SHA-256 is in the ledger identity
  (`autotrader/invest/universe.py`).

## Monthly decision

**Schedule** (12 slots; `registration.schedule`):

| step | when |
|---|---|
| cutoff | close of the last NYSE session of the month before each fill month |
| freeze deadline | open of the next session (the fill session) |
| fill | that open |

**The pack.** Built at or after the cutoff (`pack.py`), and refuses any item that became public after the cutoff:
* the AI portfolio at the cutoff close, and the thesis recorded for each stock it holds;
* for every candidate, up to 4 passages (each at most 1,200 characters) from its newest 10-K, 10-Q and 8-K
  (Item 2.02) filings accepted by EDGAR at or before the cutoff;
* a valuation row computed by code: cutoff close, shares outstanding, trailing-four-quarter revenue, net income and
  diluted EPS from the newest filing's XBRL facts, market value, P/E, price/sales.

**The decision.** The AI chooses, for every stock, buy, increase, hold, reduce or sell, or 100% VOO. For each pick it
records:
* why it should outperform VOO at the current price;
* the filing passages and figures supporting that view, plus its valuation row;
* the main downside;
* what would invalidate the thesis;
* why the chosen weight makes sense.

A hold says why the thesis still holds; a reduce or sale says what changed.

**Code enforces** (`contract.py`), accepting or rejecting the decision whole and never editing it:
* the limits;
* every held stock is addressed;
* only eligible candidates are bought;
* quotes are verbatim and every number in a figure appears in its quote.

A held stock that has drifted above 10% must be reduced or sold.

## Timing, fills and costs (both portfolios)

**Freeze and timestamps:**
* each pack and decision is hashed and written to the keyed, append-only, hash-chained ledger
  (`autotrader/invest/ledger.py`) before the fill session's open;
* a decision frozen at or after the open is **late**; none by then is **missed**;
* late, missed and rejected decisions keep the existing holdings, and a missing month-1 decision means 100% VOO;
* in forward mode the run uses the wall clock, and each month's pack and decision hashes are committed and pushed
  before the open, so GitHub gives an independent timestamp.

**Fill and cost convention:**

| item | rule |
|---|---|
| fill price | the session's official opening price; buy at open × (1 + s), sell at open × (1 − s) |
| s | 5 bps for VOO, 10 bps for stocks |
| commission | $0 |
| fees | the app's verified schedule (`stockpilot/risk/fees.py`): SEC Section 31 and FINRA TAF on sales, CAT on every share; each type summed per portfolio per session and rounded up to a cent once. The rates used are written into every fill. |
| buys | whole-cent cash amounts |
| sales | share quantities, with proceeds truncated to a cent |
| small trades | rebalancing trades below $1 are skipped; full sales never are |

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

**Retries:**
* a rerun of a month adds nothing;
* a frozen decision is read back, never asked for again;
* a price that changed after a fill was recorded stops the run (`LedgerConflict`) instead of rewriting history.

## Model and cost

| | |
|---|---|
| model | Claude Opus 5.5, `claude-opus-5-5` (not the local 3B model, which failed earlier tests) |
| request | one per month to `api.anthropic.com/v1/messages`; structured output (`output_config.format`, `contract.json_schema`); effort `medium`; `max_tokens` 16,000; `service_tier` standard only; `inference_geo` global; no tools, no fallback model, no retries |
| failure | a refusal, cut-off, error or invalid answer is a missed or rejected month, and its cost is still charged |
| price | $4 per million input tokens, $20 per million output tokens, thinking included (provider pricing page, read 2026-11-10) |
| charge | from the provider's reported usage, to the AI portfolio, **every month including months it buys nothing**; a charge not reconciled with the provider's usage report leaves the month incomplete |

**Estimated cost per month** (an estimate, not a bound):
* **Input:** about 17,000–26,000 tokens, $0.07–0.10. At the caps the pack is about 70,000 characters (12 × 4 × 1,200
  of passages, valuation rows, holdings, instructions and schema), plus the undocumented structured-output prompt.
* **Output:** usually 3,500–11,000 tokens, $0.07–0.22. The maximum is 16,000 tokens, $0.32.
* **Total:** about **$0.14–0.32 a month**, central estimate $0.20, ceiling about $0.42.

**Over 12 months** that is **about $1.70–3.90 (central $2.40) and at most about $5**: a hurdle of roughly 2–4% of the
$100. With at most 30% in stocks, the stock sleeve must beat VOO by roughly 8 percentage points a year just to cover
a $2.40 research bill. The registration keeps that hurdle visible rather than hiding it.

The rendered request for any frozen pack is printed by `python -m autotrader.invest request STATE_DIR SLOT`, offline.
In the synthetic dry run it is 20–25 KB, because its passages are short.

## Data sources (for a genuine run)

* **Filings and acceptance times:** SEC EDGAR, as submissions JSON (`acceptanceDateTime`, UTC, converted to ET),
  filing documents and XBRL company facts. EDGAR asks for a declared contact; the owner approves its use each time.
* **Universe:** the same fund's N-PORT-P on EDGAR.
* **Prices and corporate actions:** an owner-approved source of official daily opens and closes, dividends (ex and
  pay dates) and splits. One candidate is Alpaca market data with a **newly issued** key; the exposed key that
  starts with PKJB7U is never used.
  * Vendor terms may forbid committing prices. In that case the full ledger stays local and only the monthly pack
    and decision hashes are committed.
* **Fees:** the app's schedule. It must be updated when the SEC's FY2027 rate is published, and the fill records
  show which rates applied.

## Outcome and stop rule (fixed before launch)

**Reported separately:**
* ending wealth;
* dollar and percentage-point excess return over VOO;
* maximum drawdown (daily closes, and all-in for the AI portfolio);
* turnover (the lesser of purchases and sales over average month-end wealth);
* trading costs (fees, and the modeled spread and slippage);
* model costs.

**All-in wealth** is AI wealth minus every model charge grown at the voo portfolio's return from the charge's
cutoff to the end. Performance is shown before and after model costs.

**Verdicts:**

| record | verdict |
|---|---|
| incomplete (a gap, pending fill, unreconciled charge or missing month) | **inconclusive** |
| 12 complete months, all-in wealth at or below VOO | **stop this version** |
| 12 complete months, all-in wealth above VOO | **independent review of a longer paper test** |

A positive 12-month result from one path is not evidence of skill. Fixture decisions and synthetic data are never
a result.

## Limitations

* Twelve monthly decisions on one path have no statistical power. Luck dominates.
* At $100, cent rounding of fees is material: a single small rebalance can cost $0.03 in fees.
* The candidates are VOO's largest holdings, so picks are overweights of stocks VOO already holds.
* Taxes, withholding and settlement are ignored, and fills are modeled rather than real.
* A single model, with no fallback.

## Approvals and work needed before a genuine decision

1. **Independent review** of this registration and the code (`autotrader/invest`, `tests/test_invest_pilot.py`).
2. **Owner approval and freeze:** set the first fill month, change `FROZEN["status"]`, record the new
   `config_sha256`, and have the owner write a dated approval.
3. **SEC contact:** approve the use of the owner's declared contact for EDGAR access (fresh approval each time).
4. **Universe freeze:** run the rule on the latest public N-PORT-P and review the CUSIP→symbol map.
5. **Market data:** choose the price and corporate-action source, issue a new key (never PKJB7U), and decide whether
   prices may be committed.
6. **Model spend:** an Anthropic API account with prepaid credit and a workspace spend limit, a key held only on the
   machine that runs the job, and approval of about $2.40 expected and about $5 at most for 12 months.
7. **Code still to write and review:**
   * the EDGAR adapter (filings, passages, XBRL facts);
   * the N-PORT universe builder;
   * the price and corporate-action adapter;
   * the paid client, with a request journal so a month's request is sent once and never resent.

   None of this exists yet; the forward mode refuses to run.
8. **A host** that runs the monthly job between the cutoff close and the next open and pushes the freeze commit.

## Offline dry run (synthetic data, fixture decisions)

```
python -m autotrader.invest dryrun OUT_DIR
```

**What it uses:**
* invented companies and prices on a simulated 2031 calendar;
* twelve scripted fixture months: buy, increase, hold, reduce, sell, 100% VOO, a missing answer, a late answer and
  a rejected four-stock answer.

Its record is in `dry-run/`. The verdict is always **NOT A RESULT**: fixture decisions are labelled in every record
and report, and say nothing about AI stock selection.

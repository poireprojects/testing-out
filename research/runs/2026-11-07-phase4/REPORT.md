# Phase 4: event-driven intraday alpha, feasibility and proof (2026-11-07)

> **Corrections (closeout):** this report and its result files are the original record and are kept unchanged.
> Three measurement defects (stop timing, per-trade fee rounding, the ML acceptance flag) are corrected
> separately in [`corrected/CORRECTIONS.md`](corrected/CORRECTIONS.md). The decision is unchanged, and the
> corrected numbers are not independent evidence.

**Decision: NO-GO.**

Real-time reaction to company disclosures is **not feasible** for this app. The verifiable news comes out
before the open or after the close. Non-earnings disclosures can't be timed. The app's live IEX feed does not
show the real market.

The one testable event strategy failed on every pre-registered criterion. It traded the morning after an
earnings release, using the vendor's time-stamped headline. Over 974 evaluation trades it lost **−0.27% per
trade** after measured costs (+0.09% before costs). It did no better than a **price-only comparable**: a
paired difference of −0.006%, p = 0.49. **The earnings information added nothing beyond the price move.**

The vault (2020-07-27 → 2022-10-31) stays untouched.

Files:
* `AUDIT.md`, `audit_events.json`: the 50-disclosure feasibility audit.
* `results.json`, `trades.json`, `ml_diagnostic.json`: the evaluation.
* `research/PREREGISTRATION-2026-11-07-phase4.md`: the frozen hypothesis (commit `1a42eb4`).

## A. Data feasibility

See `AUDIT.md`. In brief:

* **SEC acceptance times are exact**: 50 of 50, the filing header and data.sec.gov agreeing to the second.
* **SEC acceptance is often not the first release.** Across all 5,671 verified earnings events, the vendor
  headline came a median 1.8 minutes before acceptance. For **429 (7.6%) it came more than an hour before.**
  Expeditors, for example, released at 08:31 and was accepted at 11:48.
* **First-release times can be reconstructed for earnings** (verified vendor flash: 82% of 6,916 Item 2.02
  filings). They **cannot** be reconstructed for other 8-Ks: 1 of 25 in the audit, and those were excluded,
  never estimated.
* Consolidated minute and 15-minute bars and historical NBBO quotes are available free (except the latest 15
  minutes).
* EDGAR has no point-in-time ticker history; current tickers were used.

## B. Execution feasibility

* **Releases during trading hours:** 0 of 24 verified audit releases. Overall, only 4 of 5,671 verified releases fell
  between 09:25 and 16:00. Earnings come out
  before 09:30 or after 16:00, so the earliest action is the next open. The median delay from release to the
  first tradable minute is 191 minutes, and the median absolute move before it is 3.1%.
* **Spreads** (NBBO, earnings mornings): a median 45 bps at 09:31, 15 bps at 10:00 and 6 bps at 15:50.
* **IEX** (the app's free real-time feed): 4% of volume. Its quote spread was a median 588 bps at 09:31 (often
  absent) and 45 bps at 10:00, and its prices sat 9.6 bps off the consolidated tape. The app could not see the
  real bid and ask without the $99/month SIP feed.

## C. The hypothesis

**P4-H1** builds on post-earnings drift and consistent-signal drift (Bernard & Thomas; Jegadeesh & Livnat;
Brandt et al.).

**Event:** the vendor's earnings flash says **EPS beat and sales beat**, released between the prior close and
09:25.

**Rule:** buy at 10:00 if the stock is beating its sector ETF since the prior close.

**Execution:**
* entry at the NBBO after a 30-second latency, plus half the spread, plus 5 bps
* a 3% stop
* exit at 15:45 at the NBBO, minus half the spread, minus 5 bps
* dated fees with daily cent rounding
* a $20 position
* same day only

Everything was frozen before any result.

## D. Evidence

| | development 2022-12 → 2024-10 | **evaluation 2024-11 → 2026-10** |
|---|---|---|
| H1 trades | 805 | **974** |
| H1 before costs | +0.03% | **+0.09%** |
| **H1 net per trade** | −0.30% (90%: −0.46 … −0.13) | **−0.27%** (−0.42 … −0.11), p(>0) = 0.996 |
| win rate · stops | 41% · 161 | 44% · 223 |
| net with 2× spreads and slippage | −0.51% | −0.57% |
| B2 every earnings event | −0.33% (n = 2,650) | −0.30% (n = 2,965) |
| B3 price-only comparable (paired excess) | −0.18% (p = 0.90) | **−0.006% (p = 0.49)** |
| B1 sector ETF (paired excess) | −0.09% | −0.11% |
| B4 same stock, random day (paired excess) | +0.04% | +0.16% (p = 0.07) |
| half-years positive | 0 of 5 | 1 of 5 |

**Pre-registered criteria:** 1 of 8 passed, the trade count. **Decision: NO-GO.**

**What the numbers say:**
* Earnings mornings do drift slightly more than ordinary days. The paired excess over a random day was +0.16%
  (p = 0.07).
* But a price-only stock with the same opening move did just as well, so the information isn't the reason.
* The move is about a quarter of the cost of a $20 round trip: 0.24% for spreads and slippage plus 0.11% for
  cent-rounded fees.
* Across surprise types, only "EPS beat, sales miss" was positive before costs (+0.26%), and it was still
  negative after them (−0.09%). That subset was not tested; looking for it now would be data mining.

**Capital scenarios** (2 slots of 20%, the 2 largest reactions each day):

| | development | evaluation |
|---|---:|---:|
| $100 | −$17.37 (drawdown −22%) | +$2.74 |
| $1,000 | −$133 | +$81 |
| $10,000 | −$1,287 | +$856 |

The same rule gives opposite signs in the two folds. That is noise, not evidence.

**Assumptions that could change the result** (any of these could make the true result worse):
* survivorship in today's stock list
* the vendor flash time is an upper bound on the true release time
* the stop is checked on 15-minute bars
* the 5 bps slippage, which is a model, not a measurement

Even with zero slippage and zero fees, the measured half-spreads alone (about 0.14% round trip) exceed the
+0.09% gross move.

## E. Model value

The pre-registered L2 logistic model (decision-time features only, trained on development, applied once to
evaluation):

* Predicted a win probability above 0.5 for only **2 of 2,965** events. Its highest prediction was 0.503,
  against a 44% base rate.
* Those 2 trades won (+1.44% on average). By the letter of the pre-registered rule that "passes" at p = 0.00,
  but **two trades are not evidence.**
* **Correction:** the pre-registration omitted a minimum trade count for the ML test. With 2 trades the result is
  uninformative, so the model is **not kept**.
* Post-hoc diagnostic: AUC 0.52. Brier 0.2462 against the base rate's 0.2461. Its top-decile events still lost
  −0.17% per trade.

**The model has no skill.** No language model or neural network was used.

## F. Ongoing costs

| | cost |
|---|---|
| EDGAR | $0 (declared contact, at most 10 requests/s) |
| Alpaca news (historical; real-time untested) | $0 |
| Historical SIP bars and quotes | $0 |
| Seeing the true NBBO in real time | **$99/month** (Algo Trader Plus) |

No paid newswire is required, but none of these sources would rescue the result.

## G. Implementation (research only; the desktop app is unchanged, so no installer)

**Code:**
* `stockpilot/research/events.py`:
  * a fair-access EDGAR client that refuses to run without a declared contact
  * header parsing
  * the timestamp model (release / acceptance / receipt / actionable)
  * strict release matching
  * the flash parser
* `scripts/phase4_audit.py`, `scripts/phase4_collect.py` (resumable, cached, vault-guarded, parallel) and
  `scripts/phase4_test.py`.

**Tests:** `tests/test_events.py` (6) and `tests/test_phase4_trade.py` (4).

**Raw data is not committed:**
* vendor headlines
* market data
* `data-manifest.json` holds counts and SHA-256 hashes

**Credentials:** at the owner's instruction, the previously exposed paper key was used for read-only
historical data and news. No trading endpoint was called. The SEC contact was passed at run time and is not
stored in the repository.

## H. Decision

**NO-GO.**
* Disclosure reaction during trading hours is infeasible: no verifiable intraday releases, no reliable
  non-earnings timestamps, and an inadequate live feed.
* The one feasible event strategy loses after realistic costs and is no better than a price-only rule.

## I. Next action

**Stop intraday alpha development on this infrastructure.**

Thirteen pre-registered hypotheses have now been rejected, nine of them intraday. This phase tested the first
one built on genuinely new information. The binding constraint is structural: the edges measured before costs are +0.03%
to +0.09% per trade, while half-spreads alone cost about 0.14% per round trip on the stocks where news happens (0.24% with
slippage, plus about 0.11% in cent-rounded fees at $20). A
larger account removes the fee rounding but not the spread.

The only thing that would change this conclusion is information or execution that retail participants don't
have. That means faster-than-vendor news, or order-flow and market-making economics, and both are outside this
project's scope and budget.

No further feature work is justified by this evidence.

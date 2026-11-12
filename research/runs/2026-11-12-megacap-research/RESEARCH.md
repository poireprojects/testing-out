# META, GOOGL and AMZN against VOO: manual research record (2026-11-12)

This is a manual research record. It is not part of INVEST-PILOT-1 and does not replace or certify that pilot's 12-stock universe.

* **Nothing executed:** no orders, fills, ledger writes or forward clock.
* **Gates unchanged:** launch **NOT APPROVED**, paid dispatch **BLOCKED**.
* **Repo state:** HEAD `415d1296e23757a8b9f024c4e4b113f02fa2f8fe`, clean at start.
* **Author:** a Claude Code session. Session policy keeps model identifiers out of repository files, so the harness-reported model is stated only in the session chat; it is unverified.
* **Detail:** sources, hashes and every number are in `DECISION.json`.

## Decision

**Proposed $100 paper allocation: 100% VOO.** No stock clears its entry ceiling.

| rank | stock | Nov 11 close | base value | decision | entry ceiling |
|---|---|---|---|---|---|
| 1 | META | $628.77 | $581 | PASS | $528 per current share |
| 2 | GOOGL | $293.45 | $246 | PASS | $223 |
| – | AMZN | $266.17 | – | UNASSESSABLE | none |

Both are strong businesses (Q3 revenue +26% and +25%). The issue is the price, not the business.

* **What the prices imply:** about 16% (Meta) and 18–19% (Alphabet) annual growth in fully financed per-share earnings for five years.
* **My base case:** 14% for both.
* **Why Meta ranks first:** its gap to value is smaller (−8% against −16%).

## Evidence (cutoff 2026-11-12T17:58:29Z)

| document (company-hosted copy) | period ended | published | SHA-256 |
|---|---|---|---|
| [Meta Q3 2026](https://s21.q4cdn.com/399680738/files/doc_news/Meta-Reports-Third-Quarter-2026-Results-2026.pdf) | 2026-09-30 | 2026-10-21 | `2e3c8324a8ce…` |
| [Meta Q2 2026](https://s21.q4cdn.com/399680738/files/doc_news/Meta-Reports-Second-Quarter-2026-Results-2026.pdf), [Q1 2026](https://s21.q4cdn.com/399680738/files/doc_news/Meta-Reports-First-Quarter-2026-Results-2026.pdf), [Q4/FY2025](https://s21.q4cdn.com/399680738/files/doc_news/Meta-Reports-Fourth-Quarter-and-Full-Year-2025-Results-2026.pdf) | Jun 30, Mar 31, Dec 31 | Jul 29, Apr 29, Jan 28 | in JSON |
| [Alphabet Q3 2026, Exhibit 99.1](https://s206.q4cdn.com/479360582/files/doc_financials/2026/q3/2026q3-alphabet-earnings-release.pdf) | 2026-09-30 | 2026-10-28 | `2f9b94e9194e…` |
| [Alphabet Q2 2026](https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/2026q2-alphabet-earnings-release.pdf), [Q1 2026](https://s206.q4cdn.com/479360582/files/doc_financials/2026/q1/2026q1-alphabet-earnings-release.pdf), [Q4/FY2025](https://s206.q4cdn.com/479360582/files/doc_financials/2025/q4/2025q4-alphabet-earnings-release.pdf) | Jun 30, Mar 31, Dec 31 | Jul 22, Apr 29, Feb 4 | in JSON |

**Prices.** Yahoo Finance chart API, Nov 11 regular-session closes, retrieved 17:20–17:30Z.
* **Cross-check:** META matches the director's $628.77.
* **Nov 12 intraday snapshots change nothing:** META $624.43, GOOGL $300.69, AMZN $269.33, VOO $699.50.

**Rate.** 10-year Treasury par yield 4.98% on Nov 10.

**Blocked, and not bypassed:**
* **SEC EDGAR:** returns 403 "Undeclared Automated Tool". The 10-Qs, 8-Ks, 424B5, Class C information statement and any later filings are unread.
* **IR sites:** all three serve Cloudflare challenges.
* **Internet Archive:** the session's safety classifier refused it.
* **Not found:** Meta's offering press release and Amazon's 2026 files have no findable company-CDN copy.
* **Network changes:** the session owner approved the allowlist additions used here (company CDNs, Yahoo, Treasury).

## Method (both companies)

* **FFE (fully financed economic earnings, FY2027)** = normalized operating income × (1 − tax) − 4.5% × funding gap.
* **Funding gap** = capex (Meta's includes finance-lease principal) − (NOPAT + depreciation).
  * Earnings already deduct depreciation, so capex enters only through the gap's carrying cost; nothing is subtracted twice.
* **Stock compensation** stays expensed, and employee issuance is not added to units, so it is counted once.
* **Why the financing mix barely matters:** 4.5% after tax is close to what new stock costs at these prices (Meta's FFE yield is 4.95%). The unknown mix of debt, stock and preferred therefore moves EPS by under 0.5%.
* **Units are current-share equivalents:**
  * **Meta:** 2,573M (Q3 diluted, Class A+B). After the Class C dividend, one current share is one A share plus one C share. Per-unit figures are unchanged; post-dividend per-share figures halve.
  * **Alphabet:** 12,368M diluted plus 65M for the $19.06B mandatory convertible preferred, converted at $293.45 (terms unverified; its dividends are not deducted).
* **Value** = justified P/E × FFE EPS + (net cash at 2026-12-31E + after-tax investments − other claims) ÷ units.
* **Justified P/E** = 15.27 × ((1+g)/(1+r))⁵.
  * r = 9.5%: the 4.98% Treasury yield plus an assumed 4.5% premium.
  * 15.27 is the terminal multiple at 4% growth and 25% ROE.
  * With base g = 14%, the justified P/E is 18.68×.
* **Implied growth** solves the same formula for g at the market price.

## META: PASS, ceiling $528

**Sourced (Q3 2026 unless noted):**
* **Revenue** $64.60B, with ad impressions +10% and price per ad +14%.
* **Operating income** $12.99B after a $9.89B attorneys-general settlement charge (payments of up to about $18.59B); $22.88B before it.
* **Cash and capital:** capex $34.91B; free cash flow −$2.12B; cash and securities $82.45B; long-term debt $80.92B; non-marketable investments $32.62B; headcount −15%.
* **Guidance:** Q4 revenue $73–76B; 2026 expenses $175–179B and capex $135–145B. For 2027, only a Reality Labs loss of about $15B and "continue investing aggressively".
* **One-offs:**
  * Q2 legal ($2.40B) is kept as a recurring-type cost.
  * Q2 severance ($1.18B) and the Q1 tax benefit ($8.03B) are excluded.

**Derived:**
* **2026E revenue** $256.2B (+27.5%).
* **Margins:** normalized margin 35.2%, but the implied Q4 margin is 33.0% (41% a year earlier).
* **Depreciation** runs at 12.0% of average net PP&E.
* **Trailing GAAP EPS** $29.32 (21.4×).

| FY2027 | bear | base | bull |
|---|---|---|---|
| revenue growth / operating margin / tax | 10% / 28% / 18% | 18% / 32.5% / 16% | 24% / 36% / 15% |
| NOPAT ($B) | 64.7 | 82.5 | 97.2 |
| capex / depreciation ($B) | 185 / 43 | 180 / 44 | 190 / 45 |
| funding gap → charge ($B) | 77.3 → 3.5 | 53.5 → 2.4 | 47.8 → 2.2 |
| FFE EPS (2,573M units) | $23.80 | $31.14 | $36.95 |
| per-unit adjustments | −$8.21 | −$1.02 | −$1.02 |
| value at g 10 / 14 / 18% | $364 | $581 | $819 |

**Per-unit adjustments (base):**
* **Net debt at 2026-12-31E:** −$25.4B, because Q4 capex of $54.2B exceeds Q4 earnings plus depreciation.
* **Investments:** +$32.6B.
* **Settlement:** −$9.89B. Removing the charge from earnings does not remove its cash liability.
* **Bear case:** the settlement is $18.59B and investments are marked at ×0.7.

**Financing:**
* **Verified:** $24.91B of Q2 debt; no 2026 buybacks.
* **Unverified:** the director reports post-quarter debt and common-share financing, a mandatory convertible preferred, forward-sale Class A stock and an intended $60B share-sale program. Sizes, prices, the preferred dividend, conversion terms and timing are all unverified.
* **Bounded:**
  * The Q4 2026–2027 funding need is about $80B.
  * Selling $60B of stock at $628.77 adds 95M units (3.7%).
  * An all-stock base gap gives $31.05 instead of $31.14.
  * A 3% offering discount costs $0.12 per unit for each $10B sold.
* **Value effect:** proceeds are value-neutral at market prices and add value only if the capex earns more than it costs.
* The decision does not depend on the unverified terms.

**GAAP versus economic EPS.** FY2027 GAAP EPS will use post-dividend weighted shares (about half these figures), exclude unsettled forward shares and deduct preferred dividends. FFE charges the whole funding need in the year it arises.

**Expectations:**
* The price implies 15.8% annual FFE growth (10.3–20.9% across r 8.5–10.5%).
* No consensus was verifiable.
* Against my 14%, there is no robust disagreement: roughly fair, not cheap.

**Strongest counterargument:**
* AI is lifting ad prices while headcount falls. If 2027 expenses grow no faster than revenue, FFE EPS is about $37 and value $690–820.
* My expense path predates Meta's 2027 outlook.

## GOOGL: PASS, ceiling $223

**Sourced (Q3 2026 unless noted):**
* **Revenue and segments:**
  * Revenue $127.58B.
  * Search & other +12%, slowing from 19% in Q1 and 17% in Q2.
  * Cloud $30.71B (+103%, including TPU system sales) at a 36.2% margin.
* **Margins:** operating margin fell 36.1% → 34.0% → 31.8% from Q1 to Q3.
* **Cash and balance sheet:** capex $51.36B; trailing free cash flow $19.75B; cash and securities $265.1B; long-term debt $125.1B; non-marketable securities $135.2B.
* **Completed 2026 financing:**
  * A June raise of $49.6B: $30.5B common plus $19.06B of 6.25% mandatory convertible preferred.
  * ATM sales of $12.4B (36M shares). $27.6B of the ATM remains; that part is a plan, aimed at employee-equity taxes.
  * $85.2B of debt.
* **Capex guidance:** the only guidance on record is $175–185B for 2026 (February). Nine-month spending of $132.0B points to about $187B.

**Investment gains:**
* 2026 includes $127.3B of pre-tax equity gains (tax $28.2B).
* **The low-P/E trap:** GAAP trailing EPS of $19.15 makes GOOGL look cheapest (15.3×); recurring EPS is $11.01 (26.6×).
* The gains are excluded from earnings, but the assets are kept at $107.0B after tax ($8.61 per unit; bear ×0.7; bull untaxed).

| FY2027 | bear | base | bull |
|---|---|---|---|
| revenue growth / operating margin / tax | 12% / 27.5% / 19% | 19% / 31% / 17% | 26% / 33.5% / 15% |
| NOPAT ($B) | 124.1 | 152.3 | 178.4 |
| capex / depreciation ($B) | 220 / 47 | 230 / 48 | 250 / 50 |
| funding gap → charge ($B) | 48.9 → 2.2 | 29.7 → 1.3 | 21.6 → 1.0 |
| FFE EPS (12,433M units) | $9.80 | $12.14 | $14.27 |
| per-unit adjustments (net cash $10.26 + investments) | +$16.28 | +$18.87 | +$21.13 |
| value at g 10 / 14 / 18% | $169 | $246 | $338 |

**Expectations:**
* The price implies 18.5% annual FFE growth (12.8–23.6% across r 8.5–10.5%).
* **My disagreement is on margins.** Depreciation is up 49% year over year to $8.37B a quarter, and Search slowed to 12%, so I expect margins near 31% in 2027 rather than a recovery.
* **What will settle it:** Q4 2026 and Q1 2027 margins, Search growth and the 2027 capex guidance.

**Strongest counterargument:**
* Cloud doubled with a backlog above $460B (Q1).
* $18.87 per unit of cash and investments cushions the downside.
* At r = 8.5%, the base value is $309.

## AMZN: UNASSESSABLE

**Missing:** Amazon's Q3 2026 earnings release and 10-Q, which hold the income statement, the cash flow with capex and lease principal, segments, guidance and commitments.

**Tried:**
* The IR URL: a Cloudflare challenge.
* SEC: refused, because no contact may be declared.
* The company CDN: the Q3 2025 file exists, but the same naming returns 404 for every later quarter.

Only the price is verified. This is missing evidence, not a negative result.

## Sensitivity (value per unit, base EPS)

| | g 10% | g 14% | g 18% |
|---|---|---|---|
| META, r 8.5 / 9.5 / 10.5% | 622 / 486 / 392 | 743 / 581 / 469 | 884 / 690 / 558 |
| GOOGL, r 8.5 / 9.5 / 10.5% | 262 / 209 / 172 | 309 / 246 / 202 | 364 / 288 / 237 |

## Allocation and dollars at $100

**Allocation:** 100% VOO. Each ceiling is base value ÷ 1.10, a margin for model error.

**What a winning pick is worth at this size:**
* A 10% position is $10. Beating VOO by 10 points adds $1.00 gross.
* A modeled round trip costs $0.06–0.08.
* The registered monthly model call is estimated at $0.14–0.32, so 4–7 months of calls erase that $1.
* This session's own cost is unknown. The harness reports about $363 of API-equivalent usage across the multi-day session; the billing basis is unknown, and it is not zero.

The forecasts are gross. They are not claims about net return.

## Forecasts (new record; uncalibrated)

**Window:**
* **Start:** the close of the first full NYSE session after this record's final response (expected 2026-11-13).
* **End:** the last NYSE session on or before the anniversary (expected 2027-11-12).
* **Wealth measured:** dividends plus all distributed securities, including META Class C, at the end close.
* **Prices:** null until observed.
* These are observations, not simulated executions.

| stock | expected return against VOO | P(beats VOO) | direction |
|---|---|---|---|
| META | −1.9 pp | 0.47 | underperform |
| GOOGL | −4.1 pp | 0.44 | underperform |
| AMZN | none | 0.50 placeholder | none (Brier only) |

* **Expected return** assumes 25% of the value gap closes in 12 months.
* **P** = Φ(expected ÷ 27% tracking error).
* **Scoring:** a wrong direction fails the forecast; missing data is unscorable.

## Invalidators (revise the business view; keep this record)

* **META:**
  * A 2027 outlook with an expense midpoint ≤ $191B and a capex midpoint ≤ $170B (value would be about $667).
  * Or Q4 2026 revenue ≥ $76B at an operating margin ≥ 36%.
* **GOOGL:**
  * Q4 2026 or Q1 2027 operating margin ≥ 33.5% with Search & other ≥ 15%.
  * Or 2027 capex guidance ≤ $200B with Cloud ≥ 80%.
* **Reconsider buying** if META ≤ $528 or GOOGL ≤ $223, absent an opposite invalidator.

## Changes from the director's META conclusion

The director's record stays unchanged and separate: PASS at $628.77, $27.85 EPS, $613 at 22×, reconsider near $500, 55% confidence.

This record reaches the same PASS by a different route:
* **EPS:** my base EPS is higher ($31.14).
* **Multiple:** derived at 18.7×. Assuming 22× by itself implies about 16% growth.
* **Result:** value $581, ceiling $528.
* **Confidence:** my P(META beats VOO) is 0.47, not 55%.
* **Coverage:** GOOGL is now valued; AMZN is unassessable.

## Limits

* **Sources:** earnings releases, not 10-Qs.
* **Assumptions:** the 2027 figures are assumptions, and r and g dominate the value.
* **Missing yardsticks:** no consensus; VOO's own expected return is not estimated.
* **Scope:** no edge is established, and no real-money or software conclusion follows.

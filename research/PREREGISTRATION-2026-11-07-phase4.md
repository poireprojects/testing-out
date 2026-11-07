# Phase 4 pre-registration: earnings-day continuation of consistent good news (2026-11-07)

This is written and committed **before** any strategy-level result is computed. The feasibility audit
(`research/runs/2026-11-07-phase4/AUDIT.md`) is the only analysis done before it. The audit fixes what is
testable:
* earnings released outside regular hours
* entered after the open, once spreads have narrowed
* priced with historical NBBO quotes

**Scope.** Research only. No trading behaviour changes. Long only, no leverage, no overnight holding,
$20 positions in the $100 allocation. A pass earns forward shadow observation only.

## Hypothesis P4-H1

**Mechanism.** Investors under-react to earnings news (post-earnings-announcement drift: Bernard & Thomas
1989, 1990). The drift is stronger and faster when signals agree, i.e. earnings *and* revenue both beat
(Jegadeesh & Livnat 2006), and when the first price reaction confirms the news (Brandt, Kishore, Santa-Clara
& Venkatachalam 2008). Limited attention slows full adjustment (Hirshleifer, Lim & Teoh 2009; DellaVigna &
Pollet 2009).

The information is new compared with phases 1–3. The event and the direction of the surprise come from a
time-stamped vendor headline, not from prices. What phase 2 could not do is the subject of the test: tell an
earnings-driven move from a price-only move.

**Event.** An 8-K with Item 2.02 whose vendor earnings flash is **verified**. The flash is an Alpaca/Benzinga
headline in the strict form used by the audit, naming the company, found in
[acceptance − 18 h, acceptance + 15 min]. Its timestamp *t* must fall between the previous session's 16:00
close and **09:25 ET** on the trade day, the first session that opens after *t*. The 8-K is used only to
find candidate dates; the decision uses only the flash and prices. A second Item 2.02 flash for the same
stock within the previous 3 sessions excludes the event.

**Eligible stocks.** The app's curated universe (today's list, so survivorship bias is acknowledged):
* prior close ≥ $5
* 20-session average daily dollar volume ≥ $20 million, from consolidated 15-minute bars

**Information at the decision, 10:00:00 ET:**
* The flash text: EPS beat/miss/in-line and sales beat/miss/in-line.
* Consolidated 15-minute bars that **ended** by 10:00 (09:30 and 09:45), and the previous session's last
  regular-hours price.
* The same for SPY and the stock's SPDR sector ETF (from its SEC SIC code).

**Rule (frozen):** buy when all of these hold:
1. The flash says **EPS beat AND sales beat**.
2. The stock's return from the prior close to 10:00 minus its sector ETF's return over the same span is
   **> 0**.

The rule has no thresholds to tune and no ranking. Every qualifying event is a candidate.

**Execution (no same-bar or hindsight fills):**
* **Entry** after a 30-second decision latency, at the median NBBO mid over 10:00:30–10:01:30, plus half the
  median quoted spread in that window, plus 5 bps slippage.
* **Exit** at the median mid over 15:45:00–15:46:00, minus half the spread there, minus 5 bps.
* **Stop:** 3% below the entry fill (the app's maximum stop distance). It is checked on the 10:00 … 15:30
  15-minute bars; the first bar whose low reaches the stop exits at min(stop, bar open), minus the entry
  window's half-spread, minus 5 bps.
* **Fees:** the app's dated `CostModel` with daily cent rounding, for a $20 trade.
* No quote in a window means "unfillable": counted as a missed opportunity, not traded.

**Maximum holding:** the same session, 15:45. Never overnight.

## Baselines (same entry and exit times and the same cost method)

| | baseline | what it isolates |
|---|---|---|
| B0 | no trade | — |
| B1 | the stock's sector ETF, same day, same times (paired) | market and sector moves |
| B2 | every verified earnings event, any surprise, any reaction | whether the beat/beat + reaction filter adds value |
| B3 | **price-only comparable**: on the same day, the eligible stock with **no earnings flash within ±3 sessions** whose sector-relative prior-close→10:00 return is nearest to the H1 trade's (paired) | **whether the information adds value beyond the price move**. Phase 2's failure mode |
| B4 | random-event placebo: the same stock on a random non-event day within ±60 sessions (seeded) | stock-specific intraday drift |

## Periods

* **Development fold:** 2022-12-01 → 2024-10-31. Bars start 2022-11-01, never in the vault.
* **Evaluation fold:** 2024-11-01 → 2026-10-30.

Both are "examined" in the ledger, from price-only research, and no earnings-event test has used them. The
evaluation fold is a chronological out-of-sample check, **not** an unseen holdout. The vault is untouched
(see below).

## Pass criteria (evaluation fold; all required)

1. At least 100 filled trades.
2. **Mean net per trade > 0**, day-block bootstrap one-sided p < 0.05.
3. **Mean paired net excess over B3 > 0**, p < 0.05.
4. Mean paired net excess over B1 > 0 (point estimate).
5. Net positive in at least 3 of 4 half-year blocks.
6. Net > 0 with **2× spreads and 2× slippage**.
7. The 5 best trades contribute less than 50% of total net profit.
8. The development fold's mean net per trade is also > 0.

Holm–Bonferroni covers criteria 2 and 3 and the ML test below, a family of 3 at α = 0.05.

**Every variation that will be computed** is listed here, and nothing else:
* H1
* B1–B4
* 2× costs
* the capital scenarios: $100 with 2 slots, taking the 2 largest reactions when more qualify; $1,000; and
  $10,000
* the ML comparison below

## Machine learning (one bounded test)

* **Model:** L2 logistic regression (the app's `LogisticL2`).
* **Target:** P(net > 0).
* **Data:** all B2 events.
* **Features** (decision-time only, standardised on the development fold):
  * the four EPS/sales surprise directions
  * sector-relative reaction to 10:00
  * SPY's reaction to 10:00
  * the 09:30–10:00 consolidated volume ÷ 20-day average daily volume
  * log price
  * log average daily dollar volume
* **Procedure:** trained once on the development fold, applied once to the evaluation fold; trade when P > 0.5.

It is kept only if its evaluation-fold mean net per trade > 0 at Holm-adjusted p < 0.05 **and** it exceeds
both B2's and H1's evaluation means. It is compared with the deterministic rule and the event-only baseline.
**No language model or neural network.**

## Vault rule

If and only if every criterion above passes, the frozen code is run **once** on the vault
(2020-07-27 → 2022-10-31). That data would be registered in the ledger before download. Otherwise the vault
stays untouched.

## Decision

* **GO** (forward shadow only): everything above passes.
* **NO-GO:** the rule loses after costs, or does not beat the price-only comparable.
* **INSUFFICIENT EVIDENCE:** fewer than 100 trades, or results inside the noise.

# Pre-registration — phase 2: opportunities with better economics (2026-11-07)

Committed **before** any new data was downloaded or any result below was computed. Parameters are
fixed. A change after seeing results is a new hypothesis in a new pre-registration.

## Why these hypotheses (from round 1, development evidence)

Round 1 showed the problem is economic, not predictive sophistication: the best intraday signal
(momentum / volume breakout) moved ≈ +0.035–0.04% more than an average entry over 30 minutes, against
≈ 0.17% round-trip cost on a $20 trade; median 30-minute moves are ≈ 0.20%. So we look for opportunities
whose **typical move is several times the cost**, or whose **cost per unit of move** is lower:

| direction | hypothesis | mechanism |
|---|---|---|
| A. longer holds | **P2-H1** momentum continuation held to the close | intraday momentum persists for hours ("stocks in motion"); a fixed cost is spread over a bigger expected move |
| B+C. selective, high conviction | **P2-H2** opening-range breakout on gap-up "stocks in play" | news-driven order imbalance and attention: larger, more persistent intraday ranges (Zarattini, Barbon & Aziz, 2024; long side only here) |
| D. execution | **P2-H3** passive limit entry for P2-H1 | save the half-spread and slippage on entry; risk: non-fills and adverse selection |
| E. horizon (research only) | **P2-H4** overnight hold of index ETFs | the overnight-return premium (Lou, Polk & Skouras, 2019): most index returns accrue close-to-open |
| E. horizon (research only) | **P2-H5** index trend filter (multi-day) | time-series momentum / risk management; very low turnover |

H4 and H5 are **research only**: the app does not hold positions overnight, fractional-share stops are
DAY orders and cannot protect against gaps. Nothing here enables overnight trading.

## Periods (registered in `ledger.json` with this file)

* **Vault (untouched, reserved for future rounds):** 2020-07-27 → 2022-10-31 (start of free IEX history).
* **Warm-up only:** 2022-11-01 → 2023-08-31 — indicator history (100-day SMA, 14-day volume averages,
  10-session baselines) for the holdout. No result is computed on these days.
* **Phase-2 holdout (single use):** 2023-09-01 → 2025-08-29. Evaluated once, for every hypothesis, after
  the development run and this code are committed. Marker: `research/HOLDOUT_P2_USED.json`.
* **Development:** 2025-09-02 → 2026-11-05 (already examined; development evidence only).

## Universe and data

* H1, H3: the same 40 liquid symbols as before (1-minute IEX bars).
* H2: the app's curated ~450-symbol universe (liquid **today**: survivorship bias, stated in every
  report). Gaps are measured from daily IEX bars (verified equal to the regular-session minute bars);
  1-minute bars are fetched only for gap candidates.
* H4, H5: daily IEX bars for SPY, QQQ, IWM, DIA.

## Rules (all long only; $20 positions; app limits: ≤ 2 open, one per symbol, 20-minute cooldown)

**P2-H1.** Entry: the first `momentum` or `volume_breakout` signal (unchanged strategy code) per symbol
per day with the signal bar between 10:00 and 14:00 ET; buy at the next bar's open. Stop:
entry × (1 − s), s = min(3%, max(1.0%, 3 × ATR(14, 1-min) ÷ price)). No target, no strategy exit, no
trailing. Exit at the stop or at the closing cutoff (15:50). Slots: first come (earlier signal first,
then higher score). Descriptive: the same entries' outcomes at 30, 60, 120, 180 minutes.

**P2-H2.** Each day, candidates: gap = today's open ÷ yesterday's close − 1 ≥ +3%; yesterday's close ≥ $5;
14-day average daily dollar volume (IEX) in the universe's top half that day; first five 1-minute bars:
close of the 5th > open of the 1st, relative volume rvol5 = volume(first 5 min) ÷ (14-day average daily
volume × f(5)) ≥ 2 where f(5) is the app's expected fraction of daily volume after 5 minutes. Opening
range (OR) = high/low of the first five 1-minute bars; skip if (OR high − OR low) ÷ OR high > 4%. Rank by
rvol5, at most 2 per day. Entry: buy-stop at OR high from 9:35 to 11:00 — a bar whose high exceeds OR high
by ≥ 1 bp fills at max(OR high, that bar's open). Stop at OR low. Exit at the stop or 15:50.

**P2-H3.** H1's candidates, but a limit buy at the signal bar's close × (1 − 5 bps), good for 10 minutes;
filled only if a later bar's low ≤ limit × (1 − 1 bp), at the limit price (no spread or slippage on
entry). Unfilled = no trade (its marketable outcome is reported to measure adverse selection). Exit as H1.

**P2-H4 (research only).** Every session: buy SPY, QQQ, IWM, DIA at the close, sell at the next open.

**P2-H5 (research only).** Hold SPY from the open after a close above its 100-day simple moving average;
sell at the open after a close below it. Compared with buy-and-hold SPY on the same days.

## Costs

* Standard (H1, H3 exits, H4, H5): 3 bps quoted spread, 5 bps slippage per side, broker's dated fee
  schedule with daily rounding (as in round 1). Stress: 2× slippage.
* H2 (opening-minute gap stocks have wide spreads): **10 bps spread + 5 bps slippage per side**. Stress:
  2× slippage, and 20 bps spread.
* H4 sensitivity (reported, never used for a verdict): 1 bp spread, 1 bp slippage.
* Fills: stop-first inside a bar, gaps fill at the open, entries at the next bar.

## Metrics and decision

Primary: average net return per trade of the account replay (H1–H4); H5: average daily net return.
Uncertainty: day-block bootstrap (5,000 draws), Holm–Bonferroni across H1–H5 at α = 0.05.
Also reported for each: MFE/MAE, holding-period curve, chance of reaching +0.5% / +1% / +2% before the
stop, capture ratio (realized ÷ MFE), break-even gross move, by-regime results (regime labels known only
at the close are used for reporting only), Sharpe, max drawdown.

Rejected if any holds:
1. mean net ≤ 0, or Holm-adjusted p ≥ 0.05;
2. fewer than 100 trades (H5: fewer than 250 days) → "insufficient";
3. fewer than 60% of 4 folds positive;
4. the 5 best trades make more than 50% of profit;
5. negative under the stress costs;
6. not better than the best simple baseline: no trade; for H1 random entry times on the same
   symbol-days with the same exit rule; for H2 buying the same candidates at 9:35 without the breakout;
   for H3 H1 itself (paired by day); for H5 buy-and-hold (must match or beat its Sharpe and have a
   smaller max drawdown).

A pass on development is not evidence; only the holdout verdict counts. A holdout pass makes a
hypothesis eligible for **forward shadow observation** only (and H4/H5 only for an owner decision about
scope). Live use needs forward evidence and explicit approval.

## Machine learning (secondary, conditional)

Only for H1/H2 if the unfiltered development result is positive after costs: compare unfiltered,
a fixed deterministic filter (H1: relative volume ≥ 2 and SPY above VWAP; H2: rvol5 ≥ 4), the existing
L2 logistic/ridge model (walk-forward on development, trained on development only for the holdout, rule
expected net > 0 and P(win) > 0.5), and no trade — on net results, not accuracy.

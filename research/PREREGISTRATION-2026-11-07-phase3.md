# Phase 3 pre-registration: low-turnover, multi-day hypotheses (2026-11-07)

This document is written and committed **before** any strategy-level backtest of these rules is run on any
period. It is also committed before holdout A is opened.

The hypotheses were chosen after an exploratory review on development data only. The review is
`research/runs/2026-11-07-phase3/review.json` and covers 36 factor/condition trials. That review is disclosed
here and counts toward the multiple-testing budget.

**Scope.** Both strategies hold positions overnight, so they are simulation only. The app does not trade them.
A forward shadow record (decisions recorded before outcomes, no fills) is the only live component allowed.
Neither strategy may change trading behaviour without the owner's explicit approval.

## Data and periods (research/ledger.json)

| period | dates | use |
|---|---|---|
| Development, long history | 1926-07-01 → 2005-12-30 | Exploratory work and rule checks. Covers the original samples of the studies below |
| Development, recent | 2022-11-01 → 2026-09-30 (academic series); → 2026-11-05 (Alpaca ETF bars) | Already examined. Tradable-ETF replication. Low power |
| **Holdout A** | **2006-01-03 → 2020-07-24** | Reserved before download, split into separate files, **single use** after this document and the frozen code are committed. Marker `research/HOLDOUT_P3_USED.json` |
| Vault | 2020-07-27 → 2022-10-31 | Untouched. Opened only if a hypothesis passes **both** development and holdout A (see "Vault rule") |

Sources:
* Ken French data library: daily value-weighted returns of 12 and 49 industry portfolios, the market, and the
  1-month T-bill rate RF. These include dividends and are survivorship-free (all CRSP stocks).
* For the tradable replication: Alpaca IEX daily bars of SPY and the SPDR sector ETFs, price only. The
  2025-12-05 2-for-1 splits are corrected; dividends are not included.

## Hypothesis P3-H1: industry relative strength (cross-sectional momentum)

**Mechanism.** Industries under-react to industry-level news, and investors herd slowly. Industry returns
therefore persist over 3–12 months. Sources:
* Moskowitz & Grinblatt (1999): industry momentum explains much of stock momentum.
* Hong, Torous & Valkanov (2007): industries lead.
* Asness, Moskowitz & Pedersen (2013): momentum everywhere.

At the monthly horizon, turnover is low enough that costs are small relative to the documented spread.

**Rules (frozen):**
* **Universe:** the 12 Fama-French industries, value-weighted.
* **Signal:** on the last trading day of each month, 12-1 momentum = return from the close 252 trading days
  earlier to the close 21 trading days earlier.
* **Portfolio:** equal weight in the top 3 of 12. Ties are broken by the higher 6-1 momentum.
* **Execution:** at the close of the **next** trading day (a one-day implementation lag). Weights are reset to
  equal at each rebalance and drift in between.
* **Cash:** none (always fully invested).
* **Risk limits:** long only, no leverage. Overnight and weekend gaps are fully exposed (no stop is assumed).
* **Tradable mapping (replication only):** the same rule on the 11 SPDR sector ETFs (top 3 of 11).

**Baselines:**
* Cash.
* Market buy-and-hold.
* Equal-weight 12 industries, rebalanced monthly at the same costs. This is the primary benchmark: it isolates
  *selection* from market exposure.
* 1,000 random top-3 draws per month (a distribution).

**Primary metric:** the average monthly **net** return of H1 minus the equal-weight benchmark (both after
costs).

## Hypothesis P3-H2: turn of the month (institutional cash-flow timing)

**Mechanism.** Pay-days, pension contributions, fund inflows and dividend reinvestment arrive around month-end.
Institutions sell before it to raise liquidity and buy after it. Sources:
* Ariel (1987); Lakonishok & Smidt (1988).
* Ogden (1990): payment-system timing.
* McConnell & Xu (2008): 1926–2005, 35 countries.
* Etula, Rinne, Suominen & Vaittinen (2020, RFS): "Dash for Cash".

The calendar is known in advance, so there is no look-ahead. Unscheduled closures only extend a holding.

**Rules (frozen):**
* **Market:** US equity market (French value-weighted market; tradable: SPY).
* **Holding:** buy at the close of trading day −2 (the second-to-last trading day of the month). Sell at the
  close of trading day +3 of the next month. This earns the returns of days −1, +1, +2 and +3. The rest of the
  time the capital is in cash.
* **Cash return:** 0% (primary, conservative). Sensitivity: the T-bill rate.
* **Risk limits:** long only, no leverage. The position is held over at least one weekend every month
  (simulation only).
* **Unscheduled closures:** trading-day counts come from the days the market actually traded. A closure
  extends the holding.

**Baselines:** cash; market buy-and-hold; the same number of days held at random (1,000 draws of 4-day windows
per month).

**Primary metric:** the average **net** return per monthly holding window, which is an excess over cash
because cash earns 0. Co-primary: the TOM-day versus other-day difference in mean daily excess return.

## Costs (both hypotheses)

* **Standard:** 3 bps quoted spread (1.5 bps paid per side) + 5 bps slippage per side, on traded notional.
* **Regulatory fees:** the app's dated `CostModel`, with each fee type's daily total rounded up to a cent,
  modelled at each capital level.
* **Stress:** 2× slippage and 2× spread.
* **SPY realistic sensitivity (H2 only, reported):** 0.5 bps spread + 1 bp slippage.

## Capital scenarios (simulation only, no leverage)

$100, $500, $1,000, $5,000 and $10,000:
* fractional shares, $1 minimum order
* fee rounding per day at each size
* costs as above (no market impact at these sizes)
* settlement sensitivity: buys delayed one day after sells (cash account, T+1)

## Evaluation and rejection criteria

Statistics:
* Month-block bootstrap (5,000 draws), one-sided p for primary metric > 0.
* Holm–Bonferroni across the hypotheses tested on holdout A, at α = 0.05.
* Deflated Sharpe ratio (Bailey & López de Prado 2014). The number of trials includes every factor trial from
  the review and every strategy variant reported in development.

**Development pass** (all required, on 1926-07 → 2005-12; recent development reported, not required):
1. Primary metric > 0 with p < 0.05.
2. Positive in at least 6 of the 8 complete decades (1930s–2000s, with the 1926–29 stub folded into the 1930s).
3. Still positive with 2× costs.
4. The 5 best months contribute less than 50% of the cumulative excess.
5. Deflated Sharpe ratio of the excess series > 0.95.

**Holdout A pass** (single run, 2006-01 → 2020-07):
1. Primary metric > 0 with Holm-adjusted p < 0.05.
2. Positive in at least 3 of the 5 three-year blocks.
3. Positive with 2× costs.
4. *(H2)* Strategy Sharpe ≥ 0.5 × buy-and-hold Sharpe, because its exposure is about 20%. H2's absolute net
   return must also be positive.

A hypothesis failing any criterion is **rejected**. A pass makes it eligible only for **forward shadow
observation**. It does not make it eligible for trading.

**Vault rule:** the vault is opened only for hypotheses that pass both development and holdout A, once, with
the same code. Otherwise it stays untouched.

## Machine learning (secondary, pre-specified, bounded)

Run only on development data, walk-forward with expanding windows refit every January, starting 1946 (20
years of training), and only for a hypothesis that passes development:

* **H1-ML:** ridge regression of next-month industry excess returns on standardised 12-1, 6-1 and 1-month
  momentum, 200-day trend and 63-day volatility. The top 3 by prediction are compared with the top 3 by plain
  12-1, net of the same costs.
* **H2-ML:** logistic regression of "TOM window beats cash" on market trend (12-month excess > 0), 63-day
  volatility tercile and 200-day industry breadth. Hold the TOM window only when P > 0.5. Compared with
  unconditional H2 on net Sharpe.

A model is kept only if it improves the net primary metric on development walk-forward by a margin whose
bootstrap p < 0.05. Only then is it added to the holdout-A Holm family. Otherwise it is reported and dropped.

## Reporting

Every number is regenerated from saved JSON. The report covers:
* gross, cost and net results
* exposure and turnover
* drawdown, volatility, Sharpe and Sortino
* win/loss distribution
* regimes (up/down market years, high/low volatility)
* cost sensitivity
* capital scenarios
* absolute return vs benchmark outperformance vs risk reduction, stated separately for each hypothesis

The development console output is committed alongside the JSON.

# What would justify reopening day-trading research (closeout, 2026-11-07)

**Status: closed.** No strategy tested in this project made money after costs. That does not show that
profitable retail trading is impossible. Some individuals and many firms do profit, and regulators' complete
records find a small minority of day traders who are persistently profitable (under 1% in Taiwan, Barber et al.
2014). It shows that **we have found no advantage**, and that nothing in this project's evidence claims one.

## The arithmetic a salary has to satisfy

    yearly income ≈ net edge per trade × position size × trades per year

Using the app's own shape (2 positions of 20% of capital, 10 round trips a day, 250 days), traded value is about
500 × capital a year. The net edge needed for **$1,000 a month** ($12,000 a year):

| capital | position | net edge needed per trade | trades needed to show that edge is real* |
|---|---:|---:|---:|
| $100 | $20 | 24% | — (not a trading edge) |
| $1,000 | $200 | 2.4% | — |
| $10,000 | $2,000 | **0.24%** | ~430 |
| $25,000 | $5,000 | **0.10%** | ~2,700 |
| $100,000 | $20,000 | **0.024%** | ~43,000 |

\* Roughly (2σ ÷ edge)², with σ ≈ 2.5% per trade, as measured on our phase-4 trades. A smaller edge needs far
more trades before it can be told apart from luck: 43,000 trades is about 17 years at 10 a day.

Multiply the required edge by 3 for $3,000 a month, or by 5 for $5,000. For comparison:
* Our best measured gross edges were **+0.03% to +0.09% per trade before costs**.
* Every net result was negative: costs ran about 0.2–0.35% per round trip.
* So the question is not "how much capital" but **"is there any edge at all after costs."**

## Affordability is not profitability

| item | cost | affordable? | can it make a strategy profitable? |
|---|---|---|---|
| Real-time consolidated quotes (Alpaca Algo Trader Plus) | $99/month = $1,188/year | Yes in dollars, but it equals **1,188% a year of a $100 account** and 11.9% of $10,000 | **No.** It only shows the true price. It is necessary for any intraday plan, not sufficient |
| More capital | — | Your decision | **No.** It removes cent-rounded fees (about 0.03% per trade at $20 once fees are totalled per day), not spreads. It turns an existing edge into income; it cannot create one |
| Direct newswire feeds or full order-book history | unknown, not quoted; typically institutional pricing | Unknown | Unknown. Only with evidence that it is faster or more informative than what professionals already use |
| Co-location, direct market access, market making | far beyond retail | No | For firms at scale, yes (Virtu, Citadel Securities); not reachable from a retail account |

## Reopening requires one new input *and* the test standard

None of the four inputs below is enough on its own. Each only earns a new pre-registered test.

**1. New information**
* Verifiable first-release timestamps that are earlier than the vendor flash.
* Events that are actually released during trading hours. In phase 4 that was 4 of 5,671 earnings releases;
  candidate event types are deal announcements, regulatory decisions and trading halts.
* A survivorship-free point-in-time universe, including delisted stocks.

**2. Execution capability**
* Real-time consolidated quotes.
* Measured fill quality on the app's own orders, replacing the assumed 5 bps slippage with numbers.
* Evidence that resting limit orders earn rather than pay the spread net of adverse selection. Phase 2's passive
  entry (P2-H3) failed on exactly this.

**3. Verified outside evidence**
* A live record verified by a broker statement or an audit. Not hypothetical, not self-selected.
* At least 3 years, net of costs, at a trade size like ours, with a stated mechanism: who pays, and why it
  persists.
* Then an independent, pre-registered replication here.
* Marketplace leaderboards (Collective2, Myfxbook) and vendor bots do not qualify.

**4. Capital**
* Only after an edge passes the standard below.
* Before any plan that depends on it, confirm the current FINRA pattern-day-trader minimum with the broker. The
  app's settings note records its replacement on 2026-06-04, but the reset report could not confirm the final
  rule.

**The test standard**, unchanged from this project:
* Written down before any data is seen.
* Gross edge at least twice the measured round-trip cost.
* At least 100 trades. Beats the price-only and random-day comparables after correction for multiple tests.
* Then at least 12 months of forward shadow and a paper period with measured fills.
* Only then a small, separately authorized real-money test.
* Untouched data left: forward days from 2026-11-09, and the vault (2020-07-27 → 2022-10-31), which may be used
  **once**.

## Not reasons to reopen

* A new indicator or model on the same data.
* A different stop, holding time or parameter.
* A subset found after seeing results, such as "EPS beat, sales miss" in phase 4.
* A good month in the forward shadow.
* A larger account.
* A paid data feed without a hypothesis that needs it.

Sources: `research/runs/2026-11-07-reset/REPORT.md`, `research/runs/2026-11-07-phase4/corrected/CORRECTIONS.md`,
`docs/OPERATIONAL-STATUS.md`.

# Investing pilot report (invest-pilot-1, dry_run)

> **DRY RUN. FIXTURE DECISIONS, SYNTHETIC DATA.** 12 of 12 decisions are scripted fixtures and 0 are genuine model decisions. Prices, companies and filings are invented. Nothing here says anything about AI stock selection; it only shows the accounting and the rules.

**Verdict: NOT A RESULT: dry run with synthetic data and fixture decisions**

Outcome rule, mechanics check only: *stop this version* (meaningless here: fixture decisions).

| | VOO | AI before model costs | AI all-in |
|---|---:|---:|---:|
| ending wealth (USD) | 84.62 | 85.40 | 83.12 |
| return | -15.38% | -14.60% | -16.88% |
| excess over VOO (USD) | | 0.77 | -1.50 |
| excess over VOO (percentage points) | | 0.77 | -1.50 |
| maximum drawdown | 23.93% | 22.95% | 24.48% |
| turnover (lesser of purchases and sales / average month-end wealth) | 0.00% | 36.08% | |
| fees (USD) | 0.02 | 0.16 | |
| modeled spread and slippage (USD) | 0.05 | 0.12 | |
| dividends received (USD) | 1.32 | 1.21 | |
| model costs (USD, nominal / grown at VOO) | | | 2.40 / 2.27 |

Sessions valued: VOO 251/251, AI 251/251. Frozen packs verified: 12.

| slot | month | decider | status | actions | VOO % | problems |
|---:|---|---|---|---|---:|---|
| 1 | 2031-01 | fixture (FIXTURE) | valid | buy SYNTH01 8%; buy SYNTH02 6% | 86 |  |
| 2 | 2031-02 | fixture (FIXTURE) | valid | hold SYNTH01; increase SYNTH02 9% | 83.28 |  |
| 3 | 2031-03 | fixture (FIXTURE) | valid | hold SYNTH01; sell SYNTH02; buy SYNTH03 7% | 85.51 |  |
| 4 | 2031-04 | fixture (FIXTURE) | missed |  |  | fixture: the scripted month has no answer |
| 5 | 2031-05 | fixture (FIXTURE) | invalid | hold SYNTH01; hold SYNTH03; buy SYNTH04 5%; buy SYNTH05 5% | 75.74 | 4 stocks after the decision; at most 3 |
| 6 | 2031-06 | fixture (FIXTURE) | late | reduce SYNTH01 4%; hold SYNTH03 |  | frozen at or after the fill session's open |
| 7 | 2031-07 | fixture (FIXTURE) | valid | reduce SYNTH01 4%; hold SYNTH03 | 88.56 |  |
| 8 | 2031-08 | fixture (FIXTURE) | valid | hold SYNTH01; hold SYNTH03 | 88.03 |  |
| 9 | 2031-09 | fixture (FIXTURE) | valid | sell SYNTH01; sell SYNTH03 | 100 |  |
| 10 | 2031-10 | fixture (FIXTURE) | valid | 100% VOO | 100 |  |
| 11 | 2031-11 | fixture (FIXTURE) | valid | buy SYNTH04 10% | 90 |  |
| 12 | 2031-12 | fixture (FIXTURE) | valid | hold SYNTH04 | 90.58 |  |

Model charges: 2031-01 0.20 (fixture_estimate), 2031-02 0.20 (fixture_estimate), 2031-03 0.20 (fixture_estimate), 2031-04 0.20 (fixture_estimate), 2031-05 0.20 (fixture_estimate), 2031-06 0.20 (fixture_estimate), 2031-07 0.20 (fixture_estimate), 2031-08 0.20 (fixture_estimate), 2031-09 0.20 (fixture_estimate), 2031-10 0.20 (fixture_estimate), 2031-11 0.20 (fixture_estimate), 2031-12 0.20 (fixture_estimate)


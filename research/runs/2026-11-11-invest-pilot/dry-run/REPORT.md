# Investing pilot report (invest-pilot-1, dry_run)

> **DRY RUN. FIXTURE DECISIONS, SYNTHETIC DATA.** 12 of 12 decisions are scripted fixtures and 0 are genuine model decisions. Prices, companies and filings are invented. Nothing here says anything about AI stock selection; it only shows the accounting and the rules.

**Verdict: NOT A RESULT: dry run with synthetic data and fixture decisions**

Outcome rule, mechanics check only: *stop this version* (meaningless here: fixture decisions).

Decisions: valid 9, invalid_answer 1, missed_answer 2, missing_input 0; valid genuine decisions 0.
Model charges: reconciled 12, not_requested 0, unreconciled 0, missing 0.

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
| known model charges (USD, nominal / grown at VOO) | | | 2.40 / 2.27 |

Sessions valued: VOO 251/251, AI 251/251. Frozen packs: 12, problems: 0.
Limits: largest stock weight at a close 10.21% (sessions with drift above the cap after targets were set: 10); risk-required reductions 0; skipped discretionary trades 0; residuals above the cap 0.
Claims: 0 genuine claim(s) need blinded review, 0 pending, 0 unsupported; 26 fixture claim(s) are not reviewed.

| slot | month | decider | status | actions | VOO % | problems |
|---:|---|---|---|---|---:|---|
| 1 | 2031-01 | fixture (FIXTURE) | valid | buy SYNTH01 8%; buy SYNTH02 6% | 86 |  |
| 2 | 2031-02 | fixture (FIXTURE) | valid | hold SYNTH01; increase SYNTH02 9% | 83.28 |  |
| 3 | 2031-03 | fixture (FIXTURE) | valid | hold SYNTH01; sell SYNTH02; buy SYNTH03 7% | 85.51 |  |
| 4 | 2031-04 | fixture (FIXTURE) | missed_answer (no_answer) |  |  | fixture: the scripted month has no answer |
| 5 | 2031-05 | fixture (FIXTURE) | invalid_answer (rules) | hold SYNTH01; hold SYNTH03; buy SYNTH04 5%; buy SYNTH05 5% | 75.74 | 4 stocks after the decision; at most 3 |
| 6 | 2031-06 | fixture (FIXTURE) | missed_answer (late) | reduce SYNTH01 4%; hold SYNTH03 |  | frozen at or after the fill session's open |
| 7 | 2031-07 | fixture (FIXTURE) | valid | reduce SYNTH01 4%; hold SYNTH03 | 88.56 |  |
| 8 | 2031-08 | fixture (FIXTURE) | valid | hold SYNTH01; hold SYNTH03 | 88.03 |  |
| 9 | 2031-09 | fixture (FIXTURE) | valid | sell SYNTH01; sell SYNTH03 | 100 |  |
| 10 | 2031-10 | fixture (FIXTURE) | valid | 100% VOO | 100 |  |
| 11 | 2031-11 | fixture (FIXTURE) | valid | buy SYNTH04 10% | 90 |  |
| 12 | 2031-12 | fixture (FIXTURE) | valid | hold SYNTH04 | 90.58 |  |

## Secondary diagnostics (cannot change the verdict)

Forecast scores: 84 scored, 24 pending, 0 missing (prices) over 7 decision(s). Brier 0.2503 (constant 0.5: 0.25); selected mean excess 1.0673 pp (n=10) vs rejected 1.5346 pp (n=74); mean Spearman -0.1237 over 5 decision(s).
Horizons overlap and companies recur: these are not independent observations and prove nothing statistically. Fixture forecasts are placeholders, not assessments.
Shadow (70% VOO + 2.5% in each of 12 candidates): ending wealth 89.39, 12 stock holdings vs the AI's at most 3, fees 0.01, spread and slippage 0.06 (cost burden vs the AI -0.21); complete.
Factual accuracy is the blinded claim review above; forecast accuracy is this section; wealth after all costs is the primary result. They are separate results.


Model charges by month: 2031-01 0.20 (reconciled: fixture_estimate), 2031-02 0.20 (reconciled: fixture_estimate), 2031-03 0.20 (reconciled: fixture_estimate), 2031-04 0.20 (reconciled: fixture_estimate), 2031-05 0.20 (reconciled: fixture_estimate), 2031-06 0.20 (reconciled: fixture_estimate), 2031-07 0.20 (reconciled: fixture_estimate), 2031-08 0.20 (reconciled: fixture_estimate), 2031-09 0.20 (reconciled: fixture_estimate), 2031-10 0.20 (reconciled: fixture_estimate), 2031-11 0.20 (reconciled: fixture_estimate), 2031-12 0.20 (reconciled: fixture_estimate)

Limitations: Quantities are sized from the opening price and filled at that price plus slippage: a paper-model convention, not evidence that fractional opening-auction orders would execute. Citation checks verify quotation mechanics, not financial correctness (see claims).


# Upgrade report: trading intelligence, research and Windows redesign

Starting point: build 20 (`a5a4f98`), with docs commit `81ecb35`. All work is on branch `claude/ai-day-trading-app-i8p65e`. Release details are in section H.

Live trading was never turned on and no money was spent. Every broker call went to the Alpaca **paper** account (with your OK, using the old paper keys). The AI ran on a free local model. Nothing was purchased.

---

## I. The plain answers first

1. **Has a strategy with positive expected value been demonstrated? No.** On 81 never-used trading days, every approach that trades lost money after costs. Not trading was best.
2. **Does the statistical model outperform the rules? Only by declining to trade.** Its win probabilities are calibrated and slightly informative. It cannot predict the *size* of results, so it found nothing worth buying. That "beat" the rules' −$6.11 by losing nothing.
3. **Does the language model add measurable value? No.**
   * It approved 72 of 72 picks, producing exactly the rules' trades.
   * 14% of its explanations contradicted the app's own numbers.
   * It took a median of 43 s per answer.
4. **Too uncertain for real money? Yes. Worse than uncertain: measured negative.** Live trading stays off.
5. **Next evidence-based milestone:** collect forward shadow/paper evidence from 2026-11-09. Then test, pre-registered, ideas that *cut cost per trade*:
   * lower turnover
   * passive limit entries whose fills are verified in paper

   The measured signal is only a quarter of the cost, so better picking alone won't close the gap.

---

## A. What was actually wrong with the original system

| area | finding | confirmed or hypothesis |
|---|---|---|
| **Costs** | Build 20 rounded the SEC fee and the TAF up to a cent on **every** sale: $0.02 per $20 sale (0.10%). Alpaca's current fee schedule instead adds up each fee type per **day** and rounds that total once. The TAF is $0 for Oct–Dec 2026. Alpaca adds a CAT fee of $0.000003/share. Real cost of a $20 round trip is ≈0.17%, not 0.23%. Spread and slippage (0.13%) are most of it. | confirmed (primary sources, `research/COSTS.md`) |
| **Strategy signals** | Momentum and volume-breakout entries move about +0.03–0.04% more than an average entry over 30 minutes; the 90% range barely clears zero. Trend, opening-range and VWAP entries show about 0. | confirmed (197 development days) |
| **Moves vs costs** | The median 30-minute move is 0.20% in *either* direction, against a 0.17% break-even. The mean move is about 0. Intraday moves at this scale cannot pay for spread and slippage. | confirmed |
| **Exits** | Most trades close on the strategy's own exit rule at a small loss before costs. Only 5–10% reach the target. 11–29% were ahead by more than the cost at some point and still lost. | confirmed; "better exits would fix it" is **not** supported. Targets and stops just redistribute a ~0 average. |
| **Ranker** | It picks better than chance (+0.03–0.04% vs −0.015% for random, before costs), but not by nearly enough. | confirmed |
| **Regime** | Up-and-volatile days averaged +0.11% before costs; down-and-volatile days −0.10%. Nothing available in the morning predicted which kind of day it would be. | confirmed after the fact; "trading only good regimes" is untested |
| **Language model** | It approved everything at ~0.95 confidence and invented numbers: "stop 1.2%" when it was 0.92%, "oversold" at RSI 64, prices written as percentages. Answers took 20–100 s. | confirmed (`llm_consistency_audit.json`) |
| **Backtest pipeline** | Three findings, all fixed:<br>• The ranker scored candidates using the fill bar's open price, which wasn't known when deciding.<br>• A target counted as filled the moment price touched it.<br>• Fees were rounded per trade. | Leak confirmed. Its effect on results is small; the frozen baseline still reproduces exactly with the old rules. |

## B. Code changes

New modules:
* `dkapp/quant/`:
  * `features.py`: point-in-time features, one definition shared by research and the live app.
  * `model.py`: numpy L2 logistic and ridge regression, with a day-bootstrap ensemble for uncertainty and JSON fingerprints.
  * `registry.py`: stages experimental → shadow → paper → retired. There is no live stage. Includes rollback.
  * `gate.py`: records the model's prediction for every candidate. It can only *block* a paper entry, and only for an eligible model.
* `dkapp/research/`:
  * `ledger.py`: tracks which days are examined, reserved or forward.
  * `data.py`: refuses to load the reserved holdout, and marks its single use.
  * `dataset.py`: labels built with the backtester's pessimistic fills, plus the pre-registered rules.
  * `evaluate.py`: walk-forward with an embargo, and the go/no-go judge.
  * `stats.py`: day-block bootstrap, Holm correction, calibration, AUC, minimum detectable effect.
  * `diagnose.py`: the failure analysis in section A.
  * `economics.py`: break-even and capital scenarios.
* `dkapp/ai/consistency.py`: hands the model app-computed facts and rejects contradictory explanations.
* `dkapp/ui/chrome.py`, `TitleBar.qml` and `WindowButton.qml`: the integrated window frame (section F).

Changed:
* `dkapp/risk/fees.py`: dated, verified fee schedule with daily rounding. `CostModel.legacy_build20()` reproduces the old rule.
* `dkapp/backtest/engine.py`:
  * targets must trade through by 1 bp to fill
  * the ranker decides on the signal bar's close
  * buy-side CAT fee
  * `with_daily_rounding`
  * `BTConfig.baseline_build20()`
* `dkapp/engine/trading_engine.py`:
  * model assessment in every decision
  * a "No candidate demonstrates an adequate expected advantage after costs." summary
  * effective decision mode: a language model has no authority in live mode
  * signal-to-order latency recorded
  * overnight-exposure warning
* `dkapp/config.py`: settings schema v3 adds `quant` settings. The default decision mode is now rules + ranker; v2's untouched `ai_confirm` default migrates to it.
* UI:
  * Performance: "what the evidence says"
  * AI activity: the model's estimate on every candidate
  * chart labels no longer clip
  * narrow-card fixes

Preserved and re-verified: everything listed as a build 20 safeguard. That includes:
* confirmed-stop protection
* unconfirmed orders treated as unprotected
* unique order ids
* restart and network recovery
* emergency stop and limits
* app-managed capital
* settlement handling
* the installer's fail-closed checks

## C. Financial feasibility ($100 account, $20 positions)

| position | spread | slippage | regulatory fees + daily rounding | **break-even per round trip** |
|---|---|---|---|---|
| $20 | 0.030% | 0.100% | 0.040% | **0.170%** |
| $100 | 0.030% | 0.100% | 0.010% | 0.140% |
| $1,000 | 0.030% | 0.100% | 0.003% | 0.133% |

A larger position only dilutes the small fixed fee rounding. The 0.13% of spread and slippage stays. These are *modeled* assumptions: 3 bps quoted spread and 5 bps slippage per side.

Hypothetical month: 21 days, 4 trades a day, positions of 20% of capital. This is arithmetic, not a forecast.

| capital | −0.25%/trade | −0.10% | 0% | +0.05% | +0.10% | +0.20% | 90% range at +0.05% |
|---|---|---|---|---|---|---|---|
| $100 | −$4.20 | −$1.68 | $0 | +$0.84 (0.84% of account) | +$1.68 | +$3.36 | −$1.77 … +$3.45 |
| $500 | −$21.00 | −$8.40 | $0 | +$4.20 | +$8.40 | +$16.80 | −$8.84 … +$17.24 |
| $1,000 | −$42.00 | −$16.80 | $0 | +$8.40 | +$16.80 | +$33.60 | −$17.68 … +$34.48 |
| $5,000 | −$210 | −$84 | $0 | +$42 | +$84 | +$168 | −$88 … +$172 |
| $10,000 | −$420 | −$168 | $0 | +$84 | +$168 | +$336 | −$177 … +$345 |

* **What the evidence says right now:** about −0.13% per trade. At $100 that is about −$1.70 a month; at $10,000, about −$170.
* **Capital needed for $100 a month** (4 trades a day, after costs): **$11,900** at a +0.05% edge, $5,950 at +0.10%. Neither edge exists today.
* **A paid data feed** ($99/month, not bought) would cost more than any plausible edge earns below about $12,000 of capital.
* **Denominators:** "per trade" means P/L ÷ the $20 position. "% of account" means P/L ÷ $100. A 1% gain on a $20 trade is 0.2% of the account.

## D. Research comparison (equal conditions)

Conditions: the same costs, 2 slots of $20 on a $100 account, and one position per symbol.

The **holdout** is 2026-05-01 → 2026-08-26 (81 days). It was registered in the ledger before the download and evaluated once (`research/HOLDOUT_USED.json`). Development is 197 days of walk-forward. Full report: `research/runs/2026-11-06-round1/REPORT.md`.

| approach | dev trades | dev per trade | holdout trades | **holdout per trade** | holdout 90% range | holdout $ | max drawdown $ | verdict |
|---|---|---|---|---|---|---|---|---|
| doing nothing | 0 | 0 | 0 | **0** | — | $0.00 | 0 | baseline |
| rules + ranker (app logic) | 434 | −0.136% | 230 | **−0.133%** | −0.23 … −0.04% | −$6.11 | 6.34 | baseline |
| H1 mean reversion | 1,477 | −0.154% | 699 | **−0.153%** | −0.19 … −0.12% | −$21.40 | 21.63 | rejected |
| H2 relative strength | 379 | −0.142% | 157 | **−0.134%** | −0.41 … +0.16% | −$4.20 | 9.07 | rejected |
| H3 market intraday momentum | 108 | −0.229% | 49 | **−0.268%** | −0.29 … −0.25% | −$2.63 | 2.63 | insufficient (and negative) |
| random pick | 3,785 | −0.127% | 1,572 | −0.129% | −0.15 … −0.11% | −$40.50 | 40.50 | baseline |
| H4 statistical model (dense) | 21 | −0.071% | 0 | — | took no trades | $0 | 0 | insufficient |
| H5 rules + model filter | 4 | −0.519% | 0 | — | took no trades | $0 | 0 | insufficient |
| rules + 3B LLM confirmation (build 20 replay) | 72 | −0.230%* | — | — | — | −$3.31* | — | identical to the rules |

\* Build 20's 40 days under its old cost rule.

* Holm-adjusted p = 1.00 for every hypothesis.
* With 2× slippage, every approach is −0.16% to −0.24% per trade.
* Model calibration on the holdout:
  * **H5:** AUC 0.61, Brier 0.1735 vs 0.1784 for the base rate, calibration error 0.006. AUC for the *expected result* is 0.49, i.e. no skill.
  * **H4:** AUC 0.56, calibration error 0.008.

## E. Model readiness

| model | stage | eligible for forward paper gating? |
|---|---|---|
| `strategy-filter-v1` (H5, ships in the app) | **shadow**: shows its estimate, never trades | **No.** It found no positive-expected candidates. |
| H4 dense model | experimental (research only) | No |
| H1–H3 rules | rejected / insufficient | No |
| local 3B language model | no trade authority (live); paper experiments only | No. It fails the latency and consistency checks. |

No candidate improves on the original application in a way that makes money. Forward shadow evidence starts accumulating automatically on 2026-11-09. Promotion requires:
* at least 100 forward trades over at least 30 days
* positive after costs with Holm-adjusted p < 0.05
* your approval

## F. Desktop redesign

The window keeps Windows' native window behaviour but draws its own title bar.

* **How it's built:**
  * The window is frameless for Qt, with the native WS_THICKFRAME, WS_CAPTION and min/max styles added back. That gives Aero snap, Win+arrow keys, animations, the taskbar, the system menu, the DWM shadow, rounded corners, and a dark graphite border.
  * `WM_NCCALCSIZE` removes the system caption. A maximized window is clipped to the monitor's work area.
  * `WM_NCHITTEST` maps our title bar: resize edges, a drag caption, and **HTMAXBUTTON over our maximize button**, which is what Windows 11 Snap Layouts needs.
* **What's in the 50 px title bar:**
  * the dk. icon and name, aligned with the sidebar column
  * the current page
  * market session status
  * model stage
  * account mode
  * 46 px minimize / maximize-restore / close buttons, with a red hover on close
* **On other platforms:** Linux uses Qt's system move and resize. macOS keeps its native frame.
* **Screenshots:** `screenshots/`. They are taken from the real running app at 1366×768 (100%), 1536×864 (125%) and 1280×720 (150%), plus a live paper session with real market data. Windows CI screenshots are uploaded as the `windows-ui-screenshots` artifact.

**Verified automatically on Windows CI** at 100/125/150% via `--chrome-selftest`:
* every hit-test region: maximize → Snap target, caption, all edges and corners, title-bar buttons, page content
* the maximized client area equals the monitor work area (no overflow, taskbar visible)
* restore and minimize from our buttons
* a synthetic Win+Up (informational only)

**Not verified:**
* the Snap Layouts flyout as a person sees it (it needs a real mouse on Windows 11)
* dragging between monitors with different scaling
* the high-DPI look on a physical screen

## G. Execution safety

All build 20 safeguards still pass their tests (273 automated tests). The 8 real paper-account integration tests passed again, including fractional orders and broker stops, and the account was left with no positions and no orders. The $100 allocation was re-checked against the real $100,000 paper balance ($400,000 buying power): the app still sizes $20 per trade, a $2 daily loss limit and $1 risk per trade.

Remaining operational risks:
* **Overnight exposure.** Fractional-share stops are DAY orders and expire at the close. If the laptop is off or a sale fails at the close, a position sits overnight with no broker stop, and an opening gap can't be prevented. The app now warns as soon as it sees this (notification and activity log), places a new stop at the open and sells after the opening minutes. That is mitigation, not prevention.
* **While the app is off or offline,** only broker-side stops protect positions, and only during that day's session.

## H. Tests and release

* Local: **273 passed**, 0 failed. 8 paper-integration tests are skipped in the normal run and were **run separately on the real paper account: 8 passed**.
* Reproductions: the build 20 backtest and its AI-mode comparison reproduce exactly (`scripts/reproduce_baseline.py`).
* CI and release: see the final message (filled in after GitHub confirms them).

## Limitations

* **Data:** IEX only (a few % of U.S. volume) and 40 symbols, chosen as liquid names today, so survivorship is possible. One year of history. Fills are modeled from bars, with no quotes.
* **Holdout:** used once. All history is now development data; only forward observation is unseen.
* **Stronger models:** a stronger local language model was not tested. On a 4-core laptop it would be slower still, and intraday signals go stale while it answers. A paid model was not tried (no purchases).
* **Security:** the paper keys used today are the ones previously pasted into chat (used with your permission). Regenerate them in the Alpaca paper dashboard.

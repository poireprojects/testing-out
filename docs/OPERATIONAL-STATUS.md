# Operational status (closeout, 2026-11-07)

**Research is closed.** No strategy has shown a profit after costs, and no new research phase is authorized.
The app (build 43) is unchanged; builds 47 and 48 contain the same app.

I describe what the code does in each mode. I can't see the settings on your computer, so check the two
settings named below in the app itself.

## 1. Strategies

**No strategy has evidence of profit.** None of the app's five strategies passed its backtests, and none of
the 13 pre-registered research hypotheses (nor the language-model filter) passed.

| strategy | default setting | live (real money) | paper |
|---|---|---|---|
| intraday momentum, trend following, opening-range breakout, VWAP reclaim, volume-confirmed breakout | enabled | **blocked**: live mode needs both your typed authorization and backtest evidence for the strategy, and none has it | **can place paper orders** while autopilot is on, because the setting **paper can trade untested strategies** is on by default |

The research hypotheses were never given order authority:

| phase | hypotheses | result |
|---|---|---|
| round 1 | H1 mean reversion, H2 relative strength, H3 market intraday momentum, H4/H5 statistical models, H6 language-model review | rejected or insufficient |
| phase 2 | P2-H1 momentum to the close, P2-H2 gap stocks in play, P2-H3 passive limit entry, P2-H4 ETFs overnight, P2-H5 SPY trend filter | rejected |
| phase 3 | P3-H1 industry relative strength, P3-H2 turn of the month | rejected |
| phase 4 | P4-H1 earnings-day continuation | rejected (NO-GO; corrected in `research/runs/2026-11-07-phase4/corrected/`) |

**To stop the app from placing any new paper orders**, turn the **autopilot** switch off (the app then shows
"autopilot is off — watching only"). Or turn off **paper can trade untested strategies**, which blocks all five
strategies in paper mode as well. These are settings, not code changes, and the forward observations below keep
running either way.

## 2. Forward observations

These run only while the app is open in **paper** mode with real market data. Demo mode records none of them.
Nothing here places an order, and missed days are reported as missed, never backfilled. Forward evidence
counts from **2026-11-09** (`research/ledger.json`).

| recorder | what it records | code |
|---|---|---|
| forward ledger | every ranked candidate of the five strategies, taken or not, scored later with hypothetical costs | `stockpilot/research/forward.py` |
| gap-stock observer | P2-H2 and P2-H2b gap candidates each morning | `stockpilot/engine/trading_engine.py` |
| daily shadow | P3-H1 sector relative strength, P3-H2 turn of the month, SPY buy-and-hold benchmark (month-end decisions) | `stockpilot/research/daily_shadow.py` |
| shadow tracker and statistical-model predictions | hypothetical results of rules decisions, and the model's prediction for each candidate (it has no authority: `gate_paper_entries` is off) | `stockpilot/engine/shadow.py`, `stockpilot/quant/` |

All four keep running with autopilot off. The ledger is append-only and hash-chained.

## 3. What can run without placing orders

| activity | places orders? |
|---|---|
| paper mode with **autopilot off** ("watching only") | **No new entries.** It still manages positions that are already open, which can mean protective stop, exit or cancel orders for them. With no open positions, it places none |
| demo mode | No. It uses a local simulator and never contacts Alpaca's trading API |
| in-app backtests | No: historical data only |
| `--self-test` and window self-tests | No |
| research and report scripts in `scripts/` | No: historical data only. The phase-4 collectors also refuse anything that looks like a live key and refuse the vault (2020-07-27 → 2022-10-31). **Exception:** `live_paper_shots.py` runs the real app in paper mode and can place paper orders unless started with `--no-autopilot` |
| paper mode with **autopilot on** | **Yes**: paper orders from the five strategies |
| live mode | Not possible as configured: no live authorization, and no strategy has evidence |

## 4. Credentials to rotate

**The Alpaca paper key whose ID begins `PKJB7U`** was shared in conversation earlier, then used, at your
instruction, for read-only historical data and news only. No trading endpoint was called with it. It should
be replaced:

1. In Alpaca's dashboard (paper account), regenerate the API keys. This invalidates the old key everywhere.
2. In the app, enter the new key and secret under **brokerage — Alpaca → paper trading keys**. They are stored
   in the operating system's credential store (Windows Credential Manager or macOS Keychain), never in a file.
3. In claude.ai, open the cloud environment **testing-out** and delete or replace its two network secrets
   for the old key: **`data.alpaca.markets`** (header `APCA-API-SECRET-KEY`) and
   **`paper-api.alpaca.markets`**.

Nothing else needs rotating for this project:
* The repository contains no secrets: no key values, and no key ID beyond the prefix above.
* The SEC contact email was passed at run time only and is not stored.
* Any AI provider key you entered yourself is not affected by this project.

## 5. Phase 5 prototype (simulation only)

`autotrader/` is a separate research package: an autonomous research-and-decision loop (SEC filings, a language
model or simple baselines, the app's own risk engine, simulated execution, a hash-chained ledger and a daily
report). It is not part of the desktop app, is never imported or bundled by it, and cannot place an order: its
owner policy permits only `simulate`, and the exposed paper key is refused in code. See
`research/PHASE5-AUTONOMOUS.md`.

## 6. Releases

* **Pushes that only change research, tests or documentation no longer publish an installer.**
  `packaging/app_changed.py` compares each push with the newest published build. The test suite still runs
  on every push. To force a rebuild, run the workflow manually.
* Existing releases are untouched.
* Your installed app and its data folder are unaffected by any of this.

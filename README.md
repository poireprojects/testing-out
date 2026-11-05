# don’t know what im doing

<img src="dkapp/assets/icons/dk.png" width="96" align="right" alt="dk. app icon">

A personal desktop app that scans ~400 U.S. stocks during market hours, finds day-trade setups with simple
rule-based strategies, asks an AI for a second opinion, and places trades by itself **only when a separate set of
fixed safety rules says yes**. It starts in a demo simulator, then paper trading (fake money, real prices) at
Alpaca. Real-money trading stays locked until you explicitly authorize it.

> **Honest expectations.** This software can lose money. Nothing here shows that any strategy is profitable, and
> on a $100 account the regulatory fees and the bid/ask spread alone eat about 0.2% of every round trip. The app
> is built to *measure* whether a strategy has an edge after costs — and to refuse real money until there is
> evidence. It's not financial advice.

---

## 1. Install it (no terminal needed)

The installers are built automatically on GitHub for each update.

1. Open this repository on GitHub → **Actions** → the latest **"test & build desktop app"** run → **Artifacts**.
   (Tagged versions also appear under **Releases**.)
2. Download the one for your computer:

| Your computer | Download | Then |
|---|---|---|
| **Windows 10/11** | `dontknowwhatimdoing-windows-installer` | Unzip, double-click `dontknowwhatimdoing-setup-1.0.0.exe`. It installs for your user only (no admin needed) and adds a **Start menu** entry plus an optional **desktop shortcut**. If Windows SmartScreen warns about an unknown publisher, click **More info → Run anyway** (the app isn't code-signed; signing costs money). |
| **Mac (Apple Silicon)** | `dontknowwhatimdoing-macos-dmg` | Open the `.dmg` and drag **don’t know what im doing** into **Applications**. The first time, **right-click the app → Open → Open** (it isn't notarized by Apple, which needs a paid developer account). Keep it in the **Dock**: right-click its Dock icon → Options → Keep in Dock. |

After that, launch it like any other app. Closing the window keeps it running in the **system tray / menu bar**
(trading continues); use the tray icon → **quit** to exit completely.

<details><summary>Prefer to build it yourself?</summary>

Install Python 3.12 from python.org, then double-click:

* Windows: `packaging\windows\BUILD-INSTALLER.bat` → installer appears in `dist\installer`.
* macOS: `packaging/macos/BUILD-APP.command` → `dist/dontknowwhatimdoing-macos.dmg`.
* Linux: `packaging/linux/build_linux.sh` → adds the app to your applications menu.

Or run from source: `python -m dkapp` (after `pip install -r requirements.txt`).
</details>

## 2. First launch: demo mode

You start in **demo mode**. A built-in simulator generates fake prices, so you can watch the scanner, the AI
activity log, trades and the reports actually work. An amber banner says *demo* everywhere, and demo results live in
their own database. They are never shown as real performance.

## 3. Connect Alpaca paper trading (free)

1. Create a free account at **alpaca.markets** and open the **paper trading** dashboard.
2. Generate an **API key** (you get a *key ID* and a *secret*).
3. In the app, go to **settings → brokerage — Alpaca**, paste both into **paper trading keys**, and click
   **test & save**. The app checks the keys really belong to a *paper* account, then stores them in your computer's
   secure credential store (Windows Credential Manager / macOS Keychain) — never in a file.
4. In **settings → trading mode**, choose **paper (practice)**.

## 4. Set up the AI (free, local)

By default every entry needs the AI's approval. The free option runs on your own computer:

1. Install **Ollama** from ollama.com and open it.
2. In a terminal, run once: `ollama pull llama3.1:8b` (≈ 5 GB download).
3. In **settings → ai**, click **test ai connection**.

No AI yet? Switch **who decides entries** to **rules only**. If the AI is offline, times out, or gives an invalid
answer, **no AI-dependent trade happens** — the app never guesses.

**Optional cloud AI (Claude):** choose **cloud (paid)**, add an Anthropic API key, pick a model, and set a monthly
budget (default $5). When the budget is reached, cloud calls stop. Every call's actual cost is recorded and shown on
the *ai activity* and *performance* pages.

---

## How it decides (in plain English)

1. **Scan.** Every ~60 s it pulls quick snapshots for ~400 liquid stocks and ETFs (two to five requests in total) and
   ranks them by unusual volume, intraday strength, liquidity and spread. The top 20 get minute-by-minute analysis
   every ~15 s.
2. **Spot a setup.** Five simple strategies look for: **intraday momentum**, **trend-following pullbacks**,
   **opening-range breakouts**, **VWAP reclaims** and **volume-confirmed breakouts**. Every indicator is computed
   only from bars that already happened (tested for look-ahead bias).
3. **Second opinion.** The AI gets the numbers (indicators, recent bars, market context, costs) and must answer in a
   strict JSON format: buy/skip, confidence, evidence quality, stop, target, reasoning. Answers are validated in code.
4. **Final say: the risk engine.** Deterministic rules the AI cannot override (defaults below). It can shrink a
   trade, tighten its stop, or block it, and says why in plain English.
5. **Execution.** A limit order slightly above the ask (avoids bad fills in fast markets), then a **broker-side stop
   order** so the position stays protected even if your computer turns off. Targets, trailing stops (to break-even
   after +1R, then trailing), time limits and strategy exits are handled by the app. **Everything is sold before the
   market closes.**

### Default safety rules (all adjustable in settings)

| Rule | Default |
|---|---|
| Starting capital | $100 |
| Max per position | $20 (scales with the account when reinvesting) |
| Positions at once | 2 |
| Daily loss limit | $2 — then no new trades until tomorrow |
| Max loss per trade if the stop hits | 1% of the account; sizes shrink to fit, and a stop-out can never be sized to break the daily limit |
| Stop distance | 0.4%–3%; setups that need wider stops are skipped |
| After 3 losses in a row | done for the day; each loss also shrinks the next trade by 25% |
| Always | no margin, no leverage, no shorting, no options, no crypto, no stale quotes (> 90 s), no wide spreads, DAY orders only |
| Emergency stop | big coral button: halts trading, cancels orders, sells every position the app opened |

Stop orders and loss limits **reduce** risk but cannot guarantee a maximum loss: prices can gap past a stop.

### Growing the account (compounding)

* **Reinvest** (default): limits follow your account. At $105 the max position becomes $21; at $95 it becomes $19.
* **Fixed allocation**: the app trades with a set amount and leaves profits alone.

Sizes use **settled cash** and actual buying power, not the displayed portfolio value, and they only ever shrink
after losses. Risk is never increased to win money back.

### Is the AI actually helping?

For every setup the AI reviews, the app follows a *shadow trade* with identical exit rules. The **performance** page
compares setups the AI approved with those it skipped. If the AI's picks don't beat its skips, use rules-only mode.

---

## Brokerage facts this design is based on (checked November 2026)

* **The $25,000 pattern-day-trader rule is gone.** FINRA's Rule 4210 amendments took effect **June 4, 2026**, and
  Alpaca moved to an intraday-margin framework that day. A legacy guard is still in settings (off by default) in case
  a broker keeps enforcing day-trade counts.
* **A $100 account has no margin** (margin needs $2,000+). Sale proceeds settle **T+1**. To avoid good-faith
  violations entirely, the app spends **only settled cash** by default. Example: with $100 settled and $20 positions,
  about five round trips per day are possible before the morning's sale proceeds are tied up until tomorrow.
* **Fractional shares**: market, limit, stop and stop-limit orders, all **DAY only**. Bracket orders with fractional
  quantities aren't clearly supported, so the app places a separate stop order. If Alpaca rejects it, the app watches
  the stop itself, labels the position **app-watched stop** and warns you that it's unprotected while the app is off.
* **Free market data is IEX-only.** Real-time prices come from one exchange (IEX, roughly 2–3% of U.S. volume), and
  full-market SIP data is 15 minutes delayed on the free plan. IEX prices track liquid stocks closely, but volumes are
  a small slice of the market and quotes are IEX's, not the national best bid/offer. The scanner says so, and volume is
  always compared with IEX's own history. The full SIP feed costs about $99/month, which isn't sensible for a $100
  account.
* ~200 data requests/minute on the free plan (the app throttles itself to 180).

## What happens when your laptop…

| Situation | What happens |
|---|---|
| **Is open & online** | Fully autonomous: starts scanning at 9:30 ET, trades, flattens before 4:00 ET, writes the daily summary. Optional "keep awake during market hours" prevents *idle* sleep. |
| **Window closed** | Keeps running in the tray/menu bar. |
| **Sleeps, lid closed, or shuts down** | **No trading happens.** Open positions keep their broker-side stop orders, but those expire at the close, so a position could be held overnight unprotected. When the app starts again it reconciles with Alpaca first, then sells leftover positions after the next open (default). |
| **Loses internet** | No new entries. Retries automatically. Broker-side stops still protect positions. On reconnect it reconciles every order and position. |
| **Alpaca is down** | Same as no internet. Unconfirmed orders are looked up by their unique ID before anything is retried, so duplicates aren't created. |
| **App crashes / restarts** | Orders are saved *before* they're sent, and everything is reconciled on startup. Tested: no duplicate orders or stops. |

Launch-at-login is in **settings → app behavior** (or choose it in the Windows installer).

## Monthly operating cost (estimate)

| Item | Cost |
|---|---|
| Alpaca paper trading, commissions | $0 |
| Market data (IEX) | $0 |
| Local AI (Ollama) | $0 (uses your computer) |
| Regulatory fees on sales (~$0.02 per sale on $20 trades) | ≈ $0.40–$4 depending on trade count |
| Optional cloud AI, ~10–20 decisions/day | Haiku 5.5 ≈ $0.15–0.30 · Sonnet 5.5 ≈ $2.50–5 · Opus 5.5 ≈ $5–11 (hard-capped by your budget) |
| **Typical total with local AI** | **≈ $1–4 / month**, nearly all regulatory fees |

The **performance → operating costs** card shows the real numbers as they happen.

## Going live (real money)

1. Paper-trade first. We suggest at least **20 trading days**.
2. Run a **backtest** (performance → strategy lab) on real Alpaca history. Only strategies marked **EVIDENCE ✓** can
   trade real money. That requires: profitable after costs on the held-out 40% of days, a profit factor of 1.15 or
   more, beating random entries that use the same stops and targets, and a p-value below 0.10.
3. Add your **live** Alpaca keys (checked to be a live account), confirm the four statements, type
   `i authorize real-money trading`, then switch the mode to **live**.
4. Raising any risk limit later automatically pauses live trading until you re-authorize. Tightening limits doesn't.

---

## Testing

```
pip install -r requirements-dev.txt
python -m pytest -q
```

158 automated tests (156 run anywhere; 2 need your Alpaca paper keys) cover market-data ingestion and batching, indicators (including look-ahead checks), every
strategy, AI response validation (malformed, out-of-range and missing fields → no trade), Ollama/Anthropic providers,
position sizing and compounding, settled cash (T+1 across weekends and holidays), fees, every risk rule, idempotent
order submission, duplicate prevention, lost responses, partial fills, network failures, crash/restart recovery,
external closes, protective-stop fallback, trailing stops, emergency stop, daily loss limit, end-of-day flatten, a
complete simulated trading day (P/L reconciled to the cent against the simulated broker's cash), paper/live
separation, the backtester's realism rules, and loading the real interface (`--self-test`).

### What is **not** verified yet

* **Real Alpaca calls.** The build environment couldn't reach Alpaca and had no keys. The adapters are tested
  against fakes that mirror alpaca-py's models. `tests/test_alpaca_integration.py` runs against your paper account
  when you set `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET`, and `ALPACA_PAPER_ORDER_TEST=1` adds a tiny
  far-from-market order that is cancelled immediately.
* **Whether Alpaca accepts fractional stop orders** for your account. If it doesn't, the fallback above kicks in.
* **Real historical backtests.** They need Alpaca keys. Only synthetic demo backtests have been run, and on that
  random data the evidence gate correctly found *no edge* in any strategy.
* **A real AI model.** Ollama and Claude responses are tested with recorded and fake responses. No real model was
  called, because Ollama isn't installed here and a cloud call would have cost money without your approval.
* **Native notifications, tray menus and installers on your actual machine.** CI builds and self-tests the Windows
  installer and the macOS app, and a Linux build was verified here. Visual behavior of tray and notifications on
  Windows/macOS hasn't been observed by a person.
* **Profitability.** Unknown, and possibly negative. That's what paper trading and the evidence gate are for.

## Where things live

* Settings: `%APPDATA%\dontknowwhatimdoing` (Windows) · `~/Library/Application Support/dontknowwhatimdoing` (macOS)
* Databases (one per mode: `trading_demo.db`, `trading_paper.db`, `trading_live.db`) and logs: open them from
  **settings → about & diagnostics**.
* Secrets: only in the OS credential store.

## Project layout

```
dkapp/
  scanner.py          two-stage market scanner
  strategies/         the five rule-based strategies
  analysis/           indicators + causal feature frame
  ai/                 prompts, providers (Ollama, Anthropic), validation, budget
  risk/               risk engine, sizing/compounding, fees, settlement ledger
  broker/             Alpaca adapter + simulated broker
  execution/          idempotent order manager
  engine/             trading engine (session state machine, reconciliation, recovery), shadow trades
  backtest/           bar-by-bar backtester, out-of-sample evidence gate
  data/               Alpaca + demo market data, universe, rate limiting
  ui/                 PySide6/QML interface, custom charts, tray, notifications
packaging/            PyInstaller spec, Windows installer, macOS/Linux build scripts
tests/                automated tests
```

Fonts: Space Grotesk and Inter, both under the SIL Open Font License (licenses in `dkapp/assets/fonts`).
For personal use only.

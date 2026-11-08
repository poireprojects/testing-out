# StockPilot changelog

What changed in each version of the app, newest first. Each GitHub release shows the notes for its own version.
Build numbers are the GitHub release numbers (`build-N`). Before version 1.1.0 the app had another name. Every
1.0.0 build was published under it, and installing StockPilot upgrades any of them in place.

None of these changes turned on live trading. Every broker test used Alpaca's **paper** (pretend-money) account.
No strategy tested so far has passed its pre-registered test after trading costs.

## 1.2.0 · 2026-11-08

### Release notes and version numbers
- Every GitHub release now has notes saying what changed, and its title shows the version: "StockPilot 1.2.0
  (build N)" instead of just "build N". The notes come from `CHANGELOG.md` in the repository.
- The notes on the older releases were rewritten from the same file, so every published build says what it
  contained.
- A new app build is published only when the version number was raised and `CHANGELOG.md` has a section for
  it, so two different installers can never carry the same version.
- Version 1.2.0: shown in the sidebar, in settings → about, in the installer, and in Windows' file properties
  and Installed Apps.

No change to trading, safety rules, settings or your data.

## 1.1.0 · build 57 · 2026-11-07

### The app is now StockPilot
- New name and logo everywhere: the window and title bar, the tray icon, the sidebar, the Start menu and desktop
  shortcuts, Installed Apps, the macOS app, the installer, and the download files
  (`StockPilot-windows-setup.exe`, `StockPilot-macos.dmg`). The logo is a lime price line that climbs into an
  arrowhead, in the same rounded square, colours and fonts as before.
- The "StockPilot" wordmark in the title bar and sidebar ("Stock" in white, "Pilot" in lime).

### Upgrading keeps everything
- Install StockPilot over the earlier version and nothing is lost. Your settings, trade history, logs, test
  copies, saved API keys and launch-at-login all move to StockPilot.
- The Windows installer closes the earlier version only after its own safety check says it is safe (never with
  open positions). It then removes the old program folder and shortcuts, and leaves a single "StockPilot" entry
  in Installed Apps.
- On a Mac, the first time StockPilot opens it does the same and moves the earlier app to the Trash.
- Nothing is moved while the earlier version is still running, and StockPilot won't start alongside it (two
  copies must never trade one account).
- Orders the earlier version left at the broker are still recognised as the app's own, so their stops keep
  being managed.
- A new automatic test installs the last earlier release on a real Windows machine, runs it with data, a saved
  key and launch-at-login, and upgrades it. A release is published only if every check passes.

### Fixed
- Launch-at-login: no warning in the log on every start when Windows has no launch-at-login entries at all.

### Also since build 48 (research and project only, no app change)
- Phase 4 result: trading the day of good earnings news did not work after costs (−0.19% per trade after a fee
  correction; no better than an equivalent price-only rule).
- A strategic review of where retail trading profits could come from, with sourced evidence and a capital and
  risk simulation. Its conclusion: none of the approaches it found is a viable route to salary-level income.
- Release gate: research-only changes run the tests but no longer publish a new installer.
- `docs/OPERATIONAL-STATUS.md` (what runs, what is off, what to rotate) and `research/REOPENING-CRITERIA.md`.
- Phase 5: a simulation-only prototype of an autonomous research-and-decision loop. It is kept outside the
  desktop app and never places orders. A paid model check is planned with a $12.50 hard limit and is not active.

## 1.0.0 · builds 8–48 · 2026-11-06 to 2026-11-07

Published under the app's earlier name. Every build below shows version 1.0.0 in the app.

### Build 48 · 2026-11-07
- No change in the app. Research data collection runs several workers at once (phase 4).

### Build 47 · 2026-11-07
- No change you would notice in the app; it adds research tools only.
- Phase 4 research: an audit of 50 SEC filings found that trading company announcements during market hours
  isn't feasible. Most news comes out before the open, and early-morning spreads are wide. The next idea
  (earnings-day continuation) was registered with its rules frozen before any result.

### Build 43 · 2026-11-07
- New: the app records the multi-day research rules as shadow decisions on new market data (sector-ETF
  strength and turn-of-the-month on SPY). It places no orders, and decisions are never filled in after the fact.
  Performance shows them in a "multi-day shadow" table, plus a phase 3 evidence card.
- Fixed: the 9:35 gap observer stamped its records at 9:35 even when it ran later. It now records the real
  decision time, so outcomes only use prices from after the decision.
- Research phase 3 (multi-day rules on data back to 1926): turn-of-the-month looked strong before 2006 but
  vanished in the 2006–2020 holdout. Industry strength failed the strict test. Neither rule is used for trading.

### Build 38 · 2026-11-06
- New: a forward research record. Every candidate the app considers (taken or rejected) is written down at the
  moment of the decision, in a record that can't be edited or reordered without detection. It is scored only
  after the close, from prices that came later. Performance shows "forward evidence — new market data only".
  It isn't collected in demo mode.
- New: a phase 2 evidence card on Performance.
- Fixed: small negative percentages and amounts could be shown without their minus sign.
- Research phase 2: five new day-trading ideas (momentum held to the close, gap stocks, passive entries,
  overnight ETFs, an SPY trend filter) were all rejected on unseen data.

### Build 32 · 2026-11-06
- Fixed: the sidebar's footer line pushed every sidebar row about 9 px past the edge. An automatic test now
  measures the sidebar and title bar at five window sizes and display scales.

### Build 31 · 2026-11-06
- Fixed: the safety-rules list fits in a 1280-pixel-wide window.
- The new Windows title bar was checked on a real Windows desktop at 100%, 125% and 150% scaling.

### Build 30 · 2026-11-06
- New (Windows): an integrated title bar showing the page, market session, model stage and account mode. It
  keeps Windows behaviour: drag, double-click, Snap Layouts, Aero Snap, Win+arrow keys, the shadow, rounded
  corners and the system menu. On macOS the native frame is kept.
- New: "what the evidence says" on Performance: historical holdout results per approach, with ranges, the
  statistical model's calibration and the language model's audit. It is clearly marked as history, not your
  account.
- New: AI activity shows each candidate's estimated result and win chance from the model (shadow only). The
  last real comparison stays on screen between scans.
- New: the overview says how much of a large paper account the app actually trades.
- Changed: an AI answer older than 30 seconds (was 90) is treated as stale and the trade is skipped.
- Fixed: chart axis labels no longer get cut off; price labels have room for six-figure values.

### Build 27 · 2026-11-06
- New: a statistical model gives every candidate a win-chance and expected result, in shadow mode (it can't
  place or approve trades).
- New: a consistency check on the AI's answers. The app gives the AI the real numbers (stop and target
  distance, reward after costs), and an explanation that contradicts them is rejected, so no trade is made.
- Changed: the default decision mode is rules + ranker. In live mode, a language model can never authorize a
  trade.
- New: "NO TRADE" is shown when nothing has an edge. The app measures the delay from signal to order, and says
  once a day when a position is left open overnight.
- Research round 1: every pre-registered idea failed on unseen data. The strategy filter ships as a shadow
  model only.

### Build 23 · 2026-11-06
- No change in the app. New history was registered before download, with part of it kept back as an unseen
  test.

### Build 22 · 2026-11-06
- Changed: fees follow the current Alpaca, SEC and FINRA schedules, by date. That includes the CAT fee and
  Alpaca's daily rounding of each fee type (the old estimate rounded every sale up to a cent).
- Fixed (strategy lab backtests): a profit target only fills when the price trades through it. The ranker
  decides on the signal bar, not the next one. Buy-side CAT fees are included.
- Research: the results so far are frozen so they can be reproduced exactly, and a record of which data has
  already been looked at was started.

### Build 21 · 2026-11-06
- No change in the app. A report on the build 15 review fixes.

### Build 20 · 2026-11-06
- Fixed (important): trade sizes now come from the app's own money (starting capital plus its own profits and
  losses), not the whole brokerage account. On a $100,000 paper account the limits had grown to $20,000 per
  trade. This was caught before any order was placed.
- New: every database marks its broker orders with its own tag, so two copies of the app (or a test) on one
  account never mix up each other's orders.
- New: the Windows installer is published as soon as it passes its tests, without waiting for the Mac build.
- Results on 40 days of real market history: every strategy lost money after costs. A live paper session with
  the AI is documented, with screenshots.

### Build 17 · 2026-11-06
- Safety: a stop order counts as protection only once the broker confirms it. An unconfirmed stop is never
  doubled up; the app watches the price itself and flags the position. In live mode, new trades are blocked and
  the position is sold if no stop is confirmed within 10 minutes.
- Safety: orders of the app's own that are left at the broker are taken back over or cancelled. Orders the app
  didn't place are never touched.
- Safety (installer): every safety check fails closed. If the check can't run, crashes or times out, the update
  or uninstall stops. A copy that won't close is force-closed only after a fresh check finds no open live
  positions, and only if you agree (the default answer is No).
- Changed: accounts under $2,000 follow Alpaca's limited-margin rules: same-day sale proceeds can be reused,
  and spending never exceeds cash.
- Fixed: the Windows installer failed to build in build 16.
- New tests on the real Alpaca paper account (fractional orders, stops, cancels, rejections).

### Build 15 · 2026-11-06
- Safety: when the laptop wakes from sleep, the app discards stale prices, checks in with the broker and waits
  for a fresh scan before trading again. Open positions keep being managed.
- Safety: a stop the broker refuses is retried every 2 minutes. While a position has no stop there is a
  highly visible banner, and live mode blocks new trades.
- New: broker rejections are explained in plain English (not enough settled cash, market closed, and so on),
  with the broker's own words alongside.
- New: attention banners (no stop, broker unreachable, running on battery, "keep awake" off while holding).
  There is an always-visible paper / REAL MONEY strip.
- New: a one-click "save diagnostics" file, from which API keys are always left out.
- New: the setup's last step offers start at login and keep-awake during market hours.
- Fixed: "name 'base_events' is not defined" errors on Windows (a start-up race between background tasks).
- The detailed decision journal is kept for 30 days.

### Build 13 · 2026-11-06
- New: the app compares every opportunity before spending money. It reads the market mood (trending up, choppy,
  risk-off, volatile) and ranks candidates on strength, reward after costs, fit and liquidity. It explains in
  plain English why it took, passed on or skipped each one.
- New: stops that are too tight for the trading costs are widened, while the dollar risk stays capped.
- New: the "ai picks" decision mode (the AI chooses one of the top three, or none).
- New: every decision is journaled, and shadow trades compare the rules, the ranker and the AI side by side.
  Performance reports whether the AI helps.
- New: a stricter strategy lab, with train, validation and test periods, walk-forward checks, a double-slippage
  stress test, risk-adjusted results on $100 and per-market-mood results.
- Fixed (Windows uninstall): it stops the app in the tray safely, waits for it to fully exit, removes the
  launch-at-login entry, and asks whether to keep or delete your data and saved keys (keep is the default).
  Open real-money positions block it.

### Build 9 · 2026-11-06
- New: a separate "fresh test" copy with its own data and keys, and a Start menu shortcut for it.
- New: settings → start over, which erases this copy's settings, history and keys and reopens the setup. It is
  refused in live mode and needs a typed confirmation.
- New: the uninstaller offers to delete settings, history, logs and test copies.

### Build 8 · 2026-11-06 · first published build
- The desktop app: it scans about 400 liquid US stocks and ETFs, runs five rules-based intraday strategies, and
  asks an AI to review trades (a free local model or Claude). A fixed set of safety rules has the final say:
  $20 max per trade, 2 positions at once, $2 daily loss limit, $1 risk per trade, and no margin, leverage,
  shorting or options.
- Demo mode with simulated prices, paper trading on Alpaca, and a separate live mode that is off by default.
- Pages: overview, live scanner, AI activity, trades, performance and settings, with a tray icon and
  notifications.
- A guided first-run setup that installs the free local AI with one click, or connects a Claude key, and walks
  through connecting Alpaca paper keys.
- A per-user Windows installer (no admin needed) and a Mac disk image, built and tested automatically, with
  direct download links in the README.
- Sharp icons on high-resolution displays, and gentler order polling that stays inside Alpaca's limits.

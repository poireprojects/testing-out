# Build 15 review: fixes, tests and what's still limited

Build 15 is commit `086e143`. The fixes are in commits `2b03bd4`, `ee61a4a`, `1d934ac`, `8348ae5`, `571cb41` and `a5a4f98` on `claude/ai-day-trading-app-i8p65e`. The tested installer is **build 20**:
https://github.com/poireprojects/testing-out/releases/tag/build-20 (its Windows setup file)
(sha256 `87ec4539dba1dfc6b670117c8566f743d82d904c021c3a04680951c33914da90`).

Live trading was not turned on and no money was spent. Every broker call went to the Alpaca **paper** account. The AI ran on a free local model, and nothing was purchased.

| # | item | result |
|---|---|---|
| 1 | stop counted as active before the broker confirmed it | fixed; 16 new tests |
| 2 | installer read a failed safety check as success and force-closed the app | fixed; 8 policy tests and 5 new Windows CI scenarios |
| 3 | settlement rule for Alpaca accounts under $2,000 | was too strict, now relaxed; still never borrows |
| 4 | real paper-account integration tests | 8 tests, all passed on your paper account |
| 5 | strategies on real history and AI modes compared with rules only | done. No strategy makes money after costs, and the AI added nothing |
| 6 | full test suite and Windows rebuild | 254 passed, 8 skipped locally. Build 20 CI passed and the release is published |
| + | found while testing: sizing used the whole $100,000 paper account | fixed before any order was placed |

`git diff --shortstat 086e143..a5a4f98`: 43 files changed, 7,227 insertions and 177 deletions. Code and tests account for 2,462 of the added lines. The rest is the results data and pictures in this folder.

---

## 1. Protective stops (`stockpilot/engine/trading_engine.py`, `stockpilot/execution/order_manager.py`)

**Problem in build 15.** `_ensure_protection` returned as soon as *any* "active" stop row existed:

```python
if self.orders.active(trade_id=t["id"], purpose="stop"):
    return
```

That included `pending_submit`, a stop the app sent that the broker never answered. A timeout or lost response therefore counted as protection. Also, `sync()` wrote an order off as "never arrived" even when the broker simply couldn't be asked.

**Change.**

* `order_manager.py:33-44` defines the only states that count as protection. The order must have a broker id and be in one of these states:
  ```python
  WORKING_STATES = frozenset({"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding", "pending_replace"})
  IN_FLIGHT_STATES = frozenset({"pending_submit", "pending_cancel"})
  def is_working(row): return bool(row.get("broker_order_id")) and row.get("status") in WORKING_STATES
  ```
  `pending_submit`, `pending_cancel`, `rejected`, `canceled`, `expired`, `abandoned` and any status the app doesn't recognise do not count.
* `trading_engine.py:348` `stop_coverage()` counts the shares covered by confirmed working stops. They must cover the **whole** remaining position.
* `trading_engine.py:577` `_ensure_protection()` was rewritten:
  * If the stop has been raised, a lower stop still held at the broker is canceled first. The new stop is placed only after the broker confirms the cancel.
  * If a stop is in flight, the app never sends a second sell order, which could sell more shares than are held. Until the broker answers, the app watches the stop price itself and the position counts as unprotected.
  * When the broker rejects a stop, the app records the refusal, retries every 120 s and shows a warning.
  * When the broker doesn't confirm a stop, the app warns "couldn't confirm the stop-loss order". The stop is never assumed to exist.
* `order_manager.py:146-163` `sync()`: "couldn't ask" (`BrokerUnavailable`) now propagates. An order is written off only when the broker answers that it doesn't have it, after 90 s.
* `trading_engine.py:355` `_is_unprotected()` and `unprotected()`: positions that are open or closing count as unprotected until fully covered by confirmed stops.
* **New live entries are blocked.** `risk/engine.py:145` refuses real-money trades while any position "has no broker-confirmed stop order". `risk/engine.py:149` pauses real-money entries for the rest of the day after the broker refuses stops on two positions, and one refusal blocks that symbol.
* **Unprotected positions are handled.** In live mode, `trading_engine.py:652` `_guard_unprotected()` sells a position that has had no confirmed stop for `risk.unprotected_live_exit_minutes`, which defaults to 10 and can be set from 1 to 60. Paper positions are watched but not sold.
* **Recovery.** `trading_engine.py:666` `_sweep_stray_orders()` deals with orders the app lost track of. If a stop the app sent turns up live at the broker for a position it still holds, the app adopts it. Any other leftover order of its own is canceled, so a stray sell can't open a short. Orders the app didn't place are never touched. Each database has a random tag inside every `client_order_id` (`sp{mode}{tag}-…`, `order_manager.py:66`), so two copies of the app on one account never touch each other's orders.

**Tests** (`tests/test_protection.py`, 16 tests, using hooks in `stockpilot/broker/sim_broker.py` that simulate submit timeouts, lost responses and failed lookups):
`only_acknowledged_live_states_count`, `healthy_stop_is_confirmed_protection`, `stop_that_never_reached_the_broker_is_not_protection` (timeout), `lost_response_stop_is_adopted_not_duplicated`, `lost_response_is_resolved_on_the_spot_when_lookup_works`, `stop_canceled_at_the_broker_is_replaced`, `raised_stop_replaces_an_older_lower_broker_stop`, `app_watched_stop_sells_safely_when_broker_stop_is_unconfirmed`, `restart_resolves_unconfirmed_stop` (recovery), `stray_sell_order_with_nothing_behind_it_is_canceled`, `orders_not_placed_by_the_app_are_never_touched`, `live_position_without_confirmed_stop_is_sold_after_the_limit`, `paper_position_without_stop_is_watched_not_sold`, `live_entries_pause_after_broker_refuses_stops`, `unconfirmed_stop_blocks_live_entries_via_risk_context`, `another_copys_orders_on_the_same_account_are_never_touched`.

## 2. Installer safety checks (`packaging/windows/installer.iss`)

**Problem in build 15.**

```pascal
function RunApp(const ExePath, Params: String): Integer;
  if not FileExists(ExePath) then begin Result := RC_OK; exit; end;   // missing program = "all clear"
  if Exec(...) then Result := rc else Result := RC_OK;                 // couldn't start = "all clear"
...
PrepareToInstall:   else if rc = RC_RUNNING then ForceClose();         // force-closed with no check, any other code ignored
InitializeUninstall: only RC_LIVE blocked; every other failure continued
```

**Change.**

* `RunApp` (line 80) now returns `True` only if the program actually ran. A missing or unstartable program gives `False` and `rc = RC_NOT_RUN`. It is never read as success.
* `PrepareToInstall` (line 205, upgrade) and `InitializeUninstall` (line 236) block on `RC_LIVE` and on **every** unexpected exit code. When the program couldn't run, they fall back to the installer's own checks, `NothingRunning` and `FallbackCheck` (line 140). These block if a copy is running, if that couldn't be determined, or if any real-money database exists, including test copies.
* `CurUninstallStepChanged` (line 268) runs before any file is removed. Every failure goes through `RefuseUninstall`, which shows a message and calls `Abort`, and the uninstall ends with nothing removed. Before files go, it checks again that nothing is running.
* `MayForceClose` (line 172) allows a force-close only if a **fresh** `--check-live` returns "none open", the installer is not running silently, and the person answers Yes. The default button is No. `ForceClose` then checks that the app actually closed. The ifs are nested so that nothing relies on Pascal short-circuit evaluation.
* `stockpilot/maintenance.py`: the live check covers the main database and every test copy, and an unreadable database counts as open. `STOCKPILOT_TEST_MAINTENANCE_FAIL` and `STOCKPILOT_TEST_IGNORE_QUIT` (in `stockpilot/ui/app.py`) let CI simulate a crashed check and a hung app.
* Also fixed: in build 16 a continuation line starting with `#13#10` was parsed as a preprocessor directive and the installer didn't compile.

**Tests.**

* `tests/test_installer_policy.py` (8): `restart_manager_is_off`, `runapp_never_reports_success_when_the_check_could_not_run`, `every_unexpected_exit_code_blocks`, `program_that_cannot_run_falls_back_to_independent_checks`, `force_close_only_after_fresh_live_check_and_consent_never_silent`, `uninstall_verifies_nothing_is_running_before_files_go`, `live_check_covers_test_copies_too`, `no_code_line_starts_with_a_preprocessor_hash`. There are also new `tests/test_maintenance.py` cases for the failure paths.
* `packaging/windows/test_uninstall.ps1` runs against the real installer on a Windows CI machine. New scenarios:
  * `upgrade-over-running`
  * `[check-fails]`: uninstall refused, nothing deleted, same process still running
  * `[upgrade-blocked]`: setup exits non-zero and the installed copy is untouched
  * `[hung]`: an app that ignores quit is **not** force-closed
  * `[exe-missing-live]`: uninstall refused when positions can't be verified
  * `final`: run with the program renamed

  All passed in build 20. The log shows `force-close refused: silent mode never force-closes a trading process` → `uninstall refused`.

## 3. Alpaca limited margin under $2,000 and settlement (`stockpilot/risk/sizing.py`, `stockpilot/config.py`)

**Checked against Alpaca's current documentation.**

* Every Alpaca account is a margin account. Under $2,000 of equity it is **limited margin**: multiplier 1, so buying power equals cash. It cannot borrow or short.
* A limited-margin account **may trade with unsettled sale proceeds**, because Alpaca covers the T+1 float.
* Alpaca's documentation describes FINRA's intraday margin rule replacing the old pattern-day-trader rule: no limit on the number of trades and no $25,000 minimum.
* Fractional shares support market, limit, stop and stop-limit DAY orders.

**Problem in build 15.** The only setting was "settled cash only". After the app sold a position, a $100 account couldn't use the proceeds until the next day, which blocked legitimate same-day trades that Alpaca allows.

**Change.** `risk.settlement_mode` has a new default, `"auto"`.

```python
no_margin = min(inp.broker_buying_power, inp.broker_cash)   # never margin, even on a 2x/4x account
avail = min(no_margin, inp.settled_cash) if mode == "conservative" else no_margin
return max(0.0, avail - inp.pending_buy_notional)            # open buy orders already count
```

"Settled only" is still available in Settings. Settings files from v1 that had the old default are moved to "auto" (`config.py:171`). Spending never goes above the cash in the account or the broker's buying power, so there is no leverage.

**Found while testing (step 4):** on the $100,000 paper account the app set its limits from the whole account: $20,000 per trade, a $2,000 daily loss limit and $1,000 of risk per trade. The run was stopped before any order. `capital_base()` (`sizing.py:48`) now uses the app's own capital, which is the starting capital plus its realized profit or loss, capped by account equity.

**Tests.** `test_risk_and_sizing.py` covers auto mode, the migration, a $100,000 account at the sizing and engine level, and compounding that grows only from the app's own results. `test_separation_config_calendar.py` covers reusing proceeds under limited margin compared with conservative mode.

## 4. Real Alpaca paper integration tests (`tests/test_alpaca_integration.py`)

These run only when paper keys are set. Several guards apply: the paper endpoint must be used, the account number must start with `PA`, and a key that looks like a live key (`AK…`) is refused. Live credentials were never used.

The tests and what they check:

* `paper_account_is_used_and_sizing_never_borrows`
* `calendar_assets_and_positions`
* `unknown_order_is_confirmed_not_found`
* `market_data_snapshot_and_bars`
* `fractional_limit_order_submit_lookup_duplicate_cancel`: a duplicate `client_order_id` is refused
* `rejections_are_mapped_and_explained`
* `fractional_buy_broker_stop_and_safe_exit`: a fractional buy, then a broker DAY stop, which is confirmed working, then raised and confirmed again, then a safe exit
* `engine_order_paths_against_the_real_paper_broker`: the engine's own order, protection and exit code paths

**All 8 passed on your paper account during market hours.** The account was left with no positions and no orders. Tests that need fills skip while the market is closed.

## 5. Real-history results (details: `REPORT.md`, pictures in `figures/` and `live-paper/`)

The data is 40 trading days from 2026-09-11 to 2026-11-05: 40 symbols of Alpaca IEX 1-minute bars, 1,600 of 1,600 symbol-days with nothing missing. Trades are $20 positions. Costs are a 3 bps spread plus 5 bps slippage each way plus SEC/FINRA fees, about **0.23% per round trip**.

| strategy | trades | before costs | after costs | verdict |
|---|---|---|---|---|
| intraday momentum | 285 | −0.000% | −0.230% | no edge |
| trend following | 705 | −0.042% | −0.271% | no edge |
| opening-range breakout | 302 | −0.052% | −0.282% | no edge |
| VWAP reclaim | 1,237 | −0.015% | −0.245% | no edge |
| volume-confirmed breakout | 466 | +0.008% | −0.222% | no edge |

The unseen test period with a $100 account and 2 slots:

* first come, first served: −$10.73 over 190 trades
* the app's ranker: −$2.57 over 26 trades
* SPY buy and hold: −3.48%
* doing nothing: $0

**AI modes compared with rules only.** The model was Llama 3.2 3B running locally for $0. Prompts were anonymised: no tickers, no dates, and prices rescaled.

| mode | trades | avg/trade | total |
|---|---|---|---|
| rules + ranker | 72 | −0.230% | −$3.31 |
| AI confirms each pick | 72 | −0.230% | −$3.31 |
| AI picks one of the top 3 (39 days) | 69 | −0.185% | −$2.55 |
| rules + ranker, same 39 days | 71 | −0.213% | −$3.02 |

The AI approved 72 of 72 picks at about 0.95 confidence. It chose the ranker's own top pick 69 of 69 times and never chose "none". The small difference in "AI picks" comes from trades it didn't take, not from judgment.

**Live paper session with the AI** (`LIVE_SESSION.md`, 5 screenshots): the AI approved XLE, XLB and PFE. It also said buy on AAPL, but its answer took 98 s and the price had moved 0.24%, so the app skipped that trade. The broker confirmed the stops. Afterwards the account was clean.

## 6. Full test suite and Windows rebuild

* **Local:** `python -m pytest`: **254 passed, 8 skipped**. The 8 skipped are the paper-account tests, which skip without keys and were run separately above.
* **CI build 20** (`a5a4f98`): all of these passed:
  * `test` (the full suite on Ubuntu)
  * `windows`: PyInstaller build, the packaged app's self-test and the Inno installer
  * `windows-uninstall-test`: install, launch, upgrade over a running copy, every blocked scenario above, uninstall keeping data, reinstall, clean uninstall and fresh start

  The `latest` job published **release build-20 with the Windows installer at 16:51 UTC**.

---

## Remaining limitations

* **None of the strategies makes money after costs.** On $20 trades the ≈0.23% cost is larger than any edge they had before costs. Do not switch to real money on these results. The app's own evaluation gate reaches the same "no edge" verdict.
* **The AI result is for one small local model (3B) on 40 days.** It rubber-stamped every pick. A larger model, such as Claude through the API, wasn't tested because that costs money. The run was stopped early at your request, so "AI picks" covers 39 of 40 days. The extra random sample, which would test whether the AI's yes/no separates good setups from bad ones, was skipped. With 0 rejections the run says nothing about that.
* **Data is IEX only**, a few percent of U.S. volume, and the period is short. Backtest fills assume the next bar's open plus costs.
* **Broker stops on fractional shares are DAY orders** and expire at the close. The app closes everything 10 minutes before the close, but if it is off at that point, a leftover position has no broker stop overnight.
* **The app-watched stop only works while the app runs.** This is why a live position that stays unprotected is sold after 10 minutes.
* **The uninstall dialogs were tested silently on CI.** A person hasn't clicked through the Yes/No prompts on a real PC.
* **macOS:** the Mac runner has been stuck in GitHub's queue, so build 20 has no .dmg yet. It will be attached automatically if that job finishes.
* **Security:** your paper secret key was pasted into the chat. It was never written to the repository. Regenerate your paper keys in the Alpaca dashboard.

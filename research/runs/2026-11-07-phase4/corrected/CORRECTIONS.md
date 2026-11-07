# Phase 4 integrity corrections (closeout, 2026-11-07)

**The decision is unchanged: NO-GO.** The corrected rule still loses money on both folds.

**These are not new evidence.** Every corrected number below comes from the same, already-examined data
(2022-12 → 2026-10). The corrections fix how the frozen rule was measured; they are not a second test.

**The originals are preserved.** `../results.json`, `../trades.json`, `../REPORT.md` and `scripts/phase4_test.py`
are unchanged. The corrected outputs are separate: `results_corrected.json` and `trades_corrected.json`, produced
by `scripts/phase4_corrected.py`. Regression tests are in `tests/test_phase4_corrections.py` (11 tests).

## The three corrections

| | defect in the original | correction | effect |
|---|---|---|---|
| **C1** stop timing | Entry is at 10:00:30, but the stop was checked on the 15-minute bar starting at 10:00:00, so a price printed in the 30 seconds *before* entry could stop the trade | Within 10:00–10:15 the stop is checked on 1-minute bars from 10:01. A sensitivity run also counts the 10:00 minute but fills at the stop, never at the pre-entry open | **None in this data.** 37 H1 stops (16 development, 21 evaluation) fell in that bar; 1-minute bars confirmed all 37 hit the stop *after* entry. Stop counts are unchanged in every set, and the sensitivity run is identical |
| **C2** fees | Each $20 trade was charged SEC, TAF and CAT fees rounded up to a cent, as if it were the only trade that day (~0.10–0.11% per trade) | Each fee type is summed over the day's trades within the same set, rounded up to a cent once per day, and shared in proportion to each trade's unrounded amount (~0.03% per trade) | **All of the change**: about +0.07 to +0.10 percentage points per trade. The capital scenarios already aggregated fees per day and are unchanged |
| **C3** ML acceptance | `mlKept` was **true** because the model selected 2 of 2,965 events and both won (bootstrap p = 0). The pre-registration's own decision rule calls fewer than 100 trades insufficient evidence | The ML test now needs at least 100 selected trades before its result can count; otherwise it enters the Holm correction with p = 1 | With the original labels: not evaluable, not kept. Retrained on the corrected labels (same model and features), it selects 102 trades that lose −0.27% per trade (p = 0.86): **not kept** |

## Corrected numbers

| H1, mean net per trade | development | evaluation |
|---|---:|---:|
| original | −0.30% | −0.27% |
| C1 only | −0.30% | −0.27% |
| **C1 + C2 (corrected)** | **−0.23%** (90%: −0.39 … −0.06) | **−0.19%** (90%: −0.34 … −0.02), p(>0) = 0.97 |
| corrected, 2× spreads and slippage | −0.44% | −0.49% |
| corrected, every earnings event (B2) | −0.25% | −0.20% |
| price-only comparable, paired excess (B3) | −0.18% (p = 0.90) | −0.006% (p = 0.49), unchanged |

Pre-registered criteria passed: **1 of 8**, the trade count, as before.

## Numbers that must not be read as evidence

* **Random-day comparison (B4), evaluation: +0.18%, p = 0.047** (originally +0.16%, p = 0.07). It moved because
  random-day trades are spread over more days (about 2 per day, against 3.8 for the earnings trades), so each
  pays a larger share of the cent-rounded daily fee. That's an accounting asymmetry, not information. B4 was not a pass
  criterion, and the same rule is no better than the price-only comparable (B3).
* **"EPS beat, sales miss", evaluation: +0.01%** (development −0.17%). This subset was never a hypothesis, was
  noticed after the results were seen, and is about zero. Testing it now would be data mining.
* **The original ML "pass"** (2 trades, p = 0) was a defect of the acceptance rule, not a finding.

## Data

`data-manifest.json` describes the only new data: 1-minute SIP bars for 10:00–10:14 on 137 symbol-days, fetched
read-only and only where the 10:00 bar reached the stop. Every day is inside the ledger's already-examined
period; the vault (2020-07-27 → 2022-10-31) was not opened.

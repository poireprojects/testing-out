# Phase 5 historical intraday research: closed, inconclusive (2026-11-11)

**Opus made zero decisions.** No model was called, nothing was paid, and no event return or strategy P&L was
computed. The files in this folder are kept as they are, as an archive.

**Why it stopped** (zero-cost feasibility audit of `1b3a926`, 2026-11-10):
* **Executable fills (gate A): fail.**
  * The phase-4 cache holds, per window, only the median mid, median half-spread, count and median ask size of the
    NBBO updates in the 60 seconds after 10:00:30 and 15:45:00 (`scripts/phase4_collect.py`).
  * There is no timestamped bid or ask: 0 of 160 events for entry, timed exits, stops or targets.
  * Every fill would be a proxy, and the entry conditions would see up to 60 seconds of later data.
* **The $12.50 ceiling (gate B): fail.**
  * No demonstrated bound exists on the billable input tokens of the exact requests: the injected structured-output
    prompt is undocumented, the tokenizer is private, and token counts are estimates.
  * No provider spending cap with a documented non-overshoot guarantee applies.
* **160 vs 157:** three filings were accepted after 10:00 ET (SRE 2023-02-28, C 2024-01-12, C 2025-04-15). The
  current budget proof requires 160 rendered requests, so it can never hold. A proposed fix was described but not
  applied.

**Rules from here:**
* The paid path stays blocked: do not write `dev-eval/APPROVAL.json`.
* Under `ADVANCEMENT-SCREEN.md` §3, an implementation failure or missing data is an inconclusive experiment and a
  spending stop. It is not evidence about the model, and it does not justify another implementation round on these
  dates.

The next experiment is a different question on new data: the forward, paper-only investing pilot
(`research/runs/2026-11-11-invest-pilot/REGISTRATION.md`, proposed).

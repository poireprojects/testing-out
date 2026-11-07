# Phase 4 data feasibility audit (2026-11-07)

**Question:** can historical company disclosures be reconstructed accurately enough to test an intraday
strategy honestly? Could this app actually have acted on them, at realistic prices and timing?

**Sample:**
* 50 Form 8-K filings, accepted 2025-09-02 → 2026-10-30. These are development days in
  `research/ledger.json`; the vault (2020-07-27 → 2022-10-31) was not touched.
* Drawn from 120 companies picked at random from the app's universe (seed 20261107).
* Stratified into 25 earnings 8-Ks (Item 2.02) and 25 other 8-Ks, one per company per stratum. The
  population held 566 earnings 8-Ks and 1,089 others.

Script: `scripts/phase4_audit.py`. Per-event data: `audit_events.json`. Spreads by time of day:
`audit_spreads_by_time.json`.

## Sources and terms

| source | what it gives | access | cost |
|---|---|---|---|
| SEC EDGAR (`data.sec.gov` submissions, `www.sec.gov` filing headers and documents) | Filing acceptance time to the second, 8-K item numbers, the press-release exhibit | Free. **Requires a declared contact (name + email) in the User-Agent** and at most 10 requests per second. `www.sec.gov` refused an undeclared client. The audit declared the owner's contact (owner-approved), used 4 requests per second and made 206 requests | $0 |
| Alpaca news (Benzinga), REST, `created_at` | Time-stamped headlines, including the vendor's earnings flash ("X Q1 EPS $0.75 Beats $0.72 Estimate, Sales … Beat …") | Free Basic plan. Historical worked. Real-time news over websocket is documented but **not tested live** | $0 |
| Alpaca historical consolidated (SIP) minute bars and NBBO quotes | Consolidated prices, volume and bid/ask | Free Basic plan, history since 2016, **except the latest 15 minutes** | $0 historical. **$99/month** (Algo Trader Plus) for real-time SIP |
| Alpaca IEX (the app's real-time feed) | One exchange's trades and quotes | Free | $0 |

**Credentials.** At the owner's explicit instruction, the previously exposed paper key was used for
**read-only historical market data and news only**. No trading endpoint was called.

## Results

| # | question | answer |
|---|---|---|
| 1 | Reliable timestamps? | **SEC acceptance time: 50 of 50 exact.** The filing header (Eastern) and data.sec.gov (UTC) agree to the second. **First public release: verifiable for 24 of 50.** That is 23 of 25 earnings filings (the vendor's earnings flash), but **only 1 of 25 other 8-Ks** (no headline that is clearly that disclosure; excluded, never estimated). Press-release exhibits never state their own release time: the one time found was a conference-call time |
| 1b | Is the SEC time the first release? | **Often not.** The vendor headline came before acceptance for 18 of 24 verified events: median 1.3 minutes before, more than 5 minutes for 6, and 3.3 hours for one (Expeditors: release 08:31, 8-K accepted 11:48). A strategy keyed to EDGAR acceptance would treat old news as new |
| 2 | Usable historical market data? | Consolidated minute bars at the actionable minute for **50 of 50**. NBBO quotes for 24 of 24 verified events at 10:00 and 15:50, and for 23 of 24 at 9:31 (NBBO available, unlike IEX) |
| 3 | Could the app act during regular hours? | **0 of 24 verified releases fell in regular hours**: 14 pre-market, 10 after the close. Only 3 of 50 filings were accepted in regular hours, none with a verifiable release time |
| 4 | Delay from disclosure to the first tradable minute? | Median **191 minutes** (25th percentile 150, 75th percentile 1,039; minimum 15). The median absolute price change **before** the first tradable minute was **3.1%**, larger than the next hour's 2.2% |
| 5 | Spreads and costs? | Median NBBO spread: **45 bps at 9:31** (75th percentile 76), **15 bps at 10:00** (75th percentile 21), **6 bps at 15:50**. Entering at the open would cost about 23 bps for half the spread alone. Entering at 10:00 and exiting at 15:50 costs about 10 bps in half-spreads, before slippage and fees |
| 6 | Can the information available at entry be reconstructed? | **Yes for earnings:** the flash text (EPS and sales against estimate) at its timestamp, plus consolidated prices and volume up to the decision. **No** for most other 8-Ks |
| 7 | Legal and affordable to monitor in production? | EDGAR: yes (free, declared contact, at most 10/s). News: likely yes on the free plan (historical verified, real-time not tested). Seeing the real NBBO in real time costs $99/month |
| 8 | Is the app's real-time IEX feed adequate? | **No.** IEX traded **4.1%** of consolidated volume in the first 30 minutes. Its displayed spread was a median **588 bps at 9:31** (no quote at all for 4 of 24), 45 bps at 10:00 and 124 bps at 15:50, against NBBO 45 / 15 / 6. IEX trade prices at the actionable minute differed from the consolidated tape by a median 9.6 bps (90th percentile 36 bps). Orders are routed to the whole market, but the app would size and price them from a quote that is not the market |

**Ticker mapping:** EDGAR and the app use current tickers. 8 of 50 companies changed their *name* inside
the window. No ticker gap broke a lookup in this sample, but EDGAR offers no point-in-time ticker history.

**Audit correction (documented):** v1 matched headlines with loose keywords. It picked up previews ("Insights
Ahead: …") and market wrap-ups as "releases", including two dated a day before the filing. v2 requires the
vendor's own earnings-flash form, naming the company. v1's output is kept in
`audit_events_v1_loose_matching.json`.

## Verdict

* **Reacting to disclosures during regular hours: NOT FEASIBLE.**
  * None of the verifiable releases came during trading hours.
  * Non-earnings 8-Ks can't be tied to a first-release time with free data.
  * The app's live quote (IEX) does not show the market price or spread.
* **Scheduled earnings released before the open or after the close: FEASIBLE to test honestly.**
  * Release time, acceptance time and the information known at decision are reconstructable.
  * Consolidated bars and NBBO quotes give realistic entry and exit costs.
  * The realistic entry is **after** the open, once spreads have narrowed. The first-minute spread (45 bps)
    exceeds most plausible edges.
  * Live execution on IEX alone would be degraded (see question 8).

This makes one narrow hypothesis testable. Its registration is in
`research/PREREGISTRATION-2026-11-07-phase4.md`.

# INVEST-PILOT-1: data-integrity gate and readiness (2026-11-12)

**Verdict: B. IMPLEMENTATION COMPLETE; REAL-DATA VALIDATION BLOCKED.**
* All four corrections pass every listed acceptance test: corporate-action coverage, the latest eligible N-PORT
  report, share counts, and the reporting basis behind the Dominion mismatch.
* Every real-source category that exists on this machine passes.
* The real files the remaining checks need are absent ("Missing data"), and none was downloaded.

This verdict does not authorize launch or spending. Launch is **not approved**, and **paid dispatch is BLOCKED**.
The single gate list is in `LAUNCH-MANIFEST.md` §0.

**Audited revision:** `0f19afbf52745fb60f9b381bb59e04726594353f`. HEAD equalled it, the worktree was clean and the
remote branch had not moved, so the findings were reassessed on exactly that code. This report is committed last,
and the final commit SHA is given in the hand-off message.

**Spend and access:** $0. None of the following happened:
* a model call, paid call or broker call;
* an SEC or market-data download;
* a credential read or use of an email contact;
* an approval file, or a start of the forward clock.

Unauthenticated documentation reading was attempted (ecfr.gov, federalregister.gov, docs.alpaca.markets). The
session's network policy refused all three hosts. Nothing was retried, and the environment was not changed.

The tests ran with external networking blocked in two ways:
* sockets are refused in every investing test (`tests/invest_helpers.py`);
* the full suite ran inside a network namespace with no route (`unshare -rn`).

## Reproduced on the audited revision

Each reported defect was reproduced first, through the audited code's own functions:

| finding | reproduction at `0f19afb` | result |
|---|---|---|
| 1. corporate actions | `market.normalize([], None, ["VOO"])`; `pipeline.build_data` passes `None` when no actions file exists | succeeds with empty `splits`, `dividends` and `unsupported`: a missing collection reads as "no actions" |
| 2. N-PORT | `select_report` with an older and a newer pre-freeze NPORT-P in the index, only the older parsed | returns the older accession; the newer one is never resolved |
| 3. share counts | `shares_outstanding` with two 500-share classes; with a total of 1,000 plus classes of 800 and 200 | **500** with no warning; **2,000** |

Found while doing this work, and corrected in the same pass (evidence below):

| # | finding | evidence |
|---|---|---|
| 4 | N-PORT writes `<lei>N/A</lei>` for issuers without an LEI, and the universe rule took `N/A` as one issuer's LEI | in the real iShares Russell 1000 report, 273 holdings have `N/A`. The audited rule groups the 1,011 common-stock rows into 733 issuers instead of 996 |
| 5 | company facts include DEF 14A pay-versus-performance figures rounded to millions, and the latest one replaced the 10-K figure | 269 such facts over the 40 real companies. Chevron's FY2025 net income is 12,299M in its 10-K and 12,300M in its proxy |
| 6 | company facts carry no dimensions, so a company that reports its share classes separately has no company-facts share count | Reddit, Roblox, Rivian and Pinterest in the real cache have none. The audited code would have labelled such candidates "unavailable" (excluded). The real top 12 includes Alphabet, Meta and Berkshire |
| 7 | the earlier report blamed the Dominion mismatch on the four-quarter method | the registered method actually used (fiscal year + YTD − prior YTD) mixes reporting bases. See "Dominion" |
| 8 | a change in reporting precision (3,727.4M later reported as 3,727M) counted as a restatement | of the 323 "restatements" seen on real files, 47 were proxy figures (5) and 51 precision changes; 225 remain |
| 9 | after the valuation row gained its unresolved wording, the budget's maximum-size request was no longer a maximum | the budget now renders the longest row the code can write: 84,129 bytes, was 81,796 (`LAUNCH-MANIFEST.md` §3) |

## Evidence: the corrections

Every test named below is in `tests/test_invest_integrity.py` unless stated. The 43 tests in that file all fail on
`0f19afb` and all pass here (JUnit: 43 tests, 43 failures at the audited revision; 43 passed at the final one).
At the audited revision:
* 35 fail on interfaces the old code does not have (21 `AttributeError`, 14 `TypeError`);
* 8 fail on behaviour:
  * the `N/A` LEI test keeps 1 issuer instead of 12;
  * `normalize` returns no coverage record;
  * the Dominion and restatement tests find no basis status;
  * the proxy-fact test finds no exclusion;
  * two pack tests stop with `InputsIncomplete`: an unresolved share count or trailing value was a collection
    failure there.

The reproduction table above shows the three reported defects through the old functions themselves.

### Corporate-action coverage (`adapters/market.py`, `data.py`, `engine.py`, `diagnostics.py`)

| pass criterion | test | result |
|---|---|---|
| missing file fails safely | `test_no_response_covers_nothing_and_an_empty_local_list_is_never_verified_absence`; `test_a_raw_store_without_any_action_response_builds_but_blocks_every_use` (13 symbols uncovered; VOO gap on 2031-01-03; slot 1 `missing_input`) | pass |
| incomplete paginated response fails safely | `test_an_incomplete_or_failed_response_covers_nothing` (7 cases: unfinished pagination, missing first page, a loop, an orphan page, a type filter, an error body, no `next_page_token`) | pass |
| uncovered symbol, uncovered date range | `test_an_uncovered_symbol_and_an_uncovered_date_range_fail_safely` (VOO covered to 2031-03-26 → `gap|voo|2031-03-27|corporate_actions`; STK1 uncovered → month 1 `missing_input`; the report is incomplete) | pass |
| fully collected, explicitly empty response succeeds | `test_an_explicitly_empty_fully_collected_response_is_verified_absence` (covered to the query end; a response retrieved mid-range covers only to the day before retrieval) | pass |
| split and dividend reconcile by hand | `test_a_split_and_a_dividend_reconcile_to_hand_calculated_shares_cash_and_wealth`: 0.999400299 VOO → $1.69 entitlement → 1.68/100.05 = 0.016791604 more → 2:1 split to 2.032383806 at 50.00 = **101.6191903**; cash 0.00 | pass |
| resolution after a restart, no duplicate application | `test_a_coverage_gap_resolves_after_a_restart_without_applying_any_action_twice`: gaps kept, `gap_resolved` appended, the ledger only appended, decisions byte-identical; actions are exactly one entitlement, one payment, one split; the same 101.6191903; a further rerun adds nothing | pass |
| shadow-only failure leaves the primary record complete | `test_a_shadow_only_gap_never_invalidates_the_primary_record_and_outcomes_resolve_from_later_data` (SYNTH12 covered to the last cutoff: shadow gap 2031-12-01; the primary report stays complete) | pass |
| dates per purpose; outcome accounting may use later data | the same test (December-horizon outcome: `outcome_gap`, then recorded from later data) and `test_decision_inputs_use_only_actions_dated_by_their_cutoff_and_need_coverage_only_until_then` (a merger after a cutoff does not touch that pack; coverage to a cutoff serves that pack and blocks the next) | pass |

### The latest eligible N-PORT report (`adapters/nport.py`, `universe.py`, `pipeline.py`)

| pass criterion | test | result |
|---|---|---|
| missing newer report → blocked | `test_a_newer_pre_freeze_filing_that_was_not_collected_blocks_selection` | pass |
| verified newer report of another series → target selected | `test_a_verified_newer_report_of_another_series_is_excluded_with_its_evidence` (evidence from the XML or the filing header; a header naming the target with no XML blocks) | pass |
| newer eligible target report → selected; post-freeze → excluded | `test_the_newer_eligible_report_is_selected_and_a_post_freeze_one_is_excluded` | pass |
| unresolved series, incomplete holdings, another registrant → blocked | `test_unresolved_series_incomplete_holdings_or_another_registrant_block_selection` (3 cases) | pass |
| duplicate issuer mappings → blocked | `test_duplicate_issuer_mappings_and_a_short_review_table_block_the_freeze` (symbol, CIK; 11 rows) | pass |
| amended filings | `test_amendments_resolve_by_reporting_period_under_the_documented_interpretation` | pass |
| ties | `test_ties_between_versions_or_duplicate_originals_block_and_equal_values_rank_by_cusip` | pass |
| insufficient holdings, missing or repeated CUSIP, `N/A` LEI | `test_issuers_without_an_lei_are_not_merged_and_the_universe_needs_twelve_distinct_issuers` | pass |
| publication not proven by acceptance; stale index | `test_acceptance_alone_does_not_prove_publication_before_the_freeze` | pass |
| the fixture run | `test_nport_selection_uses_the_latest_report_public_before_the_freeze` (`test_invest_adapters.py`): the newer pre-freeze report of the trust's other series is excluded by series ID | pass |

Each selection records, for every NPORT-P and NPORT-P/A in the index:
* its accession, form and acceptance time;
* its series ID and where that ID came from;
* its reporting period and the publication test (`public_by`);
* its raw SHA-256, status and reason.

The record is in `review_table.json`, under `reconciliation`. The no-guessing mapping review is unchanged.

**Applicable rule (as understood; the text could not be re-read here).**
* Form N-PORT General Instruction F, with rule 30b1-9, has the Commission make a report public up to 60 days after
  the end of its period. Only the third month of each fiscal quarter is public under the rule in force; under the
  2024 amendments, each monthly report becomes public 60 days after its month ends.
* So acceptance is not treated as publication. A report is public before the freeze only when its acceptance and
  its period end + 60 days (00:00 ET the next day) are both before the freeze.
* A filing accepted inside that window is "uncertain" and blocks the selection.

**Amendments: documented interpretation of "the most recent N-PORT-P report".** The latest reporting period wins, a
public same-period NPORT-P/A supersedes the original, and an amendment of an older period never makes that period
the latest. The universe policy is unchanged: `FROZEN["universe"]` and `config_sha256` are identical.

### Share counts (`adapters/edgar.py`, `pipeline.py`, `pack.py`, `data.py`)

| pass criterion | test | result |
|---|---|---|
| one class; repeated identical facts; equal-sized distinct classes; unequal classes; aggregate plus components; missing dimensions | `test_share_counts_resolve_by_context_or_say_unresolved`, 9 cases: <ul><li>one class: 1,000 (1 class)</li><li>repeated identical facts, including one reported at a different scale: 1,000</li><li>two classes of 500: 1,000 (2 classes; the audited code gave 500)</li><li>800 + 200: 1,000 (2 classes)</li><li>a total of 1,000 with its classes of 800 and 200: 1,000 (the audited code gave 2,000)</li><li>a total that its components contradict: unresolved</li><li>two undimensioned counts: unresolved</li><li>another legal entity's count: excluded</li><li>an unknown dimension: unresolved</li></ul> | pass |
| company facts only a cross-check | `test_company_facts_must_agree_with_the_cover_page_and_never_stand_in_for_it`; `test_eps_is_labelled_an_approximation_and_company_facts_alone_never_resolve_a_share_count` (`test_invest_adapters.py`) | pass |
| no ambiguous count reaches the rendered prompt | `test_no_ambiguous_count_reaches_the_rendered_request_as_an_established_fact` (the candidate stays eligible; market value and price/sales are not computed; several classes: market value not computed because only one class's price is collected; an absent, not unresolved, fact is still a collection failure) | pass |
| pipeline end to end | `test_a_conflicting_cover_page_in_the_raw_store_becomes_an_explicit_unresolved_input`; `test_raw_fixtures_become_a_verified_data_directory_with_provenance` (`test_invest_pipeline.py`: SYNTH01's two classes come from its cover page, with no company-facts row, as in real files) | pass |

**Multiple classes.** Company market value would need every class's price, and only the universe member's class is
collected. So with several classes the row states the summed count and says that market value and price/sales are
not computed. No class's price is ever multiplied by all classes.

### Reporting basis of trailing values (`adapters/edgar.py`)

| criterion | test | result |
|---|---|---|
| a mixed basis is unresolved, with its components (real Dominion data) | `test_the_dominion_trailing_net_income_mixes_reporting_bases_and_is_unresolved` (REAL excerpt, `tests/fixtures/invest_real/dominion/`) | pass |
| consistent restatements resolve; precision changes are not restatements; real revisions are | `test_consistent_restatements_resolve_and_rounding_is_not_a_restatement` | pass |
| the valuation row names an unresolved value and omits its ratio | `test_an_unresolved_trailing_value_is_named_and_its_ratios_are_not_computed` | pass |
| proxy figures never enter | `test_proxy_statement_figures_never_enter_trailing_values` | pass |

## Dominion trailing net income

Source: real SEC company facts and submissions for Dominion Energy (CIK 715957), cached by the phase-4 collector:
* company facts: cache file `58aded0d34c61b120bd2e793`, original SHA-256 `09d685b4…ec`;
* submissions: cache file `b3acaad62f64e61ad6ea2071`, original SHA-256 `d6e95607…a6`;
* excerpt: `tests/fixtures/invest_real/dominion/`.

| as of (10-Q acceptance) | method used | components (value, accession) | value | basis |
|---|---|---|---|---|
| 2025-08-01 10:36 ET | FY + YTD − prior YTD | FY2024 2,124M (10-K 0000950170-25-028387) + H1 2025 1,425M − H1 2024 966M (both 10-Q 0000950170-25-101177) | 2,583M | **mixed** |
| 2025-10-31 11:49 ET | same | FY2024 2,124M (0000950170-25-028387) + 9M 2025 2,431M − 9M 2024 1,900M (both 10-Q 0001193125-25-260246) | 2,655M | **mixed** |

* **The 10-K's own basis gives different prior-year figures.** It reports 2024's quarters at 441, 580, 958 and
  145M, so H1 2024 on its basis is 1,021M, not 966M, and 9M 2024 is 1,979M, not 1,900M.
* **The 2025 10-Qs recast 2024.** The FY2025 10-K (0001193125-26-063120, accepted 2026-02-23) later reports FY2024
  as 2,034M, with quarters 403, 563, 934 and 134M.
* **So the production calculation mixes reporting bases.** It combines FY2024 as first reported with recast
  comparatives.
* **The four-quarter cross-check is mixed too.** Its 2,509M and 2,557M use Q1 2025 at 646M as first reported, while
  H1 2025 implies 665M.
* **A consistent figure was not public then.** On the recast basis the values would be 2,034 + 1,425 − 966 =
  2,493M and 2,034 + 2,431 − 1,900 = 2,565M. They need the recast FY2024, which was not public at either time.
* **Both values are now `unresolved`.** They keep their components for review and never reach a pack as figures.
  Diluted EPS shows the same pattern at both points, and revenue at 2023-11-08 is not established.
* **The rest of the 40 companies.** 30 of 1,896 trailing values (632 points × 3 concepts) are unresolved:
  * revenue 13, net income 5, diluted EPS 12;
  * real recasts: EPS restated for stock splits (Amazon, DexCom, KLA), LyondellBasell's discontinued operations,
    Dominion and Kenvue;
  * small real revisions: Republic Services, Gallagher, Illumina;
  * AST SpaceMobile: the comparative is not from the current filing.

## Evidence: real-source categories

No file was downloaded. Every file's URL is proven before use: by its cache key (`sha256(url)[:24]`), or by the
feasibility fetcher's `index.jsonl` (name `sha256(url)[:32]`, bytes matching the recorded SHA-256). The values
compared are SEC public data. Committed output: `real-source-check.json`
(`python -m autotrader.invest real-check …`).

| category | files | fields checked | expected | result |
|---|---|---|---|---|
| SEC company facts and submissions | 40 companies (cache keys), 632 as-of points | <ul><li>public time of every fact vs its period end</li><li>exclusions</li><li>restatements</li><li>trailing-value status and components</li><li>two independent methods where both apply</li></ul> | <ul><li>0 early facts</li><li>methods agree unless a basis is mixed</li></ul> | <ul><li>0 early facts</li><li>269 proxy facts excluded</li><li>225 restatements</li><li>159 of 161 cross-method checks agree; the 2 are Dominion's mixed basis, now unresolved</li><li>30 unresolved values listed with components</li><li>88 unavailable (no method applies)</li></ul> |
| SEC filing headers | 43 headers | accession, issuer CIK, acceptance time vs the index | all reconcile | 43 reconciled, 0 disagree |
| real N-PORT-P XML | iShares Russell 1000 ETF (iShares Trust, CIK 1100663):<ul><li>accession 0001004726-26-000805</li><li>`primary_doc.xml` SHA-256 `82633fb5…f2d9a6`</li><li>index page `29f5a36b…ade41`</li></ul> | `parse_nport` vs an independent regex parse of the raw XML: <ul><li>holdings 1,015</li><li>value total $46,423,559,689.21</li><li>categories EC 1,011, STIV 2, DE 2</li><li>`N/A` LEIs 273 and `N/A` CUSIPs 2</li><li>ticker identifiers 2</li><li>series ID S000004347</li><li>registrant CIK 0001100663</li><li>period 2025-12-31</li><li>submission type</li><li>index page (series, period, CIK)</li><li>issuer count</li><li>top-12 CUSIPs</li></ul> | equal | **15 of 15 agree, 0 discrepancies.** <ul><li>996 issuers, which the feasibility study's separate program also counted; 733 under the audited rule</li><li>top 12: NVIDIA … Visa, the same under both rules for this report</li><li>0 of the top 12 carry a ticker identifier, so every mapping row is manual</li><li>accepted 2026-02-23 14:19:39 ET; certainly public by 2026-03-02 under the 60-day rule</li></ul> |
| real 8-K documents (passages) | 98 documents: 92 from the feasibility store, 6 from the SEC caches (URL from the index's primary document) | <ul><li>every passage vs an independent `html.parser` text extraction (verbatim, ignoring whitespace)</li><li>at least 100 characters</li><li>carries its rule's figure or adverse keyword</li></ul> | all pass | 377 passages, 0 problems |
| real 8-K inline XBRL covers (contexts) | 8 covers: Landstar ×2, Realty Income, Public Storage, Travelers, Molina, Delta, Roku | <ul><li>`inline_facts` trading symbols with class-axis members vs a separate regex reading of the contexts</li><li>entity CIK vs the URL's CIK</li></ul> | equal | 8 of 8 agree, incl. Realty Income's 15 and Public Storage's 20 class-axis members. 0 share-count facts: 8-K covers carry none |
| share-count context on real 10-K/10-Q covers | none saved | | | **pending** (Missing data 3) |
| MD&A and risk-factor sections on real 10-K/10-Q | none saved | | | **pending** (Missing data 3) |
| the registered fund's N-PORT-P and its fund index (multi-series reconciliation) | none saved | | | **pending** (Missing data 1–2) |
| vendor bars | only pandas pickles written by earlier project scripts: 15-minute SIP bars (phase 4) and daily IEX bars (phase 2). They hold no response body and no adjustment parameter, and were not unpickled (a pickle runs code) | | | **pending** (Missing data 4) |
| vendor corporate-action responses | none saved | | | **pending** (Missing data 5) |

## Missing data (no downloads in this task)

| # | source and file type | smallest representative sample | cutoff | cost, credential, contact | why it is necessary |
|---|---|---|---|---|---|
| 1 | SEC EDGAR submissions JSON (`data.sec.gov/submissions/CIK##########.json`) of the registrant of Vanguard 500 Index Fund (Vanguard Index Funds; confirm the CIK on EDGAR), with every older page it lists | 1–3 JSON files | retrieved after the freeze | $0, no credential; the SEC's fair-access policy needs a declared User-Agent with a contact, so the **owner's scoped SEC authorization** (pending) | the selection reconciles every NPORT-P and NPORT-P/A of the trust and blocks without it; only a fixture index has been reconciled |
| 2 | the latest pre-freeze NPORT-P `primary_doc.xml` of Vanguard 500 Index Fund, plus the `-index-headers.html` of each other NPORT-P or NPORT-P/A in that index whose period is not before it (the trust's other series) | 1 XML and roughly 10–20 headers | retrieved after the freeze | as 1 | series identity on the target trust: only the iShares format was checked |
| 3 | inline XBRL 10-K and 10-Q primary documents, each with its `-index-headers.html`, for three issuers: one with one class of common stock; one reporting several classes on the class axis (Alphabet or Meta); one combined filing with co-registrants (Dominion Energy with Virginia Electric and Power) | 6 documents and 6 headers | any recent filings; for the pilot, the newest periodic filing by each cutoff | as 1 | <ul><li>share-count contexts, `LegalEntityAxis` exclusion and the company-facts cross-check on real cover pages: never seen, since 8-K covers carry no counts</li><li>MD&A and risk-factor section detection on real 10-K/10-Q text</li></ul> |
| 4 | the approved vendor's unadjusted daily bars (`adjustment=raw`), each response saved verbatim with its request URL: VOO, two stocks with a split in the window, one with a dividend; one month around each ex-date | 3–4 responses | historical, any dates | the owner-approved vendor; a **newly issued** key (never the exposed PKJB7U key); licence terms on committing prices | the normalizer's raw format and the split price-step check have only seen fixtures |
| 5 | the same vendor's corporate-action responses for those symbols and dates: untyped, every page saved verbatim with its URL (page token); one query with a small `limit` so that it paginates; one symbol and range with no actions | 3–5 pages | retrieved after the window | as 4 | coverage is derived from the response shape (nesting, `next_page_token`, field names). That shape is modelled, not verified, because the vendor's documentation was unreachable |
| 6 | documentation only (no data): <ul><li>Form N-PORT General Instruction F, rule 30b1-9 and the 2024 amendments' compliance dates (ecfr.gov, federalregister.gov or sec.gov)</li><li>the vendor's corporate-actions API reference</li></ul> | the pages | current | $0, no credential; the session's network policy refused these hosts (an environment setting) | the publication window and the response shape are coded from understanding, not from re-read text |

## Exact commands

```
python -m autotrader.invest dryrun OUT_DIR       # raw fixtures -> pipeline -> data -> fixture decisions -> ledger -> report -> request
python -m autotrader.invest budget OUT_DIR       # maximum-size request (longest valuation rows), token estimates, BLOCKED
python -m autotrader.invest real-check OUT.json SEC_CACHE_DIR... --raw-index FEASIBILITY_RAW_DIR
python -m pytest -o addopts="" -q tests/test_invest_pilot.py tests/test_invest_contract.py tests/test_invest_adapters.py \
    tests/test_invest_pipeline.py tests/test_invest_diagnostics.py tests/test_invest_integrity.py --junitxml=JUNIT.xml
unshare -rn env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false \
    python -m pytest -o addopts="" -q -p no:cacheprovider --junitxml=JUNIT.xml           # full suite, no network
```

`unshare -rn` gives the process a network namespace with no route at all. The environment override turns off
commit signing for the git repositories the release-gate tests create: this machine signs commits through a local
service that the namespace also cuts off.

The real check used four phase-4/phase-5 SEC cache directories and the public-data feasibility study's `raw/` store,
all saved by earlier sessions on this machine.

## Hashes

| item | SHA-256 |
|---|---|
| proposed configuration (`config_sha256`), **unchanged** | `d2e857b796bca155afe773abdf714432c58f564905ff5abefb349f1ad535e080` |
| raw fixture manifest (`dry-run/raw-MANIFEST.json`) | `20614cf6b040d8fd3e26cf83ab2397295cc60382acfb6d1e868b73303943bf86` |
| data manifest built by the pipeline (`dry-run/data-MANIFEST.json`) | `4f10b51462f2fcda9898ca78418d37b527958afbda46efc8e8b76e5852cc4ec5` |
| demonstration ledger (`dry-run/ledger.jsonl`, 956 records) | `bc1fdf648db610a062ed98a6347d9577da817f1d1de3816cf5d5d052c83619da` |
| `dry-run/report.json` | `77cad28f90a2d3c3938c97939b209b0044b24129242eecf816f936d25b93d1b6` |
| `dry-run/REPORT.md` (unchanged) | `5bfb155f2148ff547ab8517fdbf8fc83e1b5bed7650415b08e499516b10c6150` |
| `dry-run/summary.json` | `04983e7ce9c47f807a70f9ef4a82b22b8558aa056478079f4b3ad9d9cd43583c` |
| rendered request, slot 1 (29,953 bytes; not sent) | `6c093232c86193b7adae2cba5787412f10a1904e6f4419b2f2575b69b49f85e0` |
| maximum-size request (84,129 bytes; not sent) | `96793584d6fc68f5d32b2bdaa3d0af4a25a2195eabf5653a73dab7b236bec613` |
| `real-source-check.json` | `1206b7fb979df15d39c42af849d7445281a04dc55c28ea215be500f8a92c5d32` |

The code hash of every identity file and each pack's hash are in `dry-run/summary.json`. The code hash changed,
because the corrections are code.

## Demonstration (fixtures; NOT A RESULT)

* Verdict **NOT A RESULT**, and the record is complete. Decisions: 12 fixture and 0 genuine; 9 valid, 1 invalid,
  2 missed. Forecasts: 84 scored, 24 pending, 0 missing; Brier 0.2503, which means nothing.
* Wealth matches the audited run to the cent:
  * VOO 84.62;
  * AI 85.40 before model costs and 83.12 all in;
  * shadow 89.39, with its VOO weight at 70.01% at launch and 64.57% at its lowest, and 131 rebalancing trades
    skipped under the $1 minimum.
  * The shadow's drift disclosure is kept, and its rebalance rules are unchanged.
* The corrections show in the evidence:
  * the newer pre-freeze N-PORT of the trust's other series is excluded by its ID;
  * the corporate actions come from two pages of one query and are covered to 2032-01-04;
  * SYNTH01's two share classes give "market value not computed" in its rows.
* **Replay:**
  * Two fresh runs into empty directories were byte-identical across all 796 files: raw store, data, 12 packs,
    ledger, reports, request and manifests.
  * The committed `dry-run/` matches a fresh run (`test_replay_is_deterministic_resume_appends_nothing_and_matches_the_committed_run`).
  * `--resume` appends 0 records.

## Tests

* **Focused (investing pilot): 220 passed, 0 failed, 0 errors, 0 skipped** (JUnit, 19.5 s; sockets refused):
  * `test_invest_pilot.py` 42, `test_invest_contract.py` 81, `test_invest_adapters.py` 30,
    `test_invest_pipeline.py` 13 and `test_invest_diagnostics.py` 11: the 177 the director ran;
  * `test_invest_integrity.py` 43 (new).
* **New tests at the audited revision:** 43 tests, 43 failed (worktree at `0f19afb` with only the new test file and
  its fixture added).
* **Full suite: 826 tests, 818 passed, 0 failed, 0 errors, 8 skipped** (JUnit, 302 s). All 8 skips are
  `test_alpaca_integration.py`, which needs Alpaca paper keys.
  * It ran with external networking blocked (`unshare -rn`; the command is under "Exact commands") on code
    identical to commit `02ad0ae`.
  * Only documents changed during the run (`README.md`, `REGISTRATION.md`, this file), and no test reads them.
  * An earlier run inside the namespace without the signing override failed two release-gate tests on
    `git commit`. They pass outside it (33 of 33) and with the override; the cause is the namespace, not the code.
* Updated expectations in existing tests:
  * the share-count, N-PORT and action-format tests now use the corrected interfaces;
  * the real Starbucks excerpt no longer yields a share count from company facts alone;
  * test data that is rewritten carries its coverage record;
  * no assertion was loosened to pass.

No test implies investment skill. Fixture decisions are scripted placeholders, and every record and report labels
them.

## Changed paths

* code: `autotrader/invest/` — `adapters/edgar.py`, `adapters/market.py`, `adapters/nport.py`, `universe.py`,
  `pipeline.py`, `data.py`, `pack.py`, `engine.py`, `diagnostics.py`, `report.py`, `budget.py`, `real_check.py`,
  `synthetic.py`, `synthetic_raw.py`, `__main__.py`;
* tests: `tests/test_invest_integrity.py` (new), `tests/fixtures/invest_real/dominion/` (new, REAL excerpt),
  `tests/invest_helpers.py`, `tests/test_invest_adapters.py`, `tests/test_invest_pipeline.py`,
  `tests/test_invest_pilot.py`;
* records: `dry-run/` (regenerated), `real-source-check.json` (regenerated), this file, `LAUNCH-MANIFEST.md`,
  `AMENDMENT-1.md` (A6), `REGISTRATION.md` (the SEC-contact authorization marked proposed and not on record; its
  old checklist points to the manifest's gate);
* `README.md` (the gate in the test list).

Unchanged:
* the desktop app (`stockpilot/`);
* Phase 5 code and records;
* the model, universe rule, portfolio rules, scoring and the $12.50 ceiling (`config_sha256` identical);
* the paid path, which still refuses.

## Open items for the reviewer

* **`FROZEN["pipeline"]` summaries were left unchanged**, so that the configuration hash proves nothing frozen
  moved. They now describe the code less fully than AMENDMENT-1 A6; update them at the freeze.
* **The N-PORT publication window and the corporate-action page shape are implemented from understanding.** The
  rule text and the vendor documentation could not be read here (Missing data 6). The publication test only
  widens the "uncertain" window; it never treats a filing as public before its acceptance.
* **An unresolved share count or trailing value keeps the candidate.** Its row says so, but the record is not
  marked incomplete. Whether it should be is a policy choice.
* **Likely three of the real top 12 report several share classes** (Alphabet, Meta, Berkshire). Their rows will
  carry no market value or price/sales.
* **The reporting-basis check is conservative.** It also leaves small real revisions unresolved, for example
  Republic Services' Q1 2022 revenue (3,466.3M → 3,466.1M).
* **Still unchanged from before:**
  * paid dispatch is BLOCKED (`LAUNCH-MANIFEST.md` §3);
  * the live collectors, the paid transport, the external timestamp and the host do not exist;
  * section starts need a following paragraph of at least 200 characters.

## Recommended next action

The owner should give one written, dated and scoped authorization for a one-time, $0 collection of exactly the
sample in "Missing data" 1–5:
* the SEC contact, for the EDGAR items;
* an approved vendor with a newly issued key and its terms, for the bar and action items.

Then rerun `real-check` and the integrity tests on those files, before any independent launch review. Everything
else on the gate (`LAUNCH-MANIFEST.md` §0) waits on that evidence or on the owner, and the paid path stays
refusing.

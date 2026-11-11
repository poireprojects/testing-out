# INVEST-PILOT-1: launch manifest and runbook (2026-11-11)

**Launch status: NOT APPROVED. Paid dispatch: BLOCKED.** This is the single list of what a genuine launch needs. It
authorizes nothing. No paid client, live collector, broker connection or forward clock exists in this version, and
`python -m autotrader.invest forward` refuses.

## 1. What would be sent

| | |
|---|---|
| model | `claude-opus-5-5` (Claude Opus 5.5) |
| endpoint | `POST https://api.anthropic.com/v1/messages`, one request per month, 12 at most, no retries |
| request fields | `model`, `max_tokens` 16000, `system` (`contract.INSTRUCTIONS`), `messages` (one user message: the frozen pack), `output_config: {effort: "medium", format: {type: "json_schema", schema: contract.json_schema(universe)}}`, `inference_geo: "global"` |
| not sent | `thinking` (adaptive thinking is always on for this model), `service_tier` (removed by amendment 1), tools, fallbacks, `speed`, betas, sampling parameters |
| rendered offline | `python -m autotrader.invest request STATE_DIR SLOT` prints the exact body and its SHA-256; nothing is sent |
| registration | `REGISTRATION.md` (revision 2) + `AMENDMENT-1.md`, both proposed; `config_sha256` `d2e857b796bca155afe773abdf714432c58f564905ff5abefb349f1ad535e080` |

## 2. Checked against the provider's documentation (unauthenticated reading, 2026-11-11)

| item | finding | source |
|---|---|---|
| model and price | `claude-opus-5-5` is listed at $4 per million input tokens and $20 per million output tokens; `inference_geo: "global"` is standard pricing (`"us"` adds 1.1x) | platform.claude.com/docs/en/about-claude/pricing |
| thinking and `max_tokens` | Opus 5.5 always thinks (a `disabled` setting returns 400); thinking tokens bill as output and count toward `max_tokens`. So `max_tokens` 16,000 bounds output, thinking included | Claude API reference for Opus 5.5 (bundled with Claude Code, cached 2026-11-03) |
| effort | `output_config.effort`; this model's default is `medium`, and the request sets it explicitly | same |
| structured outputs | supported on `claude-opus-5-5`. The schema uses only supported features: string `enum`, `integer`, `additionalProperties: false` and every field required, with no min/max constraints. An injected system prompt "costs you tokens like any other system prompt". The first use of a schema compiles a grammar (extra latency). A `refusal` or `max_tokens` stop may not match the schema | platform.claude.com/docs/en/build-with-claude/structured-outputs |
| `service_tier` | service tiers are legacy. `standard_only` matters only with a Priority Tier commitment, and the feature's model list excludes Opus 5.5. **Removed** from the request (amendment 1, A4) | platform.claude.com/docs/en/api/service-tiers |
| refusals | `stop_reason: "refusal"` returns HTTP 200 and the generated tokens are billed. The reference recommends server-side fallbacks by default. The registration forbids them (one model), so a refusal is a missed month and its charge counts | Claude API reference; structured-outputs page |
| spend limits | an organization can set a monthly spend limit below its tier cap, and workspaces can have their own (not the default workspace). Once usage reaches a limit, requests return 400. **The page says nothing about a request admitted just below the limit that pushes spend past it, or how quickly the limit takes effect** | platform.claude.com/docs/en/api/rate-limits |
| token counting | free, with a separate rate limit; supports `claude-opus-5-5`; counts the same structured input. **"The token count is an estimate ... might differ by a small amount"**, and the difference is not quantified | platform.claude.com/docs/en/build-with-claude/token-counting |
| rate limits | Opus 5.5 at the Start tier allows 4,000 requests a minute: irrelevant at one request a month | rate-limits page |

## 3. Budget (`python -m autotrader.invest budget OUT_DIR`)

* **Measured:** a request with every registered cap filled (12 candidates x 4 passages x 1,200 characters, 12
  valuation rows, three holdings with theses) is **81,796 bytes**, SHA-256
  `f2fd1ffd960f560bf5f57e1cdc390d4fb1ac3e5595b241cc6d913dca86c0d919`. A maximum-length answer under the caps is
  24,050 bytes.
* **Estimated (not bounds):**
  * input of 20,449–32,718 tokens, at 2.5–4 bytes per token (the injected structured-output prompt is not
    included);
  * an answer at every cap of 6,012–9,620 tokens, which leaves about 6,380 tokens for thinking at the high
    estimate. An answer at every cap plus long thinking could hit `max_tokens`: a missed month that is still
    charged.
* **Documented bound:** output is at most 16,000 tokens, or **$0.32** per request.
* **Estimated cost:** **$0.40–0.45** per request at the caps; **$4.82–5.41** for 12 requests.
* **Plan for a later paid client:**
  * at most 12 requests, with no retries; a failed or lost request counts and is never resent;
  * before each request, reserve its high estimate in the durable journal, and keep the reservation until the
    provider's charge is reconciled;
  * reservations plus reconciled charges must stay within $12.50.

**Paid dispatch stays BLOCKED.** The exact missing guarantee is either of these:

* a provider-documented upper bound on the billable input tokens of this exact request, including the injected
  structured-output prompt;
* a provider-enforced spend limit documented to stop spend at the limit, even for a request admitted just below it,
  set at or below the remaining ceiling.

The documentation offers neither. Token counting is an estimate with an unquantified error, and spend limits are
documented only as blocking requests after usage reaches them.

*For the owner's decision, not a guarantee:* $12.50 over 12 requests is $1.04 each. After the $0.32 output bound,
that leaves $0.72, or about 180,000 input tokens per request: about **5.5 times** the high estimate, or more than
2 tokens per byte of the 81,796-byte request. Pairing a
free token count before each request with a workspace spend limit at the remaining ceiling would make overspend
very unlikely. Accepting that as "enforcement" is a policy choice only the owner can make. Code does not make it.

## 4. Launch dependencies

| dependency | status | what is needed |
|---|---|---|
| independent review | pending | review of this code, `REGISTRATION.md`, `AMENDMENT-1.md` and `READINESS.md` |
| scoped SEC authorization | recorded | the owner's contact may be used for this pilot's EDGAR collection until revoked or until the scope changes (registration). A live EDGAR transport that sends it, at the SEC's fair-access rate, is **not written** (`adapters.raw.RefusingTransport` is the only live transport) |
| market-data entitlement | not chosen | an owner-approved source of unadjusted daily opens and closes, cash dividends (ex and pay dates), splits, symbol changes and other actions, with terms that say whether prices may be committed. A **newly issued** key; the exposed key starting PKJB7U is never used |
| fee verification | not verified for 2027 onward | SEC Section 31 and FINRA TAF rates for the pilot dates, from primary sources |
| mapping review | not done | a person checks `review_table.json` (CUSIP → symbol and CIK) against the N-PORT report date and writes the review file. The fixture review is scripted and labelled so |
| host | not chosen | a machine that runs from each cutoff close (16:00 ET) to the next open, keeps the model key locally, and pushes each freeze |
| external timestamps | not chosen | proof from outside this machine that each pack and decision existed before its fill open: for example, a pushed commit with the host's receipt time, or an RFC 3161 timestamp of the ledger hash |
| paid transport | not written | journal before dispatch, one request per slot, **never resend an ambiguous request** (in flight at a crash = `lost_in_flight`, charged, reconciled by hand), usage capture, reconciliation, and the ceiling rule above. It stays BLOCKED until the missing guarantee is resolved |
| model spend | not given | an account, a key kept on the host only, and the owner's explicit approval of an amount within the $12.50 ceiling |
| final approval and freeze | not given | the owner approves the registration and amendment in writing, dated; sets the first fill month and `FROZEN["status"]`; the new `config_sha256` is recorded |

## 5. Runbook

**Now (offline, $0, no credentials):**

```
python -m autotrader.invest dryrun OUT_DIR             # fixtures end to end; compare with dry-run/summary.json
python -m autotrader.invest budget OUT_DIR             # maximum-size request and budget
python -m autotrader.invest real-check OUT.json CACHE_DIR...   # parsers on already-cached real SEC files
python -m pytest -q -o addopts="" tests/test_invest_*.py
```

**Before launch (each step needs the dependency named above):**
1. Collect the raw store: EDGAR submissions, headers, documents, company facts, `company_tickers.json`, the fund's
   N-PORT-P files, prices and actions. Every file goes to `RAW_DIR` with its URL, retrieval time and SHA-256.
2. `python -m autotrader.invest build-data RAW_DIR DATA_DIR --freeze ISO --series NAME`, then have the mapping
   reviewed. Rebuild with the signed review. The build stops on any unapproved or unresolved row.
3. `python -m autotrader.invest trial-pack DATA_DIR OUT --first-fill-month YYYY-MM --slot 1`. This inspects the
   inputs only. It is not a backtest or a decision, and it is not sent.
4. Owner approval and freeze. After that, nothing in `FROZEN` or the code may change for the run.

**Each month of a genuine run (not implemented: needs the paid transport):**
1. After the cutoff close: collect the month's new raw files; rebuild the data; build and freeze the pack.
2. Render the request, write its journal entry, send it once, and store the answer as received.
3. Decide from the stored answer (valid, invalid, missed); record the charge from the provider's usage.
4. Push the freeze, for an external timestamp, before the fill open.
5. At the fill open, fill or keep the holdings. Mark daily; report.

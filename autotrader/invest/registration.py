"""The pilot's frozen parameters and its monthly schedule (proposed registration INVEST-PILOT-1).

Every value here is part of the experiment identity (`config_sha256`): changing any of them is a new registration,
never an edit of a running one. Prose and reasons: research/runs/2026-11-11-invest-pilot/REGISTRATION.md.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from stockpilot.clock import ET, MarketCalendar, SessionDay

FROZEN: dict = {
    "experiment": "invest-pilot-1",
    "registration": "research/runs/2026-11-11-invest-pilot/REGISTRATION.md",
    "status": "proposed; not approved; the forward clock has not started",
    "revision": "2 (2026-11-11): input coverage, risk-required reductions, crash recovery, statuses; proposed only",
    "capital_usd": "100.00",                     # each portfolio, once; no further deposits
    "benchmark": "VOO",
    "books": ["voo", "ai"],
    "months": 12,
    "limits": {"voo_min_pct": "70", "voo_max_pct": "100", "max_stocks": 3, "stock_max_pct": "10",
               "long_only": True, "fractional_shares": True},
    "schedule": {
        "cutoff": "the close of the last NYSE session of the month before each fill month (16:00 ET, or the early close)",
        "freeze_deadline": "the open of the next NYSE session (the fill session); a decision frozen at or after it is late",
        "fill": "the open of the first NYSE session of the fill month",
        "launch": "both portfolios are funded with $100 cash at the first fill session; month 1's decision is applied "
                  "to cash there, and a missing month-1 decision means 100% VOO (the benchmark's holding)",
        "end": "the close of the last session of the twelfth fill month",
    },
    "execution": {
        "reference_price": "the session's official opening price from the approved price source",
        "slippage_bps": {"VOO": "5", "stock": "10"},     # buy at open x (1 + s), sell at open x (1 - s)
        "fees": "the app's verified schedule, stockpilot.risk.fees.rates_on(session): SEC Section 31 and FINRA TAF on "
                "sales, CAT on every share; each fee type summed per portfolio per session and rounded up to a cent "
                "once (Alpaca's rule); the rates used are written into every fill record",
        "commission_usd": "0",
        "share_decimals": 9,
        "buy": "a cash amount in whole cents; shares = amount / fill price, truncated to 9 decimals",
        "sell": "a share quantity; proceeds = shares x fill price, truncated to a cent",
        "min_order_usd": "1.00",                  # discretionary trades below this are skipped (full sales and
                                                  # risk-required reductions never are)
        "cap_tolerance_pp": "0.05",
        "risk_required": "a reduce of a stock more than cap_tolerance_pp above the cap at the fill session's opening "
                         "prices sells to its target whatever the amount; a hold never trades, and a missed, late or "
                         "rejected decision never trades, even when drift has taken a stock above the cap",
        "weights": "cutoff weights (what the AI saw) decide whether a hold is allowed; targets are shares of the "
                   "portfolio's value at the opening prices before trades; resulting weights are measured at the "
                   "opening prices after trades and fees and may exceed a target by the trading costs' share "
                   "(within cap_tolerance_pp); intended, executed and resulting weights are recorded per trade",
        "paper_convention": "quantities are sized from the opening price and filled at that price plus slippage: a "
                            "paper-model convention, not evidence that fractional opening-auction orders would execute",
        "sweep": "at every session open, cash of at least $1.00 (after that session's fees) buys VOO, in both "
                 "portfolios: the launch purchase, dividends and sale residue all go through this one rule",
        "min_sweep_usd": "1.00",
        "order": "corporate actions, then stock sales, then stock purchases, then VOO (sold to fund purchases, or "
                 "bought with the remaining cash), then fees",
        "dividends": "entitlement on the ex-date from the shares held before that session's trades; credited as cash "
                     "before the open of the first session on or after the pay date; declared but unpaid dividends "
                     "count in wealth",
        "splits": "shares multiplied by the ratio before the open of the ex-date session; no cash",
        "targets": "a target weight is a share of the portfolio's value (cash plus positions) at the fill session's "
                   "opening prices",
    },
    "universe": {
        "rule": "the 12 largest common-stock positions by reported value in the most recent N-PORT-P report of "
                "Vanguard 500 Index Fund (VOO is its ETF share class) that is public on EDGAR before the "
                "registration is frozen; one security per issuer (an issuer with two share classes keeps the class "
                "with the larger position); ties broken by CUSIP; frozen for the whole pilot",
        "size": 12,
    },
    "evidence": {
        "filings": "SEC EDGAR 10-K, 10-Q and 8-K (Item 2.02) accepted at or before the cutoff "
                   "(acceptanceDateTime, UTC, converted to ET)",
        "passages": "per candidate, the newest filings first: up to 4 passages, each at most 1,200 characters, cut at a "
                    "word boundary",
        "max_passages_per_candidate": 4,
        "max_passage_chars": 1200,
        "valuation": "computed by code at the cutoff close: price, shares outstanding and trailing-four-quarter revenue, "
                     "net income and diluted EPS from the newest filing's XBRL facts; market value, P/E, price/sales",
        "min_text_chars": 20,
        "coverage": "for every universe member and held stock: the newest 10-K/10-Q accepted by the cutoff in the "
                    "collected EDGAR submission index (retrieved after the cutoff), any later 8-K Item 2.02, their text "
                    "and the periodic report's XBRL facts, and the cutoff close. Status complete, unavailable (a "
                    "reproducible reason: no such filing in the index, or a recorded no-trading exception) or "
                    "collection_failure. Any collection failure: no pack, no model request, the month is "
                    "missing_input and the research record stays inconclusive; marks continue",
    },
    "contract": {
        "version": "2 (proposed, unfrozen; AMENDMENT-1.md)",
        "assessments": "every eligible candidate and held stock: integer rank (1 best, ties allowed), "
                       "expected_excess_pct_3m (percentage points of total return minus VOO over the next three monthly "
                       "holding periods, within [-100, 100]), p_positive_excess (fraction in [0, 1]), note",
        "claims": "typed: fact (cited), calculation (sourced inputs, recomputed), assumption and forecast (labelled, "
                  "no citations), inferred_expectation (model inference; valuation rows only; never analysts/consensus)",
        "kept_positions": "mispricing, valuation_link, contrary_evidence, downside, measurable invalidation, "
                          "weight_rationale, base/upside/downside scenarios",
        "abstention": "prefer_voo_reason required when no stock remains",
        "caps": {"text": 240, "note": 100, "scenario_reasoning": 160, "claim_text": 160, "quote": 200, "claims": 4,
                 "inputs": 4, "citations_per_claim": 2},
    },
    "pipeline": {
        "ttm": "annual; else fiscal year + YTD - prior YTD; else four reported quarters (dates within 7 days); latest "
               "value per period public by the as-of time; diluted EPS labelled an approximation",
        "facts_public_at": "the acceptance time of the reporting accession, from the submissions index; facts of "
                           "unknown accession are excluded",
        "reconciliation": "header accession, issuer CIK and acceptance time must equal the index",
        "passages": "adapters.edgar.passages: two MD&A figure paragraphs, the first adverse-disclosure paragraph by "
                    "keyword priority, the first risk-factor paragraph; verbatim; cut to 1,200 characters",
        "universe": "adapters.nport: latest NPORT-P of the series accepted before the freeze; review table; frozen only "
                    "from a review approving every row; unresolved mappings are never guessed",
        "market": "unadjusted bars only; split ex-date price-step check; symbol changes merged; unsupported actions "
                  "flagged (gap if held, exclusion if a candidate)",
    },
    "diagnostics": {
        "status": "secondary; proposed; frozen before any genuine answer; never changes the primary verdict",
        "horizon": "slot k fill-session open to the close of slot k+2's last session; slots 11-12 pending",
        "total_return": "per share: close x split factor + dividends with ex-date after the fill day, per start share, "
                        "over the open; no costs; dividends not reinvested",
        "ties": "excess exactly 0 counts as y = 0.5; Spearman with average ranks",
        "calibration_bins": "[0,0.2) [0.2,0.4) [0.4,0.6) [0.6,0.8) [0.8,1.0]",
        "shadow": "70% VOO + 2.5% in each of the 12 frozen candidates, rebalanced toward those targets at each fill "
                  "session under the same execution convention; orders under the $1 discretionary minimum are "
                  "skipped, so at $100 it drifts (its VOO weight after each fill is reported)",
    },
    "statuses": {
        "valid": "a genuine answer frozen before the deadline that passed every check (100% VOO included)",
        "invalid_answer": "an answer that broke a rule: rejected whole, holdings kept",
        "missed_answer": "no answer frozen before the deadline (no answer, late, refused, or lost with a request in "
                         "flight): holdings kept",
        "missing_input": "research inputs incomplete: no request was made; holdings kept; the record is inconclusive",
        "cost_unreconciled": "a request whose charge has no provider evidence yet: the record is inconclusive until "
                             "an append-only reconciliation record resolves it (never assumed to be zero)",
    },
    "model": {
        "id": "claude-opus-5-5",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "effort": "medium",
        "max_tokens": 16000,
        "structured_output": "output_config.format json_schema (autotrader.invest.contract.json_schema)",
        "request_fields": "model, max_tokens, system, messages, output_config {effort, format}, inference_geo 'global'; "
                          "no thinking field (adaptive thinking is always on for this model), no service_tier "
                          "(amendment 1: legacy, and this model has no Priority Tier), no tools, no fallbacks",
        "requests_per_month": 1,
        "retries": 0,
        "fallbacks": None,                         # one model: a refusal, cut-off or error is a missed decision
        "tools": None,
        "price_sheet": {"input_usd_per_mtok": "4", "output_usd_per_mtok": "20",
                        "source": "platform.claude.com/docs/en/about-claude/pricing (read 2026-11-10)"},
        "charge": "every request is charged to the ai portfolio from the provider's reported usage, including months "
                  "it buys nothing and failed requests; a charge that cannot be reconciled with the provider's usage "
                  "report leaves the month incomplete",
        "spend": "none approved; this pilot has no budget of its own; the project's $12.50 overall ceiling stays in "
                 "force unless Riley explicitly replaces it; no per-request or per-month bound is enforced yet",
    },
    "outcome": {
        "all_in": "ai wealth minus every model charge grown at VOO's total return from the charge's cutoff to the end",
        "stop": "after 12 complete months, all-in wealth at or below the voo portfolio's wealth: stop this version",
        "continue": "all-in wealth above VOO: independent review of a longer paper test (not evidence of skill)",
        "incomplete": "any unresolved data gap, pending fill, missing_input month, unreconciled charge, missing month "
                      "or unfinished blinded claim review: inconclusive",
        "genuine_required": "a favorable verdict needs at least one valid genuine decision; fixture decisions never count",
        "claim_review": "every claim cited by a valid genuine decision is reviewed by a person blinded to actions, "
                        "weights, prices and outcomes (autotrader/invest/claims.py); findings stay visible and never "
                        "edit a decision or remove its trading outcome",
    },
}


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def config_sha256() -> str:
    return hashlib.sha256(canonical_json(FROZEN)).hexdigest()


_PKG = Path(__file__).resolve().parent
# The code that decides what a ledger contains. A change to any of these files is a new identity: an existing
# ledger refuses to resume under it. (The app's fee schedule is not listed: dated fee rows may be added during a
# pilot; the rates used are written into every fill, and a retroactive change stops the run as a ledger conflict.)
IDENTITY_FILES = sorted([p for p in _PKG.glob("*.py")] + [p for p in _PKG.glob("adapters/*.py")]
                        + [_PKG.parent / n for n in ("ledger.py", "evidence.py", "decision.py")]
                        + [_PKG.parents[1] / "stockpilot" / n for n in ("clock.py", "research/events.py")])


def code_sha256() -> dict:
    root = _PKG.parents[1]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in IDENTITY_FILES}


def dec(x) -> Decimal:
    return Decimal(str(x))


CAPITAL = dec(FROZEN["capital_usd"])
BENCHMARK = FROZEN["benchmark"]
MIN_ORDER = dec(FROZEN["execution"]["min_order_usd"])
MIN_SWEEP = dec(FROZEN["execution"]["min_sweep_usd"])


def slippage_bps(symbol: str) -> Decimal:
    s = FROZEN["execution"]["slippage_bps"]
    return dec(s["VOO"] if symbol == BENCHMARK else s["stock"])


# ------------------------------------------------------------------ schedule
@dataclass(frozen=True)
class Slot:
    index: int                 # 1..12
    month: str                 # the fill month, YYYY-MM
    cutoff: SessionDay         # last session of the previous month: evidence and prices up to its close
    fill: SessionDay           # first session of the fill month: the freeze deadline is its open
    period_end: SessionDay     # last session of the fill month

    @property
    def deadline(self) -> datetime:
        return self.fill.open


def _month_sessions(cal: MarketCalendar, year: int, month: int) -> list[SessionDay]:
    d, out = date(year, month, 1), []
    while d.month == month:
        s = cal.session(d)
        if s is not None:
            out.append(s)
        d += timedelta(days=1)
    return out


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def schedule(first_fill_month: str, months: int = FROZEN["months"], cal: MarketCalendar | None = None) -> list[Slot]:
    cal = cal or MarketCalendar()
    y0, m0 = map(int, first_fill_month.split("-"))
    out = []
    for i in range(months):
        y, m = _add_months(y0, m0, i)
        py, pm = _add_months(y, m, -1)
        prev, cur = _month_sessions(cal, py, pm), _month_sessions(cal, y, m)
        slot = Slot(i + 1, f"{y:04d}-{m:02d}", prev[-1], cur[0], cur[-1])
        if cal.next_session(slot.cutoff.close) != slot.fill:
            raise ValueError(f"{slot.month}: the fill session is not the session after the cutoff")
        out.append(slot)
    return out


def sessions_between(first: date, last: date, cal: MarketCalendar | None = None) -> list[SessionDay]:
    cal = cal or MarketCalendar()
    return [cal.session(d) for d in cal.trading_days_between(first, last)]


def et(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)

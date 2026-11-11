"""Secondary diagnostics (proposed): forecast outcomes over the fixed three-month horizon, scoring (ties, Brier,
calibration, rank association, selected vs rejected), pending and missing forecasts, the shadow portfolio, and the
rule that no diagnostic can change the primary verdict. Hand-calculated fixtures; offline; no network.
Nothing here measures skill: fixture forecasts are placeholders and the data are synthetic."""
import inspect
import json
from datetime import date, timezone
from decimal import Decimal as D

import pytest

from autotrader.invest import diagnostics, report, synthetic
from autotrader.invest.diagnostics import BINS, horizon, score, spearman, total_return
from autotrader.invest.registration import FROZEN
from autotrader.invest.report import build_report, markdown, verdict
from tests.invest_helpers import ALL_VOO, SLOTS, by_key, dry, no_network  # noqa: F401


class Data:
    """A minimal price source: {(symbol, day, field): price}, splits and dividends."""

    def __init__(self, px, splits=(), dividends=()):
        self.px, self.splits, self.dividends = px, list(splits), list(dividends)

    def price(self, sym, day, field):
        v = self.px.get((sym, day.isoformat(), field))
        return None if v is None else D(v)


S, E = date(2031, 1, 2), date(2031, 3, 31)


# ------------------------------------------------------------------ total return
def test_total_return_counts_splits_and_dividends_per_starting_share_by_hand():
    d = Data({("ABC", "2031-01-02", "open"): "100", ("ABC", "2031-03-31", "close"): "52"},
             splits=[{"symbol": "ABC", "ex_date": "2031-02-18", "num": 2, "den": 1}],
             dividends=[{"symbol": "ABC", "ex_date": "2031-02-10", "amount": "1.00"},     # before the split: x1
                        {"symbol": "ABC", "ex_date": "2031-03-27", "amount": "0.50"},     # after it: x2 shares
                        {"symbol": "ABC", "ex_date": "2031-01-02", "amount": "9.99"},     # ex on the fill day: excluded
                        {"symbol": "ABC", "ex_date": "2031-04-01", "amount": "9.99"}])    # after the end: excluded
    r = total_return(d, "ABC", S, E)
    # (52 x 2 + 1.00 x 1 + 0.50 x 2) / 100 - 1 = (104 + 2) / 100 - 1 = 0.06
    assert D(r["total_return"]) == D("0.06") and r["split_factor"] == "2" and D(r["dividends_per_start_share"]) == 2


def test_a_split_on_the_fill_day_is_already_in_the_open_and_a_missing_price_stays_missing():
    d = Data({("ABC", "2031-01-02", "open"): "50", ("ABC", "2031-03-31", "close"): "55"},
             splits=[{"symbol": "ABC", "ex_date": "2031-01-02", "num": 2, "den": 1}])
    assert D(total_return(d, "ABC", S, E)["total_return"]) == D("0.1")
    assert total_return(Data({("ABC", "2031-01-02", "open"): "50"}), "ABC", S, E) is None
    assert total_return(Data({("ABC", "2031-03-31", "close"): "50"}), "ABC", S, E) is None


def test_the_horizon_is_three_monthly_holding_periods_and_the_last_two_months_stay_pending():
    assert horizon(SLOTS, 1) == (SLOTS[0].fill.day, SLOTS[2].period_end.day) == (date(2031, 1, 2), date(2031, 3, 31))
    assert horizon(SLOTS, 10) == (SLOTS[9].fill.day, SLOTS[11].period_end.day)
    assert horizon(SLOTS, 11) is None and horizon(SLOTS, 12) is None


# ------------------------------------------------------------------ scoring
def test_spearman_uses_average_ranks_and_needs_three_varied_forecasts():
    assert spearman([1, 2, 3], [10, 20, 30]) == D(1) and spearman([1, 2, 3], [3, 2, 1]) == D(-1)
    assert spearman([1, 1, 2], [1, 2, 3]) == D("0.8660")      # ranks 1.5, 1.5, 3: 1.5 / sqrt(1.5 x 2)
    assert spearman([1, 2], [1, 2]) is None and spearman([1, 1, 1], [1, 2, 3]) is None


def _dec(slot, assess, kept=(), status="valid"):
    return {"slot": slot, "status": status, "decision": {
        "assessments": [{"symbol": s, "rank": r, "expected_excess_pct_3m": e, "p_positive_excess": p}
                        for s, r, e, p in assess],
        "positions": [{"symbol": s, "action": "buy"} for s in kept]} if status == "valid" else None}


def _tr(slot, sym, x):
    return {(slot, sym): {"total_return": x}}


def test_scores_by_hand_with_a_tie_an_abstention_overlap_pending_and_missing_prices():
    decisions = [
        _dec(1, [("A", 1, 2, 0.7), ("B", 2, 0, 0.5), ("C", 3, -1, 0.2)], kept=["A"]),
        _dec(2, [("A", 1, 1, 1.0)], status="invalid_answer"),                       # not valid: never scored
        _dec(3, [("A", 2, -1, 0.4), ("B", 1, 0.5, 0.6), ("C", 2, -2, 0.1)]),         # an abstention: all rejected
        _dec(4, [("A", 1, 0, 0.5), ("D", 1, 0, 0.5)]),                               # D's price is missing
        _dec(11, [("A", 1, 0, 0.5)]),                                                # matures after the end
    ]
    oc = {**_tr(1, "VOO", "0.02"), **_tr(1, "A", "0.05"), **_tr(1, "B", "0.02"), **_tr(1, "C", "-0.01"),
          **_tr(3, "VOO", "0.01"), **_tr(3, "A", "0.03"), **_tr(3, "B", "0.00"), **_tr(3, "C", "0.01"),
          **_tr(4, "VOO", "0.00"), **_tr(4, "A", "0.01")}
    out = score(decisions, oc, SLOTS, last_day=horizon(SLOTS, 4)[1])
    assert (out["scored"], out["pending"], out["missing"], out["decisions_scored"]) == (7, 1, 1, 3)
    assert out["missing_rows"][0]["symbol"] == "D" and out["pending_rows"][0]["slot"] == 11
    rows = {(r["slot"], r["symbol"]): r for r in out["rows"]}
    # excess in pp: slot 1 A +3 (y 1), B 0 (tie, y 0.5), C -3 (y 0); slot 3 A +2, B -1, C 0 (tie); slot 4 A +1
    assert [(rows[k]["excess_pp"], rows[k]["y"]) for k in sorted(rows)] == [
        (D(3), 1), (D(0), D("0.5")), (D(-3), 0), (D(2), 1), (D(-1), 0), (D(0), D("0.5")), (D(1), 1)]
    # Brier: (.09 + 0 + .04 + .36 + .36 + .16 + .25) / 7 = 1.26 / 7
    assert out["brier"] == (D("1.26") / 7).quantize(D("0.0001")) == D("0.1800") and out["brier_constant_half"] == D("0.25")
    sel = out["selected_vs_rejected"]
    assert (sel["selected_n"], sel["selected_mean_excess_pp"]) == (1, D(3))
    assert (sel["rejected_n"], sel["rejected_mean_excess_pp"]) == (6, (D(0 - 3 + 2 - 1 + 0 + 1) / 6).quantize(D("0.0001")))
    err = out["forecast_error"]       # expected - realized: -1, 0, 2, -3, 1.5, -2, -1
    assert err["mean_pp"] == (D("-3.5") / 7).quantize(D("0.0001")) and err["mean_abs_pp"] == (D("10.5") / 7).quantize(D("0.0001"))
    per = {x["slot"]: x["spearman"] for x in out["rank_association"]["per_decision"]}
    assert per[1] == D(1)                                       # attractiveness -1, -2, -3 vs excess 3, 0, -3
    assert per[3] == D("-0.8660") and per[4] is None    # ranks 1.5, 3, 1.5 vs 3, 1, 2; slot 4: one forecast
    q = lambda x: D(x).quantize(D("0.0001"))  # noqa: E731
    cal = {c["bin"]: (c["n"], c["mean_p"], c["mean_y"]) for c in out["calibration"]}
    assert cal == {"[0, 0.2)": (1, q("0.1"), q("0.5")), "[0.2, 0.4)": (1, q("0.2"), q(0)),
                   "[0.4, 0.6)": (3, q(D("1.4") / 3), q(D("2.5") / 3)), "[0.6, 0.8)": (2, q("0.65"), q("0.5")),
                   "[0.8, 1.0]": (0, None, None)}
    assert any("not independent" in n for n in out["notes"]) and any("cannot change" in n for n in out["notes"])


def test_horizons_that_have_not_closed_are_pending_and_p_equal_to_one_lands_in_the_top_bin():
    d = [_dec(1, [("A", 1, 5, 1.0)], kept=["A"])]
    oc = {**_tr(1, "VOO", "0"), **_tr(1, "A", "0.1")}
    early = score(d, oc, SLOTS, last_day=date(2031, 3, 30))
    assert early["scored"] == 0 and early["pending_rows"][0]["why"] == "horizon ends 2031-03-31"
    assert score(d, oc, SLOTS, last_day=None)["pending"] == 1
    out = score(d, oc, SLOTS, last_day=date(2031, 3, 31))
    assert out["calibration"][-1] == {"bin": "[0.8, 1.0]", "n": 1, "mean_p": D(1), "mean_y": D(1)}
    assert BINS[0] == 0 and BINS[-1] == 1


# ------------------------------------------------------------------ in the dry run
@pytest.fixture(scope="module")
def run_state(tmp_path_factory):
    base = tmp_path_factory.mktemp("diag")
    synthetic.build(base / "data")
    dry(base / "s", base / "data")
    return base


def test_forecast_outcomes_are_recorded_from_data_at_the_horizon_close_and_match_a_hand_formula(run_state):
    k = by_key(run_state / "s")
    prices = json.loads((run_state / "data" / "prices.json").read_text())
    o = k["forecast_outcome|1|SYNTH01"]
    p0, p1 = D(prices["SYNTH01"]["2031-01-02"]["open"]), D(prices["SYNTH01"]["2031-03-31"]["close"])
    assert D(o["payload"]["total_return"]) == p1 * 2 / p0 - 1                  # the 2-for-1 split of 2031-02-18
    v0, v1 = D(prices["VOO"]["2031-01-02"]["open"]), D(prices["VOO"]["2031-03-31"]["close"])
    assert D(k["forecast_outcome|1|VOO"]["payload"]["total_return"]) == (v1 + D("1.70")) / v0 - 1   # ex 2031-03-27
    assert o["recorded_at"] == SLOTS[2].period_end.close.astimezone(timezone.utc).isoformat()
    slots_with = {int(x.split("|")[1]) for x in k if x.startswith("forecast_outcome|")}
    assert slots_with == {1, 2, 3, 7, 8, 9, 10}               # valid and matured; 4-6 not valid; 11-12 pending


def test_the_report_scores_forecasts_separately_and_counts_pending_ones(run_state):
    rep = build_report(run_state / "s")
    f = rep["diagnostics"]["forecasts"]
    assert (f["scored"], f["pending"], f["missing"], f["decisions_scored"]) == (84, 24, 0, 7)
    assert f["selected_vs_rejected"]["selected_n"] == 10 and f["selected_vs_rejected"]["rejected_n"] == 74
    assert rep["diagnostics"]["secondary_only"] is True
    md = markdown(rep)
    assert md.index("## Secondary diagnostics (cannot change the verdict)") > md.index(rep["verdict"])


def test_the_shadow_portfolio_holds_70_percent_voo_and_2_5_percent_in_each_candidate(run_state):
    k = by_key(run_state / "s")
    for slot in (SLOTS[0], SLOTS[5]):
        o = k[f"session|shadow|{slot.fill.day}|open"]["payload"]
        assert o["decision"] == {"slot": slot.index, "status": "mechanical"}
        stocks = {e["symbol"]: D(e["resulting_weight_pct"]) for e in o["plan"]}
        assert len(stocks) == 12 and all(D("2.3") <= w <= D("2.5") or e_skipped(o, s) for s, w in stocks.items())
    rep = build_report(run_state / "s")["diagnostics"]["shadow"]
    assert rep["complete"] and rep["stock_holdings_at_end"] == 12 and rep["ai_max_stock_holdings"] == 3
    assert rep["sessions_valued"] == rep["sessions"] == 251
    v = [D(x) for x in rep["voo_pct_after_each_fill"]]
    assert len(v) == 12 and D("69.9") < v[0] < D("70.1")       # launch: 70/30 to the cent
    assert rep["skipped_rebalancing_trades"] > 0 and min(v) < 70  # then drift: sub-$1 rebalances are skipped


def e_skipped(o, s):
    return next(e for e in o["plan"] if e["symbol"] == s)["note"].startswith("skipped")


def test_a_shadow_data_gap_never_touches_the_primary_record(tmp_path):
    synthetic.build(tmp_path / "data", drop_prices=[("SYNTH12", "2031-02-03", "open")])   # only the shadow needs it
    dry(tmp_path / "s", tmp_path / "data", ALL_VOO)
    rep = build_report(tmp_path / "s")
    assert rep["complete"] and not rep["unresolved_gaps"]
    assert not rep["diagnostics"]["shadow"]["complete"]
    assert rep["diagnostics"]["shadow"]["unresolved_gaps"][0]["missing"] == ["SYNTH12"]


# ------------------------------------------------------------------ diagnostics cannot advance a failed primary
def test_no_diagnostic_can_change_the_verdict_or_the_outcome_rule(run_state, monkeypatch):
    assert "diagnostic" not in " ".join(inspect.signature(verdict).parameters)
    assert "never changes the primary verdict" in FROZEN["diagnostics"]["status"]
    before = build_report(run_state / "s")
    assert before["outcome_rule_mechanics"] == "stop this version"             # all-in below VOO in this fixture
    perfect = {"scored": 84, "pending": 0, "missing": 0, "decisions_scored": 7, "brier": D(0),
               "rank_association": {"mean_spearman": D(1)},
               "selected_vs_rejected": {"selected_mean_excess_pp": D(50), "rejected_mean_excess_pp": D(-50)},
               "pending_rows": [], "missing_rows": [], "rows": [], "notes": []}
    monkeypatch.setattr(report, "score", lambda *a, **k: perfect)
    after = build_report(run_state / "s")
    assert after["diagnostics"]["forecasts"]["brier"] == "0"
    for key in ("verdict", "outcome_rule_mechanics", "complete", "incomplete_because", "ai_end_all_in", "voo_end"):
        assert after[key] == before[key], key
    for args in (("forward", 0, False, True, D("99.99"), D(100), 12),            # STOP stays STOP
                 ("forward", 0, False, False, D(120), D(100), 12)):              # INCONCLUSIVE stays INCONCLUSIVE
        assert verdict(*args) == verdict(*args) and not verdict(*args).startswith("ELIGIBLE")
    assert diagnostics.HORIZON_SLOTS == 3 and diagnostics.SHADOW_STOCK_PCT == 30

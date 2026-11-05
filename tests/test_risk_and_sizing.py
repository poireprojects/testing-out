from datetime import date, datetime, timedelta, timezone

import pytest

from dkapp.clock import ET, MarketCalendar, session_state
from dkapp.config import AppSettings
from dkapp.models import AccountInfo, AssetInfo
from dkapp.risk.engine import EntryProposal, OpenRisk, RiskContext, RiskEngine, floor_qty, round_price
from dkapp.risk.fees import CostModel
from dkapp.risk.sizing import SizingInputs, position_size, scaled_limits, spendable_cash

UTC = timezone.utc
CAL = MarketCalendar()
NOW = datetime(2026, 11, 4, 11, 0, tzinfo=ET).astimezone(UTC)


def acct(equity=100.0, cash=100.0, bp=None, **kw):
    bp = cash if bp is None else bp
    return AccountInfo("PA123", "active", "USD", equity, equity, cash, bp, bp, equity, equity - cash, 1.0, False,
                       False, True, **kw)


def ctx(now=NOW, **kw):
    s = session_state(CAL, now)
    base = dict(now=now, session=s, mode="paper", autopilot=True, emergency_stop=False, live_authorized=False,
                broker_connected=True, data_ok=True, account=acct(), settled_cash=100.0)
    base.update(kw)
    return RiskContext(**base)


def proposal(**kw):
    base = dict(symbol="AAPL", price=100.0, bid=99.99, ask=100.01, quote_time=NOW - timedelta(seconds=3),
                stop_price=98.8, target_price=102.5, strategy="orb", strategy_validated=False,
                asset=AssetInfo("AAPL", "Apple", "NASDAQ", "us_equity", True, True))
    base.update(kw)
    return EntryProposal(**base)


@pytest.fixture
def engine():
    return RiskEngine(AppSettings())


# ---------------------------------------------------------------- sizing & compounding
def test_compounding_scales_up_and_down():
    s = AppSettings()
    assert scaled_limits(s, 100)["max_position_usd"] == pytest.approx(20)
    assert scaled_limits(s, 105)["max_position_usd"] == pytest.approx(21)
    assert scaled_limits(s, 95)["max_position_usd"] == pytest.approx(19)
    assert scaled_limits(s, 95)["daily_loss_limit_usd"] == pytest.approx(1.9)


def test_fixed_allocation_never_grows_but_shrinks():
    s = AppSettings()
    s.compounding.mode = "fixed"
    s.compounding.fixed_allocation_usd = 100
    assert scaled_limits(s, 150)["max_position_usd"] == pytest.approx(20)
    assert scaled_limits(s, 80)["max_position_usd"] == pytest.approx(16)


def test_size_limited_by_settled_cash_not_portfolio_value():
    s = AppSettings()
    r = position_size(s, SizingInputs(equity=100, settled_cash=12.0, broker_cash=60, broker_buying_power=60), 1.0)
    assert r.notional <= 12.0 and r.binding == "available settled cash"


def test_no_margin_even_if_broker_offers_buying_power():
    s = AppSettings()
    s.risk.settlement_mode = "broker"
    inp = SizingInputs(equity=100, settled_cash=100, broker_cash=10, broker_buying_power=400)
    assert spendable_cash(s, inp) == 10


def test_risk_per_trade_caps_wide_stops():
    s = AppSettings()
    r = position_size(s, SizingInputs(100, 100, 100, 100), stop_pct=10.0)  # $1 risk / 10% = $10
    assert r.notional == pytest.approx(10.0) and r.binding == "risk per trade"


def test_losses_only_shrink_size():
    s = AppSettings()
    sizes = [position_size(s, SizingInputs(100, 100, 100, 100, consecutive_losses=n), 1.0).notional for n in range(5)]
    assert sizes == sorted(sizes, reverse=True) and sizes[0] == pytest.approx(20) and min(sizes) >= 10


def test_pending_orders_reserve_cash():
    s = AppSettings()
    r = position_size(s, SizingInputs(100, 25, 100, 100, pending_buy_notional=20), 1.0)
    assert r.notional <= 5.0


# ---------------------------------------------------------------- risk rules
def test_happy_path_is_approved(engine):
    d = engine.check_entry(ctx(), proposal())
    assert d.approved, d.reasons
    assert d.notional <= 20 and d.qty == floor_qty(d.notional / d.limit_price)
    assert d.limit_price == round_price(100.01 * 1.001)


@pytest.mark.parametrize("kw,needle", [
    (dict(emergency_stop=True), "emergency"),
    (dict(autopilot=False), "autopilot"),
    (dict(broker_connected=False), "brokerage"),
    (dict(data_ok=False), "market data"),
    (dict(account=acct(trading_blocked=True) if False else AccountInfo("PA1", "active", "USD", 100, 100, 100, 100, 100, 100, 0, 1, True, False, True)), "blocked"),
    (dict(realized_today=-2.0), "daily loss limit"),
    (dict(consecutive_losses=3), "in a row"),
    (dict(trades_today=10), "trades today"),
    (dict(open_positions=[OpenRisk("MSFT", 0.05, 400, 400, 396), OpenRisk("NVDA", 0.1, 150, 150, 148)]), "allowed positions"),
    (dict(pending_entry_symbols={"AAPL"}), "already have"),
    (dict(held_symbols={"AAPL"}), "already have"),
    (dict(last_exit_time={"AAPL": NOW - timedelta(minutes=5)}), "cooling down"),
])
def test_rules_block_entries(engine, kw, needle):
    d = engine.check_entry(ctx(**kw), proposal())
    assert not d.approved
    assert any(needle in r for r in d.reasons), d.reasons


def test_market_hours(engine):
    for t, needle in [(datetime(2026, 11, 4, 9, 0, tzinfo=ET), "closed"), (datetime(2026, 11, 4, 9, 32, tzinfo=ET), "first"),
                      (datetime(2026, 11, 4, 15, 45, tzinfo=ET), "close"), (datetime(2026, 11, 7, 12, 0, tzinfo=ET), "closed"),
                      (datetime(2026, 11, 26, 12, 0, tzinfo=ET), "closed")]:
        now = t.astimezone(UTC)
        d = engine.check_entry(ctx(now=now), proposal(quote_time=now))
        assert not d.approved and any(needle in r for r in d.reasons), (t, d.reasons)


def test_stale_data_and_wide_spread(engine):
    d = engine.check_entry(ctx(), proposal(quote_time=NOW - timedelta(seconds=300)))
    assert any("old" in r for r in d.reasons)
    d = engine.check_entry(ctx(), proposal(bid=99.5, ask=100.5))
    assert any("spread" in r for r in d.reasons)
    d = engine.check_entry(ctx(), proposal(quote_time=None))
    assert any("no live quote" in r for r in d.reasons)


@pytest.mark.parametrize("symbol,asset", [
    ("BTC/USD", AssetInfo("BTC/USD", "Bitcoin", "CRYPTO", "crypto", True, True)),
    ("AAPL261120C00200000", None),
    ("TQQQ", AssetInfo("TQQQ", "ProShares UltraPro QQQ 3x", "NASDAQ", "us_equity", True, True)),
])
def test_no_crypto_options_or_leverage(engine, symbol, asset):
    d = engine.check_entry(ctx(), proposal(symbol=symbol, asset=asset))
    assert not d.approved


def test_price_range(engine):
    d = engine.check_entry(ctx(), proposal(price=2.0, bid=1.99, ask=2.01, stop_price=1.97, target_price=2.06))
    assert any("outside" in r for r in d.reasons)


def test_stop_too_wide_is_rejected_and_too_tight_is_widened(engine):
    d = engine.check_entry(ctx(), proposal(stop_price=90.0))
    assert any("stop would be" in r for r in d.reasons)
    d = engine.check_entry(ctx(), proposal(stop_price=99.95))
    assert d.approved and any("widened" in n for n in d.notes)
    assert d.stop_price == round_price(100.01 * (1 - 0.004))


def test_size_is_cut_so_stopouts_cannot_break_the_daily_limit(engine):
    # already down $1.80 of a $2.00 limit -> only ~$0.20 of room for a new stop-out
    d = engine.check_entry(ctx(realized_today=-1.8), proposal())
    assert d.approved and d.notional < 20
    worst = d.notional * ((100.01 - d.stop_price) / 100.01) + 2 * 5 / 10_000 * d.notional
    assert worst <= 0.2 + 1e-6


def test_open_risk_counts_against_daily_limit(engine):
    big = OpenRisk("MSFT", 1.0, 100, 100, 98.1)  # $1.90 of the $2.00 limit already at risk
    d = engine.check_entry(ctx(open_positions=[big]), proposal())
    assert d.notional < 10
    small = OpenRisk("MSFT", 1.0, 100, 100, 99.5)  # only $0.50 at risk -> full size still fits
    assert engine.check_entry(ctx(open_positions=[small]), proposal()).notional == pytest.approx(20)


def test_live_requires_authorization_and_evidence(engine):
    d = engine.check_entry(ctx(mode="live", live_authorized=False), proposal(strategy_validated=True))
    assert any("not authorized" in r for r in d.reasons)
    d = engine.check_entry(ctx(mode="live", live_authorized=True), proposal(strategy_validated=False))
    assert any("backtest evidence" in r for r in d.reasons)
    d = engine.check_entry(ctx(mode="live", live_authorized=True), proposal(strategy_validated=True))
    assert d.approved, d.reasons


def test_paper_can_be_restricted_to_validated(engine):
    engine.settings.strategies.paper_allow_unvalidated = False
    assert not engine.check_entry(ctx(), proposal()).approved


def test_legacy_pdt_guard_is_opt_in(engine):
    pdt = acct()
    pdt.daytrade_count, pdt.pattern_day_trader = 3, False
    assert engine.check_entry(ctx(account=pdt), proposal()).approved  # retired rule: off by default
    engine.settings.risk.pdt_guard = True
    assert not engine.check_entry(ctx(account=pdt), proposal()).approved


def test_exits_never_oversell():
    assert RiskEngine.check_exit("AAPL", 1.0, 1.0)[0]
    assert not RiskEngine.check_exit("AAPL", 1.5, 1.0)[0]
    assert not RiskEngine.check_exit("AAPL", 0.0, 1.0)[0]


def test_daily_limit_breach_detection(engine):
    assert engine.daily_limit_breached(ctx(realized_today=-1.5, unrealized_today=-0.5))
    assert not engine.daily_limit_breached(ctx(realized_today=-1.5, unrealized_today=0.2))


# ---------------------------------------------------------------- fees & prices
def test_regulatory_fees_round_up_to_the_cent():
    c = CostModel()
    assert c.sell_fees(20.0, 0.1) == pytest.approx(0.02)  # $0.01 SEC + $0.01 TAF minimums
    assert c.sell_fees(1_000_000, 10_000) == pytest.approx(27.80 + 1.66)
    assert c.sell_fees(10_000_000, 1_000_000) == pytest.approx(278.0 + 8.30)  # TAF capped


def test_fill_prices_are_pessimistic():
    c = CostModel(slippage_bps=5)
    assert c.fill_price("buy", 100, 4) == pytest.approx(100 * (1 + 7 / 10_000))
    assert c.fill_price("sell", 100, 4) == pytest.approx(100 * (1 - 7 / 10_000))


def test_price_and_qty_rounding():
    assert round_price(12.3456) == 12.35 and round_price(0.123456) == 0.1235
    assert floor_qty(0.1234567891) == 0.123456

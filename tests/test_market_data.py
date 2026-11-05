from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from dkapp.clock import ET, ManualClock, MarketCalendar
from dkapp.credentials import AlpacaKeys
from dkapp.data.alpaca_data import AlpacaMarketData, convert_snapshot
from dkapp.data.base import DataUnavailable
from dkapp.data.demo_data import DemoMarket, DemoMarketData
from dkapp.data.ratelimit import RateLimitTimeout, TokenBucket
from dkapp.data.universe import asset_allowed, build_universe, curated_universe, is_safe_symbol
from dkapp.models import AssetInfo

UTC = timezone.utc


def ts(h, m, s=0):
    return datetime(2026, 11, 4, h, m, s, tzinfo=ET).astimezone(UTC)


# ---------------------------------------------------------------- universe
def test_curated_universe_size_and_safety():
    u = curated_universe()
    assert 300 <= len(u) <= 600
    assert len(set(u)) == len(u)
    assert all(is_safe_symbol(s) for s in u)
    for bad in ("TQQQ", "SQQQ", "IBIT", "BITO", "SOXL"):
        assert bad not in u


def test_asset_filter_rejects_crypto_leverage_and_non_fractionable():
    ok = AssetInfo("AAPL", "Apple Inc", "NASDAQ", "us_equity", True, True)
    assert asset_allowed(ok)[0]
    assert not asset_allowed(AssetInfo("BTC/USD", "Bitcoin", "CRYPTO", "crypto", True, True))[0]
    assert not asset_allowed(AssetInfo("XYZL", "ProShares UltraPro 3x Long", "ARCA", "us_equity", True, True))[0]
    assert not asset_allowed(AssetInfo("ABC", "Abc Corp", "NYSE", "us_equity", True, False))[0]
    assert not asset_allowed(AssetInfo("ABC", "Abc Corp", "OTC", "us_equity", True, True))[0]


def test_build_universe_with_broker_assets():
    assets = [AssetInfo(s, s, "NYSE", "us_equity", True, s != "MSFT") for s in ("SPY", "AAPL", "MSFT")]
    uni, rejected = build_universe(assets, 400)
    assert "AAPL" in uni and "MSFT" not in uni
    assert rejected["MSFT"] == "fractional shares not supported"
    assert "not offered by broker" in rejected.values()


def test_symbol_validation():
    assert is_safe_symbol("BRK.B")
    for s in ("BTC/USD", "AAPL240119C00150000", "", "toolongsym"):
        assert not is_safe_symbol(s)


# ---------------------------------------------------------------- rate limiter
def test_token_bucket_limits_and_refills():
    t = [0.0]
    b = TokenBucket(60, burst=2, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert b.try_acquire() and b.try_acquire()
    assert not b.try_acquire()
    t[0] += 1.0  # 60/min = 1 token per second
    assert b.try_acquire()
    b.acquire(1, timeout=5)  # waits ~1s of fake time
    assert t[0] >= 2.0
    with pytest.raises(RateLimitTimeout):
        b.acquire(2, timeout=0.5)


# ---------------------------------------------------------------- demo data
@pytest.fixture
def demo():
    cal = MarketCalendar()
    clock = ManualClock(ts(10, 0))
    market = DemoMarket(["SPY", "AAPL", "MSFT"], cal, seed=3)
    return clock, market, DemoMarketData(market, clock)


def test_demo_bars_never_include_the_future(demo):
    clock, market, data = demo
    bars = data.get_intraday_bars(["AAPL"], ts(9, 30), ts(16, 0))["AAPL"]
    assert len(bars) == 30  # 9:30..9:59 completed by 10:00
    assert bars.index[-1] < clock.now()
    clock.set(ts(10, 0, 30))
    assert len(data.get_intraday_bars(["AAPL"], ts(9, 30), ts(16, 0))["AAPL"]) == 30  # 10:00 bar not done


def test_demo_snapshot_is_consistent(demo):
    clock, market, data = demo
    s = data.get_snapshots(["AAPL"])["AAPL"]
    assert s.quote.bid < s.quote.ask
    assert s.day_low <= s.price <= s.day_high
    assert s.prev_close > 0 and s.day_volume > 0
    assert isinstance(s.price, float)


def test_demo_is_deterministic():
    cal = MarketCalendar()
    a = DemoMarket(["AAPL"], cal, seed=5).frame("AAPL", date(2026, 11, 4))
    b = DemoMarket(["AAPL"], cal, seed=5).frame("AAPL", date(2026, 11, 4))
    pd.testing.assert_frame_equal(a, b)


def test_demo_daily_bars_only_completed_days(demo):
    clock, market, data = demo
    d = data.get_daily_bars(["SPY"], ts(9, 0) - timedelta(days=10), clock.now())["SPY"]
    assert d.index[-1].date() < date(2026, 11, 4)


# ---------------------------------------------------------------- alpaca adapter (fake client)
def _bar(t, px, v=100):
    return SimpleNamespace(timestamp=t, open=px, high=px + 0.1, low=px - 0.1, close=px, volume=v, vwap=px)


class FakeDataClient:
    def __init__(self, fail=False):
        self.snapshot_calls = []
        self.bar_calls = []
        self.fail = fail

    def get_stock_snapshot(self, req):
        if self.fail:
            raise RuntimeError("503 service unavailable")
        self.snapshot_calls.append(list(req.symbol_or_symbols))
        out = {}
        now = ts(10, 0)
        for s in req.symbol_or_symbols:
            out[s] = SimpleNamespace(
                latest_trade=SimpleNamespace(price=10.0, timestamp=now - timedelta(seconds=5)),
                latest_quote=SimpleNamespace(bid_price=9.99, ask_price=10.01, bid_size=1, ask_size=1, timestamp=now),
                minute_bar=_bar(now - timedelta(minutes=1), 10.0),
                daily_bar=SimpleNamespace(timestamp=ts(4, 0), open=9.5, high=10.2, low=9.4, close=10.0, volume=5000),
                previous_daily_bar=SimpleNamespace(timestamp=ts(4, 0) - timedelta(days=1), open=9, high=9.6, low=8.9,
                                                   close=9.5, volume=4000))
        return out

    def get_stock_bars(self, req):
        self.bar_calls.append(req)
        syms = req.symbol_or_symbols
        return SimpleNamespace(data={s: [_bar(ts(9, 30) + timedelta(minutes=i), 10 + i * 0.01) for i in range(5)] for s in syms})


def test_alpaca_snapshots_are_batched_and_converted():
    fake = FakeDataClient()
    md = AlpacaMarketData(AlpacaKeys("k", "s"), feed="iex", requests_per_minute=1000, client=fake)
    syms = [f"S{i}" for i in range(250)]
    snaps = md.get_snapshots(syms)
    assert len(fake.snapshot_calls) == 3 and max(len(c) for c in fake.snapshot_calls) <= 100
    s = snaps["S0"]
    assert s.price == pytest.approx(10.0)  # quote mid is newer than the trade
    assert s.prev_close == 9.5 and s.day_volume == 5000
    assert s.quote.spread_bps == pytest.approx(20.0, rel=1e-3)
    assert "IEX" in md.feed_label and "only" in md.coverage_note


def test_alpaca_bars_use_requested_feed_and_normalize():
    from alpaca.data.enums import DataFeed
    fake = FakeDataClient()
    md = AlpacaMarketData(AlpacaKeys("k", "s"), feed="iex", requests_per_minute=1000, client=fake)
    bars = md.get_intraday_bars(["AAPL", "MSFT"], ts(9, 30), ts(10, 0))
    assert fake.bar_calls[0].feed == DataFeed.IEX
    assert list(bars["AAPL"].columns) == ["open", "high", "low", "close", "volume", "vwap"]
    assert str(bars["AAPL"].index.tz) == "UTC"


def test_alpaca_errors_become_data_unavailable():
    md = AlpacaMarketData(AlpacaKeys("k", "s"), client=FakeDataClient(fail=True), requests_per_minute=1000)
    with pytest.raises(DataUnavailable):
        md.get_snapshots(["AAPL"])


def test_snapshot_before_first_trade_uses_yesterday_close():
    now = ts(9, 0)
    snap = SimpleNamespace(
        latest_trade=SimpleNamespace(price=50.0, timestamp=now), latest_quote=None, minute_bar=None,
        daily_bar=SimpleNamespace(timestamp=now - timedelta(days=1), open=49, high=51, low=48, close=50.5, volume=10),
        previous_daily_bar=SimpleNamespace(timestamp=now - timedelta(days=2), open=48, high=50, low=47, close=49, volume=9))
    s = convert_snapshot("X", snap)
    assert s.prev_close == 50.5 and s.day_open == 0.0 and s.quote is None

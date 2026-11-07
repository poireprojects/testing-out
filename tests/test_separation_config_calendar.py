import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stockpilot import credentials, paths
from stockpilot.broker.alpaca_broker import AlpacaBroker, convert_order
from stockpilot.broker.base import BrokerUnavailable, DuplicateOrder, NotFound, OrderRejected
from stockpilot.clock import ET, MarketCalendar, rule_based_session, session_state
from stockpilot.config import (LIVE_ACKS, LIVE_PHRASE, AppSettings, authorize_live, live_authorization_status,
                          load_settings, save_settings)
from stockpilot.credentials import AlpacaKeys
from stockpilot.models import OrderRequest
from stockpilot.risk.settlement import SettlementLedger
from stockpilot.runtime import NotConfigured, Runtime
from stockpilot.storage.db import Database

UTC = timezone.utc


# ================================================================ paper vs live separation
def test_separate_databases_per_mode(tmp_path):
    assert paths.database_file("paper") != paths.database_file("live") != paths.database_file("demo")
    db = Database(tmp_path / "x.db", "paper")
    db.close()
    with pytest.raises(RuntimeError):
        Database(tmp_path / "x.db", "live")  # a paper database can never be opened as live


def test_credentials_are_separate_and_secure():
    credentials.save_alpaca_keys("paper", "PKPAPER", "paper-secret")
    assert credentials.load_alpaca_keys("live") is None
    credentials.save_alpaca_keys("live", "AKLIVE", "live-secret")
    assert credentials.load_alpaca_keys("paper").key_id == "PKPAPER"
    assert credentials.load_alpaca_keys("live").key_id == "AKLIVE"
    credentials.delete_alpaca_keys("paper")
    assert credentials.load_alpaca_keys("paper") is None and credentials.load_alpaca_keys("live") is not None
    with pytest.raises(ValueError):
        credentials.save_alpaca_keys("demo", "a", "b")


def test_settings_file_never_contains_secrets(tmp_path):
    credentials.save_alpaca_keys("paper", "PKPAPER123", "supersecretvalue")
    s = AppSettings()
    save_settings(s)
    text = paths.settings_file().read_text()
    assert "supersecretvalue" not in text and "PKPAPER123" not in text


def test_paper_mode_without_keys_is_not_configured():
    with pytest.raises(NotConfigured):
        Runtime(AppSettings(), "paper").build()


def test_live_authorization_flow():
    s = AppSettings()
    assert live_authorization_status(s)[0] is False
    assert authorize_live(s, "yes please", list(LIVE_ACKS))[0] is False
    assert authorize_live(s, LIVE_PHRASE, list(LIVE_ACKS)[:2])[0] is False
    ok, _ = authorize_live(s, LIVE_PHRASE.upper(), list(LIVE_ACKS))
    assert ok and live_authorization_status(s)[0]
    s.risk.max_position_usd = 15  # tightening keeps authorization
    assert live_authorization_status(s)[0]
    s.risk.max_position_usd = 50  # loosening revokes it
    ok, msg = live_authorization_status(s)
    assert not ok and "re-authorize" in msg
    s.risk.max_position_usd = 20
    assert live_authorization_status(s)[0]
    s.risk.settlement_mode = "conservative"   # tighter: still authorized
    assert live_authorization_status(s)[0]
    ok, _ = authorize_live(s, LIVE_PHRASE, list(LIVE_ACKS))
    s.risk.settlement_mode = "auto"           # looser than what was authorized: revoked
    assert not live_authorization_status(s)[0]


class FakeTradingClient:
    def __init__(self, account_number="PA1234", fail=None):
        self.account_number = account_number
        self.fail = fail
        self.submitted = []

    def get_account(self):
        return SimpleNamespace(account_number=self.account_number, status="ACTIVE", currency="USD", equity="100.5",
                               last_equity="100", cash="80.5", buying_power="80.5", non_marginable_buying_power="80.5",
                               portfolio_value="100.5", long_market_value="20", multiplier="1", trading_blocked=False,
                               account_blocked=False, daytrade_count=None, pattern_day_trader=None, accrued_fees="0")

    def submit_order(self, req):
        if self.fail:
            raise self.fail
        self.submitted.append(req)
        return SimpleNamespace(id="o1", client_order_id=req.client_order_id, symbol=req.symbol, side=req.side,
                               order_type="limit", type="limit", status="accepted", qty=str(req.qty), notional=None,
                               filled_qty="0", filled_avg_price=None, limit_price=getattr(req, "limit_price", None),
                               stop_price=getattr(req, "stop_price", None), time_in_force="day", submitted_at=None,
                               filled_at=None, updated_at=None)


def test_alpaca_paper_flag_and_environment_check():
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=True, client=FakeTradingClient("PA999"))
    assert b.is_paper and b.verify_environment()[0]
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=True, client=FakeTradingClient("123456789"))
    ok, msg = b.verify_environment()
    assert not ok and "LIVE" in msg
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=False, client=FakeTradingClient("PA999"))
    assert not b.verify_environment()[0]


def test_real_trading_client_gets_paper_flag():
    from alpaca.trading.client import TradingClient
    paper = AlpacaBroker(AlpacaKeys("PKTEST", "secret"), paper=True)
    live = AlpacaBroker(AlpacaKeys("AKTEST", "secret"), paper=False)
    assert isinstance(paper.client, TradingClient)
    assert "paper" in paper.client._base_url and "paper" not in live.client._base_url


def test_alpaca_order_mapping_day_only_no_shorts_in_request():
    fake = FakeTradingClient()
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=True, client=fake)
    bo = b.submit_order(OrderRequest("AAPL", "buy", "limit", "dkp-1", qty=0.123456, limit_price=150.12))
    r = fake.submitted[0]
    assert str(r.time_in_force.value).lower() == "day" and r.extended_hours is False and r.client_order_id == "dkp-1"
    assert bo.status == "accepted" and bo.qty == pytest.approx(0.123456)
    with pytest.raises(OrderRejected):
        b.submit_order(OrderRequest("AAPL", "buy", "limit", "dkp-2", qty=1, limit_price=1, time_in_force="gtc"))


def _api_error(status, message):
    from alpaca.common.exceptions import APIError
    http = SimpleNamespace(response=SimpleNamespace(status_code=status), request=None)
    return APIError(json.dumps({"code": 1, "message": message}), http)


@pytest.mark.parametrize("status,msg,exc", [
    (500, "internal", BrokerUnavailable), (429, "rate limit", BrokerUnavailable),
    (422, "client_order_id must be unique", DuplicateOrder), (403, "insufficient buying power", OrderRejected),
    (404, "order not found", NotFound),
])
def test_alpaca_error_mapping(status, msg, exc):
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=True, client=FakeTradingClient(fail=_api_error(status, msg)))
    with pytest.raises(exc):
        b.submit_order(OrderRequest("AAPL", "buy", "market", "x", qty=1))


def test_network_error_is_retryable():
    import requests
    b = AlpacaBroker(AlpacaKeys("k", "s"), paper=True, client=FakeTradingClient(fail=requests.ConnectionError("down")))
    with pytest.raises(BrokerUnavailable):
        b.submit_order(OrderRequest("AAPL", "buy", "market", "x", qty=1))


# ================================================================ settings persistence
def test_settings_round_trip_and_corruption_backup():
    s = AppSettings()
    s.risk.max_position_usd = 17.5
    s.window.x = 40
    save_settings(s)
    assert load_settings().risk.max_position_usd == 17.5
    paths.settings_file().write_text("{ not json")
    restored = load_settings()
    assert restored.risk.max_position_usd == 20.0
    assert list(paths.config_dir().glob("settings.broken-*.json"))


def test_defaults_match_requested_experimental_limits():
    r = AppSettings().risk
    assert (r.starting_capital, r.max_position_usd, r.max_positions, r.daily_loss_limit_usd) == (100, 20, 2, 2)
    s = AppSettings()
    assert s.broker.mode == "demo" and s.broker.live_authorization is None


# ================================================================ calendar & sessions
def test_holidays_and_early_closes():
    cal = MarketCalendar()
    assert cal.session(date(2026, 11, 26)) is None          # Thanksgiving
    assert cal.session(date(2026, 11, 27)).early_close      # day after
    assert cal.session(date(2026, 7, 3)) is None            # July 4 is a Saturday -> observed Friday
    assert cal.session(date(2026, 4, 3)) is None            # Good Friday
    assert cal.session(date(2026, 11, 7)) is None           # Saturday
    assert rule_based_session(date(2026, 12, 24)).early_close


def test_broker_calendar_overrides_rules():
    cal = MarketCalendar()
    d = date(2026, 11, 4)
    special = rule_based_session(date(2026, 11, 5))
    cal.load([special], date(2026, 11, 4), date(2026, 11, 5))
    assert cal.session(d) is None  # not in the broker's list -> closed (e.g. unscheduled closure)
    assert cal.session(date(2026, 11, 5)) == special


def test_session_phases():
    cal = MarketCalendar()
    f = lambda h, m: session_state(cal, datetime(2026, 11, 4, h, m, tzinfo=ET)).phase
    assert [f(8, 0), f(9, 31), f(10, 0), f(15, 40), f(15, 55), f(16, 5)] == \
           ["closed", "opening", "trading", "wind_down", "flatten", "after_close"]
    early = session_state(cal, datetime(2026, 11, 27, 12, 45, tzinfo=ET))
    assert early.phase == "wind_down"  # early close at 13:00


# ================================================================ settlement
def test_t_plus_one_settlement_skips_weekends_and_holidays(tmp_path):
    db = Database(tmp_path / "s.db", "paper")
    cal = MarketCalendar()
    led = SettlementLedger(db, cal)
    assert led.record_sale(1, 20.0, date(2026, 11, 6)) == date(2026, 11, 9)    # Fri -> Mon
    assert led.record_sale(2, 10.0, date(2026, 11, 25)) == date(2026, 11, 27)  # skips Thanksgiving
    assert led.unsettled(date(2026, 11, 6)) == pytest.approx(30.0)
    assert led.settled_cash(100.0, date(2026, 11, 6)) == pytest.approx(70.0)
    assert led.settled_cash(100.0, date(2026, 11, 9)) == pytest.approx(90.0)
    assert led.settled_cash(100.0, date(2026, 11, 30)) == pytest.approx(100.0)


def _two_round_trips(tmp_path, mode):
    from tests.harness import enter, make_rig, override_price
    rig = make_rig(tmp_path, start_offset_min=90, universe_size=80, starting_cash=40.0)
    rig.settings.risk.settlement_mode = mode
    rig.settings.risk.starting_capital = 40.0  # two full $20 positions use all of the cash
    rig.engine.step()
    t1 = enter(rig, "AAPL", target_pct=0.6)
    t2 = enter(rig, "MSFT", target_pct=0.6)
    for t in (t1, t2):
        override_price(rig, t["symbol"], t["target_price"] * 1.003)
    rig.run_until(rig.clock.now() + timedelta(seconds=20), step_seconds=5)
    assert all(rig.db.trade(t["id"])["status"] == "closed" for t in (t1, t2))
    rig.engine.reconcile(rig.clock.now(), force=True)
    rig.engine.settings.risk.symbol_cooldown_minutes = 0
    return rig, enter


def test_limited_margin_account_reuses_same_day_proceeds_without_leverage(tmp_path):
    """Alpaca accounts under $2,000 are limited-margin (1x): today's sale proceeds can buy again today."""
    rig, enter = _two_round_trips(tmp_path, "auto")
    assert rig.engine._settled_cash(rig.clock.now()) < 1.0  # all of it is still unsettled...
    t3 = enter(rig, "NVDA")                                  # ...and it may be reused
    assert t3 is not None and t3["entry_qty"] > 0
    acct = rig.broker.get_account()
    assert acct.cash >= -1e-9, "never borrowed"
    assert t3["entry_qty"] * t3["entry_price"] <= 20.0 * 1.01 + 0.01


def test_conservative_mode_still_refuses_unsettled_proceeds(tmp_path):
    rig, enter = _two_round_trips(tmp_path, "conservative")
    assert rig.broker.get_account().cash > 39
    assert rig.engine._settled_cash(rig.clock.now()) < 1.0
    assert enter(rig, "NVDA") is None


def _legacy_sales_proceeds_cannot_be_reused_same_day(tmp_path):
    """(old conservative-default behaviour, kept for reference; see the two tests above)"""
    from tests.harness import enter, make_rig, override_price
    rig = make_rig(tmp_path, start_offset_min=90, universe_size=80, starting_cash=40.0)
    rig.settings.risk.starting_capital = 40.0  # two full $20 positions use all of the cash
    rig.engine.step()
    t1 = enter(rig, "AAPL", target_pct=0.6)
    t2 = enter(rig, "MSFT", target_pct=0.6)
    for t in (t1, t2):
        override_price(rig, t["symbol"], t["target_price"] * 1.003)
    rig.run_until(rig.clock.now() + timedelta(seconds=20), step_seconds=5)
    assert all(rig.db.trade(t["id"])["status"] == "closed" for t in (t1, t2))
    # all $40 is now unsettled sale proceeds -> no new buys today, even though the broker shows cash
    assert rig.broker.get_account().cash > 39
    rig.engine.reconcile(rig.clock.now(), force=True)
    assert rig.engine._settled_cash(rig.clock.now()) < 1.0
    rig.engine.settings.risk.symbol_cooldown_minutes = 0
    assert enter(rig, "NVDA") is None

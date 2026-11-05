"""Live checks against Alpaca's PAPER environment.

Skipped unless ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET are set. Set
ALPACA_PAPER_ORDER_TEST=1 to also place (and immediately cancel) a tiny limit order far
below the market — paper money only.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from dkapp.credentials import AlpacaKeys

pytestmark = pytest.mark.integration
KEY, SECRET = os.environ.get("ALPACA_PAPER_KEY"), os.environ.get("ALPACA_PAPER_SECRET")
needs_keys = pytest.mark.skipif(not (KEY and SECRET), reason="no Alpaca paper credentials in the environment")


@needs_keys
def test_paper_account_clock_assets_and_data():
    from dkapp.broker.alpaca_broker import AlpacaBroker
    from dkapp.data.alpaca_data import AlpacaMarketData
    b = AlpacaBroker(AlpacaKeys(KEY, SECRET), paper=True)
    ok, msg = b.verify_environment()
    assert ok, msg
    acct = b.get_account()
    assert acct.is_paper and acct.equity >= 0
    today = datetime.now(timezone.utc).date()
    assert b.get_calendar(today - timedelta(days=7), today + timedelta(days=7))
    assets = b.get_assets()
    assert any(a.symbol == "SPY" and a.fractionable for a in assets)
    md = AlpacaMarketData(AlpacaKeys(KEY, SECRET), feed="iex")
    snaps = md.get_snapshots(["SPY", "AAPL"])
    assert snaps["SPY"].price > 0
    bars = md.get_intraday_bars(["SPY"], datetime.now(timezone.utc) - timedelta(days=5), datetime.now(timezone.utc) - timedelta(minutes=20))
    assert not bars["SPY"].empty


@needs_keys
@pytest.mark.skipif(os.environ.get("ALPACA_PAPER_ORDER_TEST") != "1", reason="order test not requested")
def test_paper_order_submit_lookup_cancel():
    from dkapp.broker.alpaca_broker import AlpacaBroker
    from dkapp.data.alpaca_data import AlpacaMarketData
    from dkapp.models import OrderRequest
    b = AlpacaBroker(AlpacaKeys(KEY, SECRET), paper=True)
    px = AlpacaMarketData(AlpacaKeys(KEY, SECRET)).get_snapshots(["SPY"])["SPY"].price
    cid = f"dk-itest-{int(datetime.now().timestamp())}"
    o = b.submit_order(OrderRequest("SPY", "buy", "limit", cid, qty=0.01, limit_price=round(px * 0.5, 2)))
    try:
        assert b.get_order_by_client_id(cid).id == o.id
    finally:
        b.cancel_order(o.id)

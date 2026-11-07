"""Failure handling: laptop sleep, broker-refused stop orders, rejection wording, attention alerts."""
from datetime import timedelta

import pytest

from stockpilot.broker.explain import explain_rejection, with_raw
from tests.harness import enter, make_rig


@pytest.fixture
def rig(tmp_path):
    r = make_rig(tmp_path, start_offset_min=90, universe_size=80)  # 11:00 ET, market open
    r.engine.step()
    return r


class FakeWall:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- laptop sleep / frozen app
def test_sleep_is_detected_and_blocks_entries_until_rescanned(rig):
    eng = rig.engine
    wall = FakeWall()
    eng._wall = wall
    eng.step()
    t = enter(rig, "AAPL")
    assert t is not None
    # 25 minutes pass with no ticks (lid closed), market moved on
    wall.t += 25 * 60
    rig.clock.advance(25 * 60)
    eng.step()
    assert eng.resumed_at is not None
    assert eng.recovering(rig.clock.now())
    assert rig.db.query("SELECT * FROM events WHERE title='woke up after a pause'")
    assert any(a["title"] == "back after a pause" for a in eng.snapshot()["alerts"])
    assert eng.status_line(rig.clock.now())["headline"].startswith("back after a pause")
    # the risk engine refuses new entries while re-checking
    assert enter(rig, "MSFT") is None
    # the position is still managed and agrees with the broker (it may have hit its stop/target meanwhile)
    tr = rig.db.trade(t["id"])
    if tr["status"] == "closed":
        assert tr["exit_reason"] and rig.broker.state.positions.get("AAPL") is None
    else:
        assert tr["status"] in ("open", "closing") and rig.broker.state.positions.get("AAPL") is not None
    # after the hold and a fresh full scan, trading resumes
    for _ in range(10):
        wall.t += 10
        rig.clock.advance(10)
        eng.step()
    assert eng._last_full_scan_at and eng._last_full_scan_at > eng.resumed_at
    assert eng.recovering(rig.clock.now()) == ""


def test_normal_ticks_never_look_like_sleep(rig):
    eng = rig.engine
    wall = FakeWall()
    eng._wall = wall
    for _ in range(30):
        wall.t += 2
        rig.clock.advance(2)
        eng.step()
    assert eng.resumed_at is None


# ---------------------------------------------------------------- protective stops the broker refuses
def test_refused_stop_is_highly_visible_and_retried(rig):
    eng = rig.engine
    rig.broker.reject_stop_orders = True
    t = enter(rig, "NVDA")
    assert rig.db.trade(t["id"])["software_stop"] == 1
    eng._publish_snapshot(rig.clock.now())
    alerts = eng.snapshot()["alerts"]
    assert alerts and "no confirmed stop-loss order at the brokerage" in alerts[0]["title"]
    assert alerts[0]["level"] == "warning"  # paper: warn, keep trading
    assert eng.unprotected() == ["NVDA"]
    # the broker starts accepting stops again -> the app retries and is protected again
    rig.broker.reject_stop_orders = False
    rig.run_until(rig.clock.now() + timedelta(seconds=eng.STOP_RETRY_SECONDS + 30), step_seconds=10)
    tr = rig.db.trade(t["id"])
    assert tr["software_stop"] == 0
    assert eng.orders.active(trade_id=t["id"], purpose="stop")
    assert rig.db.query("SELECT * FROM events WHERE title LIKE '%protected by a broker stop again%'")
    assert eng.unprotected() == []


def test_unprotected_real_money_position_blocks_new_live_entries():
    from stockpilot.risk.engine import RiskEngine
    from stockpilot.config import AppSettings
    from tests.test_risk_and_sizing import ctx, proposal
    s = AppSettings()
    c = ctx(mode="live", live_authorized=True, unprotected_symbols=["NVDA"])
    d = RiskEngine(s).check_entry(c, proposal())
    assert not d.approved
    assert any("no broker-confirmed stop order" in r for r in d.reasons)
    # in paper the same situation is a warning, not a block
    d2 = RiskEngine(s).check_entry(ctx(mode="paper", unprotected_symbols=["NVDA"]), proposal())
    assert not any("no broker-confirmed stop order" in r for r in d2.reasons)


# ---------------------------------------------------------------- plain-English rejections
@pytest.mark.parametrize("raw,needle", [
    ("insufficient buying power", "enough settled cash"),
    ("asset AAPL is not fractionable", "fractions"),
    ("potential wash trade detected. use complex orders", "wash trade"),
    ("market is closed", "market was closed"),
    ("asset XYZ is not tradable", "can't be traded right now"),
    ("connection timed out", "unclear whether the order arrived"),
    ("something we have never seen", "broker refused the order"),
    ("", "gave no reason"),
])
def test_rejections_are_explained(raw, needle):
    assert needle in explain_rejection(raw)
    if raw:
        assert raw in with_raw(raw)


def test_rejected_entry_is_explained_in_the_activity_log(rig):
    rig.broker.reject_next = "insufficient buying power"
    enter(rig, "AAPL")
    rig.run_until(rig.clock.now() + timedelta(seconds=10), step_seconds=5)
    ev = rig.db.query("SELECT * FROM events WHERE title LIKE '%entry not filled%' OR title LIKE '%rejected%'")
    assert ev and any("enough settled cash" in (e["message"] or "") for e in ev)


# ---------------------------------------------------------------- one-click diagnostics
def test_diagnostics_bundle_has_logs_and_status_but_no_secrets(tmp_path, monkeypatch):
    import logging
    import zipfile
    from stockpilot import credentials, diagnostics, paths
    from stockpilot.config import AppSettings
    from stockpilot.storage.db import Database
    secret = "PKABCDEF1234567890XYZ"
    monkeypatch.setattr(credentials, "load_alpaca_keys", lambda env: (secret, "s" * 40) if env == "paper" else None)
    monkeypatch.setattr(credentials, "load_anthropic_key", lambda: None)
    (paths.log_dir() / "stockpilot.log").write_text(f"2026 INFO something api_key={secret} happened\nkey id {secret}\n")
    db = Database(paths.database_file("paper"), "paper")
    db.insert("orders", trade_id=1, purpose="entry", client_order_id="x1", symbol="AAPL", side="buy", type="limit",
              status="rejected", reject_reason="insufficient buying power", created_at="2026-11-04T15:00:00+00:00")
    db.close()
    out = diagnostics.build(AppSettings(), "paper", {"status": {"headline": "ok"}, "scanner": {"rows": [1] * 500}},
                            dest_dir=tmp_path)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "summary.txt" in names and "logs/stockpilot.log" in names
        blob = "".join(z.read(n).decode("utf-8", "replace") for n in names)
    assert secret not in blob
    summary = zipfile.ZipFile(out).read("summary.txt").decode()
    assert '"paperKeysSaved": true' in summary
    assert "enough settled cash" in summary
    assert '"rows"' not in summary  # big scanner table left out
    logging.getLogger(__name__).info("diagnostics at %s", out)

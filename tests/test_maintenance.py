from dkapp import credentials, maintenance, paths
from dkapp.config import AppSettings, save_settings
from dkapp.storage.db import Database


def _live_trade(status="open"):
    db = Database(paths.database_file("live"), "live")
    db.insert("trades", symbol="SPY", strategy="orb", mode="live", status=status, managed=1,
              session_date="2026-11-05", created_at="2026-11-05T15:00:00+00:00", entry_qty=0.01, entry_price=500)
    db.close()


def test_not_a_maintenance_command():
    assert maintenance.run(["app"]) is None
    assert maintenance.run(["app", "--minimized"]) is None


def test_open_live_positions_block_everything():
    _live_trade("open")
    save_settings(AppSettings())
    credentials.save_alpaca_keys("live", "AK", "S")
    assert maintenance.run(["app", "--check-live"]) == maintenance.BLOCKED_LIVE
    assert maintenance.run(["app", "--uninstall-prepare", "--delete-data"]) == maintenance.BLOCKED_LIVE
    assert maintenance.run(["app", "--shutdown-running"]) == maintenance.BLOCKED_LIVE
    assert paths.settings_file().exists() and credentials.load_alpaca_keys("live") is not None  # nothing touched


def test_closed_live_trades_do_not_block():
    _live_trade("closed")
    assert maintenance.run(["app", "--check-live"]) == maintenance.OK


def test_keep_data_uninstall_keeps_everything():
    save_settings(AppSettings())
    credentials.save_alpaca_keys("paper", "PK", "S")
    assert maintenance.run(["app", "--uninstall-prepare"]) == maintenance.OK
    assert paths.settings_file().exists() and credentials.load_alpaca_keys("paper") is not None


def test_clean_uninstall_deletes_data_and_all_keys(monkeypatch):
    save_settings(AppSettings())
    Database(paths.database_file("paper"), "paper").close()
    credentials.save_alpaca_keys("paper", "PK", "S")
    credentials.save_anthropic_key("sk-ant-x")
    (paths.data_dir() / "profiles" / "test").mkdir(parents=True)
    credentials.SERVICE = "dontknowwhatimdoing-profile-test"
    credentials.save_alpaca_keys("paper", "PROFILEKEY", "S")
    credentials.SERVICE = "dontknowwhatimdoing"
    disabled = []
    from dkapp.platform_utils import autostart
    monkeypatch.setattr(autostart, "set_enabled", lambda on: (disabled.append(on), (True, "off"))[1])
    assert maintenance.run(["app", "--uninstall-prepare", "--delete-data"]) == maintenance.OK
    assert disabled == [False]
    assert not paths.database_file("paper").exists() and not paths.settings_file().exists()
    assert credentials.load_alpaca_keys("paper") is None and credentials.load_anthropic_key() is None
    credentials.SERVICE = "dontknowwhatimdoing-profile-test"
    assert credentials.load_alpaca_keys("paper") is None
    credentials.SERVICE = "dontknowwhatimdoing"


def test_shutdown_running_when_nothing_runs():
    assert maintenance.run(["app", "--shutdown-running"]) == maintenance.OK

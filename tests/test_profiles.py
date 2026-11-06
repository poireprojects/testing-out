import pytest

from dkapp import credentials, paths, profiles


@pytest.fixture(autouse=True)
def restore_service():
    old = credentials.SERVICE
    yield
    credentials.SERVICE = old
    profiles.active_profile = None


def test_parse_args():
    assert profiles.parse_args(["app"]) == (None, False)
    assert profiles.parse_args(["app", "--profile", "test", "--fresh"]) == ("test", True)
    assert profiles.parse_args(["app", "--profile=Demo-2"]) == ("demo-2", False)
    with pytest.raises(ValueError):
        profiles.parse_args(["app", "--profile", "../evil"])


def test_profile_is_isolated_and_fresh_wipes_only_it(monkeypatch):
    main_root = paths.data_dir()
    credentials.save_alpaca_keys("paper", "MAINKEY", "mainsecret")
    (main_root / "trading_paper.db").write_text("main history")

    root = profiles.activate("test", fresh=False)
    assert paths.data_dir() == root / "data" and root.parent == main_root / "profiles"
    assert credentials.load_alpaca_keys("paper") is None  # separate credential namespace
    credentials.save_alpaca_keys("paper", "TESTKEY", "testsecret")
    (paths.data_dir() / "trading_demo.db").write_text("test history")

    monkeypatch.setenv("DK_HOME", str(main_root.parent))
    credentials.SERVICE = "dontknowwhatimdoing"
    root = profiles.activate("test", fresh=True)
    assert not (paths.data_dir() / "trading_demo.db").exists()
    assert credentials.load_alpaca_keys("paper") is None
    credentials.SERVICE = "dontknowwhatimdoing"
    assert credentials.load_alpaca_keys("paper").key_id == "MAINKEY"  # main copy untouched
    assert (main_root / "trading_paper.db").read_text() == "main history"


def test_factory_reset_erases_settings_history_and_keys():
    from dkapp.config import AppSettings, save_settings
    save_settings(AppSettings())
    (paths.data_dir() / "trading_paper.db").write_text("x")
    (paths.data_dir() / "cache").mkdir()
    credentials.save_alpaca_keys("paper", "K", "S")
    credentials.save_anthropic_key("sk-ant-test")
    assert profiles.factory_reset() == []
    assert not paths.settings_file().exists() and not (paths.data_dir() / "trading_paper.db").exists()
    assert credentials.load_alpaca_keys("paper") is None and credentials.load_anthropic_key() is None

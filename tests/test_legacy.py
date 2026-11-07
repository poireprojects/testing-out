"""Moving an installation from the app's previous name to StockPilot (stockpilot/legacy.py)."""
from __future__ import annotations

from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

from stockpilot import APP_ID, legacy


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """platformdirs and the home folder redirected into tmp_path, a memory keychain, no data-folder override."""
    monkeypatch.delenv("STOCKPILOT_HOME", raising=False)
    for name, sub in (("user_data_dir", "data"), ("user_config_dir", "config"), ("user_log_dir", "logs")):
        monkeypatch.setattr(legacy, name, lambda app_id, appauthor=False, _s=sub: str(tmp_path / _s / app_id))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(legacy, "_app_dirs", lambda: [tmp_path / "Applications", tmp_path / "home" / "Applications"])
    previous = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    calls = []
    import stockpilot.platform_utils.autostart as auto
    monkeypatch.setattr(auto, "set_enabled", lambda on: calls.append(on) or (True, ""))
    yield tmp_path, kr, calls
    keyring.set_keyring(previous)


def old(tmp_path, sub):
    return tmp_path / sub / legacy.PREVIOUS_APP_ID


def new(tmp_path, sub):
    return tmp_path / sub / APP_ID


def test_data_config_and_logs_move_to_stockpilot(home):
    tmp, kr, _ = home
    for sub in ("data", "config", "logs"):
        old(tmp, sub).mkdir(parents=True)
        (old(tmp, sub) / f"{sub}.txt").write_text(sub)
    (old(tmp, "data") / "profiles" / "test").mkdir(parents=True)
    out = legacy.migrate()
    assert len(out["folders"]) == 3
    for sub in ("data", "config", "logs"):
        assert not old(tmp, sub).exists() and (new(tmp, sub) / f"{sub}.txt").read_text() == sub
    assert legacy.migrate() == {}                                   # second start: nothing left to do


def test_existing_stockpilot_data_is_never_overwritten_or_merged(home):
    tmp, kr, _ = home
    old(tmp, "data").mkdir(parents=True)
    (old(tmp, "data") / "old.txt").write_text("old")
    new(tmp, "data").mkdir(parents=True)
    (new(tmp, "data") / "new.txt").write_text("new")
    legacy.migrate()
    assert (old(tmp, "data") / "old.txt").exists() and not (new(tmp, "data") / "old.txt").exists()


def test_an_empty_stockpilot_folder_is_replaced_by_the_previous_data(home):
    tmp, kr, _ = home
    old(tmp, "data").mkdir(parents=True)
    (old(tmp, "data") / "trading_paper.db").write_text("db")
    new(tmp, "data").mkdir(parents=True)
    legacy.migrate()
    assert (new(tmp, "data") / "trading_paper.db").exists() and not old(tmp, "data").exists()


def test_saved_keys_move_and_the_old_entry_goes_only_after_a_verified_copy(home):
    tmp, kr, _ = home
    (old(tmp, "data") / "profiles" / "test").mkdir(parents=True)
    kr.set_password(legacy.PREVIOUS_APP_ID, "alpaca-paper-key-id", "PKMAIN")
    kr.set_password(legacy.PREVIOUS_APP_ID, "anthropic-api-key", "sk-test")
    kr.set_password(f"{legacy.PREVIOUS_APP_ID}-profile-test", "alpaca-paper-secret", "SECRET")
    kr.set_password(legacy.PREVIOUS_APP_ID, "alpaca-live-key-id", "OLDLIVE")
    kr.set_password(APP_ID, "alpaca-live-key-id", "NEWLIVE")       # StockPilot already has a different one
    out = legacy.migrate()
    assert kr.get_password(APP_ID, "alpaca-paper-key-id") == "PKMAIN"
    assert kr.get_password(APP_ID, "anthropic-api-key") == "sk-test"
    assert kr.get_password(f"{APP_ID}-profile-test", "alpaca-paper-secret") == "SECRET"
    assert kr.get_password(legacy.PREVIOUS_APP_ID, "alpaca-paper-key-id") is None
    assert kr.get_password(APP_ID, "alpaca-live-key-id") == "NEWLIVE"                     # never overwritten
    assert kr.get_password(legacy.PREVIOUS_APP_ID, "alpaca-live-key-id") == "OLDLIVE"     # and the old one kept
    assert out["keys"] == 3


def test_launch_at_login_is_replaced_by_stockpilots(home, monkeypatch):
    tmp, kr, calls = home
    monkeypatch.setattr(legacy.sys, "platform", "linux")
    f = tmp / "home" / ".config" / "autostart" / f"{legacy.PREVIOUS_APP_ID}.desktop"
    f.parent.mkdir(parents=True)
    f.write_text("[Desktop Entry]")
    assert legacy.migrate().get("autostart") is True
    assert not f.exists() and calls == [True]


def _bundle(where: Path, name: str, bundle_id: str) -> Path:
    import plistlib
    b = where / f"{name}.app"
    (b / "Contents").mkdir(parents=True)
    with open(b / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleName": name}, f)
    return b


def test_on_macos_the_previous_app_goes_to_the_trash_and_nothing_else_does(home, monkeypatch):
    tmp, kr, _ = home
    monkeypatch.setattr(legacy.sys, "platform", "darwin")
    trashed = []
    monkeypatch.setattr(legacy, "_to_trash", lambda p: trashed.append(p.name) or True)
    prev = _bundle(tmp / "Applications", "Older", legacy.PREVIOUS_BUNDLE_ID)       # found by bundle id, not by name
    _bundle(tmp / "Applications", "StockPilot", "com.stockpilot.app")
    _bundle(tmp / "home" / "Applications", "Something Else", "com.example.other")
    out = legacy.migrate()
    assert trashed == ["Older.app"] and out["previous_app_to_trash"] == [str(prev)]
    monkeypatch.setattr(legacy.sys, "platform", "win32")
    assert legacy._retire_previous_mac_app() == []                                    # macOS only


def test_nothing_moves_while_the_previous_version_is_running(home):
    from PySide6.QtCore import QLockFile
    tmp, kr, _ = home
    old(tmp, "data").mkdir(parents=True)
    lock = QLockFile(str(old(tmp, "data") / "app.lock"))
    assert lock.tryLock(100)
    try:
        assert legacy.previous_copy_running()
        assert legacy.migrate() == {"skipped": "the previous version is still running"}
        assert old(tmp, "data").exists() and not new(tmp, "data").exists()
    finally:
        lock.unlock()
    assert not legacy.previous_copy_running()


def test_a_custom_data_folder_never_triggers_a_migration(home, monkeypatch):
    tmp, kr, _ = home
    old(tmp, "data").mkdir(parents=True)
    monkeypatch.setenv("STOCKPILOT_HOME", str(tmp / "elsewhere"))
    assert legacy.migrate() == {"skipped": "custom data folder"} and old(tmp, "data").exists()


def test_installer_safety_commands_refuse_while_the_previous_version_runs(home, monkeypatch):
    from stockpilot import maintenance
    monkeypatch.setattr(legacy, "previous_copy_running", lambda: True)
    assert maintenance.run(["StockPilot.exe", "--check-live"]) == maintenance.STILL_RUNNING


def test_the_previous_name_appears_only_in_the_migration_code():
    root = Path(__file__).resolve().parents[1] / "stockpilot"
    hits = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".qml")
            and legacy.PREVIOUS_APP_ID in p.read_text(encoding="utf-8")]
    assert hits == ["legacy.py"]

import os
import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MemoryKeyring(KeyringBackend):
    """In-memory stand-in for the OS credential store (tests only)."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DK_HOME", str(tmp_path / "dkhome"))
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield tmp_path

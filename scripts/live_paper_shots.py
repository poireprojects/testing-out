"""Run the real app in PAPER mode against the real Alpaca paper account and screenshot it over time.

    ALPACA_PAPER_KEY=... ALPACA_PAPER_SECRET=... DK_HOME=/tmp/dk python scripts/live_paper_shots.py OUT_DIR \
        --minutes 30 --every 5 --mode ai_confirm

Paper mode only (refuses anything else). Keys are put in an in-memory credential store for this process
(the OS keychain isn't available on a headless test machine). The local AI is reached through Ollama's API
(see scripts/ollama_shim.py when Ollama itself isn't installed). Every page is captured each round.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--minutes", type=float, default=30)
ap.add_argument("--every", type=float, default=5)
ap.add_argument("--mode", default="ai_confirm", choices=["rules", "ai_confirm", "ai_select"])
ap.add_argument("--model", default="llama3.2:3b")
ap.add_argument("--no-autopilot", action="store_true", help="scan and explain, but place no orders")
ap.add_argument("--pages", default="overview,scanner,activity,trades,performance,settings")
ap.add_argument("--first-after", type=float, default=90, help="seconds before the first round")
a = ap.parse_args()
out_dir = Path(a.out)
out_dir.mkdir(parents=True, exist_ok=True)

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def set_password(self, s, u, p):
        self.store[(s, u)] = p

    def get_password(self, s, u):
        return self.store.get((s, u))

    def delete_password(self, s, u):
        self.store.pop((s, u), None)


keyring.set_keyring(MemoryKeyring())

from dkapp import credentials, paths, preload  # noqa: E402

preload.preload()
key, secret = os.environ["ALPACA_PAPER_KEY"], os.environ["ALPACA_PAPER_SECRET"]
if key.upper().startswith("AK"):
    raise SystemExit("refusing: that looks like a LIVE key")
credentials.save_alpaca_keys("paper", key, secret)

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dkapp.config import load_settings, save_settings  # noqa: E402
from dkapp.log import setup_logging  # noqa: E402

setup_logging()
QQuickStyle.setStyle("Basic")
app = QApplication(sys.argv)
from dkapp.ui.app import _load_fonts  # noqa: E402

_load_fonts()
s = load_settings()
s.broker.mode = "paper"
s.first_run_done = True
s.ai.provider = "ollama"
s.ai.ollama_model = a.model
s.ai.decision_mode = a.mode
s.ai.timeout_seconds = 240.0          # a 4-core test box runs a 3B model slowly
s.automation.autopilot = not a.no_autopilot
save_settings(s)
assert s.broker.mode == "paper"

from dkapp.ui.charts import LineChart  # noqa: E402,F401
from dkapp.ui.controller import AppController  # noqa: E402

ctl = AppController(s)
eng = QQmlApplicationEngine()
eng.addImportPath(str(paths.qml_dir()))
ctx = eng.rootContext()
ctx.setContextProperty("app", ctl)
ctx.setContextProperty("initialGeometry", {"x": 0, "y": 0, "width": 1440, "height": 900, "maximized": False, "page": 0})
ctx.setContextProperty("trayAvailable", False)
ctx.setContextProperty("profileName", "")
ctx.setContextProperty("startMinimized", False)
ctx.setContextProperty("iconUrl", QUrl.fromLocalFile(str(paths.assets_dir() / "icons" / "dk.png")))
eng.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
win = eng.rootObjects()[0]
all_pages = ["overview", "scanner", "activity", "trades", "performance", "settings"]
pages = [p for p in all_pages if p in a.pages.split(",")]
start = time.monotonic()
state = {"round": 0, "i": 0}


def round_shots():
    if state["i"] >= len(pages):
        state["i"] = 0
        state["round"] += 1
        if time.monotonic() - start > a.minutes * 60:
            win.close()
            QTimer.singleShot(200, lambda: (ctl.shutdown(), app.quit()))
            return
        QTimer.singleShot(int(a.every * 60 * 1000), round_shots)
        return
    name = pages[state["i"]]
    idx = all_pages.index(name)
    win.setProperty("page", idx)

    def grab():
        ctl._refresh_history()
        f = out_dir / f"r{state['round']:02d}-{idx}-{name}.png"
        win.grabWindow().save(str(f))
        print("saved", f.name, flush=True)
        state["i"] += 1
        round_shots()
    QTimer.singleShot(1200, grab)


QTimer.singleShot(int(a.first_after * 1000), round_shots)  # first round after the engine has scanned for a while
app.exec()

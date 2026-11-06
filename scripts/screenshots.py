"""Render every page of the app offscreen and save PNG screenshots (used to verify the UI)."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "screenshots")
out_dir.mkdir(parents=True, exist_ok=True)
warm = float(os.environ.get("DK_WARMUP", "25"))

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dkapp import paths  # noqa: E402
from dkapp.config import load_settings, save_settings  # noqa: E402
from dkapp.log import setup_logging  # noqa: E402

setup_logging()
QQuickStyle.setStyle("Basic")
app = QApplication(sys.argv)
from dkapp.ui.app import _load_fonts  # noqa: E402
_load_fonts()
s = load_settings()
if os.environ.get("DK_SHOT_RULES"):
    s.ai.decision_mode = "rules"
s.demo.speed = float(os.environ.get("DK_SPEED", "20"))
s.first_run_done = not os.environ.get("DK_SHOT_WELCOME")
save_settings(s)
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
errors = []
eng.warnings.connect(lambda ws: errors.extend(w.toString() for w in ws))
eng.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
if not eng.rootObjects():
    print("QML LOAD FAILED"); print("\n".join(errors)); sys.exit(1)
win = eng.rootObjects()[0]
pages = ["overview", "scanner", "activity", "trades", "performance", "settings"]
only = os.environ.get("DK_PAGES")
order = [i for i, p in enumerate(pages) if not only or p in only.split(",")]
state = {"i": 0}


def shoot():
    if state["i"] >= len(order):
        for e in errors:
            print("QML WARNING:", e)
        win.close()
        eng.deleteLater()
        QTimer.singleShot(100, lambda: (ctl.shutdown(), app.quit()))
        return
    idx = order[state["i"]]
    win.setProperty("page", idx)

    def grab():
        QTimer.singleShot(900, lambda: (win.grabWindow().save(str(out_dir / f"{idx}-{pages[idx]}.png")),
                                        print("saved", pages[idx]), state.__setitem__("i", state["i"] + 1), shoot()))
    QTimer.singleShot(600, grab)


def start():
    ctl._refresh_history()
    if os.environ.get("DK_BACKTEST"):
        ctl.runBacktest(20, 12)
    QTimer.singleShot(1500, shoot)


QTimer.singleShot(int(warm * 1000), start)
app.exec()

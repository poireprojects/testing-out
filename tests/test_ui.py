"""Loads the real QML interface offscreen against a demo engine and checks every page renders."""
import pytest

pytest.importorskip("PySide6.QtQuick")

pytestmark = pytest.mark.ui


def test_qml_loads_all_pages_without_errors(qapp=None):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from dkapp import paths
    from dkapp.config import AppSettings
    from dkapp.ui.app import _load_fonts
    from dkapp.ui.charts import LineChart  # noqa: F401
    from dkapp.ui.controller import AppController

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication([])
    _load_fonts()
    s = AppSettings()
    s.first_run_done = True
    s.ai.decision_mode = "rules"
    ctl = AppController(s)
    eng = QQmlApplicationEngine()
    eng.addImportPath(str(paths.qml_dir()))
    warnings = []
    eng.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    ctx = eng.rootContext()
    ctx.setContextProperty("app", ctl)
    ctx.setContextProperty("initialGeometry", {"x": 0, "y": 0, "width": 1300, "height": 820, "maximized": False, "page": 0})
    ctx.setContextProperty("trayAvailable", False)
    ctx.setContextProperty("startMinimized", False)
    ctx.setContextProperty("iconUrl", QUrl.fromLocalFile(str(paths.assets_dir() / "icons" / "dk.png")))
    eng.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
    assert eng.rootObjects(), "\n".join(warnings)
    win = eng.rootObjects()[0]

    def spin(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    spin(2500)
    for page in range(6):
        win.setProperty("page", page)
        spin(400)
        img = win.grabWindow()
        assert img.width() > 0
    assert ctl.state.get("mode") == "demo"
    win.close()
    eng.deleteLater()
    spin(100)
    ctl.shutdown()
    real = [w for w in warnings if "of null" not in w]
    assert real == [], "\n".join(real)


def test_charts_paint_with_data():
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from dkapp.ui.charts import BarChart, CandleChart, LineChart, Ring, Sparkline

    QApplication.instance() or QApplication([])
    for item, setup in [
        (LineChart(), lambda c: (c.setProperty("values", [1.0, 2.0, 1.5, 3.0]), c.setProperty("labels", ["a", "b", "c", "d"]))),
        (BarChart(), lambda c: c.setProperty("values", [1.0, -2.0, 0.5])),
        (Sparkline(), lambda c: c.setProperty("values", [1.0, 1.2, 0.9])),
        (Ring(), lambda c: c.setProperty("value", 0.4)),
        (CandleChart(), lambda c: c.setProperty("bars", [{"t": "09:30", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "vwap": 1.2, "ema20": 1.1},
                                                       {"t": "09:31", "o": 1.5, "h": 2, "l": 1, "c": 1.2, "v": 20, "vwap": 1.3, "ema20": 1.2}])),
    ]:
        setup(item)
        item.setWidth(300)
        item.setHeight(160)
        img = QImage(300, 160, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        item.paint(p)
        p.end()


def test_model_roles_do_not_shadow_qml_delegate_names():
    from dkapp.ui import controller
    reserved = {"model", "modelData", "index", "parent"}
    for name in dir(controller):
        if name.endswith("_ROLES"):
            assert not reserved & set(getattr(controller, name)), name


def test_app_self_test_entry_point(tmp_path):
    """Runs the real entry point (`python -m dkapp --self-test`): loads every page with a live demo engine."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, DK_HOME=str(tmp_path / "selftest"), QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "dkapp", "--self-test"], env=env, capture_output=True, text=True,
                       timeout=120, cwd=str(tmp_path))
    rp = tmp_path / "selftest" / "logs" / "self-test.txt"
    assert rp.exists(), r.stdout[-2000:] + r.stderr[-3000:]
    report = rp.read_text()
    assert r.returncode == 0, report + r.stdout[-2000:] + r.stderr[-2000:]
    assert report.startswith("OK")

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

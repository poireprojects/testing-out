"""Desktop application entry point."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .. import APP_ID, APP_NAME, __version__, paths
from ..config import load_settings, save_settings
from ..log import setup_logging

log = logging.getLogger(__name__)


def _load_fonts() -> None:
    from PySide6.QtGui import QFontDatabase
    for f in sorted((paths.assets_dir() / "fonts").glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(f)) < 0:
            log.warning("could not load font %s", f.name)


def _app_icon():
    from PySide6.QtGui import QIcon, QPixmap
    from .icon import render_icon
    icon = QIcon()
    png = paths.assets_dir() / "icons" / "dk.png"
    if png.exists():
        icon.addFile(str(png))
    else:
        for s in (16, 32, 64, 128, 256, 512):
            icon.addPixmap(QPixmap.fromImage(render_icon(s)))
    return icon


def _valid_geometry(win) -> dict:
    """Restore the last window position only if it is still on a connected screen."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    g = {"x": win.x, "y": win.y, "width": win.width, "height": win.height, "maximized": win.maximized,
         "page": win.page}
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen().availableGeometry() if QGuiApplication.primaryScreen() else QRect(0, 0, 1440, 900)
    g["width"] = min(max(1000, g["width"]), max(1000, primary.width()))
    g["height"] = min(max(660, g["height"]), max(660, primary.height()))
    ok = False
    if g["x"] is not None and g["y"] is not None:
        r = QRect(g["x"], g["y"], g["width"], g["height"])
        ok = any(s.availableGeometry().intersects(r.adjusted(40, 40, -40, -40)) for s in screens)
    if not ok:
        g["x"] = primary.x() + (primary.width() - g["width"]) // 2
        g["y"] = primary.y() + (primary.height() - g["height"]) // 2
    return g


class _Tray:
    def __init__(self, app, controller, icon):
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.controller = controller
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.show_action = QAction(f"open {APP_NAME}", menu)
        self.autopilot_action = QAction("autopilot", menu)
        self.autopilot_action.setCheckable(True)
        self.stop_action = QAction("emergency stop", menu)
        self.quit_action = QAction("quit", menu)
        menu.addAction(self.show_action)
        menu.addSeparator()
        menu.addAction(self.autopilot_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(self.quit_action)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.autopilot_action.toggled.connect(lambda on: controller.setAutopilot(on))
        self.stop_action.triggered.connect(controller.emergencyStop)
        self.quit_action.triggered.connect(controller.requestQuit)
        controller.settingsChanged.connect(self.sync)
        self.sync()
        if self.available:
            self.tray.show()

    def sync(self):
        on = bool(self.controller.settings.get("automation", {}).get("autopilot"))
        if self.autopilot_action.isChecked() != on:
            self.autopilot_action.blockSignals(True)
            self.autopilot_action.setChecked(on)
            self.autopilot_action.blockSignals(False)

    def notify(self, title: str, message: str, level: str = "info") -> None:
        from PySide6.QtWidgets import QSystemTrayIcon
        if not self.available:
            return
        icon = {"error": QSystemTrayIcon.MessageIcon.Critical, "warning": QSystemTrayIcon.MessageIcon.Warning}.get(
            level, QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage(title, message[:240], icon, 7000)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    self_test = "--self-test" in argv
    if self_test:
        # Packaging smoke test: isolated data folder, offscreen rendering, quit after loading the UI.
        import tempfile
        os.environ.setdefault("DK_HOME", tempfile.mkdtemp(prefix="dk-selftest-"))
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        try:  # own taskbar entry + correct attribution for Windows notifications
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.dontknowwhatimdoing.app")  # type: ignore[attr-defined]
        except Exception:
            pass
    # Single-instance lock always lives in the main data folder, so a test copy and the main
    # app never run (and trade) at the same time.
    lock_dir = paths.data_dir()
    from .. import profiles
    profile, fresh = profiles.parse_args(argv)
    if profile and not self_test:
        profiles.activate(profile, fresh)
    setup_logging()
    log.info("starting %s %s%s", APP_NAME, __version__, f" (test copy '{profile}')" if profile else "")

    from PySide6.QtCore import QCoreApplication, QLockFile, Qt, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QCoreApplication.setOrganizationName(APP_ID)
    QCoreApplication.setApplicationName(APP_ID)
    QQuickStyle.setStyle("Basic")
    app = QApplication(argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    # ---- single instance: a second launch just brings the first window forward
    lock = QLockFile(str(lock_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200) and not self_test:
        if profile:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(None, APP_NAME, "the app is already running. quit it first (tray icon → quit), "
                                                    "then open the fresh test copy again.")
            return 0
        sock = QLocalSocket()
        sock.connectToServer(APP_ID)
        if sock.waitForConnected(1500):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(1000)
        log.info("already running; asked the existing window to show")
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(APP_ID)
    server.listen(APP_ID)

    _load_fonts()
    icon = _app_icon()
    app.setWindowIcon(icon)

    settings = load_settings()
    if self_test:
        settings.first_run_done = True
    from .charts import LineChart  # noqa: F401  (registers DkCharts QML types)
    from .controller import AppController

    tray_holder: dict = {}

    def notifier(title: str, message: str, level: str) -> None:
        t = tray_holder.get("tray")
        if t:
            t.notify(title, message, level)

    controller = AppController(settings, notifier=notifier)
    tray = _Tray(app, controller, icon)
    tray_holder["tray"] = tray

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(paths.qml_dir()))
    ctx = engine.rootContext()
    ctx.setContextProperty("app", controller)
    ctx.setContextProperty("initialGeometry", _valid_geometry(settings.window))
    ctx.setContextProperty("trayAvailable", tray.available)
    ctx.setContextProperty("profileName", profile or "")
    ctx.setContextProperty("iconUrl", QUrl.fromLocalFile(str(paths.assets_dir() / "icons" / "dk.png")))
    ctx.setContextProperty("startMinimized", "--minimized" in argv or settings.automation.start_minimized)
    qml_warnings: list[str] = []
    engine.warnings.connect(lambda ws: qml_warnings.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
    if not engine.rootObjects():
        log.error("QML failed to load: %s", qml_warnings)
        if self_test:
            (paths.log_dir() / "self-test.txt").write_text("FAIL\n" + "\n".join(qml_warnings), encoding="utf-8")
            print("SELF-TEST FAIL", *qml_warnings, sep="\n")
        controller.shutdown()
        return 1
    window = engine.rootObjects()[0]
    if self_test:
        from PySide6.QtCore import QTimer

        def finish():
            ok = bool(controller.state.get("mode")) and window.property("visible") is not None
            for p in range(6):
                window.setProperty("page", p)
            controller.openSetup()
            real = [w for w in qml_warnings if "of null" not in w]
            result = "OK" if ok and not real else "FAIL"
            text = f"{result}\nmode={controller.state.get('mode')}\n" + "\n".join(real)
            (paths.log_dir() / "self-test.txt").write_text(text, encoding="utf-8")
            print("SELF-TEST", text)
            app.exit(0 if result == "OK" else 2)
        QTimer.singleShot(6000, finish)

    def show_window():
        window.show()
        window.raise_()
        window.requestActivate()

    controller.showRequested.connect(show_window)
    tray.show_action.triggered.connect(show_window)

    from PySide6.QtWidgets import QSystemTrayIcon

    def on_tray(reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            show_window()
    tray.tray.activated.connect(on_tray)

    def on_conn():
        c = server.nextPendingConnection()
        if c is not None:
            c.readyRead.connect(lambda: (c.readAll(), show_window()))
    server.newConnection.connect(on_conn)

    def on_state(state):
        if state == Qt.ApplicationState.ApplicationActive and sys.platform == "darwin" and not window.isVisible():
            show_window()  # clicking the Dock icon brings the window back
    app.applicationStateChanged.connect(on_state)

    rc = app.exec()
    engine.deleteLater()  # tear down QML before the objects it binds to
    del engine
    controller.shutdown()
    save_settings(controller._settings)
    lock.unlock()
    return rc

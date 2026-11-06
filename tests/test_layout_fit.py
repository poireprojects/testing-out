"""Nothing may stick out of the sidebar or the title bar (clipping / overflow), at common window sizes and scales."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _truncated(container):
    """Visible text that is cut off ("…") inside the container."""
    bad = []
    stack = list(container.childItems())
    while stack:
        it = stack.pop()
        if not it.isVisible() or it.opacity() == 0:
            continue
        stack.extend(it.childItems())
        if it.property("truncated") is True and (it.property("text") or "").strip():
            bad.append(f"text cut off: {it.property('text')!r:.60} (w={it.width():.0f})")
    return bad


def _overflow(root_item, container, tol=0.5):
    """Visible descendants whose box extends past the container's left/right edge."""
    bad = []
    stack = list(container.childItems())
    while stack:
        it = stack.pop()
        if not it.isVisible() or it.width() <= 0 or it.opacity() == 0:
            continue
        stack.extend(it.childItems())
        p = it.mapToItem(container, 0, 0)
        x, w = p.x(), it.width()
        if x < -tol or x + w > container.width() + tol:
            name = it.objectName() or type(it).__name__
            txt = it.property("text")
            bad.append(f"{name} {txt!r:.40} x={x:.1f} w={w:.1f} (container {container.width():.0f})")
    return bad


@pytest.mark.parametrize("size,scale", [((1060, 680), "1"), ((1366, 768), "1"), ((1440, 900), "1"),
                                        ((1536, 864), "1.25"), ((1280, 720), "1.5")])
def test_sidebar_and_title_bar_contents_fit(qtbot, size, scale, tmp_path, monkeypatch):
    """QT_SCALE_FACTOR is read at startup, so the scaled cases check layout at the logical sizes those scales give."""
    monkeypatch.setenv("DK_HOME", str(tmp_path))
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from dkapp import paths
    from dkapp.config import load_settings
    from dkapp.ui.app import _load_fonts
    from dkapp.ui.charts import LineChart  # noqa: F401
    from dkapp.ui.chrome import WindowChrome
    from dkapp.ui.controller import AppController
    _load_fonts()
    s = load_settings()
    s.first_run_done = True
    ctl = AppController(s)
    eng = QQmlApplicationEngine()
    eng.addImportPath(str(paths.qml_dir()))
    ctx = eng.rootContext()
    chrome = WindowChrome()
    for k, v in {"app": ctl, "chrome": chrome, "trayAvailable": False, "profileName": "", "startMinimized": False,
                 "iconUrl": QUrl.fromLocalFile(str(paths.assets_dir() / "icons" / "dk.png")),
                 "initialGeometry": {"x": 0, "y": 0, "width": size[0], "height": size[1], "maximized": False, "page": 0}}.items():
        ctx.setContextProperty(k, v)
    eng.load(QUrl.fromLocalFile(str(paths.qml_dir() / "Main.qml")))
    win = eng.rootObjects()[0]
    qtbot.wait(400)
    from PySide6.QtQuick import QQuickItem
    sidebar = win.findChild(QQuickItem, "sidebar")
    assert sidebar is not None
    bad = _overflow(win.contentItem(), sidebar) + _truncated(sidebar)
    bar = win.findChild(QQuickItem, "titleBar")
    brand = win.findChild(QQuickItem, "brand")
    assert bar is not None and brand is not None
    bad += _overflow(win.contentItem(), bar) + _truncated(bar)
    b = brand.mapToItem(bar, 0, 0)
    if b.x() + brand.childrenRect().width() > sidebar.width() + 0.5:
        bad.append(f"title-bar brand ({b.x() + brand.childrenRect().width():.0f}px) runs past the sidebar column")
    win.close()
    ctl.shutdown()
    assert not bad, f"layout problems at {size} (scale {scale}):\n" + "\n".join(bad)

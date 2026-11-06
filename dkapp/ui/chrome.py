"""Integrated window frame: the app draws its own title bar but keeps normal desktop behaviour.

Windows (hybrid native/custom): the window is frameless for Qt, the native WS_THICKFRAME / WS_CAPTION /
min/max styles are added back (Aero snap, Win+arrow keys, minimize/maximize animations, taskbar, system
menu, DWM shadow and rounded corners), WM_NCCALCSIZE removes the system caption, and WM_NCHITTEST tells
Windows which part of OUR title bar is what: resize edges, caption (drag, double-click maximize, snap
by dragging), and HTMAXBUTTON over our maximize button — the documented requirement for the Windows 11
Snap Layouts flyout. When maximized, the client area is clipped to the monitor's work area so nothing
spills onto another monitor or under the taskbar.

Linux: frameless + Qt's startSystemMove/startSystemResize. macOS: the native title bar is kept.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Property, QAbstractNativeEventFilter, QObject, QRectF, Qt, QTimer, Signal, Slot

log = logging.getLogger(__name__)

HTCLIENT, HTCAPTION, HTMINBUTTON, HTMAXBUTTON = 1, 2, 8, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT, HTCLOSE = 10, 11, 12, 13, 14, 15, 16, 17, 20
HT_NAMES = {1: "client", 2: "caption", 8: "min", 9: "max", 10: "left", 11: "right", 12: "top", 13: "top-left",
            14: "top-right", 15: "bottom", 16: "bottom-left", 17: "bottom-right", 20: "close"}

RESIZE_BORDER = 6.0   # logical px inside the window edge that resize


def _inside(r, x: float, y: float) -> bool:
    return r is not None and r[2] > 0 and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]


PROGRESS: Optional[Path] = None   # set by the --chrome-selftest run: where it got to, for CI logs


def mark(step: str) -> None:
    if PROGRESS is not None:
        try:
            with open(PROGRESS, "a", encoding="utf-8") as f:
                f.write(step + "\n")
        except OSError:
            pass


def hit_test(x: float, y: float, w: float, h: float, *, title_height: float, max_rect=None, client_rects=(),
             maximized: bool = False, border: float = RESIZE_BORDER) -> int:
    """Which part of the window is at (x, y)? Logical pixels relative to the window's top-left."""
    if not maximized:
        left, right = x < border, x >= w - border
        top, bottom = y < border, y >= h - border
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if bottom:
            return HTBOTTOM
        if top and not _inside(max_rect, x, y):
            return HTTOP
    if _inside(max_rect, x, y):
        return HTMAXBUTTON
    if y < title_height:
        if any(_inside(r, x, y) for r in client_rects):
            return HTCLIENT   # our own buttons/chips in the title bar receive normal mouse events
        return HTCAPTION
    return HTCLIENT


# ====================================================================== Windows native integration
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    WM_NCCALCSIZE, WM_NCHITTEST, WM_NCMOUSEMOVE = 0x0083, 0x0084, 0x00A0
    WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCLBUTTONDBLCLK, WM_NCMOUSELEAVE = 0x00A1, 0x00A2, 0x00A3, 0x02A2
    GWL_STYLE = -16
    WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00020000, 0x00010000, 0x00080000
    SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010  # FRAMECHANGED|NOMOVE|NOSIZE|NOZORDER|NOACTIVATE
    TME_LEAVE, TME_NONCLIENT = 0x02, 0x10

    class NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

    class TRACKMOUSEEVENT(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                    ("dwHoverTime", wintypes.DWORD)]

    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.TrackMouseEvent.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def _signed16(v: int) -> int:
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    def work_area(hwnd: int) -> tuple[int, int, int, int]:
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(mon, ctypes.byref(mi))
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom

    class _WinFilter(QAbstractNativeEventFilter):
        def __init__(self, chrome: "WindowChrome"):
            super().__init__()
            self.chrome = chrome

        def nativeEventFilter(self, event_type, message):
            try:
                return self._filter(event_type, message)
            except Exception:  # never let a Python error break the window's message handling
                if not getattr(self, "_logged", False):
                    self._logged = True
                    log.exception("window frame filter failed; falling back to default handling")
                return False, 0

        def _filter(self, event_type, message):
            c = self.chrome
            if c.hwnd is None or bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if (msg.hWnd or 0) != c.hwnd:
                return False, 0
            m = msg.message
            if m == WM_NCCALCSIZE and msg.wParam:
                if user32.IsZoomed(c.hwnd):
                    p = NCCALCSIZE_PARAMS.from_address(int(msg.lParam))
                    l, t, r, b = work_area(c.hwnd)
                    p.rgrc[0].left, p.rgrc[0].top, p.rgrc[0].right, p.rgrc[0].bottom = l, t, r, b
                return True, 0  # the whole window is client area: no system caption
            if m == WM_NCHITTEST:
                code = c.native_hit(_signed16(msg.lParam), _signed16(msg.lParam >> 16))
                c._set_max_hovered(code == HTMAXBUTTON)
                return True, code
            if m == WM_NCMOUSEMOVE:
                c._set_max_hovered(msg.wParam == HTMAXBUTTON)
                tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE | TME_NONCLIENT, c.hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
                return False, 0
            if m == WM_NCMOUSELEAVE:
                c._set_max_hovered(False)
                c._set_max_pressed(False)
                return False, 0
            if msg.wParam == HTMAXBUTTON and m in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK):
                c._set_max_pressed(True)
                return True, 0
            if msg.wParam == HTMAXBUTTON and m == WM_NCLBUTTONUP:
                if c._max_pressed:
                    c.toggleMaximize()
                c._set_max_pressed(False)
                return True, 0
            return False, 0


class WindowChrome(QObject):
    changed = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._custom = sys.platform != "darwin"
        self._native = sys.platform == "win32"
        self.window = None
        self.hwnd: Optional[int] = None
        self._filter = None
        self.title_height = 52.0
        self.max_rect: Optional[tuple] = None
        self.client_rects: list[tuple] = []
        self._max_hovered = False
        self._max_pressed = False
        self._maximized = False

    # ------------------------------------------------------------ QML API
    @Property(bool, constant=True)
    def custom(self) -> bool:
        return self._custom

    @Property(bool, constant=True)
    def nativeHitTest(self) -> bool:
        return self._native

    @Property(str, constant=True)
    def platform(self) -> str:
        return sys.platform

    @Property(bool, notify=changed)
    def maxHovered(self) -> bool:
        return self._max_hovered

    @Property(bool, notify=changed)
    def maxPressed(self) -> bool:
        return self._max_pressed

    @Property(bool, notify=changed)
    def maximized(self) -> bool:
        return self._maximized

    def _set_max_hovered(self, v: bool) -> None:
        if v != self._max_hovered:
            self._max_hovered = v
            self.changed.emit()

    def _set_max_pressed(self, v: bool) -> None:
        if v != self._max_pressed:
            self._max_pressed = v
            self.changed.emit()

    @Slot(float, "QVariant", "QVariant")
    def setRegions(self, title_height: float, max_rect, client_rects) -> None:
        """Title-bar geometry from QML (logical px, window coordinates)."""
        self.title_height = float(title_height)
        self.max_rect = self._rect(max_rect)
        self.client_rects = [r for r in (self._rect(x) for x in (client_rects or [])) if r]

    @staticmethod
    def _rect(v) -> Optional[tuple]:
        if v is None:
            return None
        if isinstance(v, QRectF):
            return (v.x(), v.y(), v.width(), v.height())
        try:
            if hasattr(v, "toVariant"):
                v = v.toVariant()
            if isinstance(v, dict):
                return (float(v["x"]), float(v["y"]), float(v["width"]), float(v["height"]))
            return tuple(float(x) for x in v)[:4]
        except Exception:
            return None

    @Slot()
    def minimize(self) -> None:
        if self.window is not None:
            self.window.showMinimized()

    @Slot()
    def toggleMaximize(self) -> None:
        if self.window is None:
            return
        if self.window.visibility() == self.window.Visibility.Maximized:
            self.window.showNormal()
        else:
            self.window.showMaximized()

    @Slot()
    def closeWindow(self) -> None:
        if self.window is not None:
            self.window.close()   # goes through onClosing: tray / quit dialog / safe shutdown

    @Slot()
    def startMove(self) -> None:
        if self.window is not None and not self._native:
            self.window.startSystemMove()

    @Slot(int)
    def startResize(self, edges: int) -> None:
        if self.window is not None and not self._native and not self._maximized:
            self.window.startSystemResize(Qt.Edge(edges))

    # ------------------------------------------------------------ setup
    def attach(self, window) -> None:
        self.window = window
        window.visibilityChanged.connect(self._on_visibility)
        window.windowStateChanged.connect(lambda *_: self._on_visibility())
        if not self._native:
            return
        try:
            from PySide6.QtWidgets import QApplication
            mark("attach: winId")
            self.hwnd = int(window.winId())
            mark(f"attach: hwnd={self.hwnd}")
            style = user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
            user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE,
                                     style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
            m = MARGINS(0, 0, 1, 0)   # keeps the DWM shadow
            dwmapi.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(m))
            for attr, val in ((20, 1),            # DWMWA_USE_IMMERSIVE_DARK_MODE
                              (33, 2),            # DWMWA_WINDOW_CORNER_PREFERENCE = round (Windows 11)
                              (34, 0x002B2622)):  # DWMWA_BORDER_COLOR = graphite #22262B (COLORREF is BGR)
                v = ctypes.c_int(val)
                dwmapi.DwmSetWindowAttribute(self.hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
            mark("attach: styles + dwm set")
            self._filter = _WinFilter(self)
            QApplication.instance().installNativeEventFilter(self._filter)
            mark("attach: filter installed")
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
            mark("attach: frame changed")
        except Exception:
            log.exception("native window integration failed; using Qt's frameless behaviour")
            self._native = False

    def _on_visibility(self, *_):
        if self.window is None:
            return
        mx = self.window.visibility() == self.window.Visibility.Maximized
        if mx != self._maximized:
            self._maximized = mx
            self.changed.emit()

    def native_hit(self, sx: int, sy: int) -> int:
        """Screen (physical) coordinates -> hit-test code, using the regions QML reported."""
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        dpr = self.window.devicePixelRatio() or 1.0
        zoomed = bool(user32.IsZoomed(self.hwnd))
        if zoomed:  # client area starts at the work area, not the (oversized) window rect
            l, t, rr, b = work_area(self.hwnd)
            r.left, r.top, r.right, r.bottom = l, t, rr, b
        return hit_test((sx - r.left) / dpr, (sy - r.top) / dpr, (r.right - r.left) / dpr, (r.bottom - r.top) / dpr,
                        title_height=self.title_height, max_rect=self.max_rect, client_rects=self.client_rects,
                        maximized=zoomed)


# ====================================================================== self-test (CI and manual)
def self_test(chrome: WindowChrome, window, out_dir: Path, done) -> None:
    """Checks the frame really behaves like a Windows window, screenshots it, writes results JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res: dict = {"platform": sys.platform, "dpr": window.devicePixelRatio(), "checks": [], "native": chrome.nativeHitTest}
    scale = window.screen().devicePixelRatio() if window.screen() else 1.0
    res["screen"] = {"dpr": scale, "logicalSize": [window.screen().size().width(), window.screen().size().height()]
                     if window.screen() else None}

    def check(name, ok, detail="", required=True):
        res["checks"].append({"name": name, "ok": bool(ok), "detail": detail, "required": required})

    def save():
        res["ok"] = all(c["ok"] for c in res["checks"] if c["required"])
        (out_dir / "chrome-selftest.json").write_text(json.dumps(res, indent=1))

    def shot(name):
        mark(f"selftest: screenshot {name}")
        if sys.platform == "win32":
            # QQuickWindow.grabWindow() waits on the render thread and can deadlock on a CI desktop; a plain
            # copy of the screen also shows what Windows really composes (shadow, rounded corners, border).
            scr = window.screen()
            if scr is None:
                return None
            g = window.frameGeometry()
            p = out_dir / f"{name}-desktop.png"
            scr.grabWindow(0, g.x() - 24, g.y() - 24, g.width() + 48, g.height() + 48).save(str(p))
        else:
            p = out_dir / f"{name}.png"
            window.grabWindow().save(str(p))
        return p.name

    def step1():
        mark("selftest: step1")
        w, h = window.width(), window.height()
        mx = chrome.max_rect
        pts = [("max button -> Snap Layouts target", (mx[0] + mx[2] / 2, mx[1] + mx[3] / 2) if mx else None, HTMAXBUTTON),
               ("empty title bar -> drag/double-click", (w * 0.55, chrome.title_height / 2), HTCAPTION),
               ("left edge -> resize", (2, h / 2), HTLEFT), ("right edge -> resize", (w - 2, h / 2), HTRIGHT),
               ("bottom edge -> resize", (w / 2, h - 2), HTBOTTOM), ("bottom-right corner", (w - 2, h - 2), HTBOTTOMRIGHT),
               ("top-left corner", (2, 2), HTTOPLEFT), ("page content -> client", (w / 2, h / 2), HTCLIENT)]
        for r in chrome.client_rects[:4]:
            pts.append((f"title-bar control at {int(r[0])},{int(r[1])} -> client", (r[0] + r[2] / 2, r[1] + r[3] / 2), HTCLIENT))
        for name, pt, want in pts:
            if pt is None:
                check(name, False, "no maximize button region reported")
                continue
            if chrome.nativeHitTest and chrome.hwnd:
                dpr = window.devicePixelRatio() or 1.0
                r = wintypes.RECT()
                user32.GetWindowRect(chrome.hwnd, ctypes.byref(r))
                sx, sy = int(r.left + pt[0] * dpr), int(r.top + pt[1] * dpr)
                got = int(user32.SendMessageW(chrome.hwnd, WM_NCHITTEST, 0, (sy & 0xFFFF) << 16 | (sx & 0xFFFF)))
            else:
                got = hit_test(pt[0], pt[1], w, h, title_height=chrome.title_height, max_rect=chrome.max_rect,
                               client_rects=chrome.client_rects, maximized=chrome.maximized)
            check(name, got == want, f"got {HT_NAMES.get(got, got)}, want {HT_NAMES.get(want, want)}")
        save()
        res["shots"] = [shot("normal")]
        chrome.toggleMaximize()
        QTimer.singleShot(1500, step2)

    def step2():
        mark("selftest: step2 (maximized)")
        check("maximize from our button", chrome.maximized, f"visibility={window.visibility()}")
        if chrome.nativeHitTest and chrome.hwnd:
            l, t, r_, b = work_area(chrome.hwnd)
            cr = wintypes.RECT()
            user32.GetClientRect(chrome.hwnd, ctypes.byref(cr))
            pt = wintypes.POINT(0, 0)
            user32.ClientToScreen(chrome.hwnd, ctypes.byref(pt))
            got = (pt.x, pt.y, pt.x + cr.right, pt.y + cr.bottom)
            check("maximized client area == monitor work area (no overflow, taskbar visible)",
                  all(abs(a - b_) <= 1 for a, b_ in zip(got, (l, t, r_, b))), f"client {got} vs work area {(l, t, r_, b)}")
            sx, sy = int((l + r_) / 2), int(t + 8)
            got_ht = int(user32.SendMessageW(chrome.hwnd, WM_NCHITTEST, 0, (sy & 0xFFFF) << 16 | (sx & 0xFFFF)))
            check("maximized: no resize border at the top edge", got_ht in (HTCAPTION, HTCLIENT), HT_NAMES.get(got_ht, got_ht))
        save()
        res["shots"].append(shot("maximized"))
        chrome.toggleMaximize()
        QTimer.singleShot(1200, step3)

    def step3():
        check("restore from maximized", not chrome.maximized)
        chrome.minimize()
        QTimer.singleShot(1000, step4)

    def step4():
        check("minimize from our button", window.visibility() == window.Visibility.Minimized
              or (chrome.hwnd and sys.platform == "win32" and bool(user32.IsIconic(chrome.hwnd))),
              f"visibility={window.visibility()}")
        window.showNormal()
        if chrome.nativeHitTest and chrome.hwnd:   # Win+Up via synthetic input (needs an interactive desktop)
            try:
                user32.SetForegroundWindow(chrome.hwnd)
                for vk, up in ((0x5B, 0), (0x26, 0), (0x26, 2), (0x5B, 2)):
                    user32.keybd_event(vk, 0, up, 0)
            except Exception as exc:
                check("Win+Up keyboard snap", False, str(exc), required=False)
        QTimer.singleShot(1500, step5)

    def step5():
        mark("selftest: step5")
        if chrome.nativeHitTest:
            check("Win+Up keyboard snap maximizes (synthetic input; informational)", chrome.maximized,
                  f"visibility={window.visibility()}", required=False)
            if chrome.maximized:
                chrome.toggleMaximize()
        save()
        print("CHROME SELF-TEST", "OK" if res["ok"] else "FAIL", json.dumps(res["checks"]), flush=True)
        done(0 if res["ok"] else 3)

    QTimer.singleShot(2500, step1)

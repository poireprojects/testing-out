"""Integrated title bar: hit-testing decides drag / resize / Snap Layouts / buttons (pure logic, every OS)."""
from stockpilot.ui.chrome import (HTBOTTOM, HTBOTTOMRIGHT, HTCAPTION, HTCLIENT, HTLEFT, HTMAXBUTTON, HTRIGHT, HTTOP,
                             HTTOPLEFT, WindowChrome, hit_test)

W, H, T = 1280.0, 800.0, 50.0
MAX = (1188.0, 0.0, 46.0, 50.0)
CLIENT = [(1142.0, 0.0, 46.0, 50.0), (1234.0, 0.0, 46.0, 50.0)]


def ht(x, y, maximized=False):
    return hit_test(x, y, W, H, title_height=T, max_rect=MAX, client_rects=CLIENT, maximized=maximized)


def test_title_bar_regions():
    assert ht(1210, 25) == HTMAXBUTTON           # Windows 11 Snap Layouts flyout needs this
    assert ht(1160, 25) == HTCLIENT and ht(1255, 25) == HTCLIENT   # minimize / close get normal clicks
    assert ht(640, 25) == HTCAPTION              # drag, double-click maximize, drag-to-snap
    assert ht(640, 400) == HTCLIENT              # the app itself


def test_resize_edges_only_when_not_maximized():
    assert ht(2, 400) == HTLEFT and ht(1278, 400) == HTRIGHT and ht(640, 798) == HTBOTTOM
    assert ht(1278, 798) == HTBOTTOMRIGHT and ht(2, 2) == HTTOPLEFT and ht(640, 2) == HTTOP
    assert ht(2, 400, maximized=True) == HTCLIENT
    assert ht(640, 2, maximized=True) == HTCAPTION   # a maximized window has no top resize border
    assert ht(1210, 2, maximized=True) == HTMAXBUTTON


def test_regions_from_qml_values():
    c = WindowChrome()
    c.setRegions(50.0, [10, 0, 46, 50], [[1, 2, 3, 4], {"x": 5, "y": 6, "width": 7, "height": 8}])
    assert c.max_rect == (10.0, 0.0, 46.0, 50.0) and c.client_rects == [(1.0, 2.0, 3.0, 4.0), (5.0, 6.0, 7.0, 8.0)]

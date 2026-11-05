"""Custom-painted, interactive financial charts for QML (import DkCharts 1.0)."""
from __future__ import annotations

import math
from typing import Any, Optional

from PySide6.QtCore import Property, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtQml import QmlElement
from PySide6.QtQuick import QQuickPaintedItem

QML_IMPORT_NAME = "DkCharts"
QML_IMPORT_MAJOR_VERSION = 1


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fmt(v: float, prefix: str = "", decimals: int = 2, suffix: str = "") -> str:
    sign = "-" if v < 0 and prefix else ""
    body = f"{abs(v) if prefix else v:,.{decimals}f}"
    return f"{sign}{prefix}{body}{suffix}"


class _ChartBase(QQuickPaintedItem):
    textColorChanged = Signal()
    gridColorChanged = Signal()
    fontFamilyChanged = Signal()
    bubbleColorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAntialiasing(True)
        self.setAcceptHoverEvents(True)
        self.setRenderTarget(QQuickPaintedItem.RenderTarget.Image)
        self._text = QColor("#8D949C")
        self._grid = QColor(255, 255, 255, 16)
        self._bubble = QColor("#22262C")
        self._family = "Inter"
        self._hover: Optional[QPointF] = None
        self._reveal = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(750)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._last_len = 0

    def _on_anim(self, v):
        self._reveal = float(v)
        self.update()

    def _maybe_animate(self, n: int) -> None:
        if (self._last_len == 0 and n > 0) or abs(n - self._last_len) > max(10, self._last_len // 2):
            self._anim.stop()
            self._anim.start()
        self._last_len = n

    # common properties
    def _gt(self): return self._text
    def _st(self, c):
        self._text = QColor(c); self.textColorChanged.emit(); self.update()
    textColor = Property(QColor, _gt, _st, notify=textColorChanged)

    def _gg(self): return self._grid
    def _sg(self, c):
        self._grid = QColor(c); self.gridColorChanged.emit(); self.update()
    gridColor = Property(QColor, _gg, _sg, notify=gridColorChanged)

    def _gb(self): return self._bubble
    def _sb(self, c):
        self._bubble = QColor(c); self.bubbleColorChanged.emit(); self.update()
    bubbleColor = Property(QColor, _gb, _sb, notify=bubbleColorChanged)

    def _gf(self): return self._family
    def _sf(self, f):
        self._family = str(f); self.fontFamilyChanged.emit(); self.update()
    fontFamily = Property(str, _gf, _sf, notify=fontFamilyChanged)

    def hoverMoveEvent(self, event):
        self._hover = event.position()
        self.update()

    def hoverLeaveEvent(self, event):
        self._hover = None
        self.update()

    def _font(self, px: float, weight=QFont.Weight.Medium) -> QFont:
        f = QFont(self._family)
        f.setPixelSize(int(px))
        f.setWeight(weight)
        return f

    def _bubble_at(self, p: QPainter, anchor: QPointF, lines: list[tuple[str, QColor]], bounds: QRectF) -> None:
        f1 = self._font(11, QFont.Weight.Medium)
        fm = QFontMetricsF(f1)
        w = max(fm.horizontalAdvance(t) for t, _ in lines) + 20
        h = len(lines) * (fm.height() + 2) + 12
        x = anchor.x() + 12
        if x + w > bounds.right():
            x = anchor.x() - 12 - w
        y = max(bounds.top(), min(anchor.y() - h / 2, bounds.bottom() - h))
        r = QRectF(x, y, w, h)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(self._bubble)
        p.drawRoundedRect(r, 9, 9)
        p.setFont(f1)
        ty = y + 6 + fm.ascent()
        for text, color in lines:
            p.setPen(color)
            p.drawText(QPointF(x + 10, ty), text)
            ty += fm.height() + 2


@QmlElement
class LineChart(_ChartBase):
    valuesChanged = Signal()
    labelsChanged = Signal()
    compareChanged = Signal()
    lineColorChanged = Signal()
    compareColorChanged = Signal()
    baselineChanged = Signal()
    prefixChanged = Signal()
    decimalsChanged = Signal()
    showAxisChanged = Signal()
    compareLabelChanged = Signal()
    seriesLabelChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._compare: list[float] = []
        self._line = QColor("#C8FF3C")
        self._cmp = QColor("#7CC4FF")
        self._baseline: float = float("nan")
        self._prefix = "$"
        self._decimals = 2
        self._axis = True
        self._cmp_label = "benchmark"
        self._series_label = "value"

    def _gv(self): return self._values
    def _sv(self, v):
        self._values = [x for x in (_num(a) for a in (v or [])) if x is not None]
        self._maybe_animate(len(self._values)); self.valuesChanged.emit(); self.update()
    values = Property("QVariantList", _gv, _sv, notify=valuesChanged)

    def _gl(self): return self._labels
    def _sl(self, v):
        self._labels = [str(a) for a in (v or [])]; self.labelsChanged.emit(); self.update()
    labels = Property("QVariantList", _gl, _sl, notify=labelsChanged)

    def _gc(self): return self._compare
    def _sc(self, v):
        self._compare = [x for x in (_num(a) for a in (v or [])) if x is not None]; self.compareChanged.emit(); self.update()
    compareValues = Property("QVariantList", _gc, _sc, notify=compareChanged)

    def _glc(self): return self._line
    def _slc(self, c):
        self._line = QColor(c); self.lineColorChanged.emit(); self.update()
    lineColor = Property(QColor, _glc, _slc, notify=lineColorChanged)

    def _gcc(self): return self._cmp
    def _scc(self, c):
        self._cmp = QColor(c); self.compareColorChanged.emit(); self.update()
    compareColor = Property(QColor, _gcc, _scc, notify=compareColorChanged)

    def _gbl(self): return self._baseline
    def _sbl(self, v):
        self._baseline = float(v) if _num(v) is not None else float("nan"); self.baselineChanged.emit(); self.update()
    baselineValue = Property(float, _gbl, _sbl, notify=baselineChanged)

    def _gp(self): return self._prefix
    def _sp(self, v):
        self._prefix = str(v); self.prefixChanged.emit(); self.update()
    prefix = Property(str, _gp, _sp, notify=prefixChanged)

    def _gd(self): return self._decimals
    def _sd(self, v):
        self._decimals = int(v); self.decimalsChanged.emit(); self.update()
    decimals = Property(int, _gd, _sd, notify=decimalsChanged)

    def _ga(self): return self._axis
    def _sa(self, v):
        self._axis = bool(v); self.showAxisChanged.emit(); self.update()
    showAxis = Property(bool, _ga, _sa, notify=showAxisChanged)

    def _gcl(self): return self._cmp_label
    def _scl(self, v):
        self._cmp_label = str(v); self.compareLabelChanged.emit(); self.update()
    compareLabel = Property(str, _gcl, _scl, notify=compareLabelChanged)

    def _gsl(self): return self._series_label
    def _ssl(self, v):
        self._series_label = str(v); self.seriesLabelChanged.emit(); self.update()
    seriesLabel = Property(str, _gsl, _ssl, notify=seriesLabelChanged)

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        vals = self._values
        if len(vals) < 2 or w < 20 or h < 20:
            return
        allv = vals + self._compare + ([] if math.isnan(self._baseline) else [self._baseline])
        lo, hi = min(allv), max(allv)
        if hi - lo < 1e-9:
            hi, lo = hi + 1, lo - 1
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad
        right_pad = 58 if self._axis else 6
        plot = QRectF(4, 10, w - right_pad - 4, h - (24 if self._axis and self._labels else 14))

        def X(i, n):
            return plot.left() + plot.width() * i / max(1, n - 1)

        def Y(v):
            return plot.bottom() - (v - lo) / (hi - lo) * plot.height()

        if self._axis:
            p.setFont(self._font(10.5))
            for k in range(4):
                v = lo + (hi - lo) * (k + 0.5) / 4
                y = Y(v)
                p.setPen(QPen(self._grid, 1))
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                p.setPen(self._text)
                p.drawText(QPointF(plot.right() + 8, y + 4), _fmt(v, self._prefix, self._decimals))
            if self._labels:
                n = len(self._labels)
                step = max(1, n // 5)
                for i in range(0, n, step):
                    p.drawText(QPointF(X(i, n) - 12, h - 4), self._labels[i])

        if not math.isnan(self._baseline):
            pen = QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            y = Y(self._baseline)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        n = len(vals)
        shown = max(2, int(math.ceil(n * self._reveal)))

        if len(self._compare) >= 2:
            cn = len(self._compare)
            path = QPainterPath(QPointF(X(0, cn), Y(self._compare[0])))
            for i in range(1, max(2, int(math.ceil(cn * self._reveal)))):
                path.lineTo(X(i, cn), Y(self._compare[i]))
            p.setPen(QPen(self._cmp, 1.6, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        line = QPainterPath(QPointF(X(0, n), Y(vals[0])))
        for i in range(1, shown):
            if n <= 80:
                x0, y0 = X(i - 1, n), Y(vals[i - 1])
                x1, y1 = X(i, n), Y(vals[i])
                mx = (x0 + x1) / 2
                line.cubicTo(QPointF(mx, y0), QPointF(mx, y1), QPointF(x1, y1))
            else:
                line.lineTo(X(i, n), Y(vals[i]))
        area = QPainterPath(line)
        area.lineTo(X(shown - 1, n), plot.bottom())
        area.lineTo(X(0, n), plot.bottom())
        area.closeSubpath()
        g = QLinearGradient(0, plot.top(), 0, plot.bottom())
        c1 = QColor(self._line); c1.setAlpha(70)
        c2 = QColor(self._line); c2.setAlpha(0)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(area)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self._line, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(line)

        # last point glow
        lx, ly = X(shown - 1, n), Y(vals[shown - 1])
        glow = QColor(self._line); glow.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(lx, ly), 7, 7)
        p.setBrush(self._line)
        p.drawEllipse(QPointF(lx, ly), 3.2, 3.2)

        if self._hover is not None and plot.contains(self._hover):
            i = int(round((self._hover.x() - plot.left()) / plot.width() * (n - 1)))
            i = max(0, min(n - 1, i))
            x, y = X(i, n), Y(vals[i])
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(QPen(QColor("#0B0C0E"), 2))
            p.setBrush(self._line)
            p.drawEllipse(QPointF(x, y), 5, 5)
            lines = []
            if i < len(self._labels):
                lines.append((self._labels[i], self._text))
            lines.append((f"{self._series_label}  {_fmt(vals[i], self._prefix, self._decimals)}", QColor("#F1F3F2")))
            if len(self._compare) == n:
                lines.append((f"{self._cmp_label}  {_fmt(self._compare[i], self._prefix, self._decimals)}", self._cmp))
            self._bubble_at(p, QPointF(x, y), lines, QRectF(0, 0, w, h))


@QmlElement
class CandleChart(_ChartBase):
    barsChanged = Signal()
    markersChanged = Signal()
    upColorChanged = Signal()
    downColorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: list[dict] = []
        self._markers: list[dict] = []
        self._up = QColor("#C8FF3C")
        self._down = QColor("#FF6B5B")
        self._vwap = QColor("#7CC4FF")
        self._ema = QColor("#FFC857")

    def _gb2(self): return self._bars
    def _sb2(self, v):
        self._bars = [dict(b) for b in (v or [])]; self._maybe_animate(len(self._bars)); self.barsChanged.emit(); self.update()
    bars = Property("QVariantList", _gb2, _sb2, notify=barsChanged)

    def _gm(self): return self._markers
    def _sm(self, v):
        self._markers = [dict(m) for m in (v or [])]; self.markersChanged.emit(); self.update()
    markers = Property("QVariantList", _gm, _sm, notify=markersChanged)

    def _gu(self): return self._up
    def _su(self, c):
        self._up = QColor(c); self.upColorChanged.emit(); self.update()
    upColor = Property(QColor, _gu, _su, notify=upColorChanged)

    def _gdn(self): return self._down
    def _sdn(self, c):
        self._down = QColor(c); self.downColorChanged.emit(); self.update()
    downColor = Property(QColor, _gdn, _sdn, notify=downColorChanged)

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bars = self._bars
        w, h = self.width(), self.height()
        if len(bars) < 2 or w < 40 or h < 40:
            return
        right = 60
        price_r = QRectF(6, 10, w - right - 6, (h - 30) * 0.78)
        vol_r = QRectF(6, price_r.bottom() + 8, price_r.width(), (h - 30) * 0.22 - 8)
        lows = [float(b["l"]) for b in bars]
        highs = [float(b["h"]) for b in bars]
        extra = [float(b.get("vwap") or 0) for b in bars if b.get("vwap")] + \
                [float(b.get("ema20") or 0) for b in bars if b.get("ema20")]
        lo, hi = min(lows + extra), max(highs + extra)
        pad = (hi - lo) * 0.08 or 1
        lo, hi = lo - pad, hi + pad
        vmax = max(float(b["v"]) for b in bars) or 1
        n = len(bars)
        step = price_r.width() / n
        cw = max(1.0, min(9.0, step * 0.62))

        def X(i):
            return price_r.left() + step * (i + 0.5)

        def Y(v):
            return price_r.bottom() - (v - lo) / (hi - lo) * price_r.height()

        p.setFont(self._font(10.5))
        for k in range(5):
            v = lo + (hi - lo) * (k + 0.5) / 5
            y = Y(v)
            p.setPen(QPen(self._grid, 1))
            p.drawLine(QPointF(price_r.left(), y), QPointF(price_r.right(), y))
            p.setPen(self._text)
            p.drawText(QPointF(price_r.right() + 8, y + 4), f"{v:,.2f}")
        tstep = max(1, n // 6)
        for i in range(0, n, tstep):
            p.drawText(QPointF(X(i) - 14, h - 6), str(bars[i].get("t", "")))

        shown = max(2, int(math.ceil(n * self._reveal)))
        for i in range(shown):
            b = bars[i]
            o, c, hh, ll = float(b["o"]), float(b["c"]), float(b["h"]), float(b["l"])
            col = self._up if c >= o else self._down
            x = X(i)
            p.setPen(QPen(col, 1))
            p.drawLine(QPointF(x, Y(hh)), QPointF(x, Y(ll)))
            top, bot = Y(max(o, c)), Y(min(o, c))
            body = QRectF(x - cw / 2, top, cw, max(1.0, bot - top))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(body, 1.2, 1.2)
            vc = QColor(col); vc.setAlpha(90)
            vh = float(b["v"]) / vmax * vol_r.height()
            p.setBrush(vc)
            p.drawRect(QRectF(x - cw / 2, vol_r.bottom() - vh, cw, vh))

        for key, col in (("vwap", self._vwap), ("ema20", self._ema)):
            path = None
            for i in range(shown):
                v = _num(bars[i].get(key))
                if v is None:
                    continue
                if path is None:
                    path = QPainterPath(QPointF(X(i), Y(v)))
                else:
                    path.lineTo(X(i), Y(v))
            if path is not None:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(col, 1.6 if key == "vwap" else 1.2))
                p.drawPath(path)

        # trade markers
        index_by_t = {str(b.get("t")): i for i, b in enumerate(bars)}
        for m in self._markers:
            i = index_by_t.get(str(m.get("t")))
            price = _num(m.get("price"))
            if i is None or price is None:
                continue
            x, y = X(i), Y(price)
            buy = m.get("side") == "buy"
            col = self._up if buy else self._down
            tri = QPainterPath()
            if buy:
                tri.moveTo(x, y + 6); tri.lineTo(x - 6, y + 15); tri.lineTo(x + 6, y + 15)
            else:
                tri.moveTo(x, y - 6); tri.lineTo(x - 6, y - 15); tri.lineTo(x + 6, y - 15)
            tri.closeSubpath()
            p.setPen(QPen(QColor("#0B0C0E"), 1.5))
            p.setBrush(col)
            p.drawPath(tri)

        # legend
        p.setFont(self._font(10.5))
        lx = price_r.left() + 6
        for label, col in (("VWAP", self._vwap), ("20-min avg", self._ema)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(lx, price_r.top() + 2, 10, 3), 1.5, 1.5)
            p.setPen(self._text)
            p.drawText(QPointF(lx + 15, price_r.top() + 7), label)
            lx += 15 + QFontMetricsF(self._font(10.5)).horizontalAdvance(label) + 14

        if self._hover is not None and price_r.adjusted(0, 0, 0, vol_r.height() + 8).contains(self._hover):
            i = max(0, min(n - 1, int((self._hover.x() - price_r.left()) / step)))
            b = bars[i]
            x = X(i)
            p.setPen(QPen(QColor(255, 255, 255, 45), 1))
            p.drawLine(QPointF(x, price_r.top()), QPointF(x, vol_r.bottom()))
            hy = self._hover.y()
            if price_r.contains(QPointF(x, hy)):
                p.drawLine(QPointF(price_r.left(), hy), QPointF(price_r.right(), hy))
            o, c = float(b["o"]), float(b["c"])
            chg = (c / o - 1) * 100 if o else 0
            lines = [(str(b.get("t", "")), self._text),
                     (f"open {o:,.2f}   close {c:,.2f}", QColor("#F1F3F2")),
                     (f"high {float(b['h']):,.2f}   low {float(b['l']):,.2f}", QColor("#F1F3F2")),
                     (f"{chg:+.2f}%   vol {float(b['v']):,.0f}", self._up if chg >= 0 else self._down)]
            if _num(b.get("vwap")) is not None:
                lines.append((f"vwap {float(b['vwap']):,.2f}", self._vwap))
            self._bubble_at(p, QPointF(x, Y(c)), lines, QRectF(0, 0, w, h))


@QmlElement
class BarChart(_ChartBase):
    valuesChanged = Signal()
    labelsChanged = Signal()
    posColorChanged = Signal()
    negColorChanged = Signal()
    prefixChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._pos = QColor("#C8FF3C")
        self._neg = QColor("#FF6B5B")
        self._prefix = "$"

    def _gv(self): return self._values
    def _sv(self, v):
        self._values = [(_num(a) or 0.0) for a in (v or [])]; self._maybe_animate(len(self._values)); self.valuesChanged.emit(); self.update()
    values = Property("QVariantList", _gv, _sv, notify=valuesChanged)

    def _gl(self): return self._labels
    def _sl(self, v):
        self._labels = [str(a) for a in (v or [])]; self.labelsChanged.emit(); self.update()
    labels = Property("QVariantList", _gl, _sl, notify=labelsChanged)

    def _gp(self): return self._pos
    def _sp(self, c):
        self._pos = QColor(c); self.posColorChanged.emit(); self.update()
    posColor = Property(QColor, _gp, _sp, notify=posColorChanged)

    def _gn(self): return self._neg
    def _sn(self, c):
        self._neg = QColor(c); self.negColorChanged.emit(); self.update()
    negColor = Property(QColor, _gn, _sn, notify=negColorChanged)

    def _gpf(self): return self._prefix
    def _spf(self, v):
        self._prefix = str(v); self.prefixChanged.emit(); self.update()
    prefix = Property(str, _gpf, _spf, notify=prefixChanged)

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        vals = self._values
        w, h = self.width(), self.height()
        if not vals or w < 20:
            return
        plot = QRectF(4, 10, w - 62, h - 30)
        m = max(abs(v) for v in vals) or 1
        lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
        if hi - lo < 1e-9:
            hi = lo + 1
        span = hi - lo

        def Y(v):
            return plot.bottom() - (v - lo) / span * plot.height()

        zero = Y(0)
        p.setFont(self._font(10.5))
        for v in (hi, 0.0, lo):
            y = Y(v)
            p.setPen(QPen(self._grid if v else QColor(255, 255, 255, 40), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(self._text)
            p.drawText(QPointF(plot.right() + 8, y + 4), _fmt(v, self._prefix, 2))
        n = len(vals)
        step = plot.width() / n
        bw = max(2.0, min(26.0, step * 0.6))
        hover_i = None
        if self._hover is not None and plot.adjusted(0, -10, 0, 10).contains(self._hover):
            hover_i = max(0, min(n - 1, int((self._hover.x() - plot.left()) / step)))
        for i, v in enumerate(vals):
            x = plot.left() + step * (i + 0.5)
            hv = (Y(v) - zero) * self._reveal
            r = QRectF(x - bw / 2, min(zero, zero + hv), bw, abs(hv) or 1)
            col = QColor(self._pos if v >= 0 else self._neg)
            if hover_i is not None and hover_i != i:
                col.setAlpha(110)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, min(4, bw / 3), min(4, bw / 3))
        if self._labels:
            p.setPen(self._text)
            st = max(1, n // 6)
            for i in range(0, n, st):
                x = plot.left() + step * (i + 0.5)
                p.drawText(QPointF(x - 16, h - 6), self._labels[i][5:] if len(self._labels[i]) == 10 else self._labels[i])
        if hover_i is not None:
            x = plot.left() + step * (hover_i + 0.5)
            v = vals[hover_i]
            lines = []
            if hover_i < len(self._labels):
                lines.append((self._labels[hover_i], self._text))
            lines.append((_fmt(v, self._prefix, 2), self._pos if v >= 0 else self._neg))
            self._bubble_at(p, QPointF(x, Y(v)), lines, QRectF(0, 0, w, h))


@QmlElement
class Sparkline(_ChartBase):
    valuesChanged = Signal()
    colorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = []
        self._color = QColor(0, 0, 0, 0)
        self.setAcceptHoverEvents(False)

    def _gv(self): return self._values
    def _sv(self, v):
        self._values = [x for x in (_num(a) for a in (v or [])) if x is not None]; self.valuesChanged.emit(); self.update()
    values = Property("QVariantList", _gv, _sv, notify=valuesChanged)

    def _gc(self): return self._color
    def _sc(self, c):
        self._color = QColor(c); self.colorChanged.emit(); self.update()
    color = Property(QColor, _gc, _sc, notify=colorChanged)

    def paint(self, p: QPainter) -> None:
        v = self._values
        if len(v) < 2:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        lo, hi = min(v), max(v)
        span = (hi - lo) or 1
        col = self._color if self._color.alpha() > 0 else (QColor("#C8FF3C") if v[-1] >= v[0] else QColor("#FF6B5B"))
        path = QPainterPath(QPointF(1, h - 2 - (v[0] - lo) / span * (h - 4)))
        for i in range(1, len(v)):
            path.lineTo(1 + (w - 2) * i / (len(v) - 1), h - 2 - (v[i] - lo) / span * (h - 4))
        area = QPainterPath(path)
        area.lineTo(w - 1, h)
        area.lineTo(1, h)
        g = QLinearGradient(0, 0, 0, h)
        c1 = QColor(col); c1.setAlpha(55)
        c2 = QColor(col); c2.setAlpha(0)
        g.setColorAt(0, c1); g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(area)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)


@QmlElement
class Ring(_ChartBase):
    valueChanged = Signal()
    colorChanged = Signal()
    trackColorChanged = Signal()
    thicknessChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._color = QColor("#C8FF3C")
        self._track = QColor(255, 255, 255, 18)
        self._thick = 6.0
        self.setAcceptHoverEvents(False)

    def _gv(self): return self._value
    def _sv(self, v):
        self._value = max(0.0, min(1.0, _num(v) or 0.0)); self.valueChanged.emit(); self.update()
    value = Property(float, _gv, _sv, notify=valueChanged)

    def _gc(self): return self._color
    def _sc(self, c):
        self._color = QColor(c); self.colorChanged.emit(); self.update()
    color = Property(QColor, _gc, _sc, notify=colorChanged)

    def _gt2(self): return self._track
    def _st2(self, c):
        self._track = QColor(c); self.trackColorChanged.emit(); self.update()
    trackColor = Property(QColor, _gt2, _st2, notify=trackColorChanged)

    def _gk(self): return self._thick
    def _sk(self, v):
        self._thick = float(v); self.thicknessChanged.emit(); self.update()
    thickness = Property(float, _gk, _sk, notify=thicknessChanged)

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        s = min(self.width(), self.height())
        r = QRectF((self.width() - s) / 2 + self._thick / 2, (self.height() - s) / 2 + self._thick / 2,
                   s - self._thick, s - self._thick)
        p.setPen(QPen(self._track, self._thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 0, 360 * 16)
        if self._value > 0:
            p.setPen(QPen(self._color, self._thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * self._value))


@QmlElement
class SvgIcon(QQuickPaintedItem):
    """Stroked line icon drawn through QSvgRenderer: crisp at any display scale and properly clipped."""
    pathDataChanged = Signal()
    colorChanged = Signal()
    strokeWidthChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAntialiasing(True)
        self._path = ""
        self._color = QColor("#F1F3F0")
        self._stroke = 1.8
        self._renderer = None

    def _rebuild(self):
        from PySide6.QtCore import QByteArray
        from PySide6.QtSvg import QSvgRenderer
        if not self._path:
            self._renderer = None
        else:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{self._path}" fill="none" '
                   f'stroke="{self._color.name()}" stroke-opacity="{self._color.alphaF():.3f}" '
                   f'stroke-width="{self._stroke:.3f}" stroke-linecap="round" stroke-linejoin="round"/></svg>')
            self._renderer = QSvgRenderer(QByteArray(svg.encode()))
        self.update()

    def _gp(self): return self._path
    def _sp(self, v):
        self._path = str(v or ""); self.pathDataChanged.emit(); self._rebuild()
    pathData = Property(str, _gp, _sp, notify=pathDataChanged)

    def _gc(self): return self._color
    def _sc(self, c):
        self._color = QColor(c); self.colorChanged.emit(); self._rebuild()
    color = Property(QColor, _gc, _sc, notify=colorChanged)

    def _gs(self): return self._stroke
    def _ss(self, v):
        self._stroke = float(v); self.strokeWidthChanged.emit(); self._rebuild()
    strokeWidth = Property(float, _gs, _ss, notify=strokeWidthChanged)

    def paint(self, p: QPainter) -> None:
        if self._renderer is not None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._renderer.render(p, QRectF(0, 0, self.width(), self.height()))

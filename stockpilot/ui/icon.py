"""The StockPilot icon: an electric-lime flight path that climbs like a price chart and ends in a heading arrow,
taking off from a small off-white dot, on the deep graphite squircle (same palette as the rest of the app)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient

LIME = QColor("#C8FF3C")
INK = QColor("#F3F5F2")
# the flight path, in fractions of the squircle (x right, y down): a dip, then a climb to the heading arrow
PATH = ((0.22, 0.67), (0.42, 0.49), (0.55, 0.59), (0.74, 0.36))


def render_icon(size: int = 1024, family: str = "Space Grotesk", tray: bool = False) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = float(size)
    inset = 0.0 if tray else s * 0.06  # macOS-style margin for app icons
    r = QRectF(inset, inset, s - 2 * inset, s - 2 * inset)
    squircle = QPainterPath()
    squircle.addRoundedRect(r, r.width() * 0.225, r.height() * 0.225)
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0.0, QColor("#24282E"))
    g.setColorAt(1.0, QColor("#0E1012"))
    p.fillPath(squircle, g)
    glow = QRadialGradient(QPointF(r.left() + r.width() * 0.70, r.top() + r.height() * 0.38), r.width() * 0.55)
    c = QColor(LIME)
    c.setAlpha(46)
    glow.setColorAt(0.0, c)
    glow.setColorAt(1.0, QColor(200, 255, 60, 0))
    p.fillPath(squircle, glow)
    p.setPen(QPen(QColor(255, 255, 255, 26), max(1.0, s * 0.006)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(squircle)

    def pt(fx: float, fy: float) -> QPointF:
        return QPointF(r.left() + fx * r.width(), r.top() + fy * r.height())

    w = r.width()
    # flight path (stops short of the tip so the arrowhead sits cleanly on it)
    pts = [pt(x, y) for x, y in PATH]
    tip, prev = pts[-1], pts[-2]
    ang = math.atan2(tip.y() - prev.y(), tip.x() - prev.x())
    head = w * 0.22                                   # arrowhead length
    base_c = QPointF(tip.x() - math.cos(ang) * head * 0.55, tip.y() - math.sin(ang) * head * 0.55)
    line = QPainterPath(pts[0])
    for q in pts[1:-1]:
        line.lineTo(q)
    line.lineTo(base_c)
    stroke = max(2.0, w * (0.085 if size >= 64 else 0.11))
    p.setPen(QPen(LIME, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(line)

    # heading arrow: a solid chevron pointing along the last leg, a little beyond it
    far = QPointF(tip.x() + math.cos(ang) * head * 0.45, tip.y() + math.sin(ang) * head * 0.45)
    back = QPointF(tip.x() - math.cos(ang) * head * 0.55, tip.y() - math.sin(ang) * head * 0.55)
    nx, ny = -math.sin(ang), math.cos(ang)
    half = head * 0.50
    notch = QPointF(back.x() + math.cos(ang) * head * 0.22, back.y() + math.sin(ang) * head * 0.22)
    arrow = QPolygonF([far, QPointF(back.x() + nx * half, back.y() + ny * half), notch,
                       QPointF(back.x() - nx * half, back.y() - ny * half)])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(LIME)
    p.drawPolygon(arrow)

    # take-off point: a small off-white dot where the flight path starts
    p.setBrush(INK)
    p.drawEllipse(pts[0], stroke * 0.50, stroke * 0.50)
    p.end()
    return img

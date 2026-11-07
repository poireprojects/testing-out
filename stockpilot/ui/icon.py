"""The StockPilot icon: an electric-lime flight path that climbs like a price chart and ends in a heading arrow,
taking off from a small off-white dot, on the deep graphite squircle (same palette as the rest of the app)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient

LIME = QColor("#C8FF3C")
INK = QColor("#F3F5F2")
# the flight path, in fractions of the squircle (x right, y down): a climb, a dip, then a climb into the arrowhead.
# Every leg is at 45 degrees so the zigzag reads evenly at every size.
PATH = ((0.21, 0.69), (0.41, 0.49), (0.53, 0.61), (0.79, 0.35))


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
    pts = [pt(x, y) for x, y in PATH]
    tip, prev = pts[-1], pts[-2]
    ang = math.atan2(tip.y() - prev.y(), tip.x() - prev.x())
    ux, uy = math.cos(ang), math.sin(ang)          # along the last leg
    nx, ny = -uy, ux                               # across it
    small = size < 64
    stroke = max(2.0, w * (0.11 if small else 0.085))
    head = w * (0.24 if small else 0.21)           # arrowhead length, tip to base
    half = head * 0.62                             # half the base width
    back = QPointF(tip.x() - ux * head, tip.y() - uy * head)

    # the line ends inside the arrowhead (flat end, hidden), so there is no seam or bump where they meet
    end = QPointF(back.x() + ux * head * 0.4, back.y() + uy * head * 0.4)
    line = QPainterPath(pts[0])
    for q in pts[1:-1]:
        line.lineTo(q)
    line.lineTo(end)
    p.setPen(QPen(LIME, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(line)

    # arrowhead: a solid triangle centred on the last leg, its corners softly rounded by a thin stroke of the same lime
    rr = stroke * 0.22
    tri = QPolygonF([QPointF(tip.x() - ux * rr, tip.y() - uy * rr),
                     QPointF(back.x() + nx * (half - rr), back.y() + ny * (half - rr)),
                     QPointF(back.x() - nx * (half - rr), back.y() - ny * (half - rr))])
    p.setPen(QPen(LIME, rr * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(LIME)
    p.drawPolygon(tri)

    # take-off point: a small off-white dot where the flight path starts
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    p.drawEllipse(pts[0], stroke * 0.50, stroke * 0.50)
    p.end()
    return img

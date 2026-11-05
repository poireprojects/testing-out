"""The app icon: lowercase "dk" with an electric-lime dot on a deep graphite squircle."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

LIME = QColor("#C8FF3C")


def render_icon(size: int = 1024, family: str = "Space Grotesk", tray: bool = False) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    s = float(size)
    inset = 0.0 if tray else s * 0.06  # macOS-style margin for app icons
    r = QRectF(inset, inset, s - 2 * inset, s - 2 * inset)
    path = QPainterPath()
    path.addRoundedRect(r, r.width() * 0.225, r.height() * 0.225)
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0.0, QColor("#24282E"))
    g.setColorAt(1.0, QColor("#0E1012"))
    p.fillPath(path, g)
    glow = QRadialGradient(QPointF(r.right() - r.width() * 0.22, r.bottom() - r.height() * 0.26), r.width() * 0.55)
    c = QColor(LIME)
    c.setAlpha(46)
    glow.setColorAt(0.0, c)
    glow.setColorAt(1.0, QColor(200, 255, 60, 0))
    p.fillPath(path, glow)
    p.setPen(QPen(QColor(255, 255, 255, 26), max(1.0, s * 0.006)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)

    f = QFont(family)
    f.setPixelSize(int(r.height() * 0.50))
    f.setWeight(QFont.Weight.Bold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 94)
    fm = QFontMetricsF(f)
    text = "dk"
    tw = fm.horizontalAdvance(text)
    dot_r = r.width() * 0.062
    total = tw + dot_r * 2.6
    x = r.center().x() - total / 2
    base = r.center().y() + fm.capHeight() * 0.5 + r.height() * 0.02
    p.setFont(f)
    p.setPen(QColor("#F3F5F2"))
    p.drawText(QPointF(x, base), text)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(LIME)
    p.drawEllipse(QPointF(x + tw + dot_r * 1.45, base - dot_r), dot_r, dot_r)
    p.end()
    return img

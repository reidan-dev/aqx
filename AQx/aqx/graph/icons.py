from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

PLAY_COLOR = QColor("#2fae60")
STOP_COLOR = QColor("#d1493f")
PAUSE_COLOR = QColor("#d1a13f")
INACTIVE_COLOR = QColor("#5a5f68")


def triangle_icon(color: QColor, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    path = QPainterPath()
    path.moveTo(size * 0.28, size * 0.15)
    path.lineTo(size * 0.28, size * 0.85)
    path.lineTo(size * 0.85, size * 0.5)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def square_icon(color: QColor, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    margin = size * 0.22
    painter.drawRoundedRect(QRectF(margin, margin, size - 2 * margin, size - 2 * margin), 2, 2)
    painter.end()
    return QIcon(pixmap)


def eye_icon(color: QColor, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, size * 0.09)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    eye_rect = QRectF(size * 0.08, size * 0.3, size * 0.84, size * 0.4)
    painter.drawArc(eye_rect, 0, 180 * 16)
    painter.drawArc(eye_rect, 180 * 16, 180 * 16)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    pupil_r = size * 0.11
    painter.drawEllipse(QRectF(size / 2 - pupil_r, size / 2 - pupil_r, pupil_r * 2, pupil_r * 2))
    painter.end()
    return QIcon(pixmap)


def pause_bars_icon(color: QColor, size: int = 20) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(color))
    painter.setPen(Qt.NoPen)
    bar_w = size * 0.22
    painter.drawRoundedRect(QRectF(size * 0.2, size * 0.15, bar_w, size * 0.7), 1, 1)
    painter.drawRoundedRect(QRectF(size * 0.58, size * 0.15, bar_w, size * 0.7), 1, 1)
    painter.end()
    return QIcon(pixmap)

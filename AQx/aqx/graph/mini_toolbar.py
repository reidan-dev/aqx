from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from ..macos_window_fix import keep_panel_visible_across_app_switches
from .icons import INACTIVE_COLOR, PAUSE_COLOR, PLAY_COLOR, STOP_COLOR, pause_bars_icon, square_icon, triangle_icon


class MiniRunToolbar(QWidget):
    """A small, draggable, always-on-top control strip that appears only when the
    main AQx window is minimized while a flow is running, so the user still has
    Play/Pause/Stop/Maximize without having to restore the window first."""

    play_pause_clicked = Signal()
    stop_clicked = Signal()
    maximize_clicked = Signal()
    settings_clicked = Signal()

    def __init__(self):
        super().__init__()
        # Same Qt.Tool + hidesOnDeactivate fix as the OCR region-picker toolbar - a
        # plain window's clicks aren't reliably delivered while the app isn't
        # frontmost, which is exactly the situation this toolbar exists for.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet(
            "QWidget { background: rgb(28,30,34); border-radius: 8px; }"
            "QPushButton { border-radius: 4px; border: none; background: #3a3d44; padding: 6px; }"
            "QPushButton:hover { background: #4a4e56; }"
        )
        self._drag_offset: Optional[QPoint] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._play_icon = triangle_icon(PLAY_COLOR)
        self._pause_icon = pause_bars_icon(PAUSE_COLOR)
        self._stop_icon = square_icon(STOP_COLOR)

        self.play_pause_btn = QPushButton()
        self.play_pause_btn.setIcon(self._pause_icon)
        self.play_pause_btn.setToolTip("Pause")
        self.play_pause_btn.clicked.connect(self.play_pause_clicked.emit)
        layout.addWidget(self.play_pause_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(self._stop_icon)
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.stop_btn)

        self.maximize_btn = QPushButton("⤢")
        self.maximize_btn.setToolTip("Restore window")
        self.maximize_btn.clicked.connect(self.maximize_clicked.emit)
        layout.addWidget(self.maximize_btn)

        # So the hotkey (and other preferences) is reachable without restoring the
        # main window first - this toolbar's whole reason to exist is letting the
        # user stay out of AQx while a flow runs.
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Preferences")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.settings_btn)

        self.adjustSize()

    def sync_state(self, running: bool, paused: bool) -> None:
        if not running:
            self.hide()
            return
        if paused:
            self.play_pause_btn.setIcon(self._play_icon)
            self.play_pause_btn.setToolTip("Resume")
        else:
            self.play_pause_btn.setIcon(self._pause_icon)
            self.play_pause_btn.setToolTip("Pause")

    def show_near_top_right(self) -> None:
        self.adjustSize()
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self.move(geo.right() - self.width() - 24, geo.top() + 24)
        self.show()
        keep_panel_visible_across_app_switches(self)

    def showEvent(self, event):
        super().showEvent(event)
        keep_panel_visible_across_app_switches(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..macos_window_fix import keep_panel_visible_across_app_switches
from .icons import INACTIVE_COLOR, PAUSE_COLOR, PLAY_COLOR, STOP_COLOR, pause_bars_icon, square_icon, triangle_icon


class _PanelComboBox(QComboBox):
    """A QComboBox whose dropdown list is patched the same way the toolbar itself
    is - without this, opening/selecting from the dropdown activates AQx as a side
    effect (the popup list is its own fresh native window each time, so the
    toolbar's own showEvent-time fix never reaches it), which then hides this very
    toolbar (see _should_show_mini_toolbar) and brings the main window forward
    instead of letting you pick a value."""

    def showPopup(self) -> None:
        super().showPopup()
        popup = self.view().window()
        if popup is not None:
            keep_panel_visible_across_app_switches(popup)


class MiniRunToolbar(QWidget):
    """A small, draggable, always-on-top control strip that appears only when the
    main AQx window is minimized while a flow is running, so the user still has
    Play/Pause/Stop/Maximize without having to restore the window first. Also shows
    one dropdown per Controls block value, so those can be changed mid-run too."""

    play_pause_clicked = Signal()
    stop_clicked = Signal()
    maximize_clicked = Signal()
    settings_clicked = Signal()
    control_value_changed = Signal(str, str)  # name, new value

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
            "QComboBox { border-radius: 4px; border: none; background: #3a3d44; padding: 3px 6px; color: #e6e6e6; }"
            "QLabel { color: #9aa4b2; }"
        )
        self._drag_offset: Optional[QPoint] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        outer.addLayout(button_row)

        self._controls_container = QWidget()
        self._controls_layout = QVBoxLayout(self._controls_container)
        self._controls_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_layout.setSpacing(4)
        outer.addWidget(self._controls_container)
        self._controls_container.setVisible(False)

        self._play_icon = triangle_icon(PLAY_COLOR)
        self._pause_icon = pause_bars_icon(PAUSE_COLOR)
        self._stop_icon = square_icon(STOP_COLOR)

        self.play_pause_btn = QPushButton()
        self.play_pause_btn.setIcon(self._pause_icon)
        self.play_pause_btn.setToolTip("Pause")
        self.play_pause_btn.clicked.connect(self.play_pause_clicked.emit)
        button_row.addWidget(self.play_pause_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(self._stop_icon)
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        button_row.addWidget(self.stop_btn)

        self.maximize_btn = QPushButton("⤢")
        self.maximize_btn.setToolTip("Restore window")
        self.maximize_btn.clicked.connect(self.maximize_clicked.emit)
        button_row.addWidget(self.maximize_btn)

        # So the hotkey (and other preferences) is reachable without restoring the
        # main window first - this toolbar's whole reason to exist is letting the
        # user stay out of AQx while a flow runs.
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Preferences")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        button_row.addWidget(self.settings_btn)

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

    def set_controls(self, controls: List[dict]) -> None:
        """Rebuilds the dropdown rows from GraphRunner.controls_registered's payload
        - a list of {"name", "options", "current"}. Called with [] to clear them
        (e.g. once a run finishes)."""
        while self._controls_layout.count():
            item = self._controls_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for entry in controls:
            name = entry["name"]
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            label = QLabel(name)
            row_layout.addWidget(label)

            combo = _PanelComboBox()
            combo.addItems(entry["options"])
            idx = combo.findText(entry["current"])
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(
                lambda value, name=name: self.control_value_changed.emit(name, value)
            )
            row_layout.addWidget(combo, stretch=1)

            self._controls_layout.addWidget(row)

        self._controls_container.setVisible(bool(controls))
        self.adjustSize()

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

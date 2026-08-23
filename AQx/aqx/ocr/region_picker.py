from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget
from pynput import mouse

from ..macos_window_fix import keep_panel_visible_across_app_switches

BORDER = 3
MIN_SIZE = 4


class _DrawFrame(QWidget):
    """Purely visual: a hollow rectangle that tracks/shows a selection. Unlike a
    normal widget, it never receives the drag itself - the actual gesture is driven
    by a global mouse listener (see RegionPicker) so the very first click can start
    anywhere on screen, including directly over another app's window, not just
    within this widget's own bounds (which don't exist yet at that point)."""

    def __init__(self):
        super().__init__()
        # No Qt.Tool (would hide on app-switch) and no WA_TranslucentBackground
        # (doesn't reliably composite here) - same reasoning as the other overlays.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # purely visual, never intercepts clicks
        self.hide()

    def show_rect(self, rect: QRect) -> None:
        self.setGeometry(rect)
        w, h = rect.width(), rect.height()
        outer = QRegion(0, 0, w, h)
        inner = QRegion(BORDER, BORDER, max(w - 2 * BORDER, 0), max(h - 2 * BORDER, 0))
        self.setMask(outer.subtracted(inner))
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#4fa3ff"), BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(BORDER // 2, BORDER // 2, -BORDER // 2, -BORDER // 2))


class _MouseBridge(QObject):
    """pynput's mouse callbacks fire from its own listener thread; this hands them
    back to the Qt main thread via queued signals."""

    pressed = Signal(int, int)
    moved = Signal(int, int)
    released = Signal(int, int)


class _Toolbar(QWidget):
    """A small, draggable, out-of-the-way control strip - nothing about it covers the
    target area, unlike a full hint overlay. The user positions it wherever suits
    them, clicks Select Area only when actually ready, and gets a chance to review
    (Redo/Accept/Cancel) after drawing instead of committing the instant they release
    the mouse."""

    select_clicked = Signal()
    redo_clicked = Signal()
    cancel_clicked = Signal()
    accept_clicked = Signal()

    def __init__(self):
        super().__init__()
        # Qt.Tool makes this an NSPanel (non-activating): clicks are delivered
        # immediately regardless of which app is currently frontmost. A plain window
        # (no Qt.Tool) often just consumes a background click to activate the app
        # instead of delivering it to the control - confirmed directly, real
        # synthetic clicks were silently dropped or badly delayed while another app
        # was frontmost. NSPanel's own hidesOnDeactivate default is corrected
        # separately in showEvent (see macos_window_fix), since that's what caused
        # an earlier Qt.Tool overlay in this app to vanish on app-switch - not
        # Qt.Tool itself.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setStyleSheet(
            "QWidget { background: rgb(28,30,34); border-radius: 6px; }"
            "QLabel { color: #cfd6e0; padding: 0 4px; }"
            "QPushButton { color: white; font-weight: bold; padding: 6px 12px;"
            " border-radius: 4px; border: none; background: #444; }"
        )
        self._drag_offset: Optional[QPoint] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.select_btn = QPushButton("Select Area")
        self.select_btn.setStyleSheet("background: #4fa3ff;")
        self.select_btn.clicked.connect(self.select_clicked.emit)
        layout.addWidget(self.select_btn)

        self.redo_btn = QPushButton("Redo")
        self.redo_btn.clicked.connect(self.redo_clicked.emit)
        layout.addWidget(self.redo_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        layout.addWidget(self.cancel_btn)

        self.accept_btn = QPushButton("Accept")
        self.accept_btn.setStyleSheet("background: #27ae60;")
        self.accept_btn.clicked.connect(self.accept_clicked.emit)
        layout.addWidget(self.accept_btn)

        self.set_state("idle")

    def set_state(self, state: str, size_text: str = "") -> None:
        if state == "idle":
            self.status_label.setText("Drag this toolbar anywhere, then click Select Area when ready")
            self.select_btn.show()
            self.redo_btn.hide()
            self.cancel_btn.hide()
            self.accept_btn.hide()
        elif state == "selecting":
            self.status_label.setText("Click and drag on screen to draw the region")
            self.select_btn.hide()
            self.redo_btn.hide()
            self.cancel_btn.show()
            self.accept_btn.hide()
        elif state == "selected":
            self.status_label.setText(f"Selected: {size_text}")
            self.select_btn.hide()
            self.redo_btn.show()
            self.cancel_btn.show()
            self.accept_btn.show()
        self.adjustSize()

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


class RegionPicker(QObject):
    """A small toolbar the user positions and paces themselves: click Select Area
    when ready, drag anywhere on screen to draw the region - including directly over
    another running app - then Redo, Cancel, or Accept before it's saved."""

    region_selected = Signal(float, float, float, float)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = _DrawFrame()
        self._toolbar = _Toolbar()
        self._toolbar.select_clicked.connect(self._begin_selecting)
        self._toolbar.redo_clicked.connect(self._redo)
        self._toolbar.cancel_clicked.connect(self._cancel)
        self._toolbar.accept_clicked.connect(self._accept)

        self._bridge = _MouseBridge()
        self._bridge.pressed.connect(self._on_pressed, Qt.QueuedConnection)
        self._bridge.moved.connect(self._on_moved, Qt.QueuedConnection)
        self._bridge.released.connect(self._on_released, Qt.QueuedConnection)

        self._listener: Optional[mouse.Listener] = None
        self._start: Optional[QPoint] = None
        self._result_rect: Optional[QRect] = None

    def start(self) -> None:
        self._toolbar.adjustSize()
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self._toolbar.move(geo.center().x() - self._toolbar.width() // 2, geo.top() + 24)
        self._toolbar.show()

    def _begin_selecting(self) -> None:
        self._toolbar.set_state("selecting")
        self._listener = mouse.Listener(on_click=self._on_click, on_move=self._on_move)
        self._listener.start()

    def _redo(self) -> None:
        self._frame.hide()
        self._result_rect = None
        self._begin_selecting()

    def _cancel(self) -> None:
        self._stop_listener()
        self._frame.close()
        self._toolbar.close()
        self.cancelled.emit()

    def _accept(self) -> None:
        if self._result_rect is None:
            return
        rect = self._result_rect
        self._stop_listener()
        self._frame.close()
        self._toolbar.close()
        self.region_selected.emit(rect.x(), rect.y(), rect.width(), rect.height())

    def _stop_listener(self) -> None:
        if self._listener is not None and self._listener.running:
            self._listener.stop()
        self._listener = None

    # --- pynput thread ---
    def _on_click(self, x, y, button, pressed) -> Optional[bool]:
        if button != mouse.Button.left:
            return None
        if pressed:
            self._bridge.pressed.emit(int(x), int(y))
        else:
            self._bridge.released.emit(int(x), int(y))
            return False  # stop the listener - the gesture is complete
        return None

    def _on_move(self, x, y) -> None:
        self._bridge.moved.emit(int(x), int(y))

    # --- Qt main thread ---
    def _on_pressed(self, x: int, y: int) -> None:
        self._start = QPoint(x, y)
        self._frame.show_rect(QRect(x, y, 1, 1))

    def _on_moved(self, x: int, y: int) -> None:
        if self._start is None:
            return
        rect = QRect(self._start, QPoint(x, y)).normalized()
        self._frame.show_rect(rect)

    def _on_released(self, x: int, y: int) -> None:
        start = self._start
        self._start = None
        if start is None:
            return
        rect = QRect(start, QPoint(x, y)).normalized()
        if rect.width() < MIN_SIZE or rect.height() < MIN_SIZE:
            self._frame.hide()
            self._toolbar.set_state("idle")
            return
        self._result_rect = rect
        self._toolbar.set_state("selected", f"{rect.width()} × {rect.height()}")

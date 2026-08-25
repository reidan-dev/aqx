from __future__ import annotations

import io
import json
from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ocr.capture import capture_cgimage, cgimage_to_pil
from ..paths import RECORDINGS_DIR
from ..recording.events import InputEvent

BUTTON_COLORS = {
    "left": QColor("#4fb85f"),
    "right": QColor("#d1493f"),
    "middle": QColor("#a34fd1"),
}
DEFAULT_MARKER_COLOR = QColor("#9aa4b2")
PATH_COLOR = QColor("#4fa3ff")
FALLBACK_BG = QColor("#20242b")  # used only if a screenshot of the real screen can't be captured
PANEL_BG = "background: rgb(20,20,24); border-radius: 6px;"


def _capture_screen_pixmap(geo: QRect) -> Optional[QPixmap]:
    """A frozen photo of the real screen the recording happened on (minus AQx's own
    windows, same exclusion capture_cgimage already does for OCR), used as the
    overlay's background so markers/path line up with what's actually there instead
    of an abstract diagram. Returns None if Screen Recording permission isn't
    granted or the capture otherwise fails - caller falls back to a plain color."""
    try:
        image_ref = capture_cgimage(geo.x(), geo.y(), geo.width(), geo.height())
        pil_image = cgimage_to_pil(image_ref)
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buf.getvalue(), "PNG")
        return pixmap if not pixmap.isNull() else None
    except Exception:
        return None


class _MarkerItem(QGraphicsEllipseItem):
    """One mouse_down/mouse_up event on the canvas - draggable to reposition it,
    right-click to delete it. Filled = press, hollow (ring) = release, so a drag
    (down and up at different spots) reads clearly as two connected markers."""

    RADIUS = 7

    def __init__(self, dialog: "RecordingVisualizerDialog", event_index: int, filled: bool, color: QColor):
        r = self.RADIUS
        super().__init__(-r, -r, r * 2, r * 2)
        self.dialog = dialog
        self.event_index = event_index
        self.setBrush(QBrush(color) if filled else QBrush(Qt.transparent))
        self.setPen(QPen(color, 2))
        self.setZValue(3)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.OpenHandCursor)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.dialog._on_marker_moved(self.event_index, self.pos())
        return super().itemChange(change, value)

    def contextMenuEvent(self, event):
        menu = QMenu()
        delete_act = menu.addAction("Delete this event")
        chosen = menu.exec(event.screenPos())
        if chosen == delete_act:
            self.dialog._delete_event(self.event_index)


class RecordingVisualizerDialog(QDialog):
    """A full-screen, frameless overlay of the real screen the recording happened on
    (a frozen screenshot, not a live/transparent view - screen composting of true
    transparency doesn't render reliably here, same constraint documented in
    overlay.py), with the recorded mouse path, click markers, and key events drawn on
    top at their real screen positions. Markers are draggable/deletable in place;
    key events are deletable from the floating list. Save writes edits back to the
    same recording file (opened via the eye icon in RecordBlockDialog)."""

    def __init__(self, parent, name: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._name = name
        self._path = RECORDINGS_DIR / f"{name}.json"
        self._data = json.loads(self._path.read_text())
        self._events: List[InputEvent] = [InputEvent.from_dict(e) for e in self._data.get("events", [])]
        self._deleted: set = set()  # indices into self._events staged for removal on Save
        self._dirty = False
        self._markers: Dict[int, _MarkerItem] = {}
        self._path_item: Optional[QGraphicsPathItem] = None

        self._screen_geo = self._determine_screen_geometry()
        self.setGeometry(self._screen_geo)

        self.scene = QGraphicsScene(0, 0, self._screen_geo.width(), self._screen_geo.height())
        bg_pixmap = _capture_screen_pixmap(self._screen_geo)
        if bg_pixmap is not None:
            bg_item = QGraphicsPixmapItem(bg_pixmap)
            bg_item.setZValue(-10)
            self.scene.addItem(bg_item)
        else:
            self.scene.setBackgroundBrush(QBrush(FALLBACK_BG))

        self.view = QGraphicsView(self.scene, self)
        self.view.setGeometry(0, 0, self._screen_geo.width(), self._screen_geo.height())
        self.view.setFrameShape(QGraphicsView.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setRenderHint(QPainter.Antialiasing)

        self._build_control_bar()
        self._build_key_panel()

        self._redraw_path()
        self._draw_markers()
        self._populate_key_list()

    def _determine_screen_geometry(self) -> QRect:
        """Picks whichever physical screen the recording actually happened on (by the
        first positioned event), falling back to the primary screen - so a recording
        made on a secondary monitor still overlays correctly instead of being offset."""
        app = QApplication.instance()
        for e in self._events:
            if e.x is not None and e.y is not None:
                screen = app.screenAt(QPoint(int(e.x), int(e.y))) if app else None
                if screen is not None:
                    return screen.geometry()
                break
        screen = app.primaryScreen() if app else None
        return screen.geometry() if screen is not None else QRect(0, 0, 1440, 900)

    # --- floating controls (raised above the QGraphicsView, which fills the window) ---
    def _build_control_bar(self) -> None:
        bar = QWidget(self)
        bar.setStyleSheet(PANEL_BG)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        info = QLabel(
            "Drag a marker to reposition it. Right-click a marker, or select keys, to delete. "
            "Filled = press, hollow = release."
        )
        info.setStyleSheet("color:#c7d0dc;")
        layout.addWidget(info)
        layout.addStretch()
        self.status_label = QLabel(f"{len(self._events)} events loaded.")
        self.status_label.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(self.status_label)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save_clicked)
        layout.addWidget(save_btn)
        close_btn = QPushButton("Close (Esc)")
        close_btn.clicked.connect(self._on_close_clicked)
        layout.addWidget(close_btn)
        bar.adjustSize()
        bar.resize(min(self._screen_geo.width() - 40, 920), bar.sizeHint().height())
        bar.move(20, 20)
        bar.raise_()
        bar.show()
        self._control_bar = bar

    def _build_key_panel(self) -> None:
        panel = QWidget(self)
        panel.setStyleSheet(PANEL_BG)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Key events:"))
        self.key_list = QListWidget()
        self.key_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.key_list.setStyleSheet("background: rgb(30,32,38); color:#c7d0dc; border:none;")
        layout.addWidget(self.key_list, 1)
        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self._delete_selected_keys)
        layout.addWidget(del_btn)
        panel.resize(260, 320)
        panel.move(self._screen_geo.width() - 260 - 20, 20 + self._control_bar.height() + 12)
        panel.raise_()
        panel.show()
        self._key_panel = panel

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_close_clicked()
            return
        super().keyPressEvent(event)

    # --- coordinate mapping: real screen pixels <-> scene (offset by this screen's origin) ---
    def _to_scene(self, x: float, y: float) -> QPointF:
        return QPointF(x - self._screen_geo.x(), y - self._screen_geo.y())

    def _to_event_coords(self, pos: QPointF) -> tuple:
        return (pos.x() + self._screen_geo.x(), pos.y() + self._screen_geo.y())

    # --- drawing ---
    def _redraw_path(self) -> None:
        if self._path_item is not None:
            self.scene.removeItem(self._path_item)
            self._path_item = None
        path = None
        for idx, e in enumerate(self._events):
            if idx in self._deleted or e.x is None or e.y is None:
                continue
            pt = self._to_scene(e.x, e.y)
            if path is None:
                path = QPainterPath(pt)
            else:
                path.lineTo(pt)
        if path is not None:
            item = QGraphicsPathItem(path)
            item.setPen(QPen(PATH_COLOR, 2))
            item.setZValue(1)
            self.scene.addItem(item)
            self._path_item = item

    def _draw_markers(self) -> None:
        for idx, e in enumerate(self._events):
            if e.type not in ("mouse_down", "mouse_up") or e.x is None or e.y is None:
                continue
            color = BUTTON_COLORS.get(e.button, DEFAULT_MARKER_COLOR)
            marker = _MarkerItem(self, idx, filled=(e.type == "mouse_down"), color=color)
            marker.setPos(self._to_scene(e.x, e.y))
            marker.setToolTip(f"{e.type} {e.button} @ {e.t:.2f}s ({e.x:.0f}, {e.y:.0f}) - drag to move, right-click to delete")
            self.scene.addItem(marker)
            self._markers[idx] = marker

    def _populate_key_list(self) -> None:
        self.key_list.clear()
        for idx, e in enumerate(self._events):
            if e.type not in ("key_down", "key_up"):
                continue
            item = QListWidgetItem(f"{e.t:.2f}s  {e.type}  '{e.key}'")
            item.setData(Qt.UserRole, idx)
            self.key_list.addItem(item)

    # --- editing ---
    def _on_marker_moved(self, index: int, pos: QPointF) -> None:
        x, y = self._to_event_coords(pos)
        self._events[index].x = x
        self._events[index].y = y
        self._dirty = True
        marker = self._markers.get(index)
        if marker is not None:
            e = self._events[index]
            marker.setToolTip(f"{e.type} {e.button} @ {e.t:.2f}s ({e.x:.0f}, {e.y:.0f}) - drag to move, right-click to delete")
        self._redraw_path()

    def _mark_deleted(self, index: int) -> None:
        self._deleted.add(index)
        self._dirty = True
        self.status_label.setText(f"{len(self._deleted)} event(s) marked for deletion - Save to apply.")

    def _delete_event(self, index: int) -> None:
        self._mark_deleted(index)
        marker = self._markers.pop(index, None)
        if marker is not None:
            self.scene.removeItem(marker)
        self._redraw_path()

    def _delete_selected_keys(self) -> None:
        for item in self.key_list.selectedItems():
            self._mark_deleted(item.data(Qt.UserRole))
            self.key_list.takeItem(self.key_list.row(item))

    # --- persistence ---
    def _on_save_clicked(self) -> None:
        remaining = [e for i, e in enumerate(self._events) if i not in self._deleted]
        self._data["events"] = [e.to_dict() for e in remaining]
        self._data["duration"] = remaining[-1].t if remaining else 0
        self._path.write_text(json.dumps(self._data, indent=2))
        self._dirty = False
        self.status_label.setText(f"Saved {len(remaining)} events to '{self._name}'.")

    def _on_close_clicked(self) -> None:
        if self._dirty:
            reply = QMessageBox.question(self, "AQx", "Discard unsaved changes to this recording?")
            if reply != QMessageBox.Yes:
                return
        self.reject()

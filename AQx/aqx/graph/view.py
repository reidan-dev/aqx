from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QMimeData, QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .model import Connection, Graph, Node, new_id
from .nodes import label_for, make_node, node_has_summary, summary_for

NODE_WIDTH = 150
NODE_HEADER = 24
SUMMARY_ROW = 18
PORT_ROW = 18
PORT_RADIUS = 5

PORT_COLOR = QColor("#4fa3ff")
PORT_COLOR_HOVER = QColor("#ffffff")
NODE_BG = QColor("#2b2f36")
TEXT_COLOR = QColor("#e6e6e6")
SUMMARY_COLOR = QColor("#9aa4b2")

# Per-node-type accent colors so different block kinds are recognizable at a glance
# (header tint + border + left accent stripe). "_default" covers any future node
# type that hasn't been given its own color yet.
NODE_COLORS = {
    "start": {"header": QColor("#173d2c"), "border": QColor("#2f9e63"), "selected": QColor("#57e39a")},
    "recorded_block": {"header": QColor("#17324d"), "border": QColor("#3f8fd1"), "selected": QColor("#6fbaff")},
    "delay": {"header": QColor("#4d3a12"), "border": QColor("#d19a3d"), "selected": QColor("#ffc966")},
    "log": {"header": QColor("#3a1a4d"), "border": QColor("#a34fd1"), "selected": QColor("#d38aff")},
    "_default": {"header": QColor("#1f2329"), "border": QColor("#4a4f58"), "selected": QColor("#4fa3ff")},
}
ACCENT_WIDTH = 4


class PortItem(QGraphicsEllipseItem):
    def __init__(self, node_item: "NodeItem", name: str, direction: str, row: int, port_area_top: int = NODE_HEADER):
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2, node_item)
        self.node_item = node_item
        self.name = name
        self.direction = direction  # "in" or "out"
        self.setBrush(QBrush(PORT_COLOR))
        self.setPen(QPen(Qt.NoPen))
        self.setAcceptHoverEvents(True)
        self.setZValue(2)

        x = 0 if direction == "in" else NODE_WIDTH
        y = port_area_top + PORT_ROW * row + PORT_ROW / 2
        self.setPos(x, y)

        # QGraphicsTextItem is QObject-based (unlike plain QGraphicsItem siblings), so
        # PySide6 needs an explicit kept reference here or the Python wrapper gets
        # garbage-collected despite the C++ scene-graph parent, silently dropping the label.
        self.label = QGraphicsTextItem(name, node_item)
        self.label.setDefaultTextColor(TEXT_COLOR)
        font = QFont()
        font.setPointSize(8)
        self.label.setFont(font)
        lw = self.label.boundingRect().width()
        self.label.setPos(x + (6 if direction == "in" else -6 - lw), y - 8)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(PORT_COLOR_HOVER))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(PORT_COLOR))
        super().hoverLeaveEvent(event)

    def scene_center(self) -> QPointF:
        return self.mapToScene(self.rect().center())


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, conn_id: str, source: PortItem, target: Optional[PortItem] = None):
        super().__init__()
        self.conn_id = conn_id
        self.source = source
        self.target = target
        self.setPen(QPen(QColor("#8fb8ff"), 2))
        self.setZValue(-1)
        self.update_path()

    def update_path(self, end_pos: Optional[QPointF] = None) -> None:
        p0 = self.source.scene_center()
        p1 = self.target.scene_center() if self.target is not None else end_pos
        if p1 is None:
            return
        path = QPainterPath(p0)
        dx = max(abs(p1.x() - p0.x()) * 0.5, 40)
        path.cubicTo(QPointF(p0.x() + dx, p0.y()), QPointF(p1.x() - dx, p1.y()), p1)
        self.setPath(path)


class NodeItem(QGraphicsRectItem):
    def __init__(self, node: Node):
        self.has_summary = node_has_summary(node.type)
        self.port_area_top = NODE_HEADER + (SUMMARY_ROW if self.has_summary else 0)
        rows = max(len(node.inputs), len(node.outputs), 1)
        height = self.port_area_top + PORT_ROW * rows + 10
        super().__init__(0, 0, NODE_WIDTH, height)
        self.node = node
        self.colors = NODE_COLORS.get(node.type, NODE_COLORS["_default"])
        self.setPos(node.x, node.y)
        self.setBrush(QBrush(NODE_BG))
        self.setPen(QPen(self.colors["border"], 1))
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(1)

        self.title_bg = QGraphicsRectItem(0, 0, NODE_WIDTH, NODE_HEADER, self)
        self.title_bg.setBrush(QBrush(self.colors["header"]))
        self.title_bg.setPen(QPen(Qt.NoPen))

        self.accent = QGraphicsRectItem(0, 0, ACCENT_WIDTH, height, self)
        self.accent.setBrush(QBrush(self.colors["border"]))
        self.accent.setPen(QPen(Qt.NoPen))

        # Kept as self.title (not a local var) - QGraphicsTextItem is QObject-based, so an
        # unreferenced local gets garbage-collected by PySide6 even with a C++ scene parent.
        self.title = QGraphicsTextItem(label_for(node.type), self)
        self.title.setDefaultTextColor(TEXT_COLOR)
        font = QFont()
        font.setBold(True)
        font.setPointSize(9)
        self.title.setFont(font)
        self.title.setPos(8, 3)

        if self.has_summary:
            self.summary = QGraphicsTextItem("", self)
            self.summary.setDefaultTextColor(SUMMARY_COLOR)
            summary_font = QFont()
            summary_font.setPointSize(8)
            summary_font.setItalic(True)
            self.summary.setFont(summary_font)
            self.summary.setPos(8, NODE_HEADER + 1)
            self.refresh_summary()
        else:
            self.summary = None

        self.in_ports: Dict[str, PortItem] = {}
        self.out_ports: Dict[str, PortItem] = {}
        for i, p in enumerate(node.inputs):
            self.in_ports[p.name] = PortItem(self, p.name, "in", i, self.port_area_top)
        for i, p in enumerate(node.outputs):
            self.out_ports[p.name] = PortItem(self, p.name, "out", i, self.port_area_top)

    def refresh_summary(self) -> None:
        if self.summary is not None:
            self.summary.setPlainText(summary_for(self.node))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.node.x = self.pos().x()
            self.node.y = self.pos().y()
            scene = self.scene()
            if isinstance(scene, GraphScene):
                scene.refresh_connections_for(self.node.id)
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):
        border = self.colors["selected"] if self.isSelected() else self.colors["border"]
        self.setPen(QPen(border, 2 if self.isSelected() else 1))
        super().paint(painter, option, widget)


class GraphScene(QGraphicsScene):
    node_double_clicked = Signal(str)

    def __init__(self, graph: Graph):
        super().__init__()
        self.graph = graph
        self.node_items: Dict[str, NodeItem] = {}
        self.conn_items: Dict[str, ConnectionItem] = {}
        self._pending_source: Optional[PortItem] = None
        self._pending_line: Optional[ConnectionItem] = None
        self._panning = False
        self._pan_start = QPointF()
        self.setBackgroundBrush(QBrush(QColor("#1a1d22")))
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        self.node_items.clear()
        self.conn_items.clear()
        for node in self.graph.nodes.values():
            self._add_node_item(node)
        for conn in self.graph.connections.values():
            self._add_connection_item(conn)

    def _add_node_item(self, node: Node) -> NodeItem:
        item = NodeItem(node)
        self.addItem(item)
        self.node_items[node.id] = item
        return item

    def _add_connection_item(self, conn: Connection) -> None:
        src = self.node_items[conn.from_node].out_ports[conn.from_port]
        dst = self.node_items[conn.to_node].in_ports[conn.to_port]
        item = ConnectionItem(conn.id, src, dst)
        self.addItem(item)
        self.conn_items[conn.id] = item

    def add_node(self, node_type: str, pos: QPointF) -> Node:
        node = make_node(node_type, new_id(), pos.x(), pos.y())
        self.graph.add_node(node)
        self._add_node_item(node)
        return node

    def delete_selected(self) -> None:
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                node_id = item.node.id
                for cid in [
                    c.conn_id
                    for c in self.conn_items.values()
                    if c.source.node_item.node.id == node_id
                    or (c.target is not None and c.target.node_item.node.id == node_id)
                ]:
                    self._remove_connection_item(cid)
                self.graph.remove_node(node_id)
                self.removeItem(item)
                del self.node_items[node_id]
            elif isinstance(item, ConnectionItem):
                self._remove_connection_item(item.conn_id)

    def _remove_connection_item(self, conn_id: str) -> None:
        item = self.conn_items.pop(conn_id, None)
        if item is not None:
            self.removeItem(item)
        self.graph.remove_connection(conn_id)

    def refresh_connections_for(self, node_id: str) -> None:
        for c in self.conn_items.values():
            if c.source.node_item.node.id == node_id or (
                c.target is not None and c.target.node_item.node.id == node_id
            ):
                c.update_path()

    def _view_transform(self):
        views = self.views()
        return views[0].transform() if views else None

    def mousePressEvent(self, event):
        transform = self._view_transform()
        item = self.itemAt(event.scenePos(), transform) if transform is not None else None
        if isinstance(item, PortItem):
            if item.direction == "out":
                # Start a brand new connection from this output.
                self._pending_source = item
                self._pending_line = ConnectionItem("__pending__", item)
                self._pending_line.update_path(event.scenePos())
                self.addItem(self._pending_line)
                return
            existing = next((c for c in self.conn_items.values() if c.target is item), None)
            if existing is not None:
                # Grab the wire already plugged into this input so it can be dragged to a
                # different input (reattach) or dropped on empty space to detach it.
                self._pending_source = existing.source
                self._remove_connection_item(existing.conn_id)
                self._pending_line = ConnectionItem("__pending__", self._pending_source)
                self._pending_line.update_path(event.scenePos())
                self.addItem(self._pending_line)
                return
        if item is None and event.button() == Qt.LeftButton:
            # Clicking truly empty canvas pans instead of rubber-band-selecting.
            view = self.views()[0] if self.views() else None
            if view is not None:
                self._panning = True
                self._pan_start = event.screenPos()
                view.setCursor(Qt.ClosedHandCursor)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pending_line is not None:
            self._pending_line.update_path(event.scenePos())
            return
        if self._panning:
            view = self.views()[0] if self.views() else None
            if view is not None:
                delta = event.screenPos() - self._pan_start
                self._pan_start = event.screenPos()
                view.horizontalScrollBar().setValue(int(view.horizontalScrollBar().value() - delta.x()))
                view.verticalScrollBar().setValue(int(view.verticalScrollBar().value() - delta.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pending_line is not None:
            transform = self._view_transform()
            item = self.itemAt(event.scenePos(), transform) if transform is not None else None
            if isinstance(item, PortItem) and item.direction == "in":
                for cid in [c.conn_id for c in self.conn_items.values() if c.target is item]:
                    self._remove_connection_item(cid)
                conn = Connection(
                    new_id(),
                    self._pending_source.node_item.node.id,
                    self._pending_source.name,
                    item.node_item.node.id,
                    item.name,
                )
                self.graph.add_connection(conn)
                self._add_connection_item(conn)
            self.removeItem(self._pending_line)
            self._pending_line = None
            self._pending_source = None
            return
        if self._panning:
            self._panning = False
            view = self.views()[0] if self.views() else None
            if view is not None:
                view.setCursor(Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        transform = self._view_transform()
        item = self.itemAt(event.scenePos(), transform) if transform is not None else None
        node_item = item
        while node_item is not None and not isinstance(node_item, NodeItem):
            node_item = node_item.parentItem()
        if node_item is not None:
            self.node_double_clicked.emit(node_item.node.id)
            return
        super().mouseDoubleClickEvent(event)


NODE_TYPE_MIME = "application/x-aqx-node-type"


class NodePaletteList(QListWidget):
    """The node palette: items can be dragged onto the canvas to place a node there,
    or double-clicked to add one at the center of the current view."""

    def __init__(self, node_types: list, parent=None):
        super().__init__(parent)
        self.node_types = node_types
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def mimeTypes(self):
        return [NODE_TYPE_MIME]

    def mimeData(self, items):
        mime = QMimeData()
        if items:
            node_type = self.node_types[self.row(items[0])]
            mime.setData(NODE_TYPE_MIME, node_type.encode("utf-8"))
        return mime


class GraphView(QGraphicsView):
    def __init__(self, scene: GraphScene):
        super().__init__(scene)
        # Empty-canvas panning is implemented by GraphScene itself (click-drag pans);
        # NoDrag avoids Qt's built-in rubber-band-select fighting with that.
        self.setDragMode(QGraphicsView.NoDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setRenderHint(self.renderHints())
        self.setAcceptDrops(True)
        self._panning = False
        self._pan_start = QPointF()
        self._build_zoom_controls()

    def _build_zoom_controls(self) -> None:
        # A small floating +/- cluster anchored to the corner of the canvas, since
        # scrolling now pans instead of zooming.
        container = QWidget(self)
        container.setStyleSheet(
            "QWidget { background: rgba(35,38,44,220); border-radius: 6px; }"
            "QPushButton { background:#333; color:#e6e6e6; border:1px solid #555;"
            " border-radius:4px; font-weight:bold; font-size:15px; }"
            "QPushButton:hover { background:#4fa3ff; border-color:#4fa3ff; }"
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(28, 28)
        zoom_in_btn.setToolTip("Zoom in")
        zoom_in_btn.clicked.connect(lambda: self.zoom_by(1.2))
        layout.addWidget(zoom_in_btn)

        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedSize(28, 28)
        zoom_out_btn.setToolTip("Zoom out")
        zoom_out_btn.clicked.connect(lambda: self.zoom_by(1 / 1.2))
        layout.addWidget(zoom_out_btn)

        self._zoom_container = container
        self._position_zoom_controls()

    def zoom_by(self, factor: float) -> None:
        self.scale(factor, factor)

    def _position_zoom_controls(self) -> None:
        container = getattr(self, "_zoom_container", None)
        if container is None:
            return
        margin = 16
        container.adjustSize()
        container.move(
            self.viewport().width() - container.width() - margin,
            self.viewport().height() - container.height() - margin,
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_zoom_controls()

    def wheelEvent(self, event):
        # Scrolling pans the canvas (natural for a trackpad); use the on-screen +/-
        # buttons or pinch-to-zoom-style Ctrl+scroll for zoom instead.
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            return
        delta = event.pixelDelta()
        if delta.isNull():
            delta = event.angleDelta()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(NODE_TYPE_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(NODE_TYPE_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(NODE_TYPE_MIME):
            node_type = bytes(event.mimeData().data(NODE_TYPE_MIME)).decode("utf-8")
            drop_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            scene_pos = self.mapToScene(drop_pos) - QPointF(NODE_WIDTH / 2, NODE_HEADER / 2)
            self.scene().add_node(node_type, scene_pos)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def reset_view(self) -> None:
        """Pan and zoom back to a default framing of the whole graph ("Home")."""
        self.resetTransform()
        scene = self.scene()
        rect = scene.itemsBoundingRect() if scene is not None else None
        if rect is None or rect.isEmpty():
            self.centerOn(0, 0)
            return
        margin = 60
        self.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        # On macOS the physical "delete" key sends Key_Backspace, not Key_Delete (that's
        # the separate fn+delete "forward delete"), so both are bound here. This is a
        # local, in-app key handler (only fires while the canvas has focus) and is
        # independent of the global system-wide emergency-stop hotkey listener.
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.scene().delete_selected()
            return
        super().keyPressEvent(event)

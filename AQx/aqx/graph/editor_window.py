from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QSpinBox,
    QStatusBar,
    QStyle,
    QToolBar,
)

from ..config import Settings
from ..emergency import GlobalEmergencyStop
from ..overlay import CountdownOverlay
from ..paths import FLOWS_DIR, RECORDINGS_DIR
from ..recording.player import StopFlag
from .model import Graph
from .nodes import NODE_SPECS, label_for
from .ocr_dialog import OCRNodeDialog
from .record_dialog import RecordBlockDialog
from .runner import GraphRunner
from .view import GraphScene, GraphView, NodePaletteList


class GraphEditorWindow(QMainWindow):
    """The main (and only) AQx window: node graph canvas, palette, and execution log."""

    def __init__(
        self,
        graph: Optional[Graph] = None,
        stop_flag: Optional[StopFlag] = None,
        settings: Optional[Settings] = None,
        global_stop: Optional[GlobalEmergencyStop] = None,
    ):
        super().__init__()
        self.setWindowTitle("AQx")
        self.resize(1100, 750)

        self.settings = settings or Settings.load()
        self.graph = graph or Graph()
        self.stop_flag = stop_flag or StopFlag()
        self.global_stop = global_stop
        self.current_flow_path: Optional[Path] = None
        self._palette_types = list(NODE_SPECS.keys())

        self.run_overlay = CountdownOverlay()
        self._run_countdown_timer = QTimer(self)
        self._run_countdown_timer.timeout.connect(self._on_run_countdown_tick)
        self._run_countdown_remaining = 0

        self.scene = GraphScene(self.graph)
        self.scene.node_double_clicked.connect(self._edit_node)
        self.view = GraphView(self.scene)
        self.setCentralWidget(self.view)

        self._build_palette()
        self._build_toolbar()
        self._build_log_panel()
        self._build_menu()
        self.setStatusBar(QStatusBar())

        if graph is None and self.settings.last_flow_path and Path(self.settings.last_flow_path).exists():
            self._load_path(Path(self.settings.last_flow_path))
        else:
            self._refresh_flow_combo()

        if not self.graph.nodes:
            self.scene.add_node("start", QPointF(40, 40))

        self.view.reset_view()

    def _build_palette(self) -> None:
        self.palette_dock = QDockWidget("Nodes (drag or double-click to add)", self)
        listw = NodePaletteList(self._palette_types)
        for node_type in self._palette_types:
            listw.addItem(QListWidgetItem(label_for(node_type)))
        listw.itemDoubleClicked.connect(self._on_palette_double_click)
        self.palette_dock.setWidget(listw)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.palette_dock)

    def _on_palette_double_click(self, item: QListWidgetItem) -> None:
        row = item.listWidget().row(item)
        node_type = self._palette_types[row]
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_node(node_type, center)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        self.addToolBar(tb)

        new_act = QAction("New", self)
        new_act.triggered.connect(self._new_graph)
        tb.addAction(new_act)

        save_act = QAction("Save", self)
        save_act.triggered.connect(self._save)
        tb.addAction(save_act)

        save_as_act = QAction("Save As...", self)
        save_as_act.triggered.connect(self._save_as)
        tb.addAction(save_as_act)

        load_act = QAction("Load...", self)
        load_act.triggered.connect(self._load)
        tb.addAction(load_act)

        tb.addSeparator()

        style = self.style()

        home_act = QAction(style.standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon), "Home", self)
        home_act.setToolTip("Reset pan and zoom to frame the whole graph")
        home_act.triggered.connect(lambda: self.view.reset_view())
        tb.addAction(home_act)
        tb.widgetForAction(home_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        tb.addSeparator()

        self.run_act = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Run", self)
        self.run_act.setToolTip("Run")
        self.run_act.triggered.connect(self._run)
        tb.addAction(self.run_act)
        tb.widgetForAction(self.run_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        stop_act = QAction(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop), "Stop", self)
        stop_act.setToolTip("Stop")
        stop_act.triggered.connect(self._stop)
        tb.addAction(stop_act)
        tb.widgetForAction(stop_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        clear_log_act = QAction("Clear Log", self)
        clear_log_act.triggered.connect(lambda: self.log_view.clear())
        tb.addAction(clear_log_act)

        tb.addSeparator()

        tb.addWidget(QLabel(" Loops: "))
        self.loops_spin = QSpinBox()
        self.loops_spin.setRange(0, 1_000_000)
        self.loops_spin.setValue(self.graph.repeat)
        self.loops_spin.setToolTip("Times to run the whole flow. 0 = repeat indefinitely.")
        self.loops_spin.valueChanged.connect(self._on_loops_changed)
        tb.addWidget(self.loops_spin)

        tb.addSeparator()

        tb.addWidget(QLabel(" Flow: "))
        self.flow_combo = QComboBox()
        self.flow_combo.setMinimumWidth(160)
        self.flow_combo.setToolTip("Quick-switch between saved automation flows")
        self.flow_combo.textActivated.connect(self._on_flow_combo_selected)
        tb.addWidget(self.flow_combo)

    def _on_loops_changed(self, value: int) -> None:
        self.graph.repeat = value

    def _build_log_panel(self) -> None:
        self.log_dock = QDockWidget("Execution Log", self)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.log_view.setFont(mono)
        self.log_view.setStyleSheet("background:#1a1d22; color:#c7d0dc; border:none;")
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

    def _build_menu(self) -> None:
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.palette_dock.toggleViewAction())
        view_menu.addAction(self.log_dock.toggleViewAction())

    def _log(self, text: str) -> None:
        self.log_view.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def _new_graph(self) -> None:
        self.graph = Graph()
        self.scene.graph = self.graph
        self.scene.rebuild()
        self.scene.add_node("start", QPointF(40, 40))
        self.loops_spin.setValue(self.graph.repeat)
        self.current_flow_path = None
        self.flow_combo.setCurrentIndex(-1)
        self.view.reset_view()

    # --- Flow persistence ---
    def _refresh_flow_combo(self, select: Optional[str] = None) -> None:
        FLOWS_DIR.mkdir(parents=True, exist_ok=True)
        names = sorted(p.stem for p in FLOWS_DIR.glob("*.json"))
        self.flow_combo.blockSignals(True)
        self.flow_combo.clear()
        self.flow_combo.addItems(names)
        if select and select in names:
            self.flow_combo.setCurrentText(select)
        else:
            self.flow_combo.setCurrentIndex(-1)
        self.flow_combo.blockSignals(False)

    def _load_path(self, path: Path) -> None:
        self.graph = Graph.load(path)
        self.scene.graph = self.graph
        self.scene.rebuild()
        self.loops_spin.setValue(self.graph.repeat)
        self.current_flow_path = path
        self.settings.last_flow_path = str(path)
        self.settings.save()
        self._refresh_flow_combo(select=path.stem)
        self.statusBar().showMessage(f"Loaded {path}", 4000)
        self._log(f"Loaded flow: {path.name}")
        self.view.reset_view()

    def _save_path(self, path: Path) -> None:
        self.graph.save(path)
        self.current_flow_path = path
        self.settings.last_flow_path = str(path)
        self.settings.save()
        self._refresh_flow_combo(select=path.stem)
        self.statusBar().showMessage(f"Saved to {path}", 4000)
        self._log(f"Saved flow: {path.name}")

    def _save(self) -> None:
        if self.current_flow_path is not None:
            self._save_path(self.current_flow_path)
        else:
            self._save_as()

    def _save_as(self) -> None:
        FLOWS_DIR.mkdir(parents=True, exist_ok=True)
        default = str(self.current_flow_path or (FLOWS_DIR / "flow.json"))
        path, _ = QFileDialog.getSaveFileName(self, "Save Flow As", default, "AQx Flow (*.json)")
        if path:
            self._save_path(Path(path))

    def _load(self) -> None:
        FLOWS_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Load Flow", str(FLOWS_DIR), "AQx Flow (*.json)")
        if path:
            self._load_path(Path(path))

    def _on_flow_combo_selected(self, name: str) -> None:
        if not name:
            return
        path = FLOWS_DIR / f"{name}.json"
        if path.exists():
            self._load_path(path)

    def _edit_node(self, node_id: str) -> None:
        node = self.graph.nodes.get(node_id)
        if node is None:
            return
        if node.type == "delay":
            val, ok = QInputDialog.getDouble(
                self, "Delay", "Seconds:", float(node.props.get("seconds", 1.0)), 0, 3600, 2
            )
            if ok:
                node.props["seconds"] = val
        elif node.type == "log":
            val, ok = QInputDialog.getText(self, "Log", "Message:", text=str(node.props.get("message", "")))
            if ok:
                node.props["message"] = val
        elif node.type == "recorded_block":
            dlg = RecordBlockDialog(
                self,
                node.props.get("recording", ""),
                int(node.props.get("repeat", 1)),
                self.settings,
                self.global_stop,
            )
            if dlg.exec() == QDialog.Accepted:
                node.props["recording"] = dlg.selected_recording
                node.props["repeat"] = dlg.selected_repeat
        elif node.type == "ocr":
            # Shown non-modally (.show(), not .exec()): on macOS any modal QDialog
            # triggers a native Cocoa modal session that blocks mouse input to every
            # other window in the app, including the region picker's own toolbar -
            # confirmed directly with real synthetic clicks. So changes apply via the
            # finished signal instead of a blocking return value, and the dialog is
            # kept alive on self (a non-modal dialog isn't held open by a blocking
            # call, so a bare local variable would go out of scope and be
            # garbage-collected while the user is still using it).
            dlg = OCRNodeDialog(self, node.props.get("region", ""), float(node.props.get("interval_seconds", 5.0)))
            self._active_ocr_dialog = dlg

            def on_finished(result, node=node, dlg=dlg):
                if result == QDialog.Accepted:
                    node.props["region"] = dlg.selected_region
                    node.props["interval_seconds"] = dlg.selected_interval
                item = self.scene.node_items.get(node.id)
                if item is not None:
                    item.refresh_summary()

            dlg.finished.connect(on_finished)
            dlg.show()
            return

        item = self.scene.node_items.get(node_id)
        if item is not None:
            item.refresh_summary()

    def _run(self) -> None:
        self.stop_flag.clear()
        # A flow that plays back a recording is about to drive real mouse/keyboard
        # input, same as recording itself - give the user the same prep countdown so
        # they have time to switch focus to the target app first.
        if any(n.type == "recorded_block" for n in self.graph.nodes.values()):
            self._start_run_countdown()
        else:
            self._run_now()

    def _start_run_countdown(self) -> None:
        self.run_act.setEnabled(False)
        self._run_countdown_remaining = self.settings.prep_delay_seconds
        self._on_run_countdown_tick()
        self._run_countdown_timer.start(1000)

    def _on_run_countdown_tick(self) -> None:
        if self.stop_flag.is_set():
            self._run_countdown_timer.stop()
            self.run_overlay.hide()
            self.run_act.setEnabled(True)
            self._log("Run cancelled before it started.")
            return
        if self._run_countdown_remaining > 0:
            self.statusBar().showMessage(f"Starting in {self._run_countdown_remaining}...")
            self.run_overlay.show_message(str(self._run_countdown_remaining))
            self._run_countdown_remaining -= 1
            return
        self._run_countdown_timer.stop()
        self.run_overlay.hide()
        self.run_act.setEnabled(True)
        self._run_now()

    def _run_now(self) -> None:
        self.stop_flag.clear()
        self.runner = GraphRunner(self.graph, self.stop_flag, RECORDINGS_DIR)
        self.runner.status.connect(self._on_runner_status, Qt.QueuedConnection)
        self.runner.node_started.connect(self._highlight_node, Qt.QueuedConnection)
        self.runner.node_updated.connect(self._refresh_node_summary, Qt.QueuedConnection)
        self._log("Run started.")
        thread = threading.Thread(target=self.runner.run, daemon=True)
        thread.start()

    def _on_runner_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 4000)
        self._log(text)

    def _stop(self) -> None:
        self.stop_flag.set()
        if self._run_countdown_timer.isActive():
            self._run_countdown_timer.stop()
            self.run_overlay.hide()
            self.run_act.setEnabled(True)
            self._log("Run cancelled before it started.")
        else:
            self._log("Stop requested.")

    def _highlight_node(self, node_id: str) -> None:
        for nid, item in self.scene.node_items.items():
            item.setSelected(nid == node_id)

    def _refresh_node_summary(self, node_id: str) -> None:
        item = self.scene.node_items.get(node_id)
        if item is not None:
            item.refresh_summary()

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QStatusBar,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..emergency import GlobalEmergencyStop
from ..mouse_guard import MouseAggressionGuard
from ..overlay import CountdownOverlay
from ..paths import FLOWS_DIR, RECORDINGS_DIR
from ..recording.player import PauseFlag, StopFlag
from .code_dialog import CodeDialog
from .connector_dialog import ConnectorDialog
from .controls_dialog import ControlsDialog
from .custom_blocks import list_custom_blocks
from .skills_dialog import SkillsDialog
from .condition_editor import SimpleConditionDialog
from .icons import INACTIVE_COLOR, PAUSE_COLOR, PLAY_COLOR, STOP_COLOR, pause_bars_icon, square_icon, triangle_icon
from .if_dialog import IfDialog
from .loop_dialog import LoopDialog
from .mini_toolbar import MiniRunToolbar
from .model import Graph
from .nodes import HELP_TEXT, NODE_SPECS, LOOP_TYPES, label_for, parse_duration_seconds
from .ocr_dialog import OCRNodeDialog
from .record_dialog import RecordBlockDialog
from .runner import GraphRunner
from .settings_dialog import SettingsDialog
from .telegram_dialog import TelegramDialog
from .variable_dialog import SetVariableDialog
from .view import GraphScene, GraphView, NodePaletteList

DOUBLE_PRESS_WINDOW = 0.6  # seconds - F12 pressed again within this counts as a stop


class GraphEditorWindow(QMainWindow):
    """The main (and only) AQx window: node graph canvas, palette, and execution log."""

    hotkey_triggered = Signal()  # emitted (from any thread) whenever the global hotkey fires
    mouse_guard_triggered = Signal()  # emitted (from the guard's own listener thread) on an aggressive real mouse move

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
        self._is_running = False
        self._pending_code_error = False  # forces the mini toolbar to stay up so an in-run Code error is seen even after the run stops
        self.pause_flag = PauseFlag()
        self._last_hotkey_time = 0.0
        self.hotkey_triggered.connect(self._on_hotkey_triggered)
        # Created once, for the app's whole lifetime - see MouseAggressionGuard's
        # own docstring for why (repeatedly creating/tearing down its CGEventTap
        # per run was destabilizing the unrelated global hotkey listener).
        self.mouse_guard = MouseAggressionGuard(self.settings.mouse_guard_threshold, self.mouse_guard_triggered.emit)
        self.mouse_guard_triggered.connect(self._on_mouse_guard_triggered)

        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state_changed)

        # Local fallback for the same hotkey used by GlobalEmergencyStop: the global
        # listener needs macOS Input Monitoring permission to fire while another app
        # is frontmost, and that permission can silently go stale (e.g. after the
        # Python interpreter it was granted to gets replaced by a dependency
        # reinstall). A QShortcut has no such dependency, so F12 still works whenever
        # AQx itself has focus even if the global listener's permission is broken.
        self._local_hotkey_shortcut = QShortcut(QKeySequence(self.settings.emergency_key.upper()), self)
        self._local_hotkey_shortcut.activated.connect(self._on_hotkey_triggered)

        self.mini_toolbar = MiniRunToolbar()
        self.mini_toolbar.play_pause_clicked.connect(self._toggle_pause)
        self.mini_toolbar.stop_clicked.connect(self._stop)
        self.mini_toolbar.maximize_clicked.connect(self._restore_from_mini)
        self.mini_toolbar.settings_clicked.connect(self._open_settings)
        self.mini_toolbar.control_value_changed.connect(self._on_control_value_changed)
        self.mini_toolbar.error_dismissed.connect(self._on_mini_error_dismissed)

        self.run_overlay = CountdownOverlay()
        self._run_countdown_timer = QTimer(self)
        self._run_countdown_timer.timeout.connect(self._on_run_countdown_tick)
        self._run_countdown_remaining = 0

        self.scene = GraphScene(self.graph)
        self.scene.node_double_clicked.connect(self._edit_node)
        self.scene.connection_double_clicked.connect(self._edit_connection_tap)
        self.view = GraphView(self.scene)

        # A little breathing room around the canvas instead of the view running edge
        # to edge into the docks/window frame, which felt cramped.
        canvas_container = QWidget()
        canvas_container.setStyleSheet("background: #1a1d22;")
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(10, 10, 10, 10)
        canvas_layout.addWidget(self.view)
        self.setCentralWidget(canvas_container)

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

    def _palette_entries(self) -> list:
        entries = [
            {"node_type": t, "props": None, "label": label_for(t), "help": HELP_TEXT.get(t, "")}
            for t in self._palette_types
        ]
        for block in list_custom_blocks():
            entries.append(
                {
                    "node_type": block["base_type"],
                    "props": block["props"],
                    "label": f"{block['name']} ({label_for(block['base_type'])})",
                    "help": HELP_TEXT.get(block["base_type"], ""),
                }
            )
        return entries

    def _populate_palette_items(self, listw: NodePaletteList) -> None:
        for entry in listw.entries:
            item = QListWidgetItem(f"{entry['label']}  (?)")
            item.setToolTip(entry.get("help", ""))
            listw.addItem(item)

    def _build_palette(self) -> None:
        self.palette_dock = QDockWidget("Nodes (drag or double-click to add)", self)
        listw = NodePaletteList(self._palette_entries())
        self._populate_palette_items(listw)
        listw.itemDoubleClicked.connect(self._on_palette_double_click)
        self.palette_dock.setWidget(listw)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.palette_dock)
        self.scene.custom_block_saved.connect(self._on_custom_block_saved)

    def _refresh_palette(self) -> None:
        listw = self.palette_dock.widget()
        listw.entries = self._palette_entries()
        listw.clear()
        self._populate_palette_items(listw)

    def _on_custom_block_saved(self, name: str) -> None:
        self._log(f"Saved custom block: {name}")
        self._refresh_palette()

    def _on_palette_double_click(self, item: QListWidgetItem) -> None:
        listw = item.listWidget()
        entry = listw.entries[listw.row(item)]
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_node(entry["node_type"], center, props=entry.get("props"))

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

        self._play_icon_active = triangle_icon(PLAY_COLOR)
        self._play_icon_inactive = triangle_icon(INACTIVE_COLOR)
        self._stop_icon_active = square_icon(STOP_COLOR)
        self._stop_icon_inactive = square_icon(INACTIVE_COLOR)
        self._pause_icon_running = pause_bars_icon(PAUSE_COLOR)
        self._pause_icon_paused = triangle_icon(PLAY_COLOR)
        self._pause_icon_inactive = pause_bars_icon(INACTIVE_COLOR)

        self.run_act = QAction(self._play_icon_active, "Run", self)
        self.run_act.setToolTip("Run")
        self.run_act.triggered.connect(self._run)
        tb.addAction(self.run_act)
        tb.widgetForAction(self.run_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.pause_act = QAction(self._pause_icon_inactive, "Pause", self)
        self.pause_act.setToolTip(f"Pause ({self._hotkey_label()})")
        self.pause_act.triggered.connect(self._toggle_pause)
        tb.addAction(self.pause_act)
        tb.widgetForAction(self.pause_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.stop_act = QAction(self._stop_icon_inactive, "Stop", self)
        self.stop_act.setToolTip(f"Stop ({self._hotkey_label()} twice)")
        self.stop_act.triggered.connect(self._stop)
        tb.addAction(self.stop_act)
        tb.widgetForAction(self.stop_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._set_running_state(False)

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

        settings_menu = self.menuBar().addMenu("Settings")
        prefs_act = QAction("Preferences...", self)
        # Without this, macOS/Qt auto-detects "Preferences..." text and silently
        # relocates the action into the application menu (the bold "AQx" menu next to
        # the Apple logo) instead of leaving it in the Settings menu it was actually
        # added to - confirmed directly, the Settings menu rendered empty because of
        # it. NoRole opts out of that heuristic relocation entirely.
        prefs_act.setMenuRole(QAction.MenuRole.NoRole)
        prefs_act.triggered.connect(self._open_settings)
        settings_menu.addAction(prefs_act)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings, self.global_stop)
        if dlg.exec() != QDialog.Accepted:
            return

        changed = False
        new_key = dlg.selected_emergency_key
        if new_key != self.settings.emergency_key:
            self.settings.emergency_key = new_key
            changed = True
            if self.global_stop is not None:
                self.global_stop.key_name = new_key
            self._local_hotkey_shortcut.setKey(QKeySequence(new_key.upper()))
            self.stop_act.setToolTip(f"Stop ({self._hotkey_label()} twice)")
            self._update_pause_icon()
            self._log(f"Global hotkey changed to {self._hotkey_label()}.")

        if dlg.selected_telegram_bot_token != self.settings.telegram_bot_token:
            self.settings.telegram_bot_token = dlg.selected_telegram_bot_token
            changed = True
        if dlg.selected_telegram_chat_id != self.settings.telegram_chat_id:
            self.settings.telegram_chat_id = dlg.selected_telegram_chat_id
            changed = True
        if dlg.selected_mouse_guard_enabled != self.settings.mouse_guard_enabled:
            self.settings.mouse_guard_enabled = dlg.selected_mouse_guard_enabled
            changed = True
        if dlg.selected_mouse_guard_threshold != self.settings.mouse_guard_threshold:
            self.settings.mouse_guard_threshold = dlg.selected_mouse_guard_threshold
            changed = True
        if dlg.selected_mouse_guard_action != self.settings.mouse_guard_action:
            self.settings.mouse_guard_action = dlg.selected_mouse_guard_action
            changed = True
        if changed:
            self.settings.save()

    def _log(self, text: str) -> None:
        self.log_view.appendPlainText(f"[{time.strftime('%I:%M:%S %p')}] {text}")

    def show_permission_warning(self, message: str) -> None:
        """Called from main() when the global hotkey listener failed to start (almost
        always missing/stale macOS Input Monitoring permission). A console print alone
        is easy to miss - this surfaces it in both the log panel (permanent) and the
        status bar (until dismissed), and F12 still works locally via the QShortcut
        fallback set up in __init__ while the app itself has focus."""
        self._log(f"WARNING: {message}")
        self.statusBar().showMessage(message)

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
        elif node.type == "telegram":
            dlg = TelegramDialog(self, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "recorded_block":
            dlg = RecordBlockDialog(
                self,
                node.props.get("recording", ""),
                int(node.props.get("repeat", 1)),
                self.settings,
                self.global_stop,
                speed=float(node.props.get("speed", 1.0)),
            )
            if dlg.exec() == QDialog.Accepted:
                node.props["recording"] = dlg.selected_recording
                node.props["repeat"] = dlg.selected_repeat
                node.props["speed"] = dlg.selected_speed
        elif node.type == "ocr":
            # Shown non-modally (.show(), not .exec()): on macOS any modal QDialog
            # triggers a native Cocoa modal session that blocks mouse input to every
            # other window in the app, including the region picker's own toolbar -
            # confirmed directly with real synthetic clicks. So changes apply via the
            # finished signal instead of a blocking return value, and the dialog is
            # kept alive on self (a non-modal dialog isn't held open by a blocking
            # call, so a bare local variable would go out of scope and be
            # garbage-collected while the user is still using it).
            dlg = OCRNodeDialog(
                self,
                node.props.get("region", ""),
                float(node.props.get("interval_seconds", 5.0)),
                str(node.props.get("extract_pattern", "")),
            )
            self._active_ocr_dialog = dlg

            def on_finished(result, node=node, dlg=dlg):
                if result == QDialog.Accepted:
                    node.props["region"] = dlg.selected_region
                    node.props["interval_seconds"] = dlg.selected_interval
                    node.props["extract_pattern"] = dlg.selected_extract_pattern
                item = self.scene.node_items.get(node.id)
                if item is not None:
                    item.refresh_summary()

            dlg.finished.connect(on_finished)
            dlg.show()
            return
        elif node.type == "set_variable":
            dlg = SetVariableDialog(self, self.graph, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type in LOOP_TYPES:
            dlg = LoopDialog(self, self.graph, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "if":
            dlg = IfDialog(self, self.graph, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
                self._apply_output_port_change(node_id, node)
                return
        elif node.type == "connector":
            dlg = ConnectorDialog(self, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "logic":
            dlg = SimpleConditionDialog(
                self, self.graph, node, "Logic",
                "Result of this AND/OR/NOT combination - reusable by any other block's "
                "condition via \"Logic block\" as a source:",
            )
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "loop_exit":
            dlg = SimpleConditionDialog(
                self, self.graph, node, "Exit Loop",
                "Exit the direct parent loop (nearest enclosing For/While/Until) when this is true:",
            )
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "code":
            dlg = CodeDialog(self, self.graph, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "controls":
            dlg = ControlsDialog(self, node)
            if dlg.exec() == QDialog.Accepted:
                dlg.apply_to_node()
        elif node.type == "skills":
            # Non-modal for the same reason as the OCR node's dialog - it can launch
            # RegionPicker, and a modal QDialog on macOS blocks the picker's own
            # toolbar clicks. Kept alive on self so it isn't garbage-collected while
            # still open.
            dlg = SkillsDialog(self, node)
            self._active_skills_dialog = dlg

            def on_finished(result, node=node, dlg=dlg):
                if result == QDialog.Accepted:
                    dlg.apply_to_node()
                item = self.scene.node_items.get(node.id)
                if item is not None:
                    item.refresh_summary()

            dlg.finished.connect(on_finished)
            dlg.show()
            return
        elif node.type == "turn_off":
            current = str(node.props.get("duration", ""))
            while True:
                val, ok = QInputDialog.getText(
                    self, "Turn Off", "Stop the run after (e.g. 4h, 2m, 3h30m):", text=current
                )
                if not ok:
                    break
                if val.strip() and parse_duration_seconds(val) is None:
                    QMessageBox.warning(
                        self, "AQx", f"'{val}' isn't a valid duration - use a form like 4h, 2m, or 3h30m."
                    )
                    current = val
                    continue
                node.props["duration"] = val
                break

        item = self.scene.node_items.get(node_id)
        if item is not None:
            item.refresh_summary()

    def _edit_connection_tap(self, conn_id: str) -> None:
        conn = self.graph.connections.get(conn_id)
        if conn is None:
            return
        text, ok = QInputDialog.getText(
            self,
            "Log Tap",
            "Message to log whenever this wire fires (use $variable_name, blank to remove):",
            text=conn.log_message,
        )
        if not ok:
            return
        conn.log_message = text.strip()
        item = self.scene.conn_items.get(conn_id)
        if item is not None:
            item.set_tap_message(conn.log_message)

    def _apply_output_port_change(self, node_id: str, node) -> None:
        """After a dialog changes a node's output port count (If's Elif rows,
        Connector's fan-out count), drop any connections that pointed at a port that
        no longer exists, then rebuild the node's visual item to match."""
        valid_ports = {p.name for p in node.outputs}
        stale = [
            c.id
            for c in self.graph.connections.values()
            if c.from_node == node_id and c.from_port not in valid_ports
        ]
        for conn_id in stale:
            self.scene._remove_connection_item(conn_id)
        self.scene.rebuild_node(node_id)

    def _set_running_state(self, running: bool) -> None:
        """Play is only clickable while idle (an already-running flow can't be
        started again from underneath itself); Stop and Pause are only clickable
        while something is actually running or counting down. Icon color follows the
        same state so it's visible at a glance, not just via the disabled look."""
        self._is_running = running
        self.run_act.setEnabled(not running)
        self.run_act.setIcon(self._play_icon_inactive if running else self._play_icon_active)
        self.stop_act.setEnabled(running)
        self.stop_act.setIcon(self._stop_icon_active if running else self._stop_icon_inactive)
        self.pause_act.setEnabled(running)
        self._update_pause_icon()
        self._sync_mini_toolbar()

    def _hotkey_label(self) -> str:
        return self.settings.emergency_key.upper()

    def _update_pause_icon(self) -> None:
        if not self._is_running:
            self.pause_act.setIcon(self._pause_icon_inactive)
            self.pause_act.setToolTip(f"Pause ({self._hotkey_label()})")
        elif self.pause_flag.is_set():
            self.pause_act.setIcon(self._pause_icon_paused)
            self.pause_act.setToolTip(f"Resume ({self._hotkey_label()})")
        else:
            self.pause_act.setIcon(self._pause_icon_running)
            self.pause_act.setToolTip(f"Pause ({self._hotkey_label()})")

    def _toggle_pause(self) -> None:
        if not self._is_running:
            return
        if self.pause_flag.is_set():
            self.pause_flag.clear()
            self._log("Resumed.")
            self.statusBar().showMessage("Resumed", 3000)
            self._update_pause_icon()
            self._sync_mini_toolbar()
        else:
            self._pause()

    def _pause(self, log_message: str = "Paused.") -> None:
        """The actual "go to paused" half of _toggle_pause, pulled out so
        something other than the pause hotkey/button - the mouse guard, when set
        to "Pause the run" instead of "Stop the run" - can pause a run the same
        way. A no-op if already paused (or not running), so a caller doesn't need
        to check first."""
        if not self._is_running or self.pause_flag.is_set():
            return
        self.pause_flag.set()
        self._log(log_message)
        key = self._hotkey_label()
        self.statusBar().showMessage(f"Paused - {key} to resume, {key} twice quickly to stop", 4000)
        self._update_pause_icon()
        self._sync_mini_toolbar()

    def _on_hotkey_triggered(self) -> None:
        """The global hotkey (configurable in Settings, F12 by default) means
        different things depending on context: outside of a run it's a harmless
        no-op (nothing to pause/stop); a single press during a run toggles pause
        immediately; a second press within DOUBLE_PRESS_WINDOW escalates to a full
        stop."""
        if not self._is_running:
            return
        now = time.monotonic()
        if now - self._last_hotkey_time < DOUBLE_PRESS_WINDOW:
            self._last_hotkey_time = 0.0
            self._stop()
            return
        self._last_hotkey_time = now
        self._toggle_pause()

    def _sync_mini_toolbar(self) -> None:
        self._update_mini_toolbar_visibility()

    def _should_show_mini_toolbar(self) -> bool:
        """The mini toolbar's whole purpose is giving Play/Pause/Stop access when the
        main window isn't what the user is looking at - which is just as true when
        another app is frontmost (AQx not minimized, just not the active app) as when
        AQx is literally minimized. Only shown while a flow is actually running, except
        a pending Code-block error keeps it up (with the error message) even after the
        run has stopped, until the user dismisses it or restores the window."""
        if self._pending_code_error:
            return True
        if not self._is_running:
            return False
        if self.isMinimized():
            return True
        app = QApplication.instance()
        return app is not None and app.applicationState() != Qt.ApplicationState.ApplicationActive

    def _update_mini_toolbar_visibility(self) -> None:
        mini = getattr(self, "mini_toolbar", None)
        if mini is None:
            return
        mini.sync_state(running=self._is_running, paused=self.pause_flag.is_set())
        if self._should_show_mini_toolbar():
            mini.show_near_top_right()
        else:
            mini.hide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._update_mini_toolbar_visibility()

    def _on_application_state_changed(self) -> None:
        """Covers losing focus without minimizing - e.g. clicking another app while
        AQx's window is still fully visible underneath. changeEvent's
        WindowStateChange alone never fires for that case."""
        self._update_mini_toolbar_visibility()

    def _restore_from_mini(self) -> None:
        self._pending_code_error = False
        self.mini_toolbar.clear_error()
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
        self._set_running_state(True)
        self._run_countdown_remaining = self.settings.prep_delay_seconds
        self._on_run_countdown_tick()
        self._run_countdown_timer.start(1000)

    def _on_run_countdown_tick(self) -> None:
        if self.stop_flag.is_set():
            self._run_countdown_timer.stop()
            self.run_overlay.hide()
            self._set_running_state(False)
            self._log("Run cancelled before it started.")
            return
        if self._run_countdown_remaining > 0:
            self.statusBar().showMessage(f"Starting in {self._run_countdown_remaining}...")
            self.run_overlay.show_message(str(self._run_countdown_remaining))
            self._run_countdown_remaining -= 1
            return
        self._run_countdown_timer.stop()
        self.run_overlay.hide()
        self._run_now()

    def _run_now(self) -> None:
        self.stop_flag.clear()
        self.pause_flag.clear()
        self._pending_code_error = False
        self.mini_toolbar.clear_error()
        self._set_running_state(True)
        self.runner = GraphRunner(
            self.graph, self.stop_flag, RECORDINGS_DIR, pause_flag=self.pause_flag, settings=self.settings
        )
        self.runner.status.connect(self._on_runner_status, Qt.QueuedConnection)
        self.runner.error.connect(self._on_runner_error, Qt.QueuedConnection)
        self.runner.node_started.connect(self._highlight_node, Qt.QueuedConnection)
        self.runner.node_updated.connect(self._refresh_node_summary, Qt.QueuedConnection)
        self.runner.finished.connect(self._on_run_finished, Qt.QueuedConnection)
        self.runner.controls_registered.connect(self._on_controls_registered, Qt.QueuedConnection)
        self._log("Run started.")
        thread = threading.Thread(target=self.runner.run, daemon=True)
        thread.start()
        self._start_mouse_guard()

    def _start_mouse_guard(self) -> None:
        """Armed here (once actual playback begins), not alongside
        _set_running_state - that also covers the prep-delay countdown before a
        Recorded Block flow starts, where the user is expected to be moving the
        mouse to get their window ready. arm() is cheap (no OS-level tap churn -
        see MouseAggressionGuard's docstring), so this is safe to call every run."""
        if not self.settings.mouse_guard_enabled:
            return
        self.mouse_guard.threshold = self.settings.mouse_guard_threshold
        self.mouse_guard.arm()

    def _stop_mouse_guard(self) -> None:
        self.mouse_guard.disarm()

    def _on_mouse_guard_triggered(self) -> None:
        if self.settings.mouse_guard_action == "pause":
            self._pause("Aggressive mouse movement detected - pausing.")
        else:
            self._log("Aggressive mouse movement detected - stopping.")
            self._stop()

    def _on_runner_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 4000)
        self._log(text)

    def _on_runner_error(self, node_id: str, message: str) -> None:
        """A Code block error now stops the run (see GraphRunner._execute_code), so
        unlike a plain status line this needs to stay visible even after the run
        ends and the main window isn't in focus - the mini toolbar is the only
        surface still up in that case."""
        self._pending_code_error = True
        self.mini_toolbar.show_error(message)
        self._update_mini_toolbar_visibility()

    def _on_mini_error_dismissed(self) -> None:
        self._pending_code_error = False
        self._update_mini_toolbar_visibility()

    def _on_run_finished(self) -> None:
        self._set_running_state(False)
        self.mini_toolbar.set_controls([])
        self._stop_mouse_guard()

    def _on_controls_registered(self, controls: list) -> None:
        self.mini_toolbar.set_controls(controls)
        # A Controls block's own face shows its values too, same as any other node.
        for node in self.graph.nodes.values():
            if node.type == "controls":
                self._refresh_node_summary(node.id)

    def _on_control_value_changed(self, name: str, value: str) -> None:
        runner = getattr(self, "runner", None)
        if runner is not None:
            runner.set_control_value(name, value)

    def _stop(self) -> None:
        self.stop_flag.set()
        self.pause_flag.clear()  # a paused runner is blocked waiting on this - clear it so stop can land
        if self._run_countdown_timer.isActive():
            self._run_countdown_timer.stop()
            self.run_overlay.hide()
            self._set_running_state(False)
            self._log("Run cancelled before it started.")
        else:
            # The runner thread is still winding down - Stop itself is now a no-op
            # (nothing more to cancel), but Run must stay disabled until the
            # `finished` signal confirms the runner has actually exited.
            self.stop_act.setEnabled(False)
            self._log("Stop requested.")

    def _highlight_node(self, node_id: str) -> None:
        for nid, item in self.scene.node_items.items():
            item.setSelected(nid == node_id)

    def _refresh_node_summary(self, node_id: str) -> None:
        item = self.scene.node_items.get(node_id)
        if item is not None:
            item.refresh_summary()

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..config import Settings
from ..emergency import GlobalEmergencyStop
from ..overlay import CountdownOverlay
from ..paths import RECORDINGS_DIR
from ..recording.recorder import Recorder
from .icons import eye_icon
from .recording_visualizer import RecordingVisualizerDialog


class _RecorderBridge(QObject):
    """Recorder callbacks fire from pynput listener threads; this hands the status
    string back to the Qt main thread via a queued signal."""

    status = Signal(str)


class RecordBlockDialog(QDialog):
    """Configure a Record Block node: pick an existing recording and repeat count,
    or capture a brand new mouse/keyboard recording without leaving the graph editor."""

    def __init__(
        self,
        parent,
        recording: str,
        repeat: int,
        settings: Settings,
        global_stop: Optional[GlobalEmergencyStop] = None,
        speed: float = 1.0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Record Block")
        self.settings = settings
        self.global_stop = global_stop
        self.selected_recording = recording
        self.selected_repeat = repeat
        self.selected_speed = speed
        self.recorder: Optional[Recorder] = None
        self.overlay = CountdownOverlay()
        self._global_stop_paused = False
        # Paused for this dialog's entire lifetime, not just while actively recording:
        # a keyboard.Listener still crashes (see _pause_global_stop) merely by being
        # active while a QLineEdit has focus elsewhere in the app - e.g. the "Save
        # Recording" name field below, right after a recording finishes.
        self._pause_global_stop()

        self._bridge = _RecorderBridge()
        self._bridge.status.connect(self._on_recorder_status, Qt.QueuedConnection)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._countdown_remaining = 0

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Recording:"))
        combo_row = QHBoxLayout()
        self.combo = QComboBox()
        self._reload_names(select=recording)
        combo_row.addWidget(self.combo, 1)
        self.visualize_btn = QPushButton()
        self.visualize_btn.setIcon(eye_icon(QColor("#c7d0dc")))
        self.visualize_btn.setToolTip("Visualize / edit this recording's mouse path and key events")
        self.visualize_btn.setFixedWidth(32)
        self.visualize_btn.clicked.connect(self._open_visualizer)
        combo_row.addWidget(self.visualize_btn)
        layout.addLayout(combo_row)
        self.combo.currentTextChanged.connect(self._update_visualize_enabled)
        self._update_visualize_enabled()

        repeat_row = QHBoxLayout()
        repeat_row.addWidget(QLabel("Repeat (0 = infinite):"))
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(0, 1_000_000)
        self.repeat_spin.setValue(repeat)
        repeat_row.addWidget(self.repeat_spin)
        layout.addLayout(repeat_row)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed (1.0 = as recorded):"))
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.01, 100.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setValue(speed)
        speed_row.addWidget(self.speed_spin)
        layout.addLayout(speed_row)

        layout.addWidget(QLabel("Record new input:"))
        mode_row = QHBoxLayout()
        self.mouse_check = QCheckBox("Mouse")
        self.mouse_check.setChecked(True)
        self.keyboard_check = QCheckBox("Keyboard")
        self.keyboard_check.setChecked(True)
        mode_row.addWidget(self.mouse_check)
        mode_row.addWidget(self.keyboard_check)
        layout.addLayout(mode_row)

        self.record_btn = QPushButton("Record New...")
        self.record_btn.setStyleSheet("background:#c0392b; color:white; font-weight:bold; padding:6px;")
        self.record_btn.clicked.connect(self._start_recording)
        layout.addWidget(self.record_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#9aa4b2;")
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _reload_names(self, select: str = "") -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        names = sorted(p.stem for p in RECORDINGS_DIR.glob("*.json"))
        self.combo.clear()
        self.combo.addItems(names)
        if select and select in names:
            self.combo.setCurrentText(select)

    def _set_busy(self, busy: bool) -> None:
        self.record_btn.setEnabled(not busy)
        self.mouse_check.setEnabled(not busy)
        self.keyboard_check.setEnabled(not busy)
        self.combo.setEnabled(not busy)
        self.buttons.setEnabled(not busy)
        self.visualize_btn.setEnabled(not busy and bool(self.combo.currentText().strip()))

    def _update_visualize_enabled(self) -> None:
        name = self.combo.currentText().strip()
        self.visualize_btn.setEnabled(bool(name) and (RECORDINGS_DIR / f"{name}.json").exists())

    def _open_visualizer(self) -> None:
        name = self.combo.currentText().strip()
        if not name:
            return
        dlg = RecordingVisualizerDialog(self, name)
        dlg.exec()

    def _start_recording(self) -> None:
        if not (self.mouse_check.isChecked() or self.keyboard_check.isChecked()):
            QMessageBox.information(self, "AQx", "Select Mouse and/or Keyboard to record.")
            return
        self._set_busy(True)
        self._countdown_remaining = self.settings.prep_delay_seconds
        self.overlay.show_message(str(self._countdown_remaining))
        self._on_countdown_tick()
        self._countdown_timer.start(1000)

    def _on_countdown_tick(self) -> None:
        if self._countdown_remaining > 0:
            self.status_label.setText(f"Starting in {self._countdown_remaining}...")
            self.overlay.show_message(str(self._countdown_remaining))
            self._countdown_remaining -= 1
            return
        self._countdown_timer.stop()
        self._begin_recording()

    def _begin_recording(self) -> None:
        self.recorder = Recorder(
            record_mouse=self.mouse_check.isChecked(),
            record_keyboard=self.keyboard_check.isChecked(),
            emergency_key=self.settings.emergency_key,
            on_status=self._bridge.status.emit,
        )
        self.recorder.start()

    def _on_recorder_status(self, status: str) -> None:
        if status == "recording":
            msg = f"Recording... press {self.settings.emergency_key} to stop"
            self.status_label.setText(msg)
            self.overlay.show_message(f"● REC\npress {self.settings.emergency_key} to stop")
        elif status == "stopped":
            self._finish_recording()

    def _finish_recording(self) -> None:
        if self.recorder is None:
            return
        events = self.recorder.events
        self.recorder = None
        self.overlay.hide()
        self._set_busy(False)
        self.status_label.setText("")
        if not events:
            QMessageBox.information(self, "AQx", "No input captured.")
            return
        # Defaults to whatever was selected before recording started, so re-recording
        # to just replace it is one Enter press away - type a different name instead
        # to save as a separate recording. global_stop stays paused through this -
        # it's a QLineEdit dialog, and typing into it is exactly what reproduces the
        # crash if the listener is active.
        previously_selected = self.combo.currentText().strip()
        name, ok = QInputDialog.getText(
            self, "Save Recording", "Name (same name replaces it):", text=previously_selected
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if name != previously_selected and (RECORDINGS_DIR / f"{name}.json").exists():
            reply = QMessageBox.question(
                self, "AQx", f"A recording named '{name}' already exists. Replace it?"
            )
            if reply != QMessageBox.Yes:
                return
        self._save_recording(name, events)
        self._reload_names(select=name)

    def _pause_global_stop(self) -> None:
        if self.global_stop is not None and not self._global_stop_paused:
            try:
                self.global_stop.stop()
            except Exception:
                pass
            self._global_stop_paused = True

    def _resume_global_stop(self) -> None:
        if self.global_stop is not None and self._global_stop_paused:
            try:
                self.global_stop.start()
            except Exception:
                pass
            self._global_stop_paused = False

    def _abort_recording(self) -> None:
        """Cleanly stop everything if the dialog is closed mid-countdown or mid-recording."""
        self._countdown_timer.stop()
        if self.recorder is not None:
            try:
                self._bridge.status.disconnect(self._on_recorder_status)
            except (RuntimeError, TypeError):
                pass
            self.recorder.stop()
            self.recorder = None
        self.overlay.hide()

    def reject(self) -> None:
        self._abort_recording()
        self._resume_global_stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self._abort_recording()
        self._resume_global_stop()
        super().closeEvent(event)

    def _save_recording(self, name: str, events) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        path = RECORDINGS_DIR / f"{name}.json"
        data = {
            "name": name,
            "mode": {"mouse": self.mouse_check.isChecked(), "keyboard": self.keyboard_check.isChecked()},
            "duration": events[-1].t if events else 0,
            "events": [e.to_dict() for e in events],
        }
        path.write_text(json.dumps(data, indent=2))

    def accept(self) -> None:
        self.selected_recording = self.combo.currentText()
        self.selected_repeat = self.repeat_spin.value()
        self.selected_speed = self.speed_spin.value()
        self._resume_global_stop()
        super().accept()

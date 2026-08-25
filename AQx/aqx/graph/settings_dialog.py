from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)
from pynput import keyboard

from ..config import Settings
from ..emergency import GlobalEmergencyStop


class _HotkeyBridge(QObject):
    """pynput's callback fires from its own listener thread; this hands the captured
    key name back to the Qt main thread via a queued signal."""

    captured = Signal(str)


class SettingsDialog(QDialog):
    """App-wide preferences: the global hotkey (pauses/resumes/stops a running flow
    from anywhere, even outside AQx) - captured via a momentary pynput listener
    rather than Qt's own key events, so whatever name gets stored is guaranteed to
    match exactly what GlobalEmergencyStop itself will see for that key (pynput's own
    naming, e.g. "f12", "a", "space" - not Qt's) - and the Telegram bot credentials
    used by every Telegram block in every flow (one bot, configured once here,
    rather than re-entered per block)."""

    def __init__(self, parent, settings: Settings, global_stop: Optional[GlobalEmergencyStop] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        self.global_stop = global_stop
        self.selected_emergency_key = settings.emergency_key
        self.selected_telegram_bot_token = settings.telegram_bot_token
        self.selected_telegram_chat_id = settings.telegram_chat_id
        self._listener: Optional[keyboard.Listener] = None
        self._global_stop_paused = False
        # Same reasoning as RecordBlockDialog: a pynput listener active anywhere in
        # AQx while the global emergency listener is also running risks the crash
        # documented there - pause it for this dialog's whole lifetime, not just
        # while actively capturing.
        self._pause_global_stop()

        self._bridge = _HotkeyBridge()
        self._bridge.captured.connect(self._on_key_captured, Qt.QueuedConnection)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Global hotkey (pause/resume/stop a running flow from anywhere):"))

        row = QHBoxLayout()
        self.capture_btn = QPushButton()
        self.capture_btn.clicked.connect(self._start_capture)
        row.addWidget(self.capture_btn)
        layout.addLayout(row)
        self._refresh_button_text()

        hint = QLabel("Click, then press any single key (function keys included).")
        hint.setStyleSheet("color:#9aa4b2; font-size:11px;")
        layout.addWidget(hint)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color:#3a3d44;")
        layout.addWidget(divider)

        layout.addWidget(QLabel("Telegram bot (used by every Telegram block):"))

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("Bot token:"))
        self.token_edit = QLineEdit(settings.telegram_bot_token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("123456:ABC-DEF...")
        token_row.addWidget(self.token_edit)
        show_token_check = QCheckBox("Show")
        show_token_check.toggled.connect(
            lambda checked: self.token_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        token_row.addWidget(show_token_check)
        layout.addLayout(token_row)

        chat_row = QHBoxLayout()
        chat_row.addWidget(QLabel("Chat ID:"))
        self.chat_id_edit = QLineEdit(settings.telegram_chat_id)
        self.chat_id_edit.setPlaceholderText("e.g. 123456789")
        chat_row.addWidget(self.chat_id_edit)
        layout.addLayout(chat_row)

        telegram_hint = QLabel(
            "Create a bot via @BotFather in Telegram to get a token, message it once, then open "
            "https://api.telegram.org/bot<token>/getUpdates in a browser to find your chat ID."
        )
        telegram_hint.setWordWrap(True)
        telegram_hint.setStyleSheet("color:#9aa4b2; font-size:11px;")
        layout.addWidget(telegram_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_button_text(self) -> None:
        self.capture_btn.setText(f"Change (currently: {self.selected_emergency_key.upper()})")

    def _start_capture(self) -> None:
        self.capture_btn.setEnabled(False)
        self.capture_btn.setText("Press any key...")
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()

    # --- pynput thread ---
    def _key_name(self, key) -> str:
        if isinstance(key, keyboard.KeyCode):
            return key.char if key.char is not None else f"vk_{key.vk}"
        return key.name

    def _on_press(self, key) -> bool:
        self._bridge.captured.emit(self._key_name(key))
        return False  # stop this listener from within its own callback thread

    # --- Qt main thread ---
    def _on_key_captured(self, name: str) -> None:
        self._listener = None
        self.selected_emergency_key = name
        self.capture_btn.setEnabled(True)
        self._refresh_button_text()

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

    def _abort_capture(self) -> None:
        if self._listener is not None:
            try:
                self._bridge.captured.disconnect(self._on_key_captured)
            except (RuntimeError, TypeError):
                pass
            if self._listener.running:
                self._listener.stop()
            self._listener = None

    def reject(self) -> None:
        self._abort_capture()
        self._resume_global_stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self._abort_capture()
        self._resume_global_stop()
        super().closeEvent(event)

    def accept(self) -> None:
        self._abort_capture()
        self._resume_global_stop()
        self.selected_telegram_bot_token = self.token_edit.text().strip()
        self.selected_telegram_chat_id = self.chat_id_edit.text().strip()
        super().accept()

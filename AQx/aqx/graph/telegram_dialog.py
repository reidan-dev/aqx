from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from .model import Node


class TelegramDialog(QDialog):
    """Configure a Telegram node: the message (with $variable_name interpolation,
    same as Log), an optional cap on how many times it actually sends, and an
    optional delay before each send."""

    def __init__(self, parent, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Telegram")
        self._node = node

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Message (use $variable_name to insert a variable):"))
        self.message_edit = QLineEdit(str(node.props.get("message", "")))
        layout.addWidget(self.message_edit)

        max_sends_label = QLabel(
            "Max sends (0 = unlimited) - caps how many times this block actually "
            "sends, even if reached more often (e.g. inside a loop):"
        )
        max_sends_label.setWordWrap(True)
        layout.addWidget(max_sends_label)
        self.max_sends_spin = QSpinBox()
        self.max_sends_spin.setRange(0, 1_000_000)
        self.max_sends_spin.setValue(int(node.props.get("max_sends", 0)))
        layout.addWidget(self.max_sends_spin)

        layout.addWidget(QLabel("Wait before sending (seconds):"))
        self.wait_spin = QDoubleSpinBox()
        self.wait_spin.setRange(0.0, 3600.0)
        self.wait_spin.setDecimals(2)
        self.wait_spin.setValue(float(node.props.get("wait_seconds", 0.0)))
        layout.addWidget(self.wait_spin)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_node(self) -> None:
        self._node.props["message"] = self.message_edit.text()
        self._node.props["max_sends"] = self.max_sends_spin.value()
        self._node.props["wait_seconds"] = self.wait_spin.value()

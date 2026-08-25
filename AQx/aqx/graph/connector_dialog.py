from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from .model import Node


class ConnectorDialog(QDialog):
    """Configure a Connector: just an optional display name. Its single "out" port
    can be wired to as many blocks as you like directly on the canvas - no separate
    output-count management needed."""

    def __init__(self, parent, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Connector")
        self._node = node

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name (optional, shown on the block):"))
        self.name_edit = QLineEdit(str(node.props.get("name", "")))
        layout.addWidget(self.name_edit)

        hint = QLabel("Drag more wires from its “out” port to attach it to multiple blocks.")
        hint.setStyleSheet("color:#9aa4b2; font-style:italic;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_node(self) -> None:
        self._node.props["name"] = self.name_edit.text().strip()

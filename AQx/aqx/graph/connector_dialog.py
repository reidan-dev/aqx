from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .model import Node
from .nodes import connector_output_ports


class ConnectorDialog(QDialog):
    """Configure a Connector: an optional display name and how many outputs it fans
    out to. "+"/"-" only ever append or remove the last output, so existing output
    ports never need renumbering."""

    def __init__(self, parent, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Connector")
        self._node = node
        self._count = max(int(node.props.get("num_outputs", 1)), 1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name (optional, shown on the block):"))
        self.name_edit = QLineEdit(str(node.props.get("name", "")))
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Fans out to:"))
        self._count_label = QLabel()
        layout.addWidget(self._count_label)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Output")
        add_btn.clicked.connect(self._add_output)
        btn_row.addWidget(add_btn)
        self._remove_btn = QPushButton("− Remove Last Output")
        self._remove_btn.clicked.connect(self._remove_output)
        btn_row.addWidget(self._remove_btn)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    def _refresh(self) -> None:
        self._count_label.setText(f"{self._count} output{'s' if self._count != 1 else ''}")
        self._remove_btn.setEnabled(self._count > 1)

    def _add_output(self) -> None:
        self._count += 1
        self._refresh()

    def _remove_output(self) -> None:
        if self._count <= 1:
            return
        self._count -= 1
        self._refresh()

    def apply_to_node(self) -> None:
        self._node.props["name"] = self.name_edit.text().strip()
        self._node.props["num_outputs"] = self._count
        self._node.outputs = connector_output_ports(self._count)

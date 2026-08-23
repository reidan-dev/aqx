from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
)

from .model import Graph, Node


class SetVariableDialog(QDialog):
    """Configure a Set Variable node: a name, and where its value comes from - a
    literal you type, or an OCR node's current reading."""

    def __init__(self, parent, graph: Graph, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Set Variable")
        self._node = node

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Variable name:"))
        self.name_edit = QLineEdit(str(node.props.get("name", "")))
        self.name_edit.setPlaceholderText("e.g. score")
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Value:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Literal text", "literal")
        self.source_combo.addItem("An OCR node's reading", "ocr")
        layout.addWidget(self.source_combo)

        self._stack = QStackedWidget()
        self.literal_edit = QLineEdit(str(node.props.get("literal_value", "")))
        self._stack.addWidget(self.literal_edit)

        self.ocr_combo = QComboBox()
        for n in graph.nodes.values():
            if n.type == "ocr":
                self.ocr_combo.addItem(n.props.get("region") or "(no region)", n.id)
        if self.ocr_combo.count() == 0:
            self.ocr_combo.addItem("(no OCR nodes in this flow)", "")
        self._stack.addWidget(self.ocr_combo)
        layout.addWidget(self._stack)

        self.source_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)

        source_type = node.props.get("source_type", "literal")
        idx = self.source_combo.findData(source_type)
        self.source_combo.setCurrentIndex(max(idx, 0))
        self._stack.setCurrentIndex(max(idx, 0))
        ocr_idx = self.ocr_combo.findData(node.props.get("source_node", ""))
        if ocr_idx >= 0:
            self.ocr_combo.setCurrentIndex(ocr_idx)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_node(self) -> None:
        self._node.props["name"] = self.name_edit.text().strip()
        source_type = self.source_combo.currentData()
        self._node.props["source_type"] = source_type
        if source_type == "literal":
            self._node.props["literal_value"] = self.literal_edit.text()
        else:
            self._node.props["source_node"] = self.ocr_combo.currentData() or ""
            self._node.props["source_label"] = self.ocr_combo.currentText()

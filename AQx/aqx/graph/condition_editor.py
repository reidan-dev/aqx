from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QStackedWidget, QWidget

from .conditions import OPERATOR_LABELS, OPERATORS
from .model import Graph
from .nodes import default_condition


class ConditionEditorWidget(QWidget):
    """One row for editing a condition: a value source (an OCR node's reading, or a
    named variable set earlier in the flow), an operator, and a value to compare
    against. Used by If's cases and by While/Until's loop condition."""

    def __init__(self, graph: Graph, condition: dict = None, parent=None):
        super().__init__(parent)
        self._graph = graph
        condition = condition or default_condition()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.source_type_combo = QComboBox()
        self.source_type_combo.addItem("OCR reading", "ocr")
        self.source_type_combo.addItem("Variable", "variable")
        layout.addWidget(self.source_type_combo)

        self._source_stack = QStackedWidget()
        self.ocr_combo = QComboBox()
        self._reload_ocr_nodes()
        self._source_stack.addWidget(self.ocr_combo)

        self.variable_combo = QComboBox()
        self.variable_combo.setEditable(True)
        self._reload_variables()
        self._source_stack.addWidget(self.variable_combo)
        layout.addWidget(self._source_stack)

        self.source_type_combo.currentIndexChanged.connect(self._source_stack.setCurrentIndex)

        self.operator_combo = QComboBox()
        for op in OPERATORS:
            self.operator_combo.addItem(OPERATOR_LABELS[op], op)
        layout.addWidget(self.operator_combo)

        self.compare_edit = QLineEdit()
        self.compare_edit.setPlaceholderText("value")
        layout.addWidget(self.compare_edit)

        self.set_condition(condition)

    def _reload_ocr_nodes(self) -> None:
        self.ocr_combo.clear()
        for node in self._graph.nodes.values():
            if node.type == "ocr":
                label = node.props.get("region") or "(no region)"
                self.ocr_combo.addItem(label, node.id)
        if self.ocr_combo.count() == 0:
            self.ocr_combo.addItem("(no OCR nodes in this flow)", "")

    def _reload_variables(self) -> None:
        self.variable_combo.clear()
        seen = set()
        for node in self._graph.nodes.values():
            if node.type == "set_variable":
                name = node.props.get("name")
                if name and name not in seen:
                    seen.add(name)
                    self.variable_combo.addItem(name)

    def set_condition(self, condition: dict) -> None:
        source_type = condition.get("source_type", "ocr")
        idx = self.source_type_combo.findData(source_type)
        self.source_type_combo.setCurrentIndex(max(idx, 0))
        self._source_stack.setCurrentIndex(max(idx, 0))

        ocr_idx = self.ocr_combo.findData(condition.get("source_node", ""))
        if ocr_idx >= 0:
            self.ocr_combo.setCurrentIndex(ocr_idx)

        name = condition.get("source_name", "")
        if name:
            self.variable_combo.setCurrentText(name)

        op_idx = self.operator_combo.findData(condition.get("operator", "equals"))
        self.operator_combo.setCurrentIndex(max(op_idx, 0))

        self.compare_edit.setText(str(condition.get("compare_to", "")))

    def get_condition(self) -> dict:
        source_type = self.source_type_combo.currentData()
        result = default_condition()
        result["source_type"] = source_type
        if source_type == "ocr":
            result["source_node"] = self.ocr_combo.currentData() or ""
            result["source_label"] = self.ocr_combo.currentText()
        else:
            result["source_name"] = self.variable_combo.currentText().strip()
        result["operator"] = self.operator_combo.currentData()
        result["compare_to"] = self.compare_edit.text()
        return result

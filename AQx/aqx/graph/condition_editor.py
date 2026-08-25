from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .conditions import OPERATOR_LABELS, OPERATORS, describe_condition, is_compound
from .model import Graph, Node
from .nodes import default_condition


class _ClauseRow(QWidget):
    """One leaf clause: an optional NOT, a value source (an OCR node's reading, or a
    named variable set earlier in the flow), an operator, and a value to compare
    against."""

    def __init__(self, graph: Graph, condition: dict = None, parent=None):
        super().__init__(parent)
        self._graph = graph
        condition = condition or default_condition()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.not_check = QCheckBox("NOT")
        layout.addWidget(self.not_check)

        self.source_type_combo = QComboBox()
        self.source_type_combo.addItem("OCR reading", "ocr")
        self.source_type_combo.addItem("Variable", "variable")
        self.source_type_combo.addItem("Logic block", "logic")
        layout.addWidget(self.source_type_combo)

        self._source_stack = QStackedWidget()
        self.ocr_combo = QComboBox()
        self._reload_ocr_nodes()
        self._source_stack.addWidget(self.ocr_combo)

        self.variable_combo = QComboBox()
        self.variable_combo.setEditable(True)
        self._reload_variables()
        self._source_stack.addWidget(self.variable_combo)

        self.logic_combo = QComboBox()
        self._reload_logic_nodes()
        self._source_stack.addWidget(self.logic_combo)
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

    def _reload_logic_nodes(self) -> None:
        self.logic_combo.clear()
        for node in self._graph.nodes.values():
            if node.type == "logic":
                label = describe_condition(node.props.get("condition")) or "(no condition)"
                self.logic_combo.addItem(label, node.id)
        if self.logic_combo.count() == 0:
            self.logic_combo.addItem("(no Logic blocks in this flow)", "")

    def set_condition(self, condition: dict) -> None:
        self.not_check.setChecked(bool(condition.get("negate")))

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

        if source_type == "logic":
            logic_idx = self.logic_combo.findData(condition.get("source_node", ""))
            if logic_idx >= 0:
                self.logic_combo.setCurrentIndex(logic_idx)

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
        elif source_type == "logic":
            result["source_node"] = self.logic_combo.currentData() or ""
            result["source_label"] = self.logic_combo.currentText()
        else:
            result["source_name"] = self.variable_combo.currentText().strip()
        result["operator"] = self.operator_combo.currentData()
        result["compare_to"] = self.compare_edit.text()
        result["negate"] = self.not_check.isChecked()
        return result


class ConditionEditorWidget(QWidget):
    """Builds a condition: one leaf clause by default, or - via "+ Add Condition" -
    several clauses combined with AND/OR, each independently negatable. "+"/"-" only
    ever append or remove the last clause. A single clause is returned as a plain
    leaf dict (unchanged shape from before compound conditions existed); 2+ clauses
    are wrapped as {"join": "and"|"or", "clauses": [...]}. Used by If's cases and by
    While/Until's loop condition."""

    def __init__(self, graph: Graph, condition: dict = None, parent=None):
        super().__init__(parent)
        self._graph = graph
        self._rows: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        join_row = QHBoxLayout()
        join_label = QLabel("Combine with:")
        join_row.addWidget(join_label)
        self.join_combo = QComboBox()
        self.join_combo.addItem("AND (all must be true)", "and")
        self.join_combo.addItem("OR (any must be true)", "or")
        join_row.addWidget(self.join_combo)
        join_row.addStretch()
        outer.addLayout(join_row)
        self._join_row_widgets = [join_label, self.join_combo]

        self._rows_layout = QVBoxLayout()
        outer.addLayout(self._rows_layout)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Condition")
        add_btn.clicked.connect(self._add_clause)
        btn_row.addWidget(add_btn)
        self._remove_btn = QPushButton("− Remove Last Condition")
        self._remove_btn.clicked.connect(self._remove_clause)
        btn_row.addWidget(self._remove_btn)
        outer.addLayout(btn_row)

        self.set_condition(condition or default_condition())

    def _add_clause(self, condition: dict = None) -> None:
        row = _ClauseRow(self._graph, condition)
        self._rows_layout.addWidget(row)
        self._rows.append(row)
        self._update_visibility()

    def _remove_clause(self) -> None:
        if len(self._rows) <= 1:
            return
        row = self._rows.pop()
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        self._update_visibility()

    def _update_visibility(self) -> None:
        multi = len(self._rows) > 1
        for w in self._join_row_widgets:
            w.setVisible(multi)
        self._remove_btn.setEnabled(multi)

    def set_condition(self, condition: dict) -> None:
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []

        condition = condition or default_condition()
        if is_compound(condition):
            join_idx = self.join_combo.findData(condition.get("join", "and"))
            self.join_combo.setCurrentIndex(max(join_idx, 0))
            clauses = condition.get("clauses") or [default_condition()]
            for c in clauses:
                self._add_clause(c)
        else:
            self.join_combo.setCurrentIndex(0)
            self._add_clause(condition)
        self._update_visibility()

    def get_condition(self) -> dict:
        if len(self._rows) == 1:
            return self._rows[0].get_condition()
        return {
            "join": self.join_combo.currentData(),
            "clauses": [row.get_condition() for row in self._rows],
        }


class SimpleConditionDialog(QDialog):
    """A dialog that's nothing but a condition editor - used by any node whose only
    configurable prop is a single "condition" (Logic, Exit Loop). `prompt` is shown
    above the editor to explain what the condition means for that node type."""

    def __init__(self, parent, graph: Graph, node: Node, title: str, prompt: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._node = node

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))
        self.condition_editor = ConditionEditorWidget(graph, node.props.get("condition"))
        layout.addWidget(self.condition_editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_node(self) -> None:
        self._node.props["condition"] = self.condition_editor.get_condition()

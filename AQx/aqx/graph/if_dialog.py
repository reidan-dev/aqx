from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .condition_editor import ConditionEditorWidget
from .model import Graph, Node
from .nodes import default_condition, if_output_ports


class IfDialog(QDialog):
    """Configure an If node: one or more conditional branches (If, then Elif 1, Elif
    2, ...) plus an always-present Else. "+"/"-" only ever append or remove the last
    Elif, so existing branch ports never need renumbering."""

    def __init__(self, parent, graph: Graph, node: Node):
        super().__init__(parent)
        self.setWindowTitle("If")
        self._node = node
        self._graph = graph
        self._case_rows: list = []  # (row_widget, ConditionEditorWidget)

        outer = QVBoxLayout(self)
        self._cases_layout = QVBoxLayout()
        outer.addLayout(self._cases_layout)

        cases = node.props.get("cases") or [default_condition()]
        for condition in cases:
            self._add_case_row(condition)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Elif")
        add_btn.clicked.connect(self._add_elif)
        btn_row.addWidget(add_btn)
        self._remove_btn = QPushButton("− Remove Last Elif")
        self._remove_btn.clicked.connect(self._remove_last_elif)
        btn_row.addWidget(self._remove_btn)
        outer.addLayout(btn_row)

        else_label = QLabel("Else: always present - runs when nothing above matched")
        else_label.setStyleSheet("color:#9aa4b2; font-style:italic;")
        outer.addWidget(else_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._update_remove_enabled()

    def _case_label(self, index: int) -> str:
        return "If:" if index == 0 else f"Elif {index}:"

    def _add_case_row(self, condition: dict) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 4, 0, 4)
        label = QLabel(self._case_label(len(self._case_rows)))
        label.setMinimumWidth(55)
        editor = ConditionEditorWidget(self._graph, condition)
        row_layout.addWidget(label)
        row_layout.addWidget(editor)
        self._cases_layout.addWidget(row)
        self._case_rows.append((row, editor))

    def _add_elif(self) -> None:
        self._add_case_row(default_condition())
        self._update_remove_enabled()

    def _remove_last_elif(self) -> None:
        if len(self._case_rows) <= 1:
            return
        row, _editor = self._case_rows.pop()
        self._cases_layout.removeWidget(row)
        row.deleteLater()
        self._update_remove_enabled()

    def _update_remove_enabled(self) -> None:
        self._remove_btn.setEnabled(len(self._case_rows) > 1)

    def apply_to_node(self) -> None:
        cases = [editor.get_condition() for (_row, editor) in self._case_rows]
        self._node.props["cases"] = cases
        self._node.outputs = if_output_ports(len(cases))

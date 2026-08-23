from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QSpinBox, QVBoxLayout

from .condition_editor import ConditionEditorWidget
from .model import Graph, Node


class LoopDialog(QDialog):
    """Configure a For / While / Until loop node."""

    def __init__(self, parent, graph: Graph, node: Node):
        super().__init__(parent)
        self._node = node
        layout = QVBoxLayout(self)

        if node.type == "for_loop":
            self.setWindowTitle("For Loop")
            layout.addWidget(QLabel("Repeat the body this many times:"))
            self.count_spin = QSpinBox()
            self.count_spin.setRange(0, 1_000_000)
            self.count_spin.setValue(int(node.props.get("count", 3)))
            layout.addWidget(self.count_spin)
            self.condition_editor = None
        else:
            verb = "is still false" if node.type == "until_loop" else "is true"
            self.setWindowTitle("Until Loop" if node.type == "until_loop" else "While Loop")
            layout.addWidget(QLabel(f"Repeat the body while this {verb} (checked before each pass):"))
            self.condition_editor = ConditionEditorWidget(graph, node.props.get("condition"))
            layout.addWidget(self.condition_editor)
            self.count_spin = None

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_node(self) -> None:
        if self.count_spin is not None:
            self._node.props["count"] = self.count_spin.value()
        if self.condition_editor is not None:
            self._node.props["condition"] = self.condition_editor.get_condition()

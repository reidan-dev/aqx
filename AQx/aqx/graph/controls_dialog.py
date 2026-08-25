from __future__ import annotations

from typing import List

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .model import Node


class ControlRow(QWidget):
    """One named control: a name, a comma-separated list of options, and which
    option is current. The Current dropdown always tracks whatever's typed into
    Options, so it never points at a choice that's since been removed."""

    def __init__(self, parent, name: str = "", options: List[str] = None, current: str = ""):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("battle-mode")
        self.name_edit.setMaximumWidth(140)
        layout.addWidget(self.name_edit)

        self.options_edit = QLineEdit(", ".join(options or []))
        self.options_edit.setPlaceholderText("abc, def, fgh")
        layout.addWidget(self.options_edit, stretch=1)

        self.current_combo = QComboBox()
        self.current_combo.setMinimumWidth(100)
        layout.addWidget(self.current_combo)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedWidth(28)
        self.remove_btn.setToolTip("Remove this control")
        layout.addWidget(self.remove_btn)

        self.options_edit.textChanged.connect(self._refresh_options)
        self._refresh_options()
        if current:
            idx = self.current_combo.findText(current)
            if idx >= 0:
                self.current_combo.setCurrentIndex(idx)

    def _parsed_options(self) -> List[str]:
        return [o.strip() for o in self.options_edit.text().split(",") if o.strip()]

    def _refresh_options(self) -> None:
        previous = self.current_combo.currentText()
        options = self._parsed_options()
        self.current_combo.blockSignals(True)
        self.current_combo.clear()
        self.current_combo.addItems(options)
        if previous in options:
            self.current_combo.setCurrentIndex(options.index(previous))
        self.current_combo.blockSignals(False)

    def to_entry(self) -> dict:
        options = self._parsed_options()
        current = self.current_combo.currentText() or (options[0] if options else "")
        return {"name": self.name_edit.text().strip(), "options": options, "current": current}


class ControlsDialog(QDialog):
    """Configure a Controls block: any number of named, multiple-choice values
    (e.g. "battle-mode" -> abc/def/fgh). Each becomes a variable a running flow can
    read, and shows up as a dropdown in the floating control so it can be changed
    mid-run - see nodes.HELP_TEXT["controls"] for the full picture."""

    def __init__(self, parent, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Controls")
        self.resize(520, 360)
        self._node = node
        self._rows: List[ControlRow] = []

        layout = QVBoxLayout(self)

        header = QLabel(
            "Each row is a variable other blocks can read by name. \"Current\" is what "
            "the flow sees when this run starts, and what the floating control shows "
            "while it's running - whatever you pick there is saved back here."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_container)
        layout.addWidget(scroll, stretch=1)

        for entry in node.props.get("controls") or []:
            self._add_row(entry.get("name", ""), entry.get("options", []), entry.get("current", ""))
        if not self._rows:
            self._add_row()

        add_btn = QPushButton("+ Add Control")
        add_btn.clicked.connect(lambda: self._add_row())
        layout.addWidget(add_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def closeEvent(self, event) -> None:
        # Closing the window (the titlebar's close button, or Escape) goes through
        # here, not through accept()/reject() - by default that would discard
        # whatever's been edited, same as Cancel. Treat it as OK instead; only the
        # explicit Cancel button should actually discard.
        self.accept()
        super().closeEvent(event)

    def _add_row(self, name: str = "", options: List[str] = None, current: str = "") -> None:
        row = ControlRow(self._rows_container, name, options, current)
        row.remove_btn.clicked.connect(lambda: self._remove_row(row))
        self._rows.append(row)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    def _remove_row(self, row: ControlRow) -> None:
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def apply_to_node(self) -> None:
        controls = []
        for row in self._rows:
            entry = row.to_entry()
            if entry["name"] and entry["options"]:
                controls.append(entry)
        self._node.props["controls"] = controls

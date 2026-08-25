from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..ocr.region import Region
from ..ocr.region_picker import RegionPicker
from .model import Node


class SkillRow(QWidget):
    """One skill slot: its row position (how other blocks and skills() in Code
    refer to it - row 1 is skills.s1, row 2 is skills.s2, and so on), which OCR
    region to watch (drawn once over just the cooldown number - an empty read
    means ready), and which key to tap when it's ready."""

    def __init__(self, parent, dialog: "SkillsDialog", region: str = "", key: str = ""):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.index_label = QLabel("s?")
        self.index_label.setStyleSheet("color:#4ec9b0; font-family:Menlo,monospace; font-weight:bold;")
        self.index_label.setFixedWidth(28)
        layout.addWidget(self.index_label)

        self.region_combo = QComboBox()
        self.region_combo.setMinimumWidth(140)
        self.reload_regions(select=region)
        layout.addWidget(self.region_combo, stretch=1)

        self.pick_btn = QPushButton("Pick Region...")
        self.pick_btn.clicked.connect(lambda: dialog.pick_region_for(self))
        layout.addWidget(self.pick_btn)

        self.key_edit = QLineEdit(key)
        self.key_edit.setPlaceholderText("1")
        self.key_edit.setMaximumWidth(60)
        layout.addWidget(self.key_edit)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedWidth(28)
        self.remove_btn.setToolTip("Remove this skill")
        layout.addWidget(self.remove_btn)

    def set_index(self, index: int) -> None:
        self.index_label.setText(f"s{index}")

    def reload_regions(self, select: str = "") -> None:
        names = Region.list_names()
        self.region_combo.clear()
        self.region_combo.addItems(names)
        if select and select in names:
            self.region_combo.setCurrentText(select)

    def to_entry(self) -> dict:
        return {
            "region": self.region_combo.currentText().strip(),
            "key": self.key_edit.text().strip(),
        }


class SkillsDialog(QDialog):
    """Configure the flow's Skills block (only one is allowed per flow): any
    number of skill slots, each watching a screen region for a cooldown number and
    tapping a key the moment it reads empty. A row's position is its identity - row
    1 is skills.s1 in a Code block, row 2 is skills.s2, and so on. Must stay
    non-modal (shown via .show(), never .exec()) - same reason as OCRNodeDialog: a
    modal QDialog on macOS blocks the region picker's own toolbar clicks."""

    def __init__(self, parent, node: Node):
        super().__init__(parent)
        self.setWindowTitle("Skills")
        self.resize(560, 360)
        self._node = node
        self._rows: List[SkillRow] = []
        self._picker: Optional[RegionPicker] = None
        self._picker_target: Optional[SkillRow] = None

        layout = QVBoxLayout(self)

        header = QLabel(
            "Each row watches one skill: an empty read of its region means ready (and "
            "taps Key), any text means still on cooldown. Row order is identity - "
            "a Code block calls skills() and gets skills.s1, skills.s2, ... in the "
            "same order as these rows (e.g. skills.s1.press(), or "
            "skills.press_ready([skills.s1, skills.s2]) for a priority scan)."
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

        for entry in node.props.get("skills") or []:
            self._add_row(entry.get("region", ""), entry.get("key", ""))
        if not self._rows:
            self._add_row()

        add_btn = QPushButton("+ Add Skill")
        add_btn.clicked.connect(lambda: self._add_row())
        layout.addWidget(add_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def closeEvent(self, event) -> None:
        # Closing the window (the titlebar's close button, or Escape) goes through
        # here, not through accept()/reject() - by default that would discard
        # whatever's been edited, same as Cancel. Treat it as OK instead, since a
        # region you just picked is already saved to disk regardless; only the
        # explicit Cancel button should actually discard.
        self.accept()
        super().closeEvent(event)

    def _add_row(self, region: str = "", key: str = "") -> None:
        row = SkillRow(self._rows_container, self, region, key)
        row.remove_btn.clicked.connect(lambda: self._remove_row(row))
        self._rows.append(row)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
        self._renumber_rows()

    def _remove_row(self, row: SkillRow) -> None:
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._renumber_rows()

    def _renumber_rows(self) -> None:
        for i, row in enumerate(self._rows, start=1):
            row.set_index(i)

    def _set_picking_busy(self, busy: bool) -> None:
        # Guards against launching two overlapping RegionPickers (each starts its
        # own pynput mouse listener) if a second row's Pick Region... is clicked
        # while one is still active.
        for row in self._rows:
            row.pick_btn.setEnabled(not busy)

    def pick_region_for(self, row: SkillRow) -> None:
        if self._picker is not None:
            return
        self._picker_target = row
        self._set_picking_busy(True)
        self._picker = RegionPicker(self)
        self._picker.region_selected.connect(self._on_region_selected)
        self._picker.cancelled.connect(self._on_region_cancelled)
        self._picker.start()

    def _on_region_selected(self, x: float, y: float, w: float, h: float) -> None:
        self._set_picking_busy(False)
        row = self._picker_target
        self._picker = None
        self._picker_target = None
        if row is None:
            return
        name, ok = QInputDialog.getText(self, "Save Region", "Name:")
        if ok and name.strip():
            name = name.strip()
            Region(name=name, x=x, y=y, width=w, height=h).save()
            for r in self._rows:
                if r is not row:
                    r.reload_regions(select=r.region_combo.currentText())
            row.reload_regions(select=name)

    def _on_region_cancelled(self) -> None:
        self._set_picking_busy(False)
        self._picker = None
        self._picker_target = None

    def apply_to_node(self) -> None:
        skills = []
        for row in self._rows:
            entry = row.to_entry()
            if entry["region"] and entry["key"]:
                skills.append(entry)
        self._node.props["skills"] = skills

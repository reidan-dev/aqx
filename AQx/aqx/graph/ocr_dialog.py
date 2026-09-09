from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.extract import apply_extract_pattern
from ..ocr.region import Region
from ..ocr.region_picker import RegionPicker


class OCRNodeDialog(QDialog):
    """Configure an OCR node: pick an existing region (reusable across every flow),
    or draw a new one, set the read interval, optionally extract just part of the
    reading with a regex, and preview what OCR currently reads (raw and extracted)."""

    def __init__(self, parent, region_name: str, interval_seconds: float, extract_pattern: str = ""):
        super().__init__(parent)
        self.setWindowTitle("OCR")
        # This dialog must stay fully non-modal (shown via .show(), never .exec()):
        # on macOS, any modal QDialog - window-modal or application-modal alike -
        # triggers a native Cocoa modal session that blocks mouse input to every
        # other window in the app at the OS level, regardless of Qt's own modality
        # type. That would make the region picker's toolbar unclickable while this
        # dialog is open. Confirmed directly: window-modal still blocked real
        # synthetic clicks on the toolbar's Select Area button.
        self.selected_region = region_name
        self.selected_interval = interval_seconds
        self.selected_extract_pattern = extract_pattern

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Region:"))
        self.combo = QComboBox()
        self._reload_regions(select=region_name)
        layout.addWidget(self.combo)

        region_btn_row = QHBoxLayout()
        self.select_btn = QPushButton("Select New Region...")
        self.select_btn.clicked.connect(self._select_new_region)
        region_btn_row.addWidget(self.select_btn)
        self.replace_btn = QPushButton("Replace This Region...")
        self.replace_btn.setToolTip("Redraw the area for the selected region from scratch, keeping its name (and anywhere it's referenced) unchanged.")
        self.replace_btn.clicked.connect(self._replace_current_region)
        region_btn_row.addWidget(self.replace_btn)
        self.view_edit_btn = QPushButton("View / Edit Region...")
        self.view_edit_btn.setToolTip("Shows this region's outline live over the real screen, with drag handles so you can resize and reposition it in place instead of redrawing from scratch.")
        self.view_edit_btn.clicked.connect(self._view_edit_region)
        region_btn_row.addWidget(self.view_edit_btn)
        layout.addLayout(region_btn_row)
        self.combo.currentTextChanged.connect(lambda _: self._update_region_buttons_enabled())
        self._update_region_buttons_enabled()

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Read every (seconds):"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 3600.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setValue(interval_seconds)
        interval_row.addWidget(self.interval_spin)
        layout.addLayout(interval_row)
        hint = QLabel(
            "This is a delay after each read, not a background timer. To poll\n"
            "continuously, set the flow's Loops to 0 (infinite) in the toolbar."
        )
        hint.setStyleSheet("color:#9aa4b2; font-size:11px;")
        layout.addWidget(hint)

        layout.addWidget(QLabel("Extract pattern (optional regex, e.g. (\\d+)/ on \"Items 23/300\" gets \"23\"):"))
        self.extract_edit = QLineEdit(extract_pattern)
        self.extract_edit.setPlaceholderText("leave empty to use the raw text as-is")
        layout.addWidget(self.extract_edit)

        test_btn = QPushButton("Test Read")
        test_btn.clicked.connect(self._test_read)
        layout.addWidget(test_btn)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color:#9aa4b2;")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def closeEvent(self, event) -> None:
        # Closing the window (the titlebar's close button, or Escape) goes through
        # here, not through accept()/reject() - by default that would discard
        # whatever's been edited, same as Cancel. Treat it as OK instead, since the
        # region itself is already saved to disk regardless; only the explicit
        # Cancel button should actually discard.
        self.accept()
        super().closeEvent(event)

    def _reload_regions(self, select: str = "") -> None:
        names = Region.list_names()
        self.combo.clear()
        self.combo.addItems(names)
        if select and select in names:
            self.combo.setCurrentText(select)

    def _update_region_buttons_enabled(self) -> None:
        has_region = bool(self.combo.currentText().strip())
        self.replace_btn.setEnabled(has_region)
        self.view_edit_btn.setEnabled(has_region)

    def _set_pick_busy(self, busy: bool) -> None:
        # Guards against launching two overlapping RegionPickers (each starts its own
        # pynput mouse listener) if the user clicks a second picker button while one
        # is still active.
        has_region = bool(self.combo.currentText().strip())
        self.select_btn.setEnabled(not busy)
        self.replace_btn.setEnabled(not busy and has_region)
        self.view_edit_btn.setEnabled(not busy and has_region)

    def _select_new_region(self) -> None:
        # Deliberately not hiding this dialog - the toolbar is freely draggable, so
        # the user can move it (and this dialog) out of the way themselves if needed.
        self._set_pick_busy(True)
        self._picker = RegionPicker(self)
        self._picker.region_selected.connect(self._on_new_region_selected)
        self._picker.cancelled.connect(self._on_region_pick_cancelled)
        self._picker.start()

    def _on_new_region_selected(self, x: float, y: float, w: float, h: float) -> None:
        self._set_pick_busy(False)
        name, ok = QInputDialog.getText(self, "Save Region", "Name:")
        if ok and name.strip():
            name = name.strip()
            Region(name=name, x=x, y=y, width=w, height=h).save()
            self._reload_regions(select=name)

    def _replace_current_region(self) -> None:
        """Redraws the area for whatever region is currently selected, saving it back
        under the exact same name - no rename prompt, so every other block that
        references this region by name keeps working unchanged."""
        name = self.combo.currentText().strip()
        if not name:
            return
        self._set_pick_busy(True)
        self._picker = RegionPicker(self)
        self._picker.region_selected.connect(lambda x, y, w, h, name=name: self._on_region_replaced(name, x, y, w, h))
        self._picker.cancelled.connect(self._on_region_pick_cancelled)
        self._picker.start()

    def _on_region_replaced(self, name: str, x: float, y: float, w: float, h: float) -> None:
        self._set_pick_busy(False)
        Region(name=name, x=x, y=y, width=w, height=h).save()
        self._reload_regions(select=name)
        self.result_label.setText(f"Replaced region '{name}'.")

    def _view_edit_region(self) -> None:
        """Like _replace_current_region, but starts the picker already showing this
        region's saved rect - live over the real screen, with resize handles active
        immediately - instead of making the user redraw it from scratch just to
        nudge it into place."""
        name = self.combo.currentText().strip()
        if not name:
            return
        region = Region.load(name)
        rect = QRect(int(region.x), int(region.y), int(region.width), int(region.height))
        self._set_pick_busy(True)
        self._picker = RegionPicker(self)
        self._picker.region_selected.connect(lambda x, y, w, h, name=name: self._on_region_replaced(name, x, y, w, h))
        self._picker.cancelled.connect(self._on_region_pick_cancelled)
        self._picker.edit(rect)

    def _on_region_pick_cancelled(self) -> None:
        self._set_pick_busy(False)

    def _test_read(self) -> None:
        name = self.combo.currentText()
        if not name:
            QMessageBox.information(self, "AQx", "Select or create a region first.")
            return
        region = Region.load(name)
        image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
        text = read_text_from_cgimage(image_ref)
        raw_line = f"Raw: {text!r}" if text is not None else "Raw: None (no text detected)"

        pattern = self.extract_edit.text().strip()
        if not pattern:
            self.result_label.setText(raw_line)
            return
        extracted, error = apply_extract_pattern(text, pattern)
        if error:
            self.result_label.setText(f"{raw_line}\nInvalid pattern: {error}")
        else:
            self.result_label.setText(f"{raw_line}\nExtracted: {extracted!r}")

    def accept(self) -> None:
        self.selected_region = self.combo.currentText()
        self.selected_interval = self.interval_spin.value()
        self.selected_extract_pattern = self.extract_edit.text().strip()
        super().accept()

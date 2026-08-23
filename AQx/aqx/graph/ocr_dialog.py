from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..ocr.capture import capture_cgimage
from ..ocr.engine import read_text_from_cgimage
from ..ocr.region import Region
from ..ocr.region_picker import RegionPicker


class OCRNodeDialog(QDialog):
    """Configure an OCR node: pick an existing region (reusable across every flow),
    or draw a new one, set the read interval, and preview what OCR currently reads."""

    def __init__(self, parent, region_name: str, interval_seconds: float):
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

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Region:"))
        self.combo = QComboBox()
        self._reload_regions(select=region_name)
        layout.addWidget(self.combo)

        region_btn_row = QHBoxLayout()
        select_btn = QPushButton("Select New Region...")
        select_btn.clicked.connect(self._select_new_region)
        region_btn_row.addWidget(select_btn)
        layout.addLayout(region_btn_row)

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

    def _reload_regions(self, select: str = "") -> None:
        names = Region.list_names()
        self.combo.clear()
        self.combo.addItems(names)
        if select and select in names:
            self.combo.setCurrentText(select)

    def _select_new_region(self) -> None:
        # Deliberately not hiding this dialog - the toolbar is freely draggable, so
        # the user can move it (and this dialog) out of the way themselves if needed.
        self._picker = RegionPicker(self)
        self._picker.region_selected.connect(self._on_region_selected)
        self._picker.cancelled.connect(self._on_region_pick_cancelled)
        self._picker.start()

    def _on_region_selected(self, x: float, y: float, w: float, h: float) -> None:
        name, ok = QInputDialog.getText(self, "Save Region", "Name:")
        if ok and name.strip():
            name = name.strip()
            Region(name=name, x=x, y=y, width=w, height=h).save()
            self._reload_regions(select=name)

    def _on_region_pick_cancelled(self) -> None:
        pass

    def _test_read(self) -> None:
        name = self.combo.currentText()
        if not name:
            QMessageBox.information(self, "AQx", "Select or create a region first.")
            return
        region = Region.load(name)
        image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
        text = read_text_from_cgimage(image_ref)
        self.result_label.setText(f"Read: {text!r}" if text is not None else "Read: None (no text detected)")

    def accept(self) -> None:
        self.selected_region = self.combo.currentText()
        self.selected_interval = self.interval_spin.value()
        super().accept()

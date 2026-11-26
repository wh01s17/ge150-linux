"""System settings and rhythm box dialog (plan.md §2.11).

Every change is sent at once; the pedal stores these settings by itself (they are not
part of a preset), so there is nothing to save.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model import settings
from ge150.model.settings import RhythmValues, SystemValues
from ge150.services.editor import Editor


def _combo(options: tuple[str, ...]) -> QComboBox:
    combo = QComboBox()
    combo.addItems(list(options))
    return combo


def _spin(low: int, high: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setKeyboardTracking(False)
    if suffix:
        spin.setSuffix(suffix)
    return spin


class SettingsDialog(QDialog):
    def __init__(self, editor: Editor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        self._loading = False
        self.setWindowTitle(tr("Sistema y caja de ritmos"))
        layout = QVBoxLayout(self)

        self.system_box = QGroupBox("SYSTEM")
        form = QFormLayout(self.system_box)
        self.input_level = _combo(settings.LEVELS)
        self.screen = _spin(*settings.SCREEN_RANGE)
        self.cab_left = _combo(settings.CAB_THRU)
        self.cab_right = _combo(settings.CAB_THRU)
        self.otg_level = _combo(settings.LEVELS)
        form.addRow("INPUT LEVEL", self.input_level)
        form.addRow("SCREEN", self.screen)
        form.addRow(tr("CAB SIM THRU izquierda"), self.cab_left)
        form.addRow(tr("CAB SIM THRU derecha"), self.cab_right)
        form.addRow("OTG LEVEL", self.otg_level)
        layout.addWidget(self.system_box)

        self.rhythm_box = QGroupBox("RHYTHM")
        form = QFormLayout(self.rhythm_box)
        self.rhythm_on = QCheckBox(tr("Encendida"))
        self.mode = _combo(settings.RHYTHM_MODES)
        self.style = _spin(1, settings.STYLE_COUNT[0])
        self.bpm = _spin(*settings.BPM_RANGE, " BPM")
        self.volume = _spin(*settings.VOLUME_RANGE)
        form.addRow(tr("Estado"), self.rhythm_on)
        form.addRow("MODE", self.mode)
        form.addRow("STYLE", self.style)
        form.addRow("BPM", self.bpm)
        form.addRow("VOL", self.volume)
        layout.addWidget(self.rhythm_box)

        info = QLabel(tr("Los cambios se envían al instante y la pedalera los conserva."))
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load()
        for combo in (self.input_level, self.cab_left, self.cab_right, self.otg_level):
            combo.currentIndexChanged.connect(self._system_changed)
        self.screen.valueChanged.connect(self._system_changed)
        self.rhythm_on.toggled.connect(self._rhythm_changed)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for spin in (self.style, self.bpm, self.volume):
            spin.valueChanged.connect(self._rhythm_changed)

    def load(self) -> None:
        """Show the values last read from the pedal."""
        self._loading = True
        system = self.editor.system_values()
        self.system_box.setEnabled(system is not None)
        if system is not None:
            self.input_level.setCurrentIndex(system.input_level)
            self.screen.setValue(system.screen)
            self.cab_left.setCurrentIndex(int(system.cab_left_on))
            self.cab_right.setCurrentIndex(int(system.cab_right_on))
            self.otg_level.setCurrentIndex(system.otg_level)
        rhythm = self.editor.rhythm_values()
        self.rhythm_box.setEnabled(rhythm is not None)
        if rhythm is not None:
            self.rhythm_on.setChecked(rhythm.on)
            self.mode.setCurrentIndex(rhythm.mode)
            self.style.setMaximum(settings.STYLE_COUNT.get(rhythm.mode, 40))
            self.style.setValue(rhythm.style + 1)
            self.bpm.setValue(rhythm.bpm)
            self.volume.setValue(rhythm.volume)
        self._loading = False

    def system_values(self) -> SystemValues:
        return SystemValues(
            input_level=self.input_level.currentIndex(),
            screen=self.screen.value(),
            cab_left_on=bool(self.cab_left.currentIndex()),
            cab_right_on=bool(self.cab_right.currentIndex()),
            otg_level=self.otg_level.currentIndex(),
        )

    def rhythm_values(self) -> RhythmValues:
        return RhythmValues(
            on=self.rhythm_on.isChecked(),
            mode=self.mode.currentIndex(),
            style=self.style.value() - 1,
            volume=self.volume.value(),
            bpm=self.bpm.value(),
        )

    def _system_changed(self, *_: object) -> None:
        if not self._loading:
            self.editor.set_system_values(self.system_values())

    def _mode_changed(self, mode: int) -> None:
        if self._loading:
            return
        self._loading = True  # the style list changes with the mode: start from the first
        self.style.setMaximum(settings.STYLE_COUNT.get(mode, 40))
        self.style.setValue(1)
        self._loading = False
        self._rhythm_changed()

    def _rhythm_changed(self, *_: object) -> None:
        if not self._loading:
            self.editor.set_rhythm_values(self.rhythm_values())

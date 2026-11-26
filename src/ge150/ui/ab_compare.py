"""A/B bar of the AI panel: switch the pedal between the preset as saved on the pedal and
the sound the AI suggested, to compare them by playing.

Both sounds are whole edit buffers. Switching writes only the modules that differ, under the
pedal lock (off the GUI thread). Nothing is saved: the user keeps whichever is on by pressing
Save. The bar disappears when another preset is loaded or a new request starts.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ge150.i18n import tr
from ge150.services import tone_ai
from ge150.ui.ai_host import AiHost

ORIGINAL, SUGGESTED = 0, 1


class _Done(QObject):
    done = Signal(int, str)  # which sound is on now, error


class SoundCompare(QFrame):
    def __init__(self, host: AiHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("soundCompare")
        self.host = host
        self.sounds: tuple[list[int], list[int]] | None = None
        self.slot: int | None = None
        self._done = _Done(self)
        self._done.done.connect(self._switched)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        label = QLabel(tr("Comparar"))
        label.setObjectName("note")
        self.original = QPushButton(tr("Sonido original"))
        self.suggested = QPushButton(tr("Sonido sugerido"))
        self.group = QButtonGroup(self)
        for which, button in ((ORIGINAL, self.original), (SUGGESTED, self.suggested)):
            button.setObjectName("abButton")
            button.setCheckable(True)
            self.group.addButton(button, which)
        self.original.setToolTip(tr("El preset tal como está guardado en la pedalera"))
        self.suggested.setToolTip(tr("Lo que propuso la IA"))
        self.group.idClicked.connect(self.switch)
        layout.addWidget(label)
        layout.addWidget(self.original, 1)
        layout.addWidget(self.suggested, 1)
        self.hide()

    def offer(self, before: list[int], after: list[int], slot: int | None) -> None:
        """Show the bar if the AI changed the sound (the suggested one is on)."""
        if before == after:
            self.clear()
            return
        self.sounds, self.slot = (before, after), slot
        self.suggested.setChecked(True)
        self.show()

    def clear(self) -> None:
        self.sounds = None
        self.hide()

    def preset_changed(self, slot: int | None, stored: list[int] | None) -> None:
        """Another preset loaded: hide. The preset saved: the saved sound is the new original
        (if the suggestion was saved, there is nothing left to compare)."""
        if self.sounds is None:
            return
        if slot != self.slot:
            self.clear()
        elif stored is not None and stored != self.sounds[ORIGINAL]:
            if stored == self.sounds[SUGGESTED]:
                self.clear()
            else:
                self.sounds = (stored, self.sounds[SUGGESTED])

    def switch(self, which: int) -> None:
        if self.sounds is None:
            return
        words = self.sounds[which]
        tools = self.host.tools
        self.setEnabled(False)

        def work() -> None:
            try:
                with tools.lock:
                    tone_ai.restore_words(tools.editor, words)
            except Exception as exc:  # shown by the panel
                self._done.done.emit(1 - which, str(exc))
                return
            self._done.done.emit(which, "")

        threading.Thread(target=work, name="ge150-compare", daemon=True).start()

    def _switched(self, which: int, error: str) -> None:
        self.setEnabled(True)
        button = self.group.button(which)
        if button is not None:
            button.setChecked(True)
        if error:
            self.setToolTip(tr("No se pudo cambiar: {error}", error=error))

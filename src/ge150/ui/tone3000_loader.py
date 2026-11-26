"""Bring a TONE3000 IR into a user IR slot (TONE3000 tab and user IR dialog).

Downloads run off the GUI thread; storing asks the user, with the author and licence in
view, and then goes through the pedal's runner.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ge150.formats import wav_ir
from ge150.i18n import tr
from ge150.protocol import messages as msg
from ge150.protocol.opcodes import Op
from ge150.services.editor import Editor
from ge150.services.tone3000 import Download, IrSources, Tone3000Client
from ge150.ui.worker import TaskRunner


class Tone3000Loader(QObject):
    #: slot index (0-based) once stored.
    stored = Signal(int)
    failed = Signal(str)
    status = Signal(str)
    _done = Signal(object, str, int)  # result, error, slot

    def __init__(self, editor: Editor, runner: TaskRunner, parent: QWidget) -> None:
        super().__init__(parent)
        self.editor, self.runner, self.window = editor, runner, parent
        self.sources = IrSources()
        self.client = Tone3000Client()
        self._done.connect(self._ready)

    def ensure_key(self) -> bool:
        if self.client.creds.client_id:
            return True
        key, ok = QInputDialog.getText(
            self.window, "TONE3000", tr("Clave publicable (Publishable Key, t3k_pub_…) de los "
            "ajustes de API de tu cuenta de TONE3000:"))
        if not ok or not key.strip():
            return False
        self.client.set_client_id(key)
        return True

    def background(self, fn: Callable[[], Any], slot: int = -1) -> None:
        """Run ``fn`` off the GUI thread (not on the pedal runner: it may take minutes)."""

        def work() -> None:
            try:
                result, error = fn(), ""
            except Exception as exc:  # shown to the user
                result, error = None, str(exc)
            self._done.emit(result, error, slot)

        threading.Thread(target=work, name="ge150-tone3000", daemon=True).start()

    def add(self, tone_id: int, model_id: int | None, slot: int) -> None:
        """Download a tone chosen in the app and store it in ``slot`` (after confirming)."""
        self.status.emit(tr("Descargando la IR…"))
        self.background(lambda: self.client.download(tone_id, model_id), slot)

    def pick(self, slot: int) -> None:
        if not self.ensure_key():
            return
        self.status.emit(tr("Elige una IR en TONE3000 (se abrió el navegador)…"))
        self.background(lambda: self.client.download(self.client.select_tone()), slot)

    def _ready(self, result: object, error: str, slot: int) -> None:
        self.status.emit("")
        if error and slot >= -2:  # -3: optional background refresh, silent
            self.failed.emit(f"TONE3000: {error}")
        elif isinstance(result, Download):
            self._store(result, slot)

    def _store(self, download: Download, slot: int) -> None:
        try:
            payload = wav_ir.load_ir_file(download.path)
        except (OSError, wav_ir.WavError) as exc:
            self.failed.emit(str(exc))
            return
        tone = download.tone
        names = self.editor.state.user_names
        old = names.ir[slot] if names else "?"
        question = tr(
            "¿Cargar «{title} / {model}» en la ranura IR {slot} (CAB {cab})?\n"
            "Reemplaza «{old}».\n\nAutor: {author}\nLicencia: {license}\n{url}",
            title=tone.title, model=download.model.name, slot=slot + 1, cab=27 + slot,
            old=old, author=tone.author, license=tone.license, url=tone.page_url,
        )
        answer = QMessageBox.question(self.window, tr("Confirmar"), question)
        if answer != QMessageBox.StandardButton.Yes:
            return
        name = tone.title[: msg.USER_NAME_LEN]

        def done(_: object) -> None:
            self.sources.record(slot + 1, download, name)
            self.stored.emit(slot)

        self.runner.run(lambda: self.editor.upload_user_slot(Op.USER_IR, slot, name, payload),
                        done, self.failed.emit)

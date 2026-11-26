"""Secondary dialogs: user IR slots, choosing one to overwrite and expression-pedal settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ge150.formats import wav_ir
from ge150.i18n import tr
from ge150.protocol import messages as msg
from ge150.protocol.opcodes import Op
from ge150.services.editor import Editor
from ge150.services.tone3000 import IrSources
from ge150.ui.worker import TaskRunner


class IrDialog(QDialog):
    """List the 10 user IR slots; upload a WAV, delete a slot or export all to WAV."""

    def __init__(self, editor: Editor, runner: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor, self.runner = editor, runner
        self.setWindowTitle(tr("IR de usuario (CAB 27-36)"))
        self.resize(560, 380)
        self.sources = IrSources()
        from ge150.ui.tone3000_loader import Tone3000Loader

        self.loader = Tone3000Loader(editor, runner, self)
        self.loader.stored.connect(lambda _: self.refresh())
        self.loader.failed.connect(self._failed)

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.upload = QPushButton(tr("Cargar WAV…"))
        self.tone3000 = QPushButton("TONE3000…")
        self.tone3000.setToolTip(tr(
            "Elegir una IR en el catálogo de TONE3000 y cargarla en la ranura",
        ))
        self.delete = QPushButton(tr("Borrar"))
        self.export = QPushButton(tr("Exportar todos…"))
        for b in (self.upload, self.tone3000, self.delete, self.export):
            buttons.addWidget(b)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.loader.status.connect(self.status.setText)
        layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.upload.clicked.connect(self._upload)
        self.tone3000.clicked.connect(lambda: self.loader.pick(self._slot()))
        self.delete.clicked.connect(self._delete)
        self.export.clicked.connect(self._export)
        runner.busy_changed.connect(self._busy)
        self.refresh()

    def refresh(self) -> None:
        names = self.editor.state.user_names
        sources = self.sources.matching(names.ir) if names else {}
        self.list.clear()
        for i in range(msg.USER_SLOTS):
            name = names.ir[i] if names else "?"
            text = f"IR {i + 1:2d}  (CAB {27 + i})   {name}"
            source = sources.get(str(i + 1))
            if source:
                text += f"   · TONE3000: {source['author']} ({source['license']})"
            self.list.addItem(text)
            if source:
                self.list.item(i).setToolTip(f"{source['title']} / {source['model']}\n"
                                             f"{source['author']} · {source['license']}\n"
                                             f"{source['url']}")
        if self.list.count():
            self.list.setCurrentRow(0)

    def _busy(self, busy: bool) -> None:
        for b in (self.upload, self.tone3000, self.delete, self.export):
            b.setEnabled(not busy)
        self.status.setText("Trabajando…" if busy else "")

    def _slot(self) -> int:
        return max(0, self.list.currentRow())

    def _failed(self, message: str) -> None:
        QMessageBox.warning(self, "Error", message)

    def _upload(self) -> None:
        slot = self._slot()
        path, _ = QFileDialog.getOpenFileName(self, tr("Elegir IR"), "", "WAV (*.wav)")
        if not path:
            return
        try:
            payload = wav_ir.load_ir_file(path)
        except (OSError, wav_ir.WavError) as exc:
            self._failed(str(exc))
            return
        name = Path(path).stem[: msg.USER_NAME_LEN]
        answer = QMessageBox.question(self, tr("Confirmar"),
                                      tr(
                                          "¿Reemplazar la ranura IR {slot} por «{name}»?",
                                          slot=slot + 1,
                                          name=name,
                                      ))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runner.run(lambda: self.editor.upload_user_slot(Op.USER_IR, slot, name, payload),
                        lambda _: self.refresh(), self._failed)

    def _delete(self) -> None:
        slot = self._slot()
        answer = QMessageBox.question(self, tr(
            "Confirmar",
        ), tr(
            "¿Borrar la ranura IR {slot}?",
            slot=slot + 1,
        ))
        if answer != QMessageBox.StandardButton.Yes:
            return
        def deleted(_: object) -> None:
            self.sources.forget(slot + 1)
            self.refresh()

        self.runner.run(lambda: self.editor.delete_user_slot(Op.USER_IR, slot), deleted,
                        self._failed)

    def _export(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, tr("Carpeta de destino"))
        if not directory:
            return

        def done(chunks: list[msg.BackupChunk]) -> None:
            for chunk in chunks:
                clean = "".join(c if c.isalnum() or c in " -_" else "_" for c in chunk.name)
                path = Path(directory) / f"IR{(chunk.slot or 0) + 1:02d} {clean.strip()}.wav"
                path.write_bytes(wav_ir.ir_to_wav(wav_ir.unpack_planar(chunk.data)))
            self.status.setText(tr(
                "{count} IR exportados a {directory}",
                count=len(chunks),
                directory=directory,
            ))

        self.runner.run(self.editor.read_ir_backup, done, self._failed)


class IrSlotPicker(QDialog):
    """The AI wants to store an IR and every slot is used: the user picks one to overwrite."""

    def __init__(self, question: str, slots: list[dict[str, Any]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Asistente IA: elegir ranura de IR"))
        self.resize(600, 420)
        layout = QVBoxLayout(self)
        label = QLabel(question)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.list = QListWidget()
        for entry in slots:
            text = f"IR {entry['slot']:2d}  ({entry['model']})   {entry['name']}"
            source = entry.get("tone3000")
            if source:
                text += f"   · TONE3000: {source['author']} ({source['license']})"
            users = entry.get("used_by_presets") or []
            text += tr(
                "\n          usada en {count} preset{plural}",
                count=len(users),
                plural='s' * (len(users) != 1),
            ) \
                if users else tr("\n          ningún preset la usa")
            self.list.addItem(text)
            if users:
                shown = ", ".join(f"{n:03d}" for n in users[:20])
                self.list.item(self.list.count() - 1).setToolTip(
                    f"Presets: {shown}{' …' if len(users) > 20 else ''}")
        # The least used slot first: overwriting it changes the fewest presets.
        fewest = min(range(len(slots)), key=lambda i: len(slots[i].get("used_by_presets") or []),
                     default=0)
        self.list.setCurrentRow(fewest)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list, 1)
        note = QLabel(tr("Los presets que usan la ranura elegida sonarán con la nueva IR."))
        note.setObjectName("note")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("Cancelar"), QDialogButtonBox.ButtonRole.RejectRole)
        self.overwrite = buttons.addButton(tr("Reemplazar"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.overwrite.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.slots = slots

    def chosen(self) -> int:
        """Slot 1..10, or 0 when cancelled."""
        if self.result() != QDialog.DialogCode.Accepted or self.list.currentRow() < 0:
            return 0
        return int(self.slots[self.list.currentRow()]["slot"])


"""«Cambios pendientes»: stored-preset changes (from the AI or the form) waiting for review.

One row per preset with a check box, its changes per module (before → after) and any
warning underneath. A new proposal is played at once: the pedal loads that preset with the
proposed sound (live, not saved), and the bar on top switches between «Antes» and «Después»;
clicking another preset plays that one. «Aplicar» writes the ticked presets on the runner
thread; «Deshacer última edición» writes back the versions saved before (the click is the
confirmation).
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model.preset import Preset
from ge150.services import auto_backup, bulk_edit
from ge150.services.bulk_edit import PresetEdit
from ge150.services.editor import Editor
from ge150.ui.worker import TaskRunner

WARN, BLOCKED, MUTED = "#f0c860", "#ff8a80", "#8b95a3"


class BulkReview(QWidget):
    #: a message for the user (status line of the tab)
    message = Signal(str)
    #: the pending edits (or their summary) changed: worth keeping with the conversation
    edits_changed = Signal()

    def __init__(self, editor: Editor, runner: TaskRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor, self.runner = editor, runner
        self.edits: dict[int, PresetEdit] = {}
        self.previous: list[Preset] = []
        self.playing: int | None = None  # the preset heard on the pedal (live preview)
        self.playing_after = True
        self.summary_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel(tr("Cambios pendientes"))
        title.setObjectName("cardTitle")
        self.badge = QLabel()
        self.badge.setObjectName("countBadge")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.badge)
        layout.addLayout(head)
        self.summary = QLabel()
        self.summary.setObjectName("note")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.bar = QFrame()
        self.bar.setObjectName("previewBar")
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(10, 6, 6, 6)
        self.playing_label = QLabel()
        self.playing_label.setObjectName("previewLabel")
        self.playing_label.setWordWrap(True)
        bar.addWidget(self.playing_label, 1)
        self.sides = QButtonGroup(self)
        for i, (text, tip) in enumerate(((tr("Antes"), tr("Como está guardado en la pedalera")),
                                         (tr("Después"), tr("Con los cambios propuestos")))):
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sides.addButton(button, i)
            bar.addWidget(button)
        self.sides.idClicked.connect(lambda i: self.listen(self.playing or 0, bool(i)))
        self.bar.hide()
        layout.addWidget(self.bar)
        self.tree = QTreeWidget()
        self.tree.setObjectName("bulkTree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([tr("Preset / módulo"), tr("Antes"), tr("Después")])
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(lambda *_: self._count())
        self.tree.itemClicked.connect(self._clicked)
        self.tree.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tree.setToolTip(tr("Toca un preset para escucharlo en la pedalera"))
        layout.addWidget(self.tree, 1)
        self.empty = QLabel(tr("<p style='font-size:28px; margin:0'>⇄</p>"
                            "<p><b>Sin cambios pendientes</b></p>"
                            "<p>Lo que propone la IA o armas en la edición manual aparece aquí, "
                            "preset por preset, con el valor de antes y el de después.</p>"
                            "<p>Nada se escribe en la pedalera hasta que aprietes "
                            "<b>Aplicar</b>.</p>"))
        self.empty.setObjectName("emptyState")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty, 1)
        row = QHBoxLayout()
        self.discard = QPushButton(tr("Descartar"))
        self.discard.clicked.connect(self.clear)
        self.apply_button = QPushButton(tr("Aplicar"))
        self.apply_button.setObjectName("saveButton")
        self.apply_button.clicked.connect(self.apply)
        row.addWidget(self.discard)
        row.addStretch(1)
        row.addWidget(self.apply_button)
        layout.addLayout(row)
        self.undo_button = QPushButton(tr("↶  Deshacer la última edición aplicada"))
        self.undo_button.setObjectName("linkButton")
        self.undo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_button.setToolTip(tr(
            "Vuelve a escribir los presets como estaban antes de aplicar",
        ))
        self.undo_button.clicked.connect(self.undo)
        layout.addWidget(self.undo_button, 0, Qt.AlignmentFlag.AlignRight)
        runner.busy_changed.connect(lambda _: self._count())
        self._render()

    # --- content ----------------------------------------------------------------------

    def bases(self) -> dict[int, list[int]]:
        """Planned words per preset: a new change for the same preset adds to these."""
        return {slot: edit.after for slot, edit in self.edits.items() if not edit.blocked}

    def add(self, edits: Sequence[PresetEdit], summary: str = "") -> None:
        """Add (or replace) pending changes and play the first one on the pedal."""
        added = [e for e in edits if e.changed or e.blocked]
        for edit in added:
            old = self.edits.get(edit.slot)
            if edit.stacked and old is not None:  # built on top of it: keep its name/source
                edit.new_name = edit.new_name or old.new_name
                edit.source = edit.source or old.source
            self.edits[edit.slot] = edit
        if summary:
            self.summary_text = summary
            self.summary.setText(html.escape(summary))
        self._render()
        self.edits_changed.emit()
        playable = [e for e in added if e.changed and not e.blocked]
        if playable and not self.listen(playable[0].slot, True, ask=False):
            self.message.emit(tr("Toca un preset en Cambios pendientes para escucharlo en la "
                              "pedalera."))

    def clear(self) -> None:
        """«Descartar»: forget the changes; a preset being heard goes back to its saved sound."""
        playing = self.edits.get(self.playing or 0)
        self.edits.clear()
        self.summary_text = ""
        self.summary.setText("")
        self._stop_playing(playing)
        self._render()
        self.edits_changed.emit()

    def restore(self, edits: Sequence[PresetEdit], summary: str) -> None:
        """Show the edits kept with a conversation (nothing is played on the pedal)."""
        playing = self.edits.get(self.playing or 0)
        self.edits = {e.slot: e for e in edits}
        self.summary_text = summary
        self.summary.setText(html.escape(summary))
        self._stop_playing(playing)
        self._render()

    # --- hearing a proposal on the pedal ---------------------------------------------------

    def _clicked(self, item: QTreeWidgetItem) -> None:
        slot = item.data(0, Qt.ItemDataRole.UserRole)
        if slot is not None and int(slot) != self.playing:
            self.listen(int(slot), True)

    def listen(self, slot: int, after: bool = True, ask: bool = True) -> bool:
        """Load ``slot`` on the pedal with its proposed (``after``) or saved sound. If the
        loaded preset has unsaved changes of the user's, ask first (or give up, ``ask``
        False). Returns False when it did not start."""
        edit = self.edits.get(slot)
        if edit is None or edit.blocked:  # a busy runner just queues it after the rest
            return False
        state = self.editor.state
        current = state.current_slot or 0
        if current != slot and state.dirty and not self._only_previewed(current):
            if not ask:
                return False
            loaded = state.presets.get(current)
            answer = QMessageBox.question(
                self, tr("Escuchar en la pedalera"),
                tr(
                    "El preset cargado ({current:03d} «{name}») tiene cambios sin guardar.\n"
                    "¿Descartarlos para escuchar {slot:03d} «{preview_name}»?",
                    current=current,
                    name=loaded.name if loaded else '',
                    slot=slot,
                    preview_name=edit.name,
                ))
            if answer != QMessageBox.StandardButton.Yes:
                return False

        def done(_: object) -> None:
            self.playing, self.playing_after = slot, after
            self._render()

        self.runner.run(lambda: bulk_edit.preview(self.editor, edit, after), done, self._failed)
        return True

    def _only_previewed(self, slot: int) -> bool:
        """The loaded preset's changes are just one of our previews."""
        edit = self.edits.get(slot)
        return edit is not None and not bulk_edit.own_changes(self.editor, slot, edit.after)

    def _stop_playing(self, edit: PresetEdit | None) -> None:
        """Back to the saved sound if the pedal still plays our preview."""
        self.playing = None
        state = self.editor.state
        if (edit is None or state.current_slot != edit.slot or state.edit is None
                or list(state.edit.words) != edit.after):
            return
        self.runner.run(lambda: self.editor.select(edit.slot), None, self._failed)

    def preset_changed(self) -> None:
        """Another preset was loaded (from the list, the pedal…): stop showing the bar."""
        if self.playing is not None and self.editor.state.current_slot != self.playing:
            self.playing = None
            self._render()

    def chosen(self) -> list[PresetEdit]:
        result = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            edit = self.edits.get(int(item.data(0, Qt.ItemDataRole.UserRole)))
            if (edit is not None and not edit.blocked
                    and item.checkState(0) == Qt.CheckState.Checked):
                result.append(edit)
        return result

    def _render(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for slot in sorted(self.edits):
            edit = self.edits[slot]
            sounding = tr("   🔊 sonando") if slot == self.playing else ""
            name = f"{edit.name} → {edit.new_name}" if edit.renamed else edit.name
            top = QTreeWidgetItem([f"{slot:03d}  {name}{sounding}"])
            top.setData(0, Qt.ItemDataRole.UserRole, slot)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setFirstColumnSpanned(True)
            if edit.blocked:
                top.setDisabled(True)
            else:
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                top.setCheckState(0, Qt.CheckState.Checked)
            self.tree.addTopLevelItem(top)
            top.setFirstColumnSpanned(True)
            for module, before, after in bulk_edit.rows(self.editor, edit):
                child = QTreeWidgetItem(["    " + module, before, after])
                child.setForeground(1, QBrush(QColor(MUTED)))
                self.tree.addTopLevelItem(child)
                child.setData(0, Qt.ItemDataRole.UserRole, slot)
                child.setFlags(Qt.ItemFlag.ItemIsEnabled)
            notes = ([(edit.blocked, BLOCKED)] if edit.blocked else []) + \
                [(w, WARN) for w in edit.warnings]
            for text, colour in notes:
                child = QTreeWidgetItem(["    ⚠ " + text])
                child.setForeground(0, QBrush(QColor(colour)))
                child.setData(0, Qt.ItemDataRole.UserRole, slot)
                child.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(child)
                child.setFirstColumnSpanned(True)
        self.tree.blockSignals(False)
        self.empty.setVisible(not self.edits)
        self.tree.setVisible(bool(self.edits))
        playing = self.edits.get(self.playing or 0)
        self.bar.setVisible(playing is not None)
        if playing is not None:
            self.playing_label.setText(tr(
                "Escuchando en la pedalera: <b>{slot:03d} {name}</b>",
                slot=playing.slot,
                name=html.escape(playing.name),
            ))
            self.sides.button(1 if self.playing_after else 0).setChecked(True)
        self._count()

    def _count(self) -> None:
        chosen = len(self.chosen())
        busy = self.runner.busy
        self.apply_button.setText(tr(
            "Aplicar a {count} preset{plural}",
            count=chosen,
            plural='s' * (chosen != 1),
        ))
        self.apply_button.setEnabled(chosen > 0 and not busy)
        self.badge.setText(f"{len(self.edits)} preset{'s' * (len(self.edits) != 1)}"
                           if self.edits else "")
        self.badge.setProperty("on", bool(self.edits))
        self.badge.setVisible(bool(self.edits))
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        self.undo_button.setVisible(bool(self.previous))
        self.discard.setEnabled(bool(self.edits) and not busy)
        self.undo_button.setEnabled(bool(self.previous) and not busy)
        for button in self.sides.buttons():
            button.setEnabled(not busy)

    # --- writing ----------------------------------------------------------------------

    def apply(self) -> None:
        chosen = self.chosen()
        if not chosen:
            return
        self.message.emit(tr(
            "Haciendo un backup de la pedalera y escribiendo {count} presets…",
            count=len(chosen),
        ))

        def done(result: bulk_edit.Applied) -> None:
            self.previous = result.previous
            for preset in result.previous:
                self.edits.pop(preset.slot, None)
            self.playing = None  # the applied preset was loaded again with its new sound
            skipped = "; ".join(f"{slot:03d}: {why}" for slot, why in result.skipped)
            saved = (tr(" Backup previo: {backup}", backup=auto_backup.short(result.backup))
                     if result.backup else "")
            self.message.emit(tr(
                "Listo: {count} presets escritos.{backup}",
                count=len(result.previous),
                backup=saved,
            )
                              + (tr(
                                  " No se escribieron: {skipped}",
                                  skipped=skipped,
                              ) if skipped else ""))
            self._render()
            self.edits_changed.emit()

        self.runner.run(lambda: bulk_edit.apply(self.editor, chosen, self._backup("aplicar")),
                        done, self._failed)

    def _backup(self, reason: str) -> bulk_edit.Backup:
        return lambda: auto_backup.save_before(self.editor, reason)

    def undo(self) -> None:
        previous = self.previous
        if not previous:
            return

        def done(slots: list[int]) -> None:
            self.previous = []
            self.message.emit(tr(
                "Restaurados: {slots}.",
                slots=', '.join(f'{s:03d}' for s in slots),
            ))
            self._count()

        self.runner.run(lambda: bulk_edit.restore(self.editor, previous,
                                                  self._backup("deshacer")),
                        done, self._failed)

    def _failed(self, error: str) -> None:
        self.message.emit(f"Error: {error}")
        self._count()

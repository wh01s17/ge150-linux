"""«Asistente» tab: tick presets, change them with the AI or the manual form, review the
changes and apply them in one go (``bulk_form.py``, ``bulk_review.py``). The chat here
is its own conversation, separate from the side panel's."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.services import bulk_edit
from ge150.services.bulk_edit import PresetEdit
from ge150.services.editor import Editor
from ge150.ui.ai_chat import AiChat
from ge150.ui.ai_host import AiHost
from ge150.ui.bulk_form import BulkForm
from ge150.ui.bulk_review import BulkReview
from ge150.ui.icons import icon
from ge150.ui.theme import ACCENT, MUTED
from ge150.ui.worker import TaskRunner

GUIDE = """The user is in the «Asistente» tab of the editor, made to change several STORED
presets at once; the presets they ticked come before each message. To change stored presets,
read them (get_presets or get_preset) and call propose_preset_edits once, with the values for
each preset: it writes nothing, the user reviews a table and applies. The same call renames
presets ("name") and moves or reorders them ("copy_from": the slot gets that preset's stored
content and name), so do it there when asked, never tell the user to do it by hand. The
editor loads the proposed preset on the pedal at once so the user hears it (live, not
saved): NEVER use apply_patch or set_module to try out a change of a stored preset, the
loaded one may be another preset. To offer another option, call propose_preset_edits
again: it replaces the pending change of that preset."""

EXAMPLES = ("Baja el reverb a 20 en todos los Av83",
            "Apaga el delay en los presets marcados",
            "Sube 5 la ganancia del AMP en los presets de Burden",
            "Pon en los Av83 el mismo CAB que tiene el preset 24")
WELCOME = ("Marca presets a la izquierda o nómbralos («todos los Av83»). Propongo los cambios y "
           "los ves en <b>Cambios pendientes</b>: nada se escribe hasta que aprietes "
           "<b>Aplicar</b>.")


class AssistantPage(QWidget):
    def __init__(self, editor: Editor, runner: TaskRunner, host: AiHost,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor

        self.setObjectName("assistantPage")

        # Left: which presets.
        left = QFrame()
        left.setObjectName("sidePanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        title = QLabel("Presets")
        title.setObjectName("cardTitle")
        self.count = QLabel()
        self.count.setObjectName("countBadge")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count)
        left_layout.addLayout(head)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("Filtrar por nombre (ej. av83)…"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        self.filter.returnPressed.connect(lambda: self._tick_visible(True))
        self.filter.setToolTip(tr("Enter marca los presets que se ven"))
        left_layout.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setObjectName("presetChecks")
        # Only the check boxes matter: no selection (its orange row hid the tick), and a
        # click anywhere on the row ticks it.
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list.itemChanged.connect(lambda _: self._ticks_changed())
        self.list.itemPressed.connect(self._pressed)
        self.list.itemClicked.connect(self._clicked)
        self._pressed_state: Qt.CheckState | None = None
        left_layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        tick = QPushButton(tr("Marcar los visibles"))
        tick.setObjectName("linkButton")
        tick.setCursor(Qt.CursorShape.PointingHandCursor)
        tick.clicked.connect(lambda: self._tick_visible(True))
        untick = QPushButton(tr("Desmarcar todos"))
        untick.setObjectName("linkButton")
        untick.setCursor(Qt.CursorShape.PointingHandCursor)
        untick.clicked.connect(lambda: self._tick_visible(False, every=True))
        row.addWidget(tick)
        row.addStretch(1)
        row.addWidget(untick)
        left_layout.addLayout(row)

        # Centre: how (the AI or the manual form).
        self.review = BulkReview(editor, runner)
        self.form = BulkForm(editor, self.ticked, self.review.bases)
        self._restoring = False
        self.chat = AiChat(host, extra_guide=GUIDE, context_hook=self._context,
                           examples=EXAMPLES, welcome=WELCOME, scope="asistente",
                           state_hook=self.page_state, restore_hook=self.restore_state)
        # Chip in the chat: the ticked presets go with each message (click to leave them out).
        self.ticks_chip = QPushButton()
        self.ticks_chip.setObjectName("contextChip")
        self.ticks_chip.setCheckable(True)
        self.ticks_chip.setChecked(True)
        self.ticks_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ticks_chip.toggled.connect(lambda _: self._refresh_ticks_chip())
        self.chat.chips.insertWidget(0, self.ticks_chip)
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        switch = QFrame()
        switch.setObjectName("modeSwitch")
        switch_layout = QHBoxLayout(switch)
        switch_layout.setContentsMargins(3, 3, 3, 3)
        switch_layout.setSpacing(2)
        self.modes = QButtonGroup(self)
        for i, (label, tip) in enumerate(((tr("✦  Con IA"), tr("Pídeselo a Claude o Codex")),
                                          (tr("Edición manual"), tr("El mismo cambio para todos "
                                                             "los presets marcados")))):
            button = QPushButton(label)
            button.setObjectName("modeTab")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.modes.addButton(button, i)
            switch_layout.addWidget(button, 1)
        self.modes.button(0).setChecked(True)
        centre_layout.addWidget(switch)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.chat)
        manual = QFrame()
        manual.setObjectName("sidePanel")
        manual_layout = QVBoxLayout(manual)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.addWidget(self.form)
        self.stack.addWidget(manual)
        self.modes.idClicked.connect(self.stack.setCurrentIndex)
        centre_layout.addWidget(self.stack, 1)

        # Right: review and apply.
        right = QFrame()
        right.setObjectName("sidePanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.addWidget(self.review, 1)
        self.status = QLabel()
        self.status.setObjectName("note")
        self.status.setWordWrap(True)
        right_layout.addWidget(self.status)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(centre)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 600, 460])
        splitter.setHandleWidth(10)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(splitter)

        self.form.planned.connect(self.review.add)
        self.form.message.connect(self.status.setText)
        self.review.message.connect(self.status.setText)
        self.review.edits_changed.connect(self._state_changed)
        self.reload_presets()

    # --- presets ----------------------------------------------------------------------

    def reload_presets(self) -> None:
        ticked = set(self.ticked())
        self.list.blockSignals(True)
        self.list.clear()
        for slot, preset in sorted(self.editor.state.presets.items()):
            item = QListWidgetItem(f"{slot:03d}  {preset.name}")
            item.setData(Qt.ItemDataRole.UserRole, slot)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if slot in ticked
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._filter(self.filter.text())
        self._ticks_changed()

    def update_slot(self, slot: int) -> None:
        preset = self.editor.state.presets.get(slot)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if int(item.data(Qt.ItemDataRole.UserRole)) == slot:
                if preset is not None:
                    self.list.blockSignals(True)
                    item.setText(f"{slot:03d}  {preset.name}")
                    self.list.blockSignals(False)
                self.form.refresh()
                return
        self.reload_presets()

    def ticked(self) -> list[int]:
        return [int(self.list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def tick(self, slots: Sequence[int], only: bool = False) -> None:
        """Tick ``slots`` (``only``: and untick the rest)."""
        wanted = set(slots)
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if int(item.data(Qt.ItemDataRole.UserRole)) in wanted:
                item.setCheckState(Qt.CheckState.Checked)
            elif only:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._ticks_changed()

    def _pressed(self, item: QListWidgetItem) -> None:
        self._pressed_state = item.checkState()

    def _clicked(self, item: QListWidgetItem) -> None:
        """A click on the name ticks too (a click on the box was already handled by Qt)."""
        if item.checkState() == self._pressed_state:
            item.setCheckState(Qt.CheckState.Unchecked
                               if item.checkState() == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)
        self._pressed_state = None

    def _filter(self, text: str) -> None:
        wanted = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(wanted) and wanted not in item.text().lower())

    def _tick_visible(self, on: bool, every: bool = False) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if every or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._ticks_changed()

    def _ticks_changed(self) -> None:
        self.list.blockSignals(True)  # restyling an item emits itemChanged
        for i in range(self.list.count()):
            item = self.list.item(i)
            on = item.checkState() == Qt.CheckState.Checked
            font = item.font()
            if font.bold() != on:
                font.setBold(on)
                item.setFont(font)
                item.setForeground(QBrush(QColor(ACCENT)) if on else QBrush())
        self.list.blockSignals(False)
        count = len(self.ticked())
        self.count.setText(tr("{count} marcado" if count == 1 else "{count} marcados", count=count))
        self.count.setProperty("on", count > 0)
        self.count.style().unpolish(self.count)
        self.count.style().polish(self.count)
        self.form.refresh()
        self._refresh_ticks_chip()
        self._state_changed()

    def _refresh_ticks_chip(self) -> None:
        chip = self.ticks_chip
        slots = self.ticked()
        on = chip.isChecked() and bool(slots)
        font = chip.font()
        font.setStrikeOut(bool(slots) and not chip.isChecked())
        chip.setFont(font)
        chip.setIcon(icon("list-checks" if on else "slash", ACCENT if on else MUTED, 13))
        shown = ", ".join(f"{s:03d}" for s in slots[:3])
        more = f" +{len(slots) - 3}" if len(slots) > 3 else ""
        if not slots:
            chip.setText(tr("Sin presets marcados"))
            chip.setToolTip(tr("Marca presets en la lista para que la IA sepa cuáles cambiar"))
            return
        presets = self.editor.state.presets
        listed = "<br>".join(f"{s:03d} {presets[s].name}" for s in slots[:25] if s in presets)
        if len(slots) > 25:
            listed += tr("<br>… y {more} más", more=len(slots) - 25)
        if chip.isChecked():
            chip.setText(tr("Presets marcados: {shown}{more}", shown=shown, more=more))
            chip.setToolTip(tr(
                "<b>Se envían con tu mensaje:</b><br>{listed}<br><i>Clic para no incluirlos.</i>",
                listed=listed,
            ))
        else:
            chip.setText(tr("Presets marcados no incluidos: {shown}{more}", shown=shown, more=more))
            chip.setToolTip(tr(
                "{listed}<br><i>Clic para incluirlos en tu mensaje.</i>",
                listed=listed,
            ))

    def _context(self) -> str:
        slots = self.ticked()
        if not self.ticks_chip.isChecked():
            return ""
        if not slots:
            return "Presets marcados en la lista: ninguno"
        presets = self.editor.state.presets
        names = ", ".join(f"{s} «{presets[s].name}»" for s in slots[:40] if s in presets)
        more = f" y {len(slots) - 40} más" if len(slots) > 40 else ""
        return f"Presets marcados en la lista: {names}{more}"

    # --- kept with each conversation -------------------------------------------------------

    def page_state(self) -> dict[str, Any]:
        """The ticked presets and the pending edits, saved with the conversation."""
        return {"ticked": self.ticked(), "summary": self.review.summary_text,
                "pending": [bulk_edit.to_dict(e) for e in self.review.edits.values()]}

    def restore_state(self, state: dict[str, Any]) -> None:
        """A reopened conversation: tick its presets and show its pending edits again, each
        checked against the pedal (applied ones are dropped, changed ones blocked)."""
        self._restoring = True
        try:
            slots = [int(s) for s in state.get("ticked") or [] if isinstance(s, int)]
            self.tick(slots, only=True)
            kept = [bulk_edit.from_dict(self.editor, d) for d in state.get("pending") or []
                    if isinstance(d, dict)]
            edits = [e for e in kept if e is not None]
            self.review.restore(edits, str(state.get("summary") or "") if edits else "")
        finally:
            self._restoring = False
        self.chat.save_state()
        blocked = sum(1 for e in edits if e.blocked)
        if edits:
            note = (tr(
                " {blocked} ya no se pueden aplicar porque el preset cambió después.",
                blocked=blocked,
            )
                    if blocked else "")
            self.status.setText(tr(
                "Recuperé {count} cambios pendientes de esta conversación.{note}",
                count=len(edits),
                note=note,
            ))

    def _state_changed(self) -> None:
        if not self._restoring:
            self.chat.save_state()

    # --- proposals from the AI ------------------------------------------------------------

    def current_changed(self) -> None:
        self.review.preset_changed()

    def show_proposal(self, edits: list[PresetEdit], summary: str) -> None:
        self.review.add(edits, summary)
        self.tick([e.slot for e in edits])
        self.status.setText(tr(
            "La IA propone cambios en {count} presets: revísalos y aprieta Aplicar.",
            count=len(edits),
        ))

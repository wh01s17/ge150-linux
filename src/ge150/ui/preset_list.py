"""List of the 200 stored presets with search, a context menu and drag and drop to
reorder them (the pedal is rewritten; the rows only show its slots)."""

from __future__ import annotations

import time
from typing import TypeAlias

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QDropEvent, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model.device_state import DeviceState
from ge150.ui.working import Spinner

SLOT_ROLE = Qt.ItemDataRole.UserRole
DOUBLE_CLICK_S = 0.6
Indicator: TypeAlias = QAbstractItemView.DropIndicatorPosition


def drop_target(source: int, slot: int, indicator: Indicator) -> int:
    """The slot a preset dragged from ``source`` takes when dropped at ``slot``: on the row
    it takes that slot; above or below it, the gap it is inserted in."""
    if indicator == Indicator.AboveItem:
        return slot if source > slot else slot - 1
    if indicator == Indicator.BelowItem:
        return slot + 1 if source > slot else slot
    return slot


class _DragList(QListWidget):
    """Shows where a dragged preset would go, but leaves its rows alone: the move is asked
    for with ``moved`` and the rows repaint once the pedal is rewritten."""

    moved = Signal(int, int)  # (source slot, target slot)

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dropped: tuple[int, int] | None = None

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        # ``moved`` opens a confirmation: only once the drag is over (super() returns then),
        # or the drag still holds the mouse (Wayland) and the dialog cannot be clicked.
        self._dropped = None
        super().startDrag(supportedActions)
        if self._dropped is not None:
            source, target = self._dropped
            self._dropped = None
            QTimer.singleShot(0, lambda: self.moved.emit(source, target))

    def dropEvent(self, event: QDropEvent) -> None:
        dragged = self.selectedItems()
        over = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        # Ignored, so Qt neither moves nor removes the dragged row.
        event.ignore()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if event.source() is not self or not dragged or over is None:
            return
        source = int(dragged[0].data(SLOT_ROLE))
        target = drop_target(source, int(over.data(SLOT_ROLE)), indicator)
        if target != source:
            self._dropped = (source, target)  # emitted by startDrag()


class WorkBanner(QWidget):
    """Above the list while presets are being moved: what is being done and how far."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)  # its border
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.spinner = Spinner()
        self.label = QLabel()
        self.label.setWordWrap(True)
        row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        layout.addLayout(row)
        layout.addWidget(self.bar)
        self.hide()

    def start(self, text: str) -> None:
        self.label.setText(text)
        self.bar.setRange(0, 0)  # moving stripe until the first step is known
        self.spinner.start()
        self.show()

    def step(self, text: str, step: int, total: int) -> None:
        self.label.setText(text)
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(step)

    def stop(self) -> None:
        self.spinner.stop()
        self.hide()


class PresetList(QWidget):
    activated = Signal(int)  # load preset on the pedal
    action_requested = Signal(str, int)  # (action name, slot)
    move_requested = Signal(int, int)  # (source slot, target slot), dragged in the list

    ACTIONS = (("rename", "Renombrar…"), ("copy", "Copiar"), ("paste", "Pegar aquí"),
               ("export", "Exportar .mo…"), ("import", "Importar .mo aquí…"))

    def __init__(self, state: DeviceState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar preset…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.list = _DragList()
        self.list.moved.connect(self._moved)
        self.list.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        # One click loads the preset; Enter too. A double click must not load it twice.
        self.list.itemClicked.connect(self._chosen)
        self.list.itemActivated.connect(self._chosen)
        self._last_choice: tuple[int, float] = (0, 0.0)
        self.work = WorkBanner()
        layout.addWidget(self.search)
        layout.addWidget(self.work)
        layout.addWidget(self.list, 1)
        self.clipboard: int | None = None

    def _chosen(self, item: QListWidgetItem) -> None:
        slot = int(item.data(SLOT_ROLE))
        now = time.monotonic()
        last_slot, last_time = self._last_choice
        self._last_choice = (slot, now)
        if slot == last_slot and now - last_time < DOUBLE_CLICK_S:
            return
        self.activated.emit(slot)

    def _moved(self, source: int, target: int) -> None:
        self.select_slot(self.state.current_slot)  # dragging selected the dragged row
        self.move_requested.emit(source, target)

    @property
    def working(self) -> bool:
        return not self.work.isHidden()

    def start_work(self, text: str) -> None:
        """Show ``text`` above the list and dim it until :meth:`end_work`."""
        self.work.start(text)
        self.list.setEnabled(False)
        self.search.setEnabled(False)

    def end_work(self) -> None:
        self.work.stop()
        self.list.setEnabled(True)
        self.search.setEnabled(True)

    def set_busy(self, busy: bool) -> None:
        """No new moves while the pedal is being written."""
        self.list.setDragEnabled(not busy)

    def select_slot(self, slot: int | None) -> None:
        """Highlight ``slot`` (e.g. back to the current preset after a cancelled change)."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(SLOT_ROLE) == slot:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                return

    def selected_slot(self) -> int | None:
        item = self.list.currentItem()
        return None if item is None else int(item.data(SLOT_ROLE))

    def reload(self) -> None:
        current = self.selected_slot()
        self.list.clear()
        for slot in sorted(self.state.presets):
            item = QListWidgetItem()
            item.setData(SLOT_ROLE, slot)
            self.list.addItem(item)
            self._paint(item)
            if slot == (current or self.state.current_slot):
                self.list.setCurrentItem(item)
        self._filter(self.search.text())

    def update_slot(self, slot: int) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(SLOT_ROLE) == slot:
                self._paint(item)
        if self.list.count() == 0 and self.state.presets:
            self.reload()

    def mark_current(self) -> None:
        """Repaint and select the current preset, also when it was chosen on the pedal
        itself (selecting a row does not load anything: only clicks do)."""
        for row in range(self.list.count()):
            self._paint(self.list.item(row))
        self.select_slot(self.state.current_slot)

    def _paint(self, item: QListWidgetItem) -> None:
        slot = int(item.data(SLOT_ROLE))
        preset = self.state.presets.get(slot)
        current = slot == self.state.current_slot
        dirty = current and self.state.dirty
        name = preset.name if preset else "?"
        item.setText(f"{slot:3d}  {name}{'  *' if dirty else ''}")
        font = QFont(item.font())
        font.setBold(current)
        item.setFont(font)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        slot = int(item.data(SLOT_ROLE))
        menu = QMenu(self)
        load = QAction(tr("Cargar en la pedalera"), menu)
        load.triggered.connect(lambda: self.activated.emit(slot))
        menu.addAction(load)
        menu.addSeparator()
        for name, label in self.ACTIONS:
            action = QAction(tr(label), menu)
            action.setEnabled(name != "paste" or self.clipboard is not None)
            action.triggered.connect(lambda _=False, n=name: self.action_requested.emit(n, slot))
            menu.addAction(action)
        menu.exec(self.list.mapToGlobal(pos))

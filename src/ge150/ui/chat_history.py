"""Saved conversations of a chat: search, open (continue), rename and delete — like the
history panel of the AgentHub plugin for Obsidian. It replaces the chat while open."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.services import tone_agent
from ge150.services.chat_history import ChatEntry, ChatStore
from ge150.ui.icons import icon
from ge150.ui.theme import MUTED

UNTITLED = "Conversación sin título"


def _icon_button(name: str, tip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("iconButton")
    button.setIcon(icon(name, MUTED, 15))
    button.setIconSize(QSize(15, 15))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def when(stamp: float) -> str:
    """"hoy 14:32", "ayer 09:10" or "21-11-2026 18:05"."""
    day = time.strftime("%Y%m%d", time.localtime(stamp))
    today = time.strftime("%Y%m%d")
    yesterday = time.strftime("%Y%m%d", time.localtime(time.time() - 86400))
    clock = time.strftime("%H:%M", time.localtime(stamp))
    if day == today:
        return tr("hoy {clock}", clock=clock)
    if day == yesterday:
        return tr("ayer {clock}", clock=clock)
    return time.strftime("%d-%m-%Y %H:%M", time.localtime(stamp))


class _Entry(QFrame):
    opened = Signal(str)
    renamed = Signal(str, str)
    deleted = Signal(str)

    def __init__(self, entry: ChatEntry, current: bool, enabled: bool) -> None:
        super().__init__()
        self.entry, self.enabled = entry, enabled
        self.setObjectName("historyEntry")
        self.setProperty("current", current)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else
                       Qt.CursorShape.ArrowCursor)
        cls = tone_agent.AGENTS.get(entry.provider)
        agent = cls.name if cls else entry.provider
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 4, 6)
        layout.setSpacing(4)
        badge = QLabel(agent[:1].upper())
        badge.setObjectName("agentBadge")
        badge.setProperty("agent", entry.provider)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        badge.setToolTip(agent)
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = QLabel(entry.title or tr(UNTITLED))
        self.title.setObjectName("historyTitle")
        self.title.setWordWrap(True)
        self.editor = QLineEdit(entry.title)
        self.editor.setObjectName("historyRename")
        self.editor.hide()
        self.editor.installEventFilter(self)
        meta = f"{agent if enabled else agent + tr(' (no instalado)')} · {when(entry.updated)}"
        if entry.slot:
            meta += f" · preset {entry.slot:03d} {entry.preset}".rstrip()
        self.meta = QLabel(meta + (tr(" · <b>Actual</b>") if current else ""))
        self.meta.setWordWrap(True)
        self.meta.setObjectName("historyMeta")
        texts.addWidget(self.title)
        texts.addWidget(self.editor)
        texts.addWidget(self.meta)
        layout.addLayout(texts, 1)
        self.rename_button = _icon_button("pencil", tr("Renombrar"))
        self.rename_button.clicked.connect(self._start_rename)
        self.delete_button = _icon_button("trash", tr("Eliminar"))
        self.delete_button.clicked.connect(self._delete)
        self._confirming = False
        self._reset = QTimer(self, singleShot=True, interval=3000)
        self._reset.timeout.connect(self._cancel_delete)
        layout.addWidget(self.rename_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignTop)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (event.button() == Qt.MouseButton.LeftButton and self.enabled
                and not self.editor.isVisible()):
            self.opened.emit(self.entry.id)
        super().mouseReleaseEvent(event)

    # --- rename in place (Enter keeps, Esc or leaving cancels) --------------------------

    def _start_rename(self) -> None:
        self.title.hide()
        self.editor.setText(self.entry.title)
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _stop_rename(self, keep: bool) -> None:
        if not self.editor.isVisible():
            return
        text = self.editor.text().strip()
        self.editor.hide()
        self.title.show()
        if keep and text and text != self.entry.title:
            self.title.setText(text)
            self.renamed.emit(self.entry.id, text)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.editor:
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._stop_rename(True)
                    return True
                if event.key() == Qt.Key.Key_Escape:
                    self._stop_rename(False)
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                self._stop_rename(False)
        return super().eventFilter(watched, event)

    # --- delete: a second click confirms ------------------------------------------------

    def _delete(self) -> None:
        if self._confirming:
            self.deleted.emit(self.entry.id)
            return
        self._confirming = True
        self.delete_button.setIcon(icon("check", "#ff8a80", 15))
        self.delete_button.setToolTip(tr("Confirmar borrado"))
        self._reset.start()

    def _cancel_delete(self) -> None:
        self._confirming = False
        self.delete_button.setIcon(icon("trash", MUTED, 15))
        self.delete_button.setToolTip(tr("Eliminar"))


class ChatHistory(QWidget):
    opened = Signal(str)
    deleted = Signal(str)
    closed = Signal()

    def __init__(self, store: ChatStore, scope: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store, self.scope = store, scope
        self.current = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        back = _icon_button("arrow-left", tr("Volver a la conversación"))
        back.clicked.connect(self.closed)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar conversaciones"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.reload())
        head.addWidget(back)
        head.addWidget(self.search, 1)
        layout.addLayout(head)
        title = QLabel(tr("Historial de conversaciones"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("historyScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll, 1)

    def reload(self) -> None:
        needle = self.search.text().strip().lower()
        entries = [e for e in self.store.entries(self.scope)
                   if not needle or needle in (e.title or tr(UNTITLED)).lower()]
        body = QWidget()
        body.setObjectName("historyBody")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(2)
        if not entries:
            empty = QLabel(tr("No hay conversaciones que coincidan.") if needle else
                           tr("Todavía no hay conversaciones guardadas."))
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(empty)
        installed = set(tone_agent.available_agents())
        for entry in entries:
            row = _Entry(entry, entry.id == self.current, entry.provider in installed)
            row.opened.connect(self.opened)
            row.renamed.connect(self._renamed)
            row.deleted.connect(self._deleted)
            column.addWidget(row)
        column.addStretch(1)
        self.scroll.setWidget(body)

    def _renamed(self, chat_id: str, title: str) -> None:
        self.store.rename(chat_id, title)

    def _deleted(self, chat_id: str) -> None:
        self.store.delete(chat_id)
        self.deleted.emit(chat_id)
        QTimer.singleShot(0, self.reload)  # not while the clicked row is still handling it


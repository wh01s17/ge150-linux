"""Chat with Claude Code or Codex, which change the sound with the MCP tools.

The agent runs ``ge150-mcp``, which forwards to this editor (``ai_host.py``): each tool
call appears in the chat as it happens, the GUI updates at once, and storing asks the user.
Used by the side panel (``ai_panel.py``) and the «Asistente» tab; each has its own
conversation.
"""

from __future__ import annotations

import html
import importlib.util
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.services import ai_permissions, guitars, tone_agent, tone_ai
from ge150.services.chat_history import Chat, ChatEntry, ChatStore, new_id, title_for
from ge150.services.guitars import GuitarStore
from ge150.ui import markdown
from ge150.ui.ab_compare import SoundCompare
from ge150.ui.ai_host import LABELS, AiHost
from ge150.ui.ai_options import AgentOptions
from ge150.ui.ai_permissions_dialog import AiPermissionsDialog
from ge150.ui.chat_history import ChatHistory
from ge150.ui.icons import icon
from ge150.ui.theme import ACCENT
from ge150.ui.theme import MUTED as MUTED_ICON
from ge150.ui.working import WorkingIndicator

EXAMPLES = ("Tono de Slash en el solo de Sweet Child O' Mine",
            "Clean brillante con chorus estilo The Police",
            "Más ganancia y menos graves",
            "Busca en TONE3000 una IR de Mesa 4x12 y cárgala en la ranura 10")
USER, AI, MUTED, ERROR = "#ff8a1f", "#7fd3ff", "#8b95a3", "#ff8a80"


class _Prompt(QPlainTextEdit):
    """Enter sends, Shift+Enter makes a new line."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class _Done(QObject):
    finished = Signal(object, str)  # reply text (or None), error
    noted = Signal(str)
    status_text = Signal(str)


class AiChat(QWidget):
    """``extra_guide``: more instructions for the agent; ``context_hook``: text put before
    each message (e.g. the presets ticked in the «Asistente» tab); ``state_hook`` /
    ``restore_hook``: what the page keeps with each conversation and gets back when it is
    reopened (call :meth:`save_state` when it changes)."""

    def __init__(self, host: AiHost, parent: QWidget | None = None, *, extra_guide: str = "",
                 context_hook: Callable[[], str] | None = None,
                 examples: Sequence[str] = EXAMPLES, welcome: str = "",
                 scope: str = "panel",
                 state_hook: Callable[[], dict[str, Any]] | None = None,
                 restore_hook: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__(parent)
        self.host = host
        self.scope = scope  # whose history: the side panel's or the «Asistente» tab's
        self.store = ChatStore()
        self.chat_id, self.chat_title, self.chat_created = "", "", 0.0
        self.extra_guide, self.context_hook = extra_guide, context_hook
        self.state_hook, self.restore_hook = state_hook, restore_hook
        self.examples, self.welcome = examples, welcome
        self.agents: dict[str, tone_agent.Agent] = {}
        self.blocks: list[str] = []
        self._tool_run: tuple[int, str, int] | None = None  # block index, tool, calls
        self._before: list[int] | None = None
        self.busy = False
        self._done = _Done(self)
        self._done.finished.connect(self._finished)
        self._done.noted.connect(self._note)
        self._done.status_text.connect(self._show_status)
        host.called.connect(self._tool_called)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.provider = QComboBox()
        for key in tone_agent.available_agents():
            self.provider.addItem(tone_agent.AGENTS[key].name, key)
        self.new_button = QPushButton(tr("Nueva"))
        self.new_button.setToolTip(tr("Empezar una conversación nueva"))
        self.new_button.clicked.connect(self.new_conversation)
        self.history_button = QToolButton()
        self.history_button.setObjectName("iconButton")
        self.history_button.setIcon(icon("history", MUTED_ICON, 16))
        self.history_button.setToolTip(tr("Historial de conversaciones"))
        self.history_button.setCheckable(True)
        self.history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_button.toggled.connect(self._toggle_history)
        self.permissions_button = QToolButton()
        self.permissions_button.setObjectName("iconButton")
        self.permissions_button.setIcon(icon("settings", MUTED_ICON, 16))
        self.permissions_button.setToolTip(tr("Permisos del asistente"))
        self.permissions_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.permissions_button.clicked.connect(self.edit_permissions)
        top.addWidget(self.provider, 1)
        top.addWidget(self.permissions_button)
        top.addWidget(self.history_button)
        top.addWidget(self.new_button)
        layout.addLayout(top)

        # The conversation and the history share the space below the provider row.
        self.pages = QStackedWidget()
        conversation = QWidget()
        page = QVBoxLayout(conversation)
        page.setContentsMargins(0, 0, 0, 0)
        self.history = ChatHistory(self.store, scope)
        self.history.opened.connect(self.open_chat)
        self.history.deleted.connect(self._chat_deleted)
        self.history.closed.connect(lambda: self.history_button.setChecked(False))
        self.pages.addWidget(conversation)
        self.pages.addWidget(self.history)
        layout.addWidget(self.pages, 1)

        self.chat = QTextBrowser()
        self.chat.setObjectName("aiChat")
        self.chat.setOpenExternalLinks(True)
        self.chat.document().setDefaultStyleSheet(markdown.STYLE)
        page.addWidget(self.chat, 1)
        self.working = WorkingIndicator()
        page.addWidget(self.working)
        self.status = QLabel()
        self.status.setObjectName("note")
        self.status.setWordWrap(True)
        page.addWidget(self.status)
        # The composer (like the AgentHub plugin for Obsidian): context chips, the message and a
        # toolbar with option pills and a round send/stop button, all inside one rounded box.
        self.composer = QFrame()
        self.composer.setObjectName("composer")
        box = QVBoxLayout(self.composer)
        box.setContentsMargins(10, 8, 8, 8)
        box.setSpacing(4)
        self.context = QPushButton()
        self.context.setObjectName("contextChip")
        self.context.setCheckable(True)
        self.context.setChecked(True)
        self.context.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context.toggled.connect(lambda _: self.refresh_context())
        self.guitars = GuitarStore()
        self.guitar = QToolButton()
        self.guitar.setObjectName("guitarChip")
        self.guitar.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.guitar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.guitar_menu = QMenu(self.guitar)
        self.guitar_menu.setObjectName("pillMenu")
        self.guitar_menu.aboutToShow.connect(self._guitar_menu)
        self.guitar.setMenu(self.guitar_menu)
        self.cabs = QToolButton()
        self.cabs.setObjectName("pill")
        self.cabs.setCheckable(True)
        self.cabs.setChecked(QSettings("ge150", "editor").value("ai/tone3000_cabs", False,
                                                                type=bool))
        self.cabs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cabs.toggled.connect(self._cabs_toggled)
        chips = QHBoxLayout()
        self.chips = chips  # other screens add their own chips (``insertWidget(0, …)``)
        chips.setSpacing(6)
        chips.addWidget(self.context)
        chips.addWidget(self.guitar)
        chips.addStretch(1)
        box.addLayout(chips)
        self.prompt = _Prompt()
        self.prompt.setObjectName("aiPrompt")
        self.prompt.setPlaceholderText(tr("Describe el sonido…  (Enter envía, Shift+Enter salto)"))
        self.prompt.setFixedHeight(64)
        self.prompt.submitted.connect(self.ask)
        self.prompt.installEventFilter(self)
        box.addWidget(self.prompt)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.undo_button = QToolButton()
        self.undo_button.setObjectName("pill")
        self.undo_button.setIcon(icon("undo", MUTED_ICON, 13))
        self.undo_button.setToolTip(tr(
            "Deshacer: volver al sonido anterior al último cambio de la IA",
        ))
        self.undo_button.clicked.connect(self.undo)
        self.options = AgentOptions()
        self.provider.currentIndexChanged.connect(
            lambda _: self.options.set_provider(str(self.provider.currentData() or "")))
        self.options.set_provider(str(self.provider.currentData() or ""))
        self.ask_button = QToolButton()
        self.ask_button.setObjectName("sendButton")
        self.ask_button.setIcon(icon("arrow-up", "#1b1205", 16))
        self.ask_button.setToolTip(tr("Enviar (Enter)"))
        self.ask_button.clicked.connect(self._send_or_stop)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.options)
        toolbar.addWidget(self.cabs)
        toolbar.addStretch(1)
        toolbar.addWidget(self.ask_button)
        box.addLayout(toolbar)
        self.compare = SoundCompare(host)
        page.addWidget(self.compare)
        page.addWidget(self.composer)
        self.refresh_context()
        self.refresh_guitar()
        self.refresh_cabs()
        self._welcome()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """The whole box shows the focus of the message (like :focus-within)."""
        if watched is self.prompt and event.type() in (QEvent.Type.FocusIn,
                                                       QEvent.Type.FocusOut):
            self.composer.setProperty("focused", event.type() == QEvent.Type.FocusIn)
            self.composer.style().unpolish(self.composer)
            self.composer.style().polish(self.composer)
        return super().eventFilter(watched, event)

    def refresh_context(self) -> None:
        """The chip: the selected preset goes with each message unless the user unticks it."""
        state = self.host.tools.editor.state
        slot = state.current_slot
        stored = state.presets.get(slot or 0)
        self.compare.preset_changed(slot, list(stored.words) if stored else None)
        preset = state.presets.get(slot or 0)
        label = f"{slot:03d} {preset.name if preset else ''}".strip() if slot else "—"
        font = self.context.font()
        font.setStrikeOut(not self.context.isChecked())  # QSS text-decoration ignores :checked
        self.context.setFont(font)
        on = self.context.isChecked()
        self.context.setIcon(icon("sliders" if on else "slash", ACCENT if on else MUTED_ICON, 13))
        if on:
            self.context.setText(tr("Preset actual: {label}", label=label))
            self.context.setToolTip(tr("Se envía con tu mensaje. Clic para no incluirlo."))
        else:
            self.context.setText(tr("Preset actual no incluido: {label}", label=label))
            self.context.setToolTip(tr("Clic para incluir el preset actual en tu mensaje."))

    def _cabs_toggled(self, on: bool) -> None:
        QSettings("ge150", "editor").setValue("ai/tone3000_cabs", on)
        self.refresh_cabs()

    def refresh_cabs(self) -> None:
        """The speaker pill: with it on, the AI also looks for a real cabinet IR on TONE3000."""
        on = self.cabs.isChecked()
        self.cabs.setIcon(icon("speaker", ACCENT if on else MUTED_ICON, 13))
        # Rich text, so Qt wraps it (a plain-text tooltip is one long line).
        if on:
            self.cabs.setToolTip(
                tr("<b>Cabs de TONE3000: activado</b><br>"
                "En cada sonido nuevo la IA busca un gabinete real en TONE3000,<br>"
                "lo descarga en una ranura libre (o te pregunta cuál reemplazar)<br>"
                "y lo pone en el CAB.<br><i>Clic para desactivar.</i>"))
        else:
            self.cabs.setToolTip(
                tr("<b>Cabs de TONE3000: desactivado</b><br>"
                "La IA usa los CAB de fábrica, o una IR solo si se la pides.<br>"
                "<i>Clic para que busque gabinetes reales en TONE3000<br>"
                "(requiere iniciar sesión en TONE3000).</i>"))

    # --- the guitar in use ----------------------------------------------------------------

    def refresh_guitar(self) -> None:
        guitar = self.guitars.current
        self.guitar.setProperty("on", guitar is not None)
        self.guitar.setIcon(icon("guitar", ACCENT if guitar else MUTED_ICON, 13))
        self.guitar.setText(f"{guitar.title}  ⌄" if guitar else tr("Sin guitarra  ⌄"))
        self.guitar.setToolTip(tr("Guitarra que estás usando: se envía con tu mensaje") if guitar
                               else tr("Elige tu guitarra para que la IA la tenga en cuenta"))
        self.guitar.style().unpolish(self.guitar)
        self.guitar.style().polish(self.guitar)

    def _guitar_menu(self) -> None:
        menu = self.guitar_menu
        menu.clear()
        self.guitars.load()
        for guitar in self.guitars.guitars:
            action = menu.addAction(guitar.title)
            action.setCheckable(True)
            action.setChecked(guitar.id == self.guitars.active)
            action.triggered.connect(lambda _=False, gid=guitar.id: self._use_guitar(gid))
        none = menu.addAction(tr("Sin guitarra"))
        none.setCheckable(True)
        none.setChecked(self.guitars.current is None)
        none.triggered.connect(lambda: self._use_guitar(""))
        menu.addSeparator()
        menu.addAction(tr("Mis guitarras…")).triggered.connect(self.manage_guitars)

    def _use_guitar(self, guitar_id: str) -> None:
        self.guitars.use(guitar_id)
        self.refresh_guitar()

    def manage_guitars(self) -> None:
        from ge150.ui.guitar_dialog import GuitarDialog

        model, _ = self.options.selection()
        provider = str(self.provider.currentData() or "")
        if provider == "codex":  # Claude finds the exact specs on the web better
            provider, model = "claude", ""
        GuitarDialog(self.guitars, provider, model, self).exec()
        self.guitars.load()
        self.refresh_guitar()

    def edit_permissions(self) -> None:
        AiPermissionsDialog(self).exec()

    def _with_context(self, request: str) -> str:
        denied = ai_permissions.load().missing(p.key for p in ai_permissions.PERMISSIONS)
        if denied:
            request = (f"[Permisos que el usuario desactivó: {', '.join(denied)}. No intentes "
                       "esas operaciones; si hacen falta, dile que puede activarlas con el "
                       "botón de engranaje del chat.]\n\n" + request)
        if self.context_hook is not None and (extra := self.context_hook()):
            request = f"[{extra}]\n\n{request}"
        guitar = self.guitars.current
        if guitar is not None:
            request = f"[{guitars.context_text(guitar)}]\n\n{request}"
        if not self.context.isChecked():
            return request
        editor = self.host.tools.editor
        state = editor.state
        if state.edit is None:
            return request
        preset = state.presets.get(state.current_slot or 0)
        name = preset.name if preset else ""
        changed = " (con cambios sin guardar)" if state.dirty else ""
        return (f"[Contexto: preset seleccionado {state.current_slot} «{name}»{changed}. "
                f"Sonido actual:\n{tone_ai.current_text(editor)}]\n\n{request}")

    def _welcome(self) -> None:
        if importlib.util.find_spec("mcp") is None:
            self._ready(False)
            self._note(tr("Falta el SDK de MCP: <code>pip install 'ge150-linux[ai]'</code>."))
        elif self.provider.count() == 0:
            self._ready(False)
            self._note(tr(
                "No encontré <b>claude</b> ni <b>codex</b>. Instala Claude Code o Codex e inicia "
                "sesión con tu cuenta.",
            ))
        else:
            examples = "".join(f"<li>{html.escape(tr(e))}</li>" for e in self.examples)
            intro = tr(self.welcome or (
                "Pide un sonido y lo ajusto <b>en vivo</b>. No se guarda hasta que aprietes "
                "<b>Guardar</b> o me lo pidas (te pregunto antes)."
            ))
            self._note(tr("{intro} Ejemplos:<ul>{examples}</ul>", intro=intro, examples=examples))

    # --- actions ----------------------------------------------------------------------

    def _agent(self) -> tone_agent.Agent:
        """The provider's agent (one per provider keeps its conversation), with the model
        and effort picked now: changing them goes on with the same conversation."""
        key = str(self.provider.currentData())
        if key not in self.agents:
            self.agents[key] = tone_agent.AGENTS[key]()
        agent = self.agents[key]
        agent.model, agent.effort = self.options.selection()
        agent.search_cabs = self.cabs.isChecked()
        agent.extra_guide = self.extra_guide
        return agent

    def ask(self, text: str = "") -> None:
        request = (text or self.prompt.toPlainText()).strip()
        if not request or self.busy or not self.prompt.isEnabled():
            return
        agent = self._agent()
        message = self._with_context(request)
        # "Original" = the preset as stored on the pedal, not the AI's previous suggestion.
        state = self.host.tools.editor.state
        stored = state.presets.get(state.current_slot or 0)
        edit = state.edit
        self._before = list(stored.words) if stored is not None else (
            list(edit.words) if edit is not None else None)
        self.compare.clear()
        self.prompt.clear()
        self._bubble(tr("Tú"), html.escape(request).replace("\n", "<br>"), USER)
        if not self.chat_id:
            self.chat_id, self.chat_created = new_id(), time.time()
            self.chat_title = title_for(request)
        self._save()
        self._set_busy(True, tr("{agent} está trabajando…", agent=agent.name))

        def work() -> None:
            try:
                reply: object = agent.ask(message)
                error = ""
            except Exception as exc:  # shown in the chat
                reply, error = None, str(exc)
            self._done.finished.emit(reply, error)

        threading.Thread(target=work, name="ge150-agent", daemon=True).start()

    def _send_or_stop(self) -> None:
        if self.busy:
            tone_agent.stop_all()
            self.working.set_text(tr("Deteniendo"))
        else:
            self.ask()

    def undo(self) -> None:
        def work() -> None:
            try:
                undone = self.host.tools.undo().get("undone")
            except Exception as exc:  # shown in the chat
                self._done.noted.emit(f"<span style='color:{ERROR}'>{html.escape(str(exc))}</span>")
                return
            self._done.noted.emit(tr("Volví al sonido anterior.") if undone else
                                  tr("No hay cambios de la IA para deshacer."))

        threading.Thread(target=work, daemon=True).start()

    def new_conversation(self) -> None:
        for agent in self.agents.values():
            agent.reset()
        self.chat_id, self.chat_title, self.chat_created = "", "", 0.0
        self.compare.clear()
        self.blocks.clear()
        self.status.setText("")
        self.history_button.setChecked(False)
        self._welcome()

    # --- history ----------------------------------------------------------------------

    def _toggle_history(self, show: bool) -> None:
        if show:
            self.history.current = self.chat_id
            self.history.reload()
        self.pages.setCurrentIndex(1 if show else 0)

    def _save(self) -> None:
        """Keep the conversation (what the chat shows, the CLI sessions, the preset)."""
        if not self.chat_id:
            return
        state = self.host.tools.editor.state
        slot = state.current_slot or 0
        preset = state.presets.get(slot)
        entry = ChatEntry(self.chat_id, self.scope, self.chat_title,
                          str(self.provider.currentData() or ""), self.chat_created, 0.0,
                          slot, preset.name if preset else "")
        sessions = {key: agent.session for key, agent in self.agents.items() if agent.session}
        page = self.state_hook() if self.state_hook is not None else {}
        try:
            self.store.save(Chat(entry, list(self.blocks), sessions, page))
        except (OSError, ValueError) as exc:
            self.status.setText(tr("No pude guardar la conversación: {exc}", exc=exc))

    def save_state(self) -> None:
        """The page's state changed: keep it with the open conversation (if saved)."""
        if not self.chat_id or self.state_hook is None:
            return
        try:
            self.store.save_state(self.chat_id, self.state_hook())
        except (OSError, ValueError) as exc:
            self.status.setText(tr("No pude guardar la conversación: {exc}", exc=exc))

    def open_chat(self, chat_id: str) -> None:
        """Show a saved conversation as it was, continue its CLI sessions and load its preset
        on the pedal (asking first if the current one has unsaved changes)."""
        if self.busy:
            return
        chat = self.store.load(chat_id)
        if chat is None:
            self.history.reload()
            return
        switch = self._ask_switch(chat.entry)
        if switch is None:
            return
        self.agents = {}
        for key, session in chat.sessions.items():
            if key in tone_agent.AGENTS:
                agent = tone_agent.AGENTS[key]()
                agent.session = session
                self.agents[key] = agent
        index = self.provider.findData(chat.entry.provider)
        if index >= 0:
            self.provider.setCurrentIndex(index)
        self.chat_id, self.chat_title = chat.entry.id, chat.entry.title
        self.chat_created = chat.entry.created
        self.compare.clear()
        self.blocks = upgrade_blocks(chat.blocks)
        self.chat.setHtml("".join(self.blocks))
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.history_button.setChecked(False)
        self.status.setText("")
        if self.restore_hook is not None and chat.state:
            self.restore_hook(chat.state)
        if switch:
            self._select_preset(chat.entry.slot, chat.entry.preset)

    def _ask_switch(self, entry: ChatEntry) -> bool | None:
        """True = load the conversation's preset, False = keep the current one, None = cancel."""
        state = self.host.tools.editor.state
        slot = entry.slot
        if not slot or slot == state.current_slot or slot not in state.presets:
            return False
        if not state.dirty:
            return True
        current = state.presets.get(state.current_slot or 0)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Abrir conversación"))
        box.setText(tr(
            "Esta conversación era sobre el preset {slot:03d} «{name}».\nEl preset cargado "
            "({current_slot:03d} «{current_name}») tiene cambios sin guardar.",
            slot=slot,
            name=entry.preset,
            current_slot=state.current_slot,
            current_name=current.name if current else '',
        ))
        change = box.addButton(tr("Cambiar y descartar los cambios"),
                               QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton(tr("Abrir sin cambiar de preset"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is change:
            return True
        if box.clickedButton() is keep:
            return False
        return None

    def _select_preset(self, slot: int, name: str) -> None:
        self.status.setText(tr(
            "Cargando el preset {slot:03d} «{name}» en la pedalera…",
            slot=slot,
            name=name,
        ))

        def work() -> None:
            try:
                self.host.tools.select_preset(slot, discard_changes=True)
            except Exception as exc:  # shown under the chat
                self._done.status_text.emit(tr(
                    "No pude cambiar al preset {slot:03d}: {exc}",
                    slot=slot,
                    exc=exc,
                ))
                return
            self._done.status_text.emit(tr(
                "Preset {slot:03d} «{name}» cargado en la pedalera.",
                slot=slot,
                name=name,
            ))

        threading.Thread(target=work, name="ge150-chat-preset", daemon=True).start()

    def _show_status(self, text: str) -> None:
        self.status.setText(text)

    def _chat_deleted(self, chat_id: str) -> None:
        if chat_id == self.chat_id:
            self.chat_id = ""  # keep what is on screen, but it is no longer saved

    # --- results ----------------------------------------------------------------------

    def _finished(self, reply: object, error: str) -> None:
        self._end_tool_run()
        self._set_busy(False)
        state = self.host.tools.editor.state
        if self._before is not None and state.edit is not None:
            self.compare.offer(self._before, list(state.edit.words), state.current_slot)
        self._before = None
        if error == "stopped":
            self._note(tr(
                "Detenido. Lo que alcanzó a cambiar sigue aplicado (Deshacer lo revierte).",
            ))
        elif error:
            self._note(f"<span style='color:{ERROR}'>Error: {html.escape(error)}</span>")
        elif isinstance(reply, str):
            self._bubble(self.provider.currentText(), markdown.to_html(reply), AI)
        self._save()

    def _tool_called(self, tool: str) -> None:
        """One line per step; the same tool called again in a row counts up (x3)."""
        if not self.busy:
            return
        label = tr(LABELS.get(tool, tool))
        self.working.set_step(label)
        run = self._tool_run
        if run is not None and run[1] == tool and run[0] == len(self.blocks) - 1:
            self._tool_run = (run[0], tool, run[2] + 1)
            self.blocks[run[0]] = _tool_line(label, run[2] + 1, done=False)
            self._render_blocks()
            return
        self._end_tool_run()
        self._tool_run = (len(self.blocks), tool, 1)
        self._add(_tool_line(label, 1, done=False))

    def _end_tool_run(self) -> None:
        """The step before is over: tick it."""
        run, self._tool_run = self._tool_run, None
        if run is not None and run[0] < len(self.blocks):
            self.blocks[run[0]] = _tool_line(tr(LABELS.get(run[1], run[1])), run[2], done=True)
            self._render_blocks()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.ask_button.setIcon(icon("square", ACCENT, 12) if busy else
                                icon("arrow-up", "#1b1205", 16))
        self.ask_button.setToolTip(tr("Detener") if busy else tr("Enviar (Enter)"))
        self.ask_button.setProperty("busy", busy)
        self.ask_button.style().unpolish(self.ask_button)
        self.ask_button.style().polish(self.ask_button)
        self.undo_button.setEnabled(not busy)
        self.history_button.setEnabled(not busy)
        self.new_button.setEnabled(not busy)
        self.provider.setEnabled(not busy)
        self.options.setEnabled(not busy)
        self.prompt.setReadOnly(busy)
        if busy:
            self.status.setText("")
            self.working.start(text)
        else:
            self.working.stop()

    def _ready(self, ok: bool) -> None:
        for widget in (self.ask_button, self.undo_button, self.prompt, self.provider):
            widget.setEnabled(ok)

    def _add(self, block: str) -> None:
        self.blocks.append(block)
        self._render_blocks()

    def _render_blocks(self) -> None:
        """Whole-document render: ``append`` would continue an open list."""
        self.chat.setHtml("".join(self.blocks))
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _note(self, text: str) -> None:
        self._add(f"<p style='color:{MUTED}'>{text}</p>")

    def _bubble(self, who: str, body: str, colour: str) -> None:
        self._add(f"<p style='margin-top:12px'><b style='color:{colour}'>{html.escape(who)}</b>"
                  f"</p><div>{body}</div>")



_OLD_TOOL = re.compile(rf"^<div style='color:{MUTED}'>· (.*)</div>$")
_OLD_REPLY = re.compile(rf"^(<p style='margin-top:12px'><b style='color:{AI}'>.*?</b></p>)"
                        r"<div>(.*)</div>$", re.S)


def upgrade_blocks(blocks: Sequence[str]) -> list[str]:
    """Conversations saved before the Markdown renderer: replies back to Markdown and
    rendered again (tables, headings), and runs of the same step grouped (x9). Nested
    list indentation was not kept by the old renderer, so those items come out flat."""
    out: list[str] = []
    run: tuple[str, int] | None = None
    for block in blocks:
        tool = _OLD_TOOL.match(block)
        if tool:
            label = html.unescape(tool.group(1))
            if run is not None and run[0] == label:
                run = (label, run[1] + 1)
                out[-1] = _tool_line(label, run[1], done=True)
            else:
                run = (label, 1)
                out.append(_tool_line(label, 1, done=True))
            continue
        run = None
        reply = _OLD_REPLY.match(block)
        if reply and "<p>" not in reply.group(2):
            out.append(reply.group(1) + f"<div>{markdown.to_html(_old_to_markdown(reply.group(2)))}"
                       "</div>")
        else:
            out.append(block)
    return out


def _old_to_markdown(body: str) -> str:
    text = (body.replace("<ul>", "\n").replace("</ul>", "\n\n").replace("<li>", "- ")
            .replace("</li>", "\n").replace("<br>", "\n").replace("<b>", "**")
            .replace("</b>", "**").replace("<code>", "`").replace("</code>", "`"))
    return html.unescape(text)


def _tool_line(label: str, calls: int, done: bool) -> str:
    mark = "✓" if done else "\u203a"  # single right angle quote
    count = f" \u00d7{calls}" if calls > 1 else ""  # multiplication sign
    return f"<div style='color:{MUTED}'>{mark} {html.escape(label)}{count}</div>"

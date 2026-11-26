"""The editor hosts the MCP tools for ``ge150-mcp`` (Claude Code, Codex...).

Tool calls arrive on HTTP threads (``mcp_server/bridge.py``) and share the pedal lock with
the GUI's :class:`~ge150.ui.worker.TaskRunner`, so they never overlap. Writing the pedal's
memory asks the user in a dialog on the GUI thread; the calling thread waits for the answer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from ge150.i18n import tr
from ge150.mcp_server.bridge import LocalServer
from ge150.mcp_server.tools import GuardedTools, PedalTools
from ge150.services.editor import Editor
from ge150.ui.worker import TaskRunner

log = logging.getLogger(__name__)
ANSWER_TIMEOUT_S = 300
LABELS = {"get_catalog": "revisa el catálogo", "get_current_patch": "lee el sonido actual",
          "list_presets": "lee la lista de presets", "get_preset": "lee un preset",
          "list_user_irs": "lee las IR de usuario", "get_guitar": "lee tu guitarra",
          "get_settings": "lee los ajustes",
          "set_module": "cambia un módulo", "apply_patch": "aplica un sonido",
          "undo": "deshace", "select_preset": "cambia de preset", "save_preset": "guarda",
          "tone3000_search": "busca en TONE3000", "tone3000_get": "lee TONE3000",
          "load_tone3000_ir": "carga una IR de TONE3000", "get_presets": "lee varios presets",
          "propose_preset_edits": "propone cambios en varios presets"}


class AiHost(QObject):
    #: tool name, emitted for every call (on the GUI thread).
    called = Signal(str)
    #: (list[bulk_edit.PresetEdit], summary): stored-preset changes for the user to review.
    proposed = Signal(object, str)
    _ask = Signal(object, object)  # function to run on the GUI thread, answer box
    _call = Signal(str)

    def __init__(self, editor: Editor, runner: TaskRunner, parent: QWidget) -> None:
        super().__init__(parent)
        self.window = parent
        self.tools = PedalTools(editor, runner.lock, confirm=self.confirm,
                                pick_ir_slot=self.pick_ir_slot,
                                on_proposal=self.proposed.emit)
        self.server: LocalServer | None = None
        self._ask.connect(self._on_ask)
        self._call.connect(self.called)

    def start(self) -> None:
        if self.server is not None:
            return
        try:
            self.server = LocalServer(GuardedTools(self.tools), self._call.emit).start()
        except OSError as exc:  # the editor still works without it
            log.warning("cannot share the pedal with AI clients: %s", exc)

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None

    def confirm(self, question: str) -> bool:
        return bool(self._on_gui_thread(lambda: self._question(question)))

    def pick_ir_slot(self, question: str, slots: list[dict[str, Any]]) -> int:
        return int(self._on_gui_thread(lambda: self._pick(question, slots)) or 0)

    def _on_gui_thread(self, ask: Callable[[], Any]) -> Any:
        """Ask the user from any thread; the caller waits (no answer in time = no)."""
        if QThread.currentThread() is self.thread():
            return ask()
        box: dict[str, Any] = {"event": threading.Event(), "answer": None}
        self._ask.emit(ask, box)
        box["event"].wait(ANSWER_TIMEOUT_S)
        return box["answer"]

    def _on_ask(self, ask: Callable[[], Any], box: dict[str, Any]) -> None:
        try:
            box["answer"] = ask()
        finally:
            box["event"].set()

    def _pick(self, question: str, slots: list[dict[str, Any]]) -> int:
        from ge150.ui.dialogs import IrSlotPicker

        self.window.raise_()
        self.window.activateWindow()
        dialog = IrSlotPicker(question, slots, self.window)
        dialog.exec()
        return dialog.chosen()

    def _question(self, question: str) -> bool:
        self.window.raise_()
        self.window.activateWindow()
        answer = QMessageBox.question(self.window, tr("Asistente IA"), question,
                                      QMessageBox.StandardButton.Yes
                                      | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

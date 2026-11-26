"""Side panel (Ctrl+K): the AI chat docked next to the editor, for quick requests."""

from __future__ import annotations

from PySide6.QtWidgets import QDockWidget, QWidget

from ge150.i18n import tr
from ge150.ui.ai_chat import AiChat
from ge150.ui.ai_host import AiHost


class AIPanel(QDockWidget):
    def __init__(self, host: AiHost, parent: QWidget | None = None) -> None:
        super().__init__(tr("Asistente IA"), parent)
        self.setObjectName("aiPanel")
        self.body = AiChat(host)
        self.setWidget(self.body)
        self.setMinimumWidth(340)

    def refresh_context(self) -> None:
        self.body.refresh_context()

    def manage_guitars(self) -> None:
        self.body.manage_guitars()

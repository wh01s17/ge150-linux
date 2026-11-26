"""Model and reasoning-effort pills of the AI composer, remembered per provider.

Small rounded buttons inside the message box (like the AgentHub plugin for Obsidian), each
opening a menu. The model list comes from each CLI (``codex debug models`` runs off the GUI
thread); the efforts follow the chosen model. Empty values mean "the CLI's own default".
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from ge150.i18n import tr
from ge150.services import tone_agent
from ge150.ui.icons import icon
from ge150.ui.theme import ACCENT

EFFORT_LABELS = {"": "Predeterminado", "low": "Bajo", "medium": "Medio", "high": "Alto",
                 "xhigh": "Muy alto", "max": "Máximo", "ultra": "Ultra"}


class _Loaded(QObject):
    models = Signal(str, object)  # provider key, list[ModelChoice]


class Pill(QToolButton):
    """``icon value ⌄`` that opens a menu of checkable choices."""

    changed = Signal(str)

    def __init__(self, icon_name: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pill")
        self.setIcon(icon(icon_name, ACCENT, 13))
        self.setIconSize(QSize(13, 13))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setToolTip(tooltip)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_ = QMenu(self)
        self.menu_.setObjectName("pillMenu")
        self.setMenu(self.menu_)
        self.group = QActionGroup(self)
        self.group.triggered.connect(self._picked)
        self.value = ""

    def set_choices(self, choices: list[tuple[str, str]], value: str) -> None:
        self.menu_.clear()
        for action in self.group.actions():
            self.group.removeAction(action)
        ids = [cid for cid, _ in choices]
        self.value = value if value in ids else (ids[0] if ids else "")
        for cid, label in choices:
            action = QAction(label, self.menu_, checkable=True)
            action.setData(cid)
            action.setChecked(cid == self.value)
            self.group.addAction(action)
            self.menu_.addAction(action)
        self._show(dict(choices).get(self.value, ""))

    def _picked(self, action: QAction) -> None:
        self.value = str(action.data())
        self._show(action.text())
        self.changed.emit(self.value)

    def _show(self, label: str) -> None:
        self.setText(f"{label}  ⌄")


class AgentOptions(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = QSettings("ge150", "editor")
        self.provider = ""
        self.choices: dict[str, list[tone_agent.ModelChoice]] = {}
        self._loaded = _Loaded(self)
        self._loaded.models.connect(self._models_ready)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.model = Pill("sparkles", tr("Modelo de IA"))
        self.effort = Pill("gauge", tr("Esfuerzo de razonamiento: más alto piensa más y tarda más"))
        layout.addWidget(self.model)
        layout.addWidget(self.effort)
        self.model.changed.connect(lambda _: self._model_changed())
        self.effort.changed.connect(lambda _: self._remember())

    def set_provider(self, key: str) -> None:
        self.provider = key
        if key in self.choices:
            self._fill(key)
            return
        cls = tone_agent.AGENTS.get(key)
        if cls is None:
            return
        self.model.set_choices([("", tr("Predeterminado"))], "")
        self._fill_efforts(())

        def work() -> None:
            self._loaded.models.emit(key, cls.models())

        threading.Thread(target=work, name="ge150-models", daemon=True).start()

    def selection(self) -> tuple[str, str]:
        """(model id, effort) to pass to the agent."""
        return self.model.value, self.effort.value

    # --- internals ------------------------------------------------------------------

    def _models_ready(self, key: str, choices: object) -> None:
        self.choices[key] = list(choices)  # type: ignore[call-overload]
        if key == self.provider:
            self._fill(key)

    def _fill(self, key: str) -> None:
        saved = str(self.settings.value(f"ai/{key}/model", ""))
        self.model.set_choices([(c.id, tr(c.label)) for c in self.choices[key]], saved)
        self._model_changed()

    def _model_changed(self) -> None:
        choices = self.choices.get(self.provider, [])
        current = next((c for c in choices if c.id == self.model.value), None)
        self._fill_efforts(current.efforts if current else ())
        self._remember()

    def _fill_efforts(self, efforts: tuple[str, ...]) -> None:
        saved = str(self.settings.value(f"ai/{self.provider}/effort", ""))
        self.effort.set_choices([(e, tr(EFFORT_LABELS.get(e, e))) for e in ("", *efforts)], saved)

    def _remember(self) -> None:
        if not self.provider or self.provider not in self.choices:
            return
        model, effort = self.selection()
        self.settings.setValue(f"ai/{self.provider}/model", model)
        self.settings.setValue(f"ai/{self.provider}/effort", effort)

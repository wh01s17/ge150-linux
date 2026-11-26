"""Main window: preset list, signal chain, module editor, menus and status bar."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ge150.formats import mbf, mo_json
from ge150.i18n import tr
from ge150.model import device_state as ds
from ge150.model.preset import NAME_LEN, is_valid_name
from ge150.protocol.session import DeviceSession
from ge150.services import auto_backup, bulk_edit, reader, shortcuts
from ge150.services import backup as backup_service
from ge150.services.editor import Editor
from ge150.ui import scale
from ge150.ui.ai_host import LABELS, AiHost
from ge150.ui.ai_panel import AIPanel
from ge150.ui.assistant_page import AssistantPage
from ge150.ui.bridge import StateBridge
from ge150.ui.chain_view import ChainView
from ge150.ui.dialogs import IrDialog
from ge150.ui.exp_view import ExpPage
from ge150.ui.icons import icon
from ge150.ui.module_editor import ModuleEditor
from ge150.ui.preset_list import PresetList
from ge150.ui.settings_dialog import SettingsDialog
from ge150.ui.theme import TEXT
from ge150.ui.tone3000_page import Tone3000Page
from ge150.ui.worker import TaskRunner

LOADING_DOT_MS = 400  # like the AI's «trabajando...» (working.py)
BACKUP_STEPS = 3  # what backup_service.create_backup reports


class MainWindow(QMainWindow):
    def __init__(self, editor: Editor, session: DeviceSession) -> None:
        super().__init__()
        self.editor, self.session = editor, session
        self.state = editor.state
        self.runner = TaskRunner(self)
        self.bridge = StateBridge(self.state, session, self)
        self.ai_host = AiHost(editor, self.runner, self)
        self.ai_host.called.connect(
            lambda tool: self.statusBar().showMessage(tr(
                "IA: {label}",
                label=tr(LABELS.get(tool, tool)),
            ), 4000))
        self.setWindowTitle("GE150 Editor")
        self.resize(1280, 820)

        self.presets = PresetList(self.state)
        self.presets.setMinimumWidth(230)
        self.header = QLabel("Conectando…")  # full text, kept for status and tests
        self.header.hide()
        self.number = QLabel("-")
        self.number.setObjectName("presetNumber")
        self.name = QLabel("Conectando…")
        self.name.setObjectName("presetName")
        # While the pedal is read at start, the dots move: «Conectando.», «..», «...».
        self._dots = 0
        self._loading = QTimer(self)
        self._loading.setInterval(LOADING_DOT_MS)
        self._loading.timeout.connect(self._loading_tick)
        self.dirty_badge = QLabel(tr("MODIFICADO"))
        self.dirty_badge.setObjectName("dirtyBadge")
        self.dirty_badge.hide()
        self.discard_button = QPushButton(tr("Descartar"))
        self.discard_button.setObjectName("discardButton")
        self.discard_button.setIcon(icon("undo", TEXT, 15))
        self.discard_button.setToolTip(tr("Descartar los cambios y volver al preset guardado"))
        self.discard_button.clicked.connect(self.discard)
        self.discard_button.hide()
        self.save_button = QPushButton(tr("Guardar"))
        self.save_button.setObjectName("saveButton")
        self.save_button.setToolTip(tr("Guardar el preset en su ranura (Ctrl+S)"))
        self.save_button.clicked.connect(self.save_current)
        header_bar = QFrame()
        header_bar.setObjectName("headerBar")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(18, 10, 18, 10)
        self.logo = QLabel()
        self.logo.setObjectName("appLogo")
        logo_file = resources.files("ge150.data").joinpath("ge150.svg")
        self.logo.setPixmap(QIcon(str(logo_file)).pixmap(QSize(40, 40), self.devicePixelRatioF()))
        self.logo.setToolTip("GE150 Editor")
        header_layout.addWidget(self.logo)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.number)
        header_layout.addWidget(self.name)
        header_layout.addSpacing(14)
        header_layout.addWidget(self.dirty_badge)
        header_layout.addStretch(1)
        header_layout.addWidget(self.header)
        self.ai_button = QPushButton(tr("✦ IA"))
        self.ai_button.setObjectName("aiButton")
        self.ai_button.setCheckable(True)
        self.ai_button.setToolTip(tr("Asistente de tono con IA (Ctrl+K)"))
        header_layout.addWidget(self.ai_button)
        header_layout.addWidget(self.discard_button)
        header_layout.addWidget(self.save_button)

        self.chain = ChainView(editor)
        self.module_editor = ModuleEditor(editor)
        self.exp_page = ExpPage(editor)
        self.pages = QStackedWidget()  # a module's page, or the expression pedal's
        self.pages.addWidget(self.module_editor)
        self.pages.addWidget(self.exp_page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.presets)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1020])
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(12, 12, 12, 12)
        middle_layout.addWidget(splitter)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(header_bar)
        effects = QWidget()
        effects_layout = QVBoxLayout(effects)
        effects_layout.setContentsMargins(0, 0, 0, 0)
        effects_layout.setSpacing(0)
        effects_layout.addWidget(middle, 1)
        effects_layout.addWidget(self.chain)
        self.tone3000 = Tone3000Page(editor, self.runner)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(effects, tr("Efectos"))
        self.tabs.addTab(self.tone3000, "TONE3000")
        self.assistant = AssistantPage(editor, self.runner, self.ai_host)
        self.tabs.addTab(self.assistant, tr("Asistente"))
        self.ai_host.proposed.connect(self._proposed)
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.central = central

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().addPermanentWidget(self.progress)

        self.ai_panel = AIPanel(self.ai_host, self)
        self.ai_panel.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea
                                      | Qt.DockWidgetArea.LeftDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ai_panel)
        self.ai_panel.hide()
        self.ai_button.toggled.connect(self.ai_panel.setVisible)
        self.ai_panel.visibilityChanged.connect(self.ai_button.setChecked)
        toggle = QAction(self)
        toggle.setShortcut(QKeySequence("Ctrl+K"))
        toggle.triggered.connect(self.ai_button.toggle)
        self.addAction(toggle)

        self._build_menus()
        self.presets.activated.connect(self.load_preset)
        self.presets.action_requested.connect(self._preset_action)
        self.presets.move_requested.connect(self.move_preset)
        self.chain.module_selected.connect(self._show_module)
        self.chain.exp_requested.connect(self.open_exp)
        self.exp_page.error.connect(self._error)
        self.exp_page.message.connect(lambda text: self.statusBar().showMessage(text, 8000))
        self.exp_page.save_requested.connect(self.save_current)
        self.chain.error.connect(self._error)
        self.module_editor.error.connect(self._error)
        self.bridge.changed.connect(self._on_change)
        self.bridge.disconnected.connect(self._on_disconnect)
        self.bridge.progress.connect(self._on_progress)
        self.runner.busy_changed.connect(lambda busy: self.progress.setVisible(busy))
        self.runner.busy_changed.connect(self.presets.set_busy)
        self.central.setEnabled(False)

    # --- menus ---------------------------------------------------------------

    def _action(self, menu_name: str, text: str, slot, shortcut: str | None = None) -> QAction:  # type: ignore[no-untyped-def]
        menu = self._menus.get(menu_name) or self.menuBar().addMenu(tr(menu_name))
        self._menus[menu_name] = menu
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        self._menus: dict[str, Any] = {}
        self._action("&Archivo", tr(
            "Importar .mo al preset seleccionado…",
        ), self.import_mo, "Ctrl+I")
        self._action("&Archivo", tr("Exportar preset seleccionado…"), self.export_mo, "Ctrl+E")
        self._action("&Archivo", tr("Exportar los 200 presets…"), self.export_all)
        self._action("&Archivo", tr("Guardar snapshot JSON…"), self.save_snapshot)
        self._action("&Archivo", tr("Backup completo (.mbf)…"), self.backup)
        self._action("&Archivo", tr("Restaurar backup (.mbf)…"), self.restore)
        self._action("&Archivo", tr("Abrir carpeta de backups automáticos"),
                     self.open_auto_backups)
        archive = self._menus["&Archivo"]
        archive.addSeparator()
        self.shortcuts_action = self._action("&Archivo", tr("Crear accesos directos…"),
                                             self.toggle_shortcuts)
        archive.aboutToShow.connect(self._refresh_shortcuts_action)
        self._refresh_shortcuts_action()
        archive.addSeparator()
        self._action("&Archivo", tr("Salir"), self.close, "Ctrl+Q")
        self._action("&Preset", tr("Guardar"), self.save_current, "Ctrl+S")
        self._action("&Preset", tr("Guardar en otra ranura…"), self.save_as, "Ctrl+Shift+S")
        self._action("&Preset", tr(
            "Renombrar…",
        ), lambda: self.rename(self.state.current_slot), "F2")
        self._action("&Preset", tr("Descartar cambios"), self.discard)
        self._action("&Herramientas", tr("IR de usuario…"), self.open_ir)
        self._action("&Herramientas", tr("Pedal de expresión…"), self.open_exp)
        self._action("&Herramientas", tr("Sistema y caja de ritmos…"), self.open_settings)
        self._action("&Herramientas", tr("Mis guitarras…"), lambda: self.ai_panel.manage_guitars())
        self._action("&Herramientas", tr("Restaurar valores de fábrica…"), self.factory_reset)
        self._action("&Herramientas", tr("Volver a leer la pedalera"), self.start_sync, "F5")
        self._build_size_menu()
        self._action("Ay&uda", tr("Acerca de"), self.about)

    # --- shortcuts to open the editor (pipx/pip installs bring none) -------------

    SHORTCUTS_ASKED = "shortcuts/asked"

    def offer_shortcuts(self) -> None:
        """At the first start: offer the menu entry (and desktop icon) that fit this desktop.
        Asked once; Archivo → Crear accesos directos stays for later."""
        settings = QSettings("ge150", "editor")
        if settings.value(self.SHORTCUTS_ASKED, False, bool) or shortcuts.installed():
            return
        settings.setValue(self.SHORTCUTS_ASKED, True)
        plan = shortcuts.plan()
        box = QMessageBox(self)
        box.setWindowTitle(tr("Accesos directos"))
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(tr(
            "¿Agregar {name} a {location}?",
            name=shortcuts.NAME,
            location=plan.describe(),
        ))
        box.setInformativeText(tr("Si no, puedes hacerlo cuando quieras desde Archivo → Crear "
                               "accesos directos."))
        create = box.addButton(tr("Agregar"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Ahora no"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(create)
        box.exec()
        if box.clickedButton() is create:
            self._create_shortcuts()

    def _own_shortcuts(self) -> bool:
        return any(path.is_file() for path in (shortcuts.plan().menu, shortcuts.plan().desktop)
                   if path is not None)

    def _refresh_shortcuts_action(self) -> None:
        plan = shortcuts.plan()
        if plan.menu is None and plan.desktop is None:
            self.shortcuts_action.setText(tr("Accesos directos (ya los instaló el sistema)"))
            self.shortcuts_action.setEnabled(False)
            return
        self.shortcuts_action.setEnabled(True)
        self.shortcuts_action.setText(tr("Quitar accesos directos") if self._own_shortcuts()
                                      else tr("Crear accesos directos…"))

    def toggle_shortcuts(self) -> None:
        if not self._own_shortcuts():
            self._create_shortcuts()
        elif self._confirm(tr(
            "¿Quitar {name} de {location}?",
            name=shortcuts.NAME,
            location=shortcuts.plan().describe(),
        )):
            try:
                shortcuts.remove()
            except OSError as exc:
                self._error(tr("No se pudieron quitar los accesos directos: {exc}", exc=exc))
                return
            self.statusBar().showMessage(tr("Accesos directos quitados"), 5000)
        self._refresh_shortcuts_action()

    def _create_shortcuts(self) -> None:
        plan = shortcuts.plan()
        try:
            shortcuts.install()
        except OSError as exc:
            self._error(tr("No se pudieron crear los accesos directos: {exc}", exc=exc))
            return
        self.statusBar().showMessage(tr(
            "{name} ya está en {location}",
            name=shortcuts.NAME,
            location=plan.describe(),
        ), 8000)
        self._refresh_shortcuts_action()

    def _build_size_menu(self) -> None:
        """Ver → Tamaño de la interfaz: automatic (from the screen) or a fixed scale."""
        menu = self._menus.get("&Ver") or self.menuBar().addMenu(tr("&Ver"))
        self._menus["&Ver"] = menu
        sizes = menu.addMenu(tr("Tamaño de la interfaz"))
        group = QActionGroup(self)
        current = scale.preference()
        for value in scale.CHOICES:
            label = (tr("Automático (según la pantalla)") if value == scale.AUTO
                     else f"{float(value) * 100:.0f} %")
            action = QAction(label, self, checkable=True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _=False, v=value: self.set_interface_size(v))
            group.addAction(action)
            sizes.addAction(action)

    def set_interface_size(self, value: str) -> None:
        if value == scale.preference():
            return
        scale.set_preference(value)
        answer = QMessageBox.question(
            self, tr("Tamaño de la interfaz"),
            tr("El nuevo tamaño se aplica al reiniciar el editor. ¿Reiniciarlo ahora?"))
        if answer == QMessageBox.StandardButton.Yes and self.close():
            QApplication.exit(scale.RESTART)

    def _proposed(self, edits: object, summary: str) -> None:
        """The AI proposes changes to stored presets: review them in «Asistente»."""
        self.assistant.show_proposal(list(edits), summary)  # type: ignore[call-overload]
        self.tabs.setCurrentWidget(self.assistant)

    # --- sync ------------------------------------------------------------------

    def start_sync(self) -> None:
        self.central.setEnabled(False)
        self._dots = 0
        self._loading_tick()
        self._loading.start()
        self.runner.run(self.editor.sync, self._synced, self._error)

    def _loading_tick(self) -> None:
        self._dots = self._dots % 3 + 1
        dots = "." * self._dots
        self.name.setText(tr("Conectando{dots}", dots=dots))
        self.statusBar().showMessage(tr("Leyendo la pedalera{dots}", dots=dots))

    def _stop_loading(self) -> None:
        if self._loading.isActive():
            self._loading.stop()
            self.statusBar().clearMessage()

    def _synced(self, _: object) -> None:
        self._stop_loading()
        info = self.state.info
        version = f"{info.name} {info.firmware}" if info else "?"
        self.status_label.setText(f"{version}  ·  {self.session.transport.description}")
        self.statusBar().showMessage(tr("Listo"), 3000)
        self.central.setEnabled(True)
        self.presets.reload()
        self.assistant.reload_presets()
        self._refresh_current()
        self.module_editor.show_module(self.chain.selected)
        self.ai_host.start()
        self.ai_panel.refresh_context()
        self.assistant.chat.refresh_context()

    # --- state changes (GUI thread) ------------------------------------------

    def _on_change(self, kind: str, slot: int, module: int) -> None:
        if kind == ds.KIND_PRESETS:
            self.presets.reload()
            self.assistant.reload_presets()
        elif kind == ds.KIND_PRESET:
            self.presets.update_slot(slot)
            if slot == self.state.current_slot and self.pages.currentWidget() is self.exp_page:
                self.exp_page.refresh()  # saved: the unsaved-change notice goes
            self.assistant.update_slot(slot)
            self.ai_panel.refresh_context()
            self.assistant.chat.refresh_context()
        elif kind in (ds.KIND_CURRENT, ds.KIND_SELECTED):
            self._refresh_current()
            self.ai_panel.refresh_context()
            self.assistant.chat.refresh_context()
            self.assistant.current_changed()
        elif kind == ds.KIND_USER_NAMES:
            self.chain.refresh()
            self.module_editor.show_module(self.module_editor.index)
        elif kind in (ds.KIND_MODULE, ds.KIND_EXP):
            self.chain.refresh()
            if self.pages.currentWidget() is self.exp_page:
                self.exp_page.refresh()
            if module in (-1, self.module_editor.index):
                self.module_editor.refresh()
            self._refresh_header()
            if self.state.current_slot:
                self.presets.update_slot(self.state.current_slot)

    def _refresh_current(self) -> None:
        self.chain.refresh()
        self.module_editor.refresh()
        if self.pages.currentWidget() is self.exp_page:
            self.exp_page.refresh()
        self.presets.mark_current()
        self._refresh_header()

    def _refresh_header(self) -> None:
        slot = self.state.current_slot
        stored = self.state.presets.get(slot or 0)
        name = stored.name if stored else ""
        mark = tr("   (modificado)") if self.state.dirty else ""
        self.header.setText(f"{slot or '-':>3}  {name}{mark}")
        self.number.setText(f"{slot:03d}" if slot else "-")
        self.name.setText(name.strip() or "-")
        self.dirty_badge.setVisible(self.state.dirty)
        self.discard_button.setVisible(self.state.dirty)

    def _on_disconnect(self, reason: str) -> None:
        self._stop_loading()
        self.central.setEnabled(False)
        self.status_label.setText(tr("Desconectada"))
        message = tr(
            "Se perdió la conexión con la GE150:\n{reason}\n\n"
            "Vuelve a conectarla y reinicia el editor.", reason=reason,
        )
        QMessageBox.warning(self, tr("Pedalera desconectada"), message)

    def _error(self, message: str) -> None:
        self._stop_loading()
        self.statusBar().showMessage(f"Error: {message}", 8000)
        QMessageBox.warning(self, "Error", message)

    # --- preset actions ------------------------------------------------------

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.question(self, tr("Confirmar"), text)
        return answer == QMessageBox.StandardButton.Yes

    def _resolve_dirty(self) -> bool:
        """Ask what to do with unsaved edits. False = cancel the operation."""
        if not self.state.dirty:
            return True
        answer = QMessageBox.question(
            self, tr("Cambios sin guardar"),
            tr("El preset actual tiene cambios sin guardar. ¿Guardarlos antes de continuar?"),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.editor.save()
        return True

    def load_preset(self, slot: int) -> None:
        if slot == self.state.current_slot:
            return
        if not self._resolve_dirty():
            self.presets.select_slot(self.state.current_slot)  # cancelled: stay on it
            return
        self.runner.run(lambda: self.editor.select(slot), None, self._error)

    def save_current(self) -> None:
        slot = self.state.current_slot
        stored = self.state.presets.get(slot or 0)
        if slot and self._confirm(tr(
            "¿Guardar los cambios en el preset {slot} ({name})?",
            slot=slot,
            name=stored.name if stored else '',
        )):
            self.runner.run(self.editor.save, lambda _: self._refresh_header(), self._error)

    def save_as(self) -> None:
        slot, ok = QInputDialog.getInt(self, tr("Guardar en"), tr("Ranura de destino (1-200):"),
                                       self.presets.selected_slot() or 1, 1, 200)
        if not ok:
            return
        current = self.state.presets.get(self.state.current_slot or 0)
        name, ok = QInputDialog.getText(self, tr("Nombre"), tr("Nombre del preset:"),
                                        text=current.name if current else "")
        if not ok or not self._valid_name(name):
            return
        target = self.state.presets.get(slot)
        if self._confirm(tr(
            "¿Sobrescribir el preset {slot} ({name})?",
            slot=slot,
            name=target.name if target else '',
        )):
            self.runner.run(lambda: self.editor.save(slot, name), None, self._error)

    def _valid_name(self, name: str) -> bool:
        if is_valid_name(name):
            return True
        QMessageBox.warning(self, tr("Nombre no válido"),
                            tr(
                                "Hasta {NAME_LEN} caracteres: letras, números, '-' y espacio.",
                                NAME_LEN=NAME_LEN,
                            ))
        return False

    def rename(self, slot: int | None) -> None:
        stored = self.state.presets.get(slot or 0)
        if stored is None:
            return
        name, ok = QInputDialog.getText(self, tr(
            "Renombrar",
        ), tr(
            "Nuevo nombre del preset {slot}:",
            slot=slot,
        ),
                                        text=stored.name)
        if ok and name != stored.name and self._valid_name(name):
            self.runner.run(lambda: self.editor.rename(stored.slot, name), None, self._error)

    def discard(self) -> None:
        slot = self.state.current_slot
        if slot and self.state.dirty and self._confirm(tr("¿Descartar los cambios sin guardar?")):
            self.runner.run(lambda: self.editor.select(slot), None, self._error)

    def _preset_action(self, action: str, slot: int) -> None:
        if action == "rename":
            self.rename(slot)
        elif action == "copy":
            self.presets.clipboard = slot
            self.statusBar().showMessage(tr("Preset {slot} copiado", slot=slot), 3000)
        elif action == "paste" and self.presets.clipboard is not None:
            source = self.presets.clipboard
            target = self.state.presets.get(slot)
            if self._confirm(tr(
                "¿Sobrescribir el preset {slot} ({name}) con el {source}?",
                slot=slot,
                name=target.name if target else '',
                source=source,
            )):
                self.runner.run(lambda: self.editor.copy_preset(source, slot), None, self._error)
        elif action == "export":
            self.export_mo(slot)
        elif action == "import":
            self.import_mo(slot)

    def move_preset(self, source: int, target: int) -> None:
        """Dragged in the list: ``source`` takes slot ``target`` and the presets in between
        shift one place (all of them are rewritten, after an automatic backup)."""
        moving = self.state.presets.get(source)
        if self.runner.busy or moving is None:
            return
        low, high = sorted((source, target))
        shift = tr("bajan") if source > target else tr("suben")
        if not self._confirm(
                tr(
                    "¿Mover el preset {source:03d} «{name}» a la posición {target:03d}?\n\n"
                    "Los presets entre medio {shift} un lugar: se reescriben {count} presets "
                    "en la pedalera. Antes se hace un backup automático completo.",
                    source=source,
                    name=moving.name,
                    target=target,
                    shift=shift,
                    count=high - low + 1,
                )):
            return
        current = self.state.current_slot
        if current is not None and low <= current <= high and not self._resolve_dirty():
            return

        count = high - low + 1
        steps = BACKUP_STEPS + count

        def backup_step(_label: str, step: int, _total: int) -> None:
            self._progress(tr("Haciendo un backup de la pedalera…"), step, steps)

        def write_step(slot: str, step: int, _total: int) -> None:
            self._progress(tr(
                "Escribiendo preset {slot} ({current} de {count})…",
                slot=slot,
                current=step + 1,
                count=count,
            ),
                           BACKUP_STEPS + step, steps)

        def work() -> bulk_edit.Applied:
            if self.state.dirty and current is not None:  # changes discarded: reload it
                self.editor.select(current)
            return bulk_edit.move(
                self.editor, source, target,
                lambda: auto_backup.save_before(self.editor, "mover", backup_step), write_step)

        def failed(message: str) -> None:
            self.presets.end_work()
            self._error(message)

        def done(result: bulk_edit.Applied) -> None:
            self.presets.end_work()
            saved = tr(
                " Backup previo: {backup}",
                backup=auto_backup.short(result.backup),
            ) if result.backup else ""
            self.statusBar().showMessage(
                tr(
                    "Preset «{name}» movido a la posición {target:03d}.{saved}",
                    name=moving.name,
                    target=target,
                    saved=saved,
                ), 8000)
            failed = [f"{slot:03d}: {why}" for slot, why in result.skipped
                      if why != "sin cambios"]
            if failed:
                self._error(tr("No se reescribieron todos los presets:\n") + "\n".join(failed))

        text = tr("Moviendo «{name}» a la posición {target:03d}…", name=moving.name, target=target)
        self.presets.start_work(text)
        self.statusBar().showMessage(text)
        self.runner.run(work, done, failed)

    # --- files ---------------------------------------------------------------

    def export_mo(self, slot: int | None = None) -> None:
        slot = slot or self.presets.selected_slot() or self.state.current_slot
        preset = self.state.presets.get(slot or 0)
        if preset is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar preset"), f"{preset.name}.mo",
                                              tr("Preset GE150 (*.mo)"))
        if path:
            mo_json.save(path, preset)
            self.statusBar().showMessage(tr("Exportado {path}", path=path), 4000)

    def export_all(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, tr("Carpeta de destino"))
        if not directory:
            return
        for slot, preset in sorted(self.state.presets.items()):
            clean = "".join(c if c.isalnum() or c in " -_" else "_" for c in preset.name).strip()
            mo_json.save(Path(directory) / f"{slot:03d} {clean or 'unnamed'}.mo", preset)
        self.statusBar().showMessage(tr(
            "{count} presets exportados",
            count=len(self.state.presets),
        ), 4000)

    def import_mo(self, slot: int | None = None) -> None:
        slot = slot or self.presets.selected_slot()
        base = self.state.presets.get(slot or 0)
        if base is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr(
            "Importar preset",
        ), "", tr(
            "Preset GE150 (*.mo)",
        ))
        if not path:
            return
        try:
            preset = mo_json.load(path, base)
        except (OSError, mo_json.MoError) as exc:
            self._error(str(exc))
            return
        if self._confirm(tr(
            "¿Sobrescribir el preset {slot} ({old_name}) con «{new_name}»?",
            slot=base.slot,
            old_name=base.name,
            new_name=preset.name,
        )):
            self.runner.run(lambda: self.editor.copy_preset_data(preset), None, self._error)

    def save_snapshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar snapshot"), "ge150_snapshot.json",
                                              "JSON (*.json)")
        if not path:
            return

        def done(data: object) -> None:
            Path(path).write_text(json.dumps(data, indent=1) + "\n")
            self.statusBar().showMessage(tr("Snapshot guardado en {path}", path=path), 4000)

        self.runner.run(lambda: reader.snapshot(self.session), done, self._error)

    def _progress(self, label: str, step: int, total: int) -> None:
        # Called from the worker thread: only touch Qt through queued signals.
        self.bridge.progress.emit(label, step, total)

    def backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Backup completo"), "GE150.mbf",
                                              tr("Backup GE150 (*.mbf)"))
        if not path:
            return

        def done(result: object) -> None:
            assert isinstance(result, mbf.Backup)
            mbf.save(path, result)
            self.statusBar().showMessage(
                tr(
                    "Backup guardado: {presets} presets, {irs} IR",
                    presets=len(result.presets),
                    irs=len(result.irs),
                ), 6000)

        self.runner.run(lambda: backup_service.create_backup(self.editor, self._progress),
                        done, self._error)

    def open_auto_backups(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ge150.services.auto_backup import backup_dir

        directory = backup_dir()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr(
            "Restaurar backup",
        ), "", tr(
            "Backup GE150 (*.mbf)",
        ))
        if not path:
            return
        try:
            data = mbf.load(path)
        except (OSError, mbf.MbfError) as exc:
            self._error(str(exc))
            return
        if not self._confirm(tr(
            "Se SOBRESCRIBIRÁN los 200 presets y {irs} IR de la "
            "pedalera con el backup. ¿Continuar?",
            irs=len(data.irs),
        )):
            return
        self.runner.run(lambda: backup_service.restore_backup(self.editor, data, self._progress),
                        lambda _: self.start_sync(), self._error)

    def factory_reset(self) -> None:
        """DESTRUCTIVE, with two confirmations and an optional backup first."""
        box = QMessageBox(QMessageBox.Icon.Warning, tr("Restaurar valores de fábrica"),
                          tr("Se BORRARÁN los 200 presets, las IR y capturas NR de usuario y los "
                          "ajustes de sistema: la pedalera vuelve a como salió de fábrica.\n\n"
                          "Se recomienda hacer antes un backup completo."), parent=self)
        with_backup = box.addButton(tr("Hacer backup y continuar…"),
                                    QMessageBox.ButtonRole.AcceptRole)
        without = box.addButton(tr("Continuar sin backup"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() not in (with_backup, without):
            return
        path = ""
        if box.clickedButton() is with_backup:
            path, _ = QFileDialog.getSaveFileName(self, tr("Backup antes del reset"), "GE150.mbf",
                                                  tr("Backup GE150 (*.mbf)"))
            if not path:
                return
        phrase = tr("BORRAR TODO")
        text, ok = QInputDialog.getText(self, tr("Confirmar reset de fábrica"),
                                        tr(
                                            "Escribe «{phrase}» para restaurar la pedalera:",
                                            phrase=phrase,
                                        ))
        if not ok or text.strip() != phrase:
            return

        def task() -> None:
            if path:
                mbf.save(path, backup_service.create_backup(self.editor, self._progress))
            backup_service.factory_reset(self.editor)

        self.central.setEnabled(False)
        self.statusBar().showMessage(tr("Restaurando valores de fábrica…"))
        self.runner.run(task, self._synced, self._error)

    def _on_progress(self, label: str, step: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(step)
        self.statusBar().showMessage(label)
        if self.presets.working:
            self.presets.work.step(label, step, total)

    # --- tools -----------------------------------------------------------------

    def open_ir(self) -> None:
        IrDialog(self.editor, self.runner, self).exec()

    def _show_module(self, index: int) -> None:
        self.pages.setCurrentWidget(self.module_editor)
        self.module_editor.show_module(index)

    def open_exp(self) -> None:
        """The EXP tile (or Herramientas → Pedal de expresión): its page in the module area."""
        if self.state.edit is None:
            return
        self.chain.select_exp()
        self.exp_page.load()
        self.pages.setCurrentWidget(self.exp_page)

    def open_settings(self) -> None:
        SettingsDialog(self.editor, self).exec()

    def about(self) -> None:
        from ge150.ui.about_dialog import AboutDialog

        connection = self.session.transport.description if self.state.info else ""
        AboutDialog(self.state, connection, self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        from ge150.services import tone_agent

        tone_agent.stop_all()
        self.ai_host.stop()
        self.runner.wait(5000)
        self.bridge.detach()
        super().closeEvent(event)

"""GUI smoke tests (offscreen Qt + simulator)."""

import os
import time
from collections.abc import Callable, Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from ge150.i18n import set_language  # noqa: E402
from ge150.protocol.session import DeviceSession  # noqa: E402
from ge150.services.editor import Editor  # noqa: E402
from ge150.sim.sim_transport import SimTransport  # noqa: E402
from ge150.ui.ai_permissions_dialog import AiPermissionsDialog  # noqa: E402
from ge150.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app: QApplication, monkeypatch: pytest.MonkeyPatch,
           tmp_path: "os.PathLike[str]") -> Iterator[MainWindow]:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # the AI bridge discovery file
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # guitars, TONE3000 session
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # AI conversations, IR sources
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    sim = SimTransport()
    session = DeviceSession(sim, frame_delay=0, multipart_delay=0).start()
    win = MainWindow(Editor(session), session)
    win.sim = sim  # type: ignore[attr-defined]
    win.start_sync()
    assert wait(lambda: win.central.isEnabled())
    yield win
    win.close()
    session.close()


def wait(predicate: Callable[[], bool], timeout_ms: int = 3000) -> bool:
    for _ in range(timeout_ms // 10):
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return predicate()


def test_sync_populates_widgets(window: MainWindow) -> None:
    assert window.presets.list.count() == 200
    assert "PRESET 001" in window.header.text()
    assert window.chain.tiles[0].model.text().startswith("02:")
    assert window.module_editor.model.count() == 8


def test_english_catalog_translates_editor_widgets(request: pytest.FixtureRequest) -> None:
    set_language("en")
    request.addfinalizer(lambda: set_language("es"))
    window: MainWindow = request.getfixturevalue("window")

    assert window.tabs.tabText(0) == "Effects"
    assert window.save_button.text() == "Save"
    assert window.presets.search.placeholderText() == "Search presets…"
    assert window.menuBar().actions()[0].text() == "&File"
    chat = window.ai_panel.body
    assert window.ai_panel.windowTitle() == "AI assistant"
    assert chat.prompt.placeholderText().startswith("Describe the sound")
    assert chat.history.search.placeholderText() == "Search conversations"
    assert window.assistant.ticks_chip.text() == "No presets checked"
    assert window.assistant.review.tree.headerItem().text(1) == "Before"
    assert "No pending changes" in window.assistant.review.empty.text()
    dialog = AiPermissionsDialog(window)
    assert dialog.windowTitle() == "Assistant permissions"
    assert dialog.switches["save"].accessibleName() == "Save presets"
    assert window.presets.list.count() == 200
    assert "PRESET 001" in window.header.text()
    window.chain.select(3)
    assert window.module_editor.model.sections() == [
        "Pedal models", "Your IRs · user slots (TONE3000 and others)",
    ]


def test_editing_a_parameter_reaches_the_pedal(window: MainWindow) -> None:
    window.chain.select(8)  # REVERB
    widget = window.module_editor.params[1]  # LEVEL
    widget.setValue(88)
    sim = window.sim  # type: ignore[attr-defined]
    assert wait(lambda: sim.device.edit_buffer.words[60 + 3] == 88)
    assert "(modificado)" in window.header.text()


def test_changing_the_model_rebuilds_parameters(window: MainWindow) -> None:
    window.chain.select(7)  # DELAY
    window.module_editor.model.setCurrentIndex(8)  # DUAL DELAY
    sim = window.sim  # type: ignore[attr-defined]
    assert wait(lambda: sim.device.edit_buffer.module(7).type == 8)
    assert [w.param.name for w in window.module_editor.params][-2:] == ["TIME B", "SUB B"]


def test_user_slots_have_their_own_section(window: MainWindow) -> None:
    gallery = window.module_editor.model
    window.chain.select(3)  # CAB: 36 models, 27-36 are user IR slots
    assert gallery.count() == 36 and gallery.count_rows() == 38
    assert gallery.sections() == ["Modelos de la pedalera",
                                  "Tus IR · ranuras de usuario (TONE3000 y otras)"]
    first_user = gallery.model_item(26)
    assert first_user is not None and first_user.text().startswith("27:")
    gallery.setCurrentIndex(30)
    assert gallery.currentIndex() == 30
    sim = window.sim  # type: ignore[attr-defined]
    assert wait(lambda: sim.device.edit_buffer.module(3).type == 30)
    window.chain.select(8)  # REVERB: no user slots, no headers
    assert gallery.sections() == [] and gallery.count() == gallery.count_rows()
    window.chain.select(2)  # AMP: the NR slots 56-65 are hidden, indexes stay
    assert gallery.sections() == [] and gallery.count() == 65 and gallery.count_rows() == 55
    assert gallery.model_item(54) is not None and gallery.model_item(55) is None
    gallery.setCurrentIndex(60)
    assert gallery.currentIndex() == -1


def test_user_ir_tooltip_lists_the_presets_using_it(window: MainWindow) -> None:
    editor, page = window.editor, window.module_editor
    cab = 3
    preset = editor.state.presets[7].copy()
    block = preset.module(cab)
    block.words[1] = 27  # CAB 28: user IR slot 2
    preset.set_module(block)
    editor.copy_preset_data(preset)
    window.chain.select(cab)
    text = page._model_users(27)
    assert text.startswith("Usada en:") and "007" in text
    assert page._model_users(28) == "No la usa ningún preset."
    assert page._model_users(0) == ""  # a factory cabinet: no list


def test_power_button_switches_module(window: MainWindow) -> None:
    tile = window.chain.tiles[1]
    before = tile.power.isChecked()
    tile.power.click()
    sim = window.sim  # type: ignore[attr-defined]
    assert wait(lambda: bool(sim.device.edit_buffer.module(1).switch) != before)


def test_load_and_save_preset(window: MainWindow) -> None:
    window.load_preset(42)
    assert wait(lambda: "PRESET 042" in window.header.text())
    assert window.state.current_slot == 42
    window.chain.select(0)
    window.module_editor.params[0].setValue(1)
    assert wait(lambda: window.state.dirty)
    window.save_current()
    assert wait(lambda: not window.state.dirty)
    sim = window.sim  # type: ignore[attr-defined]
    assert sim.device.presets[42].words == window.state.edit.words  # type: ignore[union-attr]


def test_discard_button_restores_the_saved_preset(window: MainWindow) -> None:
    assert window.discard_button.isHidden()
    saved = list(window.state.presets[1].words)
    window.chain.select(8)  # REVERB
    window.module_editor.params[1].setValue(7)
    assert wait(lambda: window.state.dirty)
    assert not window.discard_button.isHidden()
    window.discard_button.click()
    assert wait(lambda: not window.state.dirty and window.discard_button.isHidden())
    sim = window.sim  # type: ignore[attr-defined]
    assert window.state.edit.words == saved  # type: ignore[union-attr]
    assert sim.device.edit_buffer.words == saved


def test_pedal_notification_updates_list(window: MainWindow) -> None:
    sim = window.sim  # type: ignore[attr-defined]
    window.presets.select_slot(10)
    sim.inject(sim.device.footswitch(11))
    assert wait(lambda: window.header.text().strip().startswith("11"))
    assert window.number.text() == "011"
    current = window.presets.list.currentItem()
    from ge150.ui.preset_list import SLOT_ROLE

    assert current is not None and current.data(SLOT_ROLE) == 11
    assert window.state.edit.words == sim.device.presets[11].words  # type: ignore[union-attr]


def test_settings_dialog_writes_to_pedal(window: MainWindow) -> None:
    from ge150.protocol.opcodes import Op
    from ge150.ui.settings_dialog import SettingsDialog

    device = window.sim.device  # type: ignore[attr-defined]
    dialog = SettingsDialog(window.editor, window)
    assert dialog.input_level.currentText() == "0 dB" and dialog.screen.value() == 20
    dialog.input_level.setCurrentIndex(10)  # 0.5 dB
    dialog.cab_right.setCurrentText("THRU")
    dialog.bpm.setValue(121)
    dialog.style.setValue(5)
    dialog.mode.setCurrentText("METRONOME")
    assert wait(lambda: device.rhythm[1] == 1)
    assert device.system[Op.WRITE_SYSTEM_1] == bytes([10, 0])
    assert device.system[Op.WRITE_SYSTEM_3] == bytes([1, 0, 0, 0])
    assert device.rhythm == bytes([0, 1, 0, 5, 121, 0])  # style restarts with the mode
    assert dialog.style.maximum() == 10
    dialog.close()


def test_cab_microphone_drag_sets_center_and_distance(window: MainWindow) -> None:
    window.chain.select(3)  # CAB
    page = window.module_editor
    assert page.mic.isVisibleTo(page)
    page.mic.moved.emit(0.2, 0.8)
    sim = window.sim  # type: ignore[attr-defined]
    assert wait(lambda: sim.device.edit_buffer.words[19 + 4] == 20)
    assert sim.device.edit_buffer.words[19 + 5] == 80
    window.chain.select(1)  # DS has no microphone
    assert not page.mic.isVisibleTo(page)


def test_gear_pictures_follow_the_model(window: MainWindow) -> None:
    window.chain.select(1)
    window.module_editor.model.setCurrentIndex(1)  # 808
    assert wait(lambda: window.module_editor.view.gear.gid == "ts808")
    assert "TS808" in window.module_editor.based_on.text()
    assert window.chain.tiles[1].view.gear.gid == "ts808"


def test_single_click_loads_preset_and_asks_when_dirty(
        window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    presets = window.presets.list

    def click(slot: int) -> None:
        item = presets.item(slot - 1)
        QTest.mouseClick(presets.viewport(), Qt.MouseButton.LeftButton,
                         pos=presets.visualItemRect(item).center())

    click(5)
    assert wait(lambda: window.state.current_slot == 5)
    window.module_editor.params[0].setValue(3)
    assert wait(lambda: window.state.dirty)
    asked: list[str] = []

    def cancel(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", cancel)
    click(9)
    assert asked and "sin guardar" in asked[0]
    assert window.state.current_slot == 5
    assert window.presets.selected_slot() == 5


def test_ai_clients_drive_the_open_editor(window: MainWindow) -> None:
    import threading

    from ge150.mcp_server.bridge import find_editor

    remote = find_editor()
    assert remote is not None
    results: dict[str, object] = {}

    def client() -> None:  # an AI client, on its own thread like ge150-mcp
        remote.set_module("AMP", True, "PLX100", {"GAIN": "77"})
        results["saved"] = remote.save_preset(30, "AI PLEXI")  # dialog answered "Yes"

    thread = threading.Thread(target=client)
    thread.start()
    assert wait(lambda: not thread.is_alive(), 8000)
    assert results["saved"] == {"saved": True, "slot": 30, "name": "AI PLEXI"}
    assert wait(lambda: "PLX100" in window.chain.tiles[2].model.text())
    assert window.state.presets[30].name == "AI PLEXI"
    window.close()
    assert find_editor() is None


def test_ai_panel_sends_the_selected_preset_unless_unticked(window: MainWindow) -> None:
    sim = window.sim  # type: ignore[attr-defined]
    sim.inject(sim.device.footswitch(11))
    assert wait(lambda: "011" in window.ai_panel.body.context.text())
    message = window.ai_panel.body._with_context("más ganancia")
    assert message.startswith("[Contexto: preset seleccionado 11") and "AMP:" in message
    assert message.endswith("más ganancia")
    window.ai_panel.body.context.setChecked(False)
    assert "no incluido" in window.ai_panel.body.context.text()
    assert window.ai_panel.body._with_context("más ganancia") == "más ganancia"


def test_ai_panel_compares_original_and_suggested_sound(window: MainWindow) -> None:
    panel = window.ai_panel.body
    editor = window.editor
    before = list(editor.state.edit.words)  # type: ignore[union-attr]
    panel._before = list(before)
    panel.busy = True
    panel.host.tools.set_module("DS", not editor.module(1).switch)  # what the AI would do
    after = list(editor.state.edit.words)  # type: ignore[union-attr]
    panel._finished("Listo", "")
    assert panel.compare.isVisibleTo(panel) and panel.compare.suggested.isChecked()
    panel.compare.original.click()
    assert wait(lambda: editor.state.edit.words == before)  # type: ignore[union-attr]
    assert wait(lambda: panel.compare.original.isChecked() and panel.compare.isEnabled())
    panel.compare.suggested.click()
    assert wait(lambda: editor.state.edit.words == after)  # type: ignore[union-attr]
    sim = window.sim  # type: ignore[attr-defined]
    sim.inject(sim.device.footswitch(12))
    assert wait(lambda: not panel.compare.isVisibleTo(panel))


def test_original_sound_is_the_saved_preset(window: MainWindow) -> None:
    panel, editor = window.ai_panel.body, window.editor
    saved = list(window.state.presets[window.state.current_slot].words)  # type: ignore[index]

    def ai_turn(module: str) -> None:  # what the panel does around one AI request
        panel.prompt.setPlainText("algo")
        panel._agent = lambda: type("A", (), {"name": "X", "ask": lambda s, m: "ok"})()  # type: ignore[method-assign,assignment]
        panel.ask()
        panel.host.tools.set_module(module, not editor.module(MODULES_INDEX[module]).switch)
        assert wait(lambda: not panel.busy)

    ai_turn("DS")
    ai_turn("MOD")  # a second suggestion on top of the first one
    assert panel.compare.isVisibleTo(panel)
    panel.compare.original.click()
    assert wait(lambda: editor.state.edit.words == saved)  # type: ignore[union-attr]
    assert wait(panel.compare.isEnabled)  # the switch has finished
    panel.compare.suggested.click()
    assert wait(lambda: editor.state.edit.words != saved)  # type: ignore[union-attr]
    assert wait(panel.compare.isEnabled)
    editor.save()  # keeping the suggestion: nothing left to compare
    assert wait(lambda: not panel.compare.isVisibleTo(panel))


MODULES_INDEX = {"DS": 1, "MOD": 6}


def test_assistant_tab_plans_reviews_and_applies(window: MainWindow) -> None:
    page = window.assistant
    state = window.editor.state
    assert page.list.count() == 200
    page.filter.setText("PRESET 02")  # 020..029 visible
    assert wait(lambda: not page.list.item(21).isHidden() and page.list.item(40).isHidden())
    page.tick([22, 23])
    assert page.ticked() == [22, 23] and "22" in page._context()
    page.form.select_module(8)  # REVERB
    page.form.select_state("off")
    assert "Apagar REVERB" in page.form.summary.text() and page.form.add_button.isEnabled()
    before = {s: list(state.presets[s].words) for s in (22, 23)}
    on = {s: state.presets[s].module(8).switch for s in (22, 23)}
    page.form.plan()
    planned = [s for s in (22, 23) if on[s]]  # only presets with the REVERB on change
    assert sorted(page.review.edits) == planned
    assert all(state.presets[s].words == before[s] for s in (22, 23))  # nothing written yet
    if not planned:
        return
    page.review.apply()
    assert wait(lambda: not page.review.edits and page.review.undo_button.isEnabled())
    assert "Backup previo: " in page.status.text()
    from ge150.services.auto_backup import backup_dir
    assert len(list(backup_dir().glob("auto-*-aplicar.mbf"))) == 1
    assert all(not state.presets[s].module(8).switch for s in planned)
    page.review.undo()
    assert wait(lambda: all(state.presets[s].words == before[s] for s in (22, 23)))


def test_ai_proposal_opens_the_assistant_tab(window: MainWindow) -> None:
    tools = window.ai_host.tools
    on = window.editor.state.presets[30].module(1).switch
    tools.propose_preset_edits([{"slot": 30, "modules": [{"module": "DS", "on": not on}]}],
                               "DS al revés")
    assert wait(lambda: window.tabs.currentWidget() is window.assistant)
    assert 30 in window.assistant.review.edits and 30 in window.assistant.ticked()


def test_reopened_conversation_brings_back_ticks_and_pending(window: MainWindow) -> None:
    page, tools = window.assistant, window.ai_host.tools
    chat = page.chat
    chat.chat_id, chat.chat_title = "opeth-1", "Opeth"
    page.tick([18, 19, 20, 21], only=True)
    tools.propose_preset_edits([{"slot": 18, "name": "OPETH CLEAN"}], "Opeth")
    assert wait(lambda: 18 in page.review.edits)
    chat._save()
    chat.new_conversation()  # what changes now is not kept with «opeth-1»
    page.review.clear()
    page.tick([5], only=True)
    chat.open_chat("opeth-1")
    assert page.ticked() == [18, 19, 20, 21]
    assert page.review.edits[18].final_name == "OPETH CLEAN"
    assert page.review.summary_text == "Opeth"


def test_agent_steps_and_working_indicator(window: MainWindow) -> None:
    chat = window.assistant.chat
    chat._set_busy(True, "Claude está trabajando…")
    assert chat.working.isVisibleTo(chat) and chat.working.running
    for tool in ("get_catalog", "get_catalog", "get_catalog", "get_presets"):
        chat._tool_called(tool)
    assert "revisa el catálogo \u00d73" in chat.blocks[-2] and "\u2713" in chat.blocks[-2]
    assert "lee varios presets" in chat.blocks[-1] and "\u2713" not in chat.blocks[-1]
    assert "lee varios presets" in chat.working.label.text()
    chat._finished("| A | B |\n|---|---|\n| 1 | **2** |", "")
    assert not chat.working.running and "\u2713" in chat.blocks[-2]
    assert "<table" in chat.blocks[-1] and "<strong>2</strong>" in chat.blocks[-1]


def test_assistant_preset_rows_tick_on_click(window: MainWindow) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    page = window.assistant
    window.tabs.setCurrentWidget(page)
    page.list.scrollToTop()
    item = page.list.item(4)
    rect = page.list.visualItemRect(item)
    name = QPoint(rect.center().x(), rect.center().y())  # on the name, not the box
    QTest.mouseClick(page.list.viewport(), Qt.MouseButton.LeftButton, pos=name)
    assert item.checkState() == Qt.CheckState.Checked and page.ticked() == [5]
    QTest.mouseClick(page.list.viewport(), Qt.MouseButton.LeftButton, pos=name)
    assert item.checkState() == Qt.CheckState.Unchecked and page.ticked() == []
    assert not page.list.selectedItems()
    box = QPoint(rect.left() + 14, rect.center().y())  # on the check box itself: once
    QTest.mouseClick(page.list.viewport(), Qt.MouseButton.LeftButton, pos=box)
    assert item.checkState() == Qt.CheckState.Checked


def test_assistant_chat_shows_the_ticked_presets(window: MainWindow) -> None:
    page = window.assistant
    assert page.ticks_chip.text() == "Sin presets marcados"
    page.tick([22, 23, 24, 26])
    assert page.ticks_chip.text() == "Presets marcados: 022, 023, 024 +1"
    assert "026" in page.ticks_chip.toolTip() and "26" in page._context()
    page.ticks_chip.setChecked(False)
    assert "no incluidos" in page.ticks_chip.text() and page._context() == ""
    assert "Presets marcados" not in page.chat._with_context("hola")


def test_chat_history_reopens_a_conversation_and_its_preset(window: MainWindow,
                                                             monkeypatch: pytest.MonkeyPatch,
                                                             tmp_path: "os.PathLike[str]") -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from ge150.services.chat_history import ChatStore

    chat = window.ai_panel.body
    chat.store = chat.history.store = ChatStore()
    agent = type("A", (), {"name": "Claude", "session": "sess-1", "reset": lambda s: None,
                           "ask": lambda s, m: "Listo, más ganancia."})()
    chat.agents = {"claude": agent}  # type: ignore[dict-item]
    chat._agent = lambda: agent  # type: ignore[method-assign,assignment]
    window.presets.activated.emit(11)
    assert wait(lambda: window.editor.state.current_slot == 11)
    chat.prompt.setPlainText("más ganancia")
    chat.ask()
    assert wait(lambda: not chat.busy)
    saved = chat.store.entries("panel")
    assert len(saved) == 1 and saved[0].title == "más ganancia" and saved[0].slot == 11
    assert chat.store.entries("asistente") == []  # the tab has its own history

    window.presets.activated.emit(30)  # elsewhere, then reopen it
    assert wait(lambda: window.editor.state.current_slot == 30)
    chat.new_conversation()
    assert "más ganancia" not in chat.chat.toPlainText()
    chat.history_button.setChecked(True)
    assert chat.pages.currentWidget() is chat.history
    chat.open_chat(saved[0].id)
    assert "Listo, más ganancia." in chat.chat.toPlainText()
    assert chat.chat_id == saved[0].id and chat.agents["claude"].session == "sess-1"
    assert chat.pages.currentIndex() == 0
    assert wait(lambda: window.editor.state.current_slot == 11)


def test_chat_history_asks_before_dropping_unsaved_changes(window: MainWindow) -> None:
    from ge150.services.chat_history import ChatEntry

    chat = window.ai_panel.body
    editor = window.editor
    block = editor.module(1)
    block.switch = not block.switch
    editor.write_module(block)
    assert editor.state.dirty
    asked: list[str] = []

    def answer(box: QMessageBox) -> int:
        asked.append(box.text())
        box.setProperty("clicked", "cancel")
        return 0

    monkeypatch_exec = QMessageBox.exec
    try:
        QMessageBox.exec = answer  # type: ignore[method-assign,assignment]
        entry = ChatEntry("x", "panel", "t", "claude", 1, 1, 40, "Otro")
        assert chat._ask_switch(entry) is None  # nothing clicked = cancel
    finally:
        QMessageBox.exec = monkeypatch_exec  # type: ignore[method-assign]
    assert "cambios sin guardar" in asked[0] and "040" in asked[0]


def test_a_proposal_is_heard_on_its_own_preset(window: MainWindow) -> None:
    """Pedal on «Solo» (22), the AI changes «Clean» (28): the pedal must play the Clean."""
    editor, review = window.editor, window.assistant.review
    window.presets.activated.emit(22)
    assert wait(lambda: editor.state.current_slot == 22 and not window.runner.busy)
    on = editor.state.presets[28].module(8).switch
    window.ai_host.tools.propose_preset_edits(
        [{"slot": 28, "modules": [{"module": "REVERB", "on": not on}]}], "Clean")
    assert wait(lambda: editor.state.current_slot == 28 and review.playing == 28)
    assert editor.module(8).switch == (not on) and review.bar.isVisibleTo(review)
    assert "028" in review.playing_label.text()
    review.sides.button(0).click()  # «Antes»
    assert wait(lambda: editor.module(8).switch == on and not review.playing_after)
    review.sides.button(1).click()  # «Después»
    assert wait(lambda: editor.module(8).switch == (not on))
    review.clear()  # «Descartar»: back to the saved Clean
    assert wait(lambda: not editor.state.dirty and editor.module(8).switch == on)
    assert not review.bar.isVisibleTo(review)


def test_hearing_a_proposal_asks_before_dropping_own_changes(window: MainWindow,
                                                             monkeypatch: pytest.MonkeyPatch
                                                             ) -> None:
    editor, review = window.editor, window.assistant.review
    window.presets.activated.emit(22)
    assert wait(lambda: editor.state.current_slot == 22 and not window.runner.busy)
    block = editor.module(1)
    block.switch = not block.switch
    editor.write_module(block)  # the user's own unsaved change on 22
    assert editor.state.dirty
    on = editor.state.presets[28].module(8).switch
    window.ai_host.tools.propose_preset_edits(
        [{"slot": 28, "modules": [{"module": "REVERB", "on": not on}]}])
    assert wait(lambda: 28 in review.edits)
    wait(lambda: False, 200)
    assert editor.state.current_slot == 22 and review.playing is None  # not auto-played
    asked: list[str] = []

    def no(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    assert not review.listen(28)
    assert "cambios sin guardar" in asked[0] and editor.state.current_slot == 22


def test_chain_tap_led_follows_the_delay_time(window: MainWindow) -> None:
    from ge150.model.gear import MODULE_TAGS

    delay = MODULE_TAGS.index("DELAY")
    editor, chain = window.editor, window.chain
    tap = chain.tiles[delay].tap
    assert tap is not None
    if not editor.module(delay).switch:
        chain.tiles[delay].power.click()
        assert wait(lambda: editor.module(delay).switch)
    window.ai_host.tools.set_module("DELAY", True, "DIGITAL", {"TIME": "500"})
    assert wait(lambda: tap.period == 500)
    assert "120 BPM" in tap.toolTip()
    chain.tiles[delay].power.click()  # delay off: no blinking
    assert wait(lambda: tap.period == 0)


def test_chain_hover_lights_the_cables_of_that_pedal(window: MainWindow) -> None:
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent

    chain = window.chain
    tile = chain.tiles[2]
    QApplication.sendEvent(tile, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert wait(lambda: chain.wires[2].lit > 0.9 and chain.wires[3].lit > 0.9)
    assert wait(lambda: tile.view.zoom > 1.0) and chain.wires[1].lit == 0.0
    QApplication.sendEvent(tile, QEvent(QEvent.Type.Leave))
    assert wait(lambda: chain.wires[2].lit < 0.01 and tile.view.zoom == 1.0)


def test_about_dialog_shows_the_project_and_the_system(window: MainWindow) -> None:
    from ge150 import __version__
    from ge150.ui.about_dialog import REPO, AboutDialog, folders

    dialog = AboutDialog(window.state, "simulator", window)
    info = dict(dialog.system_info())
    assert info["Editor"] == __version__ and "V1.3.8" in info["Pedalera"]
    assert info["Conexión"] == "simulator"
    text = dialog.report_text()
    assert __version__ in text and "Qt / PySide6" in text
    assert {name for name, _, _ in folders()} >= {"Backups automáticos", "Conversaciones con la IA"}
    assert "github.com/wh01s17/ge150-linux" in REPO
    dialog.deleteLater()


def test_drop_target_follows_the_indicator() -> None:
    from ge150.ui.preset_list import Indicator, drop_target
    assert drop_target(28, 10, Indicator.AboveItem) == 10
    assert drop_target(28, 10, Indicator.BelowItem) == 11
    assert drop_target(5, 10, Indicator.AboveItem) == 9
    assert drop_target(5, 10, Indicator.BelowItem) == 10
    assert drop_target(5, 10, Indicator.OnItem) == 10


def test_dragging_a_preset_moves_it_on_the_pedal(window: MainWindow) -> None:
    presets = window.state.presets
    before = {s: presets[s].name for s in range(10, 14)}
    window.presets.list.moved.emit(13, 10)
    # While it is written: a banner says so and no other preset can be clicked.
    assert window.presets.working and not window.presets.list.isEnabled()
    assert wait(lambda: presets[10].name == before[13] and not window.runner.busy)
    assert not window.presets.working and window.presets.list.isEnabled()
    assert [presets[s].name for s in range(10, 14)] == [before[s] for s in (13, 10, 11, 12)]
    assert window.presets.list.item(9).text().endswith(before[13])
    from ge150.services.auto_backup import backup_dir
    assert len(list(backup_dir().glob("auto-*-mover.mbf"))) == 1


def test_exp_tile_shows_its_page_to_assign_the_pedal(window: MainWindow) -> None:
    editor = window.editor
    window.chain.exp.clicked.emit()
    dialog = window.exp_page
    assert window.pages.currentWidget() is dialog
    assert window.chain.exp.property("selected")
    dialog._choose_module(6)  # MOD
    settings_mod_type = editor.module(6).type
    buttons = dialog.params.buttons()
    assert buttons and all(b.text() for b in buttons)
    dialog._choose_mode("volume")  # the user's case: volume first, then a parameter
    assert editor.exp_settings().volume_on and dialog.mode_buttons["volume"].isChecked()
    dialog._choose_mode("effect")  # nothing written until a parameter is picked
    assert editor.exp_settings().volume_on
    assert dialog.mode_buttons["effect"].property("browsing")
    first = dialog.params.buttons()[0]
    first.click()
    settings = editor.exp_settings()
    assert (settings.module, settings.function_on, settings.volume_on) == (6, True, False)
    assert dialog.mode_buttons["effect"].isChecked()
    assert not dialog.mode_buttons["volume"].isChecked()
    assert "volumen" not in dialog.now.text()
    assert settings.param_name == first.text().upper()
    device = window.sim.device  # type: ignore[attr-defined]
    assert wait(lambda: device._edit.words[66:69] == [6, settings.para, 1])
    window.chain.refresh()
    assert window.chain.exp.label.text().startswith(f"MOD · {first.text()}")
    tagged = [t.index for t in window.chain.tiles if not t.exp_tag.isHidden()]
    assert tagged == [6]  # the tile the pedal moves wears an «EXP» tag
    window.chain.exp.hovered.emit(True)  # hovering EXP lights the effect it moves
    assert window.chain.tiles[6].property("hover")
    window.chain.exp.hovered.emit(False)
    assert not window.chain.tiles[6].property("hover")
    yellow = next(i for i, m in enumerate(editor.catalog[0].models) if m.name == "YELLOW COMP")
    editor.set_type(0, yellow)
    dialog.refresh()
    assert "reemplaza" in dialog.wah_button.text() and "YELLOW COMP" in dialog.wah_button.text()
    asked: list[str] = []

    def answer(button: QMessageBox.StandardButton) -> Callable[..., QMessageBox.StandardButton]:
        return lambda _parent, _title, text, *a, **k: asked.append(text) or button

    QMessageBox.question = answer(QMessageBox.StandardButton.No)  # type: ignore[method-assign]
    dialog._wah()  # said no: the compressor stays
    assert len(asked) == 1 and editor.module(0).type == yellow
    QMessageBox.question = answer(QMessageBox.StandardButton.Yes)  # type: ignore[method-assign]
    dialog._wah()
    fx = editor.module(0)
    assert fx.switch and editor.catalog[0].models[fx.type].name == "CRY WAH"
    assert editor.exp_settings().param_name == "POSITION"
    assert editor.module(6).type == settings_mod_type  # the rest of the chain is untouched
    assert dialog.wah_button.text() == "Wah: el POSITION del CRY WAH"
    assert dialog.wah_button.isChecked() and dialog.modules.button(0).isChecked()
    dialog._wah()  # already a wah: nothing to replace, nothing asked
    assert len(asked) == 2
    dialog._choose_mode("volume")
    settings = editor.exp_settings()
    assert (settings.function_on, settings.volume_on) == (False, True)
    # Volume: only its range shows, and no effect looks assigned.
    assert dialog.mode_buttons["volume"].isChecked() and dialog.effect_box.isHidden()
    assert not dialog.volume_box.isHidden()
    assert not any(b.isChecked() for b in dialog.modules.buttons())
    dialog._choose_mode("none")
    settings = editor.exp_settings()
    assert (settings.function_on, settings.volume_on) == (False, False)
    assert dialog.mode_buttons["none"].isChecked()
    dialog._choose_mode("volume")
    window.chain.refresh()
    assert window.chain.exp.label.text() == "VOLUMEN"
    # An effect that is off cannot be chosen until it is switched on in the chain.
    dialog._choose_module(6)
    window.chain._on_toggle(2, False)  # AMP off with its LED
    assert wait(lambda: not dialog.modules.button(2).isEnabled())
    assert "apagado" in dialog.modules.button(2).toolTip()
    window.chain._on_toggle(2, True)
    assert wait(lambda: dialog.modules.button(2).isEnabled())
    # The effect being looked at is switched off: the selection moves to one that is on.
    dialog._choose_module(2)
    window.chain._on_toggle(2, False)
    assert wait(lambda: dialog.module != 2)
    assert not dialog.modules.button(2).isChecked()
    assert dialog.module is not None and editor.module(dialog.module).switch
    window.chain._on_toggle(2, True)
    # The pedal moves a parameter of an effect that is off: a warning, never a selection.
    dialog._choose_module(6)
    dialog.params.buttons()[0].click()
    window.chain._on_toggle(6, False)
    assert wait(lambda: not dialog.off_row.isHidden())
    assert dialog.module != 6 and not dialog.modules.button(6).isChecked()
    dialog._turn_on()
    assert editor.module(6).switch and dialog.module == 6 and dialog.off_row.isHidden()
    # Another preset starts on its own assignment, not on what was looked at before.
    dialog._choose_module(7)
    window.load_preset(30)
    assert wait(lambda: window.state.current_slot == 30 and not window.runner.busy)
    assert wait(lambda: dialog.module != 7 or editor.exp_settings().module == 7)
    on = [i for i in range(9) if editor.module(i).switch]
    assigned = editor.exp_settings().module
    assert dialog.module == (assigned if assigned in on else (on[0] if on else None))
    window.chain.select(2)  # back to a module page
    assert window.pages.currentWidget() is window.module_editor
    assert not window.chain.exp.property("selected")


def test_a_parameter_the_pedal_lets_go_of_goes_back_to_its_value(window: MainWindow) -> None:
    # The user's case: GAIN of the DS moved by the pedal up to 87, then the pedal set back to
    # volume: GAIN must not stay where the pedal left it.
    editor, page = window.editor, window.exp_page
    window.open_exp()
    editor.set_switch(1, True)
    page._choose_module(1)
    gain = next(p for p in page.params.buttons() if p.text() == "GAIN")
    before = editor.find_param(1, "GAIN")
    original = editor.module(1).words[before.word]
    messages: list[str] = []
    page.message.connect(messages.append)
    gain.click()
    assert editor.exp_settings().param_name == "GAIN"
    editor.set_param(1, "GAIN", 87)  # what the pedal does while it moves GAIN
    page._choose_mode("volume")
    assert editor.module(1).words[before.word] == original
    assert messages and "GAIN del DS/OD vuelve a" in messages[-1]
    # Moving another parameter also gives this one back, once.
    page._choose_module(1)
    next(p for p in page.params.buttons() if p.text() == "GAIN").click()
    editor.set_param(1, "GAIN", 87)
    next(p for p in page.params.buttons() if p.text() == "TONE").click()
    assert editor.module(1).words[before.word] == original
    assert editor.exp_settings().param_name == "TONE"


def test_exp_page_says_when_the_change_needs_saving(window: MainWindow) -> None:
    editor, page = window.editor, window.exp_page
    window.open_exp()
    assert page.unsaved.isHidden()  # as stored
    page._choose_mode("none" if editor.exp_settings().volume_on else "volume")
    assert not page.unsaved.isHidden()  # the pedal only uses it once saved
    page.save_requested.emit()
    assert wait(lambda: page.unsaved.isHidden() and not window.runner.busy)
    stored = window.state.presets[window.state.current_slot or 0]
    assert stored.words[66:72] == editor.state.edit.words[66:72]  # type: ignore[union-attr]


def test_exp_page_shows_the_pedal_position_live(window: MainWindow) -> None:
    editor, page = window.editor, window.exp_page
    window.open_exp()
    editor.set_switch(1, True)
    page._choose_mode("effect")
    assert page.volume_box.isHidden() and not page.effect_box.isHidden()  # only its options
    page._choose_module(1)
    next(p for p in page.params.buttons() if p.text() == "GAIN").click()
    editor.set_param(1, "GAIN", 87)  # what the pedal reports while it moves GAIN
    assert wait(lambda: page.side.position is not None and abs(page.side.position - 0.87) < 0.01)
    assert "GAIN" in page.side.label
    page._choose_mode("volume")  # the pedal reports no position in volume mode
    assert page.side.position is None
    assert page.effect_box.isHidden() and not page.volume_box.isHidden()


def test_loading_dots_move_until_the_pedal_is_read(window: MainWindow) -> None:
    assert not window._loading.isActive()  # the fixture's sync is over
    window.start_sync()
    assert window._loading.isActive()
    seen = []
    for _ in range(4):
        seen.append(window.name.text())
        window._loading_tick()
    assert seen[:3] == ["Conectando.", "Conectando..", "Conectando..."]
    assert seen[3] == "Conectando."  # and again
    assert wait(lambda: not window._loading.isActive() and window.central.isEnabled())
    assert window.name.text() != "Conectando."


def test_a_stored_assignment_lets_go_back_to_the_saved_value(window: MainWindow) -> None:
    # The user's case: the preset came with the pedal moving the chorus MIX; the pedal left
    # MIX at 100 and switching to volume kept it there. It goes back to the saved value.
    from ge150.model import exp
    editor, page = window.editor, window.exp_page
    chorus = next(i for i, m in enumerate(editor.catalog[6].models) if m.name == "ANA-CHORUS")
    editor.set_type(6, chorus)
    editor.set_switch(6, True)
    editor.set_param(6, "MIX", 35)
    settings = editor.exp_settings()
    settings.module, settings.para = 6, exp.para_index(6, "MIX") or 0
    settings.function_on, settings.volume_on = True, False
    editor.set_exp_settings(settings)
    editor.save()  # stored like that, assigned before the page ever saw it
    editor.set_param(6, "MIX", 100)  # where the pedal left it
    window.open_exp()
    messages: list[str] = []
    page.message.connect(messages.append)
    page._choose_mode("volume")
    mix = editor.find_param(6, "MIX")
    assert mix.from_raw(editor.module(6).words[mix.word]) == 35
    assert messages and "guardado en el preset" in messages[-1]


def test_first_start_offers_shortcuts_once(window: MainWindow,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from ge150.services import shortcuts
    monkeypatch.setattr(shortcuts, "_run", lambda *args: None)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    asked: list[str] = []

    def no(box: QMessageBox) -> int:  # «Ahora no»
        asked.append(box.text())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", no)
    window.offer_shortcuts()
    assert asked and "lanzador de aplicaciones de Hyprland" in asked[0]
    assert not shortcuts.installed()
    window.offer_shortcuts()  # asked once only
    assert len(asked) == 1
    window._refresh_shortcuts_action()  # still in Archivo
    assert window.shortcuts_action.text() == "Crear accesos directos…"
    window.toggle_shortcuts()
    assert shortcuts.installed()
    assert window.shortcuts_action.text() == "Quitar accesos directos"
    window.toggle_shortcuts()  # QMessageBox.question is patched to Yes
    assert not shortcuts.installed()

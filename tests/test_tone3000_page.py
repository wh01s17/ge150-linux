"""TONE3000 tab without a browser: the intercepted redirect, the card and "add to the pedal"."""

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from ge150.formats import wav_ir
from ge150.protocol.session import DeviceSession
from ge150.services.editor import Editor
from ge150.services.tone3000 import Download, IrSources, Pending, Tone, ToneModel
from ge150.sim.sim_transport import SimTransport
from ge150.ui.tone3000_page import Tone3000Page
from ge150.ui.worker import TaskRunner

TONE = Tone(42, "Mesa 4x12 V30", "irmaker", "", "", "cc-by", "ir", "cab", "Big cab", "")
MODELS = [ToneModel(7, "SM57", "u"), ToneModel(8, "R121", "u")]


class FakeClient:
    def __init__(self, wav: Path) -> None:
        from ge150.services.tone3000 import Credentials

        self.creds = Credentials("t3k_pub_x", "tok", "ref", 1e12)
        self.redirect_uri = "http://127.0.0.1:53682/callback"
        self.wav = wav
        self.finished: list[str] = []
        self.downloaded: list[tuple[int, int | None]] = []

    def begin(self, prompt: str = "", tone_id: str = "") -> Pending:
        return Pending("about:blank", "verifier", "st")

    def is_callback(self, url: str) -> bool:
        return url.startswith(self.redirect_uri)

    def finish(self, pending: Pending, callback: object) -> object:
        self.finished.append(pending.state)
        return callback

    def tone(self, tone_id: int) -> Tone:
        return TONE

    def models(self, tone_id: int) -> list[ToneModel]:
        return MODELS

    def user(self) -> dict[str, str]:
        return {"username": "wh01s17"}

    def download(self, tone_id: int, model_id: int | None = None) -> Download:
        self.downloaded.append((tone_id, model_id))
        model = next(m for m in MODELS if m.id == model_id)
        return Download(TONE, model, self.wav)


def wait(predicate: Callable[[], bool], timeout_ms: int = 4000) -> bool:
    for _ in range(timeout_ms // 10):
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tone3000Page]:
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(str(a[2])))
    wav = tmp_path / "ir.wav"
    wav.write_bytes(wav_ir.ir_to_wav([4_000_000] + [0] * 511))
    session = DeviceSession(SimTransport(), frame_delay=0, multipart_delay=0).start()
    editor = Editor(session)
    editor.sync()
    runner = TaskRunner()
    widget = Tone3000Page(editor, runner)
    client = FakeClient(wav)
    widget.client = widget.loader.client = client  # type: ignore[assignment]
    widget.loader.sources = IrSources()
    widget.pending = client.begin()
    restarts: list[bool] = []
    widget.start_catalogue = lambda: restarts.append(True)  # type: ignore[method-assign]
    widget.restarts = restarts  # type: ignore[attr-defined]
    widget.warnings = warnings  # type: ignore[attr-defined]
    yield widget
    runner.wait(5000)
    wait(lambda: False, 300)  # let background threads deliver their signals
    widget.deleteLater()
    QCoreApplication.processEvents()
    session.close()


def test_chosen_tone_is_shown_and_added_to_a_slot(page: Tone3000Page) -> None:
    page._refresh_slots()
    page._callback("http://127.0.0.1:53682/callback?code=c&state=st&tone_id=42")
    assert wait(lambda: page.tone is not None)
    assert page.client.finished == ["st"]  # type: ignore[attr-defined]
    assert page.detail.title.text() == "Mesa 4x12 V30" and "irmaker" in page.detail.author.text()
    assert page.file_combo.count() == 2 and page.add_button.isEnabled()
    page.file_combo.setCurrentIndex(1)
    page.slot_combo.setCurrentIndex(3)
    page._add()
    assert wait(lambda: "IR 4" in page.note.text())
    assert page.client.downloaded == [(42, 8)]  # type: ignore[attr-defined]
    assert page.editor.state.user_names is not None
    assert page.editor.state.user_names.ir[3] == "Mesa 4x12 V30"
    assert IrSources().load()["4"]["model"] == "R121"
    assert "Mesa 4x12 V30" in page.slot_combo.itemText(3)


def test_cancelled_flow_just_restarts(page: Tone3000Page) -> None:
    page._callback("http://127.0.0.1:53682/callback?error=access_denied&state=st")
    wait(lambda: False, 200)
    assert page.tone is None and not page.warnings  # type: ignore[attr-defined]
    assert page.restarts  # type: ignore[attr-defined]


def test_user_cab_shows_the_tone3000_image(page: Tone3000Page, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from ge150.formats import wav_ir as w
    from ge150.model.gear import gear_for
    from ge150.protocol.opcodes import Op
    from ge150.services.tone3000 import _image_path
    from ge150.ui.gear_view import T3K, load_photo, user_cab_gear

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    url = "https://img/cab.png"
    image = _image_path(url)
    image.parent.mkdir(parents=True)
    from PySide6.QtGui import QColor, QPixmap

    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor("red"))
    pixmap.save(str(image), "PNG")
    tone = Tone(42, "Mesa 4x12 V30", "irmaker", "", "", "cc-by", "ir", "cab", "", url)
    editor = page.editor
    editor.upload_user_slot(Op.USER_IR, 3, "Mesa 4x12 V30",
                            w.load_ir_file(page.client.wav))  # type: ignore[attr-defined]
    IrSources().record(4, Download(tone, MODELS[0], page.client.wav))  # type: ignore[attr-defined]
    gear = user_cab_gear(editor, 30, gear_for(3, 30))
    assert gear.gid.startswith(T3K) and load_photo(gear.gid) is not None
    assert user_cab_gear(editor, 31, gear_for(3, 31)).gid == gear_for(3, 31).gid


def test_connect_account_without_choosing_a_tone(page: Tone3000Page) -> None:
    from ge150.services.tone3000 import Credentials

    client = page.client
    client.creds = Credentials("t3k_pub_x")  # key set, not signed in
    page._refresh_account()
    assert not page.connect_button.isHidden() and "Conectar cuenta" in page.account.text()
    page.pending = client.begin()  # what connect_account starts (no browser here)

    def finish(pending: Pending, callback: object) -> object:
        client.creds = Credentials("t3k_pub_x", "tok", "ref", 1e12)
        return callback

    client.finish = finish  # type: ignore[method-assign]
    page._callback("http://127.0.0.1:53682/callback?code=c&state=st")  # no tone_id
    assert wait(lambda: "Cuenta conectada" in page.note.text())
    assert page.tone is None and page.connect_button.isHidden()
    assert wait(lambda: "wh01s17" in page.account.text())


def test_catalog_applies_to_tone3000_and_load_confirmation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ge150 import i18n

    monkeypatch.setattr(i18n, "_catalog", {
        "Catálogo": "Translated catalog",
        "por {author}": "Creator: {author}",
        "Confirmar": "Translated confirmation",
        "¿Cargar «{title} / {model}» en la ranura IR {slot} (CAB {cab})?\n"
        "Reemplaza «{old}».\n\nAutor: {author}\nLicencia: {license}\n{url}":
            "Load {title} / {model} into slot {slot} (CAB {cab}), replacing {old}? "
            "{author}, {license}, {url}",
    })
    page: Tone3000Page = request.getfixturevalue("page")
    assert page.home_button.text() == "Translated catalog"
    page._show_tone(TONE, MODELS)
    assert page.detail.author.text() == "Creator: <b>irmaker</b>"

    prompts = []

    def decline(parent: object, title: str, question: str) -> QMessageBox.StandardButton:
        prompts.append((title, question))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    page.loader._store(page.client.download(42, 8), 3)

    assert len(prompts) == 1
    title, question = prompts[0]
    assert title == "Translated confirmation"
    assert "Mesa 4x12 V30 / R121 into slot 4 (CAB 30)" in question
    assert "irmaker, cc-by" in question
    assert IrSources().load() == {}

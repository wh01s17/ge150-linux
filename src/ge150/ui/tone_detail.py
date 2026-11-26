"""Detail card of a user IR slot that came from TONE3000: image, title, author with avatar,
licence, file and description (TONE3000 asks apps to show the creator)."""

from __future__ import annotations

import html
import threading
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.services.tone3000 import fetch_image

IMAGE = QSize(420, 260)
AVATAR = 32


class _Images(QObject):
    loaded = Signal(str, str)  # url, local file ("" if it failed)


def _round(pixmap: QPixmap, size: int) -> QPixmap:
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return out


class ToneDetail(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toneDetail")
        self.source: dict[str, Any] | None = None
        self._images = _Images(self)
        self._images.loaded.connect(self._image_loaded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = QLabel()
        self.image.setObjectName("toneImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedHeight(IMAGE.height())
        layout.addWidget(self.image)
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        author = QHBoxLayout()
        self.avatar = QLabel()
        self.avatar.setFixedSize(AVATAR, AVATAR)
        self.author = QLabel()
        self.author.setOpenExternalLinks(True)
        self.licence = QLabel()
        self.licence.setObjectName("licenceChip")
        author.addWidget(self.avatar)
        author.addWidget(self.author, 1)
        author.addWidget(self.licence)
        layout.addLayout(author)
        self.file = QLabel()
        self.file.setObjectName("basedOn")
        layout.addWidget(self.file)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setObjectName("toneDescription")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.description)
        layout.addWidget(scroll, 1)
        self.open_button = QPushButton(tr("Ver en TONE3000"))
        self.open_button.clicked.connect(self._open)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignRight)
        self.show_slot(None, "")

    def show_slot(self, source: dict[str, Any] | None, name: str) -> None:
        self.source = source
        self.open_button.setVisible(source is not None)
        self.avatar.setVisible(source is not None)
        self.licence.setVisible(source is not None)
        self.avatar.clear()
        if source is None:
            self.image.setPixmap(QPixmap())
            self.image.setText(tr("Sin IR de TONE3000") if name == "EMPTY" else
                               tr("IR cargada desde un archivo WAV"))
            self.title.setText(name if name != "EMPTY" else tr("Ranura vacía"))
            self.author.setText("")
            self.file.setText("")
            self.description.setText(tr("Elige una IR en TONE3000 para esta ranura."))
            return
        self.image.setPixmap(QPixmap())
        self.image.setText(tr("Cargando imagen…") if source.get("image_url") else tr("Sin imagen"))
        self.title.setText(str(source.get("title", "")))
        author = html.escape(str(source.get("author", "")))
        link = source.get("author_url")
        attribution = (f"<a style='color:#ff8a1f' href='{html.escape(str(link))}'>{author}</a>"
                       if link else f"<b>{author}</b>")
        self.author.setText(tr("por {author}", author=attribution))
        self.licence.setText(str(source.get("license", "")))
        model = source.get("model")
        self.file.setText(tr("Archivo: {name}", name=model) if model else "")
        description = str(source.get("description") or "").strip()
        self.description.setText(description or tr("Sin descripción."))
        for url in (source.get("image_url"), source.get("avatar_url")):
            if url:
                self._load(str(url))

    def _load(self, url: str) -> None:
        def work() -> None:
            try:
                path = str(fetch_image(url))
            except Exception:  # an image is optional
                path = ""
            self._images.loaded.emit(url, path)

        threading.Thread(target=work, name="ge150-image", daemon=True).start()

    def _image_loaded(self, url: str, path: str) -> None:
        if self.source is None:
            return
        pixmap = QPixmap(path) if path else QPixmap()
        if url == self.source.get("image_url"):
            if pixmap.isNull():
                self.image.setText(tr("Sin imagen"))
            else:
                self.image.setPixmap(pixmap.scaled(
                    IMAGE, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        elif url == self.source.get("avatar_url") and not pixmap.isNull():
            self.avatar.setPixmap(_round(pixmap, AVATAR))

    def _open(self) -> None:
        if self.source and self.source.get("url"):
            QDesktopServices.openUrl(QUrl(str(self.source["url"])))

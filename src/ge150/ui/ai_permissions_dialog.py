"""«Permisos del asistente»: one switch per kind of operation the AI may do with the pedal.

Each switch is saved at once (``services/ai_permissions.py``) and applies to the next tool
call, in both chats and in ``ge150-mcp``.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.services import ai_permissions
from ge150.ui.theme import ACCENT, BORDER, MUTED, RAISED


class ToggleSwitch(QAbstractButton):
    """A checkable on/off switch (space or click toggles it)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(38, 22)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._anim.stop()
        self._set_knob(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 1, self.width() - 2, self.height() - 2)
        radius = track.height() / 2
        on = QColor(ACCENT)
        off = QColor(RAISED)
        mix = QColor.fromRgbF(*(a + (b - a) * self._pos for a, b in
                                zip(off.getRgbF()[:3], on.getRgbF()[:3], strict=True)))
        painter.setPen(QColor(ACCENT if self.hasFocus() else BORDER))
        painter.setBrush(mix if self.isEnabled() else QColor(RAISED))
        painter.drawRoundedRect(track, radius, radius)
        size = track.height() - 6
        x = track.left() + 3 + (track.width() - size - 6) * self._pos
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1a1208") if self._pos > 0.5 else QColor(MUTED))
        painter.drawEllipse(QRectF(x, track.top() + 3, size, size))


class AiPermissionsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Permisos del asistente"))
        self.setMinimumWidth(500)
        self.switches: dict[str, ToggleSwitch] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(6)
        title = QLabel(tr("Permisos del asistente"))
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        intro = QLabel(tr("Qué puede hacer la IA con tu pedalera. Leer presets, el catálogo y "
                       "tu guitarra siempre está permitido. Los cambios valen desde el "
                       "próximo pedido."))
        intro.setObjectName("note")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        permissions = ai_permissions.load()
        group = ""
        for permission in ai_permissions.PERMISSIONS:
            if permission.group != group:
                group = permission.group
                header = QLabel(tr(group).upper())
                header.setObjectName("permGroup")
                layout.addSpacing(8)
                layout.addWidget(header)
            layout.addWidget(self._row(permission, permissions.allows(permission.key)))
        layout.addSpacing(10)
        buttons = QHBoxLayout()
        allow_all = QPushButton(tr("Permitir todo"))
        allow_all.setObjectName("linkButton")
        allow_all.setCursor(Qt.CursorShape.PointingHandCursor)
        allow_all.clicked.connect(self.allow_all)
        close = QPushButton(tr("Cerrar"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(allow_all)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _row(self, permission: ai_permissions.Permission, allowed: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("permRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(12, 8, 12, 8)
        text = QVBoxLayout()
        text.setSpacing(1)
        label = QLabel(tr(permission.label))
        label.setObjectName("permLabel")
        detail = QLabel(tr(permission.detail))
        detail.setObjectName("note")
        detail.setWordWrap(True)
        text.addWidget(label)
        text.addWidget(detail)
        line.addLayout(text, 1)
        switch = ToggleSwitch()
        switch.setChecked(allowed)
        switch.setAccessibleName(tr(permission.label))
        switch.setToolTip(tr("Permitido") if allowed else tr("Desactivado"))
        switch.toggled.connect(lambda on, s=switch: self._toggled(s, on))
        line.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switches[permission.key] = switch
        return row

    def permissions(self) -> ai_permissions.Permissions:
        return ai_permissions.Permissions(
            frozenset(k for k, s in self.switches.items() if not s.isChecked()))

    def _toggled(self, switch: ToggleSwitch, on: bool) -> None:
        switch.setToolTip(tr("Permitido") if on else tr("Desactivado"))
        self._save()

    def _save(self) -> None:
        ai_permissions.save(self.permissions())

    def allow_all(self) -> None:
        for switch in self.switches.values():
            switch.setChecked(True)
        self._save()

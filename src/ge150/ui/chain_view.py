"""«Cadena de efectos», always visible at the bottom: from the guitar (IN) to the amp (OUT),
one tile per module with a picture of its gear, its model and an LED to switch it on or off.
Clicking a tile opens that module. Apart, after OUT, the expression pedal and what it moves.

Animations only answer the user: hovering a tile makes its pedal hop and lights the cables
in and out of it (the signal going through), switching a module fades its picture and its
LED. The DLY tile has a TAP LED that blinks at the delay time, like the pedal's own.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QRadialGradient,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model.gear import MODULE_TAGS
from ge150.model.preset import MODULES
from ge150.services.editor import Editor
from ge150.ui.exp_view import ExpTile
from ge150.ui.gear_view import GearView, module_look
from ge150.ui.icons import icon
from ge150.ui.theme import ACCENT, MUTED

SHORT = ("FX", "DS", "AMP", "CAB", "NS", "EQ", "MOD", "DLY", "REV")
DELAY = MODULE_TAGS.index("DELAY")
LED_ON, LED_OFF = QColor("#3ddc84"), QColor("#2b3038")
TAP_ON = QColor("#ff4d3d")
WIRE, WIRE_LIT = QColor("#6b5236"), QColor(ACCENT)
OFF_OPACITY = 0.35
HOVER_ZOOM = 1.08
JACK_ICON = 28


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _glow(p: QPainter, centre: QPointF, radius: float, colour: QColor, strength: float) -> None:
    halo = QRadialGradient(centre, radius)
    inner = QColor(colour)
    inner.setAlpha(round(160 * strength))
    halo.setColorAt(0, inner)
    inner.setAlpha(0)
    halo.setColorAt(1, inner)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(centre, radius, radius)


class LedButton(QPushButton):
    """A pedal LED as the on/off switch; it lights up and dims smoothly."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("led")
        self.setCheckable(True)
        self.setFixedSize(26, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Encender / apagar el efecto"))
        self._glow = 0.0
        self._anim = QPropertyAnimation(self, b"glow", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def show_state(self, on: bool) -> None:
        """Reflect the pedal's state without emitting ``toggled``."""
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._animate(on)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_glow(self) -> float:
        return self._glow

    def _set_glow(self, value: float) -> None:
        self._glow = value
        self.update()

    glow = Property(float, _get_glow, _set_glow)

    def sizeHint(self) -> QSize:
        return QSize(26, 26)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self._glow > 0.01:
            _glow(p, c, 13, LED_ON, self._glow * 0.9)
        ring = QColor(ACCENT) if self.underMouse() else QColor("#11151a")
        p.setPen(QPen(ring, 1.5))
        p.setBrush(_mix(LED_OFF, LED_ON, self._glow))
        p.drawEllipse(c, 6.5, 6.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, round(40 + 110 * self._glow)))
        p.drawEllipse(QPointF(c.x() - 2, c.y() - 2), 1.8, 1.8)
        p.end()


class TapLed(QWidget):
    """The TAP LED of the DLY tile: a flash every ``period`` ms (the delay time)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(34, 16))
        self.period = 0  # ms; 0 = delay off
        self._flash = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._beat)
        self._fade = QPropertyAnimation(self, b"flash", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.setToolTip(tr("TAP: el delay está apagado"))

    def set_period(self, ms: int) -> None:
        if ms == self.period:
            return
        self.period = ms
        if ms:
            self.setToolTip(tr(
                "TAP: parpadea al tiempo del delay ({ms} ms ≈ {bpm:.0f} BPM)",
                ms=ms,
                bpm=60000 / ms,
            ))
        else:
            self.setToolTip(tr("TAP: el delay está apagado"))
        self._restart()
        self.update()

    def _restart(self) -> None:
        self._timer.stop()
        if self.period and self.isVisible():
            self._timer.start(self.period)
            self._beat()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._restart()

    def hideEvent(self, event: QHideEvent) -> None:  # nobody sees it: no blinking
        self._timer.stop()
        super().hideEvent(event)

    def _beat(self) -> None:
        self._fade.stop()
        self._fade.start()

    def _get_flash(self) -> float:
        return self._flash

    def _set_flash(self, value: float) -> None:
        self._flash = value
        self.update()

    flash = Property(float, _get_flash, _set_flash)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(5, self.height() / 2)
        lit = self._flash if self.period else 0.0
        if lit > 0.01:
            _glow(p, c, 8, TAP_ON, lit)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_mix(QColor("#3a2a28"), TAP_ON, lit))
        p.drawEllipse(c, 3.2, 3.2)
        p.setPen(QColor(MUTED))
        font = p.font()
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        p.drawText(12, 0, self.width() - 12, self.height(),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "TAP")
        p.end()


class ModuleTile(QFrame):
    clicked = Signal(int)
    toggled = Signal(int, bool)
    hovered = Signal(int, bool)

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.setObjectName("moduleTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("{name}: clic para editar", name=MODULES[index].name))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.name = QLabel(SHORT[index])
        self.name.setObjectName("tileName")
        self.power = LedButton()
        self.power.toggled.connect(lambda on: self.toggled.emit(self.index, on))
        top.addWidget(self.name)
        self.exp_tag = QLabel("EXP")  # the expression pedal moves one of its parameters
        self.exp_tag.setObjectName("expTag")
        tag_font = QFont(self.exp_tag.font())
        tag_font.setPixelSize(9)
        tag_font.setBold(True)
        self.exp_tag.setFont(tag_font)
        self.exp_tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        metrics = QFontMetrics(tag_font)  # sized here: the style sheet's padding is not counted
        self.exp_tag.setFixedSize(metrics.horizontalAdvance("EXP") + 10, metrics.height() + 4)
        self.exp_tag.hide()
        top.addWidget(self.exp_tag)
        self.tap: TapLed | None = None
        if index == DELAY:
            self.tap = TapLed()
            top.addWidget(self.tap)
        top.addStretch(1)
        top.addWidget(self.power)
        self.view = GearView(compact=True)
        self.view.setMinimumHeight(56)
        self._fade = QGraphicsOpacityEffect(self.view)
        self._fade.setOpacity(1.0)
        self.view.setGraphicsEffect(self._fade)
        self._fade_anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._fade_anim.setDuration(240)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hop = QPropertyAnimation(self.view, b"zoom", self)
        self.model = QLabel("-")
        self.model.setObjectName("modelLabel")
        self.model.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.model)
        self.setMinimumWidth(96)
        self.set_selected(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self.index)
        super().mousePressEvent(event)

    # --- hover: the pedal hops and the cables light up ------------------------------------

    def enterEvent(self, event: QEnterEvent) -> None:
        self._zoom_to(HOVER_ZOOM, 320, QEasingCurve.Type.OutBack)
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._zoom_to(1.0, 180, QEasingCurve.Type.OutCubic)
        self._set_hover(False)
        super().leaveEvent(event)

    def _zoom_to(self, value: float, ms: int, curve: QEasingCurve.Type) -> None:
        self._hop.stop()
        self._hop.setDuration(ms)
        self._hop.setEasingCurve(curve)
        self._hop.setStartValue(self.view.zoom)
        self._hop.setEndValue(value)
        self._hop.start()

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self._repolish()
        self.hovered.emit(self.index, on)

    # --- state ------------------------------------------------------------------------------

    def set_state(self, on: bool, model: str) -> None:
        was = self.property("on")
        self.power.show_state(on)
        self.model.setText(model)
        self.model.setToolTip(model)
        self.model.setEnabled(on)
        self.setProperty("on", on)
        target = 1.0 if on else OFF_OPACITY
        if was is None:  # first time: no animation
            self._fade.setOpacity(target)
        elif bool(was) != on:
            self._fade_anim.stop()
            self._fade_anim.setStartValue(self._fade.opacity())
            self._fade_anim.setEndValue(target)
            self._fade_anim.start()
        self._repolish()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._repolish()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


class _Wire(QWidget):
    """The short cable between two tiles; it lights up when a pedal next to it is hovered."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(16)
        self._lit = 0.0
        self._anim = QPropertyAnimation(self, b"lit", self)
        self._anim.setDuration(160)

    def light(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._lit)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_lit(self) -> float:
        return self._lit

    def _set_lit(self, value: float) -> None:
        self._lit = value
        self.update()

    lit = Property(float, _get_lit, _set_lit)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        if self._lit > 0.01:
            _glow(p, QPointF(self.width() / 2, y), 10, WIRE_LIT, self._lit * 0.6)
        pen = QPen(_mix(WIRE, WIRE_LIT, self._lit), 2 + self._lit,
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(1, y), QPointF(self.width() - 4, y))
        p.drawLine(QPointF(self.width() - 8, y - 4), QPointF(self.width() - 3, y))
        p.drawLine(QPointF(self.width() - 8, y + 4), QPointF(self.width() - 3, y))
        p.end()


class _Jack(QWidget):
    """Where the signal comes in (the guitar) and goes out (the amp or interface)."""

    def __init__(self, icon_name: str, text: str, tip: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip(tip)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)
        layout.addStretch(1)
        picture = QLabel()
        picture.setPixmap(icon(icon_name, MUTED, JACK_ICON).pixmap(JACK_ICON, JACK_ICON))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(text)
        label.setObjectName("jackLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
        layout.addWidget(label)
        layout.addStretch(1)
        self.setFixedWidth(52)


class ChainView(QFrame):
    module_selected = Signal(int)
    exp_requested = Signal()  # the EXP tile was clicked
    error = Signal(str)

    def __init__(self, editor: Editor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.selected = 0
        self.setObjectName("chainBar")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel(tr("Cadena de efectos"))
        title.setObjectName("cardTitle")
        self.count = QLabel()
        self.count.setObjectName("countBadge")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count)
        outer.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(0)
        # wires[i] goes into tile i, wires[i + 1] comes out of it.
        self.wires = [_Wire() for _ in range(len(MODULES) + 1)]
        row.addWidget(_Jack("guitar", "IN", tr("Entrada: tu guitarra")))
        self.tiles = [ModuleTile(i) for i in range(len(MODULES))]
        for i, tile in enumerate(self.tiles):
            row.addWidget(self.wires[i])
            tile.clicked.connect(self.select)
            tile.toggled.connect(self._on_toggle)
            tile.hovered.connect(self._hovered)
            row.addWidget(tile, 1)
        row.addWidget(self.wires[-1])
        row.addWidget(_Jack("speaker", "OUT", tr("Salida: amplificador, interfaz o audífonos")))
        # The expression pedal is not in the signal path: apart, past a divider (it sits on
        # the right of the real pedal too). The tile it moves wears an «EXP» tag.
        row.addSpacing(10)
        divider = QFrame()
        divider.setObjectName("chainDivider")
        divider.setFixedWidth(1)
        row.addWidget(divider)
        row.addSpacing(10)
        self.exp = ExpTile()
        self.exp.clicked.connect(self.exp_requested)
        self.exp.hovered.connect(self._exp_hovered)
        row.addWidget(self.exp, 1)
        outer.addLayout(row, 1)

        self.tiles[0].set_selected(True)
        self.setMinimumHeight(148)  # fits 1366 x 768 screens; grows up to 214
        self.setMaximumHeight(214)

    def select(self, index: int) -> None:
        self.tiles[self.selected].set_selected(False)
        self.exp.set_selected(False)
        self.selected = index
        self.tiles[index].set_selected(True)
        self.module_selected.emit(index)

    def select_exp(self) -> None:
        """Highlight the EXP tile (its page is shown instead of a module's)."""
        self.tiles[self.selected].set_selected(False)
        self.exp.set_selected(True)

    def refresh(self) -> None:
        if self.editor.state.edit is None:
            return
        for tile in self.tiles:
            block = self.editor.module(tile.index)
            tile.set_state(block.switch, self.editor.model_label(tile.index, block.type))
            tile.view.set_gear(*module_look(self.editor, tile.index))
        settings = self.editor.exp_settings()
        self.exp.set_settings(settings)
        name = settings.param_name if settings.function_on else None
        for tile in self.tiles:
            moved = bool(name) and settings.module == tile.index
            tile.exp_tag.setVisible(moved)
            tile.exp_tag.setToolTip(tr(
                "El pedal de expresión mueve su {name}",
                name=name,
            ) if moved else "")
        self._refresh_tap()
        self._count()

    def _refresh_tap(self) -> None:
        """The TAP LED follows the delay time (TIME, or TIME A of the dual delay)."""
        tap = self.tiles[DELAY].tap
        if tap is None:
            return
        block = self.editor.module(DELAY)
        model = self.editor.catalog[DELAY].model_for_type(block.type)
        time = next((p for p in (model.params if model else ())
                     if p.name.upper().startswith("TIME") and p.unit == "ms"), None)
        ms = 0
        if block.switch and time is not None:
            value = time.from_raw(block.words[time.word])
            ms = int(value) if isinstance(value, (int, float)) else 0
        tap.set_period(ms)

    def _count(self) -> None:
        on = sum(bool(t.power.isChecked()) for t in self.tiles)
        self.count.setText(tr("{on} de {total} encendidos", on=on, total=len(self.tiles)))

    def _exp_hovered(self, on: bool) -> None:
        """Hovering the EXP tile lights the effect it moves, as if it were hovered."""
        for tile in self.tiles:
            if not tile.exp_tag.isHidden():
                tile._set_hover(on)

    def _hovered(self, index: int, on: bool) -> None:
        self.wires[index].light(on)
        self.wires[index + 1].light(on)

    def _on_toggle(self, index: int, on: bool) -> None:
        try:
            self.editor.set_switch(index, on)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        tile = self.tiles[index]
        tile.set_state(on, tile.model.text())
        if index == DELAY:
            self._refresh_tap()
        self._count()

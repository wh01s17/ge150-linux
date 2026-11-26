"""The expression pedal: its tile at the end of «Cadena de efectos» and its page, shown like
a module's, to set what it does in the current preset (which effect and parameter it moves,
and the volume).

Everything is sent live, like the rest of the editor: saving the preset keeps it.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model import exp
from ge150.model.catalog import Param
from ge150.model.exp import ExpSettings
from ge150.model.gear import MODULE_TAGS
from ge150.model.preset import MODULES
from ge150.services.editor import Editor
from ge150.ui.theme import ACCENT

SHORT = ("FX", "DS", "AMP", "CAB", "NS", "EQ", "MOD", "DLY", "REV")
FX = MODULE_TAGS.index("FX")
WAH = ("CRY WAH", "POSITION")  # the shortcut: what most people want the pedal for


def summary(settings: ExpSettings, short: bool = True) -> str:
    """What the pedal does: "MOD · MIX", "VOLUMEN", "MOD · MIX + VOL" or "Sin asignar"."""
    name = settings.param_name if settings.function_on else None
    parts = []
    if name:
        parts.append(f"{SHORT[settings.module]} · {name}" if short
                     else tr(
                         "{parameter} del {effect}",
                         parameter=name,
                         effect=MODULES[settings.module].name,
                     ))
    if settings.volume_on:
        parts.append("VOL" if short and parts else (tr("VOLUMEN") if short else tr("el volumen")))
    if not parts:
        return tr("Sin asignar") if short else "nada"
    return " + ".join(parts) if short else tr(" y ").join(parts)


class ExpPedalArt(QWidget):
    """A drawn expression pedal: a treadle with a rubber grip on a dark base, and an LED."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lit = False
        self.setMinimumSize(48, 48)

    def set_lit(self, on: bool) -> None:
        self.lit = on
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = min(self.height() - 4.0, self.width() * 1.25, 460.0)  # big pages: not a wall
        w = h * 0.62
        x, y = (self.width() - w) / 2, (self.height() - h) / 2
        # Base, seen from above with the heel nearer.
        base = QRectF(x, y + h * 0.06, w, h * 0.94)
        p.setPen(QPen(QColor("#0d0f12"), 1.2))
        p.setBrush(QColor("#2a2f37"))
        p.drawRoundedRect(base, w * 0.16, w * 0.16)
        # Treadle: narrower at the toe, as if tilted away.
        top, bottom = y + h * 0.02, y + h * 0.82
        inset = w * 0.12
        path = QPainterPath(QPointF(x + inset, top))
        path.lineTo(x + w - inset, top)
        path.lineTo(x + w - inset * 0.45, bottom)
        path.lineTo(x + inset * 0.45, bottom)
        path.closeSubpath()
        shade = QLinearGradient(0, top, 0, bottom)
        shade.setColorAt(0, QColor("#4a525e"))
        shade.setColorAt(1, QColor("#343a44"))
        p.setBrush(shade)
        p.setPen(QPen(QColor("#15181d"), 1.2))
        p.drawPath(path)
        # Rubber grip.
        p.setPen(QPen(QColor("#1d2127"), min(max(1.0, h * 0.02), 5.0), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        ridges = 6 if h < 120 else 12
        for i in range(ridges):
            t = 0.12 + i * 0.66 / (ridges - 1)
            gy = top + (bottom - top) * t
            spread = inset * (1 - t * 0.55) + w * 0.08
            p.drawLine(QPointF(x + spread, gy), QPointF(x + w - spread, gy))
        # Hinge at the heel and the LED.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1a1d22"))
        p.drawRoundedRect(QRectF(x + w * 0.2, bottom + h * 0.03, w * 0.6, h * 0.06), 2, 2)
        led = QColor(ACCENT) if self.lit else QColor("#3a2a1a")
        p.setBrush(led)
        radius = min(h * 0.028, 7.0)
        p.drawEllipse(QPointF(x + w / 2, y + h * 0.94), radius, radius)
        p.end()


class ExpSideView(QWidget):
    """The pedal seen from the side, tilted as far as it is pressed: heel down (toe up) is
    0 %, toe down 100 %. The pedal only reports its position through the parameter it moves,
    so without one it is drawn grey, half way, with no number."""

    TOE_UP = 22.0  # degrees of the treadle at 0 %

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.position: float | None = None
        self.label = ""
        self.idle = tr("Sin posición")  # what it says without one
        self._shown = 0.5
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.setMinimumSize(200, 150)

    def set_position(self, position: float | None, label: str = "") -> None:
        self.label = label
        if position is not None:
            position = min(max(position, 0.0), 1.0)
            if self.position is not None:  # follow it smoothly
                self._anim.stop()
                self._anim.setStartValue(self._shown)
                self._anim.setEndValue(position)
                self._anim.start()
            else:
                self._shown = position
        self.position = position
        self.update()

    def _step(self, value: object) -> None:
        self._shown = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        known = self.position is not None
        shown = self._shown if known else 0.5
        w = min(self.width() - 20.0, 420.0)
        x0 = (self.width() - w) / 2
        floor = self.height() * 0.62
        length = w * 0.86
        hinge = QPointF(x0 + w * 0.06, floor - w * 0.05)
        # Base: a low wedge on the floor.
        base = QPainterPath(QPointF(x0, floor))
        base.lineTo(x0 + w, floor)
        base.lineTo(x0 + w, floor - w * 0.04)
        base.lineTo(x0 + w * 0.04, floor - w * 0.09)
        base.closeSubpath()
        p.setPen(QPen(QColor("#0d0f12"), 1.2))
        p.setBrush(QColor("#2a2f37"))
        p.drawPath(base)
        # The travel, dashed: from toe up (0 %) to toe down (100 %).
        rect = QRectF(hinge.x() - length, hinge.y() - length, 2 * length, 2 * length)
        p.setPen(QPen(QColor("#3a414d"), 1.4, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, 0, int(self.TOE_UP * 16))
        # Treadle.
        angle = math.radians(self.TOE_UP * (1 - shown))
        toe = QPointF(hinge.x() + length * math.cos(angle), hinge.y() - length * math.sin(angle))
        colour = QColor(ACCENT) if known else QColor("#4a525e")
        p.setPen(QPen(QColor("#4a525e"), max(6.0, w * 0.035), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(hinge, toe)
        p.setPen(QPen(colour, max(2.0, w * 0.01), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))  # the rubber on top
        lift = max(3.0, w * 0.017)
        p.drawLine(QPointF(hinge.x(), hinge.y() - lift), QPointF(toe.x(), toe.y() - lift))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1a1d22"))
        p.drawEllipse(hinge, w * 0.022, w * 0.022)
        # The value.
        p.setPen(QColor(ACCENT) if known else QColor("#4d5562"))
        font = p.font()
        font.setPixelSize(max(14, int(w * 0.08)))
        font.setBold(True)
        p.setFont(font)
        value = QRectF(x0, floor - length * 0.62 - font.pixelSize() * 1.4, w,
                       font.pixelSize() * 1.4)  # just above the travel
        p.drawText(value, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   f"{round(shown * 100)} %" if known else "—")
        font.setPixelSize(max(11, int(w * 0.035)))
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor("#8b95a3"))
        p.drawText(QRectF(x0, floor + 8, w, 40), Qt.AlignmentFlag.AlignHCenter,
                   self.label if known else self.idle)
        p.end()


class ExpTile(QFrame):
    """After OUT in the chain: it is not in the signal path, it moves something in it."""

    clicked = Signal()
    hovered = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("moduleTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Pedal de expresión: clic para elegir qué controla en este preset"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(3)
        self.name = QLabel("EXP")
        self.name.setObjectName("tileName")
        self.art = ExpPedalArt()
        self.label = QLabel("-")
        self.label.setObjectName("modelLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name)
        layout.addWidget(self.art, 1)
        layout.addWidget(self.label)
        self.setMinimumWidth(96)

    def set_settings(self, settings: ExpSettings) -> None:
        active = bool(settings.function_on and settings.param_name) or settings.volume_on
        self.label.setText(summary(settings))
        self.label.setToolTip(tr(
            "El pedal controla {control}",
            control=summary(settings, short=False),
        ))
        self.label.setEnabled(active)
        self.art.set_lit(active)
        self.setProperty("on", active)
        self._repolish()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._repolish()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.setProperty("hover", True)
        self._repolish()
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.setProperty("hover", False)
        self._repolish()
        self.hovered.emit(False)
        super().leaveEvent(event)

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


def _segment(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("segment")
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("cardTitle")
    return label


class ExpPage(QFrame):
    """Shown in place of a module page when the EXP tile is chosen: the pedal on the left,
    what it does in the current preset on the right.

    One choice: the pedal controls the volume, a parameter of an effect, or nothing. Orange is
    always what the pedal does; a softer border is what is being chosen (nothing is written
    until a parameter is picked).
    """

    error = Signal(str)
    message = Signal(str)  # for the status bar
    save_requested = Signal()  # «Guardar» of the unsaved-change notice

    MODES = (("volume", "Volumen"), ("effect", "Un efecto"), ("none", "Nada"))

    def __init__(self, editor: Editor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.module: int | None = 0  # the effect being looked at; None: every effect is off
        self.view = "none"  # the mode whose options are shown
        self._slot: int | None = None  # the preset it was chosen for
        #: (preset, module, PARA_CTRL) -> (model, raw value) a parameter had before the pedal
        #: took it over: it goes back to it when the pedal lets go of it.
        self._before: dict[tuple[int | None, int, int], tuple[int, int]] = {}
        self._loading = False
        self.setObjectName("modulePage")

        self.art = ExpPedalArt()
        self.art.setMinimumSize(220, 200)
        caption = QLabel(tr("Pedal de expresión de la GE150"))
        caption.setObjectName("basedOn")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.side = ExpSideView()
        arts = QHBoxLayout()
        arts.setSpacing(12)
        arts.addWidget(self.art, 2)
        arts.addWidget(self.side, 3)
        left = QVBoxLayout()
        left.addLayout(arts, 1)
        left.addWidget(caption)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        title = QLabel("EXP")
        title.setObjectName("moduleTitle")
        layout.addWidget(title)
        self.now = QLabel()
        self.now.setObjectName("summaryBox")
        self.now.setWordWrap(True)
        self.now.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.now)
        # The pedal only uses a new assignment once the preset is saved (seen on hardware).
        self.unsaved = QFrame()
        self.unsaved.setObjectName("unsavedNotice")
        unsaved = QHBoxLayout(self.unsaved)
        unsaved.setContentsMargins(12, 6, 8, 6)
        notice = QLabel(tr("Guarda el preset para que el pedal use este cambio."))
        notice.setWordWrap(True)
        save = QPushButton("Guardar")
        save.setObjectName("saveButton")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.save_requested)
        unsaved.addWidget(notice, 1)
        unsaved.addWidget(save)
        layout.addWidget(self.unsaved)

        layout.addWidget(_title(tr("¿Qué controla el pedal?")))
        modes = QHBoxLayout()
        modes.setSpacing(6)
        self.mode_buttons: dict[str, QPushButton] = {}
        for key, source in self.MODES:
            label = tr(source)
            button = _segment(label)
            button.clicked.connect(lambda _=False, k=key: self._choose_mode(k))
            self.mode_buttons[key] = button
            modes.addWidget(button, 1)
        layout.addLayout(modes)

        # --- volume: its range -----------------------------------------------------------
        self.volume_box = QWidget()
        volume = QHBoxLayout(self.volume_box)
        volume.setContentsMargins(0, 4, 0, 0)
        self.vol_min, self.vol_max = QSpinBox(), QSpinBox()
        for spin in (self.vol_min, self.vol_max):
            spin.setRange(*exp.VOLUME_RANGE)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._volume_range)
        volume.addWidget(QLabel(tr("Volumen mínimo")))
        volume.addWidget(self.vol_min)
        volume.addSpacing(16)
        volume.addWidget(QLabel(tr("máximo")))
        volume.addWidget(self.vol_max)
        volume.addStretch(1)
        layout.addWidget(self.volume_box)

        # --- an effect: which one, its model and the parameter ---------------------------
        self.effect_box = QWidget()
        effect = QVBoxLayout(self.effect_box)
        effect.setContentsMargins(0, 4, 0, 0)
        effect.setSpacing(10)
        self.choose = QLabel(tr("Elige el efecto y el parámetro que moverá el pedal."))
        self.choose.setObjectName("note")
        effect.addWidget(self.choose)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.modules = QButtonGroup(self)
        self.modules.setExclusive(False)  # checked = assigned, often none
        for index in range(len(MODULES)):
            button = _segment("")
            self.modules.addButton(button, index)
            grid.addWidget(button, index // 3, index % 3)
        self.modules.idClicked.connect(self._choose_module)
        effect.addLayout(grid)
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Modelo:"))
        self.model = QComboBox()
        self.model.activated.connect(self._choose_model)
        model_row.addWidget(self.model, 1)
        effect.addLayout(model_row)
        self.off_row = QWidget()
        off = QHBoxLayout(self.off_row)
        off.setContentsMargins(0, 0, 0, 0)
        self.off_text = QLabel()
        self.off_text.setWordWrap(True)
        self.off_text.setStyleSheet("color: #f0c860;")
        turn_on = QPushButton(tr("Encenderlo"))
        turn_on.setObjectName("segment")
        turn_on.clicked.connect(self._turn_on)
        off.addWidget(self.off_text, 1)
        off.addWidget(turn_on)
        effect.addWidget(self.off_row)
        effect.addWidget(_title(tr("Parámetro")))
        self.params_box = QWidget()
        self.params_layout = QHBoxLayout(self.params_box)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setSpacing(6)
        self.params = QButtonGroup(self)
        self.params.idClicked.connect(self._choose_param)
        effect.addWidget(self.params_box)
        wah_row = QHBoxLayout()
        self.wah_button = _segment("")
        self.wah_button.clicked.connect(self._wah)
        wah_row.addWidget(self.wah_button)
        wah_row.addStretch(1)
        effect.addLayout(wah_row)
        layout.addWidget(self.effect_box)

        layout.addStretch(1)
        scroll = QScrollArea()  # fits small screens
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("expScroll")  # transparent with its body only (theme.py)
        page.setObjectName("expBody")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(22)
        outer.addLayout(left, 5)
        outer.addWidget(scroll, 6)

    def load(self) -> None:
        """Show the current preset from the start (see :meth:`_pick_module`)."""
        self._slot = None
        self.refresh()

    def _pick_module(self, settings: ExpSettings) -> int | None:
        """The effect to show: never one that is off. Another preset starts on the effect the
        pedal is assigned to (or the first one on), and on the mode it uses."""
        on = [i for i in range(len(MODULES)) if self.editor.module(i).switch]
        slot = self.editor.state.current_slot
        if slot == self._slot and self.module in on:
            return self.module
        if slot != self._slot:
            self.view = self._modes(settings)[0] if self._modes(settings) else "none"
        self._slot = slot
        if settings.module in on:
            return settings.module
        return on[0] if on else None

    # --- reading ------------------------------------------------------------------------

    def _settings(self) -> ExpSettings:
        return self.editor.exp_settings()

    @staticmethod
    def _modes(settings: ExpSettings) -> list[str]:
        """What the pedal does: ``["effect"]``, ``["volume"]``, both (presets made on the
        pedal can have both on) or none."""
        modes = ["effect"] if settings.function_on and settings.param_name else []
        return modes + (["volume"] if settings.volume_on else [])

    def refresh(self) -> None:
        if self.editor.state.edit is None:
            return
        self._loading = True
        try:
            self._refresh()
        finally:
            self._loading = False

    def _refresh(self) -> None:
        settings = self._settings()
        self.module = self._pick_module(settings)
        modes = self._modes(settings)
        if modes != getattr(self, "_last_modes", modes):  # e.g. the toe switch: follow it
            self.view = modes[0] if modes else "none"
        self._last_modes = modes
        self.art.set_lit(bool(modes))
        stored = self.editor.state.presets.get(self.editor.state.current_slot or 0)
        self.unsaved.setVisible(stored is not None
                                and ExpSettings.from_words(stored.words) != settings)
        name = settings.param_name if "effect" in modes else None
        parts = []
        if name:
            parts.append(tr(
                "mueve <b>{parameter}</b> del <b>{effect}</b> ({model})",
                parameter=name,
                effect=MODULES[settings.module].name,
                model=self._model_name(settings.module),
            ))
        if "volume" in modes:
            parts.append(tr(
                "controla el <b>volumen</b> (de {minimum} a {maximum})",
                minimum=settings.vol_min,
                maximum=settings.vol_max,
            ))
        self.now.setText(tr("El pedal {controls}.", controls=tr(" y ").join(parts)) if parts
                         else tr("El pedal no controla nada."))
        self._update_position(settings, name)
        for key, button in self.mode_buttons.items():
            active = key in modes or (key == "none" and not modes)
            button.setChecked(active)
            button.setProperty("browsing", key == self.view and not active)
            _repolish(button)
        # Only the options of the choice being looked at (the active one, or the one clicked
        # and not written yet: «Un efecto» before a parameter is picked).
        self.volume_box.setVisible(self.view == "volume")
        self.effect_box.setVisible(self.view == "effect")
        self.choose.setVisible("effect" not in modes)
        self.vol_min.setValue(settings.vol_min)
        self.vol_max.setValue(settings.vol_max)

        assigned = settings.module if name else None
        for index in range(len(MODULES)):
            button = self.modules.button(index)
            block = self.editor.module(index)
            state = "" if block.switch else tr("  · apagado")
            button.setText(f"{SHORT[index]}{state}\n{self._model_name(index)}")
            button.setChecked(assigned == index and block.switch)  # off: the warning says it
            button.setProperty("browsing", index == self.module and assigned != index)
            _repolish(button)
            # An effect that is off cannot be chosen: the pedal would move nothing you hear.
            button.setEnabled(block.switch)
            button.setToolTip("" if block.switch else
                              tr(
                                  "El {effect} está apagado: enciéndelo con su LED en la cadena de "
                                  "efectos para poder asignarle el pedal.",
                                  effect=MODULES[index].name,
                              ))
        self._fill_models()
        self._fill_params(settings, assigned)
        # The pedal moves a parameter of an effect that is off: say why nothing is heard.
        assigned_off = assigned is not None and not self.editor.module(assigned).switch
        self.off_row.setVisible(assigned_off)
        if assigned_off:
            self.off_text.setText(tr(
                "El {effect} está apagado en este preset: mover el pedal no cambiará nada.",
                effect=MODULES[settings.module].name,
            ))
        self._refresh_wah(settings, name)

    def _update_position(self, settings: ExpSettings, name: str | None) -> None:
        """Where the pedal is, read from the parameter it moves (the pedal reports nothing
        else: not in volume mode)."""
        param = self._param_of(settings.module, settings.para) if name else None
        self.side.idle = (tr("En volumen la GE150 no informa la posición") if settings.volume_on
                          else tr("Sin posición"))
        if param is None or param.kind != "number" or param.max <= param.min:
            self.side.set_position(None)
            return
        raw = self.editor.module(settings.module).words[param.word]
        value = param.from_raw(raw)
        if not isinstance(value, (int, float)):
            self.side.set_position(None)
            return
        self.side.set_position((value - param.min) / (param.max - param.min),
                               tr(
                                   "{parameter} del {effect}: {value}",
                                   parameter=param.name,
                                   effect=MODULES[settings.module].name,
                                   value=param.format(raw),
                               ))

    def _model_name(self, index: int) -> str:
        return self.editor.model_display(index, self.editor.module(index).type)

    def _fill_models(self) -> None:
        key = (self.module, None if self.module is None else self.editor.module(self.module).type,
               self.editor.state.user_names)
        if key == getattr(self, "_models_key", object()):
            return
        self._models_key = key
        self.model.clear()
        self.model.setEnabled(self.module is not None)
        if self.module is None:
            return
        info = self.editor.catalog[self.module]
        for type_index, model in enumerate(info.models):
            if model.hidden:
                continue
            label = self.editor.model_label(self.module, type_index)
            if not exp.controllable(self.module, model):
                label += tr("  (sin parámetros para el pedal)")
            self.model.addItem(label, type_index)
        current = self.model.findData(self.editor.module(self.module).type)
        self.model.setCurrentIndex(current)

    def _fill_params(self, settings: ExpSettings, assigned: int | None) -> None:
        key = (self.module, None if self.module is None else self.editor.module(self.module).type,
               settings.para if assigned == self.module else None)
        if key == getattr(self, "_params_key", object()):
            return
        self._params_key = key
        for button in self.params.buttons():
            self.params.removeButton(button)
        while self.params_layout.count():
            widget = self.params_layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # deleteLater() alone leaves it drawn until the next event loop
                widget.deleteLater()
        if self.module is None:
            empty = QLabel(tr("Todos los efectos están apagados: enciende alguno en la cadena."))
            empty.setObjectName("note")
            self.params_layout.addWidget(empty)
            self.params_layout.addStretch(1)
            return
        block = self.editor.module(self.module)
        model = self.editor.catalog[self.module].model_for_type(block.type)
        params = exp.controllable(self.module, model)
        for param in params:
            para = exp.para_index(self.module, param.name)
            assert para is not None
            button = _segment(param.name)
            button.setChecked(assigned == self.module and settings.para == para)
            self.params.addButton(button, para)
            self.params_layout.addWidget(button)
        if not params:
            empty = QLabel(tr("Este modelo no tiene parámetros que el pedal pueda mover."))
            empty.setObjectName("note")
            self.params_layout.addWidget(empty)
        self.params_layout.addStretch(1)

    # --- writing ------------------------------------------------------------------------

    def _write(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:
            self.error.emit(str(exc))
        self.refresh()

    def _change(self, **values: object) -> None:
        settings = self._settings()
        for key, value in values.items():
            setattr(settings, key, value)
        self._write(lambda: self.editor.set_exp_settings(settings))

    def _assign(self, target: tuple[int, int] | None = None, **values: object) -> None:
        """Change what the pedal does. The parameter it stops moving goes back to the value
        it had before (while moved, it follows the pedal: un-assigned, it would stay wherever
        the pedal was, and be saved like that). ``target``: the (module, PARA_CTRL) it moves
        from now on; None: no parameter."""
        def action() -> None:
            self._release(keep=target)
            settings = self._settings()
            if target is not None:
                self._remember(*target)
                settings.module, settings.para = target
            settings.function_on = target is not None
            for key, value in values.items():
                setattr(settings, key, value)
            self.editor.set_exp_settings(settings)

        self._write(action)

    def _param_of(self, module: int, para: int) -> Param | None:
        name = exp.para_name(module, para)
        model = self.editor.catalog[module].model_for_type(self.editor.module(module).type)
        return next((p for p in exp.controllable(module, model) if p.name.upper() == name), None)

    def _remember(self, module: int, para: int) -> None:
        param = self._param_of(module, para)
        key = (self.editor.state.current_slot, module, para)
        if param is not None and key not in self._before:
            block = self.editor.module(module)
            self._before[key] = (block.type, block.words[param.word])

    def _release(self, keep: tuple[int, int] | None = None) -> None:
        settings = self._settings()
        if not (settings.function_on and settings.param_name) \
                or (settings.module, settings.para) == keep:
            return
        slot = self.editor.state.current_slot
        saved = self._before.pop((slot, settings.module, settings.para), None)
        why = tr("su valor antes del pedal")
        if saved is None:
            # Assigned in the stored preset (not on this page): the live value follows the
            # pedal, the stored one does not, so that is the one to go back to.
            stored = self.editor.state.presets.get(slot or 0)
            if stored is not None:
                block = stored.module(settings.module)
                param = self._param_of(settings.module, settings.para)
                if param is not None:
                    saved = (block.type, block.words[param.word])
                    why = tr("su valor guardado en el preset")
        param = self._param_of(settings.module, settings.para)
        block = self.editor.module(settings.module)
        if saved is None or param is None or saved[0] != block.type:
            return  # another model since: the value would mean something else
        if block.words[param.word] != saved[1]:
            self.editor.set_word(settings.module, param.word, saved[1])
            self.message.emit(tr(
                "{parameter} del {effect} vuelve a {value}, {reason}",
                parameter=param.name,
                effect=MODULES[settings.module].name,
                value=param.format(saved[1]),
                reason=why,
            ))

    def _choose_mode(self, mode: str) -> None:
        self.view = mode
        if mode == "volume":
            self._assign(volume_on=True)
        elif mode == "none":
            self._assign(volume_on=False)
        else:  # an effect: written once a parameter is picked
            self.refresh()

    def _choose_module(self, index: int) -> None:
        self.module = index
        self.refresh()

    def _choose_model(self, row: int) -> None:
        type_index, module = self.model.itemData(row), self.module
        if module is not None and type_index is not None \
                and type_index != self.editor.module(module).type:
            self._write(lambda: self.editor.set_type(module, int(type_index)))

    def _choose_param(self, para: int) -> None:
        """The pedal moves this parameter, and only it (not the volume too)."""
        if not self._loading and self.module is not None:
            self.view = "effect"
            self._assign((self.module, para), volume_on=False)

    def _volume_range(self) -> None:
        if not self._loading:
            low, high = sorted((self.vol_min.value(), self.vol_max.value()))
            self._change(vol_min=low, vol_max=high)

    def _turn_on(self) -> None:
        """«Encenderlo»: the effect the pedal is assigned to, which is off."""
        module = self._settings().module
        self.module = module
        self._write(lambda: self.editor.set_switch(module, True))

    def _wah_plan(self) -> tuple[int | None, str, bool]:
        """``(model to put in FX or None to keep it, the FX model now, FX is on)``: an FX
        that already has a POSITION (CRY WAH, 535 WAH) is kept."""
        block = self.editor.module(FX)
        info = self.editor.catalog[FX]
        model = info.model_for_type(block.type)
        has_position = any(p.name == WAH[1] for p in exp.controllable(FX, model))
        cry = next(i for i, m in enumerate(info.models) if m.name == WAH[0])
        return (None if has_position else cry), self._model_name(FX), block.switch

    def _refresh_wah(self, settings: ExpSettings, name: str | None) -> None:
        replace, current, on = self._wah_plan()
        self.wah_button.setChecked(bool(name) and settings.module == FX and replace is None
                                   and name == WAH[1] and on)
        if replace is None:
            self.wah_button.setText(tr(
                "Wah: el {parameter} del {model}",
                parameter=WAH[1],
                model=current,
            ))
            self.wah_button.setToolTip(tr(
                "El pedal mueve el {parameter} del {model}",
                parameter=WAH[1],
                model=current,
            ))
        else:
            self.wah_button.setText(tr(
                "Wah: {wah} en el FX (reemplaza {model})",
                wah=WAH[0],
                model=current,
            ))
            self.wah_button.setToolTip(tr(
                "Cambia el {model} del FX por un {wah} y el pedal mueve su {parameter}",
                model=current,
                wah=WAH[0],
                parameter=WAH[1],
            ))

    def _wah(self) -> None:
        replace, current, _ = self._wah_plan()
        if replace is not None and QMessageBox.question(
                self, "Atajo wah",
                f"El FX de este preset tiene el {current}. ¿Reemplazarlo por un {WAH[0]}?\n\n"
                f"En este preset te quedarás sin el {current}. El resto de la cadena no cambia "
                "y nada se guarda hasta que pulses Guardar.") != QMessageBox.StandardButton.Yes:
            self.refresh()  # the click checked the button: back to what the pedal does
            return
        para = exp.para_index(FX, WAH[1])
        self.module, self.view = FX, "effect"

        def apply() -> None:
            self._release(keep=(FX, para or 0))
            block = self.editor.module(FX)
            if replace is not None:
                block.type = replace
            block.switch = True
            self.editor.write_module(block)
            self._remember(FX, para or 0)
            settings = self._settings()
            settings.module, settings.para = FX, para or 0
            settings.function_on, settings.volume_on = True, False
            self.editor.set_exp_settings(settings)

        self._write(apply)


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)

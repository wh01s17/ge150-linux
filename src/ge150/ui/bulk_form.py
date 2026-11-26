"""«Edición manual»: the same change for every ticked preset, in four short steps
(module, on/off, model, one parameter set to a value or moved by an amount). A live summary
says what will happen. It only plans: the changes go to the review table
(``bulk_review.py``) like the AI's."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ge150.i18n import tr
from ge150.model.catalog import Model, Param
from ge150.model.gear import MODULE_TAGS
from ge150.model.preset import Preset
from ge150.services import bulk_edit
from ge150.services.editor import Editor

KEEP, ON, OFF = "", "on", "off"
SET, ADD = "set", "add"
SHORT = {"DELAY": "DLY", "REVERB": "REV"}


def _segments(parent_layout: QHBoxLayout, items: list[tuple[str, Any, str]],
              group: QButtonGroup) -> None:
    """Exclusive checkable buttons (``label, data, tooltip``) sharing one row."""
    for i, (label, data, tip) in enumerate(items):
        button = QPushButton(label)
        button.setObjectName("segment")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("value", data)
        if tip:
            button.setToolTip(tip)
        group.addButton(button, i)
        parent_layout.addWidget(button)
    group.buttons()[0].setChecked(True)


def _step(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("stepTitle")
    return label


class ValueEditor(QWidget):
    """A slider plus an exact box (number), or a list (enum), for one parameter."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.param: Param | None = None
        self.relative = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        number = QWidget()
        row = QHBoxLayout(number)
        row.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.box = QDoubleSpinBox()
        self.box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.box.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.box.setMinimumWidth(96)
        row.addWidget(self.slider, 1)
        row.addWidget(self.box)
        self.option = QComboBox()
        self.stack.addWidget(number)
        self.stack.addWidget(self.option)
        layout.addWidget(self.stack)
        self.slider.valueChanged.connect(self._from_slider)
        self.box.valueChanged.connect(self._from_box)
        self.option.currentIndexChanged.connect(lambda _: self.changed.emit())

    def setup(self, param: Param, relative: bool) -> None:
        self.param, self.relative = param, relative
        if param.kind == "enum":
            self.option.blockSignals(True)
            self.option.clear()
            self.option.addItems(list(param.options))
            self.option.blockSignals(False)
            self.stack.setCurrentWidget(self.option)
            self.changed.emit()
            return
        self.stack.setCurrentIndex(0)
        steps = param.raw_max
        for widget in (self.slider, self.box):
            widget.blockSignals(True)
        self.slider.setRange(-steps if relative else 0, steps)
        span = param.max - param.min
        self.box.setDecimals(param.decimals)
        self.box.setSingleStep(param.step)
        self.box.setRange(-span if relative else param.min, span if relative else param.max)
        self.box.setPrefix("+" if relative else "")
        self.box.setSuffix(f" {param.unit}" if param.unit else "")
        start = 0 if relative else round((param.min + param.max) / 2 / param.step) * param.step
        self.box.setValue(start)
        self.slider.setValue(self._to_steps(start))
        for widget in (self.slider, self.box):
            widget.blockSignals(False)
        self.changed.emit()

    def _to_steps(self, value: float) -> int:
        param = self.param
        if param is None:
            return 0
        base = 0 if self.relative else param.offset
        return round((value - base) / param.step)

    def _from_slider(self, steps: int) -> None:
        param = self.param
        if param is None:
            return
        base = 0 if self.relative else param.offset
        self.box.blockSignals(True)
        self.box.setValue(base + steps * param.step)
        self.box.setPrefix("+" if self.relative and steps >= 0 else "")
        self.box.blockSignals(False)
        self.changed.emit()

    def _from_box(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_steps(value))
        self.slider.blockSignals(False)
        self.box.setPrefix("+" if self.relative and value >= 0 else "")
        self.changed.emit()

    def text(self) -> str:
        if self.param is not None and self.param.kind == "enum":
            return self.option.currentText()
        return self.box.cleanText()

    def value(self) -> float:
        return self.box.value()


class BulkForm(QWidget):
    planned = Signal(object)  # list[PresetEdit]
    message = Signal(str)

    def __init__(self, editor: Editor, ticked: Callable[[], list[int]],
                 bases: Callable[[], dict[int, list[int]]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editor, self.ticked, self.bases = editor, ticked, bases
        self._params: dict[str, Param] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        title = QLabel(tr("Edición manual"))
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        hint = QLabel(tr("El mismo cambio para todos los presets marcados. Se agrega a "
                      "<b>Cambios pendientes</b>; nada se escribe hasta que aprietes Aplicar."))
        hint.setObjectName("note")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(6)
        layout.addWidget(_step(tr("1 · Módulo")))
        modules = QHBoxLayout()
        modules.setSpacing(4)
        self.modules = QButtonGroup(self)
        _segments(modules, [(SHORT.get(tag, tag), index, editor.catalog[index].name)
                            for index, tag in enumerate(MODULE_TAGS)], self.modules)
        layout.addLayout(modules)

        layout.addWidget(_step(tr("2 · Encendido")))
        states = QHBoxLayout()
        states.setSpacing(4)
        self.states = QButtonGroup(self)
        _segments(states, [(tr("Igual"), KEEP, tr("Cada preset conserva su estado")),
                           (tr("Encender"), ON, ""), (tr("Apagar"), OFF, "")], self.states)
        states.addStretch(1)
        layout.addLayout(states)

        layout.addWidget(_step(tr("3 · Modelo")))
        self.model = QComboBox()
        layout.addWidget(self.model)

        layout.addWidget(_step(tr("4 · Parámetro")))
        self.param = QComboBox()
        layout.addWidget(self.param)
        self.value_row = QWidget()
        value_layout = QHBoxLayout(self.value_row)
        value_layout.setContentsMargins(0, 2, 0, 0)
        self.modes = QButtonGroup(self)
        modes = QHBoxLayout()
        modes.setSpacing(4)
        _segments(modes, [(tr("Fijar"), SET, tr("Todos los presets quedan con este valor")),
                          (tr("Sumar"), ADD, tr("Cada preset sube o baja esta cantidad desde su "
                                         "valor actual"))], self.modes)
        value_layout.addLayout(modes)
        self.value = ValueEditor()
        value_layout.addWidget(self.value, 1)
        layout.addWidget(self.value_row)

        layout.addSpacing(10)
        self.summary = QLabel()
        self.summary.setObjectName("summaryBox")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.add_button = QPushButton(tr("Agregar a cambios pendientes"))
        self.add_button.setObjectName("saveButton")
        self.add_button.clicked.connect(self.plan)
        layout.addWidget(self.add_button)
        layout.addStretch(1)

        self.modules.idClicked.connect(lambda _: self._fill_models())
        self.states.idClicked.connect(lambda _: self._update())
        self.modes.idClicked.connect(lambda _: self._fill_value())
        self.model.currentIndexChanged.connect(lambda _: self.refresh())
        self.param.currentIndexChanged.connect(lambda _: self._fill_value())
        self.value.changed.connect(self._update)
        self._fill_models()

    # --- choices ----------------------------------------------------------------------

    @property
    def index(self) -> int:
        return int(self.modules.checkedButton().property("value"))

    def _state(self) -> str:
        return str(self.states.checkedButton().property("value"))

    def _mode(self) -> str:
        return str(self.modes.checkedButton().property("value"))

    def select_module(self, index: int) -> None:
        self.modules.button(index).setChecked(True)
        self._fill_models()

    def select_state(self, value: str) -> None:
        for button in self.states.buttons():
            if button.property("value") == value:
                button.setChecked(True)
        self._update()

    def select_mode(self, value: str) -> None:
        for button in self.modes.buttons():
            if button.property("value") == value:
                button.setChecked(True)
        self._fill_value()

    def _fill_models(self) -> None:
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItem(tr("Mantener el modelo de cada preset"), "")
        index = self.index
        for type_index, model in enumerate(self.editor.catalog[index].models):
            if model.hidden:
                continue
            text = (f"{MODULE_TAGS[index]} {model.number}" if model.user_slot else model.name)
            self.model.addItem(self.editor.model_display(index, type_index), text)
        self.model.blockSignals(False)
        self.refresh()

    def _chosen_model(self) -> Model | None:
        text = str(self.model.currentData() or "")
        if not text:
            return None
        info = self.editor.catalog[self.index]
        for model in info.models:
            label = f"{MODULE_TAGS[self.index]} {model.number}" if model.user_slot else model.name
            if label == text:
                return model
        return None

    def _base(self, slot: int) -> Preset | None:
        stored = self.editor.state.presets.get(slot)
        if stored is None:
            return None
        words = self.bases().get(slot, stored.words)
        return Preset(slot, stored.name, list(words))

    def _models_in_use(self) -> list[Model]:
        """The model each ticked preset has in this module (after pending changes)."""
        models = []
        for slot in self.ticked():
            base = self._base(slot)
            if base is None:
                continue
            model = self.editor.catalog[self.index].model_for_type(base.module(self.index).type)
            if model is not None:
                models.append(model)
        return models

    def refresh(self) -> None:
        """Parameters of the chosen model, or the ones every ticked preset's model has."""
        chosen = self._chosen_model()
        models = [chosen] if chosen else self._models_in_use()
        self._params = {}
        if models:
            common = set.intersection(*({p.name for p in m.params if p.kind != "raw"}
                                        for m in models))
            self._params = {p.name: p for p in models[0].params if p.name in common}
        current = self.param.currentData()
        self.param.blockSignals(True)
        self.param.clear()
        if self._params:
            self.param.addItem(tr("Sin cambiar parámetros"), "")
        else:
            self.param.addItem(tr("Marca presets o elige un modelo para ver sus parámetros")
                               if not models else tr("Sin parámetros en común"), "")
        for name in self._params:
            self.param.addItem(name, name)
        self.param.setCurrentIndex(max(self.param.findData(current), 0))
        self.param.setEnabled(bool(self._params))
        self.param.blockSignals(False)
        self._fill_value()

    def _fill_value(self) -> None:
        param = self._params.get(str(self.param.currentData() or ""))
        self.value_row.setVisible(param is not None)
        if param is None:
            self._update()
            return
        enum = param.kind == "enum"
        for button in self.modes.buttons():
            button.setVisible(not enum)
        relative = not enum and self._mode() == ADD
        self.value.setup(param, relative)

    # --- summary and planning ---------------------------------------------------------

    def describe(self) -> str:
        """What the button will do, in words ("" = nothing chosen)."""
        tag = MODULE_TAGS[self.index]
        parts = []
        state = self._state()
        if state:
            parts.append(tr("encender {tag}" if state == ON else "apagar {tag}", tag=tag))
        if self.model.currentData():
            parts.append(tr("modelo {model}", model=self.model.currentText()))
        name = str(self.param.currentData() or "")
        if name and name in self._params:
            if self._params[name].kind != "enum" and self._mode() == ADD:
                parts.append(tr(
                    "{name} {value} sobre el valor de cada preset",
                    name=name,
                    value=self.value.text(),
                ))
            else:
                parts.append(f"{name} = {self.value.text()}")
        if not parts:
            return ""
        text = ", ".join(parts)
        if not state:
            text = f"{tag}: {text}"
        return text[0].upper() + text[1:]

    def _update(self) -> None:
        count = len(self.ticked())
        action = self.describe()
        if not count:
            self.summary.setText(tr("Marca presets en la lista de la izquierda."))
        elif not action:
            self.summary.setText(tr("Elige qué cambiar: encendido, modelo o un parámetro."))
        else:
            self.summary.setText(tr(
                "<b>{action}</b><br>en {count} preset{plural}",
                action=action,
                count=count,
                plural='s' * (count != 1),
            ))
        self.add_button.setEnabled(bool(count and action))
        self.add_button.setText(tr(
            "Agregar a cambios pendientes ({count})",
            count=count,
        ) if count and action
                                else tr("Agregar a cambios pendientes"))

    def specs(self) -> list[dict[str, Any]]:
        tag = MODULE_TAGS[self.index]
        state = self._state()
        model_text = str(self.model.currentData() or "")
        name = str(self.param.currentData() or "")
        chosen = self._chosen_model()
        result = []
        for slot in self.ticked():
            base = self._base(slot)
            if base is None:
                continue
            entry: dict[str, Any] = {"module": tag}
            if state:
                entry["on"] = state == ON
            if model_text:
                entry["model"] = model_text
            if name:
                block = base.module(self.index)
                model = chosen or self.editor.catalog[self.index].model_for_type(block.type)
                param = next((p for p in (model.params if model else ()) if p.name == name),
                             None)
                if param is not None and param.kind == "enum":
                    value = self.value.text()
                elif param is not None and self._mode() == ADD:
                    current = param.from_raw(block.words[param.word])
                    number = current if isinstance(current, (int, float)) else 0
                    value = str(number + self.value.value())
                else:
                    value = str(self.value.value())
                entry["params"] = [{"name": name, "value": value}]
            result.append({"slot": slot, "modules": [entry]})
        return result

    def plan(self) -> None:
        if not self.ticked() or not self.describe():
            self._update()
            return
        edits = bulk_edit.plan_edits(self.editor, self.specs(), self.bases())
        if not any(e.changed or e.blocked for e in edits):
            self.message.emit(tr("Esos presets ya están así: no hay nada que cambiar."))
            return
        self.planned.emit(edits)
        self.message.emit(tr("{count} presets agregados a Cambios pendientes.", count=len(edits)))

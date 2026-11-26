"""The GE150 as a set of tools for AI clients (transport-agnostic, see ``server.py``).

Rules (docs/plan-ia-tone3000.md §3.2, §10): reads are free; changes go to the edit
buffer only (live, reversible with ``undo``). Writing the pedal's memory (saving a preset,
storing an IR) needs ``confirm``: the GE150 editor asks the user in a dialog; without the
editor it is refused. Nothing resets, restores or flashes the pedal. Every method returns
plain JSON-able data.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from ge150.i18n import tr
from ge150.model.gear import MODULE_TAGS, describe, gear_for
from ge150.model.preset import EXP_KEYS, EXP_OFFSET, MODULES, PRESET_COUNT
from ge150.services import ai_permissions, tone_ai
from ge150.services.editor import Editor

Confirm = Callable[[str], bool]
#: (question, the 10 slots as list_user_irs shows them) -> chosen slot 1..10, or 0 = cancel.
PickSlot = Callable[[str, list[dict[str, Any]]], int]
#: (planned edits of stored presets, summary) -> shown to the user for review.
OnProposal = Callable[[list[Any], str], None]

T = TypeVar("T")

INSTRUCTIONS = """Controls a MOOER GE150 guitar multi-effects pedal connected to this computer.
Signal chain (fixed order): FX -> DS -> AMP -> CAB -> NS -> EQ -> MOD -> DELAY -> REVERB.
Workflow: read get_current_patch, look up models with get_catalog(module), then change the
sound with set_module or apply_patch: they change the LOADED preset (get_current_patch
says which), never another one. Changes are LIVE in the edit buffer and are NOT saved:
the user saves it (Save in the GE150 editor, or save_preset if they ask you to). Use undo
to go back. To change several STORED presets (e.g. "every Av83 preset"), read them with
get_presets(query) and call propose_preset_edits once with a change per preset (the user
reviews and applies them in the editor): never loop select_preset + save_preset. It also
renames stored presets (name) and moves or reorders them (copy_from). Use
only model and parameter names from get_catalog; values outside a range are clamped and
reported. save_preset and the TONE3000 IR tools write the pedal's memory: they
only work with the GE150 editor open, which asks the user to confirm each time. TONE3000
(www.tone3000.com) hosts cabinet IRs made by users: load_tone3000_ir picks the slot itself
(slot 0) and selects the IR in the CAB module; always tell the user the author and licence
of an IR you load."""


class ToolError(Exception):
    """A request the pedal cannot honour (the message is shown to the AI)."""


def _module_index(module: str) -> int:
    key = module.strip().upper()
    aliases = {"COMP": "FX", "OD": "DS", "DIST": "DS", "DLY": "DELAY", "REV": "REVERB",
               "GATE": "NS", "NOISE": "NS"}
    key = aliases.get(key, key)
    if key not in MODULE_TAGS:
        raise ToolError(f"unknown module {module!r}; use one of {', '.join(MODULE_TAGS)}")
    return MODULE_TAGS.index(key)


class PedalTools:
    """Thread-safe facade over an :class:`Editor` (one lock for every pedal access)."""

    def __init__(self, editor: Editor, lock: threading.Lock | None = None,
                 confirm: Confirm | None = None, tone3000: Any = None,
                 pick_ir_slot: PickSlot | None = None,
                 on_proposal: OnProposal | None = None) -> None:
        self.editor = editor
        self.lock = lock or threading.Lock()
        self.confirm = confirm
        self.pick_ir_slot = pick_ir_slot
        self.on_proposal = on_proposal
        self.history: list[list[int]] = []
        self._tone3000 = tone3000

    def _ask(self, question: str) -> None:
        """Ask the user (never while holding the pedal lock)."""
        if self.confirm is None:
            raise ToolError("writing the pedal's memory needs the GE150 editor open (it asks "
                            "the user to confirm); ask the user to open it or to do it there")
        if not self.confirm(question):
            raise ToolError("the user declined")

    def _locked(self, fn: Callable[[], T]) -> T:
        with self.lock:
            if self.editor.state.edit is None:
                raise ToolError("the pedal has not been read yet")
            return fn()

    # --- reading ----------------------------------------------------------------------

    def get_catalog(self, module: str = "") -> dict[str, Any]:
        if not module:
            return {"modules": [
                {"module": tag, "name": info.name,
                 "models": len([m for m in info.models if not m.hidden])}
                for tag, info in zip(MODULE_TAGS, self.editor.catalog, strict=True)]}
        index = _module_index(module)
        info = self.editor.catalog[index]
        names = self.editor.state.user_names
        models = []
        for model in info.models:
            if model.hidden:
                continue
            label = model.name
            if model.user_slot:  # what to pass back as "model": its name on the pedal
                label = self.editor.model_display(index, model.number - 1)
            entry: dict[str, Any] = {"model": label, "number": model.number}
            gear = gear_for(index, model.number)
            if gear.name:
                entry["based_on" if not gear.generic else "kind"] = gear.name
            if model.user_slot:
                bank = (names.ir if MODULE_TAGS[index] == "CAB" else names.nr) if names else []
                slot = model.number - (27 if MODULE_TAGS[index] == "CAB" else 56)
                entry["user_slot"] = bank[slot] if 0 <= slot < len(bank) else "?"
            entry["params"] = [_param_info(p) for p in model.params]
            models.append(entry)
        return {"module": MODULE_TAGS[index], "name": info.name, "models": models}

    def get_current_patch(self) -> dict[str, Any]:
        return self._locked(self._patch)

    def _patch(self) -> dict[str, Any]:
        state = self.editor.state
        stored = state.presets.get(state.current_slot or 0)
        edit = state.edit
        modules = []
        for index in range(len(MODULES)):
            block = self.editor.module(index)
            model = self.editor.catalog[index].model_for_type(block.type)
            entry: dict[str, Any] = {"module": MODULE_TAGS[index], "on": block.switch,
                                     "model": self.editor.model_display(index, block.type)}
            if model is not None:
                gear = gear_for(index, model.number)
                entry["based_on"] = describe(gear, False)
                entry["params"] = {p.name: p.format(block.words[p.word]) for p in model.params}
            modules.append(entry)
        exp = {key: edit.words[EXP_OFFSET + i] for i, key in enumerate(EXP_KEYS)} if edit else {}
        return {"slot": state.current_slot, "name": stored.name if stored else "",
                "unsaved_changes": state.dirty, "modules": modules, "expression_pedal": exp}

    def list_presets(self, query: str = "") -> dict[str, Any]:
        needle = query.strip().lower()
        presets = [{"slot": slot, "name": preset.name}
                   for slot, preset in sorted(self.editor.state.presets.items())
                   if needle in preset.name.lower()]
        return {"current": self.editor.state.current_slot, "presets": presets}

    def get_preset(self, slot: int) -> dict[str, Any]:
        preset = self.editor.state.presets.get(slot)
        if preset is None:
            raise ToolError(f"preset {slot} not loaded (1..{PRESET_COUNT})")
        modules = []
        for index in range(len(MODULES)):
            block = preset.module(index)
            model = self.editor.catalog[index].model_for_type(block.type)
            modules.append({"module": MODULE_TAGS[index], "on": block.switch,
                            "model": self.editor.model_display(index, block.type),
                            "params": {p.name: p.format(block.words[p.word])
                                       for p in (model.params if model else ())}})
        return {"slot": slot, "name": preset.name, "modules": modules}

    def get_presets(self, query: str) -> dict[str, Any]:
        """Every stored preset whose name contains ``query``, decoded (one call)."""
        found = self.list_presets(query)["presets"]
        if not found:
            raise ToolError(f"no preset name contains {query!r}")
        return {"presets": [self.get_preset(p["slot"]) for p in found[:40]],
                **({"note": f"first 40 of {len(found)}"} if len(found) > 40 else {})}

    def list_user_irs(self) -> dict[str, Any]:
        from ge150.services.tone3000 import IrSources

        names = self.editor.state.user_names
        if names is None:
            return {"ir": []}
        # The NR (AMP 56-65) slots are left out: hidden like in the official editor.
        sources, users = IrSources().matching(names.ir), self._ir_users()
        return {"ir": [_ir_entry(i, n, sources, users) for i, n in enumerate(names.ir)]}

    def _ir_users(self) -> dict[int, list[int]]:
        """User IR slot (1..10) -> the stored presets whose CAB uses it."""
        cab = MODULE_TAGS.index("CAB")
        users: dict[int, list[int]] = {}
        for model in self.editor.catalog[cab].models:
            if model.user_slot and (slots := self.editor.presets_using(cab, model.number - 1)):
                users[model.number - 26] = slots
        return users

    def get_guitar(self) -> dict[str, Any]:
        from dataclasses import asdict

        from ge150.services.guitars import GuitarStore

        guitar = GuitarStore().current
        if guitar is None:
            return {"guitar": None, "note": "the user has not chosen a guitar in the editor"}
        data = {k: v for k, v in asdict(guitar).items() if v and k not in ("id", "serial")}
        return {"guitar": data}

    def get_settings(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        system, rhythm = self.editor.system_values(), self.editor.rhythm_values()
        if system is not None:
            result["system"] = dict(system.describe())
        if rhythm is not None:
            result["rhythm"] = dict(rhythm.describe())
        return result

    # --- live changes -----------------------------------------------------------------

    def set_module(self, module: str, on: bool | None = None, model: str = "",
                   params: Mapping[str, str | float] | None = None) -> dict[str, Any]:
        index = _module_index(module)
        current = self.editor.module(index)
        entry = {"module": MODULE_TAGS[index],
                 "on": current.switch if on is None else on, "model": model,
                 "params": [{"name": k, "value": str(v)} for k, v in (params or {}).items()]}
        return self.apply_patch([entry])

    def apply_patch(self, modules: list[dict[str, Any]], name: str = "") -> dict[str, Any]:
        def run() -> dict[str, Any]:
            proposal = tone_ai.parse_proposal(
                {"name": name, "explanation": "", "modules": modules}, self.editor)
            edit = self.editor.state.edit
            if edit is not None:
                self.history.append(list(edit.words))
            tone_ai.apply_proposal(self.editor, proposal)
            return {"applied": proposal.describe(self.editor.catalog),
                    "warnings": proposal.warnings,
                    "note": "live only, not saved yet"}

        try:
            return self._locked(run)
        except tone_ai.ToneAIError as exc:
            raise ToolError(str(exc)) from exc

    def undo(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if not self.history:
                return {"undone": False, "reason": "nothing to undo"}
            tone_ai.restore_words(self.editor, self.history.pop())
            return {"undone": True, "remaining": len(self.history)}

        return self._locked(run)

    def select_preset(self, slot: int, discard_changes: bool = False) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if not 1 <= slot <= PRESET_COUNT:
                raise ToolError(f"slot must be 1..{PRESET_COUNT}")
            if self.editor.state.dirty and not discard_changes:
                raise ToolError("the current preset has unsaved changes: ask the user whether "
                                "to discard them, then call again with discard_changes=true")
            self.editor.select(slot)
            self.history.clear()
            return self._patch()

        return self._locked(run)

    # --- writing the pedal's memory (confirmed by the user) ---------------------------------

    def save_preset(self, slot: int = 0, name: str = "") -> dict[str, Any]:
        state = self.editor.state
        target = slot or state.current_slot or 0
        if not 1 <= target <= PRESET_COUNT:
            raise ToolError(f"slot must be 1..{PRESET_COUNT}")
        old = state.presets.get(target)
        label = name or (old.name if old else "")
        self._ask(tr(
            "¿Guardar el sonido actual en el preset {slot} «{name}»?\nReemplaza «{previous}».",
            slot=target, name=label, previous=old.name if old else "?",
        ))

        def run() -> dict[str, Any]:
            try:
                preset = self.editor.save(target, name or None)
            except Exception as exc:  # EditorError: invalid name...
                raise ToolError(str(exc)) from exc
            self.history.clear()
            return {"saved": True, "slot": target, "name": preset.name}

        return self._locked(run)

    def propose_preset_edits(self, edits: list[dict[str, Any]],
                             summary: str = "") -> dict[str, Any]:
        """Plan changes to stored presets; the user reviews and applies them in the editor."""
        from ge150.services import bulk_edit

        if self.on_proposal is None:
            raise ToolError("changing stored presets needs the GE150 editor open (the user "
                            "reviews the changes there); ask the user to open it")
        planned = self._locked(lambda: bulk_edit.plan_edits(self.editor, edits))
        if not any(e.changed and not e.blocked for e in planned):
            return {"proposed": False, "presets": bulk_edit.describe(self.editor, planned),
                    "reason": "nothing would change"}
        self.on_proposal(planned, summary)
        return {"proposed": True, "presets": bulk_edit.describe(self.editor, planned),
                "note": "NOT written yet: the user reviews them in the Asistente tab and "
                        "applies (or discards) them"}

    # --- TONE3000 cabinet IRs ---------------------------------------------------------

    @property
    def tone3000(self) -> Any:
        if self._tone3000 is None:
            from ge150.services.tone3000 import Tone3000Client

            self._tone3000 = Tone3000Client()
        return self._tone3000

    def _t3k(self, fn: Callable[[], T]) -> T:
        from ge150.services.tone3000 import Tone3000Error

        client = self.tone3000
        store = getattr(client, "store", None)
        if store is not None:  # the editor may have signed in (or out) since the last call
            client.creds = store.load()
        if not client.creds.signed_in:
            raise ToolError("not signed in to TONE3000: ask the user to press «Conectar "
                            "cuenta» in the TONE3000 tab of the GE150 editor (or to run "
                            "'ge150ctl t3k-login')")
        try:
            return fn()
        except Tone3000Error as exc:
            raise ToolError(str(exc)) from exc

    def tone3000_search(self, query: str) -> dict[str, Any]:
        tones = self._t3k(lambda: self.tone3000.search(query))
        return {"note": "TONE3000 limits searches: search few times, with precise words",
                "tones": [{"tone_id": t.id, "title": t.title, "author": t.author,
                           "license": t.license, "gear": t.gear, "url": t.page_url,
                           "description": t.description[:200]} for t in tones]}

    def tone3000_get(self, tone_id: int) -> dict[str, Any]:
        tone = self._t3k(lambda: self.tone3000.tone(tone_id))
        models = self._t3k(lambda: self.tone3000.models(tone_id))
        return {"tone_id": tone.id, "title": tone.title, "author": tone.author,
                "license": tone.license, "format": tone.format, "url": tone.page_url,
                "description": tone.description[:500],
                "files": [{"model_id": m.id, "name": m.name} for m in models]}

    def load_tone3000_ir(self, slot: int = 0, tone_id: int = 0, model_id: int = 0,
                         name: str = "", use: bool = True) -> dict[str, Any]:
        """Download a TONE3000 IR (or let the user pick one in the browser when
        ``tone_id`` is 0), store it in user IR slot ``slot`` (1..10; 0 = a free one, or the
        one already holding this IR) and, with ``use``, select it in the CAB module."""
        from ge150.formats import wav_ir
        from ge150.protocol import messages as msg
        from ge150.protocol.opcodes import Op
        from ge150.services.tone3000 import IrSources

        if not 0 <= slot <= 10:
            raise ToolError("IR slot must be 1..10 (models CAB 27..36), or 0 for a free one")
        chosen = tone_id or self._t3k(self.tone3000.select_tone)
        download = self._t3k(lambda: self.tone3000.download(chosen, model_id or None))
        tone = download.tone
        try:
            payload = wav_ir.load_ir_file(download.path)
        except ValueError as exc:
            raise ToolError(f"cannot use this IR: {exc}") from exc
        label = (name or tone.title)[: msg.USER_NAME_LEN]
        names = self.editor.state.user_names
        bank = list(names.ir) if names else [""] * 10
        sources = IrSources().matching(bank)
        what = tr(
            "la IR de TONE3000 «{title} / {model}»\nde {author} (licencia {license})",
            title=tone.title, model=download.model.name, author=tone.author, license=tone.license,
        )
        result = {"slot": 0, "model": "", "name": label, "tone": tone.title,
                  "file": download.model.name, "author": tone.author,
                  "license": tone.license, "url": tone.page_url}
        if not slot and (loaded := _slot_holding(sources, tone.id, download.model.name)):
            result.update(stored=False, slot=loaded, name=bank[loaded - 1],
                          note="this IR was already on the pedal")
        else:
            if not slot:
                slot = next((i + 1 for i, n in enumerate(bank) if msg.is_empty_slot_name(n)), 0)
            if slot:
                old = bank[slot - 1]
                replaces = ("" if msg.is_empty_slot_name(old)
                            else tr("\nReemplaza «{name}».", name=old))
                self._ask(tr(
                    "¿Guardar {ir} en la ranura {slot} (CAB {cab}) como «{name}»?{replaces}",
                    ir=what, slot=slot, cab=26 + slot, name=label, replaces=replaces,
                ))
            else:
                question = tr(
                    "Las 10 ranuras de IR están ocupadas. ¿Cuál reemplazo por "
                    "«{title} / {model}» de {author} (licencia {license})?",
                    title=tone.title, model=download.model.name,
                    author=tone.author, license=tone.license,
                )
                slot = self._pick_slot(question, [_ir_entry(i, n, sources, self._ir_users())
                                                  for i, n in enumerate(bank)])

            def store() -> None:
                self.editor.upload_user_slot(Op.USER_IR, slot - 1, label, payload)

            self._locked(store)
            IrSources().record(slot, download, label)
            result.update(stored=True, slot=slot)
        result["model"] = f"CAB {26 + result['slot']}"
        if use:
            self.apply_patch([{"module": "CAB", "on": True, "model": result["model"]}])
            result["selected"] = True
            result["note"] = (f"{result.get('note', 'stored')}; selected in the CAB module "
                              "(live; undo reverts the selection, not the stored IR)")
        return result

    def _pick_slot(self, question: str, slots: list[dict[str, Any]]) -> int:
        """Ask the user which user IR slot to overwrite (1..10)."""
        if self.pick_ir_slot is None:
            raise ToolError("all 10 user IR slots are in use: show the user list_user_irs, ask "
                            "which one to overwrite and call again with that slot")
        picked = self.pick_ir_slot(question, slots)
        if not picked:
            raise ToolError("the user declined")
        return int(picked)


def _slot_holding(sources: dict[str, dict[str, Any]], tone_id: int, file: str) -> int:
    """The user IR slot that already holds this TONE3000 file (0 = none)."""
    for slot, source in sources.items():
        if source.get("tone_id") == tone_id and source.get("model") == file:
            return int(slot)
    return 0


def _ir_entry(index: int, name: str, sources: dict[str, dict[str, Any]],
              users: dict[int, list[int]]) -> dict[str, Any]:
    entry: dict[str, Any] = {"slot": index + 1, "model": f"CAB {27 + index}", "name": name,
                             "used_by_presets": users.get(index + 1, [])}
    source = sources.get(str(index + 1))
    if source:
        entry["tone3000"] = {k: source.get(k) for k in
                             ("title", "model", "author", "license", "url")}
        entry["tone3000"]["description"] = str(source.get("description") or "")[:300]
    return entry


def _param_info(param: Any) -> dict[str, Any]:
    if param.kind == "enum":
        return {"name": param.name, "options": list(param.options)}
    if param.kind == "number":
        info: dict[str, Any] = {"name": param.name, "min": param.min, "max": param.max}
        if param.step != 1:
            info["step"] = param.step
        if param.unit:
            info["unit"] = param.unit
        return info
    return {"name": param.name}


#: Permissions (``services/ai_permissions.py``) each tool needs; reading needs none.
TOOL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "set_module": ("live",), "apply_patch": ("live",), "undo": ("live",),
    "select_preset": ("live",), "save_preset": ("save",),
    "tone3000_search": ("tone3000",), "tone3000_get": ("tone3000",),
    "load_tone3000_ir": ("tone3000", "load_ir")}


def required_permissions(tool: str, edits: Any = None) -> list[str]:
    """What a call needs; ``propose_preset_edits`` depends on what its ``edits`` change."""
    keys = list(TOOL_PERMISSIONS.get(tool, ()))
    if tool == "propose_preset_edits":
        for edit in edits if isinstance(edits, list) else []:
            if not isinstance(edit, Mapping):
                continue
            for field, key in (("modules", "edit_presets"), ("name", "rename"),
                               ("copy_from", "move")):
                if edit.get(field) and key not in keys:
                    keys.append(key)
    return keys


class GuardedTools:
    """The tools as the AI sees them: every call first checks the user's permissions
    (read again each time, so a change applies at once). The GUI's own buttons use the
    plain :class:`PedalTools`."""

    def __init__(self, tools: Any,
                 permissions: Callable[[], ai_permissions.Permissions] = ai_permissions.load
                 ) -> None:
        self.tools = tools
        self.permissions = permissions

    def check(self, tool: str, edits: Any = None) -> None:
        denied = self.permissions().missing(required_permissions(tool, edits))
        if denied:
            names = ", ".join(f"«{d}»" for d in denied)
            raise ToolError(f"the user turned off {names} in the assistant's permissions "
                            "(gear button of the chat): do not try it again; tell the user "
                            "it is off and that they can turn it on there")

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.tools, name)
        if name.startswith("_") or not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            self.check(name, kwargs.get("edits", args[0] if args else None))
            return target(*args, **kwargs)

        return call

"""The real gear behind every model (``data/gear.json``) and where its photo is.

No photos are shipped: the GUI draws every unit (``ui/gear_art.py``, ``ui/amp_art.py``). A photo
of the user's own in ``$XDG_DATA_HOME/ge150/photos`` (``~/.local/share/ge150/photos``) is shown
instead of the drawing.
File name: ``<gear id>.jpg`` / ``.png`` / ``.webp``. Without a photo the GUI draws the gear.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

from ge150.i18n import tr

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
#: module index (preset layout order) -> tag used by gear.json / effects.json
MODULE_TAGS = ("FX", "DS", "AMP", "CAB", "NS", "EQ", "MOD", "DELAY", "REVERB")


@dataclass(frozen=True)
class Gear:
    gid: str
    name: str
    kind: str
    body: str
    text: str = "#f2f2f2"
    knob: str = "#151515"
    panel: str = ""
    grille: str = ""
    speakers: str = ""
    slant: bool = False
    diamond: bool = False
    stripes: bool = False
    reference: str = ""  # classic unit shown in the photo when the model has no real origin
    generic: bool = False  # Mooer algorithm not based on a named unit
    # How the drawing looks when there is no photo (ui/gear_art.py, ui/amp_art.py):
    logo_text: str = ""  # brand on amps and cabinets ("Marshall", "ENGL", ...)
    logo: str = ""  # block, script, oval, rounded, lower, badge, z
    logo_colour: str = ""  # a colour, "chrome" or "gold"
    logo_pos: str = ""  # topleft, top, center, bottomright
    grille_style: str = ""  # cloth, basket, mesh, bars, sparkle, wicker
    piping: str = ""  # welt around the grille: a colour, "chrome" or "gold"
    knob_style: str = ""  # amps: skirt, chrome, gold, chicken; pedals: chrome
    corners: str = ""  # "chrome" metal corners (default: black)
    big_knob: str = ""  # Mooer micro: colour of the big centre knob
    accent: str = ""  # pedals: colour of the band behind the name
    shape: str = ""  # stomp: "slope" (Marshall-style slanted top)
    panel_side: str = ""  # combos: "right" (Tone King Falcon); heads: "top" (Mesa Mark V)
    eq_sliders: int = 0  # graphic EQ sliders on the panel (Mesa Mark)
    art: dict[str, object] = field(default_factory=dict)  # per-unit recipe (ui/art_spec.py)
    extra: dict[str, object] = field(default_factory=dict)


FALLBACK = Gear("generic", "", "micro", "#3a3f47")


def describe(gear: Gear, has_photo: bool) -> str:
    """Caption under the picture: what the model is based on and what the photo shows."""
    if not gear.name:
        return ""
    text = gear.name if gear.generic else tr("Basado en {name}", name=gear.name)
    if gear.reference and has_photo:
        text += tr("  ·  foto de referencia: {name}", name=gear.reference)
    return text


def user_photo_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ge150" / "photos"


@cache
def _table() -> tuple[dict[str, Gear], dict[str, dict[str, str]]]:
    raw = json.loads(resources.files("ge150.data").joinpath("gear.json").read_text())
    known = set(Gear.__dataclass_fields__) - {"gid", "extra"}
    gear: dict[str, Gear] = {}
    for gid, entry in raw["gear"].items():
        fields = {k: v for k, v in entry.items() if k in known}
        rest = {k: v for k, v in entry.items() if k not in known}
        gear[gid] = Gear(gid, **fields, extra=rest)
    return gear, raw["models"]


def gear_for(module_index: int, model_number: int) -> Gear:
    """Gear of a model (``model_number`` as shown by the pedal, 1-based)."""
    gear, models = _table()
    if not 0 <= module_index < len(MODULE_TAGS):
        return FALLBACK
    gid = models.get(MODULE_TAGS[module_index], {}).get(str(model_number))
    return gear.get(gid or "", FALLBACK)


def all_gear() -> dict[str, Gear]:
    return dict(_table()[0])


def photo_path(gid: str, extra_dirs: tuple[Path, ...] = ()) -> Path | None:
    """The user's own photo for ``gid`` (extra dirs first, then the user folder), if any."""
    for folder in (*extra_dirs, user_photo_dir()):
        for suffix in PHOTO_SUFFIXES:
            candidate = folder / f"{gid}{suffix}"
            if candidate.is_file():
                return candidate
    return None

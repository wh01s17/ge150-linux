"""Translation catalogs for the graphical editor.

Spanish is the source language. Each JSON catalog maps source strings to translated
strings, and an absent entry falls back to the source text.
"""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable
from typing import cast

DEFAULT_LANGUAGE = "es"
_catalog: dict[str, str] = {}


def _catalog_dir() -> Traversable:
    return resources.files("ge150").joinpath("data", "translations")


def languages() -> tuple[str, ...]:
    """Return the source language and the languages shipped with the editor."""
    directory = _catalog_dir()
    if not directory.is_dir():
        return (DEFAULT_LANGUAGE,)
    catalogs = sorted(
        entry.name.removesuffix(".json") for entry in directory.iterdir()
        if entry.is_file() and entry.name.endswith(".json")
        and entry.name != f"{DEFAULT_LANGUAGE}.json"
    )
    return DEFAULT_LANGUAGE, *catalogs


def set_language(language: str) -> None:
    """Load the selected catalog before creating the editor's widgets."""
    global _catalog

    if language not in languages():
        raise ValueError(f"Unsupported editor language: {language}")
    if language == DEFAULT_LANGUAGE:
        _catalog = {}
    else:
        source = _catalog_dir().joinpath(f"{language}.json").read_text(encoding="utf-8")
        _catalog = cast(dict[str, str], json.loads(source))


def tr(message: str, /, **values: object) -> str:
    translated = _catalog.get(message, message)
    return translated.format(**values) if values else translated

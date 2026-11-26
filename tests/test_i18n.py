import json
from pathlib import Path

import pytest

from ge150 import i18n


def test_default_language_preserves_source_text() -> None:
    i18n.set_language("es")
    assert i18n.tr("Guardar") == "Guardar"
    assert i18n.tr("Preset {slot:03d}", slot=7) == "Preset 007"
    assert i18n.tr("Texto con {llaves}") == "Texto con {llaves}"


def test_catalog_is_selected_and_missing_messages_fall_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    translation = {"Preset {slot:03d} guardado": "Gespeichert: Preset {slot:03d}"}
    (tmp_path / "de.json").write_text(json.dumps(translation), encoding="utf-8")
    monkeypatch.setattr(i18n, "_catalog_dir", lambda: tmp_path)
    assert i18n.languages() == ("es", "de")
    try:
        i18n.set_language("de")
        assert i18n.tr("Preset {slot:03d} guardado", slot=7) == "Gespeichert: Preset 007"
        assert i18n.tr("Guardar") == "Guardar"
        with pytest.raises(ValueError, match="Unsupported editor language"):
            i18n.set_language("fr")
    finally:
        i18n.set_language("es")

"""Шрифты листов: в matplotlib уходят только установленные (cormap/fonts.py)."""

from __future__ import annotations

import logging

from cormap.build import build_map
from cormap.fonts import installed_families
from cormap.params import BuildParams
from tests.synth import make_workbook

MISSING = "Нет Такого Шрифта 123"


def test_missing_families_are_dropped_and_order_kept():
    assert installed_families((MISSING, "DejaVu Serif", "DejaVu Sans")) == (
        "DejaVu Serif",
        "DejaVu Sans",
    )
    assert installed_families((MISSING,)) == ("serif",)


def test_build_does_not_warn_about_missing_font(tmp_path, monkeypatch, caplog):
    """На сервере нет Times New Roman: построение не должно сыпать findfont в журнал."""
    import importlib
    from dataclasses import replace

    from cormap import layout_settings, legend

    families = (MISSING, "DejaVu Serif")
    patched = replace(layout_settings.load_settings(), font_families=families)
    for name in ("layout_settings", "render_pdf", "render_xlsx", "layout", "build", "serialize"):
        module = importlib.import_module(f"cormap.{name}")
        if hasattr(module, "load_settings"):
            monkeypatch.setattr(module, "load_settings", lambda *a, **k: patched)
    monkeypatch.setattr(legend, "FONT_FAMILY", list(families))
    book = make_workbook(tmp_path / "book.xlsx", length_m=2000, step_m=10)
    with caplog.at_level(logging.WARNING, logger="matplotlib"):
        build_map(book, BuildParams(object_name="Шрифты"), tmp_path / "out")
    assert (tmp_path / "out" / "karta.pdf").is_file()
    assert not [r for r in caplog.records if "findfont" in r.getMessage()]

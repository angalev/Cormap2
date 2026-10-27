"""Этап 4: значки, текстуры, лист условных обозначений."""

from __future__ import annotations

import tomllib
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from cormap import assets
from cormap.legend import legend_entries, render_legend
from cormap.model import INSULATION_TYPES, SOIL_TYPES
from cormap.notes import OBJECT_TYPES

KEYS = [t.key for t in OBJECT_TYPES]
MIN_DPI = 600


def test_files_per_type_and_no_extras():
    svg = {p.stem for p in assets.ICONS_SVG.glob("*.svg")}
    png = {p.stem for p in assets.ICONS_PNG.glob("*.png")}
    with assets.ICONS_TOML.open("rb") as fh:
        cfg = set(tomllib.load(fh))
    assert svg == png == cfg == set(KEYS)


@pytest.mark.parametrize("key", KEYS)
def test_png_transparent_and_resolution(key):
    w_mm, h_mm = assets.icon_size_mm(key)
    im = Image.open(assets.icon_png(key))
    assert im.mode == "RGBA"
    alpha = np.asarray(im)[..., 3]
    assert alpha.min() == 0, "нет прозрачного фона"
    assert alpha.max() == 255
    w_px, h_px = im.size
    assert abs(w_px - w_mm / 25.4 * MIN_DPI) <= 1
    assert abs(h_px - h_mm / 25.4 * MIN_DPI) <= 1
    assert w_px / (w_mm / 25.4) >= MIN_DPI - 1


@pytest.mark.parametrize("key", KEYS)
def test_svg_is_plain_xml_without_images_or_links(key):
    path = assets.icon_svg(key)
    root = ET.parse(path).getroot()
    vb = [float(v) for v in root.attrib["viewBox"].split()]
    w_mm, h_mm = assets.icon_size_mm(key)
    assert vb[:2] == [0, 0]
    assert (vb[2], vb[3]) == (w_mm, h_mm)
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        assert tag not in {"image", "use", "script", "foreignObject", "style"}
        for name, value in el.attrib.items():
            assert "href" not in name
            assert "url(" not in value and "http" not in value
    assert "data:" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", KEYS)
def test_anchor_in_unit_square(key):
    ax, ay = assets.icon_anchor(key)
    assert 0 <= ax <= 1 and 0 <= ay <= 1


def _tiles():
    out = [assets.soil_texture(n) for n in SOIL_TYPES]
    out += [assets.insulation_texture(n) for n in INSULATION_TYPES]
    out += [assets.soil_texture("нет такого"), assets.insulation_texture("нет такого")]
    return out


def test_texture_lookup_and_unknown():
    assert assets.soil_texture("Песчаный").name == "peschanyi.png"
    assert assets.soil_texture("  ТОРФ ").name == "torf.png"
    assert assets.soil_texture("что-то").name == "unknown.png"
    assert assets.soil_texture(None).name == "unknown.png"
    assert assets.insulation_texture("Битумное").name == "bitumnoe.png"
    assert assets.insulation_texture("неизвестно").name == "unknown.png"
    assert assets.soil_title("суглинок") == "Суглинок"
    assert assets.insulation_title("пленочное полимерное") == "Пленочное полимерное"
    assert assets.soil_title("") == "Не указан"


def test_all_model_names_have_textures():
    for n in SOIL_TYPES:
        assert assets.soil_texture(n).name != "unknown.png"
    for n in INSULATION_TYPES:
        assert assets.insulation_texture(n).name != "unknown.png"


@pytest.mark.parametrize("path", _tiles(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_textures_exist_and_seamless(path):
    assert path.is_file()
    arr = np.asarray(Image.open(path).convert("L"), dtype=np.float64)
    h, w = arr.shape
    assert min(h, w) >= 256
    # шов (последний-первый столбец/строка) не заметнее обычной разницы соседних
    inner_x = np.abs(np.diff(arr, axis=1)).mean()
    inner_y = np.abs(np.diff(arr, axis=0)).mean()
    seam_x = np.abs(arr[:, -1] - arr[:, 0]).mean()
    seam_y = np.abs(arr[-1, :] - arr[0, :]).mean()
    assert seam_x <= 1.5 * inner_x + 1.0
    assert seam_y <= 1.5 * inner_y + 1.0


def test_legend_all_and_used(tmp_path):
    full = legend_entries()
    assert len(full.main) == 19  # трубопровод + 18 значков новой легенды
    assert len(full.soil) == 6 and len(full.insulation) == 4
    assert len(full.legacy) == len(OBJECT_TYPES) - 18

    fig = render_legend()
    for name in ("all.pdf", "all.png"):
        fig.savefig(tmp_path / name, dpi=100)
        assert (tmp_path / name).stat().st_size > 1000

    used = ["kip", "road", "casing"]
    part = legend_entries(used, ["торф"], ["битумное"])
    assert len(part) < len(full)
    assert [e.key for e in part.main if e.kind == "icon"] == ["road", "kip"]
    assert [e.key for e in part.legacy] == ["casing"]
    assert len(part.soil) == 1 and len(part.insulation) == 1
    fig2 = render_legend(used, ["торф", "что-то"], [], paper="A3")
    for name in ("part.pdf", "part.png"):
        fig2.savefig(tmp_path / name, dpi=100)
        assert (tmp_path / name).stat().st_size > 1000


def test_legend_order_follows_sample():
    titles = [e.title for e in legend_entries().main]
    assert titles[:3] == ["Диагностируемый трубопровод", "Высоковольтная линия", "Кабельная линия"]
    assert titles[-1] == "Повреждение ИП"


def test_legend_bad_input():
    with pytest.raises(KeyError):
        render_legend(["нет такого типа"])
    with pytest.raises(ValueError):
        render_legend(paper="A0")

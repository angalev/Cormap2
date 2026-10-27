"""Доступ к ресурсам: значки, текстуры грунта и штриховки изоляции (без Django).

PNG и текстуры собраны заранее скриптом `tools/build_assets.py`; растеризатор SVG
для работы не нужен.
"""

from __future__ import annotations

import math
import tomllib
from functools import lru_cache
from pathlib import Path

from cormap.model import normalize_text

ROOT = Path(__file__).resolve().parent
ICONS_SVG = ROOT / "icons" / "svg"
ICONS_PNG = ROOT / "icons" / "png"
SOIL_DIR = ROOT / "textures" / "soil"
INSULATION_DIR = ROOT / "textures" / "insulation"
ICONS_TOML = ROOT / "settings" / "icons.toml"

UNKNOWN = "unknown"

# Нормализованное русское название -> (имя файла, подпись для листа условных обозначений).
# Ключи совпадают с model.SOIL_TYPES и model.INSULATION_TYPES.
SOIL: dict[str, tuple[str, str]] = {
    "песчаный": ("peschanyi", "Песчаный"),
    "скальный": ("skalnyi", "Скальный"),
    "суглинок": ("suglinok", "Суглинок"),
    "супесь": ("supes", "Супесь"),
    "торф": ("torf", "Торф"),
    "илистый": ("ilistyi", "Илистый"),
}
INSULATION: dict[str, tuple[str, str]] = {
    "битумное": ("bitumnoe", "Битумное"),
    "заводское полиэтиленовое": ("zavodskoe_pe", "Заводское полиэтиленовое"),
    "лакокрасочное": ("lakokrasochnoe", "Лакокрасочное"),
    "пленочное полимерное": ("plenochnoe", "Пленочное полимерное"),
}


@lru_cache(maxsize=1)
def _settings() -> dict[str, dict]:
    with ICONS_TOML.open("rb") as fh:
        return tomllib.load(fh)


def _entry(type_key: str) -> dict:
    try:
        return _settings()[type_key]
    except KeyError:
        raise KeyError(f"нет настроек значка для типа «{type_key}»") from None


def icon_svg(type_key: str) -> Path:
    """Путь к SVG значка типа."""
    _entry(type_key)  # проверка, что тип известен
    return ICONS_SVG / f"{type_key}.svg"


def icon_png(type_key: str) -> Path:
    """Путь к PNG значка (не менее 600 dpi в размере на бумаге)."""
    _entry(type_key)
    return ICONS_PNG / f"{type_key}.png"


def icon_size_mm(type_key: str) -> tuple[float, float]:
    """Размер значка на бумаге, мм: (ширина, высота)."""
    e = _entry(type_key)
    return float(e["width_mm"]), float(e["height_mm"])


def min_width_mm(type_key: str) -> float | None:
    """Наименьшая ширина протяжённого объекта на бумаге, мм; None - ключа нет.

    Если ключа `min_width_mm` у типа нет, действует общий минимум из layout.toml.
    """
    e = _entry(type_key)
    if "min_width_mm" not in e:
        return None
    v = e["min_width_mm"]
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0:
        raise ValueError(
            f"icons.toml, [{type_key}]: min_width_mm должно быть положительным числом, "
            f"получено {v!r}."
        )
    return float(v)


def icon_anchor(type_key: str) -> tuple[float, float]:
    """Точка привязки в долях размера от левого верхнего угла (y вниз)."""
    ax, ay = _entry(type_key)["anchor"]
    return float(ax), float(ay)


def icon_keys() -> tuple[str, ...]:
    """Ключи типов, для которых заданы значки."""
    return tuple(_settings())


def _lookup(table: dict[str, tuple[str, str]], name: object) -> tuple[str, str] | None:
    return table.get(normalize_text(name)) if name is not None else None


def soil_texture(name: object) -> Path:
    """Плитка грунта по названию; неизвестное название - серая штриховка."""
    hit = _lookup(SOIL, name)
    return SOIL_DIR / f"{hit[0] if hit else UNKNOWN}.png"


def insulation_texture(name: object) -> Path:
    """Штриховка покрытия по названию; неизвестное название - серая штриховка."""
    hit = _lookup(INSULATION, name)
    return INSULATION_DIR / f"{hit[0] if hit else UNKNOWN}.png"


def _title(table: dict[str, tuple[str, str]], name: object) -> str:
    hit = _lookup(table, name)
    if hit:
        return hit[1]
    text = "" if name is None else str(name).strip()
    return text[:1].upper() + text[1:] if text else "Не указан"


def soil_title(name: object) -> str:
    """Подпись типа грунта для легенды (для неизвестного - название как есть)."""
    return _title(SOIL, name)


def insulation_title(name: object) -> str:
    """Подпись типа покрытия для легенды (для неизвестного - название как есть)."""
    return _title(INSULATION, name)

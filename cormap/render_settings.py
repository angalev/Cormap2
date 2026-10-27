"""Настройки отрисовки (`assets/settings/render.toml`) со строгой проверкой.

Модуль не зависит от Django и matplotlib.pyplot. Значения читаются как атрибуты:
``settings.sizes.title_pt``, ``settings.colors.u_on``.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from matplotlib.colors import is_color_like

from cormap.assets import ROOT

RENDER_TOML = ROOT / "settings" / "render.toml"


class RenderSettingsError(ValueError):
    """Ошибка в файле render.toml."""


NUM, COLOR = "num", "color"

_SCHEMA: dict[str, dict[str, str]] = {
    "sizes": dict.fromkeys(
        (
            "title_pt",
            "strip_label_pt",
            "tick_pt",
            "caption_pt",
            "legend_pt",
            "no_data_pt",
            "footer_pt",
        ),
        NUM,
    ),
    "geometry": dict.fromkeys(
        (
            "title_height_mm",
            "footer_mm",
            "axes_top_pad_mm",
            "tick_row_mm",
            "legend_box_mm",
            "legend_pad_mm",
            "legend_line_mm",
            "legend_gap_mm",
            "top_axis_header_mm",
            "ribbon_pad_mm",
            "objects_pad_mm",
            "caption_right_mm",
        ),
        NUM,
    ),
    "sheet1": dict.fromkeys(
        ("potential", "gradient_left", "gradient_right", "defects", "zones"), NUM
    ),
    "sheet2": dict.fromkeys(("insulation", "depth", "resistivity", "soil"), NUM),
    "axes": dict.fromkeys(
        (
            "potential_top",
            "potential_bottom",
            "potential_step",
            "gradient_top",
            "gradient_step",
            "defects_top",
            "depth_step",
            "max_ticks",
        ),
        NUM,
    ),
    "tiles": dict.fromkeys(("tile_mm", "texture_dpi"), NUM),
    "lines": dict.fromkeys(
        (
            "frame",
            "cell",
            "axis",
            "grid",
            "potential",
            "norm",
            "gradient",
            "depth",
            "depth_norm",
            "resistivity",
            "defects",
            "zones",
            "pipe",
            "cross",
            "leader",
            "water_shore",
            "water_wave",
            "open_frame",
            "open_hatch",
            "air_ground",
            "air_post",
        ),
        NUM,
    ),
    "extended": dict.fromkeys(
        (
            "wave_amp_ratio",
            "wave_amp_min_mm",
            "wave_amp_max_mm",
            "wave_periods",
            "water_wave_step_mm",
            "water_wave_alpha",
            "open_dash_on_pt",
            "open_dash_off_pt",
            "open_hatch_pitch_mm",
            "open_hatch_run_mm",
            "air_depth_ratio",
            "air_post_half_ratio",
            "air_post_half_min_mm",
            "air_post_half_max_mm",
            "air_post_top_mm",
            "air_post_foot_height_mm",
            "air_post_foot_lift_mm",
        ),
        NUM,
    ),
    "colors": dict.fromkeys(
        (
            "frame",
            "axis",
            "grid",
            "u_on",
            "u_off",
            "u_max",
            "u_min",
            "gradient_left_on",
            "gradient_left_off",
            "gradient_right_on",
            "gradient_right_off",
            "defects",
            "defects_fill",
            "depth",
            "depth_norm",
            "resistivity",
            "res_high",
            "res_mid",
            "res_low",
            "pipe",
            "pipe_light",
            "sky_top",
            "sky_bottom",
            "ground_top",
            "ground_bottom",
            "road",
            "road_mark",
            "cross",
            "leader",
            "water_shore",
            "water_fill",
            "open_frame",
            "open_fill",
            "air_ground",
            "air_post",
            "air_void",
        ),
        COLOR,
    ),
}
# Значения, которые могут быть отрицательными.
_SIGNED = {("axes", "potential_top"), ("axes", "potential_bottom")}


class Section:
    """Раздел настроек: значения доступны как атрибуты."""

    def __init__(self, name: str, values: dict[str, float | str]) -> None:
        self._name = name
        self._values = values

    def __getattr__(self, key: str):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self._values[key]
        except KeyError:
            raise AttributeError(f"render.toml, [{self._name}]: нет ключа {key}") from None

    def total(self) -> float:
        """Сумма числовых значений раздела (для проверки долей)."""
        return sum(v for v in self._values.values() if isinstance(v, float))

    def __repr__(self) -> str:
        return f"Section({self._name!r}, {self._values!r})"


class RenderSettings:
    """Все разделы render.toml."""

    sizes: Section
    geometry: Section
    sheet1: Section
    sheet2: Section
    axes: Section
    tiles: Section
    lines: Section
    extended: Section
    colors: Section

    def __init__(self, sections: dict[str, Section]) -> None:
        for name, sec in sections.items():
            setattr(self, name, sec)


def parse_render_settings(data: dict) -> RenderSettings:
    """Строгая проверка словаря настроек (результат tomllib)."""
    extra = set(data) - set(_SCHEMA)
    if extra:
        raise RenderSettingsError(f"render.toml: лишние разделы {sorted(extra)}.")
    sections: dict[str, Section] = {}
    for name, keys in _SCHEMA.items():
        sec = data.get(name)
        if not isinstance(sec, dict):
            raise RenderSettingsError(f"render.toml: нет раздела [{name}].")
        extra_keys = set(sec) - set(keys)
        missing = set(keys) - set(sec)
        if extra_keys:
            raise RenderSettingsError(f"render.toml, [{name}]: лишние ключи {sorted(extra_keys)}.")
        if missing:
            raise RenderSettingsError(f"render.toml, [{name}]: нет ключей {sorted(missing)}.")
        values: dict[str, float | str] = {}
        for key, kind in keys.items():
            v = sec[key]
            if kind == NUM:
                if isinstance(v, bool) or not isinstance(v, int | float):
                    raise RenderSettingsError(f"render.toml, [{name}] {key}: нужно число.")
                if v <= 0 and (name, key) not in _SIGNED:
                    raise RenderSettingsError(
                        f"render.toml, [{name}] {key}: значение должно быть > 0."
                    )
                values[key] = float(v)
            else:
                if not isinstance(v, str) or not is_color_like(v):
                    raise RenderSettingsError(f"render.toml, [{name}] {key}: нужен цвет.")
                values[key] = v
        sections[name] = Section(name, values)
    for part in ("sheet1", "sheet2"):
        if abs(sections[part].total() - 1.0) > 1e-6:
            raise RenderSettingsError(f"render.toml, [{part}]: доли должны давать в сумме 1.")
    return RenderSettings(sections)


def _load(path: Path) -> RenderSettings:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise RenderSettingsError(f"{path.name}: ошибка разбора TOML: {exc}") from exc
    return parse_render_settings(data)


@lru_cache(maxsize=1)
def _default() -> RenderSettings:
    return _load(RENDER_TOML)


def load_render_settings(path: str | Path | None = None) -> RenderSettings:
    """Читает render.toml (по умолчанию - из пакета; результат кэшируется)."""
    return _default() if path is None else _load(Path(path))

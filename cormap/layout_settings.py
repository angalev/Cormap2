"""Настройки листа и размещения (`assets/settings/layout.toml`) со строгой проверкой.

Модуль не зависит от Django и matplotlib: его берут и разбиение на листы (этап 5),
и отрисовка (этап 6), и Excel (этап 7).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cormap.assets import ROOT

LAYOUT_TOML = ROOT / "settings" / "layout.toml"


class LayoutError(Exception):
    """Ошибка пользователя при разбиении (текст по-русски, показывается как есть)."""


class LayoutSettingsError(ValueError):
    """Ошибка в файле настроек layout.toml."""


@dataclass(frozen=True, slots=True)
class Paper:
    """Формат бумаги, альбомная ориентация."""

    name: str
    width_mm: float
    height_mm: float


@dataclass(frozen=True, slots=True)
class LayoutSettings:
    papers: tuple[Paper, ...]
    # поля и колонки листа
    margin_mm: float
    label_column_mm: float
    plot_pad_left_mm: float
    plot_pad_right_mm: float
    # ряд длин листа и шаг делений, км
    lengths_km: tuple[float, ...]
    tick_steps_km: tuple[float, ...]
    # автоматический режим
    auto_max_length_km: float
    auto_min_length_km: float
    tiers: int
    max_sections: int
    # подписи и значки
    font_pt: float
    gap_mm: float
    callout_levels: int
    callout_step_mm: float
    min_extended_width_mm: float
    min_cross_band_width_mm: float
    font_families: tuple[str, ...]

    def paper(self, name: str) -> Paper:
        """Формат бумаги по имени (без учёта регистра); LayoutError, если нет такого."""
        key = str(name).strip().upper()
        for p in self.papers:
            if p.name.upper() == key:
                return p
        names = ", ".join(p.name for p in self.papers)
        raise LayoutError(f"Неизвестный формат бумаги «{name}». Доступно: {names}.")

    @property
    def lengths_m(self) -> tuple[float, ...]:
        """Ряд длин листа, м."""
        return tuple(round(v * 1000.0, 6) for v in self.lengths_km)

    def tick_step_m(self, nominal_length_m: float) -> float | None:
        """Шаг делений, м, если длина совпала с длиной из ряда; иначе None."""
        for length, step in zip(self.lengths_m, self.tick_steps_km, strict=True):
            if abs(length - nominal_length_m) < 1e-6:
                return round(step * 1000.0, 6)
        return None

    def plot_width_mm(self, paper: str = "A4") -> float:
        """Ширина оси X участка на бумаге, мм (для A4: 297 - 20 - 35 - 12 - 6 = 224)."""
        p = self.paper(paper)
        return (
            p.width_mm
            - 2 * self.margin_mm
            - self.label_column_mm
            - self.plot_pad_left_mm
            - self.plot_pad_right_mm
        )


def _section(data: dict, name: str, keys: set[str]) -> dict:
    sec = data.get(name)
    if not isinstance(sec, dict):
        raise LayoutSettingsError(f"layout.toml: нет раздела [{name}].")
    extra = set(sec) - keys
    missing = keys - set(sec)
    if extra:
        raise LayoutSettingsError(f"layout.toml, [{name}]: лишние ключи {sorted(extra)}.")
    if missing:
        raise LayoutSettingsError(f"layout.toml, [{name}]: нет ключей {sorted(missing)}.")
    return sec


def _num(sec: dict, section: str, key: str, *, positive: bool = True) -> float:
    v = sec[key]
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise LayoutSettingsError(f"layout.toml, [{section}] {key}: нужно число.")
    if (positive and v <= 0) or (not positive and v < 0):
        word = "> 0" if positive else ">= 0"
        raise LayoutSettingsError(f"layout.toml, [{section}] {key}: значение должно быть {word}.")
    return float(v)


def _int(sec: dict, section: str, key: str, minimum: int) -> int:
    v = sec[key]
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise LayoutSettingsError(
            f"layout.toml, [{section}] {key}: нужно целое число не меньше {minimum}."
        )
    return v


def _num_list(sec: dict, section: str, key: str) -> tuple[float, ...]:
    v = sec[key]
    ok = (
        isinstance(v, list)
        and len(v) > 0
        and all(isinstance(x, int | float) and not isinstance(x, bool) and x > 0 for x in v)
    )
    if not ok:
        raise LayoutSettingsError(
            f"layout.toml, [{section}] {key}: нужен непустой список положительных чисел."
        )
    return tuple(float(x) for x in v)


def parse_settings(data: dict) -> LayoutSettings:
    """Строгая проверка словаря настроек (результат tomllib)."""
    extra = set(data) - {"paper", "page", "sheet", "auto", "labels"}
    if extra:
        raise LayoutSettingsError(f"layout.toml: лишние разделы {sorted(extra)}.")
    paper_raw = data.get("paper")
    if not isinstance(paper_raw, dict) or not paper_raw:
        raise LayoutSettingsError("layout.toml: нет разделов [paper.A4] и т. п.")
    papers = []
    for name, sec in paper_raw.items():
        s = _section({"p": sec}, "p", {"width_mm", "height_mm"})
        papers.append(
            Paper(name, _num(s, f"paper.{name}", "width_mm"), _num(s, f"paper.{name}", "height_mm"))
        )
    page_keys = {"margin_mm", "label_column_mm", "plot_pad_left_mm", "plot_pad_right_mm"}
    page = _section(data, "page", page_keys)
    sheet = _section(data, "sheet", {"lengths_km", "tick_steps_km"})
    auto = _section(data, "auto", {"max_length_km", "min_length_km", "tiers", "max_sections"})
    lab_keys = {
        "font_pt",
        "gap_mm",
        "callout_levels",
        "callout_step_mm",
        "min_extended_width_mm",
        "min_cross_band_width_mm",
        "font_families",
    }
    lab = _section(data, "labels", lab_keys)

    lengths = _num_list(sheet, "sheet", "lengths_km")
    steps = _num_list(sheet, "sheet", "tick_steps_km")
    if len(lengths) != len(steps):
        raise LayoutSettingsError("layout.toml, [sheet]: списки длин и шагов разной длины.")
    if any(b <= a for a, b in zip(lengths, lengths[1:], strict=False)):
        raise LayoutSettingsError("layout.toml, [sheet] lengths_km: нужен возрастающий ряд.")
    amax = _num(auto, "auto", "max_length_km")
    amin = _num(auto, "auto", "min_length_km")
    if amax not in lengths or amin not in lengths or amin > amax:
        raise LayoutSettingsError(
            "layout.toml, [auto]: наибольшая и наименьшая длины должны быть в ряду "
            "lengths_km, наименьшая не больше наибольшей."
        )
    fams = lab["font_families"]
    if not (isinstance(fams, list) and fams and all(isinstance(f, str) and f for f in fams)):
        raise LayoutSettingsError("layout.toml, [labels] font_families: нужен список имён.")

    settings = LayoutSettings(
        papers=tuple(papers),
        margin_mm=_num(page, "page", "margin_mm", positive=False),
        label_column_mm=_num(page, "page", "label_column_mm", positive=False),
        plot_pad_left_mm=_num(page, "page", "plot_pad_left_mm", positive=False),
        plot_pad_right_mm=_num(page, "page", "plot_pad_right_mm", positive=False),
        lengths_km=lengths,
        tick_steps_km=steps,
        auto_max_length_km=amax,
        auto_min_length_km=amin,
        tiers=_int(auto, "auto", "tiers", 1),
        max_sections=_int(auto, "auto", "max_sections", 1),
        font_pt=_num(lab, "labels", "font_pt"),
        gap_mm=_num(lab, "labels", "gap_mm", positive=False),
        callout_levels=_int(lab, "labels", "callout_levels", 1),
        callout_step_mm=_num(lab, "labels", "callout_step_mm"),
        min_extended_width_mm=_num(lab, "labels", "min_extended_width_mm"),
        min_cross_band_width_mm=_num(lab, "labels", "min_cross_band_width_mm"),
        font_families=tuple(fams),
    )
    for p in settings.papers:
        if settings.plot_width_mm(p.name) <= 0:
            raise LayoutSettingsError(f"layout.toml: на бумаге {p.name} нет места под график.")
    return settings


def _load(path: Path) -> LayoutSettings:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutSettingsError(f"{path.name}: ошибка разбора TOML: {exc}") from exc
    return parse_settings(data)


@lru_cache(maxsize=1)
def _default() -> LayoutSettings:
    return _load(LAYOUT_TOML)


def load_settings(path: str | Path | None = None) -> LayoutSettings:
    """Читает layout.toml (по умолчанию - из пакета; результат кэшируется)."""
    return _default() if path is None else _load(Path(path))


def plot_width_mm(paper: str = "A4", settings: LayoutSettings | None = None) -> float:
    """Ширина оси X участка на бумаге, мм. Этап 6 берёт геометрию отсюда."""
    return (settings or load_settings()).plot_width_mm(paper)

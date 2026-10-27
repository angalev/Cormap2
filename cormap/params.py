"""Параметры построения карты: форма «Новая карта» (ТЗ, раздел 4.2).

Модуль не зависит от Django. Неверные значения дают ParamsError с понятным текстом
по-русски (его можно показывать пользователю как есть).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from cormap.layout import LayoutError, parse_split_spec

PAPERS = ("A4", "A3")
LEGEND_MODES = ("all", "used", "none")


class ParamsError(ValueError):
    """Ошибка в параметрах построения (текст по-русски).

    ``field`` - имя поля BuildParams, к которому относится ошибка (форма показывает
    сообщение у этого поля); None - ошибка общая.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _number(
    value: object, title: str, *, positive: bool = False, field: str | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ParamsError(f"{title}: нужно число, получено «{value}».", field)
    result = float(value)
    if not math.isfinite(result):
        raise ParamsError(f"{title}: нужно конечное число.", field)
    if positive and result <= 0:
        raise ParamsError(f"{title}: значение должно быть больше нуля.", field)
    return result


def _fmt_m(value: float) -> str:
    """Метры без потери точности (до миллиметра), без лишних нулей: 123456.5, 2500."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _split(value: object) -> str | float | tuple[float, ...]:
    """«auto», длина листа (м) или границы (м) в канонической форме."""
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in ("", "auto", "авто"):
            return "auto"
        try:
            return parse_split_spec(text)
        except LayoutError as exc:
            raise ParamsError(f"Разбиение на листы: {exc}") from exc
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _number(value, "Длина листа", positive=True)
    if isinstance(value, Sequence):
        bounds = tuple(_number(v, "Граница листа") for v in value)
        if len(bounds) < 2:
            raise ParamsError("Разбиение на листы: для границ нужно не меньше двух значений.")
        return bounds
    raise ParamsError(f"Разбиение на листы: не понимаю значение «{value}».")


@dataclass(frozen=True, slots=True)
class BuildParams:
    """Параметры построения (значения по умолчанию - из образца, ТЗ 4.2)."""

    object_name: str
    diameter_mm: float | None = None
    start_km: float = 0.0
    u_min: float = -0.85  # мин. нормативный потенциал, В (красная линия)
    u_max: float = -2.50  # макс. нормативный потенциал, В (синяя линия)
    depth_min: float = 0.80  # мин. нормативная глубина, м
    res_low: float = 20.0  # порог УЭС: ниже - высокая агрессивность, Ом·м
    res_high: float = 50.0  # порог УЭС: от него - низкая агрессивность, Ом·м
    res_axis_max: float | None = None  # верх оси УЭС, Ом·м; None - по данным участка
    paper: str = "A4"
    split: str | float | tuple[float, ...] = "auto"  # «auto», длина листа или границы, м
    legend: str = "all"  # лист условных обозначений: all / used / none

    def __post_init__(self) -> None:
        def put(name: str, value: object) -> None:
            object.__setattr__(self, name, value)

        if not isinstance(self.object_name, str) or not self.object_name.strip():
            raise ParamsError("Наименование объекта обязательно.")
        put("object_name", self.object_name.strip())
        if self.diameter_mm is not None:
            put(
                "diameter_mm",
                _number(self.diameter_mm, "Диаметр, мм", positive=True, field="diameter_mm"),
            )
        put("start_km", _number(self.start_km, "Начальный километраж, км", field="start_km"))
        put("u_min", _number(self.u_min, "Мин. нормативный потенциал, В", field="u_min"))
        put("u_max", _number(self.u_max, "Макс. нормативный потенциал, В", field="u_max"))
        if self.u_max >= self.u_min:
            raise ParamsError(
                "Макс. нормативный потенциал должен быть отрицательнее минимального "
                f"(сейчас {self.u_max:g} и {self.u_min:g} В).",
                "u_max",
            )
        put(
            "depth_min",
            _number(
                self.depth_min, "Мин. нормативная глубина, м", positive=True, field="depth_min"
            ),
        )
        put(
            "res_low",
            _number(self.res_low, "Нижний порог УЭС, Ом·м", positive=True, field="res_low"),
        )
        put(
            "res_high",
            _number(self.res_high, "Верхний порог УЭС, Ом·м", positive=True, field="res_high"),
        )
        if self.res_low >= self.res_high:
            raise ParamsError(
                "Нижний порог УЭС должен быть меньше верхнего "
                f"(сейчас {self.res_low:g} и {self.res_high:g} Ом·м).",
                "res_low",
            )
        if self.res_axis_max is not None:
            put(
                "res_axis_max",
                _number(
                    self.res_axis_max, "Верх оси УЭС, Ом·м", positive=True, field="res_axis_max"
                ),
            )
            if self.res_axis_max <= self.res_high:
                raise ParamsError(
                    "Верх оси УЭС должен быть больше верхнего порога УЭС "
                    f"(сейчас {self.res_axis_max:g} и {self.res_high:g} Ом·м).",
                    "res_axis_max",
                )
        paper = str(self.paper).strip().upper()
        if paper not in PAPERS:
            raise ParamsError(
                f"Формат листа: допустимо {' или '.join(PAPERS)}, получено «{self.paper}»."
            )
        put("paper", paper)
        put("split", _split(self.split))
        legend = str(self.legend).strip().lower()
        if legend not in LEGEND_MODES:
            raise ParamsError(
                f"Лист условных обозначений: допустимо {', '.join(LEGEND_MODES)}, "
                f"получено «{self.legend}»."
            )
        put("legend", legend)

    @property
    def split_text(self) -> str:
        """Разбиение в виде строки для parse_split_spec и check.propose_split."""
        if isinstance(self.split, str):
            return self.split
        if isinstance(self.split, tuple):
            return "; ".join(_fmt_m(v) for v in self.split)
        return _fmt_m(self.split)

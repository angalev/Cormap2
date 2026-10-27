"""Формы: новая карта (ТЗ, 4.2), правка разбиения, замена книги."""

from __future__ import annotations

import math
import zipfile
from typing import Any

from django import forms
from django.conf import settings

from cormap.layout import LayoutError, parse_split_spec
from cormap.params import LEGEND_MODES, PAPERS, BuildParams, ParamsError
from cormap.preprocess import (
    FLAG_GROUPS,
    STEP_DEFAULT,
    STEP_LABEL,
    RefineOptions,
    validate,
)
from maps.services import refinery_enabled

DEFAULTS = BuildParams(object_name="x")  # значения по умолчанию берём из ядра

LEGEND_LABELS = {
    "all": "все обозначения",
    "used": "только встретившиеся на карте",
    "none": "не добавлять",
}


def fmt_input(value: float | None) -> str:
    """Число для поля формы: десятичная запятая, без лишних нулей."""
    if value is None:
        return ""
    return f"{value:g}".replace(".", ",")


class NumberField(forms.Field):
    """Число с запятой или точкой. Пустое значение допустимо, если поле не обязательное."""

    widget = forms.TextInput

    def __init__(self, *, positive: bool = False, **kwargs: Any) -> None:
        self.positive = positive
        super().__init__(**kwargs)

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs.setdefault("inputmode", "decimal")
        attrs.setdefault("autocomplete", "off")
        return attrs

    def to_python(self, value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        text = text.replace("−", "-").replace("–", "-").replace(",", ".")
        text = text.replace(" ", "").replace(" ", "")
        try:
            number = float(text)
        except ValueError:
            raise forms.ValidationError("Введите число, например 0,85 или 0.85.") from None
        if not math.isfinite(number):
            raise forms.ValidationError("Введите конечное число.")
        if self.positive and number <= 0:
            raise forms.ValidationError("Значение должно быть больше нуля.")
        return number


def validate_xlsx(upload) -> None:
    """Расширение, размер и то, что файл - zip-контейнер книги xlsx."""
    name = (upload.name or "").lower()
    if not name.endswith(".xlsx"):
        raise forms.ValidationError("Принимаются только файлы формата .xlsx.")
    limit = settings.CORMAP_MAX_UPLOAD_BYTES
    if upload.size > limit:
        raise forms.ValidationError(
            f"Файл слишком большой: {upload.size / 1024 / 1024:.1f} МБ, "
            f"допустимо не более {limit / 1024 / 1024:g} МБ."
        )
    if upload.size == 0:
        raise forms.ValidationError("Файл пустой.")
    upload.seek(0)
    try:
        with zipfile.ZipFile(upload) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        raise forms.ValidationError(
            "Файл не похож на книгу Excel (.xlsx): это не архив-контейнер xlsx."
        ) from None
    finally:
        upload.seek(0)
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise forms.ValidationError("Файл не похож на книгу Excel (.xlsx): нет нужных частей.")


class BookField(forms.FileField):
    def validate(self, value) -> None:
        super().validate(value)
        if value:
            validate_xlsx(value)


class RefineFieldsMixin:
    """Блок «Обработка данных» (ТЗ, 4.5): флажки и шаг. Есть, только если шаг включён."""

    refinery_on: bool = False

    def _add_refinery_fields(self) -> None:
        self.refinery_on = refinery_enabled()
        if not self.refinery_on:
            return
        for _, items in FLAG_GROUPS:
            for name, label in items:
                self.fields[name] = forms.BooleanField(label=label, required=False)
        self.fields["step"] = forms.IntegerField(label=STEP_LABEL)
        self.initial.setdefault("step", STEP_DEFAULT)

    @property
    def refinery_groups(self) -> list[tuple[str, list[Any]]]:
        """Группы флажков для шаблона: (заголовок блока, [поля формы])."""
        if not self.refinery_on:
            return []
        return [(title, [self[name] for name, _ in items]) for title, items in FLAG_GROUPS]

    def _clean_refinery(self, cleaned: dict[str, Any]) -> None:
        """Правила ТЗ 4.5 до вызова модуля; ошибки - у нужных флажков."""
        if not self.refinery_on or cleaned.get("step") is None:
            return
        for field, text in validate(self._options_from(cleaned)):
            self.add_error(field if field in self.fields else None, text)

    def _options_from(self, data: dict[str, Any]) -> RefineOptions:
        flags = {name: bool(data.get(name)) for _, items in FLAG_GROUPS for name, _ in items}
        return RefineOptions(**flags, step=data["step"])

    def refine_options(self) -> RefineOptions | None:
        """Настройки модуля из проверенной формы (без сида); None - блока на форме нет."""
        if not self.refinery_on:
            return None
        return self._options_from(self.cleaned_data)


class NewMapForm(RefineFieldsMixin, forms.Form):
    """Загрузка книги и параметры построения."""

    book = BookField(
        label="Книга Excel (.xlsx)", widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"})
    )
    object_name = forms.CharField(label="Наименование объекта", max_length=255)
    diameter_mm = NumberField(label="Диаметр, мм", required=False, positive=True)
    start_km = NumberField(label="Начальный километраж, км", required=False)
    u_min = NumberField(label="Мин. нормативный потенциал, В")
    u_max = NumberField(label="Макс. нормативный потенциал, В")
    depth_min = NumberField(label="Мин. нормативная глубина, м", positive=True)
    res_low = NumberField(label="Нижний порог УЭС, Ом·м", positive=True)
    res_high = NumberField(label="Верхний порог УЭС, Ом·м", positive=True)
    res_axis_max = NumberField(label="Верх оси УЭС, Ом·м", required=False, positive=True)
    paper = forms.ChoiceField(label="Формат листа", choices=[(p, f"{p} альбомный") for p in PAPERS])
    legend = forms.ChoiceField(
        label="Лист условных обозначений", choices=list(LEGEND_LABELS.items())
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.initial.setdefault("start_km", fmt_input(DEFAULTS.start_km))
        self.initial.setdefault("u_min", fmt_input(DEFAULTS.u_min))
        self.initial.setdefault("u_max", fmt_input(DEFAULTS.u_max))
        self.initial.setdefault("depth_min", fmt_input(DEFAULTS.depth_min))
        self.initial.setdefault("res_low", fmt_input(DEFAULTS.res_low))
        self.initial.setdefault("res_high", fmt_input(DEFAULTS.res_high))
        self.initial.setdefault("paper", DEFAULTS.paper)
        self.initial.setdefault("legend", DEFAULTS.legend)
        assert set(LEGEND_LABELS) == set(LEGEND_MODES)
        self._add_refinery_fields()

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        self._clean_refinery(cleaned)
        if self.errors:
            return cleaned
        try:
            self.build_params()
        except ParamsError as exc:
            # ошибка относится к полю формы - показываем у поля, иначе общей
            self.add_error(exc.field if exc.field in self.fields else None, str(exc))
        return cleaned

    def build_params(self) -> BuildParams:
        data = self.cleaned_data
        start_km = data.get("start_km")
        return BuildParams(
            object_name=data["object_name"],
            diameter_mm=data.get("diameter_mm"),
            start_km=DEFAULTS.start_km if start_km is None else start_km,
            u_min=data["u_min"],
            u_max=data["u_max"],
            depth_min=data["depth_min"],
            res_low=data["res_low"],
            res_high=data["res_high"],
            res_axis_max=data.get("res_axis_max"),
            paper=data["paper"],
            split="auto",
            legend=data["legend"],
        )


class ReplaceBookForm(RefineFieldsMixin, forms.Form):
    """Загрузка исправленной книги для существующей карты.

    Настройки обработки данных по умолчанию - текущие настройки карты (``initial``).
    """

    book = BookField(
        label="Исправленная книга Excel (.xlsx)",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._add_refinery_fields()

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        self._clean_refinery(cleaned)
        return cleaned


SPLIT_MODES = (
    ("auto", "авто"),
    ("length", "одна длина листа, км"),
    ("bounds", "границы листов, км"),
)


class SplitForm(forms.Form):
    """Правка разбиения на листы (ТЗ, раздел 6). Значения - в километрах.

    Все сообщения у поля - в тех же единицах, что и ввод: километры по километражу
    (с учётом начального км), десятичная запятая.
    """

    mode = forms.ChoiceField(label="Режим", choices=SPLIT_MODES, widget=forms.RadioSelect)
    value = forms.CharField(label="Значение", required=False, max_length=4000)

    def __init__(
        self,
        *args: Any,
        start_km: float = 0.0,
        dist_range: tuple[float, float] | None = None,
        max_sections: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.start_km = start_km
        self.dist_range = dist_range
        self.max_sections = max_sections

    @property
    def example(self) -> str:
        """Пример границ по фактическому километражу: начало; середина; конец трассы."""
        return example_bounds(self.start_km, self.dist_range)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        mode = cleaned.get("mode")
        text = (cleaned.get("value") or "").strip()
        if mode is None:
            return cleaned
        try:
            cleaned["split"] = split_from_km(
                mode,
                text,
                self.start_km,
                route_m=(self.dist_range[1] - self.dist_range[0]) if self.dist_range else None,
                max_sections=self.max_sections,
            )
        except LayoutError as exc:
            self.add_error("value", str(exc))
        return cleaned


def km_text(dist_m: float, start_km: float) -> str:
    """Дистанция (м от начала данных) -> километраж с запятой: «12,5»."""
    return fmt_input(round(start_km + dist_m / 1000, 6))


def example_bounds(start_km: float, dist_range: tuple[float, float] | None) -> str:
    """Пример для подсказки: начало; середина; конец трассы по фактическому километражу."""
    lo, hi = dist_range if dist_range else (0.0, 0.0)
    if hi - lo < 1e-9:
        hi = lo + 1000.0
    return "; ".join(km_text(v, start_km) for v in (lo, (lo + hi) / 2, hi))


def split_from_km(
    mode: str,
    text: str,
    start_km: float,
    *,
    route_m: float | None = None,
    max_sections: int | None = None,
) -> str | float | tuple[float, ...]:
    """Значение формы (км) -> разбиение для ядра (метры дистанции).

    «auto» -> "auto"; «length» -> длина листа, м; «bounds» -> границы в метрах дистанции:
    (км - начальный км) * 1000, так как границы вводятся по километражу с учётом
    начального километра. Ошибки формулируются в километрах (в единицах ввода).
    """
    if mode == "auto":
        return "auto"
    if not text:
        raise LayoutError("Укажите значение разбиения.")
    parsed = parse_split_spec(text)
    if mode == "length":
        if not isinstance(parsed, float) or parsed <= 0:
            raise LayoutError("Нужна одна положительная длина листа, км.")
        if route_m is not None and max_sections:
            count = max(math.ceil(route_m / (parsed * 1000) - 1e-9), 1)
            if count > max_sections:
                need = math.ceil(route_m / max_sections / 10) / 100  # км, с округлением вверх
                raise LayoutError(
                    f"Разбиение даёт {count} участков, предел - {max_sections}. "
                    f"Увеличьте длину листа (не меньше {fmt_input(need)} км)."
                )
        return round(parsed * 1000, 6)
    if isinstance(parsed, float):
        example = example_bounds(start_km, (0.0, route_m) if route_m else None)
        raise LayoutError(
            f"Для границ нужно не меньше двух значений через «;», например {example}."
        )
    for prev, cur in zip(parsed, parsed[1:], strict=False):
        if cur <= prev:
            raise LayoutError(
                f"Границы листов должны возрастать: {fmt_input(prev)} -> {fmt_input(cur)} км."
            )
    if max_sections and len(parsed) - 1 > max_sections:
        raise LayoutError(f"Разбиение даёт {len(parsed) - 1} участков, предел - {max_sections}.")
    return tuple(round((v - start_km) * 1000, 6) for v in parsed)


def split_to_km_text(split: str | float | list | tuple, start_km: float) -> tuple[str, str]:
    """Разбиение ядра (метры) -> (режим, текст для поля формы в километрах)."""
    if isinstance(split, str):
        return "auto", ""
    if isinstance(split, int | float):
        return "length", fmt_input(round(split / 1000, 6))
    values = [fmt_input(round(start_km + v / 1000, 6)) for v in split]
    return "bounds", "; ".join(values)


def bounds_km_text(bounds_m: list[float] | tuple[float, ...], start_km: float) -> str:
    """Границы текущего разбиения (метры дистанции) -> «12,5; 13; 15»."""
    return "; ".join(km_text(v, start_km) for v in bounds_m)

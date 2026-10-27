"""Повреждения изоляционного покрытия (ИП): каркас расчёта и ручные данные.

Критерии расчёта лежат в файле настроек `assets/settings/defects.toml` и в коде
не зашиты (ТЗ, раздел 5.3). Пока заказчик их не передал, расчёт выключен.

Правила границ (едины для всех порогов):

* вне нормы - сигнал (или его кратность к фону) СТРОГО больше границы нормы;
* классы по силе и по протяжённости задаются нижними границами; интервалы
  полуоткрытые: «от 2 до 4» - это [2, 4), «4 и выше» - [4, +inf). Значение
  ниже нижней границы класса 1 всё равно получает класс 1 (участок уже вне нормы);
* склейка: две соседние точки вне нормы относятся к одному участку, если
  расстояние между ними НЕ БОЛЬШЕ `max_gap_m`;
* участок короче `min_length_m` отбрасывается (длина ровно `min_length_m` остаётся).
"""

from __future__ import annotations

import bisect
import re
import statistics
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .model import WARNING, DefectsBlock, Issue, Survey, fmt_num
from .notes import TrackObject

SIGNAL_COLUMNS = (
    "grad_left_on",
    "grad_left_off",
    "grad_right_on",
    "grad_right_off",
    "u_on",
    "u_off",
)
_GRADIENT_COLUMNS = frozenset(SIGNAL_COLUMNS[:4])
SIGNAL_COMBINES = ("max_abs", "sum_abs", "single")
SOURCES = ("manual", "calc", "both")
NORM_MODES = ("constant", "ratio")
BACKGROUND_METHODS = ("median", "rolling_median", "constant")
FINAL_CLASS_MODES = ("max", "min", "strength", "length")

_TOP_KEYS = {"enabled", "source", "final_class"}
_SECTION_KEYS = {
    "signal": {"columns", "combine"},
    "norm": {"mode", "value"},
    "background": {"method", "window_m", "value"},
    "merge": {"max_gap_m", "min_length_m"},
    "class_strength": {"class_1", "class_2", "class_3"},
    "class_length": {"class_1", "class_2", "class_3"},
}


class CriteriaError(ValueError):
    """Ошибка в файле критериев повреждений."""


@dataclass(frozen=True, slots=True)
class Criteria:
    """Критерии повреждений ИП. None - параметр в файле не задан."""

    enabled: bool = False
    source: str = "manual"
    final_class: str = "max"
    columns: tuple[str, ...] | None = None
    signal_combine: str = "max_abs"
    norm_mode: str | None = None
    norm_value: float | None = None
    background_method: str | None = None
    background_window_m: float | None = None
    background_value: float | None = None
    max_gap_m: float | None = None
    min_length_m: float | None = None
    strength: tuple[float, float, float] | None = None  # нижние границы классов 1, 2, 3
    length: tuple[float, float, float] | None = None  # то же по протяжённости, м


@dataclass(frozen=True, slots=True)
class DefectSegment:
    """Участок повреждения ИП."""

    start: float
    end: float
    length: float
    max_deviation: float | None  # None для ручных
    class_strength: int | None
    class_length: int | None
    class_final: int | None  # 1..3 или None (класс неизвестен)
    source: str  # "calc" | "manual"
    row: int | None = None  # строка Excel для ручных


# --------------------------------------------------------------------------
# Загрузка критериев
# --------------------------------------------------------------------------


def _default_text() -> str:
    return (
        resources.files("cormap")
        .joinpath("assets", "settings", "defects.toml")
        .read_text(encoding="utf-8")
    )


def _num(value: object, name: str, *, positive: bool = False, nonneg: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CriteriaError(f"Параметр «{name}» должен быть числом, получено: {value!r}.")
    result = float(value)
    if positive and result <= 0:
        raise CriteriaError(f"Параметр «{name}» должен быть больше нуля, получено: {value!r}.")
    if nonneg and result < 0:
        raise CriteriaError(f"Параметр «{name}» не может быть отрицательным: {value!r}.")
    return result


def _choice(value: object, name: str, allowed: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise CriteriaError(
            f"Параметр «{name}»: допустимые значения {', '.join(allowed)}; получено: {value!r}."
        )
    return value


def _thresholds(section: dict, name: str) -> tuple[float, float, float] | None:
    """Три нижние границы классов; все или ни одной; строго по возрастанию."""
    present = [k for k in ("class_1", "class_2", "class_3") if k in section]
    if not present:
        return None
    if len(present) != 3:
        missing = ", ".join(
            f"{name}.{k}" for k in ("class_1", "class_2", "class_3") if k not in section
        )
        raise CriteriaError(f"Пороги «{name}» заданы не полностью, не хватает: {missing}.")
    values = tuple(_num(section[k], f"{name}.{k}", nonneg=True) for k in present)
    if not values[0] < values[1] < values[2]:
        raise CriteriaError(f"Пороги «{name}» должны строго возрастать: {values}.")
    return values  # type: ignore[return-value]


def parse_criteria(data: dict) -> Criteria:
    """Строгая проверка разобранного TOML и сборка `Criteria`."""
    unknown = sorted(set(data) - _TOP_KEYS - set(_SECTION_KEYS))
    if unknown:
        raise CriteriaError(f"Неизвестные ключи в файле критериев: {', '.join(unknown)}.")
    sections: dict[str, dict] = {}
    for name, allowed in _SECTION_KEYS.items():
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise CriteriaError(f"«{name}» должен быть таблицей.")
        extra = sorted(set(section) - allowed)
        if extra:
            raise CriteriaError(
                f"Неизвестные ключи в разделе [{name}]: {', '.join(extra)}. "
                f"Допустимы: {', '.join(sorted(allowed))}."
            )
        sections[name] = section

    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise CriteriaError(
            f"Параметр «enabled» должен быть true или false, получено: {enabled!r}."
        )
    source = _choice(data.get("source", "manual"), "source", SOURCES)
    final_class = _choice(data.get("final_class", "max"), "final_class", FINAL_CLASS_MODES)

    sig = sections["signal"]
    columns: tuple[str, ...] | None = None
    if "columns" in sig:
        raw = sig["columns"]
        if not isinstance(raw, list) or not raw:
            raise CriteriaError("«signal.columns» должен быть непустым списком колонок.")
        for c in raw:
            _choice(c, "signal.columns", SIGNAL_COLUMNS)
        columns = tuple(raw)
        if len(set(columns)) != len(columns):
            raise CriteriaError("«signal.columns»: колонки не должны повторяться.")
        if len({c in _GRADIENT_COLUMNS for c in columns}) > 1:
            raise CriteriaError(
                "«signal.columns»: нельзя смешивать градиенты и потенциалы "
                "(у них разные дистанции)."
            )
    combine = _choice(sig.get("combine", "max_abs"), "signal.combine", SIGNAL_COMBINES)
    if combine == "single" and columns is not None and len(columns) != 1:
        raise CriteriaError("«signal.combine = single» требует ровно одну колонку.")

    norm = sections["norm"]
    norm_mode = _choice(norm["mode"], "norm.mode", NORM_MODES) if "mode" in norm else None
    norm_value = _num(norm["value"], "norm.value", positive=True) if "value" in norm else None

    bg = sections["background"]
    bg_method = (
        _choice(bg["method"], "background.method", BACKGROUND_METHODS) if "method" in bg else None
    )
    bg_window = (
        _num(bg["window_m"], "background.window_m", positive=True) if "window_m" in bg else None
    )
    bg_value = _num(bg["value"], "background.value", positive=True) if "value" in bg else None

    mg = sections["merge"]
    max_gap = _num(mg["max_gap_m"], "merge.max_gap_m", nonneg=True) if "max_gap_m" in mg else None
    min_len = (
        _num(mg["min_length_m"], "merge.min_length_m", nonneg=True)
        if "min_length_m" in mg
        else None
    )

    criteria = Criteria(
        enabled=enabled,
        source=source,
        final_class=final_class,
        columns=columns,
        signal_combine=combine,
        norm_mode=norm_mode,
        norm_value=norm_value,
        background_method=bg_method,
        background_window_m=bg_window,
        background_value=bg_value,
        max_gap_m=max_gap,
        min_length_m=min_len,
        strength=_thresholds(sections["class_strength"], "class_strength"),
        length=_thresholds(sections["class_length"], "class_length"),
    )
    if enabled:
        _require_complete(criteria)
    return criteria


def _require_complete(c: Criteria) -> None:
    """При enabled = true все обязательные критерии должны быть заполнены."""
    missing: list[str] = []
    if c.columns is None:
        missing.append("signal.columns")
    if c.norm_mode is None:
        missing.append("norm.mode")
    if c.norm_value is None:
        missing.append("norm.value")
    if c.norm_mode == "ratio":
        if c.background_method is None:
            missing.append("background.method")
        elif c.background_method == "rolling_median" and c.background_window_m is None:
            missing.append("background.window_m")
        elif c.background_method == "constant" and c.background_value is None:
            missing.append("background.value")
    if c.max_gap_m is None:
        missing.append("merge.max_gap_m")
    if c.min_length_m is None:
        missing.append("merge.min_length_m")
    if c.strength is None:
        missing.append("class_strength.class_1, class_2, class_3")
    if c.final_class == "length" and c.length is None:
        missing.append("class_length (нужен при final_class = length)")
    if missing:
        raise CriteriaError(
            "Расчёт повреждений включён (enabled = true), но не заполнено: "
            + "; ".join(missing)
            + "."
        )


def load_criteria(path: str | Path | None = None) -> Criteria:
    """Загрузить критерии; без пути - поставляемый файл настроек."""
    try:
        if path is None:
            text = _default_text()
        else:
            text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CriteriaError(f"Не удалось прочитать файл критериев: {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CriteriaError(f"Ошибка разбора файла критериев: {exc}") from exc
    return parse_criteria(data)


# --------------------------------------------------------------------------
# Расчёт
# --------------------------------------------------------------------------


def _signal(survey: Survey, c: Criteria) -> tuple[Sequence[float], list[float | None]]:
    """Дистанции блока и сигнал (None - значения нет)."""
    assert c.columns is not None
    if c.columns[0] in _GRADIENT_COLUMNS:
        block = survey.gradients
        cols = {
            "grad_left_on": block.left_on,
            "grad_left_off": block.left_off,
            "grad_right_on": block.right_on,
            "grad_right_off": block.right_off,
        }
    else:
        block = survey.intensive
        cols = {"u_on": block.u_on, "u_off": block.u_off}
    dist = block.dist
    series = [cols[name] for name in c.columns]
    signal: list[float | None] = []
    for i in range(len(dist)):
        vals = [abs(s[i]) for s in series if i < len(s) and s[i] is not None]
        if not vals:
            signal.append(None)
        elif c.signal_combine == "sum_abs":
            signal.append(sum(vals))
        else:  # max_abs и single
            signal.append(max(vals))
    return dist, signal


def _background(dist: Sequence[float], signal: list[float | None], c: Criteria) -> list[float]:
    """Фон в каждой точке (для режима ratio)."""
    if c.background_method == "constant":
        assert c.background_value is not None
        return [c.background_value] * len(dist)
    known = [(d, s) for d, s in zip(dist, signal, strict=True) if s is not None]
    if not known:
        return [0.0] * len(dist)
    if c.background_method == "median":
        med = statistics.median(s for _, s in known)
        return [med] * len(dist)
    # rolling_median: окно window_m метров с центром в точке (включая границы)
    assert c.background_window_m is not None
    half = c.background_window_m / 2
    kd = [d for d, _ in known]
    ks = [s for _, s in known]
    result = []
    for d in dist:
        lo = bisect.bisect_left(kd, d - half)
        hi = bisect.bisect_right(kd, d + half)
        result.append(statistics.median(ks[lo:hi]) if hi > lo else 0.0)
    return result


def _grade(value: float, lows: tuple[float, float, float]) -> int:
    """Класс по нижним границам: [low3, inf) - 3, [low2, low3) - 2, остальное - 1."""
    if value >= lows[2]:
        return 3
    if value >= lows[1]:
        return 2
    return 1


def _combine_classes(strength: int | None, length: int | None, mode: str) -> int | None:
    if mode == "strength":
        return strength
    if mode == "length":
        return length
    present = [x for x in (strength, length) if x is not None]
    if not present:
        return None
    return max(present) if mode == "max" else min(present)


def calc_defects(survey: Survey, criteria: Criteria) -> list[DefectSegment]:
    """Расчётные повреждения по сигналу блока (чистая функция).

    Сигнал строится по дистанциям своего блока. Пропуски (None) отклонением не
    считаются и участок сами не рвут: решает только расстояние между соседними
    точками вне нормы. Сигнал берётся по модулю. Отклонение: в режиме constant -
    значение сигнала, в режиме ratio - сигнал / фон (точки с фоном <= 0 не
    оцениваются). Вне нормы - строго больше границы. Начало и конец участка -
    дистанции первой и последней точек вне нормы; одиночная точка даёт участок
    нулевой протяжённости. Расчёт выключен (enabled = false) - пустой список.
    """
    if not criteria.enabled:
        return []
    _require_complete(criteria)
    assert criteria.norm_value is not None and criteria.strength is not None
    assert criteria.max_gap_m is not None and criteria.min_length_m is not None

    dist, signal = _signal(survey, criteria)
    ratio = criteria.norm_mode == "ratio"
    bg = _background(dist, signal, criteria) if ratio else []

    points: list[tuple[float, float]] = []  # (дистанция, отклонение) вне нормы
    for i, (d, s) in enumerate(zip(dist, signal, strict=True)):
        if s is None:
            continue
        if ratio:
            if bg[i] <= 0:
                continue
            dev = s / bg[i]
        else:
            dev = s
        if dev > criteria.norm_value:
            points.append((d, dev))
    points.sort()

    groups: list[list[tuple[float, float]]] = []
    for p in points:
        if groups and p[0] - groups[-1][-1][0] <= criteria.max_gap_m:
            groups[-1].append(p)
        else:
            groups.append([p])

    result: list[DefectSegment] = []
    for g in groups:
        start, end = g[0][0], g[-1][0]
        length = end - start
        if length < criteria.min_length_m:
            continue
        peak = max(dev for _, dev in g)
        c_str = _grade(peak, criteria.strength)
        c_len = _grade(length, criteria.length) if criteria.length else None
        final = _combine_classes(c_str, c_len, criteria.final_class)
        result.append(DefectSegment(start, end, length, peak, c_str, c_len, final, "calc"))
    return result


# --------------------------------------------------------------------------
# Ручные данные
# --------------------------------------------------------------------------

_CLASS_RE = re.compile(r"(?<![\d.,])([123])(?!\d|[.,]\d)")


def parse_class(value: object) -> int | None:
    """Класс 1/2/3 из числа или текста; None, если однозначно не читается."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if value in (1, 2, 3) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        pass
    else:
        return int(number) if number in (1, 2, 3) else None
    found = set(_CLASS_RE.findall(text))
    return int(found.pop()) if len(found) == 1 else None


def manual_defects(
    defects_block: DefectsBlock,
    pip_marks: Sequence[tuple[float, float | None, str, int | None]] = (),
) -> tuple[list[DefectSegment], list[Issue]]:
    """Ручные повреждения: блок «Повреждения» и пометки «ПИП».

    Блок: значение - класс, если это число 1, 2, 3 или текст с одним читаемым
    классом; иначе участок сохраняется с class_final=None и предупреждением
    `defect_class_unknown`. Точечное повреждение: start == end.
    Пометки: кортежи (start, end | None, подпись, строка); класс из подписи,
    если он там есть, иначе None без предупреждения. Результат отсортирован по start.
    """
    segments: list[DefectSegment] = []
    issues: list[Issue] = []
    for dist, value in zip(defects_block.dist, defects_block.values, strict=False):
        cls = parse_class(value)
        if cls is None:
            issues.append(
                Issue(
                    WARNING,
                    "defect_class_unknown",
                    f"повреждение на дистанции {fmt_num(dist)} м: класс не определён "
                    f"(значение {value!r}); участок сохранён без класса.",
                )
            )
        segments.append(DefectSegment(dist, dist, 0.0, None, None, None, cls, "manual", None))
    for start, end, label, row in pip_marks:
        lo, hi = (start, start if end is None else end)
        if hi < lo:
            lo, hi = hi, lo
        cls = parse_class(label)
        segments.append(DefectSegment(lo, hi, hi - lo, None, None, None, cls, "manual", row))
    segments.sort(key=lambda s: (s.start, s.end))
    return segments, issues


def collect_defects(
    survey: Survey,
    criteria: Criteria,
    pip_marks: Sequence[tuple[float, float | None, str, int | None]] = (),
) -> tuple[list[DefectSegment], list[Issue]]:
    """Повреждения из источника по настройкам.

    Расчёт выключен (enabled = false) - всегда только ручные, какой бы ни был source.
    Иначе: manual - ручные, calc - расчёт, both - оба, отсортированные по start.
    """
    use_calc = criteria.enabled and criteria.source in ("calc", "both")
    use_manual = (not criteria.enabled) or criteria.source in ("manual", "both")
    segments: list[DefectSegment] = []
    issues: list[Issue] = []
    if use_manual:
        segments, issues = manual_defects(survey.defects, pip_marks)
    if use_calc:
        segments = segments + calc_defects(survey, criteria)
        segments.sort(key=lambda s: (s.start, s.end))
    return segments, issues


# --------------------------------------------------------------------------
# Полоса «Площадь дефекта»
# --------------------------------------------------------------------------


def strip_steps(
    segments: Sequence[DefectSegment], start: float, end: float
) -> list[tuple[float, float, int]]:
    """Ступеньки (от, до, класс) в пределах листа [start, end].

    Участки без класса и участки нулевой ширины (точки; после обрезки тоже) в
    ступеньки не попадают. При перекрытии берётся больший класс; соседние
    ступеньки одного класса склеиваются. Результат отсортирован и не перекрывается.
    """
    clipped = []
    for s in segments:
        if s.class_final is None:
            continue
        lo, hi = max(s.start, start), min(s.end, end)
        if hi > lo:
            clipped.append((lo, hi, s.class_final))
    bounds = sorted({b for lo, hi, _ in clipped for b in (lo, hi)})
    steps: list[tuple[float, float, int]] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        cls = max((c for lo, hi, c in clipped if lo <= a and b <= hi), default=None)
        if cls is None:
            continue
        if steps and steps[-1][2] == cls and steps[-1][1] == a:
            steps[-1] = (steps[-1][0], b, cls)
        else:
            steps.append((a, b, cls))
    return steps


# --------------------------------------------------------------------------
# Связь с объектами трассы
# --------------------------------------------------------------------------


def defect_objects(survey: Survey) -> tuple[TrackObject, ...]:
    """Ручные повреждения из блока «Повреждения» как точечные объекты «ПИП».

    Их добавляют к объектам комментариев и при разбиении на листы, и при размещении,
    чтобы значки повреждений не накладывались на остальные.
    """
    return tuple(TrackObject("pip", "", dist, None, 0, None) for dist in survey.defects.dist)


def pip_marks(objects: Sequence[TrackObject]) -> list[tuple[float, float | None, str, int | None]]:
    """Пометки «ПИП» из объектов комментариев в виде, нужном collect_defects."""
    return [(o.start, o.end, o.label, o.row_start) for o in objects if o.type_key == "pip"]

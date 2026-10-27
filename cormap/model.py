"""Структуры данных ядра: прочитанная книга, замечания проверки, отчёт.

Модуль не зависит от Django и от openpyxl.
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

ERROR = "error"  # блокирует построение
WARNING = "warning"  # не блокирует

# Допустимые типы (раздел 4.1 ТЗ) в нормализованном виде (регистр, ё/е).
SOIL_TYPES: tuple[str, ...] = ("песчаный", "скальный", "суглинок", "супесь", "торф", "илистый")
INSULATION_TYPES: tuple[str, ...] = (
    "битумное",
    "заводское полиэтиленовое",
    "лакокрасочное",
    "пленочное полимерное",
)

_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})


def normalize_text(value: object) -> str:
    """Нормализация текста: регистр, ё/е, неразрывные и лишние пробелы, виды тире."""
    text = unicodedata.normalize("NFKC", str(value)).translate(_DASHES)
    return " ".join(text.lower().replace("ё", "е").split())


@dataclass(frozen=True, slots=True)
class TypeMark:
    """Отметка типа (грунта или изоляции): действует от `dist` до следующей отметки."""

    dist: float
    value: str  # нормализованный текст
    known: bool  # входит в список допустимых значений
    row: int  # номер строки Excel


@dataclass(frozen=True, slots=True)
class IntensiveBlock:
    """Интенсивные измерения: дистанция, U т-з вкл, U т-з выкл (None - значения нет)."""

    dist: tuple[float, ...] = ()
    u_on: tuple[float | None, ...] = ()
    u_off: tuple[float | None, ...] = ()


@dataclass(frozen=True, slots=True)
class GradientBlock:
    """Градиенты; дистанция - основная (колонка «Дистанция, м»).

    Хранятся только строки, где есть хотя бы одно значение градиента.
    """

    dist: tuple[float, ...] = ()
    left_on: tuple[float | None, ...] = ()
    left_off: tuple[float | None, ...] = ()
    right_on: tuple[float | None, ...] = ()
    right_off: tuple[float | None, ...] = ()


@dataclass(frozen=True, slots=True)
class CommentItem:
    """Сырой комментарий: разбор текста - этап 2."""

    dist: float
    row: int
    text: str


@dataclass(frozen=True, slots=True)
class CommentBlock:
    items: tuple[CommentItem, ...] = ()


@dataclass(frozen=True, slots=True)
class GpsItem:
    dist: float
    row: int
    text: str


@dataclass(frozen=True, slots=True)
class GpsBlock:
    """GPS в версии 1 не используется, сохраняется как есть."""

    items: tuple[GpsItem, ...] = ()


@dataclass(frozen=True, slots=True)
class SoilBlock:
    """Грунт: УЭС (своя дистанция) и отметки типа грунта."""

    dist: tuple[float, ...] = ()
    resistivity: tuple[float, ...] = ()
    types: tuple[TypeMark, ...] = ()


@dataclass(frozen=True, slots=True)
class DepthBlock:
    dist: tuple[float, ...] = ()
    depth: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class DefectsBlock:
    """Ручные повреждения ИП. Значение хранится как есть (число или текст)."""

    dist: tuple[float, ...] = ()
    values: tuple[float | str, ...] = ()


@dataclass(frozen=True, slots=True)
class InsulationBlock:
    types: tuple[TypeMark, ...] = ()


@dataclass(frozen=True, slots=True)
class Survey:
    """Прочитанная книга. Блоки независимы: у каждого свои дистанции."""

    sheet_name: str
    intensive: IntensiveBlock = IntensiveBlock()
    gradients: GradientBlock = GradientBlock()
    comments: CommentBlock = CommentBlock()
    gps: GpsBlock = GpsBlock()
    soil: SoilBlock = SoilBlock()
    depth: DepthBlock = DepthBlock()
    defects: DefectsBlock = DefectsBlock()
    insulation: InsulationBlock = InsulationBlock()

    def all_distances(self) -> Iterable[float]:
        yield from self.intensive.dist
        yield from self.gradients.dist
        yield from (c.dist for c in self.comments.items)
        yield from (g.dist for g in self.gps.items)
        yield from self.soil.dist
        yield from (t.dist for t in self.soil.types)
        yield from self.depth.dist
        yield from self.defects.dist
        yield from (t.dist for t in self.insulation.types)

    @property
    def distance_range(self) -> tuple[float, float] | None:
        """Общий диапазон дистанций по всем блокам (None, если данных нет)."""
        lo = math.inf
        hi = -math.inf
        for d in self.all_distances():
            lo = min(lo, d)
            hi = max(hi, d)
        return None if lo > hi else (lo, hi)


@dataclass(frozen=True, slots=True)
class Issue:
    """Замечание проверки."""

    level: str  # ERROR или WARNING
    code: str
    message: str
    cell: str | None = None  # адрес ячейки, например C17
    row: int | None = None  # номер строки Excel

    def format(self) -> str:
        label = "ОШИБКА" if self.level == ERROR else "предупреждение"
        place = ""
        if self.cell:
            place = f"{self.cell}: "
        elif self.row:
            place = f"строка {self.row}: "
        return f"[{label}] {place}{self.message}"


@dataclass(frozen=True, slots=True)
class BlockSummary:
    """Сводка по блоку для отчёта."""

    name: str
    rows: int
    dist_min: float | None = None
    dist_max: float | None = None
    step: float | None = None  # типичный шаг (медиана положительных разностей)
    details: str = ""

    @classmethod
    def from_distances(cls, name: str, dists: Sequence[float], details: str = "") -> BlockSummary:
        if not dists:
            return cls(name, 0, details=details)
        diffs = [b - a for a, b in zip(dists, dists[1:], strict=False) if b > a]
        step = statistics.median(diffs) if diffs else None
        return cls(name, len(dists), min(dists), max(dists), step, details)


def fmt_num(value: float) -> str:
    """Число по-русски: десятичная запятая, без лишних нулей."""
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")


# Сколько замечаний каждого уровня хранится в отчёте; остальные только считаются.
MAX_ISSUES_PER_LEVEL = 1000


@dataclass(slots=True)
class CheckReport:
    """Отчёт проверки книги."""

    path: str = ""
    sheet: str | None = None
    issues: list[Issue] = field(default_factory=list)
    blocks: list[BlockSummary] = field(default_factory=list)
    suppressed: dict[str, int] = field(default_factory=dict)  # уровень -> не сохранено
    _stored: dict[str, int] = field(default_factory=dict)

    def add(self, issue: Issue) -> None:
        stored = self._stored.get(issue.level, 0)
        if stored >= MAX_ISSUES_PER_LEVEL:
            self.suppressed[issue.level] = self.suppressed.get(issue.level, 0) + 1
            return
        self._stored[issue.level] = stored + 1
        self.issues.append(issue)

    def error(
        self, code: str, message: str, cell: str | None = None, row: int | None = None
    ) -> None:
        self.add(Issue(ERROR, code, message, cell, row))

    def warning(
        self, code: str, message: str, cell: str | None = None, row: int | None = None
    ) -> None:
        self.add(Issue(WARNING, code, message, cell, row))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def ok(self) -> bool:
        """Нет блокирующих ошибок."""
        return not any(i.level == ERROR for i in self.issues)

    def format_text(self, sections: Sequence[str] = ()) -> str:
        """Текстовый отчёт для командной строки; sections - доп. разделы перед замечаниями."""
        lines = [f"Проверка книги: {self.path}"]
        lines.append(f"Лист: {self.sheet}" if self.sheet else "Лист: не выбран")
        if self.blocks:
            lines += ["", "Блоки данных:"]
            for b in self.blocks:
                if b.rows == 0:
                    lines.append(f"  {b.name}: нет данных")
                    continue
                part = f"  {b.name}: строк {b.rows}"
                if b.dist_min is not None and b.dist_max is not None:
                    part += f", дистанция от {fmt_num(b.dist_min)} до {fmt_num(b.dist_max)} м"
                if b.step is not None:
                    part += f", шаг {fmt_num(b.step)} м"
                if b.details:
                    part += f" ({b.details})"
                lines.append(part)
        for section in sections:
            lines += ["", section]
        n_err = len(self.errors) + self.suppressed.get(ERROR, 0)
        n_warn = len(self.warnings) + self.suppressed.get(WARNING, 0)
        lines += ["", f"Замечания: ошибок {n_err}, предупреждений {n_warn}"]
        lines += [f"  {i.format()}" for i in self.issues]
        for level, count in self.suppressed.items():
            kind = "ошибок" if level == ERROR else "предупреждений"
            lines.append(f"  ... и ещё {count} {kind} (показаны первые {MAX_ISSUES_PER_LEVEL})")
        lines += ["", "Итог: " + ("проверка пройдена." if self.ok else "есть блокирующие ошибки.")]
        return "\n".join(lines)


def type_intervals(
    marks: Iterable[tuple[float, str]], start: float, end: float
) -> list[tuple[float, float, str]]:
    """Интервалы типа (начало, конец, тип) для участка трассы [start, end].

    Правило: отметка действует от своей дистанции до следующей заполненной;
    первая отметка тянется к началу трассы (start), последняя - к концу (end).
    Отметки с одинаковой дистанцией: побеждает более поздняя в списке. Соседние
    интервалы одного типа склеиваются. Интервалы обрезаются по [start, end];
    пустые не возвращаются. Без отметок или при end <= start результат пуст.
    """
    if end <= start:
        return []
    by_dist: dict[float, str] = {}
    for dist, value in marks:
        by_dist[dist] = value
    items = sorted(by_dist.items())
    result: list[tuple[float, float, str]] = []
    for i, (dist, value) in enumerate(items):
        lo = start if i == 0 else dist
        hi = end if i == len(items) - 1 else items[i + 1][0]
        lo, hi = max(lo, start), min(hi, end)
        if hi <= lo:
            continue
        if result and result[-1][2] == value and result[-1][1] == lo:
            result[-1] = (result[-1][0], hi, value)
        else:
            result.append((lo, hi, value))
    return result

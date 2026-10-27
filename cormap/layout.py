"""Разбиение трассы на листы и размещение значков (ТЗ, разделы 6 и 12).

Модуль ничего не рисует: он отдаёт геометрию в миллиметрах бумаги. Ось X участка
начинается в левом краю области графика, ось Y - линия трубы (y = 0, вверх плюс).
Модуль не зависит от Django и от состояния pyplot.

Модель размещения
-----------------
Все прямоугольники лежат в одной плоскости (x - вдоль трассы, y - от трубы), поэтому
значок «на трубе» или линия «поперёк трубы» естественно мешают значкам первого яруса.
Прямоугольники бывают четырёх видов:

- ``i`` - значок «над трубой»;
- ``o`` - значок «на трубе» (кран, ВЭИ): с ``i`` не конфликтует (кран рисуется поверх
  основания КИП, как в образце), между собой и с ``j`` конфликтует;
- ``j`` - прочие значки (линия поперёк трубы, значки «под трубой»);
- ``l`` - подпись значка;
- ``a`` - полоса «участок под трубой» (вода, болото, лес, овраг...);
- ``b`` - полоса «поперёк трубы» (дорога, ж/д, протяжённая ВЛ и т. п.).

Наложением считается пересечение двух прямоугольников, у которых зазор меньше
``gap_mm``, если они одного вида либо один из них - подпись, а другой - значок или
подпись (значки ``i`` и ``o`` между собой не конфликтуют).
Полосы ``a`` и ``b`` - это заливки: они не мешают значкам и подписям, но
две полосы одного вида друг на друга налезать не должны.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from itertools import chain, islice

from cormap.assets import icon_anchor, icon_size_mm, min_width_mm
from cormap.fonts import installed_families
from cormap.layout_settings import (
    LayoutError,
    LayoutSettings,
    LayoutSettingsError,
    load_settings,
)
from cormap.model import WARNING, Issue
from cormap.notes import (
    ABOVE,
    AREA_BELOW,
    BELOW,
    CROSS_BAND,
    CROSS_LINE,
    OBJECT_TYPES,
    ON,
    TrackObject,
    get_type,
)

__all__ = [
    "LayoutError",
    "LayoutSettings",
    "LayoutSettingsError",
    "PlacedObject",
    "Rect",
    "Section",
    "SectionPlacement",
    "SplitResult",
    "format_km",
    "nice_step",
    "parse_split_spec",
    "place_objects",
    "split_auto",
    "split_manual",
    "tick_positions",
    "zone_extents_mm",
]

_EPS = 1e-6
_PT_MM = 25.4 / 72.0
MODE_AUTO = "auto"
MODE_MANUAL = "manual"

TABLE_HEADER = ("Лист", "От, км", "До, км", "Длина, км", "Объектов", "Наложения")


# --- формат чисел -----------------------------------------------------------


def format_km(value_km: float) -> str:
    """Километраж с двумя знаками и запятой: «12,50»."""
    text = f"{value_km:.2f}".replace(".", ",")
    return text[1:] if text == "-0,00" else text


def nice_step(length_m: float, max_ticks: int = 12) -> float:
    """«Красивый» шаг делений (1/2/5 x 10^k), при котором на длине не больше max_ticks делений.

    Для ручного режима: на листе получается от 5 до 12 делений.
    """
    if length_m <= 0:
        return 1.0
    raw = length_m / max_ticks
    base = 10.0 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * base >= raw * (1 - 1e-9):
            return round(m * base, 9)
    return round(10 * base, 9)  # недостижимо


# --- геометрия --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rect:
    """Прямоугольник, мм: x0 < x1 (вправо), y0 < y1 (вверх от линии трубы)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True, slots=True)
class Section:
    """Участок трассы - один «лист» (пара листов 1 и 2 карты).

    Ось X участка всегда охватывает ``nominal_length_m``: последний участок может быть
    короче и тогда заполняет ось не до конца (раздел 6 ТЗ, п. 6).
    """

    index: int  # с 1
    start_m: float
    end_m: float
    nominal_length_m: float  # длина из ряда (авто) или фактическая (ручной режим)
    tick_step_m: float
    n_objects: int
    has_overlaps: bool
    plot_width_mm: float  # ширина оси X на бумаге
    closed_end: bool = False  # точка ровно на конце участка принадлежит ему (последний)
    # Краткий список наложений участка: (описание объекта, описание того, на что он
    # наложился или "", дистанция объекта, м). Не больше MAX_CONFLICTS на участок.
    conflicts: tuple[tuple[str, str, float], ...] = ()

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m

    def x_mm(self, dist: float) -> float:
        """Координата дистанции на бумаге, мм, от левого края оси."""
        return (dist - self.start_m) / self.nominal_length_m * self.plot_width_mm


@dataclass(frozen=True, slots=True)
class PlacedObject:
    """Размещённый объект. Все размеры - мм, y = 0 - линия трубы."""

    obj: TrackObject
    zone: str  # placement из notes: above / on / below / cross_line / cross_band / area_below
    x_mm: float  # дистанция начала (для протяжённых - обрезанного по участку)
    x_end_mm: float | None  # конец протяжённого объекта (обрезанный)
    tier: int  # ярус над трубой (0 - нижний); вне зоны «над трубой» всегда 0
    icon_rect: Rect  # прямоугольник значка (по размеру и точке привязки из icons.toml)
    band_rect: Rect | None  # полоса протяжённого объекта (area_below, cross_*, овраг)
    label: str | None  # текст подписи (для ВЛ - напряжение)
    label_rect: Rect | None
    callout_level: int  # 0 - подпись у значка, 1... - поднята на выноске
    overlap: bool  # значок или подпись не удалось разместить без наложения
    start_clipped: bool = False  # начало объекта левее участка
    end_clipped: bool = False  # конец объекта правее участка
    conflict_with: TrackObject | None = None  # с чем наложился (если overlap и это известно)


@dataclass(frozen=True, slots=True)
class SectionPlacement:
    section: Section
    paper: str
    objects: tuple[PlacedObject, ...]
    has_overlaps: bool
    above_extent_mm: float  # наибольшая высота занятого места над трубой
    below_extent_mm: float  # наибольшая глубина занятого места под трубой (положительное)


# --- размеры значков и подписей ---------------------------------------------


@cache
def _geom(type_key: str) -> tuple[float, float, float, float]:
    w, h = icon_size_mm(type_key)
    ax, ay = icon_anchor(type_key)
    return w, h, ax, ay


@lru_cache(maxsize=8)
def _font_props(families: tuple[str, ...]):
    from matplotlib import font_manager

    return font_manager.FontProperties(family=installed_families(families)[0])


@lru_cache(maxsize=4096)
def _char_width_pt(ch: str, pt: float, families: tuple[str, ...]) -> float:
    """Ширина символа (advance) в пунктах по метрикам шрифта."""
    try:
        from matplotlib.textpath import text_to_path

        prop = _font_props(families).copy()
        prop.set_size(pt)
        width, _, _ = text_to_path.get_text_width_height_descent(ch, prop, False)
        if not math.isfinite(width):
            raise ValueError
    except Exception:  # noqa: BLE001 - любая беда с шрифтом: грубая оценка
        width = pt * 0.5
    return float(width)


@lru_cache(maxsize=100_000)
def _text_width_mm(text: str, pt: float, families: tuple[str, ...]) -> float:
    """Ширина строки, мм: сумма ширин символов по метрикам шрифта (без кернинга).

    Целиком строку через TextPath не меряем: это в десятки раз медленнее, а для
    трассы с тысячами подписей размещение вызывается много раз.
    """
    return sum(_char_width_pt(ch, pt, families) for ch in text) * _PT_MM


@lru_cache(maxsize=16)
def _tier_pitch(s: LayoutSettings) -> tuple[float, float]:
    """(шаг ярусов, высота самого высокого значка над трубой), мм.

    Шаг = высота самого высокого значка зоны «над трубой» + зазор + высота строки
    подписи + зазор: подпись нижнего яруса (уровень 0) помещается под значками
    следующего. Значения не зависят от объектов, поэтому одинаковы на всех листах.
    """
    tallest = max(
        (_geom(t.key)[1] * _geom(t.key)[3] for t in OBJECT_TYPES if t.placement == ABOVE),
        default=0.0,
    )
    return tallest + 2 * s.gap_mm + s.font_pt * _PT_MM, tallest


def zone_extents_mm(settings: LayoutSettings | None = None) -> tuple[float, float]:
    """(высота над трубой, глубина под трубой), мм, достаточные для любого листа.

    Нужны этапу 6, чтобы высота полосы «Объекты на трассе» не зависела от листа.
    """
    s = settings or load_settings()
    th = s.font_pt * _PT_MM
    pitch, tallest = _tier_pitch(s)
    callout = (s.callout_levels - 1) * s.callout_step_mm
    above = (s.tiers - 1) * pitch + tallest + s.gap_mm + callout + th
    deepest = max(
        (_geom(t.key)[1] * (1 - _geom(t.key)[3]) for t in OBJECT_TYPES if t.placement != ABOVE),
        default=0.0,
    )
    below = deepest + s.gap_mm + callout + th
    return above, below


# --- выбор объектов участка --------------------------------------------------


def _is_extended(o: TrackObject) -> bool:
    return o.end is not None and o.end > o.start


class _Index:
    """Объекты, отсортированные по началу, с быстрым выбором по окну дистанций."""

    def __init__(self, objects: Iterable[TrackObject]) -> None:
        self.objs = sorted(objects, key=lambda o: (o.start, o.row_start))
        self.starts = [o.start for o in self.objs]
        self.ext = [o for o in self.objs if _is_extended(o)]
        self.ext_starts = [o.start for o in self.ext]
        self.ext_prefix_end: list[float] = []
        top = -math.inf
        for o in self.ext:
            top = max(top, o.end or o.start)
            self.ext_prefix_end.append(top)

    def window(self, a: float, b: float, closed: bool) -> tuple[Iterator[TrackObject], int]:
        """Объекты участка [a, b) (при closed - [a, b]) и их число.

        Протяжённые объекты, начавшиеся левее a и заходящие на участок, идут первыми.
        """
        lo = bisect_left(self.starts, a)
        hi = bisect_right(self.starts, b) if closed else bisect_left(self.starts, b)
        carry: list[TrackObject] = []
        first = bisect_right(self.ext_prefix_end, a)  # раньше конец не доходит до a
        last = bisect_left(self.ext_starts, a)
        for o in islice(self.ext, first, last):
            if o.start < a < (o.end or a):
                carry.append(o)
        return chain(carry, islice(self.objs, lo, hi)), len(carry) + max(hi - lo, 0)

    def reach_mm(self, s: LayoutSettings) -> float:
        """Верхняя оценка выноса прямоугольника влево от точки привязки, мм."""
        longest = max((len(o.label) for o in self.objs), default=0)
        volt = max((len(o.voltage or "") for o in self.objs), default=0)
        label_w = max(longest, volt) * s.font_pt * _PT_MM * 1.2
        icon_w = max((_geom(t.key)[0] for t in OBJECT_TYPES), default=0.0)
        widest = max(
            s.min_extended_width_mm,
            s.min_cross_band_width_mm,
            *(m for t in OBJECT_TYPES if (m := min_width_mm(t.key)) is not None),
        )
        return max(label_w, icon_w, widest) / 2 + s.gap_mm


# --- размещение -------------------------------------------------------------

_CONFLICT = {
    ("i", "i"),
    ("l", "l"),
    ("l", "i"),
    ("i", "l"),
    ("a", "a"),
    ("b", "b"),
    ("i", "j"),
    ("j", "i"),
    ("j", "j"),
    ("o", "o"),
    ("o", "j"),
    ("j", "o"),
    ("l", "o"),
    ("o", "l"),
    ("l", "j"),
    ("j", "l"),
}
MAX_CONFLICTS = 5  # сколько наложений на участок хранится для сообщения


class _Placer:
    """Жадное размещение объектов участка слева направо."""

    def __init__(
        self,
        s: LayoutSettings,
        plot_w: float,
        start_m: float,
        end_m: float,
        nominal_m: float,
        reach_mm: float,
    ) -> None:
        self.s = s
        self.a = start_m
        self.b = end_m
        self.scale = plot_w / nominal_m
        self.plot_w = plot_w
        self.reach = reach_mm
        self.th = s.font_pt * _PT_MM
        self.pitch, _ = _tier_pitch(s)
        self.items: list[tuple[float, float, float, float, str, TrackObject]] = []
        self.owner: TrackObject | None = None  # объект, который размещается сейчас
        self._since_prune = 0
        self.families = s.font_families

    # коллизии
    def _find(self, r: Rect, kind: str, skip_own: bool = False) -> TrackObject | None:
        """Объект, с прямоугольником которого r конфликтует, или None."""
        m = self.s.gap_mm - _EPS
        for x0, y0, x1, y1, k, owner in self.items:
            if (
                (kind, k) in _CONFLICT
                and r.x0 < x1 + m
                and x0 < r.x1 + m
                and r.y0 < y1 + m
                and y0 < r.y1 + m
                and not (skip_own and owner is self.owner)
            ):
                return owner
        return None

    def _hit(self, r: Rect, kind: str) -> bool:
        return self._find(r, kind) is not None

    def _add(self, r: Rect, kind: str) -> None:
        assert self.owner is not None
        self.items.append((r.x0, r.y0, r.x1, r.y1, kind, self.owner))

    def _prune(self, cur_x: float) -> None:
        self._since_prune += 1
        if self._since_prune < 64:
            return
        self._since_prune = 0
        limit = cur_x - self.reach - self.s.gap_mm
        self.items = [it for it in self.items if it[2] >= limit]

    def _label(self, ref: Rect, text: str, cx: float, up: bool) -> tuple[int, Rect] | None:
        """Первый уровень выноски, где подпись не накладывается; None - не нашлось."""
        s = self.s
        half = _text_width_mm(text, s.font_pt, self.families) / 2
        for level in range(s.callout_levels):
            lift = s.gap_mm + level * s.callout_step_mm
            if up:
                r = Rect(cx - half, ref.y1 + lift, cx + half, ref.y1 + lift + self.th)
            else:
                r = Rect(cx - half, ref.y0 - lift - self.th, cx + half, ref.y0 - lift)
            if not self._hit(r, "l"):
                return level, r
        return None

    def _label_fallback(self, ref: Rect, text: str, cx: float, up: bool) -> Rect:
        half = _text_width_mm(text, self.s.font_pt, self.families) / 2
        g = self.s.gap_mm
        if up:
            return Rect(cx - half, ref.y1 + g, cx + half, ref.y1 + g + self.th)
        return Rect(cx - half, ref.y0 - g - self.th, cx + half, ref.y0 - g)

    def place(self, obj: TrackObject) -> PlacedObject:
        s = self.s
        self.owner = obj
        t = get_type(obj.type_key)
        w, h, ax, ay = _geom(obj.type_key)
        scale, a, b = self.scale, self.a, self.b
        ext = _is_extended(obj)
        xs = (max(obj.start, a) - a) * scale
        xe = (max(min(obj.end, b), max(obj.start, a)) - a) * scale if ext and obj.end else None
        self._prune(xs)
        text = (obj.voltage if t.key == "vl" and obj.voltage else obj.label).strip()
        zone = t.placement
        # Наименьшая ширина полосы: у типа своя (icons.toml, min_width_mm), иначе общая -
        # дорога не уже min_cross_band_width_mm, прочие - не уже min_extended_width_mm.
        own_min = min_width_mm(t.key)
        if own_min is not None:
            minw = own_min
        else:
            minw = s.min_cross_band_width_mm if zone == CROSS_BAND else s.min_extended_width_mm
        cx = xs if xe is None else (xs + xe) / 2

        # Полоса: протяжённые «под трубой»/«поперёк», точечные - area_below и cross_band.
        lane: str | None = None
        if ext and zone in (AREA_BELOW, BELOW):
            lane = "a"
        elif ext and zone in (CROSS_BAND, CROSS_LINE):
            lane = "b"
        elif not ext and zone == AREA_BELOW:
            lane = "a"
        elif not ext and zone == CROSS_BAND:
            lane = "b"

        label_rect: Rect | None = None
        level = 0
        overlap = False
        tier = 0
        band: Rect | None = None
        partner: TrackObject | None = None

        if lane is not None:
            if xe is not None:
                width = max(xe - xs, minw)
            else:  # точечный объект: наименьшая ширина типа, а у типа без своей - ширина значка
                width = own_min if own_min is not None else w
            y_lo, y_hi = -(1 - ay) * h, ay * h
            # Полоса не выходит за край листа: сдвигается внутрь, ширина сохраняется.
            x0 = min(max(cx - width / 2, 0.0), max(self.plot_w - width, 0.0))
            cx = x0 + width / 2
            band = Rect(x0, y_lo, x0 + width, y_hi)
            icon = Rect(cx - ax * w, y_lo, cx + (1 - ax) * w, y_hi)
            partner = self._find(band, lane)
            overlap = partner is not None
            self._add(band, lane)
            if text:
                found = self._label(band, text, cx, up=False)
                if found:
                    level, label_rect = found
                else:
                    overlap = True
                    label_rect = self._label_fallback(band, text, cx, up=False)
                    partner = partner or self._find(label_rect, "l", skip_own=True)
                self._add(label_rect, "l")
        else:
            tiers = range(s.tiers) if zone == ABOVE else range(1)
            up = zone in (ABOVE, ON)
            ikind = "i" if zone == ABOVE else "o" if zone == ON else "j"
            chosen: tuple[int, Rect, int, Rect | None] | None = None
            icon_only: tuple[int, Rect] | None = None
            for tr in tiers:
                y_a = tr * self.pitch
                r = Rect(cx - ax * w, y_a - (1 - ay) * h, cx + (1 - ax) * w, y_a + ay * h)
                if self._hit(r, ikind):
                    continue
                if not text:
                    chosen = (tr, r, 0, None)
                    break
                found = self._label(r, text, cx, up)
                if found:
                    chosen = (tr, r, found[0], found[1])
                    break
                if icon_only is None:
                    icon_only = (tr, r)
            if chosen is not None:
                tier, icon, level, label_rect = chosen
            else:
                overlap = True
                if icon_only is not None:
                    tier, icon = icon_only
                else:
                    tier = 0
                    icon = Rect(cx - ax * w, -(1 - ay) * h, cx + (1 - ax) * w, ay * h)
                partner = self._find(icon, ikind)
                if text:
                    label_rect = self._label_fallback(icon, text, cx, up)
                    partner = partner or self._find(label_rect, "l")
            self._add(icon, ikind)
            if label_rect is not None:
                self._add(label_rect, "l")

        return PlacedObject(
            obj=obj,
            zone=zone,
            x_mm=xs,
            x_end_mm=xe,
            tier=tier,
            icon_rect=icon,
            band_rect=band,
            label=text or None,
            label_rect=label_rect,
            callout_level=level,
            overlap=overlap,
            start_clipped=obj.start < a,
            end_clipped=bool(ext and obj.end is not None and obj.end > b),
            conflict_with=partner if overlap else None,
        )


def _run(
    index: _Index,
    s: LayoutSettings,
    plot_w: float,
    a: float,
    b: float,
    nominal: float,
    closed: bool,
    reach: float,
    stop_on_overlap: bool,
    allowed: frozenset[int] | None = None,
) -> tuple[list[PlacedObject], int, bool]:
    """Размещает объекты окна. При stop_on_overlap останавливается на первом наложении.

    ``allowed`` - id объектов, чьё наложение не считается препятствием (неустранимые).
    """
    objs, count = index.window(a, b, closed)
    placer = _Placer(s, plot_w, a, b, nominal, reach)
    placed: list[PlacedObject] = []
    overlaps = False
    for obj in objs:
        p = placer.place(obj)
        placed.append(p)
        if p.overlap:
            overlaps = True
            if stop_on_overlap and (allowed is None or id(p.obj) not in allowed):
                break
    return placed, count, overlaps


def _describe(obj: TrackObject) -> str:
    title = get_type(obj.type_key).title
    label = (obj.voltage if obj.type_key == "vl" and obj.voltage else obj.label).strip()
    return f"{title} «{label}»" if label else title


def _conflicts(placed: Sequence[PlacedObject]) -> tuple[tuple[str, str, float], ...]:
    """Первые MAX_CONFLICTS наложений участка: (объект, с чем наложился, дистанция, м)."""
    found: list[tuple[str, str, float]] = []
    for p in placed:
        if p.overlap:
            other = _describe(p.conflict_with) if p.conflict_with is not None else ""
            found.append((_describe(p.obj), other, p.obj.start))
            if len(found) >= MAX_CONFLICTS:
                break
    return tuple(found)


def _extents(placed: Sequence[PlacedObject]) -> tuple[float, float]:
    top = bottom = 0.0
    for p in placed:
        for r in (p.icon_rect, p.band_rect, p.label_rect):
            if r is not None:
                top = max(top, r.y1)
                bottom = min(bottom, r.y0)
    return top, -bottom


def place_objects(
    objects: Iterable[TrackObject],
    section: Section,
    settings: LayoutSettings | None = None,
    paper: str = "A4",
) -> SectionPlacement:
    """Геометрия значков и подписей участка (протяжённые объекты обрезаются по границам)."""
    s = settings or load_settings()
    index = _Index(objects)
    plot_w = s.plot_width_mm(paper)
    placed, _, overlaps = _run(
        index,
        s,
        plot_w,
        section.start_m,
        section.end_m,
        section.nominal_length_m,
        section.closed_end,
        index.reach_mm(s),
        stop_on_overlap=False,
    )
    top, bottom = _extents(placed)
    return SectionPlacement(section, paper, tuple(placed), overlaps, top, bottom)


# --- деления оси --------------------------------------------------------------


def tick_positions(section: Section) -> list[float]:
    """Дистанции делений оси, м: start + k * шаг, не дальше конца участка."""
    step = section.tick_step_m
    count = int(math.floor((section.length_m + _EPS) / step))
    return [round(section.start_m + k * step, 6) for k in range(count + 1)]


# --- результат разбиения -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SplitResult:
    sections: tuple[Section, ...]
    issues: tuple[Issue, ...]
    mode: str  # "auto" или "manual"
    paper: str = "A4"
    start_km: float = 0.0

    @property
    def has_overlaps(self) -> bool:
        return any(s.has_overlaps for s in self.sections)

    def table(self, start_km: float | None = None) -> list[tuple[str, ...]]:
        """Строки таблицы: лист, от, до (км), длина (км), число объектов, наложения."""
        base = self.start_km if start_km is None else start_km
        return [
            (
                str(s.index),
                format_km(base + s.start_m / 1000),
                format_km(base + s.end_m / 1000),
                format_km(s.length_m / 1000),
                str(s.n_objects),
                "да" if s.has_overlaps else "нет",
            )
            for s in self.sections
        ]

    def format_text(self, start_km: float | None = None) -> str:
        """Таблица разбиения для консоли и страницы проверки."""
        title = "автоматическое" if self.mode == MODE_AUTO else "ручное"
        rows = [TABLE_HEADER, *self.table(start_km)]
        widths = [max(len(r[i]) for r in rows) for i in range(len(TABLE_HEADER))]
        lines = [f"Разбиение на листы ({title}, {self.paper}, участков: {len(self.sections)}):"]
        for r in rows:
            lines.append("  " + " | ".join(c.rjust(w) for c, w in zip(r, widths, strict=True)))
        return "\n".join(lines)


def _km(start_km: float, dist_m: float) -> str:
    return format_km(start_km + dist_m / 1000)


def _list_numbers(numbers: Sequence[int], limit: int = 20) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    rest = len(numbers) - limit
    return shown + (f" и ещё {rest}" if rest > 0 else "")


def _overlap_issue(sections: Sequence[Section], start_km: float) -> list[Issue]:
    bad = [s for s in sections if s.has_overlaps]
    if not bad:
        return []
    first = bad[0]
    details: list[str] = []
    for sec in bad:
        for name, other, dist in sec.conflicts:
            if len(details) >= MAX_CONFLICTS:
                break
            pair = f"{name} и {other}" if other else name
            details.append(f"лист {sec.index}: {pair} на км {_km(start_km, dist)}")
    what = f" Что наложилось: {'; '.join(details)}." if details else ""
    return [
        Issue(
            WARNING,
            "layout_overlap",
            f"На листах значки или подписи накладываются друг на друга: "
            f"{_list_numbers([s.index for s in bad])} (всего {len(bad)} из {len(sections)}; "
            f"первый - лист {first.index}, км {_km(start_km, first.start_m)} - "
            f"{_km(start_km, first.end_m)}).{what} "
            "Уменьшите длину листа или поправьте границы.",
        )
    ]


# --- ручной режим -----------------------------------------------------------

_NUM_RX = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def _to_number(token: str) -> float:
    tok = token.strip().replace(" ", "").replace(" ", "")
    if not _NUM_RX.match(tok):
        raise LayoutError(f"Не число в разбиении на листы: «{token.strip()}».")
    return float(tok.replace(",", "."))


def parse_split_spec(text: str) -> float | tuple[float, ...]:
    """Разбор строки формы: «5000» - длина листа, «0; 3000; 5000» - границы, м.

    Разделитель границ - точка с запятой; десятичная запятая допустима.
    """
    parts = [p for p in str(text).split(";")]
    if all(not p.strip() for p in parts):
        raise LayoutError("Не задано разбиение на листы: нужна длина листа или список границ.")
    parts = [p for p in parts if p.strip()]  # хвостовая «;» допустима
    values = [_to_number(p) for p in parts]
    if len(values) == 1:
        return values[0]
    return tuple(values)


def _make_section(
    index: int,
    start: float,
    end: float,
    nominal: float,
    tick: float,
    closed: bool,
    plot_w: float,
    idx: _Index | None,
    s: LayoutSettings,
    reach: float,
) -> Section:
    n, overlaps, conflicts = 0, False, ()
    if idx is not None:
        placed, n, overlaps = _run(idx, s, plot_w, start, end, nominal, closed, reach, False)
        conflicts = _conflicts(placed)
    return Section(index, start, end, nominal, tick, n, overlaps, plot_w, closed, conflicts)


def split_manual(
    start_m: float,
    end_m: float,
    spec: float | str | Sequence[float],
    settings: LayoutSettings | None = None,
    objects: Iterable[TrackObject] = (),
    paper: str = "A4",
    *,
    start_km: float = 0.0,
) -> SplitResult:
    """Ручное разбиение: одна длина листа или список границ (соблюдаются точно).

    Если переданы объекты, для каждого участка считаются число объектов и наложения.
    """
    s = settings or load_settings()
    plot_w = s.plot_width_mm(paper)
    if isinstance(spec, str):
        spec = parse_split_spec(spec)
    objs = list(objects)
    idx = _Index(objs) if objs else None
    reach = idx.reach_mm(s) if idx else 0.0
    issues: list[Issue] = []
    sections: list[Section] = []

    def too_many(count: int, hint: bool) -> LayoutError:
        text = f"Разбиение даёт {count} участков, предел - {s.max_sections}."
        if hint:
            need = math.ceil((end_m - start_m) / s.max_sections)
            text += f" Увеличьте длину листа (не меньше {need} м)."
        return LayoutError(text)

    if isinstance(spec, int | float):
        length = float(spec)
        if not math.isfinite(length) or length <= 0:
            raise LayoutError("Длина листа должна быть больше нуля.")
        total = max(end_m - start_m, 0.0)
        count = max(math.ceil(total / length - 1e-9), 1)
        if count > s.max_sections:
            raise too_many(count, hint=True)
        tick = nice_step(length)
        for i in range(count):
            a = start_m + i * length
            last = i == count - 1
            b = end_m if last else a + length
            sections.append(
                _make_section(i + 1, a, max(b, a), length, tick, last, plot_w, idx, s, reach)
            )
    else:
        bounds = [float(v) for v in spec]
        if len(bounds) < 2:
            raise LayoutError("Для разбиения нужно не меньше двух границ.")
        if any(not math.isfinite(v) for v in bounds):
            raise LayoutError("Границы листов должны быть конечными числами.")
        for prev, cur in zip(bounds, bounds[1:], strict=False):
            if cur <= prev:
                raise LayoutError(f"Границы листов должны возрастать: {prev:g} -> {cur:g}.")
        count = len(bounds) - 1
        if count > s.max_sections:
            raise too_many(count, hint=False)
        for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
            sections.append(
                _make_section(
                    i + 1, a, b, b - a, nice_step(b - a), i == count - 1, plot_w, idx, s, reach
                )
            )
        if bounds[0] > start_m + _EPS or bounds[-1] < end_m - _EPS:
            issues.append(
                Issue(
                    WARNING,
                    "layout_not_covered",
                    f"Границы листов ({_km(start_km, bounds[0])} - {_km(start_km, bounds[-1])} км) "
                    f"не покрывают всю трассу ({_km(start_km, start_m)} - "
                    f"{_km(start_km, end_m)} км): остальное на листы не попадёт.",
                )
            )
    issues += _overlap_issue(sections, start_km)
    return SplitResult(tuple(sections), tuple(issues), MODE_MANUAL, paper, start_km)


# --- автоматический режим ----------------------------------------------------


def _auto_pass(
    idx: _Index,
    s: LayoutSettings,
    plot_w: float,
    start_m: float,
    end_m: float,
    i_min: int,
    i_max: int,
    reach: float,
    limit: int | None,
) -> list[Section] | None:
    """Один проход разбиения с наименьшей длиной lengths[i_min].

    При limit проход прерывается (None), как только участков стало больше limit.

    Неустранимые наложения (объекты с наложением уже при наименьшей длине) не мешают
    выбрать крупный масштаб; любое другое наложение - мешает. Множество неустранимых
    считается один раз на проход: одним размещением всей трассы в масштабе наименьшей
    длины (раскладка слева направо зависит от начала участка лишь вблизи его границы).
    """
    lengths = s.lengths_m
    unavoidable = frozenset(
        id(p.obj)
        for p in _run(idx, s, plot_w, start_m, end_m, lengths[i_min], True, reach, False)[0]
        if p.overlap
    )
    sections: list[Section] = []
    a = start_m
    while True:
        for i in range(i_max, i_min - 1, -1):
            nominal = lengths[i]
            last = a + nominal >= end_m - _EPS
            b = max(end_m, a) if last else a + nominal
            smallest = i == i_min
            placed, count, overlaps = _run(
                idx, s, plot_w, a, b, nominal, last, reach, not smallest, unavoidable
            )
            blocked = bool(
                not smallest
                and placed
                and placed[-1].overlap
                and id(placed[-1].obj) not in unavoidable
            )
            if smallest or not blocked:
                break
        if last:
            # Последний участок: номинал - наименьшая длина из ряда, не меньше остатка
            # трассы (и не меньше наименьшей допустимой). Масштаб крупнее - размещение
            # пересчитывается для итогового номинала.
            floor = max(b - a, lengths[i_min])
            final = next((v for v in lengths[i_min : i_max + 1] if v >= floor - _EPS), nominal)
            if final < nominal:
                nominal = final
                placed, count, overlaps = _run(idx, s, plot_w, a, b, nominal, True, reach, False)
        tick = s.tick_step_m(nominal) or nice_step(nominal)
        sections.append(
            Section(
                len(sections) + 1,
                a,
                b,
                nominal,
                tick,
                count,
                overlaps,
                plot_w,
                last,
                _conflicts(placed),
            )
        )
        if last:
            return sections
        if limit is not None and len(sections) > limit:
            return None
        a = b


def split_auto(
    objects: Iterable[TrackObject],
    start_m: float,
    end_m: float,
    settings: LayoutSettings | None = None,
    paper: str = "A4",
    *,
    start_km: float = 0.0,
) -> SplitResult:
    """Автоматическое разбиение по разделу 6 ТЗ.

    От текущей точки берётся самая большая длина из ряда (не больше наибольшей из
    настроек), при которой участок размещается без наложений сверх неустранимых (тех,
    что остаются и при наименьшей длине); иначе меньшая; при наименьшей участок
    строится с наложением. Если участков больше предела,
    наименьшая длина листа повышается до следующей из ряда, и разбиение повторяется.
    """
    s = settings or load_settings()
    plot_w = s.plot_width_mm(paper)
    lengths = s.lengths_m
    i_max = lengths.index(round(s.auto_max_length_km * 1000, 6))
    i_min0 = lengths.index(round(s.auto_min_length_km * 1000, 6))
    idx = _Index(objects)
    reach = idx.reach_mm(s)
    issues: list[Issue] = []

    sections: list[Section] = []
    i_min = i_min0
    for i_min in range(i_min0, i_max + 1):
        limit = s.max_sections if i_min < i_max else None
        result = _auto_pass(idx, s, plot_w, start_m, end_m, i_min, i_max, reach, limit)
        if result is not None:
            sections = result
            break
    if i_min > i_min0:
        issues.append(
            Issue(
                WARNING,
                "layout_min_scale_raised",
                f"Участков получалось больше предела ({s.max_sections}): наименьшая длина "
                f"листа повышена с {_fmt_len(lengths[i_min0])} до {_fmt_len(lengths[i_min])} км.",
            )
        )
    if len(sections) > s.max_sections:
        issues.append(
            Issue(
                WARNING,
                "layout_too_many_sections",
                f"Участков {len(sections)}, предел - {s.max_sections}: трасса слишком длинная "
                "для автоматического разбиения.",
            )
        )
    issues += _overlap_issue(sections, start_km)
    return SplitResult(tuple(sections), tuple(issues), MODE_AUTO, paper, start_km)


def _fmt_len(length_m: float) -> str:
    return f"{length_m / 1000:g}".replace(".", ",")

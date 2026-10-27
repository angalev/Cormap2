"""Лист «Условные обозначения» (ТЗ, раздел 7; образец - таблица в приложении 11).

Порядок строк, названия и разбиение на группы взяты из таблицы условных
обозначений образца (`reference/prilozhenie_11_obrazec.docx`): сначала
диагностируемый трубопровод и объекты новой легенды (две пары колонок, строки
слева направо), затем «Типы грунтов» и «Типы АКП». Значки старого словаря
вынесены отдельным блоком с пометкой, что обозначения временные.

Модуль не зависит от Django. Возвращает `matplotlib.figure.Figure`; сохранение в
PDF/PNG делает вызывающий код (`fig.savefig(...)`).
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PIL import Image

from cormap import assets
from cormap.fonts import installed_families
from cormap.model import normalize_text
from cormap.notes import LEGACY, OBJECT_TYPES, get_type

# Шрифт листов: Times New Roman, на сервере - метрически совместимый Liberation Serif.
FONT_FAMILY = ["Times New Roman", "Liberation Serif", "DejaVu Serif"]

PAPERS_MM = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}  # альбомная ориентация

TITLE = "Условные обозначения"
LEGACY_TITLE = "Прочие объекты (временные обозначения)"
SOIL_TITLE = "Типы грунтов"
INSULATION_TITLE = "Типы АКП"
PIPELINE = "pipeline"  # строка «Диагностируемый трубопровод» (значка-файла нет, рисуется линией)

# Строки основной таблицы в порядке образца и их подписи из образца.
MAIN_ROWS: tuple[tuple[str, str], ...] = (
    (PIPELINE, "Диагностируемый трубопровод"),
    ("vl", "Высоковольтная линия"),
    ("cable", "Кабельная линия"),
    ("oil_pipe", "Нефтепровод, нефтепродуктопровод"),
    ("water_pipe", "Водопровод"),
    ("gas_pipe", "Газопровод"),
    ("road", "Автомобильная дорога"),
    ("rail", "Железная дорога"),
    ("crossing", "Переезд"),
    ("water", "Водная преграда"),
    ("swamp", "Болото"),
    ("forest", "Лес, кусты (заросли)"),
    ("vei", "ВЭИ (ИФС)"),
    ("kip", "Контрольно-измерительный пункт"),
    ("crane", "Крановый узел"),
    ("kzou", "КЗОУ, КПОУ"),
    ("ukz", "Станция катодной защиты"),
    ("udz", "Станция дренажной защиты"),
    ("pip", "Повреждение ИП"),
)

# Геометрия таблицы, мм.
MARGIN = 10.0
HEAD_H = 9.0  # строка заголовка основной таблицы
MIN_ROW_H = 8.0
ICON_PAD = 3.0
ICON_COL = 30.0  # колонка значка в основной таблице
SWATCH = 11.0  # сторона образца текстуры
RIGHT_W = 87.0  # ширина правой панели
GAP = 6.0


@dataclass(frozen=True, slots=True)
class Entry:
    """Строка легенды: значок (key), линия трубопровода или образец текстуры (tile)."""

    kind: str  # "icon" | "pipe" | "tile"
    title: str
    key: str | None = None  # ключ типа для kind == "icon"
    tile: str | None = None  # путь к плитке для kind == "tile"


@dataclass(slots=True)
class LegendEntries:
    main: list[Entry] = field(default_factory=list)
    legacy: list[Entry] = field(default_factory=list)
    soil: list[Entry] = field(default_factory=list)
    insulation: list[Entry] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.main) + len(self.legacy) + len(self.soil) + len(self.insulation)


def _tiles(
    names: Iterable[object] | None,
    table: dict[str, tuple[str, str]],
    texture,
    title,
) -> list[Entry]:
    if names is None:
        wanted: list[object] = list(table)
    else:
        seen = {normalize_text(n) for n in names if n is not None and str(n).strip()}
        wanted = [n for n in table if n in seen]  # известные - в порядке образца
        wanted += sorted(seen - set(table))  # неизвестные - серой штриховкой
    return [Entry("tile", title(n), tile=str(texture(n))) for n in wanted]


def legend_entries(
    used_type_keys: Iterable[str] | None = None,
    used_soil: Iterable[object] | None = None,
    used_insulation: Iterable[object] | None = None,
) -> LegendEntries:
    """Состав листа. None - показать всё; иначе только перечисленные."""
    used = None if used_type_keys is None else set(used_type_keys)
    if used is not None:
        for k in used:
            get_type(k)  # неизвестный ключ - KeyError
    out = LegendEntries()
    for key, title in MAIN_ROWS:
        if key == PIPELINE:
            out.main.append(Entry("pipe", title))
        elif used is None or key in used:
            out.main.append(Entry("icon", title, key=key))
    listed = {k for k, _ in MAIN_ROWS}
    for t in OBJECT_TYPES:  # типы новой легенды, которых нет в образце (на будущее)
        if t.group != LEGACY and t.key not in listed and (used is None or t.key in used):
            out.main.append(Entry("icon", t.title, key=t.key))
    for t in OBJECT_TYPES:
        if t.group == LEGACY and (used is None or t.key in used):
            out.legacy.append(Entry("icon", t.title, key=t.key))
    out.soil = _tiles(used_soil, assets.SOIL, assets.soil_texture, assets.soil_title)
    out.insulation = _tiles(
        used_insulation, assets.INSULATION, assets.insulation_texture, assets.insulation_title
    )
    return out


# ------------------------------------------------------------------------- рисование


class _Canvas:
    """Рисование в миллиметрах бумаги: ось y направлена вниз."""

    def __init__(self, paper: str) -> None:
        try:
            self.w, self.h = PAPERS_MM[paper.upper()]
        except KeyError:
            raise ValueError(f"неизвестный формат бумаги «{paper}»; допустимо A4 или A3") from None
        self.fig = Figure(figsize=(self.w / 25.4, self.h / 25.4), facecolor="white")
        self.ax = self.fig.add_axes((0, 0, 1, 1))
        self.ax.set_xlim(0, self.w)
        self.ax.set_ylim(self.h, 0)
        self.ax.set_aspect("equal")
        self.ax.axis("off")
        self._cache: dict[str, np.ndarray] = {}

    def text(self, x, y, s, size=10, bold=False, ha="left", va="center") -> None:
        self.ax.text(
            x,
            y,
            s,
            fontsize=size,
            fontfamily=list(installed_families(tuple(FONT_FAMILY))),
            fontweight="bold" if bold else "normal",
            ha=ha,
            va=va,
            color="black",
            linespacing=1.05,
        )

    def line(self, x0, y0, x1, y1, lw=0.6, color="black") -> None:
        self.ax.plot([x0, x1], [y0, y1], color=color, lw=lw, solid_capstyle="butt")

    def rect(self, x, y, w, h, lw=0.6) -> None:
        self.ax.add_patch(Rectangle((x, y), w, h, fill=False, edgecolor="black", linewidth=lw))

    def image(self, path: str, x: float, y: float, w: float, h: float, crop: int | None = None):
        key = f"{path}|{crop}"
        arr = self._cache.get(key)
        if arr is None:
            im = Image.open(path).convert("RGBA")
            if crop:
                im = im.crop((0, 0, crop, crop))
            arr = np.asarray(im)
            self._cache[key] = arr
        self.ax.imshow(
            arr,
            extent=(x, x + w, y + h, y),
            interpolation="antialiased",
            aspect="equal",
            zorder=2,
        )
        self.ax.set_xlim(0, self.w)
        self.ax.set_ylim(self.h, 0)

    def icon(self, key: str, cx: float, cy: float) -> None:
        w, h = assets.icon_size_mm(key)
        self.image(str(assets.icon_png(key)), cx - w / 2, cy - h / 2, w, h)


def _wrap(s: str, width: int) -> str:
    return textwrap.fill(s, width=width, break_long_words=False)


def _row_heights(entries: list[Entry], cols: int, min_h: float) -> list[float]:
    rows = []
    for i in range(0, len(entries), cols):
        h = min_h
        for e in entries[i : i + cols]:
            if e.kind == "icon" and e.key:
                h = max(h, assets.icon_size_mm(e.key)[1] + ICON_PAD)
            elif e.kind == "tile":
                h = max(h, SWATCH + 2.0)
        rows.append(h)
    return rows


def _draw_cell_symbol(c: _Canvas, e: Entry, cx: float, cy: float, col_w: float) -> None:
    if e.kind == "icon" and e.key:
        c.icon(e.key, cx, cy)
    elif e.kind == "pipe":
        half = min(col_w * 0.35, 9.0)
        c.line(cx - half, cy, cx + half, cy, lw=2.4, color="#222222")
    elif e.kind == "tile" and e.tile:
        c.image(e.tile, cx - SWATCH / 2, cy - SWATCH / 2, SWATCH, SWATCH, crop=256)
        c.rect(cx - SWATCH / 2, cy - SWATCH / 2, SWATCH, SWATCH, lw=0.6)


def _draw_main_table(c: _Canvas, x: float, y: float, width: float, entries: list[Entry]) -> float:
    """Две пары колонок «значок - название»; возвращает нижнюю границу таблицы."""
    pair_w = width / 2
    name_w = pair_w - ICON_COL
    heights = _row_heights(entries, 2, MIN_ROW_H)
    total = HEAD_H + sum(heights)
    c.rect(x, y, width, total)
    c.line(x + pair_w, y, x + pair_w, y + total)
    for p in (0, 1):
        px = x + p * pair_w
        c.line(px + ICON_COL, y, px + ICON_COL, y + total)
        c.text(px + ICON_COL / 2, y + HEAD_H / 2, "Графическое\nобозначение", 9, True, "center")
        c.text(
            px + ICON_COL + name_w / 2,
            y + HEAD_H / 2,
            "Наименование\nграфического объекта",
            9,
            True,
            "center",
        )
    yy = y + HEAD_H
    c.line(x, yy, x + width, yy)
    for r, h in enumerate(heights):
        for p in (0, 1):
            i = r * 2 + p
            if i >= len(entries):
                break
            e = entries[i]
            px = x + p * pair_w
            _draw_cell_symbol(c, e, px + ICON_COL / 2, yy + h / 2, ICON_COL)
            c.text(px + ICON_COL + 2.5, yy + h / 2, _wrap(e.title, max(10, int(name_w / 1.75))), 10)
        yy += h
        if r < len(heights) - 1:
            c.line(x, yy, x + width, yy)
    return y + total


def _draw_block(
    c: _Canvas, x: float, y: float, width: float, title: str, entries: list[Entry]
) -> float:
    """Блок с заголовком: ячейки «значок - название» в две колонки."""
    pair_w = width / 2
    sym_w = 15.0
    heights = _row_heights(entries, 2, 8.5)
    total = 8.0 + sum(heights)
    c.rect(x, y, width, total)
    c.text(x + width / 2, y + 4.0, title, 10.5, True, "center")
    yy = y + 8.0
    c.line(x, yy, x + width, yy)
    c.line(x + pair_w, yy, x + pair_w, y + total)
    for r, h in enumerate(heights):
        for p in (0, 1):
            i = r * 2 + p
            if i >= len(entries):
                break
            e = entries[i]
            px = x + p * pair_w
            _draw_cell_symbol(c, e, px + sym_w / 2, yy + h / 2, sym_w)
            c.text(
                px + sym_w + 1.0, yy + h / 2, _wrap(e.title, max(8, int((pair_w - sym_w) / 1.7))), 9
            )
        yy += h
        if r < len(heights) - 1:
            c.line(x, yy, x + width, yy)
    return y + total


def render_legend(
    used_type_keys: Iterable[str] | None = None,
    used_soil: Iterable[object] | None = None,
    used_insulation: Iterable[object] | None = None,
    paper: str = "A4",
) -> Figure:
    """Лист «Условные обозначения» (альбомный A4 или A3).

    Аргументы None - показать все обозначения соответствующей группы; список -
    только перечисленные (режим «только встретившиеся на карте»). Трубопровод
    показывается всегда.
    """
    ent = legend_entries(used_type_keys, used_soil, used_insulation)
    c = _Canvas(paper)
    c.text(c.w / 2, MARGIN + 3.0, TITLE, 16, True, "center")
    top = MARGIN + 13.0
    has_right = bool(ent.legacy or ent.soil or ent.insulation)
    main_w = c.w - 2 * MARGIN - (RIGHT_W + GAP if has_right else 0)
    _draw_main_table(c, MARGIN, top, main_w, ent.main)
    if has_right:
        x = c.w - MARGIN - RIGHT_W
        y = top
        for title, items in (
            (SOIL_TITLE, ent.soil),
            (INSULATION_TITLE, ent.insulation),
            (LEGACY_TITLE, ent.legacy),
        ):
            if items:
                y = _draw_block(c, x, y, RIGHT_W, title, items) + GAP
    return c.fig

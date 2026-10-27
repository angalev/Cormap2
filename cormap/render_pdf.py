"""Листы карты для PDF и PNG (ТЗ, разделы 5 и 8; образец - reference/obrazec_list*.png).

Лист - это ``matplotlib.figure.Figure`` точно размера бумаги (A4 или A3 альбомная).
Используется только объектный API (без pyplot и глобального состояния), поэтому код
безопасно работает в фоновом обработчике. Файлы пишет вызывающий код (build.py).

Геометрия
---------
Рамки, подписи и легенды рисуются в «листе миллиметров» (оверлей на всю фигуру,
начало координат в левом верхнем углу). Ось X каждой полосы - отдельные axes ровно
шириной ``plot_width_mm`` из layout.toml; пределы оси - в метрах трассы
``[section.start_m, section.start_m + section.nominal_length_m]``, поэтому
``Section.x_mm(dist)`` совпадает с положением дистанции на бумаге. В полосе «Объекты
на трассе» ось Y - миллиметры (труба на нуле, вверх плюс), как в ``layout.py``.

Данные не изменяются: никакого обнуления потенциалов на переходах (ТЗ, раздел 15).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO

import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon, Rectangle
from matplotlib.textpath import text_to_path
from PIL import Image

from cormap import assets
from cormap.defects import (
    DefectSegment,
    collect_defects,
    defect_objects,
    load_criteria,
    pip_marks,
    strip_steps,
)
from cormap.fonts import installed_families
from cormap.layout import (
    PlacedObject,
    Section,
    SectionPlacement,
    format_km,
    tick_positions,
    zone_extents_mm,
)
from cormap.layout_settings import LayoutSettings, load_settings
from cormap.model import Issue, Survey, type_intervals
from cormap.notes import (
    ABOVE,
    AREA_BELOW,
    BELOW,
    CROSS_BAND,
    CROSS_LINE,
    ON,
    TrackObject,
)
from cormap.params import BuildParams
from cormap.render_settings import RenderSettings, load_render_settings

__all__ = [
    "PNG_DPI",
    "STRIP_INSULATION",
    "STRIP_OBJECTS",
    "STRIP_SOIL",
    "Sheet2Heights",
    "SheetContext",
    "decimate_minmax",
    "defect_objects",
    "make_context",
    "render_sheet1",
    "render_sheet2",
    "render_strip_png",
    "sheet1_heights",
    "sheet2_heights",
    "strip_image_size_mm",
]

PNG_DPI = 300
MM = 25.4
_PT_MM = MM / 72.0

TITLE_POTENTIAL = "Потенциал трубопровода в грунте, В"
TITLE_GRADIENT = "Градиент потенциала, мВ"
TITLE_DEFECTS = "Площадь дефекта"
TITLE_ZONES = "Зоны коррозионной опасности"
TITLE_INSULATION = "Тип изоляции"
TITLE_DEPTH = "Глубина залегания трубопровода, м"
TITLE_RESISTIVITY = "Удельное электрическое сопротивление грунта, Ом·м"
TITLE_SOIL = "Тип грунта"
TITLE_OBJECTS = "Объекты на трассе"
CAPTION_LENGTH = "Протяженность, км"
NO_DATA = "нет данных"
DEFECTS_NOTE = (
    "1 - мелкий (превышение сигнала до 2 раз),  2 - средний (превышение сигнала от 2 до 4 раз), "
    "3 - крупный (превышение сигнала 4 и выше раз)"
)
ZONES_NOTE = "0 - УКО, 1 - ВКО"


# --- прореживание длинных линий ----------------------------------------------


def decimate_minmax(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float | None] | np.ndarray,
    n_bins: int,
    x_range: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Прореживание линии с сохранением минимумов и максимумов.

    Ось делится на ``n_bins`` бинов (пиксельных колонок); в каждом бине остаются точки
    минимума и максимума в исходном порядке. Пропуск (None или NaN в ``y``) рвёт линию:
    бины считаются отдельно по обе стороны разрыва, а в результат вставляется NaN.
    Ряд короче ``4 * n_bins`` точек возвращается без изменений. ``x`` должен не убывать.
    ``x_range`` - границы осей для бинов (по умолчанию первая и последняя точка).
    Возвращает (x, y); пропуски в ``y`` представлены NaN.
    """
    if n_bins < 1:
        raise ValueError("Число бинов должно быть не меньше 1.")
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)  # None -> NaN
    if xa.shape != ya.shape:
        raise ValueError("x и y должны быть одной длины.")
    if xa.size <= 4 * n_bins:
        return xa, ya
    lo, hi = x_range if x_range is not None else (float(xa[0]), float(xa[-1]))
    valid = ~np.isnan(ya)
    vi = np.flatnonzero(valid)
    if vi.size == 0:
        return xa[:0], ya[:0]
    if hi > lo:
        bins = np.clip(((xa - lo) / (hi - lo) * n_bins).astype(np.int64), 0, n_bins - 1)
    else:
        bins = np.zeros(xa.shape, dtype=np.int64)
    seg = np.cumsum(~valid)  # номер участка между разрывами
    key = seg[vi] * n_bins + bins[vi]
    new_group = np.empty(vi.size, dtype=bool)
    new_group[0] = True
    new_group[1:] = key[1:] != key[:-1]
    starts = np.flatnonzero(new_group)
    gid = np.cumsum(new_group) - 1
    yv = ya[vi]
    keep: list[np.ndarray] = []
    for pick in (
        yv == np.minimum.reduceat(yv, starts)[gid],
        yv == np.maximum.reduceat(yv, starts)[gid],
    ):
        idx = np.flatnonzero(pick)
        g = gid[idx]
        first = np.empty(idx.size, dtype=bool)
        first[0] = True
        first[1:] = g[1:] != g[:-1]
        keep.append(idx[first])
    pos = vi[np.unique(np.concatenate(keep))]
    xo, yo = xa[pos], ya[pos]
    seg_k = seg[pos]
    breaks = np.flatnonzero(seg_k[1:] != seg_k[:-1]) + 1
    if breaks.size:
        xo = np.insert(xo, breaks, xo[breaks - 1])
        yo = np.insert(yo, breaks, np.nan)
    return xo, yo


# --- данные листа ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Series:
    """Ряд для графика: дистанции (по возрастанию) и значения, пропуск - NaN."""

    x: np.ndarray
    y: np.ndarray

    @classmethod
    def of(cls, dist: Sequence[float], values: Sequence[float | None]) -> Series:
        return cls(np.asarray(dist, dtype=float), np.asarray(values, dtype=float))

    def __len__(self) -> int:
        return int(self.x.size)


@dataclass(frozen=True, slots=True)
class Window:
    """Часть ряда для участка: точки диапазона и по одной соседней с каждой стороны."""

    x: np.ndarray
    y: np.ndarray
    inside: int  # число значений (не NaN) внутри участка
    left_ok: bool  # слева есть соседняя точка со значением
    right_ok: bool

    @property
    def has_data(self) -> bool:
        """Есть что рисовать: значения внутри участка или линия, проходящая сквозь него."""
        return self.inside > 0 or (self.left_ok and self.right_ok)


def window(series: Series, a: float, b: float, n_bins: int) -> Window:
    """Точки ряда участка [a, b]: бинарный поиск, соседи, прореживание длинных рядов."""
    x, y = series.x, series.y
    i0 = int(np.searchsorted(x, a, side="left"))
    i1 = int(np.searchsorted(x, b, side="right"))
    xi, yi = x[i0:i1], y[i0:i1]
    inside = int(np.count_nonzero(~np.isnan(yi)))
    if xi.size > 4 * n_bins:
        xi, yi = decimate_minmax(xi, yi, n_bins, (a, b))
    left_ok = i0 > 0 and not math.isnan(float(y[i0 - 1]))
    right_ok = i1 < x.size and not math.isnan(float(y[i1]))
    if i0 > 0:
        xi, yi = np.concatenate(([x[i0 - 1]], xi)), np.concatenate(([y[i0 - 1]], yi))
    if i1 < x.size:
        xi, yi = np.concatenate((xi, [x[i1]])), np.concatenate((yi, [y[i1]]))
    return Window(xi, yi, inside, left_ok, right_ok)


@dataclass(slots=True)
class SurveyData:
    """Ряды книги в виде массивов numpy (строятся один раз на всю карту)."""

    u_on: Series
    u_off: Series
    grad_left_on: Series
    grad_left_off: Series
    grad_right_on: Series
    grad_right_off: Series
    depth: Series
    resistivity: Series
    soil_marks: list[tuple[float, str]]
    insulation_marks: list[tuple[float, str]]

    @classmethod
    def of(cls, survey: Survey) -> SurveyData:
        iv, g = survey.intensive, survey.gradients
        return cls(
            u_on=Series.of(iv.dist, iv.u_on),
            u_off=Series.of(iv.dist, iv.u_off),
            grad_left_on=Series.of(g.dist, g.left_on),
            grad_left_off=Series.of(g.dist, g.left_off),
            grad_right_on=Series.of(g.dist, g.right_on),
            grad_right_off=Series.of(g.dist, g.right_off),
            depth=Series.of(survey.depth.dist, survey.depth.depth),
            resistivity=Series.of(survey.soil.dist, survey.soil.resistivity),
            soil_marks=[(m.dist, m.value) for m in survey.soil.types],
            insulation_marks=[(m.dist, m.value) for m in survey.insulation.types],
        )


@dataclass(slots=True)
class SheetContext:
    """Всё, что нужно листам, кроме самого участка: настройки, массивы, повреждения."""

    n_sections: int
    data: SurveyData
    defects: list[DefectSegment]
    layout: LayoutSettings = field(default_factory=load_settings)
    render: RenderSettings = field(default_factory=load_render_settings)
    issues: list[Issue] = field(default_factory=list)  # замечания по повреждениям

    @property
    def total_sheets(self) -> int:
        return 2 * self.n_sections


def make_context(
    survey: Survey,
    notes_objects: Iterable[TrackObject],
    n_sections: int,
    *,
    layout: LayoutSettings | None = None,
    render: RenderSettings | None = None,
) -> SheetContext:
    """Контекст отрисовки: массивы, повреждения (источник - настройки defects.toml).

    ``notes_objects`` - объекты из комментариев (без defect_objects): пометки «ПИП»
    попадают в источник повреждений по критериям.
    """
    marks = pip_marks(list(notes_objects))
    defects, issues = collect_defects(survey, load_criteria(), marks)
    return SheetContext(
        n_sections=max(n_sections, 1),
        data=SurveyData.of(survey),
        defects=defects,
        layout=layout or load_settings(),
        render=render or load_render_settings(),
        issues=list(issues),
    )


# --- вспомогательное ---------------------------------------------------------


def _fmt2(value: float) -> str:
    """Число с двумя знаками и десятичной запятой."""
    text = f"{value:.2f}".replace(".", ",")
    return text[1:] if text == "-0,00" else text


def _fmt_int(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _dash(on_pt: float, off_pt: float, lw: float) -> tuple[float, tuple[float, float]]:
    """Пунктир в пунктах бумаги (matplotlib масштабирует штрихи толщиной линии)."""
    return (0.0, (on_pt / lw, off_pt / lw))


def _step_for(span: float, base: float, max_ticks: int) -> float:
    """Шаг делений: base, 2base, 5base, 10base... чтобы делений было не больше max_ticks."""
    k = 0
    while True:
        for m in (1, 2, 5):
            step = base * m * 10**k
            if span / step <= max_ticks:
                return step
        k += 1


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    n = int(math.floor((hi - lo) / step + 1e-9))
    return [round(lo + i * step, 9) for i in range(n + 1)]


@lru_cache(maxsize=256)
def _fp(families: tuple[str, ...], pt: float, bold: bool) -> FontProperties:
    weight = "bold" if bold else "normal"
    return FontProperties(family=list(installed_families(families)), size=pt, weight=weight)


@lru_cache(maxsize=20000)
def _text_w_mm(text: str, pt: float, bold: bool, families: tuple[str, ...]) -> float:
    """Ширина строки на бумаге, мм (по метрикам шрифта)."""
    width, _, _ = text_to_path.get_text_width_height_descent(text, _fp(families, pt, bold), False)
    return float(width) * _PT_MM


@lru_cache(maxsize=128)
def _image(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))


@lru_cache(maxsize=32)
def _tile(path: str, px: int) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA").resize((px, px), Image.Resampling.LANCZOS))


def _tiled(path: str, tile_mm: float, dpi: float, w_mm: float, h_mm: float, x0_mm: float):
    """Массив плитки, повторённой на w_mm x h_mm (плитка на бумаге - tile_mm).

    x0_mm - сдвиг рисунка по горизонтали (чтобы соседние куски одного листа сходились).
    """
    px = max(8, round(tile_mm / MM * dpi))
    tile = _tile(path, px)
    ppm = px / tile_mm
    cols = (np.arange(max(1, round(w_mm * ppm))) + round(x0_mm * ppm)) % px
    rows = np.arange(max(1, round(h_mm * ppm))) % px
    return tile[rows[:, None], cols[None, :]]


def wrap_text(
    text: str, pt: float, width_mm: float, families: tuple[str, ...], bold: bool = True
) -> list[str]:
    """Перенос строки по словам так, чтобы строка была не шире width_mm (метрики шрифта)."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and _text_w_mm(trial, pt, bold, families) > width_mm:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    return lines


def _gradient(top: str, bottom: str, n: int = 64) -> np.ndarray:
    a, b = np.array(to_rgba(top)), np.array(to_rgba(bottom))
    t = np.linspace(0.0, 1.0, n)[:, None]
    return (a * (1 - t) + b * t).reshape(n, 1, 4)


# --- лист --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Legend:
    text: str
    kind: str  # "line" | "dash" | "patch"
    color: str
    lw: float = 1.0


class _Sheet:
    """Один лист: фигура, оверлей в миллиметрах и вспомогательные построения."""

    def __init__(
        self,
        section: Section,
        params: BuildParams,
        ctx: SheetContext,
        number: int,
        *,
        size_mm: tuple[float, float] | None = None,
        margin_mm: float | None = None,
    ) -> None:
        # size_mm и margin_mm заменяют бумагу и поля: так одна полоса рисуется отдельной
        # картинкой (для Excel); геометрия оси X при этом остаётся как на листе.
        self.sec = section
        self.params = params
        self.ctx = ctx
        self.rs = ctx.render
        self.ls = ctx.layout
        self.number = number
        paper = self.ls.paper(params.paper)
        self.W, self.H = size_mm if size_mm is not None else (paper.width_mm, paper.height_mm)
        self.m = self.ls.margin_mm if margin_mm is None else margin_mm
        self.label_w = self.ls.label_column_mm
        self.plot_left = self.m + self.label_w + self.ls.plot_pad_left_mm
        self.plot_w = self.ls.plot_width_mm(params.paper)
        self.fam = installed_families(tuple(self.ls.font_families))
        self.n_bins = max(1, round(self.plot_w / MM * PNG_DPI))
        self.fig = Figure(figsize=(self.W / MM, self.H / MM), dpi=100, facecolor="white")
        ov = self.fig.add_axes((0, 0, 1, 1), zorder=10)
        ov.set_xlim(0, self.W)
        ov.set_ylim(self.H, 0)
        ov.axis("off")
        ov.patch.set_visible(False)
        self.ov = ov

    # --- рисование в миллиметрах листа ---

    def text(self, x, y, s, pt, *, bold=False, ha="center", va="center", **kw):
        return self.ov.text(
            x, y, s, fontsize=pt, fontfamily=list(self.fam),
            fontweight="bold" if bold else "normal", ha=ha, va=va, color="black", **kw,
        )  # fmt: skip

    def line(self, x0, y0, x1, y1, lw, color="black", **kw):
        self.ov.add_line(
            Line2D([x0, x1], [y0, y1], lw=lw, color=color, solid_capstyle="butt", **kw)
        )

    def frame(self, x, y, w, h, lw, fc="none"):
        self.ov.add_patch(
            Rectangle((x, y), w, h, fill=fc != "none", fc=fc, ec=self.rs.colors.frame, lw=lw)
        )

    def axes_mm(self, x, y, w, h):
        """Axes в прямоугольнике листа (мм от левого верхнего угла)."""
        ax = self.fig.add_axes((x / self.W, 1 - (y + h) / self.H, w / self.W, h / self.H))
        return ax

    def wrap(self, text: str, pt: float, width_mm: float, bold: bool = True) -> str:
        return chr(10).join(wrap_text(text, pt, width_mm, self.fam, bold))

    # --- полоса: рамка и название ---

    def strip_frame(self, y, h, title: str) -> None:
        c, g = self.rs.colors, self.rs.lines
        x0, w = self.m, self.W - 2 * self.m
        self.frame(x0, y, w, h, g.frame)
        self.line(x0 + self.label_w, y, x0 + self.label_w, y + h, g.cell, c.frame)
        pt = self.rs.sizes.strip_label_pt
        self.text(
            x0 + self.label_w / 2, y + h / 2, self.wrap(title, pt, self.label_w - 4.0), pt,
            bold=True, linespacing=1.15,
        )  # fmt: skip

    # --- оси ---

    def setup_x(self, ax, *, top: bool = False, labels: bool = True) -> None:
        """Пределы и деления оси X: метры трассы, подписи - километраж с запятой."""
        sec, rs = self.sec, self.rs
        ax.set_xlim(sec.start_m, sec.start_m + sec.nominal_length_m)
        ticks = tick_positions(sec)
        ax.set_xticks(ticks)
        if labels:
            ax.set_xticklabels(
                [format_km(self.params.start_km + t / 1000.0) for t in ticks],
                fontfamily=list(self.fam), fontsize=rs.sizes.tick_pt,
            )  # fmt: skip
        else:
            ax.set_xticklabels([])
        ax.tick_params(
            axis="x", length=1.5, width=rs.lines.axis, color=rs.colors.axis, pad=1.2,
            top=top, bottom=not top, labeltop=top and labels, labelbottom=(not top) and labels,
        )  # fmt: skip

    def setup_y(self, ax, ticks: Sequence[float], fmt, ylim: tuple[float, float]) -> None:
        rs = self.rs
        ax.set_ylim(*ylim)
        ax.set_yticks(list(ticks))
        ax.set_yticklabels(
            [fmt(t) for t in ticks], fontfamily=list(self.fam), fontsize=rs.sizes.tick_pt
        )
        ax.tick_params(
            axis="y", length=1.5, width=rs.lines.axis, color=rs.colors.axis, pad=1.5
        )  # fmt: skip

    def style_graph(self, ax, *, grid_x: bool = True, grid_y: bool = True, dashed_x=False) -> None:
        rs = self.rs
        for name in ("top", "right"):
            ax.spines[name].set_visible(False)
        for name in ("left", "bottom"):
            ax.spines[name].set_color(rs.colors.axis)
            ax.spines[name].set_linewidth(rs.lines.axis)
        ax.set_axisbelow(True)
        if grid_x:
            ls = _dash(3.0, 2.5, rs.lines.grid) if dashed_x else "-"
            ax.grid(axis="x", color=rs.colors.grid, lw=rs.lines.grid, ls=ls)
        if grid_y:
            ax.grid(axis="y", color=rs.colors.grid, lw=rs.lines.grid)

    def no_data(self, ax) -> None:
        ax.text(
            0.5, 0.5, NO_DATA, transform=ax.transAxes, ha="center", va="center",
            fontsize=self.rs.sizes.no_data_pt, fontfamily=list(self.fam), color="#555555",
            zorder=20,
        )  # fmt: skip

    # --- нижний блок полосы: подписи делений, легенда, «Протяженность, км» ---

    def graph_axes(self, y_top: float, h: float):
        """Axes графика в полосе с осью снизу: сверху отступ, снизу строка делений и легенда."""
        g = self.rs.geometry
        bottom = g.tick_row_mm + g.legend_pad_mm + g.legend_box_mm + g.legend_pad_mm
        top = y_top + g.axes_top_pad_mm
        ax = self.axes_mm(self.plot_left, top, self.plot_w, h - g.axes_top_pad_mm - bottom)
        return ax, top + (h - g.axes_top_pad_mm - bottom)

    def caption_below(self, axes_bottom: float) -> float:
        """«Протяженность, км» справа под осью; возвращает верх рамки легенды."""
        g = self.rs.geometry
        box_top = axes_bottom + g.tick_row_mm + g.legend_pad_mm
        self.text(
            self.plot_left + self.plot_w, box_top + g.legend_box_mm / 2, CAPTION_LENGTH,
            self.rs.sizes.caption_pt, ha="right",
        )  # fmt: skip
        return box_top

    def legend(self, box_top: float, entries: Sequence[_Legend]) -> None:
        """Легенда в рамке под графиком (по центру области графика)."""
        rs, g = self.rs, self.rs.geometry
        pt = rs.sizes.legend_pt
        room = self.plot_w - g.caption_right_mm
        while True:
            widths = [_text_w_mm(e.text, pt, False, self.fam) for e in entries]
            total = sum(g.legend_line_mm + 1.5 + w for w in widths)
            total += g.legend_gap_mm * (len(entries) - 1) + 4.0
            if total <= room or pt <= 4.0:
                break
            pt -= 0.5
        x0 = self.plot_left + max((self.plot_w - total) / 2, 0.0)
        if x0 + total > self.plot_left + room:
            x0 = self.plot_left + max(room - total, 0.0)
        y_mid = box_top + g.legend_box_mm / 2
        self.ov.add_patch(
            Rectangle(
                (x0, box_top), total, g.legend_box_mm, fc="white", ec=rs.colors.frame, lw=0.4,
                zorder=1,
            )
        )  # fmt: skip
        x = x0 + 2.0
        for e, w in zip(entries, widths, strict=True):
            x1 = x + g.legend_line_mm
            if e.kind == "patch":
                self.ov.add_patch(
                    Rectangle((x, y_mid - 0.7), x1 - x, 1.4, fc=e.color, ec="none", zorder=2)
                )
            else:
                ls = _dash(3.2, 2.0, e.lw) if e.kind == "dash" else "-"
                self.line(x, y_mid, x1, y_mid, e.lw, e.color, ls=ls, zorder=2)
            self.text(x1 + 1.5, y_mid, e.text, pt, ha="left", zorder=3)
            x = x1 + 1.5 + w + g.legend_gap_mm

    def note_box(self, box_top: float, text: str) -> None:
        """Подпись под графиком в рамке (площадь дефекта, зоны)."""
        rs, g = self.rs, self.rs.geometry
        pt = rs.sizes.legend_pt
        room = self.plot_w - g.caption_right_mm
        while _text_w_mm(text, pt, False, self.fam) + 4.0 > room and pt > 4.0:
            pt -= 0.5
        total = _text_w_mm(text, pt, False, self.fam) + 4.0
        x0 = self.plot_left + max((self.plot_w - total) / 2, 0.0)
        self.ov.add_patch(
            Rectangle(
                (x0, box_top), total, g.legend_box_mm, fc="white", ec=rs.colors.frame, lw=0.4,
                zorder=1,
            )
        )  # fmt: skip
        self.text(x0 + total / 2, box_top + g.legend_box_mm / 2, text, pt, zorder=3)

    def top_axis_axes(self, y_top: float, h: float, bottom_pad: float | None = None):
        """Axes в полосе с осью сверху: «Протяженность, км» и подписи делений над ним."""
        g = self.rs.geometry
        self.text(
            self.plot_left + self.plot_w, y_top + 2.3, CAPTION_LENGTH, self.rs.sizes.caption_pt,
            ha="right",
        )  # fmt: skip
        pad = g.ribbon_pad_mm if bottom_pad is None else bottom_pad
        top = y_top + g.top_axis_header_mm
        return self.axes_mm(self.plot_left, top, self.plot_w, h - g.top_axis_header_mm - pad)

    # --- данные ---

    def win(self, series: Series) -> Window:
        return window(series, self.sec.start_m, self.sec.end_m, self.n_bins)

    # --- колонтитул ---

    def footer(self) -> None:
        sec, p = self.sec, self.params
        km0 = format_km(p.start_km + sec.start_m / 1000.0)
        km1 = format_km(p.start_km + sec.end_m / 1000.0)
        y = self.H - self.m - self.rs.geometry.footer_mm / 2 + 0.5
        pt = self.rs.sizes.footer_pt
        self.text(self.m, y, f"{p.object_name}.  км {km0} - {km1}", pt, ha="left")
        self.text(
            self.W - self.m, y, f"Лист {self.number} из {self.ctx.total_sheets}", pt, ha="right"
        )  # fmt: skip

    def inner_height(self) -> float:
        """Высота под полосы: бумага без полей и колонтитула."""
        return self.H - 2 * self.m - self.rs.geometry.footer_mm


# --- лист 1 -------------------------------------------------------------------


def gradient_axis(w_on: Window, w_off: Window, rs: RenderSettings) -> tuple[float, float, float]:
    """(низ, верх, шаг) оси градиента по данным двух рядов участка."""
    vals: list[float] = []
    for w in (w_on, w_off):
        vals += [float(v) for v in (np.nanmin(w.y), np.nanmax(w.y))] if w.inside else []
    return _y_gradient(vals, rs)


def _y_gradient(values: list[float], rs: RenderSettings) -> tuple[float, float, float]:
    """(низ, верх, шаг) оси градиента: 0-90, при больших данных расширяется."""
    a = rs.axes
    step = a.gradient_step
    top = a.gradient_top
    lo = 0.0
    if values:
        top = max(top, math.ceil(max(values) / step) * step)
        lo = min(lo, math.floor(min(values) / step) * step)
    step = _step_for(top - lo, step, int(a.max_ticks))
    return lo, math.ceil(top / step) * step, step


def _inner_height_mm(params: BuildParams, ctx: SheetContext) -> float:
    """Высота под полосы: бумага без полей и колонтитула."""
    paper = ctx.layout.paper(params.paper)
    return paper.height_mm - 2 * ctx.layout.margin_mm - ctx.render.geometry.footer_mm


def sheet1_heights(params: BuildParams, ctx: SheetContext) -> dict[str, float]:
    """Высоты полос листа 1, мм: potential, gradient_left, gradient_right, defects, zones."""
    rs = ctx.render
    avail = _inner_height_mm(params, ctx) - rs.geometry.title_height_mm
    f = rs.sheet1
    return {
        "potential": avail * f.potential,
        "gradient_left": avail * f.gradient_left,
        "gradient_right": avail * f.gradient_right,
        "defects": avail * f.defects,
        "zones": avail * f.zones,
    }


@dataclass(frozen=True, slots=True)
class Sheet2Heights:
    """Высоты полос листа 2, мм; полоса «Объекты на трассе» зависит от размещения значков."""

    insulation: float
    depth: float
    resistivity: float
    soil: float
    objects: float
    objects_axes: float  # высота области значков (без строки подписи оси)
    above: float  # высота зоны над трубой
    below: float  # глубина зоны под трубой


def sheet2_heights(
    params: BuildParams, ctx: SheetContext, placement: SectionPlacement
) -> Sheet2Heights:
    """Раскладка листа 2 по вертикали (общая для PDF и картинок Excel)."""
    g, f = ctx.render.geometry, ctx.render.sheet2
    above, below = zone_extents_mm(ctx.layout)
    above = max(above, placement.above_extent_mm)
    below = max(below, placement.below_extent_mm)
    obj_axes_h = above + below + 2 * g.objects_pad_mm
    obj_h = g.top_axis_header_mm + obj_axes_h + g.ribbon_pad_mm
    rest = _inner_height_mm(params, ctx) - obj_h
    return Sheet2Heights(
        insulation=rest * f.insulation,
        depth=rest * f.depth,
        resistivity=rest * f.resistivity,
        soil=rest * f.soil,
        objects=obj_h,
        objects_axes=obj_axes_h,
        above=above,
        below=below,
    )


def render_sheet1(
    survey: Survey, section: Section, params: BuildParams, ctx: SheetContext
) -> Figure:
    """Лист 1: заголовок и полосы потенциала, градиентов, площади дефекта и зон."""
    del survey  # данные берутся из ctx.data (массивы строятся один раз на карту)
    sh = _Sheet(section, params, ctx, 2 * (section.index - 1) + 1)
    rs, g = ctx.render, ctx.render.geometry
    # заголовок
    title_h = g.title_height_mm
    sh.frame(sh.m, sh.m, sh.W - 2 * sh.m, title_h, rs.lines.frame)
    sh.text(sh.W / 2, sh.m + title_h / 2, params.object_name, rs.sizes.title_pt, bold=True)
    heights = sheet1_heights(params, ctx)
    y = sh.m + title_h
    parts = [
        (TITLE_POTENTIAL, "potential", _strip_potential),
        (TITLE_GRADIENT, "gradient_left", _strip_gradient_left),
        (TITLE_GRADIENT, "gradient_right", _strip_gradient_right),
        (TITLE_DEFECTS, "defects", _strip_defects),
        (TITLE_ZONES, "zones", _strip_zones),
    ]
    for title, key, draw in parts:
        h = heights[key]
        sh.strip_frame(y, h, title)
        draw(sh, y, h)
        y += h
    sh.footer()
    return sh.fig


def _graph(sh: _Sheet, y: float, h: float):
    ax, bottom = sh.graph_axes(y, h)
    return ax, sh.caption_below(bottom)


def potential_axis(
    w_on: Window, w_off: Window, p: BuildParams, rs: RenderSettings
) -> tuple[float, float, float]:
    """(верх, низ, шаг) оси потенциала: -3,00...-0,50, расширяется под данные и нормативы."""
    a = rs.axes
    top, bottom, step = a.potential_top, a.potential_bottom, a.potential_step
    vals = [p.u_min, p.u_max]
    for w in (w_on, w_off):
        fin = w.y[~np.isnan(w.y)]
        if fin.size:
            vals += [float(fin.min()), float(fin.max())]
    top = min(top, math.floor(min(vals) / step) * step)
    bottom = max(bottom, math.ceil(max(vals) / step) * step)
    step = _step_for(bottom - top, step, int(a.max_ticks))
    return top, bottom, step


def _strip_potential(sh: _Sheet, y: float, h: float) -> None:
    rs, p, d = sh.rs, sh.params, sh.ctx.data
    ax, box_top = _graph(sh, y, h)
    w_on, w_off = sh.win(d.u_on), sh.win(d.u_off)
    top, bottom, step = potential_axis(w_on, w_off, p, rs)
    sh.setup_x(ax)
    sh.style_graph(ax)
    sh.setup_y(ax, _ticks(top, bottom, step), _fmt2, (bottom, top))  # ось перевёрнута
    s, e = sh.sec.start_m, sh.sec.end_m
    ax.plot([s, e], [p.u_max, p.u_max], color=rs.colors.u_max, lw=rs.lines.norm,
            ls=_dash(3.0, 2.0, rs.lines.norm), zorder=3)  # fmt: skip
    ax.plot([s, e], [p.u_min, p.u_min], color=rs.colors.u_min, lw=rs.lines.norm,
            ls=_dash(3.0, 2.0, rs.lines.norm), zorder=3)  # fmt: skip
    ax.plot(w_off.x, w_off.y, color=rs.colors.u_off, lw=rs.lines.potential, zorder=4)
    ax.plot(w_on.x, w_on.y, color=rs.colors.u_on, lw=rs.lines.potential, zorder=5)
    if not (w_on.has_data or w_off.has_data):
        sh.no_data(ax)
    c, ln = rs.colors, rs.lines
    sh.legend(
        box_top,
        [
            _Legend("защитный потенциал", "line", c.u_on, ln.potential),
            _Legend("потенциал отключения", "line", c.u_off, ln.potential),
            _Legend("максимальный нормативный потенциал", "dash", c.u_max, ln.norm),
            _Legend("минимальный нормативный потенциал", "dash", c.u_min, ln.norm),
        ],
    )  # fmt: skip


def _strip_gradient(sh: _Sheet, y: float, h: float, side: str) -> None:
    rs, d = sh.rs, sh.ctx.data
    ax, box_top = _graph(sh, y, h)
    s_on = getattr(d, f"grad_{side}_on")
    s_off = getattr(d, f"grad_{side}_off")
    c_on = getattr(rs.colors, f"gradient_{side}_on")
    c_off = getattr(rs.colors, f"gradient_{side}_off")
    w_on, w_off = sh.win(s_on), sh.win(s_off)
    lo, hi, step = gradient_axis(w_on, w_off, rs)
    sh.setup_x(ax)
    sh.style_graph(ax, grid_y=False)
    sh.setup_y(ax, _ticks(lo, hi, step), _fmt_int, (lo, hi))
    ax.plot(w_on.x, w_on.y, color=c_on, lw=rs.lines.gradient, zorder=3)
    ax.plot(w_off.x, w_off.y, color=c_off, lw=rs.lines.gradient, zorder=4)
    if not (w_on.has_data or w_off.has_data):
        sh.no_data(ax)
    word = "левый" if side == "left" else "правый"
    sh.legend(
        box_top,
        [
            _Legend(f"Градиент {word} вкл", "line", c_on, 1.0),
            _Legend(f"Градиент {word} выкл", "line", c_off, 1.0),
        ],
    )  # fmt: skip


def _strip_gradient_left(sh: _Sheet, y: float, h: float) -> None:
    _strip_gradient(sh, y, h, "left")


def _strip_gradient_right(sh: _Sheet, y: float, h: float) -> None:
    _strip_gradient(sh, y, h, "right")


def _strip_defects(sh: _Sheet, y: float, h: float) -> None:
    rs = sh.rs
    ax, box_top = _graph(sh, y, h)
    top = rs.axes.defects_top
    sh.setup_x(ax)
    sh.style_graph(ax, dashed_x=True)
    sh.setup_y(ax, _ticks(0, top, 1.0), _fmt_int, (0.0, top))
    s, e = sh.sec.start_m, sh.sec.end_m
    for lo, hi, cls in strip_steps(sh.ctx.defects, s, e):
        ax.fill([lo, lo, hi, hi], [0, cls, cls, 0], color=rs.colors.defects_fill, lw=0, zorder=2)
        ax.plot([lo, lo, hi, hi], [0, cls, cls, 0], color=rs.colors.defects, lw=rs.lines.defects,
                zorder=3)  # fmt: skip
    # Точечные повреждения с классом (ширина ноль) - вертикальный штрих высотой в класс.
    for seg in sh.ctx.defects:
        if seg.class_final and seg.start == seg.end and s <= seg.start <= e:
            ax.plot([seg.start, seg.start], [0, seg.class_final], color=rs.colors.defects,
                    lw=rs.lines.defects, zorder=3)  # fmt: skip
    sh.note_box(box_top, DEFECTS_NOTE)


def _strip_zones(sh: _Sheet, y: float, h: float) -> None:
    rs = sh.rs
    ax, box_top = _graph(sh, y, h)
    sh.setup_x(ax)
    sh.style_graph(ax, grid_y=False, dashed_x=True)
    sh.setup_y(ax, [0.0, 1.0], _fmt_int, (0.0, 1.0))
    s, e = sh.sec.start_m, sh.sec.end_m
    ax.plot([s, e], [1, 1], color=rs.colors.frame, lw=rs.lines.cell, zorder=3, clip_on=False)
    ax.plot([s, e], [0, 0], color=rs.colors.frame, lw=rs.lines.zones, zorder=4, clip_on=False,
            solid_capstyle="butt")  # fmt: skip
    sh.note_box(box_top, ZONES_NOTE)


# --- лист 2 -------------------------------------------------------------------


def render_sheet2(
    survey: Survey,
    section: Section,
    placement: SectionPlacement,
    params: BuildParams,
    ctx: SheetContext,
) -> Figure:
    """Лист 2: тип изоляции, глубина, УЭС, тип грунта и «Объекты на трассе»."""
    del survey
    sh = _Sheet(section, params, ctx, 2 * (section.index - 1) + 2)
    hs = sheet2_heights(params, ctx, placement)
    y = sh.m
    for title, h, draw in (
        (TITLE_INSULATION, hs.insulation, _strip_insulation),
        (TITLE_DEPTH, hs.depth, _strip_depth),
        (TITLE_RESISTIVITY, hs.resistivity, _strip_resistivity),
        (TITLE_SOIL, hs.soil, _strip_soil),
    ):
        sh.strip_frame(y, h, title)
        draw(sh, y, h)
        y += h
    sh.strip_frame(y, hs.objects, TITLE_OBJECTS)
    _strip_objects(sh, y, hs.objects, hs.objects_axes, hs.above, hs.below, placement)
    sh.footer()
    return sh.fig


# --- отдельные полосы-картинки (для книги Excel) --------------------------------

STRIP_INSULATION = "insulation"
STRIP_SOIL = "soil"
STRIP_OBJECTS = "objects"
IMAGE_PAD_MM = 0.5  # поле вокруг рамки полосы на картинке, чтобы линии рамки не обрезались


def _strip_height_mm(
    kind: str, params: BuildParams, ctx: SheetContext, placement: SectionPlacement
) -> float:
    hs = sheet2_heights(params, ctx, placement)
    by_kind = {STRIP_INSULATION: hs.insulation, STRIP_SOIL: hs.soil, STRIP_OBJECTS: hs.objects}
    if kind not in by_kind:
        raise ValueError(f"Неизвестная полоса-картинка «{kind}».")
    return by_kind[kind]


def strip_image_size_mm(
    kind: str, params: BuildParams, ctx: SheetContext, placement: SectionPlacement
) -> tuple[float, float]:
    """Размер картинки полосы, мм: ширина листа без полей (плюс поле картинки), высота полосы."""
    paper = ctx.layout.paper(params.paper)
    width = paper.width_mm - 2 * ctx.layout.margin_mm + 2 * IMAGE_PAD_MM
    return width, _strip_height_mm(kind, params, ctx, placement) + 2 * IMAGE_PAD_MM


def render_strip_png(
    kind: str,
    section: Section,
    placement: SectionPlacement,
    params: BuildParams,
    ctx: SheetContext,
    *,
    dpi: int = 150,
) -> bytes:
    """Одна полоса листа 2 (STRIP_INSULATION, STRIP_SOIL или STRIP_OBJECTS) как PNG в памяти.

    Рисуется тем же кодом, что и лист PDF. Ось X картинки совпадает по горизонтали с
    полем графика листа: слева колонка названия, затем область графика ровно
    ``plot_width_mm`` (по краям рамки поле IMAGE_PAD_MM).
    """
    width, height = strip_image_size_mm(kind, params, ctx, placement)
    h = height - 2 * IMAGE_PAD_MM
    sh = _Sheet(section, params, ctx, 0, size_mm=(width, height), margin_mm=IMAGE_PAD_MM)
    y = IMAGE_PAD_MM
    if kind == STRIP_OBJECTS:
        hs = sheet2_heights(params, ctx, placement)
        sh.strip_frame(y, h, TITLE_OBJECTS)
        _strip_objects(sh, y, h, hs.objects_axes, hs.above, hs.below, placement)
    elif kind == STRIP_INSULATION:
        sh.strip_frame(y, h, TITLE_INSULATION)
        _strip_insulation(sh, y, h)
    else:
        sh.strip_frame(y, h, TITLE_SOIL)
        _strip_soil(sh, y, h)
    buf = BytesIO()
    sh.fig.savefig(buf, format="png", dpi=dpi, pil_kwargs={"compress_level": 6})
    sh.fig.clear()
    return buf.getvalue()


def _ribbon(sh: _Sheet, y: float, h: float, marks, texture) -> None:
    rs, sec = sh.rs, sh.sec
    ax = sh.top_axis_axes(y, h)
    sh.setup_x(ax, top=True)
    ax.set_ylim(0, 1)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_yticks([])
    ax_h_mm = ax.get_position().height * sh.H
    ivs = type_intervals(marks, sec.start_m, sec.end_m)
    k = sec.plot_width_mm / sec.nominal_length_m  # мм на метр
    for lo, hi, value in ivs:
        arr = _tiled(
            str(texture(value)), rs.tiles.tile_mm, rs.tiles.texture_dpi,
            (hi - lo) * k, ax_h_mm, (lo - sec.start_m) * k,
        )  # fmt: skip
        ax.imshow(arr, extent=(lo, hi, 0, 1), aspect="auto", interpolation="antialiased",
                  zorder=2)  # fmt: skip
    if ivs:
        ax.add_patch(
            Rectangle((sec.start_m, 0), sec.end_m - sec.start_m, 1, fill=False,
                      ec=rs.colors.frame, lw=rs.lines.cell, zorder=4)
        )  # fmt: skip
    else:
        sh.no_data(ax)
    ax.set_xlim(sec.start_m, sec.start_m + sec.nominal_length_m)
    ax.set_ylim(0, 1)


def _strip_insulation(sh: _Sheet, y: float, h: float) -> None:
    _ribbon(sh, y, h, sh.ctx.data.insulation_marks, assets.insulation_texture)


def _strip_soil(sh: _Sheet, y: float, h: float) -> None:
    _ribbon(sh, y, h, sh.ctx.data.soil_marks, assets.soil_texture)


def depth_axis(w: Window, p: BuildParams, rs: RenderSettings) -> tuple[float, float, float]:
    """(низ, верх, шаг) оси глубины: шаг 0,10, запас в один шаг вокруг данных и норматива."""
    step = rs.axes.depth_step
    vals = [p.depth_min]
    if w.inside:
        vals += [float(np.nanmin(w.y)), float(np.nanmax(w.y))]
    lo = math.floor(min(vals) / step + 1e-9) * step - step
    hi = math.ceil(max(vals) / step - 1e-9) * step + step
    step = _step_for(hi - lo, step, int(rs.axes.max_ticks))
    lo = math.floor(lo / step + 1e-9) * step
    hi = math.ceil(hi / step - 1e-9) * step
    return lo, hi, step


def _strip_depth(sh: _Sheet, y: float, h: float) -> None:
    rs, p = sh.rs, sh.params
    ax, box_top = _graph(sh, y, h)
    w = sh.win(sh.ctx.data.depth)
    lo, hi, step = depth_axis(w, p, rs)
    sh.setup_x(ax)
    sh.style_graph(ax)
    sh.setup_y(ax, _ticks(lo, hi, step), _fmt2, (lo, hi))
    s, e = sh.sec.start_m, sh.sec.end_m
    ax.plot([s, e], [p.depth_min] * 2, color=rs.colors.depth_norm, lw=rs.lines.depth_norm,
            ls=_dash(3.0, 2.0, rs.lines.depth_norm), zorder=3)  # fmt: skip
    ax.plot(w.x, w.y, color=rs.colors.depth, lw=rs.lines.depth, zorder=4)
    if not w.has_data:
        sh.no_data(ax)
    sh.legend(
        box_top,
        [
            _Legend("глубина залегания", "line", rs.colors.depth, rs.lines.depth),
            _Legend("минимальная нормативная глубина залегания", "dash",
                    rs.colors.depth_norm, rs.lines.depth_norm),
        ],
    )  # fmt: skip


def resistivity_axis(w: Window, p: BuildParams) -> tuple[float, float]:
    """(верх, шаг) оси УЭС: от нуля, с запасом над верхним порогом и данными.

    Если задан p.res_axis_max, верх равен ему ровно (данные выше обрезаются рамкой графика),
    шаг подбирается как обычно; последнее деление может оказаться ниже верха.
    """
    if p.res_axis_max is not None:
        return p.res_axis_max, _step_for(p.res_axis_max, 1.0, 5)
    top_raw = p.res_high * 1.5
    if w.inside:
        top_raw = max(top_raw, float(np.nanmax(w.y)) * 1.15)
    step = _step_for(top_raw, 1.0, 5)
    return math.ceil(top_raw / step) * step, step


def _strip_resistivity(sh: _Sheet, y: float, h: float) -> None:
    rs, p = sh.rs, sh.params
    ax, box_top = _graph(sh, y, h)
    w = sh.win(sh.ctx.data.resistivity)
    top, step = resistivity_axis(w, p)
    sh.setup_x(ax)
    sh.style_graph(ax)
    sh.setup_y(ax, _ticks(0.0, top, step), _fmt_int, (0.0, top))
    s, e = sh.sec.start_m, sh.sec.end_m
    c = rs.colors
    for lo, hi, col in ((0.0, p.res_low, c.res_high), (p.res_low, p.res_high, c.res_mid),
                        (p.res_high, top, c.res_low)):  # fmt: skip
        ax.add_patch(Rectangle((s, lo), e - s, hi - lo, fc=col, ec="none", zorder=1))
    ax.plot(w.x, w.y, color=c.resistivity, lw=rs.lines.resistivity, zorder=4)
    if not w.has_data:
        sh.no_data(ax)
    sh.legend(
        box_top,
        [
            _Legend("высокой агрессивности", "patch", c.res_high),
            _Legend("средней агрессивности", "patch", c.res_mid),
            _Legend("низкой агрессивности", "patch", c.res_low),
            _Legend("УЭС", "line", c.resistivity, rs.lines.resistivity + 0.3),
        ],
    )  # fmt: skip


# --- полоса «Объекты на трассе» ----------------------------------------------

ROAD_TOP_MM = 1.5  # полоса поперёк начинается на столько выше оси трубы


class _ObjectsDrawer:
    """Рисует объекты участка по SectionPlacement (все размеры - из PlacedObject, мм)."""

    def __init__(self, sh: _Sheet, ax, y_lo: float, y_hi: float) -> None:
        self.sh, self.ax, self.rs = sh, ax, sh.rs
        self.y_lo, self.y_hi = y_lo, y_hi
        self.k = sh.sec.nominal_length_m / sh.plot_w  # метров на мм
        self.start = sh.sec.start_m

    def X(self, mm: float) -> float:
        return self.start + mm * self.k

    def picture(self, key: str, x0: float, x1: float, y0: float, y1: float, z: float) -> None:
        im = self.ax.imshow(
            _image(str(assets.icon_png(key))), extent=(self.X(x0), self.X(x1), y0, y1),
            aspect="auto", interpolation="antialiased", zorder=z,
        )  # fmt: skip
        im.set_clip_on(False)

    def whole_tiles(self, key: str, b) -> None:
        """Только целые значки, равномерно по ширине полосы: не обрезаются и не сжимаются."""
        w_icon, _ = assets.icon_size_mm(key)
        n = max(1, int(b.width / w_icon + 1e-9))
        tw = min(w_icon, b.width)
        gap = (b.width - n * tw) / n
        for i in range(n):
            x = b.x0 + gap / 2 + i * (tw + gap)
            self.picture(key, x, x + tw, b.y0, b.y1, 2)

    def ravine(self, key: str, b) -> None:
        """Овраг: одна фигура на всю ширину полосы."""
        self.picture(key, b.x0, b.x1, b.y0, b.y1, 2)

    def water(self, key: str, b) -> None:
        """Два волнистых берега по границам полосы, заливка между ними, волны внутри."""
        c, ln, ex = self.rs.colors, self.rs.lines, self.rs.extended
        y = np.linspace(b.y0, b.y1, 80)
        amp = min(ex.wave_amp_max_mm, max(ex.wave_amp_min_mm, b.width * ex.wave_amp_ratio))
        wave = amp * np.sin((y - b.y0) / (b.y1 - b.y0) * 2 * np.pi * ex.wave_periods)
        xl, xr = b.x0 + wave, b.x1 + wave
        pts = np.column_stack(
            [np.concatenate([self.X(xl), self.X(xr[::-1])]), np.concatenate([y, y[::-1]])]
        )
        self.ax.add_patch(
            Polygon(pts, closed=True, fc=c.water_fill, ec="none", zorder=2, clip_on=False)
        )
        for x in (xl, xr):
            self.ax.plot(self.X(x), y, color=c.water_shore, lw=ln.water_shore, zorder=2.1,
                         clip_on=False, solid_capstyle="round")  # fmt: skip
        n = int(b.width // ex.water_wave_step_mm)  # редкие волны внутри широкой воды
        for i in range(1, n + 1):
            xi = b.x0 + b.width * i / (n + 1) + wave
            self.ax.plot(self.X(xi), y, color=c.water_shore, lw=ln.water_wave,
                         alpha=ex.water_wave_alpha, zorder=2.05, clip_on=False)  # fmt: skip

    def air_crossing(self, key: str, b) -> None:
        """Опоры по границам полосы и линия провала земли между ними."""
        c, ln, ex = self.rs.colors, self.rs.lines, self.rs.extended
        depth = (b.y1 - b.y0) * ex.air_depth_ratio
        x = np.linspace(b.x0, b.x1, 60)
        t = (x - b.x0) / max(b.width, 1e-9)
        y = b.y1 - depth * np.sin(np.pi * t) ** 0.6  # профиль земли под трубой
        pts = np.column_stack(
            [np.concatenate([self.X(x), self.X(x[::-1])]), np.concatenate([y, np.full(60, b.y1)])]
        )
        self.ax.add_patch(
            Polygon(pts, closed=True, fc=c.air_void, ec="none", zorder=2, clip_on=False)
        )
        self.ax.plot(self.X(x), y, color=c.air_ground, lw=ln.air_ground, zorder=2.1, clip_on=False)
        half = min(ex.air_post_half_max_mm, max(ex.air_post_half_min_mm,
                                                b.width * ex.air_post_half_ratio))  # fmt: skip
        foot = b.y0 + ex.air_post_foot_lift_mm
        for xc in (b.x0, b.x1):
            self.ax.plot(
                [self.X(xc)] * 2, [b.y0 + ex.air_post_top_mm, b.y1], color=c.air_post,
                lw=ln.air_post, zorder=2.3, clip_on=False, solid_capstyle="butt",
            )  # fmt: skip
            tri = [
                (self.X(xc - half), foot),
                (self.X(xc + half), foot),
                (self.X(xc), foot + ex.air_post_foot_height_mm),
            ]
            self.ax.add_patch(
                Polygon(tri, closed=True, fc=c.air_post, ec="none", zorder=2.3, clip_on=False)
            )

    def open_area(self, key: str, b) -> None:
        """Одна пунктирная рамка на всю полосу, косая штриховка с постоянным шагом."""
        c, ln, ex = self.rs.colors, self.rs.lines, self.rs.extended
        rect = Rectangle(
            (self.X(b.x0), b.y0), b.width * self.k, b.y1 - b.y0, fc=c.open_fill, ec=c.open_frame,
            lw=ln.open_frame, ls=_dash(ex.open_dash_on_pt, ex.open_dash_off_pt, ln.open_frame),
            zorder=2, clip_on=False,
        )  # fmt: skip
        self.ax.add_patch(rect)
        x = b.x0 - ex.open_hatch_run_mm
        while x < b.x1:
            (hatch,) = self.ax.plot(
                [self.X(x), self.X(x + ex.open_hatch_run_mm)], [b.y0, b.y1],
                color=c.open_frame, lw=ln.open_hatch, zorder=2.05,
            )  # fmt: skip
            hatch.set_clip_path(rect)
            x += ex.open_hatch_pitch_mm

    def under_pipe(self, key: str, b) -> None:
        """Протяжённый объект под трубой: рисунок зависит от типа (ТЗ, 7.1)."""
        draw = {
            "water": self.water,
            "ravine": self.ravine,
            "air_crossing": self.air_crossing,
            "open_area": self.open_area,
        }.get(key)
        if draw is None:  # болото, лес и остальные типы - целые значки
            self.whole_tiles(key, b)
        else:
            draw(key, b)

    def vline(self, x_mm: float) -> None:
        c, ln = self.rs.colors, self.rs.lines
        self.ax.plot([self.X(x_mm)] * 2, [self.y_lo, self.y_hi], color=c.cross, lw=ln.cross,
                     zorder=4, solid_capstyle="butt")  # fmt: skip

    def draw(self, p: PlacedObject) -> None:
        key, zone = p.obj.type_key, p.zone
        b = p.band_rect
        if b is not None and zone in (AREA_BELOW, BELOW):
            self.under_pipe(key, b)
        elif zone == CROSS_BAND and b is not None:
            self.cross_band(key, b)
        elif zone == CROSS_LINE:
            if key == "crossing":  # переезд: две линии поперёк
                self.vline(p.x_mm - 1.0)
                self.vline(p.x_mm + 1.0)
            elif b is not None and b.width > 2.0:
                self.vline(b.x0)
                self.vline(b.x1)
            else:
                self.vline(p.x_mm)
            r = p.icon_rect
            self.picture(key, r.x0, r.x1, r.y0, r.y1, 5)
        else:
            r = p.icon_rect
            if zone == ABOVE and p.tier > 0:  # ножка от значка верхнего яруса до трубы
                c, ln = self.rs.colors, self.rs.lines
                self.ax.plot([self.X(p.x_mm)] * 2, [0.0, r.y0], color=c.pipe_light,
                             lw=ln.leader, zorder=4.5, solid_capstyle="butt")  # fmt: skip
            # значки «на трубе» (кран, ВЭИ) рисуются поверх значков «над трубой»
            self.picture(key, r.x0, r.x1, r.y0, r.y1, 5.5 if zone == ON else 5)
        self.label(p)

    def cross_band(self, key: str, b) -> None:
        """Полоса поперёк: от чуть выше трубы до низа полосы, поверх фона и трубы."""
        c = self.rs.colors
        top, bottom = ROAD_TOP_MM, self.y_lo
        if key == "rail":
            # «шпальная» полоса: рисунок значка плиткой по вертикали
            h_icon = assets.icon_size_mm(key)[1]
            arr = _image(str(assets.icon_png(key)))
            y = top
            while y - bottom > 0.05:
                step = min(h_icon, y - bottom)
                rows = max(1, round(arr.shape[0] * step / h_icon))
                im = self.ax.imshow(
                    arr[:rows], extent=(self.X(b.x0), self.X(b.x1), y - step, y), aspect="auto",
                    interpolation="antialiased", zorder=3.4,
                )  # fmt: skip
                im.set_clip_on(False)
                y -= step
            return
        self.ax.add_patch(
            Rectangle((self.X(b.x0), bottom), (b.x1 - b.x0) * self.k, top - bottom, fc=c.road,
                      ec="none", zorder=3.4)
        )  # fmt: skip
        if b.width >= 1.2:  # белый пунктир по центру
            xm = self.X((b.x0 + b.x1) / 2)
            lw = min(0.8, b.width * 0.18 / _PT_MM)
            self.ax.plot([xm, xm], [bottom + 0.8, top - 0.8], color=c.road_mark, lw=lw,
                         ls=_dash(4.5, 4.0, lw), zorder=3.5, solid_capstyle="butt")  # fmt: skip

    def label(self, p: PlacedObject) -> None:
        r = p.label_rect
        if r is None or not p.label:
            return
        sh = self.sh
        bbox = None
        if p.zone == CROSS_LINE:  # подпись не должна зачёркиваться линией
            bbox = {"boxstyle": "square,pad=0.1", "fc": "white", "ec": "none", "alpha": 0.9}
        elif p.zone == CROSS_BAND:  # белая плашка: подпись читается на сером
            bbox = {"boxstyle": "square,pad=0.15", "fc": "white", "ec": "none"}
        self.ax.text(
            self.X((r.x0 + r.x1) / 2), (r.y0 + r.y1) / 2, p.label, ha="center", va="center",
            fontsize=sh.ls.font_pt, fontfamily=list(sh.fam), color="black", clip_on=False,
            zorder=7, bbox=bbox,
        )  # fmt: skip
        if p.callout_level > 0:
            base = p.band_rect or p.icon_rect
            cx = self.X((r.x0 + r.x1) / 2)
            if r.y0 >= base.y1:  # подпись выше значка
                ys = (base.y1, r.y0)
            else:
                ys = (base.y0, r.y1)
            self.ax.plot([cx, cx], list(ys), color=self.rs.colors.leader,
                         lw=self.rs.lines.leader, zorder=6)  # fmt: skip


def _strip_objects(
    sh: _Sheet,
    y: float,
    h: float,
    axes_h: float,
    above: float,
    below: float,
    placement: SectionPlacement,
) -> None:
    rs, sec = sh.rs, sh.sec
    pad = rs.geometry.objects_pad_mm
    ax = sh.top_axis_axes(y, h)
    sh.setup_x(ax, top=True)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_yticks([])
    ax.patch.set_visible(False)
    y_lo, y_hi = -below - pad, above + pad
    ax.set_ylim(y_lo, y_hi)
    s, e = sec.start_m, sec.end_m
    # небо и земля: градиентные фоны выше и ниже трубы
    c = rs.colors
    ax.imshow(_gradient(c.sky_top, c.sky_bottom), extent=(s, e, 0, y_hi), aspect="auto",
              interpolation="bilinear", zorder=0)  # fmt: skip
    ax.imshow(_gradient(c.ground_top, c.ground_bottom), extent=(s, e, y_lo, 0), aspect="auto",
              interpolation="bilinear", zorder=0)  # fmt: skip
    # вертикальный пунктир по делениям оси
    lw = rs.lines.axis
    for t in tick_positions(sec):
        ax.plot([t, t], [y_lo, y_hi], color=c.grid, lw=lw, ls=_dash(3.0, 2.5, lw), zorder=1)
    # труба
    ax.plot([s, e], [0, 0], color=c.pipe, lw=rs.lines.pipe, zorder=3.2, solid_capstyle="butt")
    ax.plot([s, e], [0, 0], color=c.pipe_light, lw=rs.lines.pipe * 0.3, zorder=3.3,
            solid_capstyle="butt")  # fmt: skip
    drawer = _ObjectsDrawer(sh, ax, y_lo, y_hi)
    if not placement.objects:
        sh.no_data(ax)
    for p in placement.objects:
        drawer.draw(p)
    ax.set_xlim(s, s + sec.nominal_length_m)
    ax.set_ylim(y_lo, y_hi)
    if axes_h <= 0:  # защита от вырожденной геометрии
        raise ValueError("Нулевая высота полосы объектов.")

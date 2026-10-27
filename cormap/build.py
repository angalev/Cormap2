"""Построение карты целиком: книга -> PDF, PNG и архив PNG (ТЗ, раздел 8).

Листы строятся по одному: фигура создаётся, пишется в PDF и PNG и освобождается, поэтому
расход памяти не растёт с числом листов. Модуль не зависит от Django и pyplot.
"""

from __future__ import annotations

import gc
import math
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from matplotlib.backends.backend_pdf import PdfPages

from cormap.check import check_workbook
from cormap.defects import defect_objects
from cormap.layout import (
    LayoutError,
    Section,
    SplitResult,
    parse_split_spec,
    place_objects,
    split_auto,
    split_manual,
)
from cormap.layout_settings import LayoutSettings, load_settings
from cormap.legend import render_legend
from cormap.model import CheckReport, Issue, fmt_num
from cormap.notes import used_types
from cormap.params import BuildParams
from cormap.reader import SHEET_NAME
from cormap.render_pdf import (
    PNG_DPI,
    make_context,
    render_sheet1,
    render_sheet2,
)
from cormap.render_settings import RenderSettings, load_render_settings
from cormap.render_xlsx import write_xlsx

PDF_NAME = "karta.pdf"
ZIP_NAME = "karta_png.zip"
XLSX_NAME = "karta.xlsx"
PNG_DIR = "png"


class BuildError(Exception):
    """Построение невозможно: блокирующие ошибки проверки или неверное разбиение.

    ``report`` - отчёт проверки (есть всегда), текст ошибки - его текстовый вид.
    """

    def __init__(self, message: str, report: CheckReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(slots=True)
class BuildResult:
    """Результат построения."""

    out_dir: Path
    pdf_path: Path
    png_paths: list[Path]
    zip_path: Path | None
    xlsx_path: Path | None
    sections: tuple[Section, ...]
    report: CheckReport
    split: SplitResult
    split_issues: tuple[Issue, ...]  # предупреждения разбиения (наложения и т. п.)
    pages: int  # страниц в PDF, включая лист условных обозначений
    timings: dict[str, float] = field(default_factory=dict)  # стадия -> секунды

    @property
    def total_time(self) -> float:
        return sum(self.timings.values())

    def format_timings(self) -> str:
        return ", ".join(f"{k} {v:.1f} с" for k, v in self.timings.items())


class _Timer:
    def __init__(self) -> None:
        self.t: dict[str, float] = {}

    def add(self, stage: str, since: float) -> float:
        now = time.perf_counter()
        self.t[stage] = self.t.get(stage, 0.0) + (now - since)
        return now


def _png_name(index: int, sheet: int, width: int) -> str:
    return f"list_{index:0{width}d}_{sheet}.png"


def build_map(
    path: str | Path,
    params: BuildParams,
    out_dir: str | Path,
    *,
    png: bool = True,
    xlsx: bool = True,
    zip_png: bool = True,
    progress: Callable[[int, int], None] | None = None,
    layout: LayoutSettings | None = None,
    render: RenderSettings | None = None,
    sheets: Sequence[str] = (SHEET_NAME,),
) -> BuildResult:
    """Строит карту по книге ``path`` в папке ``out_dir``.

    ``sheets`` - имена листа данных по порядку предпочтения (см. ``read_workbook``).

    Результат: ``karta.pdf`` (лист условных обозначений по params.legend, затем два
    листа на участок), ``png/list_NN_K.png`` (при ``png=True``), ``karta_png.zip``
    (при ``png=True`` и ``zip_png=True``; сайт собирает архив сам при скачивании),
    ``karta.xlsx`` - книга Excel с графиками (при ``xlsx=True``).
    ``progress(done, total)`` вызывается после каждого листа. Блокирующие ошибки
    проверки и неверное разбиение дают BuildError с отчётом.
    """
    timer = _Timer()
    t = time.perf_counter()
    layout = layout or load_settings()
    render = render or load_render_settings()

    survey, report, notes = check_workbook(path, sheets)
    t = timer.add("проверка", t)
    if survey is None or notes is None or not report.ok:
        raise BuildError(report.format_text(), report)

    # Ручные повреждения участвуют в размещении наравне с комментариями.
    objects = (*notes.objects, *defect_objects(survey))
    rng = survey.distance_range
    lo, hi = rng if rng is not None else (0.0, 0.0)
    try:
        spec = params.split_text
        if spec.lower() == "auto":
            split = split_auto(objects, lo, hi, layout, params.paper, start_km=params.start_km)
        else:
            split = split_manual(
                lo, hi, parse_split_spec(spec), layout, objects, params.paper,
                start_km=params.start_km,
            )  # fmt: skip
    except LayoutError as exc:
        report.error("split_error", str(exc))
        raise BuildError(report.format_text(), report) from exc
    for issue in split.issues:
        report.add(issue)
    if params.res_axis_max is not None:
        values = [v for v in survey.soil.resistivity if math.isfinite(v)]
        if values and max(values) > params.res_axis_max:
            report.warning(
                "res_above_axis",
                f"УЭС выше заданного верха оси ({fmt_num(params.res_axis_max)} Ом·м): "
                f"наибольшее значение {fmt_num(max(values))} Ом·м, на графике такие участки "
                "обрезаны. Увеличьте «Верх оси УЭС» или оставьте поле пустым.",
            )
    t = timer.add("разбиение", t)

    sections = split.sections
    ctx = make_context(survey, notes.objects, len(sections), layout=layout, render=render)
    t = timer.add("подготовка данных", t)

    out = Path(out_dir)
    png_dir = out / PNG_DIR
    out.mkdir(parents=True, exist_ok=True)
    if png:
        png_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out / PDF_NAME
    pngs: list[Path] = []
    total = len(sections) * 2 + (1 if params.legend != "none" else 0)
    done = 0
    width = max(2, len(str(len(sections))))
    pages = 0

    with PdfPages(pdf_path) as pdf:
        if params.legend != "none":
            if params.legend == "used":
                keys = [o.key for o in used_types(objects)]
                soil = [m.value for m in survey.soil.types]
                ins = [m.value for m in survey.insulation.types]
                fig = render_legend(keys, soil, ins, params.paper)
            else:
                fig = render_legend(paper=params.paper)
            t = timer.add("лист обозначений", t)
            pdf.savefig(fig, dpi=PNG_DPI)
            pages += 1
            del fig
            t = timer.add("запись PDF", t)
            done += 1
            if progress:
                progress(done, total)
        for sec in sections:
            placement = place_objects(objects, sec, layout, params.paper)
            t = timer.add("размещение", t)
            for k in (1, 2):
                fig = (
                    render_sheet1(survey, sec, params, ctx)
                    if k == 1
                    else render_sheet2(survey, sec, placement, params, ctx)
                )
                t = timer.add("рисование листов", t)
                pdf.savefig(fig, dpi=PNG_DPI)
                pages += 1
                t = timer.add("запись PDF", t)
                if png:
                    target = png_dir / _png_name(sec.index, k, width)
                    fig.savefig(target, dpi=PNG_DPI, format="png", pil_kwargs={"compress_level": 3})
                    pngs.append(target)
                    t = timer.add("запись PNG", t)
                fig.clear()
                del fig
                done += 1
                if progress:
                    progress(done, total)
            if sec.index % 25 == 0:
                gc.collect()

    zip_path: Path | None = None
    if png and zip_png:
        zip_path = out / ZIP_NAME
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for p in pngs:
                zf.write(p, p.name)
        t = timer.add("архив PNG", t)
    xlsx_path: Path | None = None
    if xlsx:
        xlsx_path = out / XLSX_NAME
        write_xlsx(
            xlsx_path, path, survey, sections, objects, params, ctx, stage=timer.add,
        )  # fmt: skip
    return BuildResult(
        out_dir=out,
        pdf_path=pdf_path,
        png_paths=pngs,
        zip_path=zip_path,
        xlsx_path=xlsx_path,
        sections=sections,
        report=report,
        split=split,
        split_issues=split.issues,
        pages=pages,
        timings=timer.t,
    )

"""Командная строка: python -m cormap check книга.xlsx [--split ...]."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cormap.build import BuildError, build_map
from cormap.check import check_and_split
from cormap.notes import format_notes
from cormap.params import BuildParams, ParamsError
from cormap.preprocess import (
    FLAG_LABELS,
    FLAG_NAMES,
    STEP_DEFAULT,
    RefineOptions,
    run_refinery_step,
    validate,
)
from cormap.template import build_template


def _cmd_check(args: argparse.Namespace) -> int:
    _, report, notes, split = check_and_split(
        args.book, args.split, args.paper, args.start_km, sheets=(args.sheet,)
    )
    sections = [format_notes(notes)] if notes is not None else []
    if split is not None:
        sections.append(split.format_text())
    print(report.format_text(sections))
    return 0 if report.ok else 1


def _number(text: str) -> float:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise argparse.ArgumentTypeError(f"не число: «{text}»") from None


def _cmd_build(args: argparse.Namespace) -> int:
    try:
        params = BuildParams(
            object_name=args.name,
            diameter_mm=args.diameter,
            start_km=args.start_km,
            u_min=args.u_min,
            u_max=args.u_max,
            depth_min=args.depth_min,
            res_low=args.res_low,
            res_high=args.res_high,
            res_axis_max=args.res_axis_max,
            paper=args.paper,
            split=args.split,
            legend=args.legend,
        )
    except ParamsError as exc:
        print(f"Ошибка параметров: {exc}", file=sys.stderr)
        return 2
    try:
        result = build_map(
            args.book,
            params,
            args.out,
            png=not args.no_png,
            xlsx=not args.no_xlsx,
            sheets=(args.sheet,),
        )
    except BuildError as exc:
        print(exc)
        return 1
    print(f"Карта построена: {result.out_dir}")
    print(f"  PDF: {result.pdf_path} (страниц: {result.pages})")
    if result.zip_path is not None:
        print(f"  PNG: {len(result.png_paths)} файлов, архив {result.zip_path}")
    if result.xlsx_path is not None:
        print(f"  Excel: {result.xlsx_path}")
    print(result.split.format_text())
    for issue in result.report.issues:
        print(f"  {issue.format()}")
    print(f"Время: {result.format_timings()}; всего {result.total_time:.1f} с")
    return 0


def _cmd_refine(args: argparse.Namespace) -> int:
    options = RefineOptions(
        **{name: getattr(args, name) for name in FLAG_NAMES}, step=args.step, random_seed=args.seed
    )
    problems = validate(options)
    if not options.any_flag:  # на сайте без флажков шаг пропускается; здесь вызывать нечего
        problems.insert(0, ("", "Выставьте хотя бы один флажок обработки данных."))
    if problems:
        for field, text in problems:
            print(f"Ошибка параметров{f' ({field})' if field else ''}: {text}", file=sys.stderr)
        return 2
    outcome = run_refinery_step(Path(args.book), Path(args.out), options)
    if not outcome.ok:
        print(f"[{outcome.error_code}] {outcome.error_message}", file=sys.stderr)
        return 1
    print(f"Книга обработана: {args.out}")
    stats = outcome.stats
    if stats is not None:
        print(f"  Строк на входе: {stats.input_rows}, на выходе: {stats.output_rows}")
        print(f"  Диапазон дистанций, м: {stats.distance_range_m}")
        print(f"  Сформированные колонки: {stats.columns_generated}")
        print(f"  Время расчёта, мс: {stats.calculation_time_ms}")
    return 0


def _cmd_template(args: argparse.Namespace) -> int:
    build_template(Path(args.out), args.sheet_name)
    print(f"Шаблон книги: {args.out} (лист данных «{args.sheet_name}»)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cormap", description="Коррозионные карты.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="проверить книгу Excel")
    check.add_argument("book", help="путь к книге .xlsx")
    check.add_argument(
        "--split",
        default="auto",
        help='разбиение на листы: auto, длина листа в метрах или границы "0;3000;5000"',
    )
    check.add_argument(
        "--paper", default="A4", type=str.upper, choices=["A4", "A3"], help="формат бумаги"
    )
    check.add_argument(
        "--start-km", type=_number, default=0.0, help="начальный километраж, км (по умолчанию 0)"
    )
    check.add_argument(
        "--sheet",
        default="Result",
        choices=["Template", "Result"],
        help="лист данных: Result - книга после обработки, Template - книга без обработки",
    )
    check.set_defaults(func=_cmd_check)

    build = sub.add_parser("build", help="построить карту: PDF, PNG, архив PNG и книга Excel")
    build.add_argument("book", help="путь к книге .xlsx")
    build.add_argument("--out", required=True, help="папка результата")
    build.add_argument("--name", required=True, help="наименование объекта (заголовок листа 1)")
    build.add_argument(
        "--split",
        default="auto",
        help='разбиение на листы: auto, длина листа в метрах или границы "0;3000;5000"',
    )
    build.add_argument(
        "--paper", default="A4", type=str.upper, choices=["A4", "A3"], help="формат бумаги"
    )
    build.add_argument("--diameter", type=_number, default=None, help="диаметр, мм")
    build.add_argument("--start-km", type=_number, default=0.0, help="начальный километраж, км")
    build.add_argument("--u-min", type=_number, default=-0.85, help="мин. нормативный потенциал, В")
    build.add_argument(
        "--u-max", type=_number, default=-2.50, help="макс. нормативный потенциал, В"
    )
    build.add_argument("--depth-min", type=_number, default=0.80, help="мин. глубина, м")
    build.add_argument("--res-low", type=_number, default=20.0, help="нижний порог УЭС, Ом*м")
    build.add_argument("--res-high", type=_number, default=50.0, help="верхний порог УЭС, Ом*м")
    build.add_argument(
        "--res-axis-max",
        type=_number,
        default=None,
        help="верх оси УЭС, Ом*м (по умолчанию подбирается по данным участка)",
    )
    build.add_argument(
        "--legend", default="all", choices=["all", "used", "none"], help="лист условных обозначений"
    )
    build.add_argument("--no-png", action="store_true", help="не писать PNG и архив")
    build.add_argument("--no-xlsx", action="store_true", help="не писать книгу Excel")
    build.add_argument(
        "--sheet",
        default="Result",
        choices=["Template", "Result"],
        help="лист данных: Result - книга после обработки, Template - книга без обработки",
    )
    build.set_defaults(func=_cmd_build)

    refine = sub.add_parser("refine", help="предварительная обработка книги модулем refinery")
    refine.add_argument("book", help="входная книга .xlsx (лист Template)")
    refine.add_argument("out", help="выходная книга .xlsx (лист Result)")
    for name in FLAG_NAMES:
        refine.add_argument(f"--{name}", action="store_true", help=FLAG_LABELS[name])
    refine.add_argument(
        "--step", type=int, default=STEP_DEFAULT, help="шаг интерполяции, м (1-10, по умолчанию 5)"
    )
    refine.add_argument("--seed", type=int, default=None, help="начальное значение случайности")
    refine.set_defaults(func=_cmd_refine)

    template = sub.add_parser("template", help="выгрузить пустой шаблон книги Excel")
    template.add_argument("out", help="выходная книга .xlsx")
    template.add_argument(
        "--sheet-name",
        default="Result",
        choices=["Template", "Result"],
        help="имя листа данных (Template - для обработки модулем refinery)",
    )
    template.set_defaults(func=_cmd_template)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Консоль Windows может не уметь часть символов: не падаем, а заменяем.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)

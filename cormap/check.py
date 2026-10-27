"""Полная проверка книги: чтение, разбор комментариев, предложенное разбиение на листы.

Функции вызываются из CLI и (позже) из сайта.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cormap.defects import collect_defects, defect_objects, load_criteria, pip_marks
from cormap.layout import LayoutError, SplitResult, parse_split_spec, split_auto, split_manual
from cormap.layout_settings import LayoutSettings
from cormap.model import CheckReport, Survey
from cormap.notes import NotesResult, parse_comments
from cormap.reader import SHEET_NAME, read_workbook


def check_workbook(
    path: str | Path, sheets: Sequence[str] = (SHEET_NAME,)
) -> tuple[Survey | None, CheckReport, NotesResult | None]:
    """Читает книгу и разбирает комментарии.

    ``sheets`` - имена листа данных по порядку предпочтения (см. ``read_workbook``).

    Замечания разбора добавляются в общий отчёт. Если книгу прочитать нельзя,
    третий элемент равен None.
    """
    survey, report = read_workbook(path, sheets=sheets)
    if survey is None:
        return None, report, None
    notes = parse_comments(survey.comments.items)
    for issue in notes.issues:
        report.add(issue)
    # Замечания по повреждениям (например, класс не определён) - в отчёт проверки.
    _, defect_issues = collect_defects(survey, load_criteria(), pip_marks(notes.objects))
    for issue in defect_issues:
        report.add(issue)
    return survey, report, notes


def propose_split(
    survey: Survey,
    notes: NotesResult,
    split: str = "auto",
    paper: str = "A4",
    start_km: float = 0.0,
    settings: LayoutSettings | None = None,
) -> SplitResult:
    """Предложенное разбиение на листы: «auto», длина листа в метрах или список границ.

    Бросает LayoutError (текст по-русски) при неверных параметрах.
    """
    rng = survey.distance_range
    lo, hi = rng if rng is not None else (0.0, 0.0)
    # Ручные повреждения участвуют в размещении наравне с комментариями (как в build_map).
    objects = (*notes.objects, *defect_objects(survey))
    text = str(split).strip()
    if text.lower() in ("", "auto", "авто"):
        return split_auto(objects, lo, hi, settings, paper, start_km=start_km)
    return split_manual(lo, hi, parse_split_spec(text), settings, objects, paper, start_km=start_km)


def check_and_split(
    path: str | Path,
    split: str = "auto",
    paper: str = "A4",
    start_km: float = 0.0,
    settings: LayoutSettings | None = None,
    sheets: Sequence[str] = (SHEET_NAME,),
) -> tuple[Survey | None, CheckReport, NotesResult | None, SplitResult | None]:
    """Проверка книги и предложенное разбиение на листы.

    Предупреждения разбиения (наложения и т. п.) попадают в общий отчёт, ошибка
    параметров разбиения - тоже (как блокирующая). Разбиение строится, только если
    книга прочитана без блокирующих ошибок.
    """
    survey, report, notes = check_workbook(path, sheets)
    if survey is None or notes is None or not report.ok:
        return survey, report, notes, None
    try:
        result = propose_split(survey, notes, split, paper, start_km, settings)
    except LayoutError as exc:
        report.error("split_error", str(exc))
        return survey, report, notes, None
    for issue in result.issues:
        report.add(issue)
    return survey, report, notes, result

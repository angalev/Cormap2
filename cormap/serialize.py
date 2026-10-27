"""Сериализация результатов проверки в JSON-совместимые структуры (для сайта и кэшей).

Модуль не зависит от Django. Сайт хранит результат проверки в базе и пересчитывает
разбиение на листы по сохранённым объектам, не перечитывая книгу.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from cormap.layout import (
    TABLE_HEADER,
    SplitResult,
    parse_split_spec,
    split_auto,
    split_manual,
)
from cormap.layout_settings import LayoutSettings
from cormap.model import BlockSummary, CheckReport, Issue
from cormap.notes import (
    POINT,
    ROLE_TITLES,
    UNPAIRED_CODES,
    NoteRecord,
    NotesResult,
    TrackObject,
    get_type,
)


def issue_to_dict(issue: Issue) -> dict[str, Any]:
    return {
        "level": issue.level,
        "code": issue.code,
        "message": issue.message,
        "cell": issue.cell,
        "row": issue.row,
    }


def issue_from_dict(data: dict[str, Any]) -> Issue:
    return Issue(data["level"], data["code"], data["message"], data.get("cell"), data.get("row"))


def block_to_dict(block: BlockSummary) -> dict[str, Any]:
    return {
        "name": block.name,
        "rows": block.rows,
        "dist_min": block.dist_min,
        "dist_max": block.dist_max,
        "step": block.step,
        "details": block.details,
    }


def report_to_dict(report: CheckReport) -> dict[str, Any]:
    """Отчёт проверки: сводка блоков, замечания, число не сохранённых замечаний."""
    return {
        "sheet": report.sheet,
        "ok": report.ok,
        "blocks": [block_to_dict(b) for b in report.blocks],
        "issues": [issue_to_dict(i) for i in report.issues],
        "suppressed": dict(report.suppressed),
    }


def describe_record(rec: NoteRecord) -> str:
    """Текст «как распознана» для строки таблицы разбора комментариев."""
    if rec.type_key is None:
        return "НЕ РАСПОЗНАНА"
    what = get_type(rec.type_key).title
    if rec.role != POINT:
        what += f", {ROLE_TITLES[rec.role]}"
    if rec.voltage:
        what += f", {rec.voltage}"
    if rec.label:
        what += f", подпись «{rec.label}»"
    return what


def notes_to_dict(notes: NotesResult) -> dict[str, Any]:
    """Таблица «строка - пометка - как распознана» и сводка (без самих объектов)."""
    return {
        "records": [
            {
                "row": r.row,
                "dist": r.dist,
                "text": r.text,
                "recognized": r.type_key is not None,
                "what": describe_record(r),
            }
            for r in notes.records
        ],
        "recognized": notes.recognized,
        "unknown": notes.unknown,
        "unpaired": notes.unpaired,
        "unpaired_issues": [issue_to_dict(i) for i in notes.issues if i.code in UNPAIRED_CODES],
    }


def object_to_dict(obj: TrackObject) -> dict[str, Any]:
    return {
        "type_key": obj.type_key,
        "label": obj.label,
        "start": obj.start,
        "end": obj.end,
        "row_start": obj.row_start,
        "row_end": obj.row_end,
        "voltage": obj.voltage,
        "variant": obj.variant,
    }


def object_from_dict(data: dict[str, Any]) -> TrackObject:
    return TrackObject(
        type_key=data["type_key"],
        label=data["label"],
        start=float(data["start"]),
        end=None if data.get("end") is None else float(data["end"]),
        row_start=int(data["row_start"]),
        row_end=None if data.get("row_end") is None else int(data["row_end"]),
        voltage=data.get("voltage"),
        variant=data.get("variant", ""),
    )


def objects_to_list(objects: Iterable[TrackObject]) -> list[dict[str, Any]]:
    return [object_to_dict(o) for o in objects]


def objects_from_list(items: Iterable[dict[str, Any]]) -> tuple[TrackObject, ...]:
    return tuple(object_from_dict(d) for d in items)


def split_to_dict(split: SplitResult) -> dict[str, Any]:
    """Разбиение: режим, таблица (строки текстом), предупреждения, границы участков, м."""
    return {
        "mode": split.mode,
        "paper": split.paper,
        "start_km": split.start_km,
        "header": list(TABLE_HEADER),
        "table": [list(row) for row in split.table()],
        "issues": [issue_to_dict(i) for i in split.issues],
        "has_overlaps": split.has_overlaps,
        "count": len(split.sections),
        # наложения: [лист, объект, с чем наложился, дистанция, м] - до 5 на участок
        "conflicts": [
            [s.index, name, other, dist]
            for s in split.sections
            for name, other, dist in s.conflicts
        ],
        "bounds_m": [split.sections[0].start_m, *(s.end_m for s in split.sections)]
        if split.sections
        else [],
    }


def resplit(
    objects: Sequence[TrackObject],
    dist_range: tuple[float, float] | None,
    split: str = "auto",
    paper: str = "A4",
    start_km: float = 0.0,
    settings: LayoutSettings | None = None,
) -> SplitResult:
    """Пересчёт разбиения по готовым объектам, без книги (аналог check.propose_split).

    ``objects`` - объекты комментариев и ручные повреждения (как в build_map).
    Бросает LayoutError (текст по-русски) при неверных параметрах.
    """
    lo, hi = dist_range if dist_range is not None else (0.0, 0.0)
    text = str(split).strip()
    if text.lower() in ("", "auto", "авто"):
        return split_auto(objects, lo, hi, settings, paper, start_km=start_km)
    return split_manual(lo, hi, parse_split_spec(text), settings, objects, paper, start_km=start_km)

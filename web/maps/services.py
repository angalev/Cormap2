"""Общая логика сайта и обработчика: параметры карты, состояние разбиения, сводки."""

from __future__ import annotations

import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from cormap.layout import SplitResult
from cormap.params import BuildParams
from cormap.preprocess import RefineOptions, refinery_available
from cormap.reader import RAW_SHEETS, SHEET_NAME
from cormap.serialize import split_to_dict
from maps import storage
from maps.models import MapJob

# Ключ настроек модуля refinery внутри MapJob.params (это не параметр построения карты)
REFINERY_KEY = "refinery"

# Коды замечаний разбиения: хранятся вместе с разбиением, а не с проверкой книги.
SPLIT_ISSUE_PREFIX = "layout_"
SPLIT_ERROR_CODE = "split_error"


def is_split_issue(code: str) -> bool:
    return code.startswith(SPLIT_ISSUE_PREFIX) or code == SPLIT_ERROR_CODE


def params_to_dict(params: BuildParams, refinery: dict[str, Any] | None = None) -> dict[str, Any]:
    """Параметры построения в виде JSON (поля BuildParams; split - «auto», метры или список).

    ``refinery`` - настройки модуля обработки данных (RefineOptions.to_dict()): хранятся
    рядом с параметрами карты под своим ключом и в BuildParams не попадают.
    """
    data = asdict(params)
    if isinstance(data["split"], tuple):
        data["split"] = list(data["split"])
    if refinery is not None:
        data[REFINERY_KEY] = refinery
    return data


def params_from_job(job: MapJob) -> BuildParams:
    """BuildParams из сохранённых параметров карты (ParamsError при испорченных данных)."""
    return BuildParams(**{k: v for k, v in job.params.items() if k != REFINERY_KEY})


def refinery_dict(job: MapJob) -> dict[str, Any] | None:
    """Сохранённые настройки модуля обработки карты (None - шага у карты нет)."""
    data = (job.params or {}).get(REFINERY_KEY)
    return data if isinstance(data, dict) else None


def refinery_options(job: MapJob) -> RefineOptions | None:
    data = refinery_dict(job)
    return RefineOptions.from_dict(data) if data is not None else None


def with_new_seed(options: RefineOptions) -> dict[str, Any]:
    """Настройки с новым сидом (его выбирает сервис при загрузке и при замене книги)."""
    data = options.to_dict()
    data["random_seed"] = secrets.randbelow(2**31)
    return data


def refinery_enabled() -> bool:
    """Шаг обработки включён: auto - если модуль установлен, required - всегда, off - нет."""
    mode = getattr(settings, "CORMAP_REFINERY", "auto")
    if mode == "required":
        return True
    if mode == "off":
        return False
    return refinery_available()


def book_path(job: MapJob) -> Path:
    """Книга для проверки и построения: обработанная, если шаг был, иначе загруженная."""
    return storage.resolve(job.processed_path or job.source_path)


def book_sheets(job: MapJob) -> tuple[str, ...]:
    """Лист данных книги: `Result` после модуля, иначе `Template` (нет его - `Result`)."""
    return (SHEET_NAME,) if job.processed_path else RAW_SHEETS


def requeue_if_missing(job: MapJob) -> bool:
    """Готовая карта без файлов результата ставится в очередь на построение.

    True - карта поставлена в очередь. Без результата проверки и разбиения строить
    не из чего: такая карта остаётся как есть.
    """
    if not job.results_missing or not job.check_result or not job.split:
        return False
    queued = MapJob.objects.filter(pk=job.pk, state=MapJob.State.DONE).update(
        state=MapJob.State.QUEUED,
        attempts=0,
        error_text="",
        progress_done=0,
        progress_total=0,
        updated_at=timezone.now(),
    )
    return bool(queued)


def split_kind(spec: str | float | list | tuple) -> str:
    if isinstance(spec, str):
        return "auto"
    if isinstance(spec, int | float):
        return "length"
    return "bounds"


def make_split_state(result: SplitResult, spec: str | float | list | tuple) -> dict[str, Any]:
    """Текущее разбиение для хранения: режим, границы, таблица, предупреждения."""
    data = split_to_dict(result)
    data["kind"] = split_kind(spec)  # auto / length / bounds (режим формы)
    data["spec"] = list(spec) if isinstance(spec, tuple) else spec
    return data


def png_name(index: int, sheet: int, sections: int) -> str:
    """Имя PNG листа - по правилам cormap.build (list_01_1.png)."""
    width = max(2, len(str(sections)))
    return f"list_{index:0{width}d}_{sheet}.png"


def error_summary(errors: list[dict[str, Any]], limit: int = 3) -> str:
    """Короткий текст о блокирующих ошибках проверки (без трассировок)."""
    parts = []
    for item in errors[:limit]:
        place = (
            f"{item['cell']}: "
            if item.get("cell")
            else (f"строка {item['row']}: " if item.get("row") else "")
        )
        parts.append(f"{place}{item['message']}")
    rest = len(errors) - limit
    text = " ".join(parts) if parts else "Книга не прошла проверку."
    if rest > 0:
        text += f" (и ещё ошибок: {rest})"
    return text

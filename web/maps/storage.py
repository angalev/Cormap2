"""Пути к файлам на диске.

Пути в базе хранятся относительно CORMAP_DATA_DIR. Путь к файлу никогда не берётся из
запроса: только из записи MapJob и (для предпросмотра) из номера листа.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.http import Http404


def data_dir() -> Path:
    return Path(settings.CORMAP_DATA_DIR)


def uploads_dir() -> Path:
    return data_dir() / "uploads"


def results_root() -> Path:
    return data_dir() / "results"


def new_upload_name() -> str:
    """Имя файла загрузки на диске (относительный путь): сгенерированное, расширение .xlsx."""
    return f"uploads/{uuid.uuid4().hex}.xlsx"


def new_processed_name() -> str:
    """Имя обработанной книги на диске (относительный путь), рядом с загруженными."""
    return f"uploads/{uuid.uuid4().hex}_processed.xlsx"


PROCESSED_SUFFIXES = ("_processed.xlsx", "_processed_trimmed.xlsx")


def remove_orphan_processed(known: set[str]) -> int:
    """Удаляет обработанные книги и их временные копии, не числящиеся ни за одной картой.

    Такие файлы остаются, когда обработку прервали на полпути. ``known`` - пути
    обработанных книг из базы. Вызывает только обработчик, когда сам ничего не пишет.
    """
    count = 0
    try:
        files = [p for p in uploads_dir().iterdir() if p.name.endswith(PROCESSED_SUFFIXES)]
    except OSError:
        return 0
    for path in files:
        if f"uploads/{path.name}" in known:
            continue
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count


def job_results_rel(job_id: int) -> str:
    return f"results/{job_id}"


def resolve(rel: str) -> Path:
    """Абсолютный путь внутри каталога данных; выход за его пределы - Http404."""
    if not rel:
        raise Http404("Файл не найден.")
    root = data_dir().resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise Http404("Файл не найден.")
    return path


def remove_file(rel: str) -> None:
    if not rel:
        return
    try:
        path = resolve(rel)
        if path.is_file():
            path.unlink()
    except (Http404, OSError):
        pass


def remove_tree(rel: str) -> None:
    if not rel:
        return
    try:
        path = resolve(rel)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except (Http404, OSError):
        pass

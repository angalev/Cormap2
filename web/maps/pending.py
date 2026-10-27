"""Прикреплённая книга формы, которая не прошла проверку (ТЗ, 4.2).

Браузер не возвращает выбранный файл в поле после ответа сервера. Поэтому книгу, которая
сама по себе годна, сайт откладывает в каталог ``pending`` и запоминает в сессии; при
повторной отправке формы файл выбирать заново не нужно.

Путь к отложенному файлу хранится только в сессии на сервере и из запроса не берётся.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from django.http import Http404, HttpRequest

from maps import storage

# Отложенные файлы старше этого срока удаляются (сессия живёт 12 часов)
MAX_AGE_SECONDS = 24 * 60 * 60

_SESSION_PREFIX = "pending_book:"


def _dir() -> Path:
    return storage.data_dir() / "pending"


def _session_key(key: str) -> str:
    return _SESSION_PREFIX + key


def get(request: HttpRequest, key: str) -> dict[str, Any] | None:
    """Сведения об отложенной книге (``rel``, ``name``, ``size``) или None."""
    info = request.session.get(_session_key(key))
    if not isinstance(info, dict):
        return None
    try:
        if storage.resolve(str(info.get("rel", ""))).is_file():
            return info
    except (Http404, OSError):  # путь вне каталога данных или сбой диска: файла нет
        pass
    request.session.pop(_session_key(key), None)
    return None


def keep(request: HttpRequest, key: str, upload) -> dict[str, Any]:
    """Откладывает загруженную книгу (вместо прежней отложенной)."""
    drop(request, key)
    _sweep()
    rel = f"pending/{uuid.uuid4().hex}.xlsx"
    path = storage.resolve(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    upload.seek(0)
    with open(path, "wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    info = {"rel": rel, "name": Path(upload.name).name[:255], "size": upload.size}
    request.session[_session_key(key)] = info
    return info


def take(request: HttpRequest, key: str) -> tuple[str, str, int] | None:
    """Переносит отложенную книгу в загрузки: (путь, исходное имя, размер) или None."""
    info = get(request, key)
    if info is None:
        return None
    rel = storage.new_upload_name()
    target = storage.resolve(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(storage.resolve(info["rel"]), target)
    request.session.pop(_session_key(key), None)
    return rel, str(info["name"]), int(info["size"])


def drop(request: HttpRequest, key: str) -> None:
    """Забывает отложенную книгу и удаляет её файл."""
    info = request.session.pop(_session_key(key), None)
    if isinstance(info, dict):
        storage.remove_file(str(info.get("rel", "")))


def _sweep() -> None:
    """Удаляет брошенные отложенные файлы (форму не дозаполнили, сессия кончилась)."""
    folder = _dir()
    if not folder.is_dir():
        return
    limit = time.time() - MAX_AGE_SECONDS
    for path in folder.glob("*.xlsx"):
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            pass

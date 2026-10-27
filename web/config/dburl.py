"""Разбор адреса базы данных из CORMAP_DATABASE_URL (без сторонних пакетов).

Поддерживаются адреса вида ``postgres://user:pass@host:5432/name`` и
``sqlite:///путь``. Драйвер PostgreSQL (psycopg) ставится только на сервере (группа
``server``). Пустой host (``postgres:///cormap``) - подключение через unix-сокет
(peer-аутентификация).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

_POSTGRES_SCHEMES = {"postgres", "postgresql", "pgsql"}


def parse_database_url(url: str) -> dict[str, Any]:
    """Возвращает словарь для DATABASES["default"]; неверный адрес даёт ValueError."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme in _POSTGRES_SCHEMES:
        name = unquote(parts.path.lstrip("/"))
        if not name:
            raise ValueError("В адресе базы не указано имя базы данных.")
        config: dict[str, Any] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": name,
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port) if parts.port else "",
            # соединение живёт минуту; перед использованием проверяется (Django 4.1+)
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
        return config
    if scheme == "sqlite":
        # sqlite:///относительный или sqlite:////абсолютный (как в других библиотеках)
        rest = url.strip()[len("sqlite://") :]
        path = unquote(rest[1:] if rest.startswith("/") else rest)
        if not path:
            raise ValueError("В адресе SQLite не указан путь к файлу.")
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}
    raise ValueError(f"Неподдерживаемый адрес базы данных: схема «{parts.scheme}».")

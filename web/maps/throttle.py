"""Ограничение попыток входа: для пары «логин + IP» и для логина с любых адресов.

После CORMAP_LOGIN_MAX_FAILURES (5) неудач в течение CORMAP_LOGIN_WINDOW_MINUTES (15)
пара блокируется на CORMAP_LOGIN_LOCK_MINUTES (15). После
CORMAP_LOGIN_NAME_MAX_FAILURES (10) неудач под одним логином с любых адресов так же
блокируется сам логин: это защита от подбора пароля со многих адресов. Пока блокировка
действует, пароль не проверяется. Успешный вход удаляет записи (счётчики сбрасываются).
Состояние хранится в таблице LoginAttempt (строка на пару и строка на логин с адресом
ANY_IP), поэтому работает при нескольких процессах сайта.
"""

from __future__ import annotations

import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from maps.models import LoginAttempt

ANY_IP = "*"  # в поле ip: счётчик логина по всем адресам


def clean_name(username: str) -> str:
    """Логин из запроса без нулевых байтов: PostgreSQL такую строку не принимает."""
    return username.replace("\x00", "")


def _key(username: str) -> str:
    return clean_name(username).strip().lower()[:150]


def _minutes_left(username: str, ip: str) -> int:
    row = LoginAttempt.objects.filter(username=_key(username), ip=ip).first()
    if row is None or row.locked_until is None:
        return 0
    left = (row.locked_until - timezone.now()).total_seconds()
    return max(math.ceil(left / 60), 1) if left > 0 else 0


def blocked_minutes(username: str, ip: str | None) -> int:
    """Сколько минут (с округлением вверх) осталось блокировки; 0 - не заблокирован."""
    return max(_minutes_left(username, ip or ""), _minutes_left(username, ANY_IP))


def _count_failure(username: str, ip: str, max_failures: int) -> None:
    window = timedelta(minutes=settings.CORMAP_LOGIN_WINDOW_MINUTES)
    lock = timedelta(minutes=settings.CORMAP_LOGIN_LOCK_MINUTES)
    now = timezone.now()
    with transaction.atomic():
        row, _ = LoginAttempt.objects.select_for_update().get_or_create(
            username=_key(username), ip=ip
        )
        expired = (row.locked_until is not None and row.locked_until <= now) or (
            row.window_start is None or now - row.window_start > window
        )
        if expired:
            row.failures = 0
            row.window_start = now
            row.locked_until = None
        row.failures += 1
        if row.failures >= max_failures:
            row.locked_until = now + lock
        row.save()


def register_failure(username: str, ip: str | None) -> None:
    _count_failure(username, ip or "", settings.CORMAP_LOGIN_MAX_FAILURES)
    _count_failure(username, ANY_IP, settings.CORMAP_LOGIN_NAME_MAX_FAILURES)


def reset(username: str, ip: str | None) -> None:
    LoginAttempt.objects.filter(username=_key(username), ip__in=(ip or "", ANY_IP)).delete()

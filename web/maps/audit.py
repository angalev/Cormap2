"""Запись в журнал действий и определение IP-адреса клиента."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from maps.models import AuditLog, MapJob


def client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    if getattr(settings, "CORMAP_TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        last = forwarded.split(",")[-1].strip()  # адрес, который видел nginx
        if last:
            return last
    return request.META.get("REMOTE_ADDR") or None


def log(
    action: str,
    *,
    request: HttpRequest | None = None,
    user=None,
    job: MapJob | None = None,
    details: str = "",
    username: str = "",
    object_repr: str = "",
) -> AuditLog:
    """Добавляет запись. ``user`` по умолчанию - пользователь запроса."""
    if user is None and request is not None:
        candidate = getattr(request, "user", None)
        if candidate is not None and candidate.is_authenticated:
            user = candidate
    if job is not None:
        object_repr = object_repr or str(job)
        if not MapJob.objects.filter(pk=job.pk).exists():
            job = None  # карту успели удалить: запись остаётся без ссылки, текст сохранён
    return AuditLog.objects.create(
        user=user,
        # нулевой байт из чужого запроса PostgreSQL не принимает
        username=(user.get_username() if user is not None else username).replace("\x00", "")[:150],
        action=action,
        job=job,
        object_repr=object_repr[:300],
        details=details,
        ip=client_ip(request),
    )

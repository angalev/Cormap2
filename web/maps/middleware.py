"""Заголовок Content-Security-Policy на каждом ответе сайта; доступ к админке по адресу."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseNotFound

from maps import audit


class AdminAccessMiddleware:
    """Админка только с адресов из CORMAP_ADMIN_ALLOWED_IPS (пусто - без ограничения).

    С остальных адресов /admin/ отвечает 404, как несуществующая страница. Вторая линия
    защиты после nginx. Признак ``request.admin_allowed`` нужен шаблону: ссылку на
    админку показывают только там, где она откроется.
    """

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        allowed = getattr(settings, "CORMAP_ADMIN_ALLOWED_IPS", [])
        request.admin_allowed = not allowed or audit.client_ip(request) in allowed
        path = request.path_info
        if not request.admin_allowed and (path == "/admin" or path.startswith("/admin/")):
            return HttpResponseNotFound("Not Found")
        return self.get_response(request)


class ContentSecurityPolicyMiddleware:
    """Ставит политику из CORMAP_CSP, если представление не задало свою.

    Отладочные страницы ошибок Django (только при DEBUG) содержат встроенные стили и
    скрипты, поэтому на них заголовок не ставится.
    """

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = getattr(settings, "CORMAP_CSP", "")
        debug_page = settings.DEBUG and response.status_code in (404, 500)
        if policy and not debug_page and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = policy
        return response

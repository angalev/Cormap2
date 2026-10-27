"""Защита сайта: Content-Security-Policy, смена пароля, журнал ошибок, разбор XML, юниты."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from conftest import PASSWORD
from django.contrib.auth import get_user_model
from maps.models import AuditLog

ROOT = Path(__file__).resolve().parents[2]
NEW_PASSWORD = "Novyj-parol-2026"

# исполняемый встроенный скрипт: без src и не блок данных application/json
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", re.I)


# --- Content-Security-Policy ----------------------------------------------------------------


def test_csp_on_pages(client, users, login, settings):
    anonymous = client.get("/login/")
    assert anonymous["Content-Security-Policy"] == settings.CORMAP_CSP
    login(users["a"])
    for url in ("/", "/maps/new/", "/forecast/", "/password/"):
        response = client.get(url)
        assert response.status_code == 200, url
        policy = response["Content-Security-Policy"]
        assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
        assert "unsafe-inline" not in policy and "unsafe-eval" not in policy


def test_csp_on_redirect_and_not_found(client, users, login, settings):
    settings.DEBUG = False
    assert "Content-Security-Policy" in client.get("/")  # перенаправление на вход
    login(users["a"])
    assert "Content-Security-Policy" in client.get("/maps/99999/")  # 404


def test_pages_have_no_inline_scripts(client, db):
    """Политика запрещает встроенные скрипты: их не должно быть ни на сайте, ни в админке."""
    admin = get_user_model().objects.create_superuser("boss", "boss@example.com", PASSWORD)
    client.force_login(admin)
    urls = ("/", "/maps/new/", "/forecast/", "/password/")
    admin_urls = ("/admin/", "/admin/maps/mapjob/", "/admin/maps/auditlog/", "/admin/auth/user/")
    for url in (*urls, *admin_urls, f"/admin/auth/user/{admin.pk}/change/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert not INLINE_SCRIPT.search(response.content.decode()), url


# --- смена пароля ---------------------------------------------------------------------------


def _change(client, old=PASSWORD, new=NEW_PASSWORD, repeat=None):
    data = {"old_password": old, "new_password1": new, "new_password2": repeat or new}
    return client.post("/password/", data)


def test_password_change(client, users, login):
    login(users["a"])
    response = _change(client)
    assert response.status_code == 302 and response["Location"] == "/"
    users["a"].refresh_from_db()
    assert users["a"].check_password(NEW_PASSWORD)
    assert client.get("/").status_code == 200  # текущий сеанс остаётся
    entry = AuditLog.objects.get(action="password")
    assert entry.username == "alice" and NEW_PASSWORD not in entry.details
    client.post("/logout/")
    old = client.post("/login/", {"username": "alice", "password": PASSWORD})
    assert old.status_code == 200  # прежний пароль больше не подходит
    assert (
        client.post("/login/", {"username": "alice", "password": NEW_PASSWORD}).status_code == 302
    )


def test_password_change_needs_current_password(client, users, login):
    login(users["a"])
    response = _change(client, old="не тот")
    assert response.status_code == 200
    users["a"].refresh_from_db()
    assert users["a"].check_password(PASSWORD)
    assert not AuditLog.objects.filter(action="password").exists()


@pytest.mark.parametrize("weak", ["12345678", "alice", "короткий"[:5]])
def test_password_change_rejects_weak(client, users, login, weak):
    login(users["a"])
    assert _change(client, new=weak).status_code == 200
    users["a"].refresh_from_db()
    assert users["a"].check_password(PASSWORD)


def test_password_change_ends_other_sessions(client, users, login):
    from django.test import Client

    other = Client()
    other.force_login(users["a"])
    login(users["a"])
    assert _change(client).status_code == 302
    assert other.get("/").status_code == 302  # второй сеанс завершён


def test_password_change_does_not_touch_other_user(client, users, login):
    login(users["a"])
    _change(client)
    users["b"].refresh_from_db()
    assert users["b"].check_password(PASSWORD)


# --- журнал ошибок, разбор XML, пределы памяти ----------------------------------------------


def test_django_errors_are_logged_without_debug(settings):
    """Ошибки 500 и события django.security пишутся в консоль и при DEBUG=0."""
    assert "console" in settings.LOGGING["loggers"]["django"]["handlers"]
    handlers = logging.getLogger("django").handlers
    assert any(isinstance(h, logging.StreamHandler) and not h.filters for h in handlers), (
        "у логгера django нет обработчика, работающего при DEBUG=0"
    )
    for name in ("django.request", "django.security"):
        assert logging.getLogger(name).propagate


def test_openpyxl_uses_defusedxml():
    from openpyxl.xml import DEFUSEDXML

    assert DEFUSEDXML is True


def test_units_limit_memory():
    shares = []
    for unit in ("cormap-worker.service", "cormap-web.service"):
        text = (ROOT / "deploy" / unit).read_text(encoding="utf-8")
        found = re.search(r"^MemoryMax=(\d+)%$", text, re.M)
        assert found, unit
        shares.append(int(found.group(1)))
    assert sum(shares) <= 75  # остаток - PostgreSQL, nginx и система


# --- админка только с самого сервера ---------------------------------------------------------

OUTSIDE = "203.0.113.5"
ADMIN_URLS = ("/admin/", "/admin", "/admin/login/", "/admin/auth/user/", "/admin/maps/mapjob/")


@pytest.fixture
def boss(db):
    return get_user_model().objects.create_superuser("boss", "boss@example.com", PASSWORD)


def test_admin_closed_for_outside_addresses(client, boss, settings):
    settings.CORMAP_ADMIN_ALLOWED_IPS = ["127.0.0.1", "::1"]
    client.force_login(boss)
    for url in ADMIN_URLS:
        response = client.get(url, REMOTE_ADDR=OUTSIDE)
        assert response.status_code == 404, url
        assert "Content-Security-Policy" in response
    assert client.post("/admin/auth/user/add/", {}, REMOTE_ADDR=OUTSIDE).status_code == 404
    # сам сайт администратору снаружи доступен, но без ссылки на админку
    page = client.get("/", REMOTE_ADDR=OUTSIDE)
    assert page.status_code == 200 and "/admin/" not in page.content.decode()


def test_admin_open_from_server_itself(client, boss, settings):
    settings.CORMAP_ADMIN_ALLOWED_IPS = ["127.0.0.1", "::1"]
    client.force_login(boss)
    assert client.get("/admin/", REMOTE_ADDR="127.0.0.1").status_code == 200
    assert client.get("/admin/auth/user/", REMOTE_ADDR="::1").status_code == 200
    assert "/admin/" in client.get("/", REMOTE_ADDR="127.0.0.1").content.decode()


def test_admin_address_is_taken_from_nginx_header(client, boss, settings):
    """За nginx адрес клиента - последнее значение X-Forwarded-For; подделка не помогает."""
    settings.CORMAP_ADMIN_ALLOWED_IPS = ["127.0.0.1"]
    settings.CORMAP_TRUST_X_FORWARDED_FOR = True
    client.force_login(boss)
    spoofed = client.get(
        "/admin/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR=f"127.0.0.1, {OUTSIDE}"
    )
    assert spoofed.status_code == 404
    local = client.get("/admin/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="127.0.0.1")
    assert local.status_code == 200


def test_admin_unrestricted_when_list_is_empty(client, boss, settings):
    settings.CORMAP_ADMIN_ALLOWED_IPS = []  # локальная работа
    client.force_login(boss)
    assert client.get("/admin/", REMOTE_ADDR=OUTSIDE).status_code == 200


def _deploy(name: str) -> str:
    return (ROOT / "deploy" / name).read_text(encoding="utf-8")


def test_nginx_serves_admin_only_on_loopback():
    servers = re.split(r"^server \{$", _deploy("nginx.conf"), flags=re.M)[1:]
    sites = [s for s in servers if "proxy_pass" in s]
    assert len(sites) == 2  # других входов на сайт нет
    public = next(s for s in sites if "listen 443 ssl;" in s)
    local = next(s for s in sites if "listen 127.0.0.1:8443 ssl;" in s)
    assert re.search(r"location \^~ /admin \{\s+return 404;\s+\}", public)
    assert "location ^~ /admin" not in local  # здесь админка открыта
    assert len(re.findall(r"^\s*listen ", local, flags=re.M)) == 1  # только 127.0.0.1
    install = _deploy("install.sh")
    assert 'env_add CORMAP_ADMIN_ALLOWED_IPS "127.0.0.1,::1"' in install
    assert not [line for line in install.splitlines() if "ufw allow" in line and "8443" in line]


# --- сокет сайта ----------------------------------------------------------------------------


def test_gunicorn_socket_is_only_for_nginx():
    socket = _deploy("cormap-web.socket")
    assert "ListenStream=/run/cormap/gunicorn.sock" in socket
    assert "SocketUser=www-data" in socket and "SocketMode=0600" in socket
    service = _deploy("cormap-web.service")
    assert "Requires=cormap-web.socket" in service
    command = service.split("ExecStart=", 1)[1].split("Restart=", 1)[0]
    assert "--bind" not in command and "--umask" not in command
    assert "RuntimeDirectory" not in service  # иначе остановка службы удалила бы сокет
    assert "server unix:/run/cormap/gunicorn.sock" in _deploy("nginx.conf")
    assert "systemctl enable --now cormap-web.socket" in _deploy("install.sh")

"""Вход, выход, обязательная авторизация и ограничение попыток входа."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import PASSWORD
from django.utils import timezone
from maps import throttle
from maps.models import AuditLog, LoginAttempt


def _urls(pk: int) -> list[tuple[str, str]]:
    """Все адреса сайта (метод, путь) для проверки доступа без входа."""
    base = f"/maps/{pk}"
    return [
        ("get", "/"),
        ("get", "/maps/new/"),
        ("post", "/maps/new/"),
        ("get", f"{base}/"),
        ("get", f"{base}/status/"),
        ("post", f"{base}/split/"),
        ("post", f"{base}/build/"),
        ("post", f"{base}/cancel/"),
        ("post", f"{base}/rebuild/"),
        ("get", f"{base}/replace/"),
        ("post", f"{base}/replace/"),
        ("get", f"{base}/delete/"),
        ("post", f"{base}/delete/"),
        ("get", f"{base}/files/pdf/"),
        ("get", f"{base}/files/png-zip/"),
        ("get", f"{base}/files/xlsx/"),
        ("get", f"{base}/preview/1/1/"),
        ("get", f"{base}/preview/1/2/"),
        ("post", "/logout/"),
        ("get", "/password/"),
        ("post", "/password/"),
        ("get", "/forecast/"),
        ("get", "/admin/"),
    ]


def test_anonymous_redirected_to_login(client, users, done_job):
    job = done_job(users["a"])
    for method, url in _urls(job.pk):
        response = getattr(client, method)(url)
        assert response.status_code == 302, (method, url)
        assert "/login/" in response["Location"], url


def test_anonymous_nonexistent_map_also_login(client, db):
    assert client.get("/maps/99999/").status_code == 302


def test_login_page_open_and_works(client, users):
    assert client.get("/login/").status_code == 200
    response = client.post("/login/", {"username": "alice", "password": PASSWORD})
    assert response.status_code == 302
    assert response["Location"] == "/"
    assert client.get("/").status_code == 200
    assert AuditLog.objects.filter(action="login", username="alice").exists()


def test_login_wrong_password_message_and_audit(client, users):
    response = client.post("/login/", {"username": "alice", "password": "неверный"})
    assert response.status_code == 200
    assert "Пожалуйста, введите правильные" in response.content.decode()
    assert AuditLog.objects.filter(action="login_failed", username="alice").exists()


def test_logout(client, users, login):
    login(users["a"])
    response = client.post("/logout/")
    assert response.status_code == 302
    assert client.get("/").status_code == 302


def test_admin_login_goes_through_site_login(client, db):
    response = client.get("/admin/login/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login/")


def test_no_self_registration(client, db):
    assert client.get("/register/").status_code in (302, 404)
    assert client.get("/accounts/signup/").status_code in (302, 404)


# --- ограничение попыток ------------------------------------------------------------------


def _fail(client, name="alice", ip="10.0.0.1", password="не тот"):
    return client.post("/login/", {"username": name, "password": password}, REMOTE_ADDR=ip)


def test_five_failures_block_even_correct_password(client, users):
    for _ in range(4):
        assert _fail(client).status_code == 200
    fifth = _fail(client)
    assert fifth.status_code == 429
    assert "попробуйте через 15 мин" in fifth.content.decode()
    # правильный пароль во время блокировки не принимается
    ok = client.post("/login/", {"username": "alice", "password": PASSWORD}, REMOTE_ADDR="10.0.0.1")
    assert ok.status_code == 429
    assert client.get("/").status_code == 302  # не вошёл
    assert AuditLog.objects.filter(details__contains="заблокирован").exists()


def test_block_is_per_login_and_ip(client, users):
    for _ in range(5):
        _fail(client)
    assert _fail(client, ip="10.0.0.2").status_code == 200  # другой IP
    assert _fail(client, name="bob").status_code == 200  # другой логин


def test_block_expires(client, users):
    for _ in range(5):
        _fail(client)
    LoginAttempt.objects.update(locked_until=timezone.now() - timedelta(seconds=1))
    ok = client.post("/login/", {"username": "alice", "password": PASSWORD}, REMOTE_ADDR="10.0.0.1")
    assert ok.status_code == 302
    assert not LoginAttempt.objects.exists()


def test_success_resets_counter(client, users):
    for _ in range(3):
        _fail(client)
    assert LoginAttempt.objects.get(ip="10.0.0.1").failures == 3
    ok = client.post("/login/", {"username": "alice", "password": PASSWORD}, REMOTE_ADDR="10.0.0.1")
    assert ok.status_code == 302
    assert not LoginAttempt.objects.exists()
    client.post("/logout/")
    for _ in range(4):  # снова четыре неудачи подряд - ещё не блокировка
        assert _fail(client).status_code == 200


def test_old_failures_leave_window(client, users):
    for _ in range(3):
        _fail(client)
    LoginAttempt.objects.update(window_start=timezone.now() - timedelta(minutes=16))
    for _ in range(4):
        assert _fail(client).status_code == 200
    assert LoginAttempt.objects.get(ip="10.0.0.1").failures == 4


@pytest.mark.parametrize("name", ["Alice", " ALICE "])
def test_login_name_case_insensitive_for_counter(client, users, name):
    for _ in range(4):
        _fail(client, name=name)
    assert _fail(client, name="alice").status_code == 429


# --- блокировка логина с любых адресов (подбор пароля со многих адресов) -----------------


def _good(client, name="alice", ip="10.9.9.9"):
    return client.post("/login/", {"username": name, "password": PASSWORD}, REMOTE_ADDR=ip)


def test_ten_failures_from_many_ips_block_login_everywhere(client, users):
    for n in range(9):
        assert _fail(client, ip=f"10.0.1.{n}").status_code == 200
    assert _fail(client, ip="10.0.1.9").status_code == 429  # десятая неудача
    # с нового адреса и с правильным паролем вход закрыт
    locked = _good(client)
    assert locked.status_code == 429
    assert "попробуйте через 15 мин" in locked.content.decode()
    assert client.get("/").status_code == 302
    assert _good(client, name="bob").status_code == 302  # другой логин не затронут


def test_login_lock_expires_and_success_resets_it(client, users):
    for n in range(10):
        _fail(client, ip=f"10.0.1.{n}")
    LoginAttempt.objects.filter(ip=throttle.ANY_IP).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )
    assert _good(client).status_code == 302
    assert not LoginAttempt.objects.filter(ip=throttle.ANY_IP).exists()


def test_login_counter_window(client, users):
    for n in range(9):
        _fail(client, ip=f"10.0.1.{n}")
    LoginAttempt.objects.filter(ip=throttle.ANY_IP).update(
        window_start=timezone.now() - timedelta(minutes=16)
    )
    assert _fail(client, ip="10.0.1.9").status_code == 200  # старые неудачи вышли из окна
    assert LoginAttempt.objects.get(ip=throttle.ANY_IP).failures == 1


def test_null_byte_in_login_is_not_stored(client, users):
    """PostgreSQL не принимает строку с нулевым байтом: в базу он попадать не должен."""
    response = client.post("/login/", {"username": "ali\x00ce", "password": "x"})
    assert response.status_code == 200
    names = [*LoginAttempt.objects.values_list("username", flat=True)]
    names += AuditLog.objects.values_list("username", flat=True)
    assert names and set(names) == {"alice"}  # сравнение в Python: LIKE в SQLite тут не годится

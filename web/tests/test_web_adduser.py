"""Команда adduser: новый обычный пользователь из командной строки (ТЗ, раздел 9)."""

from __future__ import annotations

import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from maps.management.commands import adduser


def _run(*args: str) -> str:
    out = io.StringIO()
    call_command("adduser", *args, stdout=out)
    return out.getvalue()


def test_creates_regular_user_with_generated_password(client, db):
    text = _run("sidorov")
    password = re.search(r"^Пароль: (\S+)$", text, re.M).group(1)
    assert len(password) == adduser.PASSWORD_LENGTH
    user = get_user_model().objects.get(username="sidorov")
    assert user.check_password(password)
    assert user.is_active and not user.is_staff and not user.is_superuser
    response = client.post("/login/", {"username": "sidorov", "password": password})
    assert response.status_code == 302 and response["Location"] == "/"


def test_generated_passwords_differ_and_pass_validators(db):
    from django.contrib.auth.password_validation import validate_password

    passwords = {adduser.make_password() for _ in range(50)}
    assert len(passwords) == 50
    for password in passwords:
        validate_password(password)
        assert set(password) <= set(adduser.ALPHABET)


def test_existing_login_is_refused_in_any_case(users):
    for name in ("alice", "Alice"):
        with pytest.raises(CommandError, match="уже есть"):
            _run(name)
    assert get_user_model().objects.filter(username__iexact="alice").count() == 1


@pytest.mark.parametrize("name", ["", "   ", "иван иванов", "a" * 151, "bad\x00name"])
def test_bad_login_is_refused(db, name):
    with pytest.raises(CommandError):
        _run(name)
    assert not get_user_model().objects.exists()


def test_ask_takes_typed_password_and_hides_it(db, monkeypatch):
    typed = iter(["Svoj-parol-2026", "Svoj-parol-2026"])
    monkeypatch.setattr(adduser.getpass, "getpass", lambda prompt: next(typed))
    text = _run("sidorov", "--ask")
    assert "Svoj-parol-2026" not in text and "Пароль:" not in text
    assert get_user_model().objects.get(username="sidorov").check_password("Svoj-parol-2026")


def test_ask_refuses_mismatch_and_weak_password(db, monkeypatch):
    for pair in (["Svoj-parol-2026", "Drugoj-parol-2026"], ["12345678", "12345678"]):
        typed = iter(pair)
        monkeypatch.setattr(adduser.getpass, "getpass", lambda prompt, typed=typed: next(typed))
        with pytest.raises(CommandError):
            _run("sidorov", "--ask")
    assert not get_user_model().objects.exists()

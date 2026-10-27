"""Новый обычный пользователь из командной строки сервера: cormap-manage adduser <логин>.

Замена похода в админку ради одной учётной записи (ТЗ, раздел 9). Права администратора
команда не даёт: администраторов заводит createsuperuser.
"""

from __future__ import annotations

import getpass
import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

# без похожих знаков (0 и O, 1 и l): пароль диктуют и переписывают
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LENGTH = 14


def make_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(PASSWORD_LENGTH))


class Command(BaseCommand):
    help = "Создаёт обычного пользователя; пароль создаётся автоматически и показывается один раз."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username", help="логин нового пользователя")
        parser.add_argument("--ask", action="store_true", help="ввести пароль вручную")

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        username = options["username"].strip()
        field = User._meta.get_field(User.USERNAME_FIELD)
        try:
            field.run_validators(username)
        except ValidationError as exc:
            raise CommandError("Недопустимый логин: " + " ".join(exc.messages)) from exc
        if not username or len(username) > field.max_length:
            raise CommandError(f"Логин должен быть от 1 до {field.max_length} знаков.")
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"Пользователь «{username}» уже есть.")

        user = User(username=username)
        password = self._ask_password(user) if options["ask"] else make_password()
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("Пароль не подходит: " + " ".join(exc.messages)) from exc
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS(f"Создан пользователь: {username}"))
        if not options["ask"]:
            self.stdout.write(f"Пароль: {password}")
            self.stdout.write("Пароль показан один раз. Пользователь сменит его на сайте сам.")

    def _ask_password(self, user) -> str:
        password = getpass.getpass("Пароль: ")
        if password != getpass.getpass("Пароль ещё раз: "):
            raise CommandError("Пароли не совпали.")
        return password

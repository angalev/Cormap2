"""Демонстрационные пользователи для проверки в браузере (только при DEBUG)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

PASSWORD = "Karta-2026-test"
USERS = (
    ("ivanov", False),
    ("petrov", False),
    ("admin", True),
)


class Command(BaseCommand):
    help = (
        "Создаёт пользователей ivanov, petrov (обычные) и admin (суперпользователь). Только DEBUG."
    )

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_demo работает только при CORMAP_DEBUG=1.")
        User = get_user_model()
        for name, is_super in USERS:
            if User.objects.filter(username=name).exists():
                self.stdout.write(f"Уже есть: {name}")
                continue
            if is_super:
                User.objects.create_superuser(name, f"{name}@example.com", PASSWORD)
            else:
                User.objects.create_user(name, f"{name}@example.com", PASSWORD)
            self.stdout.write(self.style.SUCCESS(f"Создан: {name} (пароль {PASSWORD})"))

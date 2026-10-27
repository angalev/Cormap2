"""Модели: карта (задание), журнал действий, счётчик попыток входа."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.http import Http404
from django.utils import timezone

from maps import storage


class MapJob(models.Model):
    """Карта пользователя: загруженная книга, параметры, результат проверки и построения."""

    class State(models.TextChoices):
        UPLOADED = "uploaded", "загружена, ждёт проверки"
        CHECKING = "checking", "проверяется"
        CHECKED = "checked", "проверена"
        CHECK_FAILED = "check_failed", "проверка не пройдена"
        QUEUED = "queued", "в очереди на построение"
        BUILDING = "building", "строится"
        DONE = "done", "готово"
        ERROR = "error", "ошибка"

    # состояния, в которых страница карты показывает шаг «Проверка и разбиение»
    CHECK_STATES = (State.UPLOADED, State.CHECKING, State.CHECKED, State.CHECK_FAILED)
    # состояния, в которых работает обработчик (после аварии возвращаются в очередь)
    WORKING_STATES = (State.CHECKING, State.BUILDING)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="map_jobs",
        verbose_name="владелец",
    )
    created_at = models.DateTimeField("создана", auto_now_add=True)
    # когда загружена нынешняя книга: меняется при «Загрузить исправленную книгу»
    uploaded_at = models.DateTimeField("книга загружена", default=timezone.now)
    updated_at = models.DateTimeField("изменена", auto_now=True)
    object_name = models.CharField("наименование объекта", max_length=255)
    params = models.JSONField("параметры формы", default=dict, blank=True)
    original_name = models.CharField("имя файла", max_length=255, blank=True)
    source_path = models.CharField("путь к книге", max_length=255, blank=True)
    processed_path = models.CharField("путь к обработанной книге", max_length=255, blank=True)
    state = models.CharField(
        "состояние", max_length=16, choices=State.choices, default=State.UPLOADED, db_index=True
    )
    error_text = models.TextField("текст ошибки", blank=True)
    check_result = models.JSONField("результат проверки", null=True, blank=True)
    split = models.JSONField("разбиение на листы", null=True, blank=True)
    progress_done = models.PositiveIntegerField("сделано листов", default=0)
    progress_total = models.PositiveIntegerField("всего листов", default=0)
    pdf_path = models.CharField("PDF", max_length=255, blank=True)
    zip_path = models.CharField("архив PNG", max_length=255, blank=True)
    xlsx_path = models.CharField("Excel", max_length=255, blank=True)
    png_dir = models.CharField("каталог PNG", max_length=255, blank=True)
    result_info = models.JSONField("сведения о построении", null=True, blank=True)
    built_at = models.DateTimeField("построена", null=True, blank=True)
    viewed_at = models.DateTimeField("открыта", null=True, blank=True)
    # сколько раз обработчик брал задание после последней постановки в очередь
    attempts = models.PositiveSmallIntegerField("попыток обработки", default=0)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "карта"
        verbose_name_plural = "карты"

    def __str__(self) -> str:
        return f"{self.object_name} (№ {self.pk})"

    @property
    def in_progress(self) -> bool:
        """Страница должна обновляться: обработчик ещё работает или ещё не взял задание."""
        return self.state in (
            self.State.UPLOADED,
            self.State.CHECKING,
            self.State.QUEUED,
            self.State.BUILDING,
        )

    @property
    def stalled(self) -> bool:
        """Задание ждёт обработчика дольше положенного, а обработчик ничем не занят.

        Так выглядит незапущенный обработчик. Если он занят другим заданием, это обычная
        очередь, и подсказка не нужна.
        """
        if self.state not in (self.State.UPLOADED, self.State.QUEUED):
            return False
        waited = (timezone.now() - self.updated_at).total_seconds()
        if waited < settings.CORMAP_STALL_SECONDS:
            return False
        return not MapJob.objects.filter(state__in=self.WORKING_STATES).exists()

    @property
    def limit_minutes(self) -> float:
        """Предел времени нынешнего шага, минут (ТЗ, раздел 9); 0 - предела нет."""
        name = (
            "CORMAP_CHECK_LIMIT_SECONDS"
            if self.state in self.CHECK_STATES
            else "CORMAP_BUILD_LIMIT_SECONDS"
        )
        return round(max(int(getattr(settings, name, 0)), 0) / 60, 1)

    @property
    def delete_after(self) -> datetime | None:
        """Когда карта будет удалена автоматически (ТЗ, раздел 9); None - не удаляется."""
        days = int(getattr(settings, "CORMAP_MAP_KEEP_DAYS", 0))
        if days <= 0 or self.created_at is None:
            return None
        return self.created_at + timedelta(days=days)

    @property
    def results_missing(self) -> bool:
        """Карта готова, а файлов результата нет.

        Так бывает, когда карту давно не открывали (файлы удалены, ТЗ, раздел 9) или
        когда база восстановлена из резервной копии: в копию результаты не входят.
        """
        if self.state != self.State.DONE:
            return False
        if not self.pdf_path:
            return True
        try:
            return not storage.resolve(self.pdf_path).is_file()
        except Http404:  # путь вне каталога данных: это не «нет файлов», а испорченная запись
            return False
        except OSError:
            return True

    @property
    def status_text(self) -> str:
        """Состояние по-русски для пользователя."""
        State = self.State
        if self.state == State.QUEUED:
            return "в очереди"
        if self.state == State.BUILDING:
            if self.progress_total:
                return f"строится — лист {max(self.progress_done, 1)} из {self.progress_total}"
            return "строится"
        if self.state == State.DONE:
            return "готово"
        if self.state == State.ERROR:
            return f"ошибка: {self.error_text}" if self.error_text else "ошибка"
        return self.get_state_display()

    @property
    def sections_count(self) -> int:
        return int((self.result_info or {}).get("sections", 0))

    @property
    def has_blocking_errors(self) -> bool:
        return self.state == self.State.CHECK_FAILED


class AuditLog(models.Model):
    """Журнал действий: кто, когда, что сделал. В админке только чтение."""

    class Action(models.TextChoices):
        LOGIN = "login", "вход"
        LOGIN_FAILED = "login_failed", "неудачный вход"
        LOGOUT = "logout", "выход"
        UPLOAD = "upload", "загрузка"
        CHECK = "check", "проверка"
        BUILD = "build", "построение"
        DOWNLOAD = "download", "скачивание файла"
        DELETE = "delete", "удаление"
        FORECAST = "forecast", "расчёт прогноза"
        PASSWORD = "password", "смена пароля"

    created_at = models.DateTimeField("когда", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        verbose_name="пользователь",
    )
    username = models.CharField("логин", max_length=150, blank=True)
    action = models.CharField("действие", max_length=16, choices=Action.choices, db_index=True)
    job = models.ForeignKey(
        MapJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        verbose_name="карта",
    )
    object_repr = models.CharField("объект", max_length=300, blank=True)
    details = models.TextField("подробности", blank=True)
    ip = models.GenericIPAddressField("IP-адрес", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "запись журнала"
        verbose_name_plural = "журнал"

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.username} {self.action}"


class LoginAttempt(models.Model):
    """Неудачные попытки входа (ограничение перебора пароля): строка на пару «логин + IP»
    и строка на логин по всем адресам (ip = «*», maps/throttle.py)."""

    username = models.CharField("логин", max_length=150)
    ip = models.CharField("IP-адрес", max_length=64, blank=True)
    failures = models.PositiveIntegerField("неудач в окне", default=0)
    window_start = models.DateTimeField("начало окна", null=True, blank=True)
    locked_until = models.DateTimeField("заблокирован до", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["username", "ip"], name="uniq_loginattempt_pair")
        ]
        verbose_name = "попытки входа"
        verbose_name_plural = "попытки входа"

    def __str__(self) -> str:
        return f"{self.username} {self.ip}: {self.failures}"


@receiver(post_delete, sender=MapJob)
def remove_job_files(sender, instance: MapJob, **kwargs) -> None:
    """Удаление записи (с сайта или из админки) удаляет и файлы карты."""
    storage.remove_file(instance.source_path)
    storage.remove_file(instance.processed_path)
    storage.remove_tree(storage.job_results_rel(instance.pk))

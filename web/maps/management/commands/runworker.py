"""Фоновый обработчик: python manage.py runworker [--once] [--interval 2].

Остановка: Ctrl+C или SIGTERM (так останавливает systemd). По SIGTERM обработчик
дорабатывает текущее задание, больше новых не берёт и выходит с кодом 0.

Каждое задание выполняется в дочернем процессе (maps/isolate.py). С ключом --once
задания выполняются в самом обработчике, пределы времени при этом не действуют.
"""

import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from maps import worker

# Пауза между проверками флага остановки во время ожидания, секунд
_SLEEP_SLICE = 0.5
# Как часто убирать карты с истёкшим сроком и давние готовые файлы, секунд
_CLEANUP_EVERY = 3600.0


class Command(BaseCommand):
    help = "Обработчик заданий: проверка книг и построение карт (отдельный процесс)."

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stop_requested = False

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="обработать всё, что есть в очереди, и выйти",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=2.0,
            help="пауза между опросами пустой очереди, секунд (по умолчанию 2)",
        )

    def request_stop(self, signum=None, frame=None) -> None:
        """Обработчик SIGTERM: только поднимает флаг, цикл завершится сам."""
        self.stop_requested = True

    def run_loop(self, interval: float) -> None:
        """Цикл опроса очереди до поднятия флага остановки."""
        next_cleanup = time.monotonic() + _CLEANUP_EVERY
        while not self.stop_requested:
            # соединение с базой могло устареть за время простоя или долгого построения
            close_old_connections()
            if time.monotonic() >= next_cleanup:
                worker.run_cleanup()
                next_cleanup = time.monotonic() + _CLEANUP_EVERY
            # задание - в дочернем процессе: предел времени, остановка при отмене (ТЗ, раздел 9)
            if worker.process_next_job(isolated=True):
                continue
            waited = 0.0
            while waited < interval and not self.stop_requested:
                step = min(_SLEEP_SLICE, interval - waited)
                time.sleep(step)
                waited += step

    def handle(self, *args, **options) -> None:
        recovered = worker.recover_stuck_jobs()
        if recovered:
            self.stdout.write(f"Возвращено в очередь зависших заданий: {recovered}")
        worker.run_cleanup()
        if options["once"]:
            count = worker.process_all()
            self.stdout.write(f"Обработано заданий: {count}")
            return
        self.stdout.write("Обработчик запущен. Остановка: Ctrl+C или SIGTERM.")
        previous = None
        try:
            previous = signal.signal(signal.SIGTERM, self.request_stop)
        except (ValueError, OSError, AttributeError):  # не главный поток или нет SIGTERM
            previous = None
        try:
            self.run_loop(options["interval"])
        except KeyboardInterrupt:
            pass
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        self.stdout.write("Обработчик остановлен.")

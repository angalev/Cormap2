"""Дочерний процесс обработчика: одно задание в отдельном процессе (ТЗ, раздел 9).

Проверку и построение нельзя прервать изнутри (расчёт идёт в чужом модуле), поэтому
обработчик выполняет задание в дочернем процессе и при отмене, удалении карты или
истечении предела времени останавливает процесс целиком.

Модуль не импортирует модели на верхнем уровне: дочерний процесс загружает его раньше,
чем настроен Django.
"""

from __future__ import annotations

import multiprocessing
import signal
from multiprocessing.process import BaseProcess


def child_main(pk: int) -> None:
    """Точка входа дочернего процесса: выполняет уже захваченное задание № pk."""
    # остановку службы и Ctrl+C разбирает родитель: он решает, ждать задание или прервать
    for name in ("SIGTERM", "SIGINT"):
        try:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        except (ValueError, OSError, AttributeError):
            pass
    import django

    django.setup()
    from maps import worker

    worker.run_claimed_pk(pk)


def start_child(pk: int) -> BaseProcess:
    """Запускает дочерний процесс для задания № pk (чистый процесс, без наследования)."""
    proc = multiprocessing.get_context("spawn").Process(
        target=child_main, args=(pk,), name=f"cormap-job-{pk}"
    )
    proc.start()
    return proc

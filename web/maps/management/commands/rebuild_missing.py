"""Постановка в очередь готовых карт без файлов: python manage.py rebuild_missing.

Нужна после восстановления из резервной копии: в копию входят база и книги, готовые
карты строятся заново. Строит обработчик (runworker), команда только ставит в очередь.
"""

from django.core.management.base import BaseCommand

from maps.models import MapJob
from maps.services import requeue_if_missing


class Command(BaseCommand):
    help = "Ставит в очередь на построение готовые карты, у которых нет файлов результата."

    def handle(self, *args, **options) -> None:
        count = 0
        for job in MapJob.objects.filter(state=MapJob.State.DONE).iterator():
            if requeue_if_missing(job):
                count += 1
        self.stdout.write(f"Поставлено в очередь карт: {count}")

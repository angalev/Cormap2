from django.apps import AppConfig


class MapsConfig(AppConfig):
    name = "maps"
    verbose_name = "Коррозионные карты"

    def ready(self) -> None:
        from maps import signals  # noqa: F401  (подключение обработчиков)

"""Шрифты листов: из списка предпочтений берутся только установленные.

Если отдать matplotlib список, первый шрифт которого не установлен (на сервере нет
Times New Roman, вместо него - метрически совместимый Liberation Serif), он пишет в
журнал предупреждение findfont на каждую надпись: сотни строк на одно построение.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=16)
def installed_families(families: tuple[str, ...]) -> tuple[str, ...]:
    """Установленные шрифты из `families` в том же порядке; если нет ни одного - serif."""
    from matplotlib import font_manager

    found = []
    for family in families:
        try:
            font_manager.findfont(
                font_manager.FontProperties(family=family), fallback_to_default=False
            )
        except ValueError:
            continue
        found.append(family)
    return tuple(found) or ("serif",)

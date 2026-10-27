"""Настройки для pytest: локальный режим включается явно, как в run_local.bat.

Файл лежит в web/tests и в поставку не входит.
"""

import os

os.environ.setdefault("CORMAP_DEBUG", "1")

from config.settings import *  # noqa: E402, F403

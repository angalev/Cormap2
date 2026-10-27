"""Настройки сайта. Всё, что меняется на сервере, берётся из переменных окружения.

Локальный режим включается явно: CORMAP_DEBUG=1 (так делает run_local.bat), база
SQLite в var/db.sqlite3. Без этой переменной действуют настройки сервера.
Переменные: CORMAP_DEBUG, CORMAP_SECRET_KEY, CORMAP_ALLOWED_HOSTS, CORMAP_DATA_DIR,
CORMAP_DATABASE_URL, CORMAP_MAX_UPLOAD_MB, CORMAP_REFINERY, CORMAP_STATIC_ROOT,
CORMAP_SSL_REDIRECT, CORMAP_TRUST_X_FORWARDED_FOR, CORMAP_HSTS_SECONDS,
CORMAP_ADMIN_ALLOWED_IPS.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config.dburl import parse_database_url

BASE_DIR = Path(__file__).resolve().parents[2]  # корень проекта


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() not in ("0", "false", "no", "off", "нет")


def _list(name: str) -> list[str]:
    return [v.strip() for v in _env(name).split(",") if v.strip()]


# Отладка включается только явным «да»: пустое, неизвестное или опечатка - выключена.
DEBUG = _env("CORMAP_DEBUG").lower() in ("1", "true", "yes", "on", "да")

SECRET_KEY = _env("CORMAP_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "Не задана переменная окружения CORMAP_SECRET_KEY (обязательна на сервере). "
            "Для локальной работы задайте CORMAP_DEBUG=1 (run_local.bat делает это сам)."
        )
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"  # только для локальной работы

ALLOWED_HOSTS = _list("CORMAP_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _list("CORMAP_CSRF_TRUSTED_ORIGINS")

# --- каталог данных: база, загрузки, результаты -------------------------------------
# Каталог НЕ раздаётся как статика или медиа: файлы отдаются только представлениями
# с проверкой прав (web/maps/views.py).
CORMAP_DATA_DIR = Path(_env("CORMAP_DATA_DIR") or BASE_DIR / "var")
try:
    CORMAP_DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:  # например, нет прав: ошибка проявится при первой записи
    pass

# --- приложения -------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "maps",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "maps.middleware.ContentSecurityPolicyMiddleware",
    "maps.middleware.AdminAccessMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # вход обязателен для каждой страницы, кроме помеченных @login_not_required
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- база данных -------------------------------------------------------------------------
_database_url = _env("CORMAP_DATABASE_URL")
if _database_url:
    try:
        DATABASES = {"default": parse_database_url(_database_url)}
    except ValueError as exc:
        raise ImproperlyConfigured(f"CORMAP_DATABASE_URL: {exc}") from exc
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": CORMAP_DATA_DIR / "db.sqlite3",
            # сайт и обработчик пишут в одну базу: ждём блокировку, журнал WAL
            "OPTIONS": {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- пароли и вход -----------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "map_list"
LOGOUT_REDIRECT_URL = "login"

# Ограничение попыток входа (web/maps/throttle.py): неудач в окне для пары «логин + IP»
# и для логина с любых адресов (ТЗ, раздел 9)
CORMAP_LOGIN_MAX_FAILURES = 5
CORMAP_LOGIN_NAME_MAX_FAILURES = 10
CORMAP_LOGIN_WINDOW_MINUTES = 15
CORMAP_LOGIN_LOCK_MINUTES = 15
# Адрес клиента брать из последнего значения X-Forwarded-For (за nginx); по умолчанию нет.
CORMAP_TRUST_X_FORWARDED_FOR = _flag("CORMAP_TRUST_X_FORWARDED_FOR", False)
# Адреса, с которых открывается админка; пусто - с любых (локальная работа). На сервере
# install.sh ставит 127.0.0.1 и ::1: вход только через SSH-туннель (ТЗ, раздел 9).
CORMAP_ADMIN_ALLOWED_IPS = _list("CORMAP_ADMIN_ALLOWED_IPS")

# --- загрузка файлов ----------------------------------------------------------------------
# Предел размера книги. Лимит тела запроса на сервере (nginx client_max_body_size)
# должен быть чуть больше: «предел + 2 МБ на поля формы».
CORMAP_MAX_UPLOAD_BYTES = int(float(_env("CORMAP_MAX_UPLOAD_MB") or 50) * 1024 * 1024)
CORMAP_UPLOAD_OVERHEAD_BYTES = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # поля формы (файлы в этот предел не входят)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # крупнее - во временный файл на диске

# --- предварительная обработка книг модулем refinery (ТЗ, 4.5) -------------------------------
# auto - шаг выполняется, если модуль установлен; required - без модуля задание завершается
# ошибкой (на сервере); off - шаг выключен.
CORMAP_REFINERY = (_env("CORMAP_REFINERY") or "auto").lower()
if CORMAP_REFINERY not in ("auto", "required", "off"):
    raise ImproperlyConfigured(
        f"CORMAP_REFINERY: неизвестное значение «{CORMAP_REFINERY}», допустимо auto, required, off."
    )

# --- обработчик заданий ---------------------------------------------------------------------
# Сколько секунд задание может ждать свободного обработчика, прежде чем страница карты
# покажет подсказку «обработчик не запущен» (ТЗ, раздел 9).
CORMAP_STALL_SECONDS = int(_env("CORMAP_STALL_SECONDS") or 30)

# --- хранение готовых карт (ТЗ, раздел 9) ------------------------------------------------
# Готовые файлы карты, которую не открывали столько суток, удаляются; книга и параметры
# остаются, при открытии карта строится заново. 0 - не удалять.
CORMAP_RESULTS_KEEP_DAYS = int(_env("CORMAP_RESULTS_KEEP_DAYS") or 90)
# Карта целиком (книга, готовые файлы, запись) удаляется через столько суток после
# создания; дата удаления показывается пользователю. 0 - не удалять.
CORMAP_MAP_KEEP_DAYS = int(_env("CORMAP_MAP_KEEP_DAYS") or 365)
# Сколько раз задание может аварийно оборвать обработчик (например, не хватило памяти),
# прежде чем оно будет снято с ошибкой вместо возврата в очередь.
CORMAP_MAX_ATTEMPTS = int(_env("CORMAP_MAX_ATTEMPTS") or 2)
# Пределы времени одного задания, секунд: дольше - дочерний процесс обработчика
# останавливается, задание снимается с ошибкой (ТЗ, раздел 9). 0 - без предела.
CORMAP_CHECK_LIMIT_SECONDS = int(_env("CORMAP_CHECK_LIMIT_SECONDS") or 1800)
CORMAP_BUILD_LIMIT_SECONDS = int(_env("CORMAP_BUILD_LIMIT_SECONDS") or 1800)

# --- язык и время ---------------------------------------------------------------------------
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

# --- статика (только собственные CSS и JS, без внешних ресурсов) -----------------------
STATIC_URL = "static/"
# collectstatic; не каталог данных. На сервере - CORMAP_STATIC_ROOT (его раздаёт nginx).
STATIC_ROOT = Path(_env("CORMAP_STATIC_ROOT") or BASE_DIR / "staticfiles")

# --- безопасность при DEBUG=0 ---------------------------------------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(_env("CORMAP_HSTS_SECONDS") or 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = _flag("CORMAP_SSL_REDIRECT", False)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Referrer-Policy и Cross-Origin-Opener-Policy ставит SecurityMiddleware (значения
    # заданы явно, чтобы не зависеть от умолчаний Django); SameSite кук - Lax по умолчанию
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
    # W021 (HSTS preload): внесение в список preload браузеров необратимо и для сервиса
    # по IP или внутреннему имени бессмысленно, поэтому preload не включаем сознательно.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 12  # смена: 12 часов
X_FRAME_OPTIONS = "DENY"
# Content-Security-Policy (web/maps/middleware.py): всё только со своего адреса; у сайта
# нет встроенных скриптов и стилей, внешних ресурсов и рамок.
CORMAP_CSP = (
    "default-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {
        "maps": {"handlers": ["console"], "level": "INFO"},
        "cormap": {"handlers": ["console"], "level": "INFO"},
        # Без этого при DEBUG=0 ошибки 500 (django.request) и события django.security
        # уходили бы только письмом на ADMINS, то есть никуда.
        "django": {"handlers": ["console"], "level": "INFO"},
    },
}

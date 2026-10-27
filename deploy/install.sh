#!/usr/bin/env bash
# Установка и обновление сервиса коррозионных карт на Ubuntu 24.04 LTS (запуск от root).
#
# Повторный запуск = обновление: код заменяется, настройки в cormap.env и данные не
# затрагиваются. Параметры (переменные окружения):
#   CORMAP_DOMAIN    доменное имя сайта; пусто = работа по IP-адресу сервера
#   CORMAP_APP_DIR   каталог кода        (по умолчанию /opt/cormap)
#   CORMAP_DATA_DIR  каталог данных      (по умолчанию /var/lib/cormap)
#   CORMAP_ETC_DIR   каталог настроек    (по умолчанию /etc/cormap)
#   CORMAP_STATIC_DIR каталог статики    (по умолчанию /var/www/cormap/static)
#   CORMAP_FIREWALL  0 = не настраивать межсетевой экран ufw (по умолчанию 1)
#   CORMAP_EXTRA_HOSTS дополнительные адреса сайта через запятую: внешний IP, если он
#                    не назначен на интерфейс сервера (облако с NAT)
# Шаблоны юнитов и nginx содержат метки @APP_DIR@, @DATA_DIR@ и т. п.; сценарий
# подставляет в них пути через sed (см. render).
set -euo pipefail

APP_DIR="${CORMAP_APP_DIR:-/opt/cormap}"
DATA_DIR="${CORMAP_DATA_DIR:-/var/lib/cormap}"
ETC_DIR="${CORMAP_ETC_DIR:-/etc/cormap}"
STATIC_DIR="${CORMAP_STATIC_DIR:-/var/www/cormap/static}"
BACKUP_DIR="/var/backups/cormap"
ACME_DIR="/var/www/certbot"
TLS_DIR="$ETC_DIR/tls"
ENV_FILE="$ETC_DIR/cormap.env"
SERVICE_USER="cormap"
DB_NAME="cormap"
DB_USER="cormap"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WARNINGS=()

log() { printf '\n== %s\n' "$*"; }
warn() { printf 'ПРЕДУПРЕЖДЕНИЕ: %s\n' "$*" >&2; WARNINGS+=("$*"); }
die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

# --- работа с env-файлом -------------------------------------------------------------
# Значения в файле - без кавычек и пробелов: так его читают и systemd, и bash.
env_get() {
    if [ -f "$ENV_FILE" ]; then sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; fi
}
env_has() { [ -f "$ENV_FILE" ] && grep -q "^$1=" "$ENV_FILE"; }
# добавляет ключ, только если его ещё нет (существующие значения не затираются)
env_add() { env_has "$1" || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }
env_set() {
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"  # так сохраняются владелец и права файла
    rm -f "$tmp"
}
# добавляет элемент в список через запятую, если его там нет
env_list_add() {
    local current
    current="$(env_get "$1")"
    case ",$current," in
        *",$2,"*) ;;
        *) env_set "$1" "${current:+$current,}$2" ;;
    esac
}

# подставляет пути в шаблон: render ШАБЛОН ФАЙЛ
render() {
    sed -e "s|@APP_DIR@|$APP_DIR|g" \
        -e "s|@DATA_DIR@|$DATA_DIR|g" \
        -e "s|@ETC_DIR@|$ETC_DIR|g" \
        -e "s|@STATIC_DIR@|$STATIC_DIR|g" \
        -e "s|@TLS_DIR@|$TLS_DIR|g" \
        -e "s|@ACME_DIR@|$ACME_DIR|g" \
        -e "s|@SERVER_NAME@|${SERVER_NAME}|g" \
        "$1" > "$2"
}

# --- 1. проверки -------------------------------------------------------------------------
log "1. Проверка системы"
[ "$(id -u)" -eq 0 ] || die "сценарий запускается от root (sudo $0)."
[ -d "$SRC_DIR/cormap" ] && [ -f "$SRC_DIR/manage.py" ] \
    || die "рядом со сценарием нет кода (ожидался $SRC_DIR/cormap и manage.py)."
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
        warn "система ${PRETTY_NAME:-неизвестная}: поставка рассчитана на Ubuntu 24.04 LTS."
    fi
fi
command -v python3 >/dev/null 2>&1 || die "не найден python3."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "нужен Python 3.12 или новее (найден $(python3 -V 2>&1))."
cd /

DOMAIN="${CORMAP_DOMAIN-}"
if [ -z "$DOMAIN" ]; then DOMAIN="$(env_get CORMAP_DOMAIN)"; fi
SERVER_IPS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)"
# Внешний адрес за NAT на интерфейсах не виден: его задают в CORMAP_EXTRA_HOSTS, иначе
# заход по нему получил бы 444 от nginx и отказ Django (ALLOWED_HOSTS).
EXTRA_HOSTS="${CORMAP_EXTRA_HOSTS-}"
if [ -z "$EXTRA_HOSTS" ]; then EXTRA_HOSTS="$(env_get CORMAP_EXTRA_HOSTS)"; fi
case "$EXTRA_HOSTS" in
    *[!A-Za-z0-9.,-]*) die "CORMAP_EXTRA_HOSTS: допустимы адреса и имена через запятую." ;;
esac
EXTRA_LIST="$(printf '%s' "$EXTRA_HOSTS" | tr ',' ' ')"
# shellcheck disable=SC2086
FIRST_IP="$(printf '%s\n' $EXTRA_LIST $SERVER_IPS | head -n 1)"
# server_name рабочего сайта в nginx: домен; без домена - IP-адреса сервера и localhost
# (иначе заход по IP попал бы на сервер по умолчанию, который отвечает 444)
if [ -n "$DOMAIN" ]; then
    SERVER_NAME="$DOMAIN"
else
    SERVER_NAME="$(printf '%s ' $EXTRA_LIST $SERVER_IPS)localhost 127.0.0.1"
fi
FIREWALL="${CORMAP_FIREWALL:-1}"

# --- 2. пакеты системы -------------------------------------------------------------------
log "2. Пакеты системы"
PACKAGES=(python3-venv python3-pip postgresql nginx fonts-liberation openssl rsync
    unattended-upgrades)
if [ "$FIREWALL" != "0" ]; then PACKAGES+=(ufw); fi
MISSING=()
for pkg in "${PACKAGES[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${MISSING[@]}"
else
    echo "Все пакеты уже установлены, шаг пропущен."
fi

# --- 3. пользователь службы --------------------------------------------------------------
log "3. Пользователь $SERVICE_USER"
if ! getent passwd "$SERVICE_USER" >/dev/null; then
    useradd --system --user-group --home-dir "$DATA_DIR" --no-create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# --- 4. код ------------------------------------------------------------------------------
log "4. Код в $APP_DIR"
mkdir -p "$APP_DIR"
if [ "$(cd "$APP_DIR" && pwd)" != "$SRC_DIR" ]; then
    # исключённые каталоги --delete не удаляет (.venv, staticfiles остаются на месте)
    # модуль заказчика кладётся на сервер вручную (ТЗ 4.5): при обновлении не удаляется
    rsync -rlpt --delete --chown=root:root --chmod=go+rX,go-w \
        --filter='P /cormap/refinery.py' \
        --exclude='/.venv' --exclude='/var' --exclude='/out' --exclude='/staticfiles' \
        --exclude='/wheels' --exclude='/build' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='*.egg-info' --exclude='.git' \
        "$SRC_DIR/" "$APP_DIR/"
else
    echo "Установка из каталога кода: копирование пропущено."
fi
chmod 755 "$APP_DIR"
find "$APP_DIR/deploy" -name '*.sh' -exec chmod 755 {} +

# --- 5. каталоги данных ------------------------------------------------------------------
log "5. Каталоги данных"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR/.matplotlib"
install -d -m 755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATIC_DIR"
install -d -m 700 -o root -g root "$BACKUP_DIR"
install -d -m 755 -o root -g root "$ACME_DIR"
install -d -m 755 -o root -g root "$ETC_DIR"
install -d -m 755 -o root -g root "$TLS_DIR"

# --- 6. PostgreSQL -----------------------------------------------------------------------
log "6. PostgreSQL"
systemctl enable --now postgresql
for _ in $(seq 1 30); do
    runuser -u postgres -- pg_isready -q && break
    sleep 1
done
runuser -u postgres -- pg_isready -q || die "PostgreSQL не запустился."

DB_PASSWORD=""
EXISTING_URL="$(env_get CORMAP_DATABASE_URL)"
if [ -n "$EXISTING_URL" ]; then
    # пароль уже выдан: берём из адреса базы (postgres://польз:ПАРОЛЬ@хост/база)
    DB_PASSWORD="$(printf '%s' "$EXISTING_URL" | sed -n 's|^[a-z]*://[^:/@]*:\([^@]*\)@.*|\1|p')"
fi
if [ -z "$DB_PASSWORD" ]; then DB_PASSWORD="$(openssl rand -hex 24)"; fi

psql_postgres() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qtA "$@"; }
if [ "$(psql_postgres -c "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")" != "1" ]; then
    psql_postgres -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD'"
else
    # роль есть: приводим пароль в соответствие с env-файлом (он только из букв и цифр)
    psql_postgres -c "ALTER ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD'"
fi
if [ "$(psql_postgres -c "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != "1" ]; then
    psql_postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8' TEMPLATE template0"
fi

# PostgreSQL не должен слушать внешние интерфейсы (сайт ходит на 127.0.0.1)
LISTEN="$(psql_postgres -c "SHOW listen_addresses" | tr -d ' ')"
if [ "$LISTEN" != "localhost" ]; then
    warn "PostgreSQL слушает не только localhost (listen_addresses=$LISTEN). Конфигурация не изменена: при работе в интернете закройте порт 5432 (listen_addresses = 'localhost' в postgresql.conf)."
fi

# --- 7. env-файл -------------------------------------------------------------------------
log "7. Настройки $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
    install -m 640 -o root -g "$SERVICE_USER" /dev/null "$ENV_FILE"
    printf '# Настройки сервиса коррозионных карт. Читается systemd и bash: без кавычек и пробелов.\n' \
        >> "$ENV_FILE"
fi
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

HOSTS="localhost,127.0.0.1"
ORIGINS=""
for ip in $SERVER_IPS $EXTRA_LIST; do
    HOSTS="$ip,$HOSTS"
    ORIGINS="${ORIGINS:+$ORIGINS,}https://$ip"
done
if [ -n "$DOMAIN" ]; then
    HOSTS="$DOMAIN,$HOSTS"
    ORIGINS="https://$DOMAIN${ORIGINS:+,$ORIGINS}"
fi

env_add CORMAP_DEBUG 0
env_add CORMAP_SECRET_KEY "$(openssl rand -hex 32)"
env_add CORMAP_ALLOWED_HOSTS "$HOSTS"
env_add CORMAP_CSRF_TRUSTED_ORIGINS "$ORIGINS"
env_add CORMAP_DATABASE_URL "postgres://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME"
env_add CORMAP_DATA_DIR "$DATA_DIR"
env_add CORMAP_STATIC_ROOT "$STATIC_DIR"
env_add CORMAP_TRUST_X_FORWARDED_FOR 1
env_add CORMAP_SSL_REDIRECT 1
# админка только с самого сервера (через SSH-туннель на порт 8443, см. nginx.conf)
env_add CORMAP_ADMIN_ALLOWED_IPS "127.0.0.1,::1"
env_add CORMAP_DOMAIN "$DOMAIN"
env_add DJANGO_SETTINGS_MODULE config.settings
env_add PYTHONPATH "$APP_DIR/web"
env_add MPLCONFIGDIR "$DATA_DIR/.matplotlib"
env_add MPLBACKEND Agg
env_add CORMAP_EXTRA_HOSTS "$EXTRA_HOSTS"
# адреса и домен, заданные при повторном запуске, добавляются к существующим спискам
if [ -n "$EXTRA_HOSTS" ]; then
    env_set CORMAP_EXTRA_HOSTS "$EXTRA_HOSTS"
    for h in $EXTRA_LIST; do
        env_list_add CORMAP_ALLOWED_HOSTS "$h"
        env_list_add CORMAP_CSRF_TRUSTED_ORIGINS "https://$h"
    done
fi
if [ -n "$DOMAIN" ]; then
    env_set CORMAP_DOMAIN "$DOMAIN"
    env_list_add CORMAP_ALLOWED_HOSTS "$DOMAIN"
    env_list_add CORMAP_CSRF_TRUSTED_ORIGINS "https://$DOMAIN"
fi

# --- 8. виртуальное окружение ------------------------------------------------------------
log "8. Виртуальное окружение и зависимости"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi
PIP=("$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --upgrade)
# точные версии всех пакетов; переменная действует и на окружение сборки пакета cormap
export PIP_CONSTRAINT="$APP_DIR/deploy/constraints.txt"
cd "$APP_DIR"
if [ -d "$SRC_DIR/wheels" ]; then
    echo "Установка из каталога $SRC_DIR/wheels (без доступа к PyPI)."
    # Сборка пакета cormap идёт в изолированном окружении; setuptools берётся из
    # того же каталога wheels/ (его кладёт туда tools/make_bundle.py --wheels).
    "${PIP[@]}" --no-index --find-links "$SRC_DIR/wheels" ".[web,server]"
else
    "${PIP[@]}" ".[web,server]"
fi
unset PIP_CONSTRAINT
rm -rf "$APP_DIR/build" "$APP_DIR"/*.egg-info
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/cormap" "$APP_DIR/web" || true
cd /

# --- 9. модуль refinery ------------------------------------------------------------------
log "9. Модуль refinery"
# модуль заказчика: файл cormap/refinery.py в поставке или отдельно установленный refinery
refinery_present() {
    "$APP_DIR/.venv/bin/python" -c "from cormap.preprocess import refinery_available as a; raise SystemExit(0 if a() else 1)" >/dev/null 2>&1
}
if refinery_present; then
    env_add CORMAP_REFINERY required
else
    env_add CORMAP_REFINERY auto
fi
if [ "$(env_get CORMAP_REFINERY)" != "required" ]; then
    if refinery_present; then
        warn "модуль refinery установлен, но в $ENV_FILE CORMAP_REFINERY=$(env_get CORMAP_REFINERY); для сервера нужно required."
    else
        warn "модуль refinery НЕ установлен: CORMAP_REFINERY=auto, предварительная обработка книг не выполняется. Положите файл модуля в cormap/refinery.py, пересоберите архив и повторите установку, затем поставьте CORMAP_REFINERY=required в $ENV_FILE и перезапустите службы."
    fi
fi

# --- 10. миграции, статика, проверка -----------------------------------------------------
log "10. Миграции, статика, проверка настроек"
render "$APP_DIR/deploy/cormap-manage" /usr/local/bin/cormap-manage
chmod 755 /usr/local/bin/cormap-manage
cormap-manage migrate --noinput
cormap-manage collectstatic --noinput
cormap-manage check --deploy --fail-level WARNING

# --- 11. службы systemd ------------------------------------------------------------------
log "11. Службы systemd"
for unit in cormap-web.socket cormap-web.service cormap-worker.service cormap-backup.service \
    cormap-backup.timer; do
    render "$APP_DIR/deploy/$unit" "/etc/systemd/system/$unit"
    chmod 644 "/etc/systemd/system/$unit"
done
systemctl daemon-reload
# Сокет сайта создаёт systemd. При обновлении с версии, где его создавал сам gunicorn,
# сайт сначала останавливается: пока служба работает, systemd сокет не займёт.
if ! systemctl is-active --quiet cormap-web.socket; then
    systemctl stop cormap-web.service 2>/dev/null || true
    rm -f /run/cormap/gunicorn.sock
fi
systemctl enable --now cormap-web.socket
systemctl enable cormap-web.service cormap-worker.service
# restart: при первой установке запускает, при обновлении перезапускает с новым кодом
systemctl restart cormap-web.service cormap-worker.service
systemctl enable --now cormap-backup.timer

# --- 12. сертификат и nginx --------------------------------------------------------------
log "12. HTTPS и nginx"
if [ ! -f "$TLS_DIR/cormap.crt" ] || [ ! -f "$TLS_DIR/cormap.key" ]; then
    CN="${DOMAIN:-${FIRST_IP:-localhost}}"
    SAN=""
    if [ -n "$DOMAIN" ]; then SAN="DNS:$DOMAIN"; fi
    for ip in $SERVER_IPS; do SAN="${SAN:+$SAN,}IP:$ip"; done
    for h in $EXTRA_LIST; do
        case "$h" in
            *[!0-9.]*) SAN="${SAN:+$SAN,}DNS:$h" ;;
            *) SAN="${SAN:+$SAN,}IP:$h" ;;
        esac
    done
    if [ -z "$SAN" ]; then SAN="DNS:localhost"; fi
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=$CN" \
        -addext "subjectAltName=$SAN" \
        -keyout "$TLS_DIR/cormap.key" -out "$TLS_DIR/cormap.crt"
    chmod 600 "$TLS_DIR/cormap.key"
    chmod 644 "$TLS_DIR/cormap.crt"
fi
# ssl.conf подключается из сайта; certbot.sh переписывает его на настоящий сертификат
if [ ! -f "$TLS_DIR/ssl.conf" ]; then
    printf 'ssl_certificate %s/cormap.crt;\nssl_certificate_key %s/cormap.key;\n' \
        "$TLS_DIR" "$TLS_DIR" > "$TLS_DIR/ssl.conf"
fi
# защита сервера в интернете: межсетевой экран и автоматические обновления безопасности
if [ "$FIREWALL" != "0" ]; then
    # правило для SSH - до включения, чтобы не отрезать доступ
    if ufw app list 2>/dev/null | grep -q '^ *OpenSSH$'; then
        ufw allow OpenSSH
    else
        ufw allow 22/tcp
        warn "профиль ufw OpenSSH не найден: разрешён порт 22/tcp; если SSH слушает другой порт, откройте его вручную."
    fi
    ufw allow 80/tcp
    ufw allow 443/tcp
    if ! ufw status | grep -q '^Status: active'; then ufw --force enable; fi
else
    warn "межсетевой экран не настроен (CORMAP_FIREWALL=0): откройте только 22, 80 и 443."
fi
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades

render "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/cormap
ln -sfn /etc/nginx/sites-available/cormap /etc/nginx/sites-enabled/cormap
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl reload-or-restart nginx

# --- 13. итог ----------------------------------------------------------------------------
log "Готово"
if [ -n "$DOMAIN" ]; then
    SITE="https://$DOMAIN/"
else
    SITE="https://${FIRST_IP:-<адрес сервера>}/"
fi
cat <<EOF
Сайт:            $SITE
Администратор:   cormap-manage createsuperuser
                 (то же, что sudo -u $SERVICE_USER ... manage.py, в окружении службы)
Админка:         из интернета закрыта. Со своего компьютера:
                 ssh -L 8443:127.0.0.1:8443 <учётная запись>@${DOMAIN:-${FIRST_IP:-<сервер>}}
                 затем https://localhost:8443/admin/ (браузер предупредит о несовпадении
                 имени в сертификате: соединение идёт внутри SSH, это ожидаемо)
Любая команда:   cormap-manage <команда Django>
Журналы:         journalctl -u cormap-web -u cormap-worker -f
                 /var/log/nginx/access.log, /var/log/nginx/error.log
Резервные копии: $BACKUP_DIR (ежесуточно в 03:30, хранятся 14 суток)
Настройки:       $ENV_FILE (после правки: systemctl restart cormap-web cormap-worker)
EOF
if [ -z "$DOMAIN" ]; then
    warn "CORMAP_DOMAIN не задан: сайт работает с самоподписанным сертификатом (браузер предупреждает о небезопасном соединении). Для работы в интернете нужно доменное имя и затем: $APP_DIR/deploy/certbot.sh <домен> <e-mail>"
else
    echo "Настоящий сертификат: $APP_DIR/deploy/certbot.sh $DOMAIN <e-mail>"
fi
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    printf '\n!!!!!!!!!!!!!!!! ПРЕДУПРЕЖДЕНИЯ !!!!!!!!!!!!!!!!\n'
    for w in "${WARNINGS[@]}"; do printf ' * %s\n' "$w"; done
    printf '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n'
fi

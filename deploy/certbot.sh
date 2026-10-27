#!/usr/bin/env bash
# Основной путь к настоящему сертификату Let's Encrypt вместо самоподписанного (запуск
# вручную от root, когда у сайта есть доменное имя, указывающее на этот сервер; порты
# 80 и 443 открыты). Продление - штатный certbot.timer, после него nginx перечитывает
# сертификат (deploy-hook).
# Использование: certbot.sh <домен> <e-mail>
# Переменные: CORMAP_ETC_DIR (по умолчанию /etc/cormap).
set -euo pipefail

DOMAIN="${1-}"
EMAIL="${2-}"
ETC_DIR="${CORMAP_ETC_DIR:-/etc/cormap}"
ENV_FILE="$ETC_DIR/cormap.env"
TLS_DIR="$ETC_DIR/tls"
SITE="/etc/nginx/sites-available/cormap"
ACME_DIR="/var/www/certbot"

[ -n "$DOMAIN" ] && [ -n "$EMAIL" ] || { echo "Использование: $0 <домен> <e-mail>" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "Запускайте от root." >&2; exit 1; }
[ -f "$SITE" ] && [ -f "$ENV_FILE" ] || { echo "Сначала выполните install.sh." >&2; exit 1; }
case "$DOMAIN" in
    *[!A-Za-z0-9.-]* | "" ) echo "Недопустимое доменное имя: $DOMAIN" >&2; exit 2 ;;
esac

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
env_set() {
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}
env_list_add() {
    local current
    current="$(env_get "$1")"
    case ",$current," in
        *",$2,"*) ;;
        *) env_set "$1" "${current:+$current,}$2" ;;
    esac
}

export DEBIAN_FRONTEND=noninteractive
if ! command -v certbot >/dev/null 2>&1; then
    apt-get update
    apt-get install -y certbot
fi
mkdir -p "$ACME_DIR"

# проверка домена идёт по http (порт 80, каталог $ACME_DIR в сайте nginx)
certbot certonly --webroot -w "$ACME_DIR" -d "$DOMAIN" -m "$EMAIL" \
    --agree-tos --no-eff-email --non-interactive \
    --deploy-hook "systemctl reload nginx"

LIVE="/etc/letsencrypt/live/$DOMAIN"
[ -f "$LIVE/fullchain.pem" ] || { echo "Сертификат не получен." >&2; exit 1; }

# nginx: подключаем сертификат и имя сайта
printf 'ssl_certificate %s/fullchain.pem;\nssl_certificate_key %s/privkey.pem;\n' \
    "$LIVE" "$LIVE" > "$TLS_DIR/ssl.conf"
# меняем только рабочие серверы (строки с пометкой), сервер по умолчанию не трогаем
sed -i "s|^\( *server_name \).*; # cormap-name|\1$DOMAIN; # cormap-name|" "$SITE"

# сайт: домен в списках разрешённых адресов (install.sh при обновлении возьмёт его же)
env_set CORMAP_DOMAIN "$DOMAIN"
env_list_add CORMAP_ALLOWED_HOSTS "$DOMAIN"
env_list_add CORMAP_CSRF_TRUSTED_ORIGINS "https://$DOMAIN"

nginx -t
systemctl reload nginx
systemctl restart cormap-web.service cormap-worker.service

echo "Готово: https://$DOMAIN/ (продление сертификата выполняет certbot.timer)."

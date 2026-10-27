#!/usr/bin/env bash
# Суточная резервная копия: дамп базы PostgreSQL и архив загруженных книг.
# Готовые карты в копию не входят: они строятся заново из книг (после восстановления:
# cormap-manage rebuild_missing).
# Запускается таймером cormap-backup.timer от root; код возврата не нулевой при ошибке.
# Копии: /var/backups/cormap/ГГГГ-ММ-ДД/, хранятся 14 суток.
set -euo pipefail

ENV_FILE="${CORMAP_ENV_FILE:-/etc/cormap/cormap.env}"
BACKUP_ROOT="${CORMAP_BACKUP_DIR:-/var/backups/cormap}"
KEEP_DAYS="${CORMAP_BACKUP_DAYS:-14}"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

DATA_DIR="${CORMAP_DATA_DIR:?в env-файле не задан CORMAP_DATA_DIR}"
URL="${CORMAP_DATABASE_URL:-}"
case "$URL" in
    postgres://* | postgresql://*) ;;
    *) echo "Резервное копирование рассчитано на PostgreSQL (CORMAP_DATABASE_URL)." >&2; exit 1 ;;
esac
DB_NAME="${URL##*/}"
DB_NAME="${DB_NAME%%\?*}"

umask 077
cd /
DAY_DIR="$BACKUP_ROOT/$(date +%F)"
mkdir -p "$DAY_DIR"
DUMP="$DAY_DIR/db.dump"
FILES="$DAY_DIR/files.tar.gz"

# недописанные файлы при ошибке не оставляем
cleanup() { rm -f "$DUMP.tmp" "$FILES.tmp"; }
trap cleanup EXIT

# база: пользователь postgres по unix-сокету, формат custom уже сжат
runuser -u postgres -- pg_dump --format=custom --compress=6 "$DB_NAME" > "$DUMP.tmp"
mv "$DUMP.tmp" "$DUMP"

# загруженные и обработанные книги (если каталог уже есть); результаты не копируются
DIRS=()
for d in uploads; do
    if [ -d "$DATA_DIR/$d" ]; then DIRS+=("$d"); fi
done
if [ "${#DIRS[@]}" -gt 0 ]; then
    rc=0
    tar -czf "$FILES.tmp" -C "$DATA_DIR" "${DIRS[@]}" || rc=$?
    # код 1 - файл изменился во время чтения (шёл процесс построения): копия пригодна
    if [ "$rc" -gt 1 ]; then
        echo "tar завершился с кодом $rc" >&2
        exit "$rc"
    fi
    mv "$FILES.tmp" "$FILES"
fi

# удаляем копии старше срока хранения
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??' \
    -mtime "+$KEEP_DAYS" -exec rm -rf {} +

echo "Резервная копия готова: $DAY_DIR"

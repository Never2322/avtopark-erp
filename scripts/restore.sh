#!/usr/bin/env bash
# Восстановление базы из файла копии.  Использование:  ./scripts/restore.sh путь/к/erp_20260101_030000_5.dump
# Файл можно скачать на странице «Копии» или достать из тома:  docker compose cp backend:/backups/ИМЯ_ФАЙЛА.dump .
# ВНИМАНИЕ: текущая база будет заменена содержимым копии.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "Укажите файл копии: ./scripts/restore.sh файл.dump"; exit 1; }
[ -f ./.env ] || { echo "Рядом с программой нет файла .env."; exit 1; }
# Нужные значения читаются из .env построчно, а не выполняются как скрипт: в файле бывают значения с пробелами (ООО «Основное»).
# Какой файл запуска использовать (docker-compose.yml или docker-compose.server.yml), docker compose берёт из .env сам (строка COMPOSE_FILE).
env_get() { grep -E "^$1=" ./.env | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' || true; }
POSTGRES_USER="$(env_get POSTGRES_USER)"
POSTGRES_DB="$(env_get POSTGRES_DB)"; POSTGRES_DB="${POSTGRES_DB:-erp}"
[ -n "$POSTGRES_USER" ] || { echo "В .env не найден POSTGRES_USER."; exit 1; }
head -c 5 "$FILE" | grep -q PGDMP || { echo "Это не файл копии (нет заголовка PGDMP)."; exit 1; }

echo "База «${POSTGRES_DB:-erp}» будет ЗАМЕНЕНА данными из $FILE."
read -r -p "Чтобы продолжить, введите ДА: " answer
[ "$answer" = "ДА" ] || { echo "Отменено."; exit 1; }

# все службы, кроме базы (на компьютере это сайт и сервер приложения, на сервере за общим Nginx: avtopark-web и сервер приложения)
APP_SERVICES="$(docker compose config --services | grep -vx db | tr '\n' ' ')"
[ -n "$APP_SERVICES" ] || { echo "Не удалось определить службы программы. Запустите скрипт из папки программы."; exit 1; }
# shellcheck disable=SC2086
docker compose stop $APP_SERVICES
docker compose up -d db
until docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; do sleep 1; done

docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB:-erp}\" WITH (FORCE)" \
  -c "CREATE DATABASE \"${POSTGRES_DB:-erp}\""
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB:-erp}" --no-owner --no-privileges --exit-on-error < "$FILE"

# shellcheck disable=SC2086
docker compose up -d $APP_SERVICES
echo "Готово. Войдите на сайт и проверьте данные. При старте сервер приведёт схему к актуальной."

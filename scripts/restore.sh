#!/usr/bin/env bash
# Восстановление базы из файла копии.  Использование:  ./scripts/restore.sh путь/к/erp_20260101_030000_5.dump
# Файл можно скачать на странице «Копии» или достать из тома:  docker compose cp backend:/backups/ИМЯ_ФАЙЛА.dump .
# ВНИМАНИЕ: текущая база будет заменена содержимым копии.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "Укажите файл копии: ./scripts/restore.sh файл.dump"; exit 1; }
set -a; . ./.env; set +a
head -c 5 "$FILE" | grep -q PGDMP || { echo "Это не файл копии (нет заголовка PGDMP)."; exit 1; }

echo "База «${POSTGRES_DB:-erp}» будет ЗАМЕНЕНА данными из $FILE."
read -r -p "Чтобы продолжить, введите ДА: " answer
[ "$answer" = "ДА" ] || { echo "Отменено."; exit 1; }

docker compose stop frontend backend
docker compose up -d db
until docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; do sleep 1; done

docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB:-erp}\" WITH (FORCE)" \
  -c "CREATE DATABASE \"${POSTGRES_DB:-erp}\""
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "${POSTGRES_DB:-erp}" --no-owner --no-privileges --exit-on-error < "$FILE"

docker compose up -d backend frontend
echo "Готово. Войдите на сайт и проверьте данные. При старте сервер приведёт схему к актуальной."

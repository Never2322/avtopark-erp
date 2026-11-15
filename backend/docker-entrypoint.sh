#!/bin/sh
# Старт сервера: схема базы приводится к актуальной (миграции), справочники и первый администратор создаются при необходимости.
set -e
echo "Обновляю схему базы..."
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

FROM python:3.12-slim-bookworm

# Клиент PostgreSQL той же версии, что и сервер (16): без него pg_dump не сделает копию
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/keyrings \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y curl gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PG_BIN_DIR=/usr/lib/postgresql/16/bin
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh .

RUN sed -i 's/\r$//' docker-entrypoint.sh && useradd --system --create-home --uid 10001 erp && mkdir /backups && chown erp:erp /backups && chmod +x docker-entrypoint.sh
USER erp
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]

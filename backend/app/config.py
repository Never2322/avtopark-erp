# -*- coding: utf-8 -*-
"""Настройки читаются из переменных окружения (в Docker их задаёт .env)."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://erp:erp@localhost:5432/erp"
    # Удобнее для Docker: пароль с любыми символами собирается в адрес сам, без ручного экранирования
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_user: str = "erp"
    postgres_password: str = ""
    postgres_db: str = "erp"
    org_timezone: str = "Asia/Novosibirsk"       # часовой пояс организации: хранится UTC, показывается в этом поясе
    cookie_secure: bool = False                  # true, когда сайт открыт по https
    trust_proxy: bool = False                    # true за nginx: адрес клиента берётся из X-Forwarded-For
    session_hours: int = 12
    session_remember_days: int = 30
    docs_enabled: bool = True                    # страница Swagger: /api/docs

    admin_login: str = ""                        # если пользователей нет, из этих двух значений создаётся первый администратор
    admin_password: str = ""
    default_legal_entity: str = "ООО «Основное»"

    lock_after_failures: int = 5
    lock_minutes: int = 10
    reading_jump_km: int = 1500                  # скачок пробега за один ввод, после которого нужна проверка
    reading_jump_hours: int = 48                 # то же для моточасов

    backup_dir: str = "/backups"
    backup_keep: int = 14                        # сколько последних копий хранить
    backup_schedule: bool = True                 # ежедневная копия
    backup_hour: int = 3                         # час (по времени организации)
    pg_bin_dir: str = ""                         # папка с pg_dump и pg_restore, если их нет в PATH

    @model_validator(mode="after")
    def build_database_url(self):
        if self.postgres_host:
            self.database_url = URL.create("postgresql+psycopg", self.postgres_user, self.postgres_password or None,
                                           self.postgres_host, self.postgres_port, self.postgres_db).render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

# -*- coding: utf-8 -*-
"""Тесты идут на настоящем PostgreSQL: схема создаётся миграцией (вместе с триггерами защиты), а не из моделей.

Адрес сервера для тестов: TEST_ADMIN_URL (по умолчанию локальный Postgres на порту 5433, пользователь postgres).
Для каждого теста таблицы очищаются и заполняются заново, поэтому тесты не зависят друг от друга.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/postgres")
TEST_DB = "erp_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
BACKEND = Path(__file__).resolve().parents[1]
BACKUPS = tempfile.mkdtemp(prefix="erp-backups-")

os.environ.update({"DATABASE_URL": TEST_URL, "BACKUP_SCHEDULE": "false", "BACKUP_DIR": BACKUPS, "BACKUP_KEEP": "3",
                   "DEFAULT_LEGAL_ENTITY": "ООО «Альфа»", "COOKIE_SECURE": "false", "ADMIN_LOGIN": "", "ADMIN_PASSWORD": ""})
sys.path.insert(0, str(BACKEND))

import pytest                                    # noqa: E402
from sqlalchemy import create_engine, text       # noqa: E402

from app.bootstrap import bootstrap              # noqa: E402
from app.config import get_settings              # noqa: E402
from app.db import reset_engine, session_factory  # noqa: E402

from tests.helpers import Client, build_world    # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    get_settings.cache_clear()
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()
    done = subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={TEST_URL}", "upgrade", "head"], cwd=BACKEND,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    reset_engine()
    yield
    reset_engine()
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def clean_database(database):
    with session_factory()() as db:
        tables = [r[0] for r in db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        db.execute(text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        db.commit()
        bootstrap(db)
    for f in Path(BACKUPS).glob("*"):
        f.unlink()
    yield


@pytest.fixture()
def world():
    return build_world()


@pytest.fixture()
def app():
    from app.main import app as application
    return application


@pytest.fixture()
def make_client(app):
    def factory(login: str | None = None, password: str | None = None) -> Client:
        client = Client(app)
        if login:
            r = client.login(login, password)
            assert r.status_code == 200, r.text
        return client
    return factory

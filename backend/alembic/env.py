# -*- coding: utf-8 -*-
from alembic import context
from sqlalchemy import create_engine

import app.models  # noqa: F401  (регистрирует таблицы)
from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run():
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()

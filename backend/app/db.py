# -*- coding: utf-8 -*-
from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


_engine = None
_factory = None


def engine():
    global _engine, _factory
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
        _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory():
    engine()
    return _factory


def reset_engine():
    """Для тестов: подключиться заново с другой базой."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()

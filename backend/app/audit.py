# -*- coding: utf-8 -*-
"""Журнал действий: кто, что, когда, откуда и над чем. Записи только добавляются (триггер в базе)."""
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def log(db: Session, who, action: str, entity_type: str = "", entity_id: int | None = None,
        legal_entity_id: int | None = None, *, login: str = "", ip: str = "", **details: Any) -> None:
    """who — Principal или None (например, неудачный вход). Подробности пишутся в JSON; пароли и токены сюда не попадают."""
    db.add(AuditLog(
        user_id=who.user.id if who else None,
        user_login=(who.user.login if who else login)[:64],
        action=action, entity_type=entity_type, entity_id=entity_id, legal_entity_id=legal_entity_id,
        ip=(who.ip if who else ip)[:64], details=_clean(details),
    ))


def _clean(details: dict) -> dict:
    banned = ("password", "token", "hash")
    return {k: (str(v) if not isinstance(v, (int, float, bool, str, list, dict, type(None))) else v)
            for k, v in details.items() if not any(b in k.lower() for b in banned)}

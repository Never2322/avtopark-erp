# -*- coding: utf-8 -*-
"""Кто пришёл и что ему можно: сессия, защита от подделки запроса (CSRF), права по юрлицам."""
import datetime as dt
import hmac
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import and_, false, select, true
from sqlalchemy.orm import Session

from app.common import fail, utcnow
from app.config import get_settings
from app.db import get_db
from app.models import Employee, Permission, RolePermission, User, UserRole, UserSession
from app.security import token_hash

COOKIE = "erp_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Scope:
    """В каких юрлицах действует право: везде или в перечисленных."""
    everything: bool = False
    ids: frozenset = frozenset()

    def allows(self, entity_id: int | None) -> bool:
        return self.everything or (entity_id is not None and entity_id in self.ids)

    def __bool__(self) -> bool:
        return self.everything or bool(self.ids)

    def __or__(self, other: "Scope") -> "Scope":
        return Scope(self.everything or other.everything, self.ids | other.ids)

    def cond(self, column):
        """Условие для запроса: строка принадлежит разрешённому юрлицу."""
        if self.everything:
            return true()
        return column.in_(self.ids) if self.ids else false()

    def narrow(self, entity_id: int | None) -> "Scope":
        """Сузить до одного юрлица, выбранного в переключателе. Чужое юрлицо даёт пустую область."""
        if entity_id is None:
            return self
        return Scope(False, frozenset({entity_id})) if self.allows(entity_id) else Scope()


NOTHING = Scope()


@dataclass
class Principal:
    user: User
    grants: dict[str, Scope]
    session: UserSession
    ip: str = ""
    employee_id: int | None = None
    _cache: dict = field(default_factory=dict)

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def login(self) -> str:
        return self.user.login

    def scope(self, perm: str) -> Scope:
        return self.grants.get(perm, NOTHING)

    def can(self, perm: str, entity_id: int | None = None) -> bool:
        s = self.scope(perm)
        return bool(s) if entity_id is None else s.allows(entity_id)

    def can_either(self, full: str, own: str, entity_id: int, is_mine: bool) -> bool:
        """Право на всё или только на своё: «полное» всегда достаточно, «своё» только для своих записей."""
        return self.can(full, entity_id) or (is_mine and self.can(own, entity_id))

    def is_global(self, perm: str) -> bool:
        return self.scope(perm).everything

    def visible_entities(self) -> Scope:
        """Юрлица, в которых у человека есть хоть какое-то право."""
        out = NOTHING
        for s in self.grants.values():
            out = out | s
        return out


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def load_grants(db: Session, user_id: int) -> dict[str, Scope]:
    rows = db.execute(
        select(Permission.code, UserRole.legal_entity_id)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, and_(UserRole.role_id == RolePermission.role_id, UserRole.user_id == user_id))
    ).all()
    tmp: dict[str, tuple[bool, set]] = {}
    for code, entity in rows:
        everything, ids = tmp.setdefault(code, (False, set()))
        if entity is None:
            tmp[code] = (True, ids)
        else:
            ids.add(entity)
    return {code: Scope(everything, frozenset(ids)) for code, (everything, ids) in tmp.items()}


def _principal(request: Request, db: Session, allow_password_change: bool) -> Principal:
    token = request.cookies.get(COOKIE)
    if not token:
        raise fail(401, "not_authenticated", "Войдите в систему")
    now = utcnow()
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if not sess or sess.revoked_at or sess.expires_at <= now:
        raise fail(401, "session_expired", "Сессия закончилась: войдите снова")
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        raise fail(401, "not_authenticated", "Войдите в систему")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf_token):
            raise fail(403, "csrf", "Запрос отклонён: обновите страницу и повторите")
    if user.must_change_password and not allow_password_change:
        raise fail(403, "password_change_required", "Сначала замените временный пароль")
    if now - sess.last_seen_at > dt.timedelta(minutes=1):
        sess.last_seen_at = now
        db.commit()
    employee_id = db.scalar(select(Employee.id).where(Employee.user_id == user.id, Employee.archived_at.is_(None)))
    return Principal(user, load_grants(db, user.id), sess, client_ip(request), employee_id)


def current(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _principal(request, db, allow_password_change=False)


def current_even_if_password_temporary(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _principal(request, db, allow_password_change=True)


def require(perm: str):
    """Зависимость: пускает, только если право есть хотя бы в одном юрлице. Какие именно юрлица — решает обработчик."""
    def dep(p: Principal = Depends(current)) -> Principal:
        if not p.can(perm):
            raise fail(403, "forbidden", "Нет доступа к этому действию")
        return p
    dep.__name__ = "require_" + perm.replace(".", "_")
    return dep


def require_global(perm: str):
    """Право действует только «везде»: пользователи, роли, копии базы, юрлица."""
    def dep(p: Principal = Depends(current)) -> Principal:
        if not p.is_global(perm):
            raise fail(403, "forbidden", "Нет доступа к этому действию")
        return p
    dep.__name__ = "require_global_" + perm.replace(".", "_")
    return dep


def entity_filter(p: Principal, perm: str, legal_entity_id: int | None) -> Scope:
    """Область данных для списка: право × выбранное в переключателе юрлицо. Чужое юрлицо не раскрывается."""
    scope = p.scope(perm)
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    return scope.narrow(legal_entity_id)

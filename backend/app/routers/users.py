# -*- coding: utf-8 -*-
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, page_of, utcnow
from app.db import get_db
from app.deps import Principal, require_global
from app.models import Employee, LegalEntity, Role, User, UserRole, UserSession
from app.security import hash_password, password_problem, temp_password

router = APIRouter(prefix="/users", tags=["Пользователи"])
LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


class RoleGrant(BaseModel):
    role_id: int
    legal_entity_id: int | None = None      # пусто: роль действует во всех юрлицах


class UserCreate(BaseModel):
    login: str = Field(min_length=1, max_length=32)
    display_name: str = Field(default="", max_length=120)
    password: str | None = Field(default=None, max_length=128)    # пусто: программа придумает временный пароль
    roles: list[RoleGrant] = []
    employee_id: int | None = None
    is_active: bool = True


class UserUpdate(BaseModel):
    display_name: str = Field(default="", max_length=120)
    is_active: bool = True
    roles: list[RoleGrant] = []
    employee_id: int | None = None


def user_out(db: Session, u: User) -> dict:
    grants = db.execute(select(UserRole.role_id, Role.code, Role.name, UserRole.legal_entity_id, LegalEntity.short_name, LegalEntity.name)
                        .join(Role, Role.id == UserRole.role_id)
                        .outerjoin(LegalEntity, LegalEntity.id == UserRole.legal_entity_id)
                        .where(UserRole.user_id == u.id).order_by(Role.id)).all()
    emp = db.scalar(select(Employee).where(Employee.user_id == u.id))
    return {
        "id": u.id, "login": u.login, "display_name": u.display_name, "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None, "last_login_ip": u.last_login_ip,
        "employee_id": emp.id if emp else None,
        "employee_name": " ".join(x for x in (emp.last_name, emp.first_name) if x) if emp else None,
        "roles": [{"role_id": rid, "code": code, "name": name, "legal_entity_id": eid,
                   "legal_entity": (short or full) if eid else "Все юрлица"} for rid, code, name, eid, short, full in grants],
    }


def global_admins(db: Session, excluding_user: int | None = None) -> int:
    q = (select(func.count(func.distinct(User.id))).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
         .where(Role.code == "admin", UserRole.legal_entity_id.is_(None), User.is_active.is_(True)))
    if excluding_user:
        q = q.where(User.id != excluding_user)
    return db.scalar(q)


def check_grants(db: Session, roles: list[RoleGrant]) -> None:
    seen = set()
    for g in roles:
        if (g.role_id, g.legal_entity_id) in seen:
            raise fail(422, "duplicate_role", "Одна и та же роль указана дважды", "roles")
        seen.add((g.role_id, g.legal_entity_id))
        if not db.get(Role, g.role_id):
            raise fail(422, "bad_role", "Роль не найдена", "roles")
        if g.legal_entity_id is not None:
            e = db.get(LegalEntity, g.legal_entity_id)
            if not e or e.archived_at:
                raise fail(422, "bad_entity", "Юридическое лицо не найдено или в архиве", "roles")


def link_employee(db: Session, user: User, employee_id: int | None) -> None:
    current = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if current and current.id != employee_id:
        current.user_id = None
        db.flush()
    if employee_id is None:
        return
    emp = db.get(Employee, employee_id)
    if not emp or emp.archived_at:
        raise fail(422, "bad_employee", "Сотрудник не найден или в архиве", "employee_id")
    if emp.user_id and emp.user_id != user.id:
        raise fail(409, "employee_taken", "Эта карточка сотрудника уже привязана к другому пользователю", "employee_id")
    emp.user_id = user.id


def set_roles(db: Session, user: User, roles: list[RoleGrant]) -> None:
    db.query(UserRole).filter(UserRole.user_id == user.id).delete()
    db.flush()
    db.add_all(UserRole(user_id=user.id, role_id=g.role_id, legal_entity_id=g.legal_entity_id) for g in roles)
    db.flush()


def revoke_sessions(db: Session, user_id: int) -> int:
    return db.execute(update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
                      .values(revoked_at=utcnow())).rowcount


@router.get("")
def list_users(q: str = "", active: bool | None = None, page: int = 1, page_size: int = 50,
               p: Principal = Depends(require_global("users.view")), db: Session = Depends(get_db)):
    query = select(User).order_by(User.is_active.desc(), func.lower(User.login))
    if q.strip():
        query = query.where(or_(User.login.ilike(like(q.strip())), User.display_name.ilike(like(q.strip()))))
    if active is not None:
        query = query.where(User.is_active.is_(active))
    rows, total = page_of(db, query, page, page_size)
    return {"items": [user_out(db, r[0]) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
def create_user(body: UserCreate, p: Principal = Depends(require_global("users.manage")), db: Session = Depends(get_db)):
    login = body.login.strip()
    if not LOGIN_RE.match(login):
        raise fail(422, "bad_login", "Логин: 3–32 символа, латиница, цифры, точка, дефис, подчёркивание", "login")
    if db.scalar(select(User.id).where(func.lower(User.login) == login.lower())):
        raise fail(409, "duplicate", "Такой логин уже есть", "login")
    if not body.roles:
        raise fail(422, "no_roles", "Назначьте хотя бы одну роль", "roles")
    check_grants(db, body.roles)
    generated = body.password is None or body.password == ""
    password = temp_password() if generated else body.password
    problem = password_problem(password, login)
    if problem:
        raise fail(422, "weak_password", problem, "password")
    user = User(login=login, display_name=body.display_name.strip(), password_hash=hash_password(password),
                is_active=body.is_active, must_change_password=True, created_by_id=p.id, updated_by_id=p.id)
    db.add(user)
    db.flush()
    set_roles(db, user, body.roles)
    link_employee(db, user, body.employee_id)
    audit.log(db, p, "user.create", "user", user.id, None, login=login, roles=[g.model_dump() for g in body.roles],
              employee_id=body.employee_id)
    db.commit()
    out = user_out(db, user)
    out["temporary_password"] = password       # показывается один раз: в базе остаётся только хэш
    return out


def get_user(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if not u:
        raise fail(404, "not_found", "Пользователь не найден")
    return u


@router.get("/{uid}")
def read_user(uid: int, p: Principal = Depends(require_global("users.view")), db: Session = Depends(get_db)):
    return user_out(db, get_user(db, uid))


@router.patch("/{uid}")
def update_user(uid: int, body: UserUpdate, p: Principal = Depends(require_global("users.manage")), db: Session = Depends(get_db)):
    u = get_user(db, uid)
    if not body.roles:
        raise fail(422, "no_roles", "Назначьте хотя бы одну роль", "roles")
    check_grants(db, body.roles)
    if uid == p.id and not body.is_active:
        raise fail(409, "self_disable", "Нельзя отключить самого себя", "is_active")
    admin_id = db.scalar(select(Role.id).where(Role.code == "admin"))
    stays_admin = body.is_active and any(g.role_id == admin_id and g.legal_entity_id is None for g in body.roles)
    if not stays_admin and global_admins(db, excluding_user=uid) == 0:
        raise fail(409, "last_admin", "Это последний администратор: сначала назначьте другого", "roles")
    before = user_out(db, u)
    u.display_name, u.updated_by_id = body.display_name.strip(), p.id
    deactivated = u.is_active and not body.is_active
    u.is_active = body.is_active
    set_roles(db, u, body.roles)
    link_employee(db, u, body.employee_id)
    ended = revoke_sessions(db, u.id) if deactivated else 0
    after = user_out(db, u)
    audit.log(db, p, "user.update", "user", u.id, None, login=u.login,
              roles_before=[(r["code"], r["legal_entity_id"]) for r in before["roles"]],
              roles_after=[(r["code"], r["legal_entity_id"]) for r in after["roles"]],
              active_before=before["is_active"], active_after=after["is_active"], sessions_ended=ended)
    db.commit()
    return after


@router.post("/{uid}/reset-password")
def reset_password(uid: int, p: Principal = Depends(require_global("users.manage")), db: Session = Depends(get_db)):
    u = get_user(db, uid)
    if uid == p.id:
        raise fail(409, "self_reset", "Свой пароль меняйте в личном профиле")
    password = temp_password()
    u.password_hash, u.must_change_password, u.updated_by_id = hash_password(password), True, p.id
    ended = revoke_sessions(db, u.id)
    audit.log(db, p, "user.password_reset", "user", u.id, None, login=u.login, sessions_ended=ended)
    db.commit()
    return {"temporary_password": password}


@router.post("/{uid}/logout-all")
def logout_all(uid: int, p: Principal = Depends(require_global("users.manage")), db: Session = Depends(get_db)):
    u = get_user(db, uid)
    ended = revoke_sessions(db, u.id)
    audit.log(db, p, "user.logout_all", "user", u.id, None, login=u.login, sessions_ended=ended)
    db.commit()
    return {"ended": ended}

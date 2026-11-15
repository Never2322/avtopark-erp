# -*- coding: utf-8 -*-
"""Первичное наполнение: права, роли, типы техники, юрлицо по умолчанию, первый администратор."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.models import (LegalEntity, Permission, Role, RolePermission, User, UserRole, VehicleType)
from app.permissions import PERMISSIONS, ROLE_DEFAULTS, ROLES
from app.security import hash_password, password_problem

# (код, название, категория, основное показание)
VEHICLE_TYPES = [
    ("truck_dump", "Самосвал", "truck", "km"),
    ("truck_tractor", "Седельный тягач", "truck", "km"),
    ("truck_flatbed", "Бортовой грузовик", "truck", "km"),
    ("truck_waste", "Мусоровоз", "truck", "km"),
    ("loader", "Фронтальный погрузчик", "special", "engine_hours"),
    ("excavator", "Экскаватор", "special", "engine_hours"),
    ("bulldozer", "Бульдозер", "special", "engine_hours"),
    ("grader", "Грейдер", "special", "engine_hours"),
    ("snow_machine", "Снегоуборочная машина", "special", "engine_hours"),
    ("sweeper", "Подметально-уборочная машина", "special", "engine_hours"),
    ("car", "Легковой автомобиль", "car", "km"),
    ("trailer", "Прицеп, полуприцеп", "trailer", "km"),
    ("other", "Прочее", "other", "km"),
]


def ensure_reference_data(db: Session) -> None:
    perms = {p.code: p for p in db.scalars(select(Permission))}
    fresh: set[str] = set()                      # права, которых в базе ещё не было (появились в новой версии)
    for code, module, description in PERMISSIONS:
        if code in perms:
            perms[code].module, perms[code].description = module, description
        else:
            perms[code] = Permission(code=code, module=module, description=description)
            db.add(perms[code])
            fresh.add(code)
    db.flush()
    roles = {r.code: r for r in db.scalars(select(Role))}
    for code, name, description in ROLES:
        if code not in roles:
            roles[code] = Role(code=code, name=name, description=description, is_system=True)
            db.add(roles[code])
            db.flush()
            # набор по умолчанию выдаётся только новой роли: позже его правят в интерфейсе, и перезаписывать нельзя
            db.add_all(RolePermission(role_id=roles[code].id, permission_id=perms[c].id) for c in ROLE_DEFAULTS[code])
        elif fresh:
            # Роль уже есть, а право новое (обновление программы): выдаём его по умолчанию один раз, при появлении.
            # Потом администратор может снять его в «Ролях и правах», и обратно оно не вернётся.
            have_ids = set(db.scalars(select(RolePermission.permission_id).where(RolePermission.role_id == roles[code].id)))
            db.add_all(RolePermission(role_id=roles[code].id, permission_id=perms[c].id)
                       for c in ROLE_DEFAULTS[code] if c in fresh and perms[c].id not in have_ids)
    # Администратор всегда может всё, в том числе новое право, появившееся в следующей версии.
    have = set(db.scalars(select(RolePermission.permission_id).where(RolePermission.role_id == roles["admin"].id)))
    db.add_all(RolePermission(role_id=roles["admin"].id, permission_id=p.id) for p in perms.values() if p.id not in have)
    known = set(db.scalars(select(VehicleType.code)))
    db.add_all(VehicleType(code=c, name=n, category=cat, primary_reading=r) for c, n, cat, r in VEHICLE_TYPES if c not in known)
    db.flush()


def ensure_default_entity(db: Session) -> LegalEntity:
    entity = db.scalar(select(LegalEntity).where(LegalEntity.archived_at.is_(None)).order_by(LegalEntity.id).limit(1))
    if not entity:
        name = get_settings().default_legal_entity
        entity = LegalEntity(name=name, short_name=name)
        db.add(entity)
        db.flush()
    return entity


def create_admin(db: Session, login: str, password: str, display_name: str = "", must_change: bool = False) -> User:
    problem = password_problem(password, login)
    if problem:
        raise ValueError(problem)
    user = User(login=login, display_name=display_name or login, password_hash=hash_password(password),
                must_change_password=must_change)
    db.add(user)
    db.flush()
    admin = db.scalar(select(Role).where(Role.code == "admin"))
    db.add(UserRole(user_id=user.id, role_id=admin.id, legal_entity_id=None))
    db.flush()
    audit.log(db, None, "user.create", "user", user.id, None, login=login, role="admin", via="bootstrap")
    return user


def bootstrap(db: Session) -> dict:
    """Идемпотентно: можно запускать при каждом старте контейнера."""
    ensure_reference_data(db)
    ensure_default_entity(db)
    created = False
    s = get_settings()
    if s.admin_login and s.admin_password and not db.scalar(select(func.count()).select_from(User)):
        create_admin(db, s.admin_login, s.admin_password)
        created = True
    db.commit()
    return {"admin_created": created}

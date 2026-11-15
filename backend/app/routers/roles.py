# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, require_global
from app.models import Permission, Role, RolePermission, UserRole
from app.permissions import PERMISSION_CODES

router = APIRouter(tags=["Роли и права"])


def role_out(db: Session, r: Role) -> dict:
    codes = list(db.scalars(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                            .where(RolePermission.role_id == r.id).order_by(Permission.code)))
    users = db.scalar(select(func.count(func.distinct(UserRole.user_id))).where(UserRole.role_id == r.id))
    return {"id": r.id, "code": r.code, "name": r.name, "description": r.description, "is_system": r.is_system,
            "permissions": codes, "users": users}


@router.get("/permissions")
def permissions(p: Principal = Depends(require_global("users.view")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Permission).order_by(Permission.id)).all()
    return {"items": [{"code": x.code, "module": x.module, "description": x.description} for x in rows]}


@router.get("/roles")
def list_roles(p: Principal = Depends(require_global("users.view")), db: Session = Depends(get_db)):
    return {"items": [role_out(db, r) for r in db.scalars(select(Role).order_by(Role.id))]}


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=300)
    permissions: list[str] = []


class PermissionsIn(BaseModel):
    permissions: list[str]


def set_permissions(db: Session, role: Role, codes: list[str]) -> None:
    unknown = sorted(set(codes) - PERMISSION_CODES)
    if unknown:
        raise fail(422, "unknown_permission", "Неизвестное право: " + ", ".join(unknown), "permissions")
    ids = {c: i for c, i in db.execute(select(Permission.code, Permission.id))}
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    db.add_all(RolePermission(role_id=role.id, permission_id=ids[c]) for c in sorted(set(codes)))


@router.post("/roles", status_code=201)
def create_role(body: RoleIn, p: Principal = Depends(require_global("roles.manage")), db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Role.id).where(func.lower(Role.name) == name.lower())):
        raise fail(409, "duplicate", "Роль с таким названием уже есть", "name")
    n = db.scalar(select(func.count()).select_from(Role)) + 1
    role = Role(code=f"custom_{n}", name=name, description=body.description.strip(), is_system=False)
    db.add(role)
    db.flush()
    set_permissions(db, role, body.permissions)
    audit.log(db, p, "role.create", "role", role.id, None, name=name, permissions=sorted(set(body.permissions)))
    db.commit()
    return role_out(db, role)


@router.put("/roles/{role_id}/permissions")
def update_permissions(role_id: int, body: PermissionsIn, p: Principal = Depends(require_global("roles.manage")),
                       db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if not role:
        raise fail(404, "not_found", "Роль не найдена")
    if role.code == "admin":
        raise fail(409, "admin_fixed", "Права администратора не меняются: у него всегда есть всё")
    before = role_out(db, role)["permissions"]
    set_permissions(db, role, body.permissions)
    after = sorted(set(body.permissions))
    audit.log(db, p, "role.permissions", "role", role.id, None, role=role.name,
              added=sorted(set(after) - set(before)), removed=sorted(set(before) - set(after)))
    db.commit()
    return role_out(db, role)

# -*- coding: utf-8 -*-
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, require, require_global
from app.models import Department, EmploymentAssignment, LegalEntity, Vehicle

router = APIRouter(tags=["Юридические лица"])


def inn_ok(inn: str) -> bool:
    """Проверка ИНН по контрольным цифрам: ловит опечатки, которые иначе всплывают в документах."""
    if not inn.isdigit() or len(inn) not in (10, 12):
        return False
    d = [int(c) for c in inn]

    def check(coefs, pos):
        return sum(c * x for c, x in zip(coefs, d)) % 11 % 10 == d[pos]
    if len(inn) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], 9)
    return check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 10) and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], 11)


class EntityIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    short_name: str = Field(default="", max_length=80)
    inn: str = Field(default="", max_length=12)
    kpp: str = Field(default="", max_length=9)
    ogrn: str = Field(default="", max_length=15)
    address: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=120)
    bank_name: str = Field(default="", max_length=120)
    bik: str = Field(default="", max_length=9)
    account: str = Field(default="", max_length=20)
    corr_account: str = Field(default="", max_length=20)
    head_name: str = Field(default="", max_length=120)
    accountant_name: str = Field(default="", max_length=120)

    @field_validator("name", "short_name", "address", "phone", "email", "bank_name", "head_name", "accountant_name", mode="before")
    @classmethod
    def strip(cls, v):
        return v.strip() if isinstance(v, str) else v


def check_entity(body: EntityIn) -> None:
    if body.inn and not inn_ok(body.inn):
        raise fail(422, "bad_inn", "ИНН не прошёл проверку: 10 цифр для организации или 12 для предпринимателя", "inn")
    if body.kpp and not (body.kpp.isdigit() and len(body.kpp) == 9):
        raise fail(422, "bad_kpp", "КПП состоит из 9 цифр", "kpp")
    if body.ogrn and not (body.ogrn.isdigit() and len(body.ogrn) in (13, 15)):
        raise fail(422, "bad_ogrn", "ОГРН состоит из 13 цифр (ОГРНИП из 15)", "ogrn")
    for name, size, label in (("bik", 9, "БИК"), ("account", 20, "Расчётный счёт"), ("corr_account", 20, "Корр. счёт")):
        v = getattr(body, name)
        if v and not (v.isdigit() and len(v) == size):
            raise fail(422, "bad_" + name, f"{label} состоит из {size} цифр", name)


def out(e: LegalEntity, vehicles: int = 0, employees: int = 0) -> dict:
    return {"id": e.id, "name": e.name, "short_name": e.short_name, "inn": e.inn, "kpp": e.kpp, "ogrn": e.ogrn,
            "address": e.address, "phone": e.phone, "email": e.email, "bank_name": e.bank_name, "bik": e.bik,
            "account": e.account, "corr_account": e.corr_account, "head_name": e.head_name, "accountant_name": e.accountant_name, "archived": e.archived_at is not None,
            "vehicles": vehicles, "employees": employees}


@router.get("/legal-entities")
def list_entities(archived: bool = False, p: Principal = Depends(require("legal_entities.view")), db: Session = Depends(get_db)):
    scope = p.scope("legal_entities.view")
    q = select(LegalEntity).where(scope.cond(LegalEntity.id)).order_by(LegalEntity.name)
    q = q.where(LegalEntity.archived_at.is_not(None) if archived else LegalEntity.archived_at.is_(None))
    items = []
    for e in db.scalars(q):
        v = db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.legal_entity_id == e.id, Vehicle.archived_at.is_(None)))
        n = db.scalar(select(func.count(func.distinct(EmploymentAssignment.employee_id))).where(EmploymentAssignment.legal_entity_id == e.id))
        items.append(out(e, v, n))
    return {"items": items}


@router.post("/legal-entities", status_code=201)
def create_entity(body: EntityIn, p: Principal = Depends(require_global("legal_entities.manage")), db: Session = Depends(get_db)):
    check_entity(body)
    if db.scalar(select(LegalEntity.id).where(func.lower(LegalEntity.name) == body.name.lower(), LegalEntity.archived_at.is_(None))):
        raise fail(409, "duplicate", "Юридическое лицо с таким названием уже есть", "name")
    e = LegalEntity(**body.model_dump(), created_by_id=p.id, updated_by_id=p.id)
    db.add(e)
    db.flush()
    audit.log(db, p, "legal_entity.create", "legal_entity", e.id, e.id, name=e.name)
    db.commit()
    return out(e)


def get_entity(db: Session, entity_id: int) -> LegalEntity:
    e = db.get(LegalEntity, entity_id)
    if not e:
        raise fail(404, "not_found", "Юридическое лицо не найдено")
    return e


@router.patch("/legal-entities/{entity_id}")
def update_entity(entity_id: int, body: EntityIn, p: Principal = Depends(require_global("legal_entities.manage")),
                  db: Session = Depends(get_db)):
    e = get_entity(db, entity_id)
    check_entity(body)
    dup = db.scalar(select(LegalEntity.id).where(func.lower(LegalEntity.name) == body.name.lower(),
                                                 LegalEntity.archived_at.is_(None), LegalEntity.id != e.id))
    if dup:
        raise fail(409, "duplicate", "Юридическое лицо с таким названием уже есть", "name")
    changed = {k: [getattr(e, k), v] for k, v in body.model_dump().items() if getattr(e, k) != v}
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    e.updated_by_id = p.id
    audit.log(db, p, "legal_entity.update", "legal_entity", e.id, e.id, changed=changed)
    db.commit()
    return out(e)


@router.post("/legal-entities/{entity_id}/archive")
def archive_entity(entity_id: int, p: Principal = Depends(require_global("legal_entities.manage")), db: Session = Depends(get_db)):
    e = get_entity(db, entity_id)
    if db.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.legal_entity_id == e.id, Vehicle.archived_at.is_(None))):
        raise fail(409, "has_vehicles", "В юрлице есть действующая техника: сначала отправьте её в архив")
    from app.common import utcnow
    e.archived_at, e.updated_by_id = utcnow(), p.id
    audit.log(db, p, "legal_entity.archive", "legal_entity", e.id, e.id)
    db.commit()
    return out(e)


@router.post("/legal-entities/{entity_id}/restore")
def restore_entity(entity_id: int, p: Principal = Depends(require_global("legal_entities.manage")), db: Session = Depends(get_db)):
    e = get_entity(db, entity_id)
    if db.scalar(select(LegalEntity.id).where(func.lower(LegalEntity.name) == e.name.lower(),
                                              LegalEntity.archived_at.is_(None), LegalEntity.id != e.id)):
        raise fail(409, "duplicate", "Есть другое действующее юрлицо с таким названием")
    e.archived_at, e.updated_by_id = None, p.id
    audit.log(db, p, "legal_entity.restore", "legal_entity", e.id, e.id)
    db.commit()
    return out(e)


class DepartmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


@router.get("/legal-entities/{entity_id}/departments")
def list_departments(entity_id: int, p: Principal = Depends(require("legal_entities.view")), db: Session = Depends(get_db)):
    if not p.can("legal_entities.view", entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    rows = db.scalars(select(Department).where(Department.legal_entity_id == entity_id, Department.archived_at.is_(None)).order_by(Department.name))
    return {"items": [{"id": d.id, "name": d.name} for d in rows]}


@router.post("/legal-entities/{entity_id}/departments", status_code=201)
def create_department(entity_id: int, body: DepartmentIn, p: Principal = Depends(require_global("legal_entities.manage")),
                      db: Session = Depends(get_db)):
    get_entity(db, entity_id)
    name = body.name.strip()
    if db.scalar(select(Department.id).where(Department.legal_entity_id == entity_id, func.lower(Department.name) == name.lower(),
                                             Department.archived_at.is_(None))):
        raise fail(409, "duplicate", "Такое подразделение уже есть", "name")
    d = Department(legal_entity_id=entity_id, name=name, created_by_id=p.id, updated_by_id=p.id)
    db.add(d)
    db.flush()
    audit.log(db, p, "department.create", "department", d.id, entity_id, name=name)
    db.commit()
    return {"id": d.id, "name": d.name}

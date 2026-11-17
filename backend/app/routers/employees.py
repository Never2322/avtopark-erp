# -*- coding: utf-8 -*-
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, org_today, page_of, utcnow
from app.db import get_db
from app.deps import Principal, Scope, current, entity_filter, require
from app.models import (Department, Employee, EmploymentAssignment, LegalEntity, Tariff, User, Vehicle, VehicleAssignment, Waybill, WorkLog)
from app.services import payroll as pay_svc, timesheet as ts_svc
from app.services.vehicles import active_assignment_filter

router = APIRouter(prefix="/employees", tags=["Сотрудники"])


class AssignmentIn(BaseModel):
    legal_entity_id: int
    position: str = Field(default="", max_length=120)
    started_on: dt.date | None = None
    ended_on: dt.date | None = None


class EmployeeIn(BaseModel):
    last_name: str = Field(min_length=1, max_length=80)
    first_name: str = Field(default="", max_length=80)
    middle_name: str = Field(default="", max_length=80)
    position: str = Field(default="", max_length=120)
    department_id: int | None = None
    is_driver: bool = False
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=120)
    birth_date: dt.date | None = None
    hired_on: dt.date | None = None
    fired_on: dt.date | None = None
    note: str = Field(default="", max_length=2000)


class EmployeeCreate(EmployeeIn):
    assignments: list[AssignmentIn] = []


SENSITIVE = ("phone", "email", "birth_date")


def visible_filter(scope: Scope):
    """Сотрудник виден, если он работает в доступном юрлице. Без места работы виден только тем, у кого доступ ко всем юрлицам."""
    if scope.everything:
        return None
    return exists().where(EmploymentAssignment.employee_id == Employee.id, EmploymentAssignment.legal_entity_id.in_(scope.ids))


def employee_entities(db: Session, e: Employee) -> set[int]:
    return {a.legal_entity_id for a in e.assignments}


def can_see(p: Principal, db: Session, e: Employee, perm: str = "employees.view") -> bool:
    scope = p.scope(perm)
    return scope.everything or bool(employee_entities(db, e) & scope.ids)


def can_sensitive(p: Principal, db: Session, e: Employee) -> bool:
    if e.user_id is not None and e.user_id == p.id:
        return True                              # свои данные человек видит всегда
    return can_see(p, db, e, "employees.view_sensitive")


def full_name(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


def out(db: Session, p: Principal, e: Employee, detailed: bool = False) -> dict:
    ids = employee_entities(db, e)
    ents = {x.id: (x.short_name or x.name) for x in db.scalars(select(LegalEntity).where(LegalEntity.id.in_(ids)))} if ids else {}
    data = {
        "id": e.id, "full_name": full_name(e), "last_name": e.last_name, "first_name": e.first_name, "middle_name": e.middle_name,
        "position": e.position, "is_driver": e.is_driver, "department_id": e.department_id,
        "hired_on": e.hired_on.isoformat() if e.hired_on else None, "fired_on": e.fired_on.isoformat() if e.fired_on else None,
        "archived": e.archived_at is not None, "user_id": e.user_id,
        "legal_entities": [{"id": i, "name": n} for i, n in ents.items()],
    }
    sens = can_sensitive(p, db, e)
    data["sensitive_hidden"] = not sens
    for f in SENSITIVE:
        v = getattr(e, f)
        data[f] = (v.isoformat() if isinstance(v, dt.date) else v) if sens else None
    if detailed:
        data["note"] = e.note
        if p.can("worklog.view") or p.can("employees.edit"):
            def _tname(tid):
                t = db.get(Tariff, tid) if tid else None
                return t.name if t else None
            data["pay"] = {"work_tariff_id": e.work_tariff_id, "repair_tariff_id": e.repair_tariff_id,
                           "work_tariff": _tname(e.work_tariff_id), "repair_tariff": _tname(e.repair_tariff_id),
                           "can_edit": p.can("employees.edit")}
            sc = p.scope("worklog.view")
            if sc:                                                          # сколько человек уже заработал в этом месяце (по внесённым часам)
                first = org_today().replace(day=1)
                data["pay"]["earned"] = {"month": first.strftime("%Y-%m"), "title": f"{ts_svc.MONTHS[first.month - 1]} {first.year}",
                                         **pay_svc.month_totals(db, e.id, first, first.replace(day=ts_svc.month_days(first)), sc.cond(WorkLog.legal_entity_id))}
        data["assignments"] = [{"id": a.id, "legal_entity_id": a.legal_entity_id, "legal_entity": ents.get(a.legal_entity_id, ""),
                                "position": a.position, "started_on": a.started_on.isoformat() if a.started_on else None,
                                "ended_on": a.ended_on.isoformat() if a.ended_on else None} for a in sorted(e.assignments, key=lambda a: a.id)]
        if p.can("users.view") and e.user_id:
            u = db.get(User, e.user_id)
            data["user_login"] = u.login if u else None
        vs = db.execute(select(Vehicle.id, Vehicle.plate, Vehicle.make, Vehicle.model).join(VehicleAssignment, VehicleAssignment.vehicle_id == Vehicle.id)
                        .where(VehicleAssignment.employee_id == e.id, active_assignment_filter())).all()
        data["vehicles"] = [{"id": i, "plate": pl, "name": f"{mk} {md}".strip()} for i, pl, mk, md in vs]
    return data


def get_visible(db: Session, p: Principal, eid: int, perm: str = "employees.view") -> Employee:
    e = db.get(Employee, eid)
    if not e or not can_see(p, db, e, perm):
        raise fail(404, "not_found", "Сотрудник не найден")        # чужого не раскрываем: нет доступа выглядит как «не найден»
    return e


@router.get("/me")
def my_card(p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Свой профиль: доступен любому вошедшему, к которому привязана карточка сотрудника."""
    e = db.get(Employee, p.employee_id) if p.employee_id else None
    if not e:
        raise fail(404, "no_card", "К вашему входу не привязана карточка сотрудника")
    return out(db, p, e, detailed=True)


@router.get("/lookup")
def lookup(legal_entity_id: int | None = None, drivers: bool = False, q: str = "",
           p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Короткий список для выпадающих полей (диспетчеру при назначении водителя, при создании путевого листа)."""
    scope = Scope()
    for perm in ("employees.view", "assignments.manage", "waybills.create", "waybills.close", "waybills.view"):
        scope = scope | p.scope(perm)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    if legal_entity_id is not None:
        scope = scope.narrow(legal_entity_id)
        if not scope:
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    query = select(Employee).where(Employee.archived_at.is_(None))
    cond = visible_filter(scope)
    if cond is not None:
        query = query.where(cond)
    if legal_entity_id is not None:
        query = query.where(exists().where(EmploymentAssignment.employee_id == Employee.id,
                                           EmploymentAssignment.legal_entity_id == legal_entity_id))
    if drivers:
        query = query.where(Employee.is_driver.is_(True))
    if q.strip():
        query = query.where(or_(Employee.last_name.ilike(like(q.strip())), Employee.first_name.ilike(like(q.strip()))))
    rows = db.scalars(query.order_by(Employee.last_name, Employee.first_name).limit(200)).all()
    return {"items": [{"id": e.id, "full_name": full_name(e), "is_driver": e.is_driver} for e in rows]}


@router.get("")
def list_employees(q: str = "", legal_entity_id: int | None = None, is_driver: bool | None = None, archived: bool = False,
                   page: int = 1, page_size: int = 50, p: Principal = Depends(require("employees.view")),
                   db: Session = Depends(get_db)):
    scope = entity_filter(p, "employees.view", legal_entity_id)
    query = select(Employee).order_by(Employee.last_name, Employee.first_name, Employee.id)
    cond = visible_filter(scope)
    if cond is not None:
        query = query.where(cond)
    if legal_entity_id is not None:
        query = query.where(exists().where(EmploymentAssignment.employee_id == Employee.id,
                                           EmploymentAssignment.legal_entity_id == legal_entity_id))
    query = query.where(Employee.archived_at.is_not(None) if archived else Employee.archived_at.is_(None))
    if is_driver is not None:
        query = query.where(Employee.is_driver.is_(is_driver))
    if q.strip():
        t = like(q.strip())
        query = query.where(or_(Employee.last_name.ilike(t), Employee.first_name.ilike(t), Employee.middle_name.ilike(t), Employee.position.ilike(t)))
    rows, total = page_of(db, query, page, page_size)
    return {"items": [out(db, p, r[0]) for r in rows], "total": total, "page": page, "page_size": page_size}


def check_assignments(p: Principal, db: Session, assignments: list[AssignmentIn], perm: str) -> None:
    for a in assignments:
        e = db.get(LegalEntity, a.legal_entity_id)
        if not e or e.archived_at:
            raise fail(422, "bad_entity", "Юридическое лицо не найдено или в архиве", "assignments")
        if not p.can(perm, a.legal_entity_id):
            raise fail(403, "forbidden", f"Нет права работать с сотрудниками юрлица «{e.short_name or e.name}»")
        if a.started_on and a.ended_on and a.ended_on < a.started_on:
            raise fail(422, "bad_dates", "Дата окончания раньше даты начала", "assignments")


def check_department(db: Session, department_id: int | None, entities: set[int]) -> None:
    if department_id is None:
        return
    d = db.get(Department, department_id)
    if not d or d.legal_entity_id not in entities:
        raise fail(422, "bad_department", "Подразделение должно относиться к юрлицу сотрудника", "department_id")


@router.post("", status_code=201)
def create_employee(body: EmployeeCreate, p: Principal = Depends(require("employees.create")), db: Session = Depends(get_db)):
    if not body.assignments:
        raise fail(422, "no_assignment", "Укажите, в каком юридическом лице работает сотрудник", "assignments")
    check_assignments(p, db, body.assignments, "employees.create")
    check_department(db, body.department_id, {a.legal_entity_id for a in body.assignments})
    data = body.model_dump(exclude={"assignments"})
    if not p.can("employees.view_sensitive"):
        for f in SENSITIVE:
            data.pop(f, None)
    for f in ("last_name", "first_name", "middle_name", "position"):
        data[f] = data[f].strip()
    e = Employee(**data, created_by_id=p.id, updated_by_id=p.id)
    e.assignments = [EmploymentAssignment(**a.model_dump()) for a in body.assignments]
    db.add(e)
    db.flush()
    audit.log(db, p, "employee.create", "employee", e.id, body.assignments[0].legal_entity_id, name=full_name(e))
    db.commit()
    return out(db, p, e, detailed=True)


@router.get("/{eid}")
def read_employee(eid: int, p: Principal = Depends(require("employees.view")), db: Session = Depends(get_db)):
    return out(db, p, get_visible(db, p, eid), detailed=True)


@router.patch("/{eid}")
def update_employee(eid: int, body: EmployeeIn, p: Principal = Depends(require("employees.edit")), db: Session = Depends(get_db)):
    e = get_visible(db, p, eid, "employees.edit")
    check_department(db, body.department_id, employee_entities(db, e))
    changes = {}
    sens = can_sensitive(p, db, e) and can_see(p, db, e, "employees.view_sensitive")
    for k, v in body.model_dump().items():
        if k in SENSITIVE and not sens:
            continue                              # чего не видишь, того и не меняешь
        if isinstance(v, str):
            v = v.strip()
        if getattr(e, k) != v:
            changes[k] = [str(getattr(e, k)) if k in SENSITIVE else getattr(e, k), "…" if k in SENSITIVE else v]
            setattr(e, k, v)
    e.updated_by_id = p.id
    ent = next(iter(sorted(employee_entities(db, e))), None)
    audit.log(db, p, "employee.update", "employee", e.id, ent, name=full_name(e), changed=list(changes))
    db.commit()
    return out(db, p, e, detailed=True)


@router.post("/{eid}/assignments", status_code=201)
def add_assignment(eid: int, body: AssignmentIn, p: Principal = Depends(require("employees.edit")), db: Session = Depends(get_db)):
    e = get_visible(db, p, eid, "employees.edit")
    check_assignments(p, db, [body], "employees.edit")
    e.assignments.append(EmploymentAssignment(**body.model_dump()))
    db.flush()
    audit.log(db, p, "employee.assignment_add", "employee", e.id, body.legal_entity_id, name=full_name(e))
    db.commit()
    return out(db, p, e, detailed=True)


class EndIn(BaseModel):
    ended_on: dt.date


@router.post("/{eid}/assignments/{aid}/end")
def end_assignment(eid: int, aid: int, body: EndIn, p: Principal = Depends(require("employees.edit")), db: Session = Depends(get_db)):
    e = get_visible(db, p, eid, "employees.edit")
    a = next((x for x in e.assignments if x.id == aid), None)
    if not a:
        raise fail(404, "not_found", "Место работы не найдено")
    if not p.can("employees.edit", a.legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    if a.started_on and body.ended_on < a.started_on:
        raise fail(422, "bad_dates", "Дата окончания раньше даты начала", "ended_on")
    a.ended_on = body.ended_on
    audit.log(db, p, "employee.assignment_end", "employee", e.id, a.legal_entity_id, name=full_name(e), ended_on=body.ended_on.isoformat())
    db.commit()
    return out(db, p, e, detailed=True)


@router.post("/{eid}/archive")
def archive_employee(eid: int, p: Principal = Depends(require("employees.archive")), db: Session = Depends(get_db)):
    e = get_visible(db, p, eid, "employees.archive")
    if db.scalar(select(func.count()).select_from(Waybill).where(Waybill.driver_id == e.id, Waybill.status.in_(("draft", "open")))):
        raise fail(409, "open_waybills", "У сотрудника есть незакрытые путевые листы: закройте или аннулируйте их")
    if db.scalar(select(func.count()).select_from(VehicleAssignment).where(VehicleAssignment.employee_id == e.id, active_assignment_filter())):
        raise fail(409, "has_vehicles", "За сотрудником закреплена техника: сначала снимите закрепление")
    e.archived_at, e.updated_by_id = utcnow(), p.id
    ent = next(iter(sorted(employee_entities(db, e))), None)
    audit.log(db, p, "employee.archive", "employee", e.id, ent, name=full_name(e))
    db.commit()
    return out(db, p, e, detailed=True)


@router.post("/{eid}/restore")
def restore_employee(eid: int, p: Principal = Depends(require("employees.archive")), db: Session = Depends(get_db)):
    e = get_visible(db, p, eid, "employees.archive")
    e.archived_at, e.updated_by_id = None, p.id
    ent = next(iter(sorted(employee_entities(db, e))), None)
    audit.log(db, p, "employee.restore", "employee", e.id, ent, name=full_name(e))
    db.commit()
    return out(db, p, e, detailed=True)

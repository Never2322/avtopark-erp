# -*- coding: utf-8 -*-
import datetime as dt
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, exists, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail, like, norm_plate, org_today, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, entity_filter, require
from app.enums import READING_KINDS, REQUEST_OPEN, VEHICLE_CATEGORIES, VEHICLE_DOC_KINDS, VEHICLE_STATUSES, status_label
from app.models import (Department, DossierDoc, Employee, LegalEntity, MaintenanceItem, ServiceRequest, User, Vehicle, VehicleAssignment, VehicleStatusHistory,
                        VehicleType, Waybill)
from app.services import access, readings as readings_svc
from app.services.vehicles import active_assignment_filter, change_status, employee_in_entity

router = APIRouter(prefix="/vehicles", tags=["Транспорт"])
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")      # в VIN не бывает букв I, O, Q


class VehicleIn(BaseModel):
    type_id: int
    plate: str = Field(min_length=1, max_length=20)
    internal_no: str = Field(default="", max_length=30)
    make: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=60)
    vin: str = Field(default="", max_length=17)
    year: int | None = Field(default=None, ge=1950, le=2100)
    department_id: int | None = None
    note: str = Field(default="", max_length=2000)


class VehicleCreate(VehicleIn):
    legal_entity_id: int
    initial_km: Decimal | None = Field(default=None, ge=0, le=9_999_999)
    initial_hours: Decimal | None = Field(default=None, ge=0, le=999_999)


class StatusIn(BaseModel):
    status: str
    reason: str = Field(default="", max_length=300)


class AssignmentIn(BaseModel):
    employee_id: int
    role: str = Field(default="primary", pattern="^(primary|backup)$")
    started_on: dt.date | None = None


class EndIn(BaseModel):
    ended_on: dt.date | None = None


def clean_vehicle(db: Session, body: VehicleIn, entity_id: int, vehicle_id: int | None = None) -> dict:
    data = body.model_dump(include=set(VehicleIn.model_fields))      # только поля самой машины, без юрлица и начальных показаний
    data["plate"] = norm_plate(data["plate"])
    if not data["plate"]:
        raise fail(422, "plate_required", "Укажите госномер", "plate")
    vin = (data["vin"] or "").strip().upper()
    if vin and not VIN_RE.match(vin):
        raise fail(422, "bad_vin", "VIN состоит из 17 символов: цифры и латинские буквы, кроме I, O, Q", "vin")
    data["vin"] = vin or None
    if not db.get(VehicleType, body.type_id):
        raise fail(422, "bad_type", "Выберите тип техники", "type_id")
    if body.department_id is not None:
        d = db.get(Department, body.department_id)
        if not d or d.legal_entity_id != entity_id:
            raise fail(422, "bad_department", "Подразделение должно относиться к тому же юрлицу", "department_id")
    q = select(Vehicle.id).where(Vehicle.plate == data["plate"], Vehicle.archived_at.is_(None))
    if vehicle_id:
        q = q.where(Vehicle.id != vehicle_id)
    if db.scalar(q):
        raise fail(409, "duplicate_plate", "Машина с таким госномером уже есть", "plate")
    if data["vin"]:
        q = select(Vehicle.id).where(Vehicle.vin == data["vin"], Vehicle.archived_at.is_(None))
        if vehicle_id:
            q = q.where(Vehicle.id != vehicle_id)
        if db.scalar(q):
            raise fail(409, "duplicate_vin", "Машина с таким VIN уже есть", "vin")
    for f in ("internal_no", "make", "model", "note"):
        data[f] = data[f].strip()
    return data


def drivers_of(db: Session, vehicle_ids: list[int]) -> dict[int, dict]:
    if not vehicle_ids:
        return {}
    rows = db.execute(select(VehicleAssignment.vehicle_id, Employee.id, Employee.last_name, Employee.first_name)
                      .join(Employee, Employee.id == VehicleAssignment.employee_id)
                      .where(VehicleAssignment.vehicle_id.in_(vehicle_ids), VehicleAssignment.role == "primary",
                             active_assignment_filter())).all()
    return {v: {"id": i, "name": " ".join(x for x in (ln, fn) if x)} for v, i, ln, fn in rows}


def out(db: Session, p: Principal, v: Vehicle, drivers: dict | None = None, detailed: bool = False) -> dict:
    d = (drivers if drivers is not None else drivers_of(db, [v.id])).get(v.id)
    data = {
        "id": v.id, "legal_entity_id": v.legal_entity_id,
        "legal_entity": v.legal_entity.short_name or v.legal_entity.name,
        "type": {"id": v.type.id, "name": v.type.name, "category": v.type.category, "primary_reading": v.type.primary_reading},
        "plate": v.plate, "internal_no": v.internal_no, "make": v.make, "model": v.model, "vin": v.vin, "year": v.year,
        "department_id": v.department_id, "status": v.status, "status_label": VEHICLE_STATUSES[v.status],
        "status_changed_at": v.status_changed_at.isoformat() if v.status_changed_at else None,
        "current_km": float(v.current_km) if v.current_km is not None else None,
        "current_hours": float(v.current_hours) if v.current_hours is not None else None,
        "driver": d, "archived": v.archived_at is not None,
    }
    if detailed:
        e = v.legal_entity_id
        data["note"] = v.note
        data["actions"] = {
            "edit": p.can("vehicles.edit", e) and not v.archived_at,
            "archive": p.can("vehicles.archive", e),
            "change_status": p.can("vehicle_status.change", e) and not v.archived_at,
            "assign": p.can("assignments.manage", e) and not v.archived_at,
            "add_reading": access.reading_allowed(p, db, v) and not v.archived_at,
            "correct_reading": p.can("readings.correct", e),
            "review_reading": p.can("readings.review", e),
            "create_request": p.can("requests.create", e) and not v.archived_at,
        }
    return data


def get_vehicle(db: Session, p: Principal, vid: int) -> Vehicle:
    v = db.get(Vehicle, vid)
    if not v or not access.can_view(p, db, v):
        raise fail(404, "not_found", "Машина не найдена")      # чужая машина выглядит так же, как несуществующая
    return v


SORTS = {"plate": Vehicle.plate, "make": Vehicle.make, "status": Vehicle.status, "current_km": Vehicle.current_km,
         "current_hours": Vehicle.current_hours, "created_at": Vehicle.created_at, "internal_no": Vehicle.internal_no}


def filtered(db: Session, p: Principal, *, q, status, type_id, category, legal_entity_id, department_id, driver_id, archived):
    cond = access.view_condition(p, db)
    if legal_entity_id is not None:
        if not (p.scope("vehicles.view") | p.scope("vehicles.view_own")).allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        cond = and_(cond, Vehicle.legal_entity_id == legal_entity_id)
    query = select(Vehicle).where(cond)
    query = query.where(Vehicle.archived_at.is_not(None) if archived else Vehicle.archived_at.is_(None))
    if q.strip():
        t = like(q.strip())
        query = query.where(or_(Vehicle.plate.ilike(t.replace(" ", "")), Vehicle.internal_no.ilike(t), Vehicle.make.ilike(t),
                                Vehicle.model.ilike(t), Vehicle.vin.ilike(t)))
    if status:
        if status not in VEHICLE_STATUSES:
            raise fail(422, "bad_status", "Неизвестный статус", "status")
        query = query.where(Vehicle.status == status)
    if type_id:
        query = query.where(Vehicle.type_id == type_id)
    if category:
        query = query.where(Vehicle.type_id.in_(select(VehicleType.id).where(VehicleType.category == category)))
    if department_id:
        query = query.where(Vehicle.department_id == department_id)
    if driver_id:
        query = query.where(exists().where(VehicleAssignment.vehicle_id == Vehicle.id, VehicleAssignment.employee_id == driver_id,
                                           active_assignment_filter()))
    return query


def next_service(db: Session, p: Principal, ids: list[int]) -> dict[int, str]:
    """Ближайший срок планового ТО по дате для каждой машины списка. Только тем, у кого есть право видеть ТО."""
    scope = p.scope("maintenance.view")
    if not scope or not ids:
        return {}
    rows = db.execute(select(MaintenanceItem.vehicle_id, func.min(MaintenanceItem.due_date))
                      .where(MaintenanceItem.vehicle_id.in_(ids), MaintenanceItem.archived_at.is_(None), MaintenanceItem.controlled.is_(True),
                             MaintenanceItem.done_at.is_(None), MaintenanceItem.due_date.is_not(None), scope.cond(MaintenanceItem.legal_entity_id))
                      .group_by(MaintenanceItem.vehicle_id)).all()
    return {vid: day.isoformat() for vid, day in rows}


INSURANCE_KINDS = ("osago", "kasko")


def insurance_until(db: Session, p: Principal, ids: list[int]) -> dict[int, list[dict]]:
    """Страховки машин списка: по каждому виду (ОСАГО, КАСКО) берётся полис с самой поздней датой окончания,
    чтобы заменённый старый полис не показывался просроченным. Первым идёт тот, что кончается раньше.
    Только тем, у кого есть право видеть личные дела техники."""
    scope = p.scope("vehicle_files.view")
    if not scope or not ids:
        return {}
    rows = db.execute(select(DossierDoc.vehicle_id, DossierDoc.kind, func.max(DossierDoc.valid_until))
                      .join(Vehicle, Vehicle.id == DossierDoc.vehicle_id)
                      .where(DossierDoc.vehicle_id.in_(ids), DossierDoc.kind.in_(INSURANCE_KINDS), DossierDoc.archived_at.is_(None),
                             DossierDoc.valid_until.is_not(None), scope.cond(Vehicle.legal_entity_id))
                      .group_by(DossierDoc.vehicle_id, DossierDoc.kind)).all()
    out: dict[int, list[dict]] = {}
    for vid, kind, day in rows:
        out.setdefault(vid, []).append({"kind": kind, "label": VEHICLE_DOC_KINDS[kind], "until": day.isoformat()})
    for items in out.values():
        items.sort(key=lambda x: (x["until"], x["kind"]))
    return out


def facets(db: Session, p: Principal, flt: dict) -> dict:
    """Сколько машин в каждой категории и в каждом статусе при текущем поиске. Категории не зависят от выбранной категории, статусы от выбранного статуса."""
    def count_by(col, **override):
        ids = filtered(db, p, **{**flt, **override}).with_only_columns(Vehicle.id)
        q = select(col, func.count()).select_from(Vehicle).join(VehicleType, VehicleType.id == Vehicle.type_id).where(Vehicle.id.in_(ids)).group_by(col)
        return {k: n for k, n in db.execute(q).all()}
    return {"categories": count_by(VehicleType.category, category=""), "statuses": count_by(Vehicle.status, status="")}


FILTER_PARAMS = dict(q="", status="", type_id=None, category="", legal_entity_id=None, department_id=None, driver_id=None, archived=False)


@router.get("/types")
def types(p: Principal = Depends(current), db: Session = Depends(get_db)):
    rows = db.scalars(select(VehicleType).order_by(VehicleType.category, VehicleType.name))
    return {"items": [{"id": t.id, "code": t.code, "name": t.name, "category": t.category,
                       "category_label": VEHICLE_CATEGORIES[t.category], "primary_reading": t.primary_reading} for t in rows],
            "statuses": [{"code": k, "label": v} for k, v in VEHICLE_STATUSES.items()],
            "categories": [{"code": k, "label": v} for k, v in VEHICLE_CATEGORIES.items()]}


@router.get("/lookup")
def lookup(q: str = "", legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    cond = access.lookup_condition(p, db)
    query = select(Vehicle).where(cond, Vehicle.archived_at.is_(None))
    if legal_entity_id:
        query = query.where(Vehicle.legal_entity_id == legal_entity_id)
    if q.strip():
        t = like(q.strip())
        query = query.where(or_(Vehicle.plate.ilike(t.replace(" ", "")), Vehicle.make.ilike(t), Vehicle.model.ilike(t), Vehicle.internal_no.ilike(t)))
    rows = db.scalars(query.order_by(Vehicle.plate).limit(200)).all()
    return {"items": [{"id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(), "legal_entity_id": v.legal_entity_id,
                       "internal_no": v.internal_no, "primary_reading": v.type.primary_reading,
                       "current_km": float(v.current_km) if v.current_km is not None else None,
                       "current_hours": float(v.current_hours) if v.current_hours is not None else None} for v in rows]}


@router.get("")
def list_vehicles(q: str = "", status: str = "", type_id: int | None = None, category: str = "",
                  legal_entity_id: int | None = None, department_id: int | None = None, driver_id: int | None = None,
                  archived: bool = False, sort: str = "plate", desc_: bool = Query(False, alias="desc"),
                  page: int = 1, page_size: int = 50, p: Principal = Depends(current), db: Session = Depends(get_db)):
    query = filtered(db, p, q=q, status=status, type_id=type_id, category=category, legal_entity_id=legal_entity_id,
                     department_id=department_id, driver_id=driver_id, archived=archived)
    col = SORTS.get(sort, Vehicle.plate)
    query = query.order_by((desc(col) if desc_ else asc(col)).nulls_last(), Vehicle.id)
    rows, total = page_of(db, query, page, page_size)
    vehicles = [r[0] for r in rows]
    drivers = drivers_of(db, [v.id for v in vehicles])
    flt = dict(q=q, status=status, type_id=type_id, category=category, legal_entity_id=legal_entity_id,
               department_id=department_id, driver_id=driver_id, archived=archived)
    nxt = next_service(db, p, [v.id for v in vehicles])
    ins = insurance_until(db, p, [v.id for v in vehicles])
    return {"items": [{**out(db, p, v, drivers), "next_service": nxt.get(v.id), "insurance": ins.get(v.id)} for v in vehicles], "total": total, "page": page, "page_size": page_size,
            "facets": facets(db, p, flt)}


@router.get("/export.csv")
def export_csv(q: str = "", status: str = "", type_id: int | None = None, category: str = "", legal_entity_id: int | None = None,
               department_id: int | None = None, driver_id: int | None = None, archived: bool = False,
               p: Principal = Depends(require("vehicles.export")), db: Session = Depends(get_db)):
    # выгрузка идёт по тем же правилам, что и список: чужие юрлица и техника без права экспорта в файл не попадают
    export_scope = p.scope("vehicles.export")
    query = filtered(db, p, q=q, status=status, type_id=type_id, category=category, legal_entity_id=legal_entity_id,
                     department_id=department_id, driver_id=driver_id, archived=archived)
    query = query.where(export_scope.cond(Vehicle.legal_entity_id)).order_by(Vehicle.plate)
    vehicles = db.scalars(query).all()
    drivers = drivers_of(db, [v.id for v in vehicles])
    rows = [["Госномер", "Внутренний №", "Юрлицо", "Тип", "Марка", "Модель", "VIN", "Год", "Статус", "Пробег, км", "Моточасы, ч", "Водитель"]]
    for v in vehicles:
        rows.append([v.plate, v.internal_no, v.legal_entity.short_name or v.legal_entity.name, v.type.name, v.make, v.model,
                     v.vin or "", v.year or "", VEHICLE_STATUSES[v.status],
                     f"{v.current_km:g}" if v.current_km is not None else "", f"{v.current_hours:g}" if v.current_hours is not None else "",
                     (drivers.get(v.id) or {}).get("name", "")])
    audit.log(db, p, "vehicle.export", "vehicle", None, legal_entity_id, rows=len(rows) - 1)
    db.commit()
    return Response(csv_text(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="vehicles.csv"'})


@router.post("", status_code=201)
def create_vehicle(body: VehicleCreate, p: Principal = Depends(require("vehicles.create")), db: Session = Depends(get_db)):
    e = db.get(LegalEntity, body.legal_entity_id)
    if not e or e.archived_at:
        raise fail(422, "bad_entity", "Выберите юридическое лицо", "legal_entity_id")
    if not p.can("vehicles.create", e.id):
        raise fail(403, "forbidden", "Нет права добавлять технику в это юридическое лицо")
    data = clean_vehicle(db, body, e.id)
    v = Vehicle(legal_entity_id=e.id, **data, status="in_work", status_changed_at=utcnow(), created_by_id=p.id, updated_by_id=p.id)
    db.add(v)
    db.flush()
    db.add(VehicleStatusHistory(vehicle_id=v.id, from_status=None, status="in_work", reason="Машина добавлена", changed_by_id=p.id))
    audit.log(db, p, "vehicle.create", "vehicle", v.id, e.id, plate=v.plate)
    for kind, val in (("km", body.initial_km), ("engine_hours", body.initial_hours)):
        if val is not None:
            readings_svc.add_reading(db, p, v, kind, val, note="Начальное показание")
    db.commit()
    db.refresh(v)
    return out(db, p, v, detailed=True)


@router.get("/{vid}")
def read_vehicle(vid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return out(db, p, get_vehicle(db, p, vid), detailed=True)


@router.patch("/{vid}")
def update_vehicle(vid: int, body: VehicleIn, p: Principal = Depends(require("vehicles.edit")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("vehicles.edit", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права менять технику этого юрлица")
    if v.archived_at:
        raise fail(409, "vehicle_archived", "Машина в архиве: сначала верните её из архива")
    data = clean_vehicle(db, body, v.legal_entity_id, v.id)
    changed = {k: [getattr(v, k), val] for k, val in data.items() if getattr(v, k) != val}
    for k, val in data.items():
        setattr(v, k, val)
    v.updated_by_id = p.id
    audit.log(db, p, "vehicle.update", "vehicle", v.id, v.legal_entity_id, plate=v.plate, changed=changed)
    db.commit()
    db.refresh(v)
    return out(db, p, v, detailed=True)


@router.post("/{vid}/archive")
def archive_vehicle(vid: int, p: Principal = Depends(require("vehicles.archive")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("vehicles.archive", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права архивировать технику этого юрлица")
    if db.scalar(select(func.count()).select_from(Waybill).where(Waybill.vehicle_id == v.id, Waybill.status.in_(("draft", "open")))):
        raise fail(409, "open_waybills", "По машине есть незакрытые путевые листы")
    if db.scalar(select(func.count()).select_from(ServiceRequest).where(ServiceRequest.vehicle_id == v.id, ServiceRequest.status.in_(REQUEST_OPEN))):
        raise fail(409, "open_requests", "По машине есть незавершённые обращения")
    v.archived_at, v.updated_by_id = utcnow(), p.id
    audit.log(db, p, "vehicle.archive", "vehicle", v.id, v.legal_entity_id, plate=v.plate)
    db.commit()
    return out(db, p, v, detailed=True)


@router.post("/{vid}/restore")
def restore_vehicle(vid: int, p: Principal = Depends(require("vehicles.archive")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("vehicles.archive", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права восстанавливать технику этого юрлица")
    if db.scalar(select(Vehicle.id).where(Vehicle.plate == v.plate, Vehicle.archived_at.is_(None), Vehicle.id != v.id)):
        raise fail(409, "duplicate_plate", "Есть другая действующая машина с таким госномером")
    v.archived_at, v.updated_by_id = None, p.id
    audit.log(db, p, "vehicle.restore", "vehicle", v.id, v.legal_entity_id, plate=v.plate)
    db.commit()
    return out(db, p, v, detailed=True)


@router.post("/{vid}/status")
def set_status(vid: int, body: StatusIn, p: Principal = Depends(require("vehicle_status.change")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("vehicle_status.change", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права менять статус техники этого юрлица")
    changed = change_status(db, p, v, body.status, body.reason)
    v.updated_by_id = p.id
    db.commit()
    data = out(db, p, v, detailed=True)
    data["changed"] = changed
    return data


@router.get("/{vid}/status-history")
def status_history(vid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    rows = db.execute(select(VehicleStatusHistory, User.login, User.display_name, ServiceRequest.number)
                      .outerjoin(User, User.id == VehicleStatusHistory.changed_by_id)
                      .outerjoin(ServiceRequest, ServiceRequest.id == VehicleStatusHistory.request_id)
                      .where(VehicleStatusHistory.vehicle_id == v.id).order_by(VehicleStatusHistory.id.desc())).all()
    return {"items": [{"id": h.id, "from_status": h.from_status, "status": h.status, "status_label": status_label(h.status),
                       "from_label": status_label(h.from_status) if h.from_status else None, "reason": h.reason,
                       "changed_at": h.changed_at.isoformat(), "by": name or login, "request_number": num,
                       "request_id": h.request_id} for h, login, name, num in rows]}


# ---- закрепление водителей

def assignment_out(a: VehicleAssignment) -> dict:
    return {"id": a.id, "employee_id": a.employee_id, "employee": " ".join(x for x in (a.employee.last_name, a.employee.first_name) if x),
            "role": a.role, "started_on": a.started_on.isoformat(), "ended_on": a.ended_on.isoformat() if a.ended_on else None,
            "active": a.started_on <= org_today() and (a.ended_on is None or a.ended_on >= org_today())}


@router.get("/{vid}/assignments")
def list_assignments(vid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    rows = db.scalars(select(VehicleAssignment).where(VehicleAssignment.vehicle_id == v.id).order_by(VehicleAssignment.started_on.desc(), VehicleAssignment.id.desc()))
    return {"items": [assignment_out(a) for a in rows]}


@router.post("/{vid}/assignments", status_code=201)
def add_assignment(vid: int, body: AssignmentIn, p: Principal = Depends(require("assignments.manage")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("assignments.manage", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права закреплять водителей за техникой этого юрлица")
    if v.archived_at:
        raise fail(409, "vehicle_archived", "Машина в архиве")
    emp = db.get(Employee, body.employee_id)
    if not emp or emp.archived_at:
        raise fail(422, "bad_employee", "Сотрудник не найден или в архиве", "employee_id")
    if not emp.is_driver:
        raise fail(422, "not_driver", "У сотрудника не отмечено «водитель»", "employee_id")
    if not employee_in_entity(db, emp, v.legal_entity_id):
        raise fail(422, "wrong_entity", "Сотрудник не работает в юрлице этой машины", "employee_id")
    start = body.started_on or org_today()
    active = db.scalars(select(VehicleAssignment).where(VehicleAssignment.vehicle_id == v.id, active_assignment_filter())).all()
    if any(a.employee_id == emp.id for a in active):
        raise fail(409, "already_assigned", "Этот водитель уже закреплён за машиной", "employee_id")
    if body.role == "primary" and any(a.role == "primary" for a in active):
        raise fail(409, "primary_exists", "Сначала снимите закрепление с основного водителя", "employee_id")
    a = VehicleAssignment(vehicle_id=v.id, employee_id=emp.id, role=body.role, started_on=start, created_by_id=p.id)
    db.add(a)
    db.flush()
    audit.log(db, p, "vehicle.assign", "vehicle", v.id, v.legal_entity_id, plate=v.plate, employee_id=emp.id, role=body.role)
    db.commit()
    db.refresh(a)
    return assignment_out(a)


@router.post("/{vid}/assignments/{aid}/end")
def end_assignment(vid: int, aid: int, body: EndIn, p: Principal = Depends(require("assignments.manage")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not p.can("assignments.manage", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права менять закрепление водителей")
    a = db.get(VehicleAssignment, aid)
    if not a or a.vehicle_id != v.id:
        raise fail(404, "not_found", "Закрепление не найдено")
    today = org_today()
    if a.ended_on is not None and a.ended_on < today:
        raise fail(409, "already_ended", "Закрепление уже снято")
    # «Последний день» включается: закрепление с ended_on = сегодня действует до конца дня.
    # Поэтому «Снять» без даты ставит последним днём вчера, и водитель перестаёт быть закреплённым сразу.
    removed = False
    if body.ended_on is None:
        end = today - dt.timedelta(days=1)
        removed = end < a.started_on          # закрепили сегодня и сняли в тот же день: ни одного полного дня не было
    else:
        end = body.ended_on
        if end < a.started_on:
            raise fail(422, "bad_dates", "Дата окончания раньше даты начала", "ended_on")
    out = assignment_out(a)
    if removed:
        db.delete(a)
        out.update(active=False)
    else:
        a.ended_on = end
        out = assignment_out(a)
    audit.log(db, p, "vehicle.unassign", "vehicle", v.id, v.legal_entity_id, plate=v.plate, employee_id=a.employee_id,
              removed=removed)
    db.commit()
    return out

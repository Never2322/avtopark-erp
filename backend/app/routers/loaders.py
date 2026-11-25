# -*- coding: utf-8 -*-
"""Грузчики мусоровозов.

Кабинет грузчика («/loaders/home»): своя машина на сегодня, водитель и напарники, маршрут на сегодня и ближайшие дни,
сколько машина вывезла сегодня и за месяц (без денег), свои замечания по маршруту.
Бригады («/loaders/crews»): директор и диспетчер видят, кто за каким мусоровозом, закрепляют и снимают грузчиков,
разбирают замечания. Закрепление грузчика отдельное от водительского: водитель машины в талонах, зарплате и маршрутах не меняется.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, org_today, utcnow
from app.db import get_db
from app.deps import Principal, current
from app.enums import ROUTE_NOTE_KINDS, VEHICLE_STATUSES
from app.models import Employee, EmploymentAssignment, LoaderAssignment, RouteNote, RoutePlan, Vehicle, WeighTicket
from app.registry import _vehicle_label, allowed_entities
from app.resources import job_today
from app.services import notify, payroll as pay, routes as routes_svc, timesheet as ts
from app.services.vehicles import current_driver_id, employee_in_entity

router = APIRouter(prefix="/loaders", tags=["Грузчики мусоровозов"])

DAYS_AHEAD = 3                 # маршрут в кабинете: сегодня и ещё столько дней
MY_NOTES = 10                  # своих последних замечаний в кабинете
NOTES_DAYS = 14                # в бригадах: решённые замечания за столько дней


def _active(on: dt.date | None = None):
    on = on or org_today()
    return and_(LoaderAssignment.started_on <= on, or_(LoaderAssignment.ended_on.is_(None), LoaderAssignment.ended_on >= on))


def loader_vehicle_ids(db: Session, employee_id: int | None, on: dt.date | None = None) -> set[int]:
    """Мусоровозы, за которыми грузчик закреплён на дату."""
    if not employee_id:
        return set()
    return set(db.scalars(select(LoaderAssignment.vehicle_id).where(LoaderAssignment.employee_id == employee_id, _active(on))))


def loaders_on(db: Session, vehicle_ids, on: dt.date | None = None) -> dict[int, list[LoaderAssignment]]:
    """Грузчики машин на дату: {машина: [закрепления]}."""
    ids = list(vehicle_ids or ())
    out: dict[int, list[LoaderAssignment]] = {}
    if not ids:
        return out
    for a in db.scalars(select(LoaderAssignment).where(LoaderAssignment.vehicle_id.in_(ids), _active(on))
                        .order_by(LoaderAssignment.started_on, LoaderAssignment.id)):
        out.setdefault(a.vehicle_id, []).append(a)
    return out


def short_name(e: Employee | None) -> str:
    return " ".join(x for x in (e.last_name, e.first_name) if x) if e else ""


def _tons(db: Session, vid: int, first: dt.date, last: dt.date) -> dict:
    rows = db.execute(select(WeighTicket.ticket_date, WeighTicket.tons).where(
        WeighTicket.vehicle_id == vid, WeighTicket.archived_at.is_(None), WeighTicket.ticket_date.between(first, last))).all()
    weighed = [float(t) for _, t in rows if t is not None]
    return {"trips": len(rows), "tons": round(sum(weighed), 3), "pending": sum(1 for _, t in rows if t is None),
            "days": len({d for d, _ in rows})}


def _note_out(n: RouteNote, with_who: bool = False) -> dict:
    out = {"id": n.id, "date": n.note_date.isoformat(), "kind": n.kind, "kind_label": ROUTE_NOTE_KINDS.get(n.kind, n.kind),
           "street": n.street, "note": n.note, "plate": n.vehicle.plate if n.vehicle else "",
           "created_at": n.created_at.isoformat() if n.created_at else None,
           "resolved": n.resolved_at is not None, "resolved_at": n.resolved_at.isoformat() if n.resolved_at else None}
    if with_who:
        out["who"] = short_name(n.employee)
    return out


# ---------------------------------------------------------------- кабинет грузчика

@router.get("/home")
def home(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("loaders.view_own"):
        raise fail(403, "forbidden", "Нет доступа к кабинету грузчика")
    emp = db.get(Employee, p.employee_id) if p.employee_id else None
    today = job_today()
    ids = loader_vehicle_ids(db, p.employee_id, today)
    own = p.scope("loaders.view_own")
    vehicles = list(db.scalars(select(Vehicle).where(Vehicle.id.in_(ids), Vehicle.archived_at.is_(None), own.cond(Vehicle.legal_entity_id))
                               .order_by(Vehicle.plate))) if ids else []
    vids = {v.id for v in vehicles}
    crew = loaders_on(db, vids, today)
    first = today.replace(day=1)
    end = today + dt.timedelta(days=DAYS_AHEAD)
    ents = {v.legal_entity_id for v in vehicles}
    plans = routes_svc.load_plans(db, today, end, RoutePlan.legal_entity_id.in_(ents)) if ents else []
    rmap, vmap = routes_svc._maps(db, plans)
    days = []
    for i in range(DAYS_AHEAD + 1):
        d = today + dt.timedelta(days=i)
        items = [{"route": r["route"], "streets": r["streets"], "note": r["note"] or r["route_note"], "plate": r["plate"], "vehicle_id": r["vehicle_id"]}
                 for r in routes_svc.entries_on(db, plans, d, rmap, vmap, vids)]
        days.append({"date": d.isoformat(), "weekday": ts.WEEKDAYS[d.weekday()], "is_today": i == 0, "items": items})
    out_vehicles = []
    for v in vehicles:
        drv = current_driver_id(db, v.id, today)
        mates = [short_name(a.employee) for a in crew.get(v.id, []) if a.employee_id != p.employee_id]
        out_vehicles.append({
            "id": v.id, "plate": v.plate, "label": _vehicle_label(v), "status": v.status, "status_label": VEHICLE_STATUSES.get(v.status, ""),
            "driver": short_name(db.get(Employee, drv)) if drv else None, "mates": mates, "tonnage": bool(v.tonnage),
            "today": _tons(db, v.id, today, today) if v.tonnage else None,
            "month": _tons(db, v.id, first, today) if v.tonnage else None,
        })
    notes = []
    if emp is not None:
        notes = [_note_out(n) for n in db.scalars(select(RouteNote).where(RouteNote.employee_id == emp.id)
                                                   .order_by(RouteNote.created_at.desc(), RouteNote.id.desc()).limit(MY_NOTES))]
    earned = None
    if emp is not None and p.can("worklog.view_own"):
        earned = {"month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}",
                  **pay.month_totals(db, emp.id, first, first.replace(day=ts.month_days(first)))}
    return {"today": today.isoformat(), "month_title": f"{ts.MONTHS[first.month - 1]} {first.year}",
            "name": " ".join(x for x in ((emp.first_name, emp.middle_name) if emp else ()) if x),
            "has_employee": emp is not None, "has_vehicle": bool(out_vehicles), "vehicles": out_vehicles, "days": days,
            "notes": notes, "note_kinds": [{"code": k, "label": v} for k, v in ROUTE_NOTE_KINDS.items()], "earned": earned,
            "can": {"requests": p.can("requests.create"), "attendance": p.can("attendance.mark_own"), "worklog": p.can("worklog.view_own")}}


class NoteIn(BaseModel):
    vehicle_id: int
    kind: str
    street: str = Field("", max_length=200)
    note: str = Field("", max_length=500)


@router.post("/notes", status_code=201)
def add_note(body: NoteIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("loaders.view_own"):
        raise fail(403, "forbidden", "Нет доступа к кабинету грузчика")
    if not p.employee_id:
        raise fail(409, "no_employee", "К вашей учётной записи не привязан сотрудник: обратитесь к администратору")
    if body.kind not in ROUTE_NOTE_KINDS:
        raise fail(422, "validation", "Выберите, что случилось", "kind")
    today = job_today()
    v = db.get(Vehicle, body.vehicle_id)
    if v is None or v.id not in loader_vehicle_ids(db, p.employee_id, today) or not p.can("loaders.view_own", v.legal_entity_id):
        raise fail(403, "forbidden", "Вы не закреплены за этой машиной", "vehicle_id")
    street, text_ = " ".join(body.street.split()), body.note.strip()
    if body.kind == "other" and len(text_) < 3:
        raise fail(422, "validation", "Напишите, что случилось", "note")
    if not street and body.kind != "other":
        raise fail(422, "validation", "Укажите улицу или адрес", "street")
    n = RouteNote(legal_entity_id=v.legal_entity_id, vehicle_id=v.id, employee_id=p.employee_id, note_date=today,
                  kind=body.kind, street=street, note=text_)
    db.add(n)
    db.flush()
    audit.log(db, p, "route_notes.create", "vehicle", v.id, v.legal_entity_id, plate=v.plate, kind=body.kind, street=street)
    emp = db.get(Employee, p.employee_id)
    title = f"{v.plate}: {ROUTE_NOTE_KINDS[body.kind].lower()}" + (f", {street}" if street else "")
    notify.safe(lambda db_: notify.push(db_, notify.users_with(db_, "route_notes.view", v.legal_entity_id), "info", title,
                                        f"Грузчик {short_name(emp)}" + (f": {notify.short(text_)}" if text_ else ""), "/loaders",
                                        v.legal_entity_id, p.id))(db)
    db.commit()
    db.refresh(n)
    return _note_out(n)


# ---------------------------------------------------------------- бригады: директор и диспетчер

def _office_scope(p: Principal, legal_entity_id: int | None):
    scope = p.scope("route_notes.view") | p.scope("assignments.manage")
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к бригадам мусоровозов")
    if legal_entity_id is not None:
        if not scope.allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        scope = scope.narrow(legal_entity_id)
    return scope


def _garbage_trucks(db: Session, scope) -> list[Vehicle]:
    waste = {v.id: v for v in routes_svc.waste_vehicles(db, scope.cond(Vehicle.legal_entity_id))}
    for v in db.scalars(select(Vehicle).where(Vehicle.tonnage.is_(True), Vehicle.archived_at.is_(None), scope.cond(Vehicle.legal_entity_id))):
        waste.setdefault(v.id, v)
    return sorted(waste.values(), key=lambda v: v.plate)


def _assignment_out(a: LoaderAssignment) -> dict:
    return {"id": a.id, "employee_id": a.employee_id, "name": short_name(a.employee), "started_on": a.started_on.isoformat(),
            "ended_on": a.ended_on.isoformat() if a.ended_on else None}


@router.get("/crews")
def crews(legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    scope = _office_scope(p, legal_entity_id)
    today = org_today()
    trucks = _garbage_trucks(db, scope)
    crew = loaders_on(db, [v.id for v in trucks], today)
    items = []
    for v in trucks:
        drv = current_driver_id(db, v.id, today)
        items.append({"id": v.id, "plate": v.plate, "label": _vehicle_label(v), "legal_entity_id": v.legal_entity_id,
                      "status": v.status, "status_label": VEHICLE_STATUSES.get(v.status, ""),
                      "driver": short_name(db.get(Employee, drv)) if drv else None,
                      "loaders": [_assignment_out(a) for a in crew.get(v.id, [])],
                      "can_assign": p.can("assignments.manage", v.legal_entity_id)})
    # кого можно закрепить: работающие в юрлицах этих машин, не в архиве и не уволенные
    ents = sorted({v.legal_entity_id for v in trucks if p.can("assignments.manage", v.legal_entity_id)})
    people = []
    if ents:
        busy = {a.employee_id: a.vehicle_id for lst in loaders_on(db, [v.id for v in trucks], today).values() for a in lst}
        rows = db.execute(select(Employee, EmploymentAssignment.legal_entity_id).join(
            EmploymentAssignment, EmploymentAssignment.employee_id == Employee.id).where(
            EmploymentAssignment.legal_entity_id.in_(ents), Employee.archived_at.is_(None),
            or_(Employee.fired_on.is_(None), Employee.fired_on >= today),
            or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= today),
            or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= today))
            .order_by(Employee.last_name, Employee.first_name, Employee.id)).all()
        seen: dict[int, dict] = {}
        for e, ent in rows:
            x = seen.setdefault(e.id, {"id": e.id, "name": short_name(e), "position": e.position, "is_driver": e.is_driver,
                                       "busy_vehicle_id": busy.get(e.id), "entities": []})
            x["entities"].append(ent)
        people = list(seen.values())
        people.sort(key=lambda x: (x["is_driver"], x["name"].lower()))    # не водители (грузчики) первыми
    notes = []
    if p.scope("route_notes.view"):
        ns = p.scope("route_notes.view")
        if legal_entity_id is not None:
            ns = ns.narrow(legal_entity_id)
        since = today - dt.timedelta(days=NOTES_DAYS)
        notes = [dict(_note_out(n, True), can_resolve=p.can("route_notes.resolve", n.legal_entity_id))
                 for n in db.scalars(select(RouteNote).where(ns.cond(RouteNote.legal_entity_id),
                                                            or_(RouteNote.resolved_at.is_(None), RouteNote.note_date >= since))
                                     .order_by(RouteNote.resolved_at.is_not(None), RouteNote.created_at.desc(), RouteNote.id.desc()).limit(200))]
    return {"today": today.isoformat(), "items": items, "people": people, "notes": notes,
            "open_notes": sum(1 for n in notes if not n["resolved"]),
            "entities": allowed_entities(db, p, "route_notes.view") if p.scope("route_notes.view") else allowed_entities(db, p, "assignments.manage"),
            "can": {"notes": bool(p.scope("route_notes.view")), "assign": bool(p.scope("assignments.manage"))}}


class AssignIn(BaseModel):
    vehicle_id: int
    employee_id: int
    started_on: dt.date | None = None


def _truck_for_manage(db: Session, p: Principal, vid: int) -> Vehicle:
    v = db.get(Vehicle, vid)
    if v is None or not p.can("assignments.manage", v.legal_entity_id):
        raise fail(404, "not_found", "Машина не найдена")
    if v.archived_at:
        raise fail(409, "vehicle_archived", "Машина в архиве")
    return v


@router.post("/assign", status_code=201)
def assign(body: AssignIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    v = _truck_for_manage(db, p, body.vehicle_id)
    emp = db.get(Employee, body.employee_id)
    if emp is None or emp.archived_at:
        raise fail(422, "bad_employee", "Сотрудник не найден или в архиве", "employee_id")
    if not employee_in_entity(db, emp, v.legal_entity_id):
        raise fail(422, "wrong_entity", "Сотрудник не работает в юрлице этой машины", "employee_id")
    today = org_today()
    start = body.started_on or today
    if start > today + dt.timedelta(days=31) or start < today - dt.timedelta(days=31):
        raise fail(422, "bad_dates", "Дату начала можно ставить в пределах месяца от сегодня", "started_on")
    other = db.scalar(select(LoaderAssignment).where(LoaderAssignment.employee_id == emp.id, _active(start)).limit(1))
    if other is not None:
        msg = "Этот грузчик уже закреплён за этой машиной" if other.vehicle_id == v.id else f"Грузчик уже закреплён за {other.vehicle.plate}: сначала снимите его там"
        raise fail(409, "already_assigned", msg, "employee_id")
    a = LoaderAssignment(vehicle_id=v.id, employee_id=emp.id, started_on=start, created_by_id=p.id)
    db.add(a)
    db.flush()
    audit.log(db, p, "loaders.assign", "vehicle", v.id, v.legal_entity_id, plate=v.plate, employee_id=emp.id, started_on=start.isoformat())
    db.commit()
    db.refresh(a)
    return _assignment_out(a)


@router.post("/assignments/{aid}/end")
def end(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a = db.get(LoaderAssignment, aid)
    if a is None:
        raise fail(404, "not_found", "Закрепление не найдено")
    v = _truck_for_manage(db, p, a.vehicle_id)
    today = org_today()
    if a.ended_on is not None and a.ended_on < today:
        raise fail(409, "already_ended", "Грузчик уже снят с машины")
    # как у водителей: последним днём ставится вчера; закрепили сегодня (или на будущее) и сразу сняли — запись удаляется
    last = today - dt.timedelta(days=1)
    removed = last < a.started_on
    out = _assignment_out(a)
    if removed:
        db.delete(a)
    else:
        a.ended_on = last
        out = _assignment_out(a)
    audit.log(db, p, "loaders.unassign", "vehicle", v.id, v.legal_entity_id, plate=v.plate, employee_id=a.employee_id, removed=removed)
    db.commit()
    return dict(out, removed=removed)


@router.post("/notes/{nid}/resolve")
def resolve(nid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    n = db.get(RouteNote, nid)
    if n is None or not p.can("route_notes.view", n.legal_entity_id):
        raise fail(404, "not_found", "Замечание не найдено")
    if not p.can("route_notes.resolve", n.legal_entity_id):
        raise fail(403, "forbidden", "Нет права отмечать замечания")
    if n.resolved_at is None:
        n.resolved_at, n.resolved_by_id = utcnow(), p.id
        audit.log(db, p, "route_notes.resolve", "vehicle", n.vehicle_id, n.legal_entity_id, note_id=n.id)
        db.commit()
    return _note_out(n, True)


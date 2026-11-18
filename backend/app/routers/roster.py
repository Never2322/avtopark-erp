# -*- coding: utf-8 -*-
"""Сводка «Водители и техника»: за каждой машиной свой водитель, и сразу видно, что с ней сейчас.

Только чтение: закрепления водителей меняются в карточке техники (право «assignments.manage»), статусы там же.
Что человек видит в строке, зависит от его прав: задания — с правом на задания, расход — с правом на заправки и так далее.
Чего нет по правам, в ответ не попадает (поле равно null), а не прячется на экране.

Замечания в строке (их видно и в выгрузке):
- нет водителя: у машины в работе или на ремонте никто не закреплён;
- расход выше нормы: по последнему отрезку между полными заправками;
- задание не закрыто за прошлые дни;
- пробег давно не обновляли: у машины «в работе» нет принятого показания дольше недели.
"""
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, Response
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, num, org_today, utcnow
from app.db import get_db
from app.exporting import check_format, local_naive, table_response
from app.deps import Principal, current, require
from app.enums import EFFECTIVE_STATES, JOB_STATES, REQUEST_OPEN, VEHICLE_STATUSES
from app.models import (Employee, EmploymentAssignment, FuelFill, Job, ServiceRequest, Trip, Vehicle, VehicleAssignment, VehicleReading)
from app.registry import _vehicle_label, org_tz
from app.resources import job_today
from app.routers.jobs import OPEN_STATES
from app.services import access
from app.services.fuel import norms, over_norm, segments
from app.services.vehicles import active_assignment_filter

router = APIRouter(prefix="/roster", tags=["Водители и техника"])

STALE_READING_DAYS = 7
# Для этих статусов у машины должен быть водитель; в аренде и «не используется» водитель не нужен.
NEEDS_DRIVER = {"in_work", "in_repair"}
FLAGS = {
    "no_driver": "Нет водителя",
    "fuel_over": "Расход выше нормы",
    "stale_job": "Задание не закрыто за прошлые дни",
    "old_reading": f"Пробег не обновляли больше {STALE_READING_DAYS} дней",
}
ONLY = {"", "no_driver", "attention"}


def full_name(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name) if x)


def last_per_vehicle(db: Session, model, date_col, ids: list[int], scope) -> dict[int, object]:
    """Последняя запись (рейс или заправка) по каждой машине одним запросом."""
    if not ids or not scope:
        return {}
    d = getattr(model, date_col)
    rn = func.row_number().over(partition_by=model.vehicle_id, order_by=(d.desc(), model.id.desc())).label("rn")
    sub = select(model.id.label("id"), rn).where(model.vehicle_id.in_(ids), model.archived_at.is_(None), scope.cond(model.legal_entity_id)).subquery()
    top = {r.id for r in db.execute(select(sub.c.id).where(sub.c.rn == 1))}
    if not top:
        return {}
    return {x.vehicle_id: x for x in db.scalars(select(model).where(model.id.in_(top)))}


def job_info(j_rows: list[Job], today: dt.date) -> dict:
    """Задания машины на сегодня и сколько незакрытых за прошлые дни."""
    todays = [j for j in j_rows if j.job_date == today and j.state != "cancelled"]
    stale = sum(1 for j in j_rows if j.job_date < today and j.state in OPEN_STATES)
    rank = {"on_site": 0, "planned": 1, "done": 2}
    todays.sort(key=lambda j: (rank.get(j.state, 3), j.time_from == "", j.time_from, j.id))
    nxt = todays[0] if todays else None
    return {"today": len(todays), "open": sum(1 for j in todays if j.state in OPEN_STATES),
            "done": sum(1 for j in todays if j.state == "done"), "stale": stale,
            "next": ({"id": nxt.id, "address": nxt.address, "customer": nxt.customer, "time_from": nxt.time_from,
                      "state": nxt.state, "state_label": JOB_STATES[nxt.state]} if nxt else None)}


def build(db: Session, p: Principal, *, legal_entity_id: int | None, q: str, status: str, only: str, export: bool = False) -> dict:
    scope = p.scope("roster.export" if export else "roster.view")
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    if status and status not in VEHICLE_STATUSES:
        raise fail(422, "bad_status", "Неизвестный статус", "status")
    if only not in ONLY:
        raise fail(422, "bad_filter", "Неизвестный отбор", "only")

    cond = [access.view_condition(p, db), scope.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None)]
    if legal_entity_id is not None:
        cond.append(Vehicle.legal_entity_id == legal_entity_id)
    vehicles = db.scalars(select(Vehicle).where(*cond).order_by(Vehicle.plate, Vehicle.id)).all()
    ids = [v.id for v in vehicles]
    today = job_today()

    # кто закреплён сейчас: основной и запасные
    drivers: dict[int, list[dict]] = {}
    if ids:
        rows = db.execute(select(VehicleAssignment.vehicle_id, VehicleAssignment.role, Employee)
                          .join(Employee, Employee.id == VehicleAssignment.employee_id)
                          .where(VehicleAssignment.vehicle_id.in_(ids), active_assignment_filter())
                          .order_by(VehicleAssignment.role.desc(), VehicleAssignment.started_on, VehicleAssignment.id)).all()
        # «primary» позже «backup» по алфавиту, поэтому сортировка по убыванию ставит основного первым
        for vid, role, e in rows:
            drivers.setdefault(vid, []).append({"id": e.id, "name": full_name(e), "role": role, "archived": e.archived_at is not None})

    sc_jobs, sc_trips, sc_fuel, sc_req = (p.scope("jobs.view"), p.scope("trips.view"), p.scope("fuel_fills.view"), p.scope("requests.view"))
    jobs: dict[int, list[Job]] = {}
    if ids and sc_jobs:
        for j in db.scalars(select(Job).where(Job.vehicle_id.in_(ids), Job.archived_at.is_(None), sc_jobs.cond(Job.legal_entity_id),
                                              or_(Job.job_date == today, and_(Job.job_date < today, Job.state.in_(OPEN_STATES))))):
            jobs.setdefault(j.vehicle_id, []).append(j)
    trips = last_per_vehicle(db, Trip, "trip_date", ids, sc_trips)
    fills = last_per_vehicle(db, FuelFill, "fill_date", ids, sc_fuel)
    tank_norm = norms(db, fills.keys()) if fills else {}
    open_req: dict[int, int] = {}
    if ids and sc_req:
        open_req = dict(db.execute(select(ServiceRequest.vehicle_id, func.count()).where(
            ServiceRequest.vehicle_id.in_(ids), ServiceRequest.status.in_(REQUEST_OPEN), sc_req.cond(ServiceRequest.legal_entity_id))
            .group_by(ServiceRequest.vehicle_id)).all())
    last_reading: dict[int, dt.datetime] = {}
    if ids:
        last_reading = dict(db.execute(select(VehicleReading.vehicle_id, func.max(VehicleReading.recorded_at)).where(
            VehicleReading.vehicle_id.in_(ids), VehicleReading.review_state.in_(EFFECTIVE_STATES)).group_by(VehicleReading.vehicle_id)).all())

    now = utcnow()
    items = []
    for v in vehicles:
        ds = drivers.get(v.id, [])
        entity_ok = lambda s: s.allows(v.legal_entity_id)        # noqa: E731  (право на этот раздел действует в юрлице машины)
        job = job_info(jobs.get(v.id, []), today) if entity_ok(sc_jobs) else None
        trip = trips.get(v.id)
        fill = fills.get(v.id)
        fuel = None
        if fill is not None:
            seg = list(segments(db, v.id).values())
            per100 = seg[-1]["per100"] if seg else None
            norm = (tank_norm.get(v.id) or (None, None))[1]
            fuel = {"date": fill.fill_date.isoformat(), "liters": float(fill.liters), "per100": float(per100) if per100 is not None else None,
                    "norm": float(norm) if norm else None, "over_pct": over_norm(per100, norm)}
        age = None
        if v.id in last_reading:
            age = max(0, (now - last_reading[v.id]).days)
        flags = []
        if v.status in NEEDS_DRIVER and not any(d["role"] == "primary" for d in ds):
            flags.append("no_driver")
        if fuel and fuel["over_pct"] is not None:
            flags.append("fuel_over")
        if job and job["stale"]:
            flags.append("stale_job")
        if v.status == "in_work" and (age is None or age > STALE_READING_DAYS):
            flags.append("old_reading")
        items.append({
            "id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(), "label": _vehicle_label(v), "legal_entity_id": v.legal_entity_id,
            "legal_entity": v.legal_entity.short_name or v.legal_entity.name, "type": v.type.name,
            "status": v.status, "status_label": VEHICLE_STATUSES[v.status],
            "status_since": v.status_changed_at.isoformat() if v.status_changed_at else None,
            "primary_reading": v.type.primary_reading,
            "current_km": float(v.current_km) if v.current_km is not None else None,
            "current_hours": float(v.current_hours) if v.current_hours is not None else None,
            "reading_age_days": age,
            "drivers": ds, "job": job,
            "last_trip": ({"date": trip.trip_date.isoformat(), "route": trip.route} if trip and entity_ok(sc_trips) else None),
            "fuel": fuel if fill is not None and entity_ok(sc_fuel) else None,
            "open_requests": open_req.get(v.id, 0) if entity_ok(sc_req) else None,
            "flags": [{"code": c, "label": FLAGS[c]} for c in flags],
        })

    totals = {"vehicles": len(items), "no_driver": sum(1 for i in items if any(f["code"] == "no_driver" for f in i["flags"])),
              "attention": sum(1 for i in items if i["flags"]),
              "by_status": [{"code": k, "label": lbl, "count": sum(1 for i in items if i["status"] == k)} for k, lbl in VEHICLE_STATUSES.items()]}
    # отбор применяется после подсчёта итогов: цифры сверху не меняются от того, что человек нажал
    shown = items
    if status:
        shown = [i for i in shown if i["status"] == status]
    if only == "no_driver":
        shown = [i for i in shown if any(f["code"] == "no_driver" for f in i["flags"])]
    elif only == "attention":
        shown = [i for i in shown if i["flags"]]
    if q.strip():
        t = q.strip().lower().replace(" ", "")
        shown = [i for i in shown if t in i["plate"].lower().replace(" ", "") or t in i["label"].lower().replace(" ", "")
                 or any(t in d["name"].lower().replace(" ", "") for d in i["drivers"])]
    return {"today": today.isoformat(), "totals": totals, "items": shown,
            "can": {"jobs": bool(sc_jobs), "trips": bool(sc_trips), "fuel": bool(sc_fuel), "requests": bool(sc_req),
                    "export": p.can("roster.export"), "assign": p.can("assignments.manage"), "employees": p.can("employees.view")}}


def free_drivers(db: Session, p: Principal, legal_entity_id: int | None) -> list[dict]:
    """Водители, которым не закреплена ни одна машина: их стоит куда-то поставить."""
    roster, staff = p.scope("roster.view"), p.scope("employees.view")
    if not staff:
        return []
    ents = None if (roster.everything and staff.everything) else (staff.ids if roster.everything else roster.ids if staff.everything else roster.ids & staff.ids)
    if legal_entity_id is not None:
        ents = {legal_entity_id} if ents is None or legal_entity_id in ents else set()
    today = org_today()
    cond = [Employee.is_driver.is_(True), Employee.archived_at.is_(None), or_(Employee.fired_on.is_(None), Employee.fired_on >= today),
            ~exists().where(VehicleAssignment.employee_id == Employee.id, active_assignment_filter(today))]
    if ents is not None:
        if not ents:
            return []
        cond.append(exists().where(EmploymentAssignment.employee_id == Employee.id, EmploymentAssignment.legal_entity_id.in_(ents),
                                   or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= today),
                                   or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= today)))
    return [{"id": e.id, "name": full_name(e)} for e in db.scalars(select(Employee).where(*cond).order_by(Employee.last_name, Employee.first_name).limit(200))]


@router.get("")
def roster(legal_entity_id: int | None = None, q: str = "", status: str = "", only: str = "",
           p: Principal = Depends(require("roster.view")), db: Session = Depends(get_db)):
    data = build(db, p, legal_entity_id=legal_entity_id, q=q, status=status, only=only)
    data["free_drivers"] = free_drivers(db, p, legal_entity_id) if data["can"]["employees"] else None
    return data


@router.get("/export.{fmt}")
def export_file(fmt: str, legal_entity_id: int | None = None, q: str = "", status: str = "", only: str = "",
                p: Principal = Depends(require("roster.export")), db: Session = Depends(get_db)):
    check_format(fmt)
    # выгрузка идёт по тем же правилам, что и экран; чужие юрлица и разделы без права в файл не попадают
    data = build(db, p, legal_entity_id=legal_entity_id, q=q, status=status, only=only, export=True)
    can = data["can"]
    head = ["Госномер", "Машина", "Юрлицо", "Водитель", "Запасные водители", "Состояние", "С какого времени"]
    kinds: list = [None, None, None, None, None, None, "datetime"]
    if can["jobs"]:
        head += ["Заданий сегодня", "Ближайшее задание"]
        kinds += ["num", None]
    if can["trips"]:
        head += ["Последний рейс"]
        kinds += [None]
    if can["fuel"]:
        head += ["Последняя заправка", "Расход, л/100 км", "Норма, л/100 км"]
        kinds += [None, "dec1", "dec1"]
    if can["requests"]:
        head += ["Открытых обращений"]
        kinds += ["num"]
    head += ["Пробег, км", "Моточасы, ч", "Замечания"]
    kinds += ["int", "dec1", None]
    rows: list[list] = [head]
    for i in data["items"]:
        main = [d["name"] for d in i["drivers"] if d["role"] == "primary"]
        spare = [d["name"] for d in i["drivers"] if d["role"] != "primary"]
        since = local_naive(dt.datetime.fromisoformat(i["status_since"])) if i["status_since"] else None
        r = [i["plate"], i["name"], i["legal_entity"], ", ".join(main) or None, ", ".join(spare) or None, i["status_label"], since]
        if can["jobs"]:
            j = i["job"]
            r += [j["today"] if j else None, (f"{j['next']['time_from'] + ' ' if j['next']['time_from'] else ''}{j['next']['address']}" if j and j["next"] else None)]
        if can["trips"]:
            t = i["last_trip"]
            r += [f"{dt.date.fromisoformat(t['date']).strftime('%d.%m.%Y')} {t['route']}" if t else None]
        if can["fuel"]:
            f = i["fuel"]
            r += [f"{dt.date.fromisoformat(f['date']).strftime('%d.%m.%Y')}, {num(Decimal(str(f['liters'])))} л" if f else None,
                  f["per100"] if f and f["per100"] is not None else None, f["norm"] if f and f["norm"] else None]
        if can["requests"]:
            r += [i["open_requests"] if i["open_requests"] is not None else None]
        r += [i["current_km"], i["current_hours"], "; ".join(f["label"] for f in i["flags"]) or None]
        rows.append(r)
    audit.log(db, p, "roster.export", "vehicle", None, legal_entity_id, rows=len(rows) - 1)
    db.commit()
    return table_response(fmt, "roster", "Водители и техника", rows, kinds)


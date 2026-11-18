# -*- coding: utf-8 -*-
"""Кабинет водителя: что сейчас с его машиной, открытые обращения, последняя заправка и расход.

Задания и записи (рейсы, заправки) водитель получает из своих разделов («/jobs/mine», «/entries/mine»): здесь только то,
что им не хватает для главной страницы. Видна только техника, закреплённая за ним сейчас; у человека без закреплённой
машины ответ пустой, а не ошибка: страница объясняет, что делать.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.enums import REQUEST_OPEN, REQUEST_PRIORITIES, REQUEST_STATUSES, VEHICLE_STATUSES
from app.models import Employee, FuelFill, ServiceRequest, Vehicle
from app.registry import _vehicle_label
from app.resources import job_today
from app.routers.requests_ import visible_condition
from app.services import hauls as hauls_svc, payroll as pay, routes as routes_svc, timesheet as ts, weigh as weigh_svc
from app.services.fuel import norms, over_norm, segments
from app.services.vehicles import assigned_vehicle_ids

router = APIRouter(prefix="/driver", tags=["Кабинет водителя"])

REQUESTS_PER_VEHICLE = 5


@router.get("/home")
def home(p: Principal = Depends(current), db: Session = Depends(get_db)):
    own = p.scope("vehicles.view_own")
    if not own:
        raise fail(403, "forbidden", "Нет доступа к кабинету водителя")
    emp = db.get(Employee, p.employee_id) if p.employee_id else None
    ids = assigned_vehicle_ids(db, p.employee_id)
    vehicles = []
    if ids:
        rows = db.scalars(select(Vehicle).where(Vehicle.id.in_(ids), Vehicle.archived_at.is_(None),
                                                own.cond(Vehicle.legal_entity_id)).order_by(Vehicle.plate)).all()
        tank_norm = norms(db, [v.id for v in rows])
        can_requests = bool(p.scope("requests.view") or p.scope("requests.view_own"))
        fuel_scope = p.scope("fuel_fills.view_own") | p.scope("fuel_fills.view")
        for v in rows:
            reqs = []
            if can_requests:
                reqs = db.scalars(select(ServiceRequest).where(
                    ServiceRequest.vehicle_id == v.id, ServiceRequest.status.in_(REQUEST_OPEN), visible_condition(p))
                    .order_by(ServiceRequest.created_at.desc()).limit(REQUESTS_PER_VEHICLE)).all()
            fuel = None
            if fuel_scope.allows(v.legal_entity_id):
                last = db.scalar(select(FuelFill).where(FuelFill.vehicle_id == v.id, FuelFill.archived_at.is_(None))
                                 .order_by(FuelFill.fill_date.desc(), FuelFill.id.desc()).limit(1))
                if last is not None:
                    seg = list(segments(db, v.id).values())
                    per100 = seg[-1]["per100"] if seg else None
                    norm = (tank_norm.get(v.id) or (None, None))[1]
                    fuel = {"date": last.fill_date.isoformat(), "liters": float(last.liters),
                            "per100": float(per100) if per100 is not None else None, "norm": float(norm) if norm else None,
                            "over_pct": over_norm(per100, norm)}
            vehicles.append({
                "id": v.id, "plate": v.plate, "label": _vehicle_label(v), "type": v.type.name, "status": v.status,
                "status_label": VEHICLE_STATUSES[v.status], "status_since": v.status_changed_at.isoformat() if v.status_changed_at else None,
                "primary_reading": v.type.primary_reading,
                "current_km": float(v.current_km) if v.current_km is not None else None,
                "current_hours": float(v.current_hours) if v.current_hours is not None else None,
                "requests": [{"id": r.id, "number": r.number, "title": r.title, "status": r.status, "status_label": REQUEST_STATUSES[r.status],
                              "priority": r.priority, "priority_label": REQUEST_PRIORITIES[r.priority]} for r in reqs],
                "fuel": fuel,
            })
    earned = None
    if emp is not None and p.can("worklog.view_own"):
        first = job_today().replace(day=1)
        earned = {"month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}",
                  **pay.month_totals(db, emp.id, first, first.replace(day=ts.month_days(first)))}
    return {"today": job_today().isoformat(), "earned": earned, "name": " ".join(x for x in ((emp.first_name, emp.middle_name) if emp else ()) if x),
            "has_vehicle": bool(vehicles), "vehicles": vehicles,
            "can": {"requests": p.can("requests.create"), "waybills": p.can("waybills.view_own") or p.can("waybills.create_own"),
                    "trip": p.can("trips.create_own"), "fuel": p.can("fuel_fills.create_own"), "jobs": bool(p.scope("jobs.view_own")),
                    "reading": p.can("readings.create_own"), "tonnage": weigh_svc.has_tonnage(db, p),
                    "status": p.can("vehicle_status.change_own"), "hauls": hauls_svc.has_hauls(db, p), "routes": routes_svc.has_routes(db, p)}}

# -*- coding: utf-8 -*-
"""Маршруты мусоровозов: что едет, когда и по каким улицам.

Расписание хранится двумя видами записей (RoutePlan):
  weekly: «машина Х ездит по маршруту А по понедельникам, средам и пятницам»;
  date:   «в этот день машина Х едет по маршруту А». Такая запись заменяет обычное расписание этого маршрута в этот
          день (заболела машина, подменили). Если на маршрут есть запись на дату, обычные записи этого маршрута в тот
          день не действуют, даже у других машин.
Один маршрут могут ехать несколько машин, одна машина может ехать несколько маршрутов за день: это не ошибка.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.enums import VEHICLE_STATUSES, WEEKDAY_FIELDS
from app.models import Employee, GarbageRoute, RoutePlan, Vehicle, VehicleAssignment, VehicleType
from app.services import timesheet as ts
from app.services.vehicles import active_assignment_filter, assigned_vehicle_ids

WASTE_CODES = ("truck_waste",)                 # мусоровоз по типу техники
WASTE_WORDS = ("мусоровоз",)                   # и по названию типа (типы из старой программы называются по-своему)
BOARD_BACK_DAYS = 400                          # как далеко от сегодняшнего дня можно смотреть расписание
BOARD_FORWARD_DAYS = 400
MINE_DAYS = 14                                 # на сколько дней вперёд водитель видит свои маршруты


# ---------------------------------------------------------------- тексты

def streets_list(text: str) -> list[str]:
    """Улицы из текста: каждая с новой строки (или через «;»). Если всё написано в одну строку через запятую,
    делим по запятым."""
    lines = [x.strip() for x in re.split(r"[\r\n;]+", text or "") if x.strip()]
    if len(lines) == 1 and "," in lines[0]:
        lines = [x.strip() for x in lines[0].split(",") if x.strip()]
    return lines


def _runs(idx: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in idx:
        if out and out[-1][1] == i - 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def weekdays_text(idx: list[int]) -> str:
    """«пн–пт», «пн, ср, пт», «каждый день»: подряд идущие три дня и больше сжимаются в «с–по»."""
    idx = sorted(set(idx))
    if len(idx) == 7:
        return "каждый день"
    parts = []
    for a, b in _runs(idx):
        w = ts.WEEKDAYS
        parts.append(w[a] if a == b else f"{w[a]}, {w[b]}" if b == a + 1 else f"{w[a]}–{w[b]}")
    return ", ".join(parts)


def plan_weekdays(plan: RoutePlan) -> list[int]:
    return [i for i, f in enumerate(WEEKDAY_FIELDS) if getattr(plan, f)]


def plan_when(plan: RoutePlan) -> str:
    if plan.kind == "date" and plan.on_date:
        return f"{plan.on_date:%d.%m.%Y} ({ts.WEEKDAYS[plan.on_date.weekday()]})"
    return weekdays_text(plan_weekdays(plan))


def vehicle_label(v: Vehicle) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·")


def person_name(e: Employee | None) -> str:
    return " ".join(x for x in ((e.last_name, e.first_name, e.middle_name) if e else ()) if x)


# ---------------------------------------------------------------- какие плановые записи действуют в день

def effective(plans: list[RoutePlan], on: dt.date) -> list[RoutePlan]:
    """Записи расписания, которые действуют в этот день, с учётом замен «на дату»."""
    wd = WEEKDAY_FIELDS[on.weekday()]
    dated = [p for p in plans if p.kind == "date" and p.on_date == on]
    replaced = {p.route_id for p in dated}
    weekly = [p for p in plans if p.kind == "weekly" and getattr(p, wd) and p.route_id not in replaced]
    return dated + weekly


def load_plans(db: Session, start: dt.date, end: dt.date, entity_cond=None) -> list[RoutePlan]:
    """Все не убранные в архив записи расписания, которые могут действовать в окне дат: обычные и «на дату» из окна."""
    q = select(RoutePlan).where(RoutePlan.archived_at.is_(None),
                                or_(RoutePlan.kind == "weekly", RoutePlan.on_date.between(start, end)))
    if entity_cond is not None:
        q = q.where(entity_cond)
    return list(db.scalars(q))


def _drivers_on(db: Session, vehicle_ids: set[int], on: dt.date) -> dict[int, Employee]:
    """Кто закреплён за машинами на дату: если закреплено несколько, тот, кого закрепили последним."""
    if not vehicle_ids:
        return {}
    rows = db.execute(select(VehicleAssignment.vehicle_id, Employee)
                      .join(Employee, Employee.id == VehicleAssignment.employee_id)
                      .where(VehicleAssignment.vehicle_id.in_(vehicle_ids), active_assignment_filter(on))
                      .order_by(VehicleAssignment.started_on, VehicleAssignment.id)).all()
    out: dict[int, Employee] = {}
    for vid, emp in rows:                       # поздние закрепления перезаписывают ранние
        out[vid] = emp
    return out


def _entry(plan: RoutePlan, route: GarbageRoute, veh: Vehicle, driver: Employee | None) -> dict:
    return {
        "plan_id": plan.id, "route_id": route.id, "route": route.name, "streets": streets_list(route.streets),
        "route_note": route.note, "vehicle_id": veh.id, "plate": veh.plate, "vehicle": vehicle_label(veh),
        "status": veh.status, "status_name": VEHICLE_STATUSES.get(veh.status, ""),
        "driver_id": driver.id if driver else None, "driver": person_name(driver) or None,
        "source": plan.kind, "note": plan.note,
    }


def _maps(db: Session, plans: list[RoutePlan]) -> tuple[dict[int, GarbageRoute], dict[int, Vehicle]]:
    rids = {p.route_id for p in plans}
    vids = {p.vehicle_id for p in plans}
    routes = {r.id: r for r in db.scalars(select(GarbageRoute).where(GarbageRoute.id.in_(rids)))} if rids else {}
    vehs = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vids)))} if vids else {}
    return routes, vehs


def entries_on(db: Session, plans: list[RoutePlan], on: dt.date, routes, vehs, vehicle_ids: set[int] | None = None) -> list[dict]:
    """Строки «маршрут: машина, водитель» на день. vehicle_ids: показать только эти машины (водителю его машины);
    замены «на дату» учитываются по всем машинам маршрута, поэтому фильтр стоит после них."""
    todays = [p for p in effective(plans, on)
              if p.route_id in routes and p.vehicle_id in vehs and not routes[p.route_id].archived_at and not vehs[p.vehicle_id].archived_at]
    if vehicle_ids is not None:
        todays = [p for p in todays if p.vehicle_id in vehicle_ids]
    drivers = _drivers_on(db, {p.vehicle_id for p in todays}, on)
    rows = [_entry(p, routes[p.route_id], vehs[p.vehicle_id], drivers.get(p.vehicle_id)) for p in todays]
    rows.sort(key=lambda r: (r["route"].lower(), r["plate"]))
    return rows


# ---------------------------------------------------------------- мусоровозы

def waste_vehicles(db: Session, entity_cond=None) -> list[Vehicle]:
    q = (select(Vehicle).join(VehicleType, VehicleType.id == Vehicle.type_id)
         .where(Vehicle.archived_at.is_(None),
                or_(VehicleType.code.in_(WASTE_CODES), *[func.lower(VehicleType.name).like(f"%{w}%") for w in WASTE_WORDS])))
    if entity_cond is not None:
        q = q.where(entity_cond)
    return list(db.scalars(q.order_by(Vehicle.plate)))


def waste_vehicle_ids(db: Session, vehicle_ids) -> list[int]:
    ids = list(vehicle_ids or ())
    if not ids:
        return []
    # мусоровоз по типу, по марке или модели («КАМАЗ мусоровоз» с типом «Самосвал» из старой программы) или добавлен в бригады вручную
    q = (select(Vehicle.id).join(VehicleType, VehicleType.id == Vehicle.type_id)
         .where(Vehicle.id.in_(ids), Vehicle.archived_at.is_(None),
                or_(VehicleType.code.in_(WASTE_CODES), *[func.lower(VehicleType.name).like(f"%{w}%") for w in WASTE_WORDS],
                    *[func.lower(Vehicle.model).like(f"%{w}%") for w in WASTE_WORDS], *[func.lower(Vehicle.make).like(f"%{w}%") for w in WASTE_WORDS],
                    Vehicle.in_crews.is_(True))))
    return list(db.scalars(q))


def has_garbage(db: Session, p) -> bool:
    """Водитель мусоровоза: за ним закреплён мусоровоз. Только у таких людей в меню группа «Мусоровозы»."""
    if p.employee_id is None:
        return False
    return bool(waste_vehicle_ids(db, assigned_vehicle_ids(db, p.employee_id)))


def has_routes(db: Session, p) -> bool:
    """Нужна ли человеку вкладка «Мои маршруты»: есть право и за ним закреплена машина, у которой есть маршруты
    (мусоровоз или любая машина, которую поставили в расписание)."""
    if not (p.can("route_plan.view_own") and p.employee_id is not None):
        return False
    mine = assigned_vehicle_ids(db, p.employee_id)
    if not mine:
        return False
    if waste_vehicle_ids(db, mine):
        return True
    return db.scalar(select(RoutePlan.id).where(RoutePlan.vehicle_id.in_(mine), RoutePlan.archived_at.is_(None)).limit(1)) is not None


# ---------------------------------------------------------------- водитель

def mine_payload(db: Session, employee_id: int, today: dt.date) -> dict:
    """Свои маршруты: на сегодня, на ближайшие дни и обычное расписание по дням недели."""
    mine = assigned_vehicle_ids(db, employee_id)
    vehs_mine = list(db.scalars(select(Vehicle).where(Vehicle.id.in_(mine)).order_by(Vehicle.plate))) if mine else []
    end = today + dt.timedelta(days=MINE_DAYS - 1)
    ents = {v.legal_entity_id for v in vehs_mine}
    plans = load_plans(db, today, end, RoutePlan.legal_entity_id.in_(ents)) if ents else []
    routes, vehs = _maps(db, plans)
    days = []
    for i in range(MINE_DAYS):
        d = today + dt.timedelta(days=i)
        rows = entries_on(db, plans, d, routes, vehs, mine)
        days.append({"date": d.isoformat(), "weekday": ts.WEEKDAYS[d.weekday()], "is_today": i == 0, "items": rows})
    # обычное расписание: по дням недели, только маршруты своих машин
    week = []
    for wd in range(7):
        names = []
        for p in plans:
            if p.kind == "weekly" and p.vehicle_id in mine and getattr(p, WEEKDAY_FIELDS[wd]) and p.route_id in routes and not routes[p.route_id].archived_at:
                r = routes[p.route_id]
                names.append({"route_id": r.id, "route": r.name, "streets": streets_list(r.streets), "vehicle": vehicle_label(vehs[p.vehicle_id]) if p.vehicle_id in vehs else ""})
        names.sort(key=lambda x: x["route"].lower())
        week.append({"weekday": ts.WEEKDAYS[wd], "index": wd, "items": names})
    return {"today": today.isoformat(), "vehicles": [{"id": v.id, "label": vehicle_label(v)} for v in vehs_mine],
            "days": days, "week": week, "has_any": any(d["items"] for d in days) or any(w["items"] for w in week)}


# ---------------------------------------------------------------- директор и диспетчер

def board_payload(db: Session, on: dt.date, scope) -> dict:
    """Что едет в выбранный день: маршруты с машинами и водителями, маршруты без машины, мусоровозы без маршрута.
    scope: в каких юрлицах человек видит расписание."""
    plans = load_plans(db, on, on, scope.cond(RoutePlan.legal_entity_id))
    routes, vehs = _maps(db, plans)
    rows = entries_on(db, plans, on, routes, vehs)
    by_route: dict[int, dict] = {}
    for r in rows:
        g = by_route.setdefault(r["route_id"], {"route_id": r["route_id"], "route": r["route"], "streets": r["streets"],
                                                "note": r["route_note"], "trucks": []})
        g["trucks"].append({k: r[k] for k in ("plan_id", "vehicle_id", "plate", "vehicle", "status", "status_name", "driver_id", "driver", "source", "note")})
    # маршруты, которые в этот день никто не едет, и мусоровозы, которым маршрута нет
    all_routes = list(db.scalars(select(GarbageRoute).where(GarbageRoute.archived_at.is_(None), scope.cond(GarbageRoute.legal_entity_id))
                                 .order_by(GarbageRoute.name)))
    without_truck = [{"route_id": r.id, "route": r.name, "streets": streets_list(r.streets)} for r in all_routes if r.id not in by_route]
    busy = {r["vehicle_id"] for r in rows}
    idle_vehs = [v for v in waste_vehicles(db, scope.cond(Vehicle.legal_entity_id)) if v.id not in busy]
    drivers = _drivers_on(db, {v.id for v in idle_vehs}, on)
    idle = [{"vehicle_id": v.id, "plate": v.plate, "vehicle": vehicle_label(v), "status": v.status, "status_name": VEHICLE_STATUSES.get(v.status, ""),
             "driver": person_name(drivers.get(v.id)) or None} for v in idle_vehs]
    return {"date": on.isoformat(), "weekday": ts.WEEKDAYS[on.weekday()],
            "routes": sorted(by_route.values(), key=lambda g: g["route"].lower()),
            "routes_without_truck": without_truck, "trucks_without_route": idle,
            "totals": {"routes": len(by_route), "trucks": len(busy), "routes_all": len(all_routes)}}

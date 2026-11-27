# -*- coding: utf-8 -*-
"""«Зарплата мусоровозов»: две таблицы на месяц.

1. По дням — каждый день, каждый мусоровоз и его экипаж: водитель (тонны его талонов весов) и грузчики, закреплённые за машиной
   в этот день (тонны всей машины за день). Ставку за тонну можно проставить прямо в строке; если её нет, берётся тариф «за тонну»
   сотрудника. За день = тонны × ставка.
2. Итог за месяц — сама собирается из первой: по каждому сотруднику дни, ходки, тонны и начислено.

Ставка водителя, проставленная на день, действует и в «Моих часах», и в «Зарплате за месяц» (см. payroll.ton_rows)."""
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, org_today
from app.db import get_db
from app.deps import Principal, entity_filter, require
from app.models import Employee, GarbageDayRate, LoaderAssignment, Vehicle, WeighTicket
from app.services import payroll as pay
from app.services.bills import to_money

router = APIRouter(tags=["Зарплата мусоровозов"])
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


def bounds(month: str | None) -> tuple[dt.date, dt.date, str]:
    if not month:
        t = org_today()
        month = f"{t.year:04d}-{t.month:02d}"
    try:
        if len(month) != 7 or month[4] != "-":
            raise ValueError
        first = dt.date(int(month[:4]), int(month[5:7]), 1)
    except ValueError:
        raise fail(422, "bad_month", "Месяц нужен в виде 2026-11", "month") from None
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return first, last, month


def fio(e: Employee | None) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x) if e else "—"


def plate(v: Vehicle | None) -> str:
    return " · ".join(x for x in (v.plate, " ".join(y for y in (v.make, v.model) if y)) if x) if v else ""


def num(x) -> float:
    return float(Decimal(x).quantize(Decimal("0.01")))


def build(db: Session, p: Principal, first: dt.date, last: dt.date, legal_entity_id: int | None) -> dict:
    sc = entity_filter(p, "worklog.view", legal_entity_id)
    tickets = list(db.scalars(select(WeighTicket).where(
        WeighTicket.ticket_date >= first, WeighTicket.ticket_date <= last, WeighTicket.archived_at.is_(None), WeighTicket.driver_id.is_not(None),
        sc.cond(WeighTicket.legal_entity_id)).order_by(WeighTicket.ticket_date, WeighTicket.id)))
    groups: dict[tuple[dt.date, int], dict] = {}
    for t in tickets:
        g = groups.setdefault((t.ticket_date, t.vehicle_id), {"ent": t.legal_entity_id, "trips": 0, "tons": Decimal(0), "pending": 0, "drivers": {}})
        d = g["drivers"].setdefault(t.driver_id, {"trips": 0, "tons": Decimal(0), "pending": 0})
        for x in (g, d):
            x["trips"] += 1
            x["tons"] += Decimal(t.tons) if t.tons is not None else 0
            x["pending"] += 1 if t.tons is None else 0
    vids = {v for _, v in groups}
    crews: dict[int, list[LoaderAssignment]] = {}
    if vids:
        for a in db.scalars(select(LoaderAssignment).where(LoaderAssignment.vehicle_id.in_(vids), LoaderAssignment.started_on <= last,
                                                           or_(LoaderAssignment.ended_on.is_(None), LoaderAssignment.ended_on >= first))):
            crews.setdefault(a.vehicle_id, []).append(a)
    own = {(r.work_date, r.vehicle_id, r.employee_id): Decimal(r.rate) for r in db.scalars(select(GarbageDayRate).where(
        GarbageDayRate.work_date >= first, GarbageDayRate.work_date <= last, GarbageDayRate.vehicle_id.in_(vids or [0])))}
    emp_ids = {e for g in groups.values() for e in g["drivers"]} | {a.employee_id for x in crews.values() for a in x}
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(emp_ids or [0])))}
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vids or [0])))}
    tariffs = {eid: pay.ton_tariff(db, e) for eid, e in emps.items()}

    def person(day, vid, eid, role, trips, tons, pending) -> dict:
        key = (day, vid, eid)
        tar = tariffs.get(eid)
        if key in own:
            rate, source = own[key], "day"
        elif tar is not None:
            rate, source = pay.day_rate(tar, tons), "tariff"
        else:
            rate, source = None, ""
        return {"employee_id": eid, "name": fio(emps.get(eid)), "role": role, "trips": trips, "tons": num(tons), "pending": pending,
                "rate": num(rate) if rate is not None else None, "rate_source": source, "tariff": pay.tariff_text(tar) if tar is not None else "",
                "sum": num(pay.money(tons * rate)) if rate is not None else 0.0}

    days = []
    month: dict[int, dict] = {}
    for (day, vid), g in sorted(groups.items(), key=lambda kv: (kv[0][0], plate(vehicles.get(kv[0][1])))):
        people = [person(day, vid, eid, "driver", d["trips"], d["tons"], d["pending"])
                  for eid, d in sorted(g["drivers"].items(), key=lambda kv: fio(emps.get(kv[0])))]
        loaders = sorted({a.employee_id for a in crews.get(vid, []) if a.started_on <= day and (a.ended_on is None or a.ended_on >= day)}
                         - set(g["drivers"]), key=lambda e: fio(emps.get(e)))
        people += [person(day, vid, eid, "loader", g["trips"], g["tons"], g["pending"]) for eid in loaders]
        for x in people:
            m = month.setdefault(x["employee_id"], {"employee_id": x["employee_id"], "name": x["name"], "roles": set(), "days": set(), "trips": 0,
                                                     "tons": 0.0, "sum": 0.0, "no_rate": 0})
            m["roles"].add(x["role"])
            m["days"].add(day)
            m["trips"] += x["trips"]
            m["tons"] += x["tons"]
            m["sum"] += x["sum"]
            m["no_rate"] += 1 if x["rate"] is None and x["tons"] else 0
        days.append({"date": day.isoformat(), "vehicle_id": vid, "vehicle": plate(vehicles.get(vid)), "legal_entity_id": g["ent"], "trips": g["trips"],
                     "tons": num(g["tons"]), "pending": g["pending"], "people": people, "sum": round(sum(x["sum"] for x in people), 2)})
    totals = sorted(({**m, "roles": ["driver"] if m["roles"] == {"driver"} else ["loader"] if m["roles"] == {"loader"} else ["driver", "loader"],
                      "days": len(m["days"]), "tons": round(m["tons"], 3), "sum": round(m["sum"], 2)} for m in month.values()),
                    key=lambda m: (0 if "driver" in m["roles"] else 1, m["name"].lower()))
    return {"days": days, "month": totals, "total": round(sum(m["sum"] for m in totals), 2), "tons": round(sum(d["tons"] for d in days), 3),
            "trips": sum(d["trips"] for d in days), "pending": sum(d["pending"] for d in days)}


@router.get("/garbage-salary")
def salary(month: str | None = None, legal_entity_id: int | None = None, p: Principal = Depends(require("worklog.view")), db: Session = Depends(get_db)):
    first, last, month = bounds(month)
    out = build(db, p, first, last, legal_entity_id)
    return {**out, "month_key": month, "title": f"{MONTHS[first.month - 1].capitalize()} {first.year}", "can_edit": p.can("worklog.edit")}


class RateIn(BaseModel):
    date: dt.date
    vehicle_id: int
    employee_id: int
    rate: str | int | float | None = None


class BulkIn(BaseModel):
    month: str = Field(min_length=7, max_length=7)
    role: str = Field(pattern="^(driver|loader|all)$")
    rate: str | int | float
    only_empty: bool = True


def put_rate(db: Session, p: Principal, day: dt.date, vid: int, eid: int, rate: Decimal | None, ent: int) -> None:
    row = db.scalar(select(GarbageDayRate).where(GarbageDayRate.work_date == day, GarbageDayRate.vehicle_id == vid, GarbageDayRate.employee_id == eid))
    if rate is None:
        if row:
            db.delete(row)
    elif row:
        row.rate, row.updated_by_id = rate, p.user.id
    else:
        db.add(GarbageDayRate(legal_entity_id=ent, work_date=day, vehicle_id=vid, employee_id=eid, rate=rate, updated_by_id=p.user.id))


@router.put("/garbage-salary/rate")
def set_rate(body: RateIn, legal_entity_id: int | None = None, p: Principal = Depends(require("worklog.edit")), db: Session = Depends(get_db)):
    """Ставка за тонну на один день одному сотруднику. Пусто — снова по тарифу сотрудника."""
    first, last, month = bounds(body.date.strftime("%Y-%m"))
    data = build(db, p, first, last, legal_entity_id)
    day = next((d for d in data["days"] if d["date"] == body.date.isoformat() and d["vehicle_id"] == body.vehicle_id), None)
    if day is None or not any(x["employee_id"] == body.employee_id for x in day["people"]):
        raise fail(404, "not_found", "Такого сотрудника в экипаже этой машины в этот день нет")
    if not p.can("worklog.edit", day["legal_entity_id"]):
        raise fail(403, "forbidden", "Нет права менять зарплату в этом юрлице")
    rate = None if body.rate in (None, "") else to_money(body.rate, what="Ставка за тонну", field="rate")
    put_rate(db, p, body.date, body.vehicle_id, body.employee_id, rate, day["legal_entity_id"])
    audit.log(db, p, "garbage.rate", "employee", body.employee_id, day["legal_entity_id"], date=body.date.isoformat(), vehicle=day["vehicle"],
              rate=str(rate) if rate is not None else "по тарифу")
    db.commit()
    return {**build(db, p, first, last, legal_entity_id), "month_key": month}


@router.post("/garbage-salary/rates")
def set_rates(body: BulkIn, legal_entity_id: int | None = None, p: Principal = Depends(require("worklog.edit")), db: Session = Depends(get_db)):
    """Одна ставка всем водителям, всем грузчикам или всем сразу за месяц. only_empty — только там, где ставки нет (ни своей, ни тарифа)."""
    first, last, month = bounds(body.month)
    rate = to_money(body.rate, what="Ставка за тонну", field="rate")
    data = build(db, p, first, last, legal_entity_id)
    n = 0
    for d in data["days"]:
        if not p.can("worklog.edit", d["legal_entity_id"]):
            continue
        for x in d["people"]:
            if body.role != "all" and x["role"] != body.role:
                continue
            if body.only_empty and x["rate"] is not None:
                continue
            put_rate(db, p, dt.date.fromisoformat(d["date"]), d["vehicle_id"], x["employee_id"], rate, d["legal_entity_id"])
            n += 1
    if n:
        audit.log(db, p, "garbage.rate", "employee", None, legal_entity_id, month=month, role=body.role, rate=str(rate), rows=n)
    db.commit()
    return {**build(db, p, first, last, legal_entity_id), "month_key": month, "changed": n}

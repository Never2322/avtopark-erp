# -*- coding: utf-8 -*-
"""Выход на работу: сотрудник (водитель) отвечает «выйду» или «не выйду» на день вперёд, директор и диспетчер видят ответы.

Ответ можно менять: прошлые значения остаются в журнале действий. Офис может отметить выход за человека (по телефону):
такой ответ помечается «отметили в офисе». Кто не ответил, в списке виден отдельно.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, current, entity_filter, require
from app.enums import (ATTENDANCE_AHEAD_DAYS, ATTENDANCE_AHEAD_OFFICE_DAYS, ATTENDANCE_BACK_DAYS, ATTENDANCE_SHOW_DAYS,
                       ATTENDANCE_SOURCES)
from app.models import Employee, EmploymentAssignment, ShiftPlan, Vehicle, VehicleAssignment
from app.registry import allowed_entities
from app.resources import job_today
from app.routers.employees import full_name, get_visible
from app.services import timesheet as ts
from app.services.vehicles import active_assignment_filter

router = APIRouter(prefix="/attendance", tags=["Выход на работу"])

STATUS_LABELS = {"yes": "Выйдет", "no": "Не выйдет", "none": "Не ответил"}


def _status(plan: ShiftPlan | None) -> str:
    return "none" if plan is None else ("yes" if plan.will_come else "no")


def _plan_out(plan: ShiftPlan | None) -> dict:
    return {"status": _status(plan), "status_label": STATUS_LABELS[_status(plan)], "reason": plan.reason if plan else "",
            "source": plan.source if plan else "", "source_label": ATTENDANCE_SOURCES.get(plan.source, "") if plan else "",
            "answered_at": plan.updated_at.isoformat() if plan else None}


def _me(db: Session, p: Principal) -> Employee:
    e = db.get(Employee, p.employee_id) if p.employee_id else None
    if e is None:
        raise fail(409, "no_employee", "К вашей учётной записи не привязан сотрудник: обратитесь к администратору")
    return e


def _date(raw: str | None, default: dt.date, low: dt.date, high: dt.date) -> dt.date:
    if not raw:
        return default
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        raise fail(422, "bad_date", "Дата указана неверно", "date") from None
    if d < low or d > high:
        raise fail(422, "bad_date", f"Можно смотреть дни с {low.strftime('%d.%m.%Y')} по {high.strftime('%d.%m.%Y')}", "date")
    return d


def _check_answer(emp: Employee, day: dt.date, will_come: bool, reason: str) -> str:
    reason = reason.strip()
    if emp.archived_at is not None:
        raise fail(409, "archived", "Сотрудник в архиве", "date")
    if emp.fired_on and day > emp.fired_on:
        raise fail(422, "validation", "Сотрудник к этому дню уже уволен", "date")
    if emp.hired_on and day < emp.hired_on:
        raise fail(422, "validation", "Сотрудник к этому дню ещё не принят", "date")
    if will_come:
        return ""
    if len(reason) < 3:
        raise fail(422, "validation", "Напишите, почему не выйдете: например «болею» или «отпросился»", "reason")
    return reason


def _set(db: Session, p: Principal, emp: Employee, day: dt.date, will_come: bool, reason: str, source: str) -> ShiftPlan:
    reason = _check_answer(emp, day, will_come, reason)
    q = select(ShiftPlan).where(ShiftPlan.employee_id == emp.id, ShiftPlan.plan_date == day)
    plan = db.scalar(q)
    before = _status(plan)
    for attempt in (1, 2):
        if plan is None:
            plan = ShiftPlan(employee_id=emp.id, plan_date=day, created_by_id=p.id)
            db.add(plan)
        plan.will_come, plan.reason, plan.source, plan.updated_by_id = will_come, reason, source, p.id
        try:
            db.flush()
            break
        except IntegrityError:                    # два ответа пришли одновременно: второй дополняет первый
            db.rollback()
            if attempt == 2:
                raise
            plan = db.scalar(q)
            before = _status(plan)
    db.refresh(plan)
    audit.log(db, p, "attendance.set", "employee", emp.id, None, name=full_name(emp), date=day.isoformat(),
              before=before, after=_status(plan), reason=reason, source=source)
    db.commit()
    return plan


# ---------------------------------------------------------------- страница сотрудника

@router.get("/mine")
def my_days(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("attendance.mark_own"):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    emp = _me(db, p)
    today = job_today()
    days = [today + dt.timedelta(days=i) for i in range(ATTENDANCE_SHOW_DAYS)]
    plans = {x.plan_date: x for x in db.scalars(select(ShiftPlan).where(ShiftPlan.employee_id == emp.id, ShiftPlan.plan_date.in_(days)))}
    return {"today": today.isoformat(), "max_date": (today + dt.timedelta(days=ATTENDANCE_AHEAD_DAYS)).isoformat(), "name": full_name(emp),
            "days": [{"date": d.isoformat(), "weekday": ts.WEEKDAYS[d.weekday()], "off": d.weekday() >= 5, "is_today": d == today, **_plan_out(plans.get(d))} for d in days]}


class AnswerIn(BaseModel):
    date: dt.date
    will_come: bool
    reason: str = Field(default="", max_length=300)


@router.post("/mine")
def answer_mine(body: AnswerIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("attendance.mark_own"):
        raise fail(403, "forbidden", "Нет права отмечать свой выход на работу")
    emp = _me(db, p)
    today = job_today()
    if body.date < today:
        raise fail(422, "validation", "Прошедший день отметить нельзя: обратитесь к диспетчеру", "date")
    if body.date > today + dt.timedelta(days=ATTENDANCE_AHEAD_DAYS):
        raise fail(422, "validation", f"Можно отвечать не дальше чем на {ATTENDANCE_AHEAD_DAYS} дней вперёд", "date")
    plan = _set(db, p, emp, body.date, body.will_come, body.reason, "driver")
    return {"date": body.date.isoformat(), **_plan_out(plan)}


# ---------------------------------------------------------------- список для офиса

def _people(db: Session, scope, day: dt.date) -> list[Employee]:
    """Кто в списке: работает в доступном юрлице в этот день и водитель (или уже ответил)."""
    works = exists().where(EmploymentAssignment.employee_id == Employee.id, scope.cond(EmploymentAssignment.legal_entity_id),
                           or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= day),
                           or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= day))
    answered = exists().where(ShiftPlan.employee_id == Employee.id, ShiftPlan.plan_date == day)
    return list(db.scalars(select(Employee).where(
        works, Employee.archived_at.is_(None), or_(Employee.hired_on.is_(None), Employee.hired_on <= day),
        or_(Employee.fired_on.is_(None), Employee.fired_on >= day), or_(Employee.is_driver.is_(True), answered))
        .order_by(Employee.last_name, Employee.first_name, Employee.id)))


@router.get("")
def day_list(date: str = "", legal_entity_id: int | None = None, p: Principal = Depends(require("attendance.view")), db: Session = Depends(get_db)):
    today = job_today()
    day = _date(date, today, today - dt.timedelta(days=ATTENDANCE_BACK_DAYS * 4), today + dt.timedelta(days=ATTENDANCE_AHEAD_OFFICE_DAYS))
    scope = entity_filter(p, "attendance.view", legal_entity_id)
    people = _people(db, scope, day)
    ids = [e.id for e in people]
    plans = {x.employee_id: x for x in db.scalars(select(ShiftPlan).where(ShiftPlan.plan_date == day, ShiftPlan.employee_id.in_(ids)))} if ids else {}
    cars: dict[int, list[str]] = {}
    if ids:
        for eid, plate in db.execute(select(VehicleAssignment.employee_id, Vehicle.plate).join(Vehicle, Vehicle.id == VehicleAssignment.vehicle_id)
                                     .where(VehicleAssignment.employee_id.in_(ids), active_assignment_filter(day)).order_by(Vehicle.plate)):
            cars.setdefault(eid, []).append(plate)
    items = [{"employee_id": e.id, "name": full_name(e), "position": e.position, "vehicles": cars.get(e.id, []), **_plan_out(plans.get(e.id))} for e in people]
    counts = {k: sum(1 for i in items if i["status"] == k) for k in STATUS_LABELS}
    counts["total"] = len(items)
    return {"date": day.isoformat(), "weekday": ts.WEEKDAYS[day.weekday()], "today": today.isoformat(), "items": items, "counts": counts,
            "entities": allowed_entities(db, p, "attendance.view"), "can_edit": p.can("attendance.edit"),
            "min_date": (today - dt.timedelta(days=ATTENDANCE_BACK_DAYS)).isoformat(),
            "max_date": (today + dt.timedelta(days=ATTENDANCE_AHEAD_OFFICE_DAYS)).isoformat()}


@router.put("/{eid}")
def set_for(eid: int, body: AnswerIn, p: Principal = Depends(require("attendance.edit")), db: Session = Depends(get_db)):
    """Офис отмечает выход за человека (позвонил, написал). Дальше чем на 60 дней вперёд и раньше чем за неделю нельзя."""
    emp = get_visible(db, p, eid, "attendance.edit")
    today = job_today()
    if body.date < today - dt.timedelta(days=ATTENDANCE_BACK_DAYS) or body.date > today + dt.timedelta(days=ATTENDANCE_AHEAD_OFFICE_DAYS):
        raise fail(422, "validation", "Этот день слишком далеко: можно от недели назад до двух месяцев вперёд", "date")
    plan = _set(db, p, emp, body.date, body.will_come, body.reason, "staff")
    return {"employee_id": emp.id, "date": body.date.isoformat(), **_plan_out(plan)}

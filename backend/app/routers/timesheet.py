# -*- coding: utf-8 -*-
"""Табель: сколько заработал каждый сотрудник в каждый день месяца. Деньги берутся из раздела «Часы работы и ремонта».

Отметок (явка, отпуск, больничный) в табеле нет: в клетке дня стоит сумма за этот день, справа итог по человеку, внизу итог за день по всем.
Табель ведётся отдельно по каждому юрлицу: в сетку попадают те, кто в этом месяце работает в выбранном юрлице (по записям «место работы»
в карточке сотрудника), и те, у кого в месяце есть часы. Чтобы видеть суммы, нужны права «Табель: смотреть» и «Часы работы и ремонта: смотреть».
"""
import datetime as dt

from fastapi import APIRouter, Depends, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail
from app.db import get_db
from app.deps import Principal, current, require
from app.models import Employee, EmploymentAssignment, LegalEntity, WorkLog
from app.registry import allowed_entities
from app.resources import job_today
from app.services import payroll as pay
from app.services import timesheet as ts

router = APIRouter(prefix="/timesheet", tags=["Табель"])


def _month(raw: str | None) -> dt.date:
    try:
        return ts.parse_month(raw, job_today())
    except ValueError as e:
        raise fail(422, "bad_month", str(e), "month") from None


def _entity(db: Session, p: Principal, code: str, legal_entity_id: int | None) -> LegalEntity:
    """Юрлицо табеля: выбранное, либо единственное из доступных. Чужое выглядит как «нет доступа»."""
    scope = p.scope(code)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    if legal_entity_id is None:
        allowed = allowed_entities(db, p, code)
        if len(allowed) != 1:
            raise fail(422, "entity_required", "Выберите юридическое лицо: табель ведётся отдельно по каждому", "legal_entity_id")
        legal_entity_id = allowed[0]["id"]
    if not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    ent = db.get(LegalEntity, legal_entity_id)
    if ent is None:
        raise fail(404, "not_found", "Юридическое лицо не найдено")
    if not p.can("worklog.view", ent.id):
        raise fail(403, "forbidden", "В табеле показаны деньги за часы работы и ремонта: нужно право смотреть часы работы и ремонта")
    return ent


def _people(db: Session, ent_id: int, first: dt.date, last: dt.date) -> list[tuple[Employee, list[EmploymentAssignment]]]:
    """Кто в сетке: работает в юрлице в этом месяце или уже внёс часы в нём."""
    has_hours = select(WorkLog.id).where(WorkLog.employee_id == Employee.id, WorkLog.legal_entity_id == ent_id, WorkLog.archived_at.is_(None),
                                         WorkLog.work_date >= first, WorkLog.work_date <= last).exists()
    works = select(EmploymentAssignment.id).where(
        EmploymentAssignment.employee_id == Employee.id, EmploymentAssignment.legal_entity_id == ent_id,
        or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= last),
        or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= first)).exists()
    emps = db.scalars(select(Employee).where(or_(and_(works, Employee.archived_at.is_(None),
                                                      or_(Employee.hired_on.is_(None), Employee.hired_on <= last),
                                                      or_(Employee.fired_on.is_(None), Employee.fired_on >= first)), has_hours))
                      .order_by(Employee.last_name, Employee.first_name, Employee.id)).all()
    ids = [e.id for e in emps]
    assigns: dict[int, list[EmploymentAssignment]] = {}
    if ids:
        for a in db.scalars(select(EmploymentAssignment).where(EmploymentAssignment.employee_id.in_(ids), EmploymentAssignment.legal_entity_id == ent_id)):
            assigns.setdefault(a.employee_id, []).append(a)
    return [(e, assigns.get(e.id, [])) for e in emps]


def _name(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


def build(db: Session, p: Principal, ent: LegalEntity, first: dt.date, code: str = "timesheet.view") -> dict:
    last = first.replace(day=ts.month_days(first))
    people = _people(db, ent.id, first, last)
    today = job_today()
    days = [{"n": d, "wd": ts.WEEKDAYS[dt.date(first.year, first.month, d).weekday()], "off": dt.date(first.year, first.month, d).weekday() >= 5,
             "today": dt.date(first.year, first.month, d) == today} for d in range(1, last.day + 1)]
    paid = pay.month_pay(db, ent.id, first, last, [e.id for e, _ in people])
    day_sums: dict[int, float] = {}
    grand = {k: 0.0 for k in ("work_h", "repair_h", "work", "repair", "total")}
    rows = []
    for e, assigns in people:
        cells = paid.get(e.id, {})
        tot = pay.totals(cells)
        rows.append({
            "employee_id": e.id, "name": _name(e), "position": next((x.position for x in assigns if x.position), "") or e.position, "archived": e.archived_at is not None,
            "days": {str(n): {k: pay.num(v) for k, v in c.items()} for n, c in sorted(cells.items())},
            "totals": {k: pay.num(v) for k, v in tot.items()}, "worked_days": len(cells),
        })
        for n, c in cells.items():
            day_sums[n] = day_sums.get(n, 0.0) + pay.num(c["total"])
        for k in grand:
            grand[k] += pay.num(tot[k])
    return {
        "month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}", "month_gen": ts.MONTHS_GEN[first.month - 1],
        "legal_entity": {"id": ent.id, "name": ent.short_name or ent.name, "full_name": ent.name},
        "entities": [x for x in allowed_entities(db, p, code) if p.can("worklog.view", x["id"])], "days": days, "rows": rows,
        "day_totals": {str(n): round(v, 2) for n, v in sorted(day_sums.items())}, "totals": {k: round(v, 2) for k, v in grand.items()},
        "can_export": p.can("timesheet.export", ent.id), "can_add": p.can("worklog.create", ent.id),
    }


@router.get("")
def sheet(month: str = "", legal_entity_id: int | None = None, p: Principal = Depends(require("timesheet.view")), db: Session = Depends(get_db)):
    ent = _entity(db, p, "timesheet.view", legal_entity_id)
    return build(db, p, ent, _month(month))


@router.get("/export.csv")
def export(month: str = "", legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ent = _entity(db, p, "timesheet.export", legal_entity_id)
    first = _month(month)
    data = build(db, p, ent, first, "timesheet.export")
    n = len(data["days"])
    fmt = lambda x: f"{x:.2f}".replace(".", ",")
    h = lambda x: ts.hours_text(x)
    rows: list[list] = [["№", "ФИО", "Должность"] + list(range(1, n + 1)) + ["Часов работы", "Часов ремонта", "Работа, ₽", "Ремонт, ₽", "Итого, ₽"]]
    for i, r in enumerate(data["rows"], 1):
        t = r["totals"]
        rows.append([i, r["name"], r["position"]] + [fmt(r["days"][str(d)]["total"]) if r["days"].get(str(d)) else "" for d in range(1, n + 1)]
                    + [h(t["work_h"]), h(t["repair_h"]), fmt(t["work"]), fmt(t["repair"]), fmt(t["total"])])
    t = data["totals"]
    rows.append(["", "Итого за день", ""] + [fmt(data["day_totals"][str(d)]) if data["day_totals"].get(str(d)) else "" for d in range(1, n + 1)]
                + [h(t["work_h"]), h(t["repair_h"]), fmt(t["work"]), fmt(t["repair"]), fmt(t["total"])])
    audit.log(db, p, "timesheet.export", "timesheet", None, ent.id, month=data["month"], rows=len(data["rows"]))
    db.commit()
    name = f"timesheet-{data['month']}.csv"
    return Response(csv_text(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

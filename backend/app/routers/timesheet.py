# -*- coding: utf-8 -*-
"""Табель учёта рабочего времени: сетка месяца по юрлицу, отметки кодами, быстрое заполнение, выгрузка.

Табель ведётся отдельно по каждому юрлицу: в сетку попадают те, кто в этом месяце работает в выбранном юрлице
(по записям «место работы» в карточке сотрудника), и те, у кого в месяце уже есть отметки. Пустая клетка — нет записи.
Что изменилось, пишется в журнал действий: кто, какая клетка, что было и что стало.
"""
import datetime as dt

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail
from app.db import get_db
from app.deps import Principal, current, require
from app.enums import TIMESHEET_CODES, TIMESHEET_FILL_MODES
from app.models import Employee, EmploymentAssignment, LegalEntity, TimesheetMark, Waybill, WorkLog
from app.registry import allowed_entities
from app.resources import job_today
from app.services import payroll as pay
from app.services import timesheet as ts

router = APIRouter(prefix="/timesheet", tags=["Табель"])
MAX_CELLS = 2000                 # за один запрос: месяц на 60 человек с запасом


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
    return ent


def _people(db: Session, ent_id: int, first: dt.date, last: dt.date) -> list[tuple[Employee, list[EmploymentAssignment]]]:
    """Кто в сетке: работает в юрлице в этом месяце или уже отмечен в нём."""
    has_marks = or_(select(TimesheetMark.id).where(TimesheetMark.employee_id == Employee.id, TimesheetMark.legal_entity_id == ent_id,
                                                   TimesheetMark.work_date >= first, TimesheetMark.work_date <= last).exists(),
                    select(WorkLog.id).where(WorkLog.employee_id == Employee.id, WorkLog.legal_entity_id == ent_id, WorkLog.archived_at.is_(None),
                                             WorkLog.work_date >= first, WorkLog.work_date <= last).exists())
    works = select(EmploymentAssignment.id).where(
        EmploymentAssignment.employee_id == Employee.id, EmploymentAssignment.legal_entity_id == ent_id,
        or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= last),
        or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= first)).exists()
    emps = db.scalars(select(Employee).where(or_(and_(works, Employee.archived_at.is_(None),
                                                      or_(Employee.hired_on.is_(None), Employee.hired_on <= last),
                                                      or_(Employee.fired_on.is_(None), Employee.fired_on >= first)), has_marks))
                      .order_by(Employee.last_name, Employee.first_name, Employee.id)).all()
    ids = [e.id for e in emps]
    assigns: dict[int, list[EmploymentAssignment]] = {}
    if ids:
        for a in db.scalars(select(EmploymentAssignment).where(EmploymentAssignment.employee_id.in_(ids), EmploymentAssignment.legal_entity_id == ent_id)):
            assigns.setdefault(a.employee_id, []).append(a)
    return [(e, assigns.get(e.id, [])) for e in emps]


def _works_on(e: Employee, assigns: list[EmploymentAssignment], day: dt.date) -> bool:
    if e.hired_on and day < e.hired_on or e.fired_on and day > e.fired_on:
        return False
    return any((a.started_on is None or a.started_on <= day) and (a.ended_on is None or day <= a.ended_on) for a in assigns)


def _name(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


def _marks(db: Session, ent_id: int, first: dt.date, last: dt.date, ids: list[int]) -> dict[int, dict[int, TimesheetMark]]:
    out: dict[int, dict[int, TimesheetMark]] = {}
    if ids:
        for m in db.scalars(select(TimesheetMark).where(TimesheetMark.legal_entity_id == ent_id, TimesheetMark.employee_id.in_(ids),
                                                        TimesheetMark.work_date >= first, TimesheetMark.work_date <= last)):
            out.setdefault(m.employee_id, {})[m.work_date.day] = m
    return out


def build(db: Session, p: Principal, ent: LegalEntity, first: dt.date, code: str = "timesheet.view") -> dict:
    last = first.replace(day=ts.month_days(first))
    people = _people(db, ent.id, first, last)
    marks = _marks(db, ent.id, first, last, [e.id for e, _ in people])
    today = job_today()
    days = [{"n": d, "wd": ts.WEEKDAYS[dt.date(first.year, first.month, d).weekday()], "off": dt.date(first.year, first.month, d).weekday() >= 5,
             "today": dt.date(first.year, first.month, d) == today} for d in range(1, last.day + 1)]
    rows = []
    pay_on = p.can("worklog.view", ent.id)
    paid = pay.month_pay(db, ent.id, first, last, [e.id for e, _ in people]) if pay_on else {}
    day_sums: dict[int, float] = {}
    grand = {k: 0.0 for k in ("work_h", "repair_h", "work", "repair", "total")}
    for e, assigns in people:
        mine = marks.get(e.id, {})
        d, h, a = ts.sums((m.code, m.hours) for m in mine.values())
        row = {
            "employee_id": e.id, "name": _name(e), "position": next((x.position for x in assigns if x.position), "") or e.position,
            "archived": e.archived_at is not None,
            "marks": {str(n): {"code": m.code, "hours": float(m.hours) if m.hours is not None else None, "text": ts.cell_text(m.code, m.hours)} for n, m in sorted(mine.items())},
            "days": d, "hours": h, "absent": a,
        }
        if pay_on:
            cells = paid.get(e.id, {})
            tot = pay.totals(cells)
            row["pay"] = {"days": {str(n): {k: pay.num(v) for k, v in c.items()} for n, c in sorted(cells.items())}, "totals": {k: pay.num(v) for k, v in tot.items()}}
            for n, c in cells.items():
                day_sums[n] = day_sums.get(n, 0.0) + pay.num(c["total"])
            for k in grand:
                grand[k] += pay.num(tot[k])
        rows.append(row)
    return {
        "month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}", "month_gen": ts.MONTHS_GEN[first.month - 1],
        "legal_entity": {"id": ent.id, "name": ent.short_name or ent.name, "full_name": ent.name},
        "entities": allowed_entities(db, p, code), "days": days, "rows": rows,
        "codes": [{"code": c, "label": t, "kind": ts.code_kind(c), "hours": c in ts.HOURS_CODES} for c, t in TIMESHEET_CODES.items()],
        "default_hours": 8, "fill_modes": [{"value": k, "label": v} for k, v in TIMESHEET_FILL_MODES.items()],
        "can_edit": p.can("timesheet.edit", ent.id), "can_export": p.can("timesheet.export", ent.id),
        # заработок: суммы за день и за месяц по часам работы и ремонта; видят те, у кого есть право на часы
        "pay": {"enabled": pay_on, "day_totals": {str(n): round(v, 2) for n, v in sorted(day_sums.items())}, "totals": {k: round(v, 2) for k, v in grand.items()},
                "can_add": p.can("worklog.create", ent.id)} if pay_on else {"enabled": False},
    }


@router.get("")
def sheet(month: str = "", legal_entity_id: int | None = None, p: Principal = Depends(require("timesheet.view")), db: Session = Depends(get_db)):
    ent = _entity(db, p, "timesheet.view", legal_entity_id)
    return build(db, p, ent, _month(month))


class CellIn(BaseModel):
    employee_id: int
    day: int = Field(ge=1, le=31)
    text: str = Field(default="", max_length=20)


class CellsIn(BaseModel):
    legal_entity_id: int
    month: str
    cells: list[CellIn] = Field(max_length=MAX_CELLS)


def _raise(fields: dict[str, str]):
    from fastapi import HTTPException
    return HTTPException(status_code=422, detail={"code": "validation", "message": "Проверьте отмеченные клетки", "field": None, "fields": fields})


@router.put("/cells")
def save_cells(body: CellsIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Сохраняет изменённые клетки (присылаются только изменённые). Пустой текст стирает отметку. Всё или ничего."""
    ent = _entity(db, p, "timesheet.edit", body.legal_entity_id)
    first = _month(body.month)
    last = first.replace(day=ts.month_days(first))
    people = {e.id: e for e, _ in _people(db, ent.id, first, last)}
    errors: dict[str, str] = {}
    parsed: dict[tuple[int, int], tuple[str | None, object]] = {}
    for c in body.cells:
        key = f"c_{c.employee_id}_{c.day}"
        e = people.get(c.employee_id)
        if e is None:
            errors[key] = "Этого сотрудника нет в табеле за этот месяц"
            continue
        if c.day > last.day:
            errors[key] = f"{_name(e)}: в этом месяце {last.day} дн."
            continue
        try:
            parsed[(c.employee_id, c.day)] = ts.parse_cell(c.text)
        except ts.CellError as ex:
            errors[key] = f"{_name(e)}, {c.day} число: {ex}"
    if errors:
        raise _raise(errors)
    existing = _marks(db, ent.id, first, last, list({k[0] for k in parsed}))
    changes, changed = [], 0
    for (eid, day), (code, hours) in parsed.items():
        cur = existing.get(eid, {}).get(day)
        before = ts.cell_text(cur.code, cur.hours) if cur else ""
        after = ts.cell_text(code, hours)
        if before == after:
            continue
        changed += 1
        if len(changes) < 200:
            changes.append([eid, day, before, after])
        if code is None:
            db.delete(cur)
        elif cur is None:
            db.add(TimesheetMark(legal_entity_id=ent.id, employee_id=eid, work_date=dt.date(first.year, first.month, day), code=code, hours=hours,
                                 created_by_id=p.id, updated_by_id=p.id))
        else:
            cur.code, cur.hours, cur.updated_by_id = code, hours, p.id
    if changed:
        audit.log(db, p, "timesheet.save", "timesheet", None, ent.id, month=first.strftime("%Y-%m"), changed=changed, changes=changes)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise fail(409, "conflict", "Табель только что изменил другой человек: обновите страницу и повторите") from None
    return {"changed": changed, **build(db, p, ent, first)}


class FillIn(BaseModel):
    legal_entity_id: int
    month: str
    mode: str


@router.post("/fill")
def fill(body: FillIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Проставляет пустые дни. Уже заполненные не меняются; исключение: «выходной» в день, когда был путевой лист."""
    ent = _entity(db, p, "timesheet.edit", body.legal_entity_id)
    if body.mode not in TIMESHEET_FILL_MODES:
        raise fail(422, "bad_mode", "Неизвестный способ заполнения", "mode")
    first = _month(body.month)
    last = first.replace(day=ts.month_days(first))
    today = job_today()
    people = _people(db, ent.id, first, last)
    by_id = {e.id: (e, a) for e, a in people}
    marks = _marks(db, ent.id, first, last, list(by_id))
    wanted: dict[tuple[int, int], str] = {}
    if body.mode in ("weekends", "weekdays"):
        for d in range(1, last.day + 1):
            day = dt.date(first.year, first.month, d)
            if body.mode == "weekends" and day.weekday() >= 5:
                code = "В"
            elif body.mode == "weekdays" and day.weekday() < 5 and day <= today:
                code = "Я"
            else:
                continue
            for eid, (e, assigns) in by_id.items():
                if _works_on(e, assigns, day):
                    wanted[(eid, d)] = code
    else:
        for eid, wd in db.execute(select(Waybill.driver_id, Waybill.work_date).where(
                Waybill.legal_entity_id == ent.id, Waybill.status.in_(("open", "closed")), Waybill.driver_id.in_(list(by_id) or [-1]),
                Waybill.work_date >= first, Waybill.work_date <= last)).all():
            wanted[(eid, wd.day)] = "РВ" if wd.weekday() >= 5 else "Я"
    added = 0
    for (eid, d), code in wanted.items():
        cur = marks.get(eid, {}).get(d)
        if cur is None:
            db.add(TimesheetMark(legal_entity_id=ent.id, employee_id=eid, work_date=dt.date(first.year, first.month, d), code=code,
                                 created_by_id=p.id, updated_by_id=p.id))
            added += 1
        elif body.mode == "waybills" and cur.code == "В":
            cur.code, cur.hours, cur.updated_by_id = code, None, p.id
            added += 1
    if added:
        audit.log(db, p, "timesheet.fill", "timesheet", None, ent.id, month=first.strftime("%Y-%m"), mode=body.mode, added=added)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise fail(409, "conflict", "Табель только что изменил другой человек: обновите страницу и повторите") from None
    return {"added": added, **build(db, p, ent, first)}


@router.get("/export.csv")
def export(month: str = "", legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ent = _entity(db, p, "timesheet.export", legal_entity_id)
    first = _month(month)
    data = build(db, p, ent, first, "timesheet.export")
    n = len(data["days"])
    pay_on = data["pay"]["enabled"]
    head = ["№", "ФИО", "Должность"] + list(range(1, n + 1)) + ["Дней", "Часов", "Неявок, дней"]
    if pay_on:
        head += ["Работа, ч", "Ремонт, ч", "Работа, ₽", "Ремонт, ₽", "Итого, ₽"]
    rows: list[list] = [head]
    for i, r in enumerate(data["rows"], 1):
        line = ([i, r["name"], r["position"]] + [r["marks"].get(str(d), {}).get("text", "") for d in range(1, n + 1)]
                + [r["days"], ts.hours_text(r["hours"]), r["absent"]])
        if pay_on:
            t = r["pay"]["totals"]
            fmt = lambda x: f"{x:.2f}".replace(".", ",")
            line += [fmt(t["work_h"]), fmt(t["repair_h"]), fmt(t["work"]), fmt(t["repair"]), fmt(t["total"])]
        rows.append(line)
    if pay_on:
        t = data["pay"]["totals"]
        fmt = lambda x: f"{x:.2f}".replace(".", ",")
        rows.append(["", "Итого", ""] + [fmt(data["pay"]["day_totals"].get(str(d), 0)) if data["pay"]["day_totals"].get(str(d)) else "" for d in range(1, n + 1)]
                    + ["", "", ""] + [fmt(t["work_h"]), fmt(t["repair_h"]), fmt(t["work"]), fmt(t["repair"]), fmt(t["total"])])
    audit.log(db, p, "timesheet.export", "timesheet", None, ent.id, month=data["month"], rows=len(data["rows"]))
    db.commit()
    name = f"timesheet-{data['month']}.csv"
    return Response(csv_text(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

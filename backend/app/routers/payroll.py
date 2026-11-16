# -*- coding: utf-8 -*-
"""Часы работы и ремонта, заработок водителя: своя страница водителя и назначение тарифов сотруднику.

Список для сотрудников офиса (поиск, правка, отмена, выгрузка) делает общий раздел «worklog» (resources.py).
Здесь то, что общему разделу не по силам: водитель сам отмечает свои часы (без подтверждения, правит и отменяет офис),
видит свой месяц с суммами, а сотруднику назначаются тарифы за работу и за ремонт.
Ставка запоминается при внесении (services/payroll.py): задним числом тариф записи не меняется.
"""
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.db import get_db
from app.deps import Principal, current
from app.enums import PAY_TARIFF_KINDS, WORKLOG_DRIVER_DELETE_HOURS, WORKLOG_KINDS, WORKLOG_BACKDATE_DAYS
from app.models import AuditLog, Employee, Tariff, Vehicle, WorkLog
from app.registry import RESOURCES, _vehicle_label, save
from app.resources import job_today
from app.routers.employees import full_name, get_visible
from app.routers.entries import mine
from app.services import notify
from app.services import payroll as pay
from app.services import timesheet as ts

router = APIRouter(prefix="/payroll", tags=["Заработок"])


def _month(raw: str | None) -> dt.date:
    try:
        return ts.parse_month(raw, job_today())
    except ValueError as e:
        raise fail(422, "bad_month", str(e), "month") from None


def _me(db: Session, p: Principal) -> Employee:
    e = db.get(Employee, p.employee_id) if p.employee_id else None
    if e is None:
        raise fail(409, "no_employee", "К вашей учётной записи не привязан сотрудник: обратитесь к администратору")
    return e


def _own_fresh(p: Principal, l: WorkLog) -> bool:
    return (l.created_by_id == p.id and l.source == "driver" and l.archived_at is None
            and utcnow() - l.created_at <= dt.timedelta(hours=WORKLOG_DRIVER_DELETE_HOURS))


def _item(db: Session, p: Principal, l: WorkLog, amt: dict[int, Decimal], plates: dict[int, str], office_changed: frozenset | set = frozenset()) -> dict:
    return {"id": l.id, "work_date": l.work_date.isoformat(), "kind": l.kind, "kind_label": WORKLOG_KINDS.get(l.kind, l.kind), "hours": float(l.hours),
            "vehicle": plates.get(l.vehicle_id, ""), "description": l.description, "source": l.source, "rate_text": pay.rate_text(l),
            "amount": pay.num(amt.get(l.id, 0)), "no_rate": l.tariff_id is None, "can_delete": _own_fresh(p, l), "office_changed": l.id in office_changed}


def _tariff_card(db: Session, emp: Employee, kind: str) -> dict | None:
    t = pay.pay_tariff(db, emp, kind)
    return {"id": t.id, "name": t.name, "unit": t.unit, "rate": pay.num(t.rate)} if t else None


# ---------------------------------------------------------------- страница водителя

@router.get("/mine")
def my_time(month: str = "", p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not (p.can("worklog.view_own") or p.can("worklog.create_own")):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    first = _month(month)
    last = first.replace(day=ts.month_days(first))
    emp = _me(db, p)
    logs = list(db.scalars(select(WorkLog).where(WorkLog.employee_id == emp.id, WorkLog.work_date >= first, WorkLog.work_date <= last,
                                                 WorkLog.archived_at.is_(None)).order_by(WorkLog.work_date.desc(), WorkLog.id.desc())))
    amt = pay.amounts(db, logs)
    vids = {l.vehicle_id for l in logs if l.vehicle_id} | set(mine(db, p))
    plates = {v.id: _vehicle_label(v) for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vids)))} if vids else {}
    tot = {"work_h": Decimal(0), "repair_h": Decimal(0), "work": Decimal(0), "repair": Decimal(0)}
    for l in logs:
        tot[f"{l.kind}_h"] += Decimal(l.hours)
        tot[l.kind] += amt[l.id]
    today = job_today()
    changed = set(db.scalars(select(AuditLog.entity_id).where(AuditLog.action == "worklog.day_kind", AuditLog.entity_type == "worklog",
                                                              AuditLog.entity_id.in_([l.id for l in logs])))) if logs else set()
    return {
        "month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}", "today": today.isoformat(),
        "min_date": (today - dt.timedelta(days=WORKLOG_BACKDATE_DAYS)).isoformat(), "delete_hours": WORKLOG_DRIVER_DELETE_HOURS,
        "items": [_item(db, p, l, amt, plates, changed) for l in logs],
        "totals": {**{k: pay.num(v) for k, v in tot.items()}, "total": pay.num(tot["work"] + tot["repair"])},
        "tariffs": {"work": _tariff_card(db, emp, "work"), "repair": _tariff_card(db, emp, "repair")},
        "vehicles": [{"id": v, "label": plates.get(v, "")} for v in sorted(mine(db, p), key=lambda i: plates.get(i, ""))],
        "can_create": p.can("worklog.create_own"),
    }


class MineIn(BaseModel):
    work_date: dt.date
    kind: str
    hours: str = Field(max_length=12)
    vehicle_id: int | None = None
    description: str = Field(default="", max_length=500)
    client_id: str = Field(default="", max_length=64)


@router.post("/mine", status_code=201)
def add_mine(body: MineIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Водитель отмечает свои часы. Без подтверждения: считается сразу, правит и отменяет диспетчер."""
    if not p.can("worklog.create_own"):
        raise fail(403, "forbidden", "Нет права отмечать свои часы")
    emp = _me(db, p)
    cid = body.client_id.strip()
    if cid:
        dup = db.scalar(select(WorkLog).where(WorkLog.created_by_id == p.id, WorkLog.client_id == cid))
        if dup is not None:                                   # та же отправка пришла второй раз
            amt = pay.amounts(db, [dup])
            return {"item": _item(db, p, dup, amt, {}), "warnings": [], "duplicate": True}
    if body.vehicle_id and body.vehicle_id not in mine(db, p):
        raise fail(422, "validation", "Выберите свою технику из списка или оставьте поле пустым", "vehicle_id")
    entity_id, why = pay.entity_for(db, emp, body.work_date, body.vehicle_id)
    if entity_id is None:
        raise fail(422, "validation", why, "vehicle_id")
    data = {"work_date": body.work_date.isoformat(), "kind": body.kind, "hours": body.hours, "vehicle_id": body.vehicle_id,
            "description": body.description, "legal_entity_id": entity_id, "employee_id": emp.id}
    obj, _w = save(db, RESOURCES["worklog"], p, data, None, code="worklog.create_own",
                   extra={"employee_id": emp.id, "source": "driver", "client_id": cid})
    warnings = []
    if obj.tariff_id is None:
        warnings.append("Тариф для вас пока не назначен: часы записаны, сумма появится, когда диспетчер назначит тариф")
    plates = {obj.vehicle_id: _vehicle_label(db.get(Vehicle, obj.vehicle_id))} if obj.vehicle_id else {}
    return {"item": _item(db, p, obj, pay.amounts(db, [obj]), plates), "warnings": warnings, "duplicate": False}


@router.post("/mine/{lid}/delete")
def delete_mine(lid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    l = db.get(WorkLog, lid)
    if l is None or l.employee_id != p.employee_id or not p.can("worklog.create_own", l.legal_entity_id):
        raise fail(404, "not_found", "Запись не найдена")
    if not _own_fresh(p, l):
        raise fail(403, "forbidden", f"Убрать можно только свою запись и только в первые {WORKLOG_DRIVER_DELETE_HOURS} ч: обратитесь к диспетчеру")
    l.archived_at, l.archived_by_id, l.archive_reason, l.updated_by_id = utcnow(), p.id, "Убрал водитель", p.id
    audit.log(db, p, "worklog.driver_void", "worklog", l.id, l.legal_entity_id)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- смена типа занятости за день

class DayKindIn(BaseModel):
    employee_id: int
    work_date: dt.date
    kind: str
    description: str = Field("", max_length=500)
    dry_run: bool = False


@router.post("/day-kind")
def day_kind(body: DayKindIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Сотрудник офиса вручную переводит все часы человека за день в «работу» или «ремонт». Ставка берётся из тарифа нового типа
    (как при внесении), сумма пересчитывается. С dry_run только показывает, что изменится, и ничего не пишет."""
    sc = p.scope("worklog.edit")
    if not sc:
        raise fail(403, "forbidden", "Нет права менять часы работы и ремонта")
    if body.kind not in WORKLOG_KINDS:
        raise fail(422, "validation", "Выберите: работа или ремонт", "kind")
    emp = db.get(Employee, body.employee_id)
    logs = list(db.scalars(select(WorkLog).where(WorkLog.employee_id == body.employee_id, WorkLog.work_date == body.work_date,
                                                 WorkLog.archived_at.is_(None)).order_by(WorkLog.id).with_for_update())) if emp is not None else []
    if not logs:
        raise fail(404, "not_found", "За этот день у сотрудника нет часов работы и ремонта")
    if any(not sc.allows(l.legal_entity_id) for l in logs):
        raise fail(403, "forbidden", "Часть записей относится к юрлицу, к которому у вас нет доступа")
    move = [l for l in logs if l.kind != body.kind]
    before = sum(pay.amounts(db, logs).values(), Decimal(0))
    note = (body.description or "").strip()
    need_note = body.kind == "repair" and any(len((l.description or "").strip()) < 3 for l in move)
    if need_note and not body.dry_run and len(note) < 3:
        raise fail(422, "validation", "Напишите, что делали на ремонте", "description")
    snap = pay.snapshot(db, emp, body.kind)
    changes = []
    for l in move:
        changes.append((l, l.kind, l.rate, l.unit))
        l.kind = body.kind
        for k, v in snap.items():
            setattr(l, k, v)
        if body.kind == "repair" and len((l.description or "").strip()) < 3:
            l.description = note
        l.updated_by_id = p.id
    after = sum(pay.amounts(db, logs).values(), Decimal(0))
    out = {"changed": len(move), "hours": pay.num(sum((Decimal(l.hours) for l in move), Decimal(0))), "before": pay.num(before), "after": pay.num(after),
           "no_rate": snap["tariff_id"] is None, "need_description": need_note, "rate_text": pay.rate_text(move[0]) if move else ""}
    if body.dry_run or not move:
        db.rollback()
        return out
    for l, old_kind, old_rate, old_unit in changes:
        audit.log(db, p, "worklog.day_kind", "worklog", l.id, l.legal_entity_id, kind_from=old_kind, kind_to=l.kind, rate_from=str(old_rate),
                  rate_to=str(l.rate), unit_from=old_unit, unit_to=l.unit, work_date=l.work_date.isoformat(), employee_id=l.employee_id)
    notify.on_worklog_kind(db, emp, body.work_date, body.kind, before, after, logs[0].legal_entity_id, p.id)
    db.commit()
    return out


# ---------------------------------------------------------------- тарифы сотрудника

def _can_assign(p: Principal) -> bool:
    return p.can("employees.edit")


@router.get("/tariffs")
def tariff_options(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not _can_assign(p):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    out = {}
    for kind in PAY_TARIFF_KINDS:
        rows = db.scalars(select(Tariff).where(Tariff.kind == kind, Tariff.archived_at.is_(None)).order_by(Tariff.name))
        out[kind] = [{"id": t.id, "name": t.name, "unit": t.unit, "rate": pay.num(t.rate)} for t in rows]
    return out


class TariffsIn(BaseModel):
    work_tariff_id: int | None = None
    repair_tariff_id: int | None = None


@router.put("/employees/{eid}/tariffs")
def set_tariffs(eid: int, body: TariffsIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Какие тарифы действуют для сотрудника. Записи часов без тарифа получают ставку сразу; записи с тарифом не меняются."""
    if not _can_assign(p):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    e = get_visible(db, p, eid, "employees.edit")
    for field, kind in (("work_tariff_id", "work"), ("repair_tariff_id", "repair")):
        tid = getattr(body, field)
        if tid is None:
            continue
        t = db.get(Tariff, tid)
        if t is None or t.archived_at is not None or t.kind != kind:
            raise fail(422, "validation", "Выберите тариф из списка: " + ("«Оплата работы»" if kind == "work" else "«Оплата ремонта»"), field)
    changes = {}
    for field in ("work_tariff_id", "repair_tariff_id"):
        new = getattr(body, field)
        if getattr(e, field) != new:
            changes[field] = [getattr(e, field), new]
            setattr(e, field, new)
    e.updated_by_id = p.id
    db.flush()
    recalculated = pay.recalc_without_tariff(db, e, p.id) if changes else 0
    audit.log(db, p, "employee.pay_tariffs", "employee", e.id, None, name=full_name(e), changes=changes, recalculated=recalculated)
    db.commit()
    return {"work_tariff_id": e.work_tariff_id, "repair_tariff_id": e.repair_tariff_id, "recalculated": recalculated}

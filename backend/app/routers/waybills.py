# -*- coding: utf-8 -*-
"""Путевые листы: черновик → открыт → закрыт (или аннулирован). Закрытый лист меняется только через исправление с причиной."""
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, require
from app.enums import READING_KINDS, VEHICLE_STATUSES, WAYBILL_SHIFTS, WAYBILL_STATUSES
from app.models import Employee, User, Vehicle, VehicleReading, Waybill, WaybillCorrection
from app.services import access
from app.services.numbering import next_number
from app.services.readings import add_reading, correct_reading, effective
from app.services.vehicles import assigned_vehicle_ids, employee_in_entity

router = APIRouter(prefix="/waybills", tags=["Путевые листы"])
BLOCKED_FOR_WORK = ("in_repair", "awaiting_repair", "written_off")
READING = dict(ge=0, le=99_999_999, max_digits=12, decimal_places=1)
FUEL = dict(ge=0, le=9999, max_digits=8, decimal_places=1)


class WaybillCreate(BaseModel):
    vehicle_id: int
    driver_id: int | None = None            # не указан — берётся карточка самого пользователя
    work_date: dt.date | None = None
    shift: str = Field(default="day", pattern="^(day|night|full)$")
    route: str = Field(default="", max_length=300)
    work_site: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class WaybillUpdate(BaseModel):
    vehicle_id: int | None = None
    driver_id: int | None = None
    work_date: dt.date | None = None
    shift: str | None = Field(default=None, pattern="^(day|night|full)$")
    route: str | None = Field(default=None, max_length=300)
    work_site: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    fuel_start_l: Decimal | None = Field(default=None, **FUEL)
    fuel_added_l: Decimal | None = Field(default=None, **FUEL)


class OpenIn(BaseModel):
    start_km: Decimal | None = Field(default=None, **READING)
    start_hours: Decimal | None = Field(default=None, **READING)
    fuel_start_l: Decimal | None = Field(default=None, **FUEL)


class CloseIn(BaseModel):
    end_km: Decimal | None = Field(default=None, **READING)
    end_hours: Decimal | None = Field(default=None, **READING)
    fuel_added_l: Decimal | None = Field(default=None, **FUEL)
    fuel_end_l: Decimal | None = Field(default=None, **FUEL)
    note: str | None = Field(default=None, max_length=2000)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class CorrectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    end_km: Decimal | None = Field(default=None, **READING)
    end_hours: Decimal | None = Field(default=None, **READING)
    fuel_start_l: Decimal | None = Field(default=None, **FUEL)
    fuel_added_l: Decimal | None = Field(default=None, **FUEL)
    fuel_end_l: Decimal | None = Field(default=None, **FUEL)
    route: str | None = Field(default=None, max_length=300)
    work_site: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- права

def mine(p: Principal, w: Waybill) -> bool:
    return p.employee_id is not None and w.driver_id == p.employee_id


def can_run(p: Principal, w: Waybill) -> bool:
    """Открывать и закрывать лист: любой (диспетчер) или только свой (водитель)."""
    return p.can_either("waybills.close", "waybills.close_own", w.legal_entity_id, mine(p, w))


def can_edit(p: Principal, w: Waybill) -> bool:
    return can_run(p, w) or p.can_either("waybills.create", "waybills.create_own", w.legal_entity_id, mine(p, w))


def visible_condition(p: Principal):
    full, own = p.scope("waybills.view"), p.scope("waybills.view_own")
    if not full and not own:
        raise fail(403, "forbidden", "Нет доступа к путевым листам")
    parts = []
    if full:
        parts.append(full.cond(Waybill.legal_entity_id))
    if own and p.employee_id:
        parts.append(and_(own.cond(Waybill.legal_entity_id), Waybill.driver_id == p.employee_id))
    return or_(*parts) if parts else false()


def get_waybill(db: Session, p: Principal, wid: int) -> Waybill:
    w = db.get(Waybill, wid)
    if not w or not (p.can("waybills.view", w.legal_entity_id) or
                     (p.can("waybills.view_own", w.legal_entity_id) and mine(p, w))):
        raise fail(404, "not_found", "Путевой лист не найден")
    return w


def driver_name(e: Employee | None) -> str | None:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x) if e else None


def number_label(w: Waybill) -> str:
    return f"{w.year}-{w.number:04d}"


def shown(v) -> str | None:
    """Значение для записи об исправлении: числа с одним знаком после запятой, как в базе."""
    return None if v is None else (f"{v:.1f}" if isinstance(v, Decimal) else str(v))


def num(v: Decimal | None):
    return float(v) if v is not None else None


def out(db: Session, p: Principal, w: Waybill, detailed: bool = False) -> dict:
    v, d = w.vehicle, w.driver
    run_km = (w.end_km - w.start_km) if w.end_km is not None and w.start_km is not None else None
    run_h = (w.end_hours - w.start_hours) if w.end_hours is not None and w.start_hours is not None else None
    data = {
        "id": w.id, "number": w.number, "year": w.year, "number_label": number_label(w), "legal_entity_id": w.legal_entity_id,
        "status": w.status, "status_label": WAYBILL_STATUSES[w.status],
        "vehicle": {"id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(), "status": v.status},
        "driver": {"id": d.id, "name": driver_name(d)},
        "work_date": w.work_date.isoformat(), "shift": w.shift, "shift_label": WAYBILL_SHIFTS[w.shift],
        "route": w.route, "work_site": w.work_site,
        "start_km": num(w.start_km), "end_km": num(w.end_km), "run_km": num(run_km),
        "start_hours": num(w.start_hours), "end_hours": num(w.end_hours), "run_hours": num(run_h),
        "opened_at": w.opened_at.isoformat() if w.opened_at else None,
        "closed_at": w.closed_at.isoformat() if w.closed_at else None,
    }
    if detailed:
        closer = db.get(User, w.closed_by_id) if w.closed_by_id else None
        run, edit = can_run(p, w), can_edit(p, w)
        data.update({
            "fuel_start_l": num(w.fuel_start_l), "fuel_added_l": num(w.fuel_added_l), "fuel_end_l": num(w.fuel_end_l),
            "note": w.note, "cancel_reason": w.cancel_reason,
            "closed_by": (closer.display_name or closer.login) if closer else None,
            "vehicle_current": {"km": num(v.current_km), "hours": num(v.current_hours)},
            "actions": {
                "edit": edit and w.status in ("draft", "open"),
                "open": run and w.status == "draft",
                "close": run and w.status == "open",
                "cancel": p.can("waybills.cancel", w.legal_entity_id) and w.status in ("draft", "open"),
                "correct": p.can("waybills.correct", w.legal_entity_id) and w.status == "closed",
            },
        })
        rows = db.execute(select(WaybillCorrection, User.display_name, User.login).outerjoin(User, User.id == WaybillCorrection.author_id)
                          .where(WaybillCorrection.waybill_id == w.id).order_by(WaybillCorrection.id)).all()
        data["corrections"] = [{"id": c.id, "reason": c.reason, "before": c.before, "after": c.after,
                                "created_at": c.created_at.isoformat(), "author": n or l} for c, n, l in rows]
    return data


# ---------------------------------------------------------------- проверки

def check_pair(db: Session, p: Principal, vehicle_id: int, driver_id: int, work_date: dt.date, entity_id: int | None = None):
    """Машина и водитель из одного юрлица; водитель действительно водитель; своё — только своей закреплённой машиной."""
    v = db.get(Vehicle, vehicle_id)
    if not v or not access.can_pick(p, db, v):
        raise fail(422, "bad_vehicle", "Машина не найдена или недоступна вам", "vehicle_id")
    if entity_id is not None and v.legal_entity_id != entity_id:
        raise fail(422, "entity_mismatch", "Нельзя перенести лист в другое юрлицо: создайте новый", "vehicle_id")
    if v.archived_at or v.status == "written_off":
        raise fail(409, "vehicle_archived", "Машина в архиве или списана", "vehicle_id")
    d = db.get(Employee, driver_id)
    if not d or d.archived_at:
        raise fail(422, "bad_driver", "Водитель не найден", "driver_id")
    if not d.is_driver:
        raise fail(422, "not_a_driver", "У этого сотрудника в карточке не отмечено «водитель»", "driver_id")
    if not employee_in_entity(db, d, v.legal_entity_id, work_date):
        raise fail(422, "driver_other_entity", "Водитель не работает в юрлице этой машины на выбранную дату", "driver_id")
    own_only = not p.can("waybills.create", v.legal_entity_id)
    if own_only:
        if p.employee_id != d.id:
            raise fail(403, "forbidden", "Вы можете оформлять путевые листы только на себя")
        if v.id not in assigned_vehicle_ids(db, d.id):
            raise fail(403, "forbidden", "Эта машина не закреплена за вами")
    if not p.can_either("waybills.create", "waybills.create_own", v.legal_entity_id, p.employee_id == d.id):
        raise fail(403, "forbidden", "Нет права создавать путевые листы")
    return v, d


def need_fuel_consistency(start, added, end):
    if None not in (start, added, end) and end > start + added:
        raise fail(422, "fuel_mismatch", "Остаток топлива больше, чем было в баке с учётом заправки", "fuel_end_l")


def reading_for_waybill(db: Session, w: Waybill, kind: str, value: Decimal) -> VehicleReading | None:
    return db.scalar(effective(w.vehicle_id, kind).where(VehicleReading.waybill_id == w.id, VehicleReading.value == value)
                     .order_by(VehicleReading.id.desc()).limit(1))


# ---------------------------------------------------------------- список и карточка

@router.get("/meta")
def meta(p: Principal = Depends(current)):
    return {"statuses": [{"code": k, "label": v} for k, v in WAYBILL_STATUSES.items()],
            "shifts": [{"code": k, "label": v} for k, v in WAYBILL_SHIFTS.items()],
            "own_employee_id": p.employee_id}


@router.get("")
def list_waybills(q: str = "", status: str = "", vehicle_id: int | None = None, driver_id: int | None = None,
                  date_from: dt.date | None = None, date_to: dt.date | None = None, mine_only: bool = False,
                  legal_entity_id: int | None = None, page: int = 1, page_size: int = 50,
                  p: Principal = Depends(current), db: Session = Depends(get_db)):
    base = visible_condition(p)
    if legal_entity_id is not None:
        if not (p.scope("waybills.view") | p.scope("waybills.view_own")).allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        base = and_(base, Waybill.legal_entity_id == legal_entity_id)
    if vehicle_id:
        base = and_(base, Waybill.vehicle_id == vehicle_id)
    if driver_id:
        base = and_(base, Waybill.driver_id == driver_id)
    if mine_only:
        base = and_(base, Waybill.driver_id == (p.employee_id or -1))
    if date_from:
        base = and_(base, Waybill.work_date >= date_from)
    if date_to:
        base = and_(base, Waybill.work_date <= date_to)
    if q.strip():
        t, raw = like(q.strip()), q.strip().lstrip("№#")
        conds = [Waybill.vehicle_id.in_(select(Vehicle.id).where(Vehicle.plate.ilike(like(q.strip().replace(" ", ""))))),
                 Waybill.driver_id.in_(select(Employee.id).where(Employee.last_name.ilike(t))), Waybill.route.ilike(t)]
        if raw.isdigit():
            conds.append(Waybill.number == int(raw))
        base = and_(base, or_(*conds))
    counts = dict(db.execute(select(Waybill.status, func.count()).where(base).group_by(Waybill.status)).all())
    query = select(Waybill).where(base)
    if status == "active":
        query = query.where(Waybill.status.in_(("draft", "open")))
    elif status:
        if status not in WAYBILL_STATUSES:
            raise fail(422, "bad_status", "Неизвестный статус", "status")
        query = query.where(Waybill.status == status)
    query = query.order_by(Waybill.work_date.desc(), Waybill.id.desc())
    rows, total = page_of(db, query, page, page_size)
    return {"items": [out(db, p, r[0]) for r in rows], "total": total, "page": page, "page_size": page_size,
            "counts": {k: counts.get(k, 0) for k in WAYBILL_STATUSES}}


@router.get("/{wid}")
def read_waybill(wid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return out(db, p, get_waybill(db, p, wid), detailed=True)


# ---------------------------------------------------------------- действия

@router.post("", status_code=201)
def create_waybill(body: WaybillCreate, p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not (p.can("waybills.create") or p.can("waybills.create_own")):
        raise fail(403, "forbidden", "Нет права создавать путевые листы")
    driver_id = body.driver_id or p.employee_id
    if not driver_id:
        raise fail(422, "driver_required", "Укажите водителя", "driver_id")
    work_date = body.work_date or dt.date.today()
    v, d = check_pair(db, p, body.vehicle_id, driver_id, work_date)
    w = Waybill(legal_entity_id=v.legal_entity_id, year=work_date.year, number=next_number(db, v.legal_entity_id, "waybill", work_date.year),
                vehicle_id=v.id, driver_id=d.id, shift=body.shift, work_date=work_date, status="draft", route=body.route.strip(),
                work_site=body.work_site.strip(), note=body.note.strip(), created_by_id=p.id, updated_by_id=p.id)
    db.add(w)
    db.flush()
    audit.log(db, p, "waybill.create", "waybill", w.id, w.legal_entity_id, number=number_label(w), vehicle=v.plate, driver_id=d.id)
    db.commit()
    db.refresh(w)
    return out(db, p, w, detailed=True)


@router.patch("/{wid}")
def update_waybill(wid: int, body: WaybillUpdate, p: Principal = Depends(current), db: Session = Depends(get_db)):
    w = get_waybill(db, p, wid)
    if not can_edit(p, w):
        raise fail(403, "forbidden", "Нет права менять этот путевой лист")
    if w.status not in ("draft", "open"):
        raise fail(409, "waybill_closed", "Лист закрыт: изменить его можно только через «Исправить» с причиной")
    editable = {"draft": {"vehicle_id", "driver_id", "work_date", "shift", "route", "work_site", "note", "fuel_start_l"},
                "open": {"route", "work_site", "note", "fuel_added_l"}}[w.status]
    sent = body.model_dump(exclude_unset=True)
    blocked = [k for k in sent if k not in editable]
    if blocked:
        raise fail(409, "field_locked", "В открытом листе эти поля уже не меняются: " + ", ".join(blocked))
    changed = []
    if any(k in sent for k in ("vehicle_id", "driver_id", "work_date")):
        vid, did, day = sent.get("vehicle_id", w.vehicle_id), sent.get("driver_id", w.driver_id), sent.get("work_date", w.work_date)
        if (vid, did, day) != (w.vehicle_id, w.driver_id, w.work_date):
            v, d = check_pair(db, p, vid, did, day, entity_id=w.legal_entity_id)
            if day.year != w.year:
                raise fail(422, "year_changed", "Дата в другом году: создайте новый лист, чтобы номера не путались", "work_date")
            w.vehicle_id, w.driver_id, w.work_date = v.id, d.id, day
            changed += [k for k in ("vehicle_id", "driver_id", "work_date") if k in sent]
    for f in ("shift", "route", "work_site", "note", "fuel_start_l", "fuel_added_l"):
        if f in sent:
            val = sent[f].strip() if isinstance(sent[f], str) else sent[f]
            if val != getattr(w, f):
                setattr(w, f, val)
                changed.append(f)
    if changed:
        w.updated_by_id = p.id
        audit.log(db, p, "waybill.update", "waybill", w.id, w.legal_entity_id, number=number_label(w), changed=changed)
    db.commit()
    db.refresh(w)
    return out(db, p, w, detailed=True)


def _start_value(db: Session, p: Principal, w: Waybill, v: Vehicle, kind: str, given: Decimal | None, current: Decimal | None):
    unit = READING_KINDS[kind].split(", ")[1]
    if given is None:
        return current
    if current is not None and given < current:
        raise fail(422, "start_below_current", f"Начальное значение меньше последнего показания машины ({current:g} {unit})",
                   "start_km" if kind == "km" else "start_hours")
    if current is None or given > current:
        add_reading(db, p, v, kind, given, source="waybill", waybill_id=w.id, note=f"Лист {number_label(w)}: начало смены")
    return given


@router.post("/{wid}/open")
def open_waybill(wid: int, body: OpenIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    w = get_waybill(db, p, wid)
    if not can_run(p, w):
        raise fail(403, "forbidden", "Нет права открывать этот путевой лист")
    if w.status != "draft":
        raise fail(409, "bad_state", f"Лист в статусе «{WAYBILL_STATUSES[w.status]}»: открыть можно только черновик")
    v = db.get(Vehicle, w.vehicle_id)
    if v.archived_at or v.status in BLOCKED_FOR_WORK:
        raise fail(409, "vehicle_unavailable", f"Машина в статусе «{VEHICLE_STATUSES[v.status]}»: путевой лист открыть нельзя")
    busy = db.scalar(select(Waybill.id).where(Waybill.vehicle_id == v.id, Waybill.status == "open", Waybill.id != w.id))
    if busy:
        raise fail(409, "vehicle_busy", "На эту машину уже открыт другой путевой лист: сначала закройте его")
    driver_busy = db.scalar(select(Waybill.id).where(Waybill.driver_id == w.driver_id, Waybill.status == "open", Waybill.id != w.id))
    if driver_busy:
        raise fail(409, "driver_busy", "У этого водителя уже есть открытый путевой лист")
    start_km = _start_value(db, p, w, v, "km", body.start_km, v.current_km)
    start_h = _start_value(db, p, w, v, "engine_hours", body.start_hours, v.current_hours)
    if start_km is None and start_h is None:
        raise fail(422, "start_required", "Укажите начальный пробег или моточасы", "start_km")
    w.start_km, w.start_hours, w.status, w.opened_at, w.updated_by_id = start_km, start_h, "open", utcnow(), p.id
    if body.fuel_start_l is not None:
        w.fuel_start_l = body.fuel_start_l
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise fail(409, "vehicle_busy", "На эту машину уже открыт другой путевой лист: сначала закройте его")
    audit.log(db, p, "waybill.open", "waybill", w.id, w.legal_entity_id, number=number_label(w), start_km=str(start_km), start_hours=str(start_h))
    db.commit()
    db.refresh(w)
    return out(db, p, w, detailed=True)


@router.post("/{wid}/close")
def close_waybill(wid: int, body: CloseIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    w = get_waybill(db, p, wid)
    if not can_run(p, w):
        raise fail(403, "forbidden", "Нет права закрывать этот путевой лист")
    if w.status != "open":
        raise fail(409, "bad_state", f"Лист в статусе «{WAYBILL_STATUSES[w.status]}»: закрыть можно только открытый")
    v = db.get(Vehicle, w.vehicle_id)
    for kind, start, end, field in (("km", w.start_km, body.end_km, "end_km"), ("engine_hours", w.start_hours, body.end_hours, "end_hours")):
        if start is not None and end is None:
            raise fail(422, "end_required", "Укажите " + ("конечный пробег" if kind == "km" else "моточасы на конец смены"), field)
        if start is None and end is not None:
            raise fail(422, "not_tracked", "В этом листе " + ("пробег" if kind == "km" else "моточасы") + " не велись", field)
        if end is not None and end < start:
            raise fail(422, "end_below_start", "Конечное значение не может быть меньше начального (" + f"{start:g})", field)
    added = body.fuel_added_l if body.fuel_added_l is not None else w.fuel_added_l
    need_fuel_consistency(w.fuel_start_l, added, body.fuel_end_l)
    review = []
    for kind, end in (("km", body.end_km), ("engine_hours", body.end_hours)):
        if end is not None:
            r = add_reading(db, p, v, kind, end, source="waybill", waybill_id=w.id, note=f"Лист {number_label(w)}: конец смены")
            if r.review_state == "pending":
                review.append(kind)
    w.end_km, w.end_hours = body.end_km, body.end_hours
    w.fuel_added_l, w.fuel_end_l = added, body.fuel_end_l
    if body.note is not None:
        w.note = body.note.strip()
    w.status, w.closed_at, w.closed_by_id, w.updated_by_id = "closed", utcnow(), p.id, p.id
    audit.log(db, p, "waybill.close", "waybill", w.id, w.legal_entity_id, number=number_label(w), end_km=str(body.end_km),
              end_hours=str(body.end_hours), needs_review=review)
    db.commit()
    db.refresh(w)
    result = out(db, p, w, detailed=True)
    result["readings_need_review"] = review
    return result


@router.post("/{wid}/cancel")
def cancel_waybill(wid: int, body: CancelIn, p: Principal = Depends(require("waybills.cancel")), db: Session = Depends(get_db)):
    w = get_waybill(db, p, wid)
    if not p.can("waybills.cancel", w.legal_entity_id):
        raise fail(403, "forbidden", "Нет права аннулировать листы этого юрлица")
    if w.status not in ("draft", "open"):
        raise fail(409, "bad_state", "Аннулировать можно только черновик или открытый лист; закрытый исправляют с причиной")
    old = w.status
    w.status, w.cancel_reason, w.updated_by_id = "cancelled", body.reason.strip(), p.id
    audit.log(db, p, "waybill.cancel", "waybill", w.id, w.legal_entity_id, number=number_label(w), from_status=old, reason=body.reason.strip())
    db.commit()
    db.refresh(w)
    return out(db, p, w, detailed=True)


@router.post("/{wid}/correct")
def correct_waybill(wid: int, body: CorrectIn, p: Principal = Depends(require("waybills.correct")), db: Session = Depends(get_db)):
    """Исправление закрытого листа: что было, что стало, кто и почему остаётся навсегда; показания машины исправляются новой записью."""
    w = get_waybill(db, p, wid)
    if not p.can("waybills.correct", w.legal_entity_id):
        raise fail(403, "forbidden", "Нет права исправлять листы этого юрлица")
    if w.status != "closed":
        raise fail(409, "bad_state", "Исправлять можно только закрытый лист")
    sent = body.model_dump(exclude_unset=True, exclude={"reason"})
    before, after = {}, {}
    for f, val in sent.items():
        if val is None:
            continue
        val = val.strip() if isinstance(val, str) else val
        if val != getattr(w, f):
            before[f], after[f] = getattr(w, f), val
    if not after:
        raise fail(422, "nothing_changed", "Ничего не изменено")
    v = db.get(Vehicle, w.vehicle_id)
    for kind, field, start in (("km", "end_km", w.start_km), ("engine_hours", "end_hours", w.start_hours)):
        if field not in after:
            continue
        if start is None or getattr(w, field) is None:
            raise fail(422, "not_tracked", "В этом листе это значение не велось", field)
        if after[field] < start:
            raise fail(422, "end_below_start", f"Конечное значение не может быть меньше начального ({start:g})", field)
        old_value = getattr(w, field)
        rd = reading_for_waybill(db, w, kind, old_value)
        if rd:
            correct_reading(db, p, rd, after[field], body.reason)
        elif db.scalar(select(VehicleReading.id).where(VehicleReading.waybill_id == w.id, VehicleReading.kind == kind,
                                                       VehicleReading.value == old_value, VehicleReading.review_state == "pending")):
            raise fail(409, "reading_pending", "Показание по этому листу ждёт проверки: сначала примите решение в разделе «Показания»", field)
        else:
            add_reading(db, p, v, kind, after[field], source="waybill", waybill_id=w.id, note=f"Лист {number_label(w)}: исправление")
    need_fuel_consistency(after.get("fuel_start_l", w.fuel_start_l), after.get("fuel_added_l", w.fuel_added_l), after.get("fuel_end_l", w.fuel_end_l))
    for f, val in after.items():
        setattr(w, f, val)
    w.updated_by_id = p.id
    db.add(WaybillCorrection(waybill_id=w.id, reason=body.reason.strip(), author_id=p.id,
                             before={k: shown(x) for k, x in before.items()}, after={k: shown(x) for k, x in after.items()}))
    audit.log(db, p, "waybill.correct", "waybill", w.id, w.legal_entity_id, number=number_label(w), reason=body.reason.strip(),
              fields=sorted(after))
    db.commit()
    db.refresh(w)
    return out(db, p, w, detailed=True)

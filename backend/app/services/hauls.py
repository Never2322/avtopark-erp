# -*- coding: utf-8 -*-
"""Счётчик ходок: личный калькулятор водителя самосвала.

Записи видит только сам водитель: нет ни отчёта, ни списка для офиса, ни записи в журнал действий (иначе директор увидел бы их там).
Время ходки берётся с телефона в момент нажатия, вместе с часовым поясом телефона: день считается по тому месту, где водитель был.
Если часы телефона врут (будущее или слишком давнее время), берётся время сервера.
"""
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import HAUL_DAYS_SHOWN, HAUL_DAY_LIMIT, HAUL_TZ_MIN
from app.models import HaulMark, Vehicle, VehicleType
from app.services import timesheet as ts
from app.services.reminders import org_tz

DUMP_CODES = ("truck_dump",)           # самосвал по типу техники
DUMP_WORDS = ("самосвал",)             # и по названию типа (типы из старой программы называются по-своему)


def dump_vehicle_ids(db: Session, vehicle_ids) -> list[int]:
    """Из машин, закреплённых за человеком, самосвалы."""
    ids = list(vehicle_ids or ())
    if not ids:
        return []
    from sqlalchemy import or_
    q = (select(Vehicle.id).join(VehicleType, VehicleType.id == Vehicle.type_id)
         .where(Vehicle.id.in_(ids), Vehicle.archived_at.is_(None),
                or_(VehicleType.code.in_(DUMP_CODES), *[func.lower(VehicleType.name).like(f"%{w}%") for w in DUMP_WORDS])))
    from app.services.routes import waste_vehicle_ids          # мусоровоз (даже с типом «Самосвал») счётчика ходок не получает
    waste = set(waste_vehicle_ids(db, ids))
    return [v for v in db.scalars(q) if v not in waste]


def has_hauls(db: Session, p) -> bool:
    """Нужна ли человеку вкладка «Счётчик ходок»: есть право и закреплён самосвал."""
    from app.services.vehicles import assigned_vehicle_ids
    return bool(p.can("hauls.use_own") and p.employee_id is not None
                and dump_vehicle_ids(db, assigned_vehicle_ids(db, p.employee_id)))


def tz_minutes(at: dt.datetime, tz_min: int | None) -> int:
    """Сдвиг часового пояса телефона. Нет или невероятный: берём пояс организации."""
    if tz_min is not None and HAUL_TZ_MIN[0] <= tz_min <= HAUL_TZ_MIN[1]:
        return int(tz_min)
    off = at.astimezone(org_tz()).utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def local_of(at: dt.datetime, tz_min: int) -> dt.datetime:
    return (at.astimezone(dt.timezone.utc) + dt.timedelta(minutes=tz_min)).replace(tzinfo=None)


def count_day(db: Session, employee_id: int, day: dt.date) -> int:
    return db.scalar(select(func.count()).select_from(HaulMark).where(HaulMark.employee_id == employee_id, HaulMark.local_date == day)) or 0


def day_payload(db: Session, employee_id: int, day: dt.date, today: dt.date) -> dict:
    """Ходки за день по порядку, итог за месяц и последние дни: всё, что нужно странице."""
    marks = list(db.scalars(select(HaulMark).where(HaulMark.employee_id == employee_id, HaulMark.local_date == day)
                            .order_by(HaulMark.marked_at, HaulMark.id)))
    items = [{"id": m.id, "n": i, "time": local_of(m.marked_at, m.tz_min).strftime("%H:%M"), "marked_at": m.marked_at.isoformat()}
             for i, m in enumerate(marks, 1)]
    first = day.replace(day=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    month_count = db.scalar(select(func.count()).select_from(HaulMark).where(
        HaulMark.employee_id == employee_id, HaulMark.local_date >= first, HaulMark.local_date < nxt)) or 0
    since = max(day, today) - dt.timedelta(days=HAUL_DAYS_SHOWN - 1)
    rows = db.execute(select(HaulMark.local_date, func.count()).where(HaulMark.employee_id == employee_id, HaulMark.local_date >= since)
                      .group_by(HaulMark.local_date).order_by(HaulMark.local_date.desc())).all()
    days = [{"date": d.isoformat(), "weekday": ts.WEEKDAYS[d.weekday()], "count": n} for d, n in rows]
    return {"date": day.isoformat(), "weekday": ts.WEEKDAYS[day.weekday()], "today": today.isoformat(), "count": len(items), "items": items,
            "month_title": f"{ts.MONTHS[first.month - 1]} {first.year}", "month_count": month_count, "days": days, "limit": HAUL_DAY_LIMIT}

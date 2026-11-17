# -*- coding: utf-8 -*-
"""Счётчик ходок: личная вкладка водителя самосвала. Кнопка «Ходка» записывает дату и время, случайную ходку можно убрать.

Записи видит только сам водитель. Здесь намеренно нет списка для офиса, выгрузки и отчёта, и в журнал действий ничего не пишется.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common import fail, utcnow
from app.db import get_db
from app.deps import Principal, current
from app.enums import HAUL_DAY_LIMIT
from app.models import HaulMark
from app.resources import job_today
from app.routers.jobs import mark_time
from app.services import hauls as svc

router = APIRouter(prefix="/hauls", tags=["Счётчик ходок"])

BACK_DAYS = 366


def _me(db: Session, p: Principal) -> int:
    if not svc.has_hauls(db, p):
        raise fail(403, "forbidden", "Счётчик ходок есть только у водителей самосвалов")
    return p.employee_id


@router.get("")
def my_hauls(date: dt.date | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Ходки водителя за день (по умолчанию за сегодня), итог за месяц и последние дни. Дату присылает телефон: день по его часам."""
    emp = _me(db, p)
    today = job_today()
    day = date or today
    if day < today - dt.timedelta(days=BACK_DAYS) or day > today + dt.timedelta(days=1):
        raise fail(422, "bad_date", "Этот день смотреть нельзя", "date")
    return svc.day_payload(db, emp, day, today)


class HaulIn(BaseModel):
    at: dt.datetime | None = None            # когда водитель нажал кнопку: телефон без связи присылает своё время
    tz_min: int | None = None                # часовой пояс телефона, минут от UTC (новосибирское время = 420)
    client_id: str = Field("", max_length=64)  # код нажатия: повторная отправка той же ходки не создаёт вторую


@router.post("")
def add_haul(body: HaulIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    emp = _me(db, p)
    today = job_today()
    cid = body.client_id.strip()
    if cid:
        old = db.scalar(select(HaulMark).where(HaulMark.employee_id == emp, HaulMark.client_id == cid))
        if old is not None:
            return {"id": old.id, **svc.day_payload(db, emp, old.local_date, today)}
    at, _ = mark_time(body.at, utcnow())
    tz = svc.tz_minutes(at, body.tz_min)
    day = svc.local_of(at, tz).date()
    if svc.count_day(db, emp, day) >= HAUL_DAY_LIMIT:
        raise fail(422, "too_many", f"За день записано уже {HAUL_DAY_LIMIT} ходок: так много не бывает. Уберите лишние")
    mark = HaulMark(employee_id=emp, marked_at=at, local_date=day, tz_min=tz, client_id=cid)
    db.add(mark)
    try:
        db.commit()
    except IntegrityError:                                   # то же нажатие пришло дважды одновременно
        db.rollback()
        mark = db.scalar(select(HaulMark).where(HaulMark.employee_id == emp, HaulMark.client_id == cid))
        if mark is None:
            raise
    return {"id": mark.id, **svc.day_payload(db, emp, mark.local_date, today)}


@router.delete("/{hid}")
def remove_haul(hid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Убрать ходку, нажатую по ошибке. Убирается совсем: это личный счётчик. Чужую ходку убрать нельзя."""
    emp = _me(db, p)
    mark = db.scalar(select(HaulMark).where(HaulMark.id == hid, HaulMark.employee_id == emp))
    if mark is None:
        raise fail(404, "not_found", "Такой ходки нет: возможно, её уже убрали")
    day = mark.local_date
    db.delete(mark)
    db.commit()
    return svc.day_payload(db, emp, day, job_today())

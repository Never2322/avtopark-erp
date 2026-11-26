# -*- coding: utf-8 -*-
"""Раздел «Расходы»: всё, что компания потратила за месяц, одним списком. Видят те, у кого есть право на деньги в отчётах."""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common import fail, org_today
from app.db import get_db
from app.deps import Principal, current
from app.services import expenses as exp
from app.services import reports as rep
from app.services import timesheet as ts

router = APIRouter(prefix="/finance", tags=["Расходы компании"])


@router.get("/expenses")
def expenses(month: str | None = None, cat: str = "", legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Расходы за месяц: итог, разбивка по статьям, сравнение с прошлым месяцем и строки. cat отбирает одну статью."""
    if not p.can("reports.money"):
        raise fail(403, "forbidden", "Нет права видеть деньги в отчётах")
    if legal_entity_id is not None and not p.visible_entities().allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    if cat and cat not in exp.CAT_LABELS:
        raise fail(422, "bad_cat", "Такой статьи расходов нет", "cat")
    today = org_today()
    try:
        first = ts.parse_month(month, today)
    except ValueError as e:
        raise fail(422, "bad_month", str(e), "month")
    c = rep.Ctx(db=db, p=p, entity=legal_entity_id, d_from=first, d_to=first, today=today, now=dt.datetime.now(dt.timezone.utc))
    return exp.overview(c, first, cat)


@router.get("/vehicle-costs")
def vehicle_costs(month: str | None = None, vehicle_id: int | None = None, legal_entity_id: int | None = None,
                  p: Principal = Depends(current), db: Session = Depends(get_db)):
    """«Затраты на спецтехнику»: все затраты по машинам за месяц из всех разделов — сводка по статьям, по машинам и журнал."""
    from app.services import vehicle_costs as vc
    if not p.can("vehicle_finance.view"):
        raise fail(403, "forbidden", "Нет права на затраты по технике")
    if legal_entity_id is not None and not p.visible_entities().allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    today = org_today()
    try:
        first = ts.parse_month(month, today)
    except ValueError as e:
        raise fail(422, "bad_month", str(e), "month")
    a, b = rep.month_bounds(first.year, first.month)
    c = vc.Ctx(db=db, p=p, entity=legal_entity_id, d_from=a, d_to=b, today=today, now=dt.datetime.now(dt.timezone.utc))
    res = vc.overview(c, vehicle_id)
    res.update({"month": first.strftime("%Y-%m"), "title": f"{ts.MONTHS[first.month - 1]} {first.year}",
                "can_add": p.can("vehicle_finance.create")})
    return res

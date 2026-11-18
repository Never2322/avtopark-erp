# -*- coding: utf-8 -*-
"""Маршруты мусоровозов: «Мои маршруты» водителя и «Маршруты по дням» директора и диспетчера.

Сами маршруты (название, улицы) и расписание ведутся в обычных разделах (garbage_routes и route_plan);
здесь только чтение того, что в итоге едет в выбранный день.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common import fail, org_today
from app.db import get_db
from app.deps import Principal, current
from app.services import routes as svc

router = APIRouter(prefix="/route-board", tags=["Маршруты мусоровозов"])


@router.get("/mine")
def mine(p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Маршруты водителя: на сегодня, на ближайшие две недели и обычное расписание по дням недели."""
    if not svc.has_routes(db, p):
        raise fail(403, "forbidden", "Маршруты есть у водителей мусоровозов")
    return svc.mine_payload(db, p.employee_id, org_today())


@router.get("/day")
def day(date: dt.date | None = None, legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Кто на каком маршруте в выбранный день (по умолчанию сегодня)."""
    scope = p.scope("route_plan.view")
    if not scope:
        raise fail(403, "forbidden", "Нет права смотреть расписание маршрутов")
    if legal_entity_id is not None:
        if not scope.allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        scope = scope.narrow(legal_entity_id)
    today = org_today()
    on = date or today
    if on < today - dt.timedelta(days=svc.BOARD_BACK_DAYS) or on > today + dt.timedelta(days=svc.BOARD_FORWARD_DAYS):
        raise fail(422, "bad_date", "Этот день смотреть нельзя", "date")
    return {"today": today.isoformat(), **svc.board_payload(db, on, scope)}

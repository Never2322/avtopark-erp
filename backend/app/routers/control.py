# -*- coding: utf-8 -*-
"""Плановое ТО: кнопка «Выполнено». Запись закрывается сегодняшним днём; если задана периодичность в км, создаётся следующая
запись с порогом «текущий пробег + периодичность». Остальное (список, добавление, правка) идёт через общий каркас (раздел «maintenance»)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.models import MaintenanceItem, Vehicle
from app.registry import RESOURCES, get_for, rows_out
from app.services import reminders as rem

router = APIRouter(prefix="/maintenance", tags=["Техобслуживание"])


class DoneIn(BaseModel):
    note: str = Field(default="", max_length=300)


@router.post("/{oid}/done", status_code=201)
def done(oid: int, body: DoneIn | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    res = RESOURCES["maintenance"]
    item = get_for(db, res, p, oid, "edit")
    if item.archived_at:
        raise fail(409, "archived", "Запись в архиве: сначала верните её")
    if item.done_at is not None:
        raise fail(409, "already_done", "Эта запись уже отмечена выполненной")
    veh = db.get(Vehicle, item.vehicle_id)
    item.done_at = rem.local_today()
    item.controlled = False
    if body and body.note:
        item.note = (item.note + "\n" if item.note else "") + body.note
    new = None
    if item.interval_km and veh.current_km is not None:
        if not p.can("maintenance.create", item.legal_entity_id):
            raise fail(403, "forbidden", "Нет права добавлять записи о ТО: следующая запись не создана")
        new = MaintenanceItem(legal_entity_id=item.legal_entity_id, vehicle_id=item.vehicle_id, kind=item.kind, work=item.work, interval_km=item.interval_km,
                              due_km=int(veh.current_km) + item.interval_km, controlled=True, created_by_id=p.user.id)
        db.add(new)
        db.flush()
    audit.log(db, p, "maintenance.done", "maintenance", item.id, item.legal_entity_id, next_id=new.id if new else None)
    db.commit()
    return {"done": rows_out(db, res, [item], p)[0], "next": rows_out(db, res, [new], p)[0] if new else None}

# -*- coding: utf-8 -*-
"""Вкладка «Тоннаж» у водителя: его талоны весов за месяц и итог.

Сам талон водитель вносит тем же запросом, что рейсы и заправки (POST /entries/mine/ticket, см. routers/entries.py): фото и заметка,
больше ничего. Здесь только то, что нужно странице: машины с галочкой «Вести тоннаж», закреплённые за ним, талоны за выбранный месяц
и итог (ходок, тонн, сколько талонов ещё без тоннажа). Журнал для сотрудников — раздел «weigh_tickets» (app/resources.py), отчёт по
машинам за месяц — «Отчёты → Тоннаж».
"""
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.enums import ENTRY_DELETE_HOURS, ENTRY_MAX_PHOTOS
from app.models import Vehicle, WeighTicket
from app.registry import _vehicle_label
from app.resources import job_today
from app.routers.entries import KINDS, items_out, mine
from app.services import weigh as weigh_svc
from app.services.reports import MONTHS_RU, month_bounds, month_period

router = APIRouter(prefix="/tonnage", tags=["Тоннаж"])
STAFF_HIDDEN = ("read_note",)         # служебная подсказка для сотрудника: водителю она не нужна


def my_vehicles(db: Session, p: Principal) -> list[Vehicle]:
    ids = weigh_svc.tonnage_vehicle_ids(db, mine(db, p))
    if not ids:
        return []
    return list(db.scalars(select(Vehicle).where(Vehicle.id.in_(ids)).order_by(Vehicle.plate)))


@router.get("/mine")
def my_tonnage(month: str | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    view = p.scope("weigh_tickets.view_own") | p.scope("weigh_tickets.view")
    if not view:
        raise fail(403, "forbidden", "Нет доступа к тоннажу")
    today = job_today()
    d_from, d_to = month_period(month, today)
    vehicles = my_vehicles(db, p)
    ids = [v.id for v in vehicles]
    items: list[dict] = []
    tons, trips, pending = Decimal(0), 0, 0
    if ids:
        where = [WeighTicket.vehicle_id.in_(ids), WeighTicket.archived_at.is_(None), WeighTicket.ticket_date >= d_from,
                 WeighTicket.ticket_date <= d_to, view.cond(WeighTicket.legal_entity_id)]
        rows = list(db.scalars(select(WeighTicket).where(*where).order_by(WeighTicket.ticket_date.desc(), WeighTicket.id.desc()).limit(300)))
        items = items_out(db, p, KINDS["ticket"], rows)
        for it in items:
            for key in STAFF_HIDDEN:
                it.pop(key, None)
        n, total, no_tons = db.execute(select(func.count(), func.coalesce(func.sum(WeighTicket.tons), 0),
                                              func.count().filter(WeighTicket.tons.is_(None))).where(*where)).one()
        trips, tons, pending = n, Decimal(total), no_tons
    prev = month_bounds(d_from.year - (d_from.month == 1), 12 if d_from.month == 1 else d_from.month - 1)[0]
    return {
        "vehicles": [{"id": v.id, "label": _vehicle_label(v), "legal_entity_id": v.legal_entity_id} for v in vehicles],
        "items": items, "today": today.isoformat(), "max_photos": ENTRY_MAX_PHOTOS, "delete_hours": ENTRY_DELETE_HOURS,
        "month": d_from.strftime("%Y-%m"), "month_label": f"{MONTHS_RU[d_from.month - 1]} {d_from.year}",
        "prev_month": prev.strftime("%Y-%m"), "is_current": d_from.strftime("%Y-%m") == today.strftime("%Y-%m"),
        "totals": {"trips": trips, "tons": float(tons), "pending": pending},
        "can": {"create": bool(vehicles) and p.can("weigh_tickets.create_own") and p.employee_id is not None},
    }

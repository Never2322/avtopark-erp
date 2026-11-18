# -*- coding: utf-8 -*-
"""«Что купить»: общий список деталей из открытых обращений. Одинаковые детали складываются в одну строку, купленное отмечается."""
import datetime as dt

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.db import get_db
from app.deps import Principal, require
from app.enums import REQUEST_PARTS_OPEN, REQUEST_STATUSES
from app.models import RequestPart, ServiceRequest, User, Vehicle
from app.parts_seed import norm
from app.registry import org_tz
from app.services import notify
from app.services import parts as svc

router = APIRouter(prefix="/purchases", tags=["Что купить"])
RECENT_DAYS = 30
MAX_MARK = 200


class MarkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_MARK)


class RestoreIn(BaseModel):
    id: int


def _scope_cond(p: Principal, perm: str, legal_entity_id: int | None):
    scope = p.scope(perm)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к разделу «Что купить»")
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    cond = [scope.cond(ServiceRequest.legal_entity_id)]
    if legal_entity_id is not None:
        cond.append(ServiceRequest.legal_entity_id == legal_entity_id)
    return cond


def _rows(db: Session, cond: list, *, bought: bool, since: dt.datetime | None = None, vehicle_id: int | None = None):
    q = (select(RequestPart, ServiceRequest, Vehicle).join(ServiceRequest, ServiceRequest.id == RequestPart.request_id)
         .join(Vehicle, Vehicle.id == ServiceRequest.vehicle_id).where(RequestPart.removed_at.is_(None), *cond))
    if bought:
        q = q.where(RequestPart.bought_at.is_not(None), RequestPart.bought_at >= since).order_by(RequestPart.bought_at.desc(), RequestPart.id.desc())
    else:
        q = q.where(RequestPart.bought_at.is_(None), ServiceRequest.status.in_(REQUEST_PARTS_OPEN)).order_by(RequestPart.added_at, RequestPart.id)
    if vehicle_id:
        q = q.where(ServiceRequest.vehicle_id == vehicle_id)
    return db.execute(q.limit(2000 if not bought else 300)).all()


def _line(part: RequestPart, r: ServiceRequest, v: Vehicle) -> dict:
    return {"part_id": part.id, "request_id": r.id, "number": r.number, "status": r.status, "status_label": REQUEST_STATUSES[r.status],
            "approved": r.status in ("in_progress", "waiting_parts"), "plate": v.plate, "vehicle_id": v.id,
            "qty": float(part.qty), "qty_text": svc.qty_text(part.qty), "added_at": part.added_at.isoformat(), "priority": r.priority}


def build(db: Session, p: Principal, *, q: str = "", vehicle_id: int | None = None, legal_entity_id: int | None = None, perm: str = "purchases.view") -> dict:
    cond = _scope_cond(p, perm, legal_entity_id)
    term = norm(q)
    groups: dict[tuple, dict] = {}
    for part, r, v in _rows(db, cond, bought=False, vehicle_id=vehicle_id):
        if term and term not in norm(part.name) and term not in norm(part.article) and term not in norm(v.plate) and term not in norm(str(r.number)):
            continue
        key = (norm(part.name), norm(part.article), part.unit)
        g = groups.setdefault(key, {"name": part.name, "article": part.article, "unit": part.unit, "qty": 0, "lines": [], "ids": []})
        g["qty"] += part.qty
        g["lines"].append(_line(part, r, v))
        g["ids"].append(part.id)
    items = []
    for g in sorted(groups.values(), key=lambda x: norm(x["name"])):
        g["qty_text"], g["qty"] = svc.qty_text(g["qty"]), float(g["qty"])
        g["requests"] = len({ln["request_id"] for ln in g["lines"]})
        items.append(g)
    since = utcnow() - dt.timedelta(days=RECENT_DAYS)
    recent = []
    for part, r, v in _rows(db, cond, bought=True, since=since, vehicle_id=vehicle_id):
        if term and term not in norm(part.name) and term not in norm(part.article) and term not in norm(v.plate) and term not in norm(str(r.number)):
            continue
        who = db.get(User, part.bought_by_id) if part.bought_by_id else None
        recent.append({**_line(part, r, v), "name": part.name, "article": part.article, "unit": part.unit,
                       "bought_at": part.bought_at.isoformat(), "bought_by": (who.display_name or who.login) if who else None,
                       "can_restore": r.status in REQUEST_PARTS_OPEN and p.can("purchases.mark", r.legal_entity_id)})
    return {"items": items, "recent": recent, "recent_days": RECENT_DAYS,
            "totals": {"positions": len(items), "lines": sum(len(g["lines"]) for g in items), "requests": len({ln["request_id"] for g in items for ln in g["lines"]})},
            "can": {"mark": bool(p.scope("purchases.mark")), "export": bool(p.scope("purchases.export")), "catalog": bool(p.scope("parts.view"))}}


@router.get("")
def read(q: str = "", vehicle_id: int | None = None, legal_entity_id: int | None = None,
         p: Principal = Depends(require("purchases.view")), db: Session = Depends(get_db)):
    return build(db, p, q=q, vehicle_id=vehicle_id, legal_entity_id=legal_entity_id)


@router.post("/bought")
def mark_bought(body: MarkIn, p: Principal = Depends(require("purchases.mark")), db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(body.ids))
    found = db.execute(select(RequestPart, ServiceRequest).join(ServiceRequest, ServiceRequest.id == RequestPart.request_id)
                       .where(RequestPart.id.in_(ids), RequestPart.removed_at.is_(None))).all()
    if len(found) != len(ids):
        raise fail(404, "not_found", "Некоторых деталей в списке уже нет: обновите страницу")
    per_request: dict[int, tuple[ServiceRequest, list[RequestPart]]] = {}
    for part, r in found:
        if not p.can("purchases.mark", r.legal_entity_id):
            raise fail(403, "forbidden", "Нет права отмечать покупки по этому юрлицу")
        if r.status not in REQUEST_PARTS_OPEN:
            raise fail(409, "request_closed", f"Обращение № {r.number} уже выполнено или закрыто: список деталей не меняется")
        if part.bought_at is None:
            per_request.setdefault(r.id, (r, []))[1].append(part)
    now, advanced, marked = utcnow(), [], 0
    for r, rows in per_request.values():
        for part in rows:
            part.bought_at, part.bought_by_id = now, p.id
        marked += len(rows)
        svc._event(db, r, p, "edit", "Куплено: " + ", ".join(f"{x.name} — {svc.qty_text(x.qty)} {x.unit}" for x in rows))
        r.updated_by_id, r.updated_at = p.id, now
        db.flush()
        if svc.after_bought(db, r, p):
            advanced.append(r.number)
        audit.log(db, p, "purchase.bought", "request", r.id, r.legal_entity_id, number=r.number, parts=[x.name for x in rows])
        notify.on_parts_bought(db, r, [x.name for x in rows], p.id)
    db.commit()
    return {"marked": marked, "advanced": advanced}


@router.post("/restore")
def restore(body: RestoreIn, p: Principal = Depends(require("purchases.mark")), db: Session = Depends(get_db)):
    part = db.get(RequestPart, body.id)
    r = db.get(ServiceRequest, part.request_id) if part else None
    if not part or part.removed_at or not r or not p.can("purchases.mark", r.legal_entity_id):
        raise fail(404, "not_found", "Такой покупки нет")
    if part.bought_at is None:
        raise fail(409, "not_bought", "Эта деталь и так не отмечена купленной")
    if r.status not in REQUEST_PARTS_OPEN:
        raise fail(409, "request_closed", f"Обращение № {r.number} уже выполнено или закрыто: покупку не отменить")
    part.bought_at = part.bought_by_id = None
    svc._event(db, r, p, "edit", f"Покупка отменена: {part.name}")
    r.updated_by_id, r.updated_at = p.id, utcnow()
    moved = svc.after_unbought(db, r, p)
    audit.log(db, p, "purchase.restore", "request", r.id, r.legal_entity_id, number=r.number, part=part.name)
    db.commit()
    return {"ok": True, "moved_to_waiting": moved}


@router.get("/export.{fmt}")
def export_file(fmt: str, q: str = "", vehicle_id: int | None = None, legal_entity_id: int | None = None,
                p: Principal = Depends(require("purchases.export")), db: Session = Depends(get_db)):
    from app.exporting import check_format, local_naive, table_response
    check_format(fmt)
    data = build(db, p, q=q, vehicle_id=vehicle_id, legal_entity_id=legal_entity_id, perm="purchases.export")
    rows: list[list] = [["Деталь", "Артикул", "Единица", "Всего", "Обращение №", "Машина", "Статус обращения", "Нужно", "Добавлено"]]
    for g in data["items"]:
        for ln in g["lines"]:
            rows.append([g["name"], g["article"] or None, g["unit"], g["qty"], ln["number"], ln["plate"] or None, ln["status_label"], ln["qty"],
                         local_naive(dt.datetime.fromisoformat(ln["added_at"])).date()])
    audit.log(db, p, "purchases.export", "request", None, legal_entity_id, rows=len(rows) - 1)
    db.commit()
    return table_response(fmt, "purchases", "Что купить", rows, [None, None, None, "num", None, None, None, "num", "date"])

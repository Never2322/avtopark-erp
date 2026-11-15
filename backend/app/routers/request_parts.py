# -*- coding: utf-8 -*-
"""Детали в обращении: что нужно купить для ремонта. Список ведёт ответственный за ремонт, водитель может дописать в свою заявку."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, utcnow
from app.db import get_db
from app.deps import Principal, current
from app.enums import REQUEST_PARTS_MAX, REQUEST_PARTS_OPEN
from app.models import PartCatalog, RequestPart
from app.routers.requests_ import get_request
from app.services import parts as svc

router = APIRouter(tags=["Детали в обращениях"])


class PartIn(BaseModel):
    name: str = Field(default="", max_length=400)
    article: str = Field(default="", max_length=200)
    qty: Any = 1
    unit: str = Field(default="шт", max_length=20)


class PartsAdd(BaseModel):
    items: list[PartIn] = Field(min_length=1, max_length=REQUEST_PARTS_MAX)


def _guard(p: Principal, r):
    if r.status not in REQUEST_PARTS_OPEN:
        raise fail(409, "request_closed", "Обращение выполнено или закрыто: список деталей уже не меняется")
    if not svc.can_change(p, r):
        raise fail(403, "forbidden", "Нет права менять детали в этом обращении")


@router.get("/requests/{rid}/parts")
def read_parts(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return svc.block(db, p, get_request(db, p, rid))


@router.post("/requests/{rid}/parts", status_code=201)
def add_parts(rid: int, body: PartsAdd, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_request(db, p, rid)
    _guard(p, r)
    lines = svc.clean_lines([i.model_dump() for i in body.items])
    live = svc.live_parts(db, r.id)
    if len(live) + len(lines) > REQUEST_PARTS_MAX:
        raise fail(422, "too_many_parts", f"В одном обращении не больше {REQUEST_PARTS_MAX} деталей: уже {len(live)}", "items")
    for i, line in enumerate(lines):
        if any(svc.same_part(line, x) for x in live) or any(svc.same_part(line, y) for y in lines[:i]):
            raise fail(409, "duplicate_part", f"«{line['name']}» уже есть в списке. Измените количество: уберите строку и добавьте заново", "items")
    for line in lines:
        db.add(RequestPart(request_id=r.id, added_by_id=p.id, **line))
    svc.learn(db, lines)
    names = ", ".join(f"{x['name']} — {svc.qty_text(x['qty'])} {x['unit']}" for x in lines)
    svc._event(db, r, p, "edit", f"Добавлены детали: {names}")
    r.updated_by_id, r.updated_at = p.id, utcnow()
    audit.log(db, p, "request_part.add", "request", r.id, r.legal_entity_id, number=r.number, parts=[x["name"] for x in lines])
    db.commit()
    db.refresh(r)
    return svc.block(db, p, r)


@router.delete("/requests/{rid}/parts/{pid}")
def remove_part(rid: int, pid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_request(db, p, rid)
    _guard(p, r)
    part = db.get(RequestPart, pid)
    if not part or part.request_id != r.id or part.removed_at:
        raise fail(404, "not_found", "Такой детали в обращении нет")
    if part.bought_at:
        raise fail(409, "part_bought", "Деталь уже куплена: сначала отмените покупку в разделе «Что купить»")
    part.removed_at, part.removed_by_id = utcnow(), p.id
    svc._event(db, r, p, "edit", f"Убрана деталь: {part.name}")
    r.updated_by_id, r.updated_at = p.id, utcnow()
    svc.after_bought(db, r, p)      # убрали последнюю некупленную — ждать больше нечего
    audit.log(db, p, "request_part.remove", "request", r.id, r.legal_entity_id, number=r.number, part=part.name)
    db.commit()
    db.refresh(r)
    return svc.block(db, p, r)


@router.get("/part-options")
def part_options(q: str = "", limit: int = 15, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Подсказки названий при вводе: сначала самые частые, потом по алфавиту. Нужны тем, кто вносит детали или ведёт каталог."""
    if not (p.scope("requests.create") or p.scope("requests.edit") or p.scope("bills.create") or p.scope("bills.edit") or p.scope("parts.view")):
        raise fail(403, "forbidden", "Нет доступа")
    query = select(PartCatalog).where(PartCatalog.archived_at.is_(None))
    t = q.strip()
    if t:
        pat = like(t)
        query = query.where(or_(PartCatalog.name.ilike(pat), PartCatalog.article.ilike(pat)))
    rows = db.scalars(query.order_by(PartCatalog.uses.desc(), func.lower(PartCatalog.name)).limit(max(1, min(limit, 50))))
    return {"items": [{"id": c.id, "name": c.name, "article": c.article, "unit": c.unit, "category": c.category, "uses": c.uses} for c in rows]}
